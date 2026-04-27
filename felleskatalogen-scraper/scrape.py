"""
Felleskatalogen-scraper for HAPI POC.

Henter preparat-sider fra felleskatalogen.no, parser ut hovedseksjoner
(Indikasjoner, Dosering, Kontraindikasjoner, Forsiktighetsregler,
Interaksjoner, Bivirkninger m.fl.) og lagrer VERBATIM tekst i SQLite.

VIKTIG:
- robots.txt krever Crawl-delay: 10s — overholdes
- Rå HTML lagres i data/raw/ for revisjon
- Hver scrape får dato + URL slik at agent-svar kan kreditere kilden
- Innhold er Felleskatalogens åndsverk; demo-bruk forutsetter avtale før prod

Kjør:
    python scrape.py                 # full hent (10s delay × ~17 = ~3 min)
    python scrape.py --only paracet  # kun ett preparat
    python scrape.py --skip-cached   # hopp over allerede lagrede preparater
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
RAW_DIR = DATA_DIR / "raw"
DATA_DIR.mkdir(exist_ok=True)
RAW_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "felleskatalogen.db"
PREPARATER_FILE = HERE / "preparater.json"

CRAWL_DELAY_S = 10  # robots.txt
USER_AGENT = "HAPI-Demo-Scraper/0.1 (helsefag-POC; respekt-robots-txt)"

# Seksjoner vi vil ha — i prioritert rekkefølge
SEKSJONER_AV_INTERESSE = [
    "Indikasjoner",
    "Dosering",
    "Administrering",
    "Kontraindikasjoner",
    "Forsiktighetsregler",
    "Interaksjoner",
    "Graviditet, amming og fertilitet",
    "Bivirkninger",
    "Overdosering",
    "Egenskaper",
]

# --- Schema ---

SCHEMA = """
CREATE TABLE IF NOT EXISTS preparater (
    id INTEGER PRIMARY KEY,
    navn TEXT NOT NULL,
    produsent TEXT,
    atc TEXT,
    virkestoff TEXT,
    url TEXT NOT NULL,
    tags TEXT,
    scrape_dato TEXT,
    raw_html_path TEXT,
    sist_endret_kilde TEXT
);

CREATE TABLE IF NOT EXISTS seksjoner (
    preparat_id INTEGER NOT NULL,
    seksjon TEXT NOT NULL,
    rekkefolge INTEGER,
    innhold_html TEXT,
    innhold_tekst TEXT,
    PRIMARY KEY (preparat_id, seksjon),
    FOREIGN KEY (preparat_id) REFERENCES preparater(id)
);
CREATE INDEX IF NOT EXISTS idx_seksjoner_seksjon ON seksjoner(seksjon);

CREATE VIRTUAL TABLE IF NOT EXISTS preparater_fts USING fts5(
    navn, produsent, virkestoff, atc, tags,
    content='preparater', content_rowid='id',
    tokenize='unicode61 remove_diacritics 1'
);

CREATE TRIGGER IF NOT EXISTS preparater_ai AFTER INSERT ON preparater BEGIN
    INSERT INTO preparater_fts(rowid, navn, produsent, virkestoff, atc, tags)
    VALUES (new.id, new.navn, new.produsent, new.virkestoff, new.atc, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS preparater_ad AFTER DELETE ON preparater BEGIN
    INSERT INTO preparater_fts(preparater_fts, rowid, navn, produsent, virkestoff, atc, tags)
    VALUES ('delete', old.id, old.navn, old.produsent, old.virkestoff, old.atc, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS preparater_au AFTER UPDATE ON preparater BEGIN
    INSERT INTO preparater_fts(preparater_fts, rowid, navn, produsent, virkestoff, atc, tags)
    VALUES ('delete', old.id, old.navn, old.produsent, old.virkestoff, old.atc, old.tags);
    INSERT INTO preparater_fts(rowid, navn, produsent, virkestoff, atc, tags)
    VALUES (new.id, new.navn, new.produsent, new.virkestoff, new.atc, new.tags);
END;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


# --- HTTP ---

def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


# --- Parsing ---

H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
ZWS_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")  # zero-width chars


def strip_tags(s: str) -> str:
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = ZWS_RE.sub("", s)
    s = WS_RE.sub(" ", s).strip()
    return s


def matches_section(name: str) -> str | None:
    """Returnerer kanonisk seksjonsnavn hvis name matcher en kjent seksjon."""
    for canonical in SEKSJONER_AV_INTERESSE:
        if name.startswith(canonical):
            return canonical
    return None


def parse_html(raw_html: str) -> dict[str, dict[str, str]]:
    """Returner mapping: kanonisk_seksjon -> {html, tekst}."""
    h2_matches = list(H2_RE.finditer(raw_html))
    sections: dict[str, dict[str, str]] = {}
    for i, m in enumerate(h2_matches):
        name = strip_tags(m.group(1))
        canonical = matches_section(name)
        if not canonical:
            continue
        body_start = m.end()
        body_end = h2_matches[i + 1].start() if i + 1 < len(h2_matches) else len(raw_html)
        body_html = raw_html[body_start:body_end].strip()
        body_text = strip_tags(body_html)
        # Skip svært korte eller tomme seksjoner
        if len(body_text) < 20:
            continue
        sections[canonical] = {"html": body_html, "tekst": body_text}
    return sections


def extract_id_from_url(url: str) -> int:
    """Felleskatalogen-URL slutter alltid på -{id}."""
    m = re.search(r"-(\d+)$", url.rstrip("/"))
    if not m:
        raise ValueError(f"Kunne ikke finne ID i URL: {url}")
    return int(m.group(1))


def extract_last_modified(raw_html: str) -> str | None:
    """Felleskatalogen viser ofte 'Sist endret: YYYY-MM-DD' nederst."""
    m = re.search(
        r"sist\s+endret[:\s]+(\d{1,2}[\.\-/]\d{1,2}[\.\-/]\d{2,4}|\d{4}[\.\-/]\d{1,2}[\.\-/]\d{1,2})",
        raw_html,
        re.IGNORECASE,
    )
    return m.group(1) if m else None


# --- DB ---

def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_preparat(conn: sqlite3.Connection, preparat: dict, raw_path: Path,
                     sections: dict, sist_endret: str | None) -> None:
    pid = preparat["id"]
    conn.execute(
        """INSERT OR REPLACE INTO preparater
           (id, navn, produsent, atc, virkestoff, url, tags, scrape_dato, raw_html_path, sist_endret_kilde)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            pid,
            preparat["navn"],
            preparat.get("produsent"),
            preparat.get("atc"),
            preparat.get("virkestoff"),
            preparat["url"],
            json.dumps(preparat.get("tags", []), ensure_ascii=False),
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            str(raw_path.relative_to(HERE)),
            sist_endret,
        ),
    )
    # Slett gamle seksjoner og insert nye (verbatim)
    conn.execute("DELETE FROM seksjoner WHERE preparat_id = ?", (pid,))
    for i, canonical in enumerate(SEKSJONER_AV_INTERESSE):
        if canonical not in sections:
            continue
        conn.execute(
            """INSERT INTO seksjoner (preparat_id, seksjon, rekkefolge, innhold_html, innhold_tekst)
               VALUES (?, ?, ?, ?, ?)""",
            (pid, canonical, i, sections[canonical]["html"], sections[canonical]["tekst"]),
        )


# --- Hoved ---

def scrape_one(conn: sqlite3.Connection, preparat: dict, skip_cached: bool = False) -> bool:
    """Scrape ett preparat. Returnerer True hvis hentet, False hvis hoppet over."""
    pid = extract_id_from_url(preparat["url"])
    preparat = {**preparat, "id": pid}

    if skip_cached:
        row = conn.execute("SELECT id FROM preparater WHERE id = ?", (pid,)).fetchone()
        if row:
            print(f"  · {preparat['navn']:20} [cached, skipper]")
            return False

    raw_path = RAW_DIR / f"{pid}_{preparat['navn'].lower().replace(' ', '_')}.html"
    print(f"  → {preparat['navn']:20} ({preparat['url']})")
    try:
        raw_html = fetch(preparat["url"])
    except (HTTPError, URLError) as e:
        print(f"    ! fetch feilet: {e}")
        return False

    raw_path.write_text(raw_html, encoding="utf-8")
    sections = parse_html(raw_html)
    sist_endret = extract_last_modified(raw_html)

    upsert_preparat(conn, preparat, raw_path, sections, sist_endret)
    print(f"    ✓ {len(sections)} seksjoner, sist endret: {sist_endret or 'ukjent'}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Scrape kun preparat med dette navnet (case-insensitive)")
    ap.add_argument("--skip-cached", action="store_true", help="Hopp over preparater allerede i DB")
    args = ap.parse_args()

    with open(PREPARATER_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    preparater = cfg["preparater"]

    if args.only:
        preparater = [p for p in preparater if args.only.lower() in p["navn"].lower()]
        if not preparater:
            print(f"Fant ingen preparater som matcher '{args.only}'")
            return

    conn = init_db()
    print(f"Scraper {len(preparater)} preparater til {DB_PATH}")
    print(f"Crawl-delay: {CRAWL_DELAY_S}s mellom forespørsler\n")

    fetched = 0
    for i, p in enumerate(preparater):
        if scrape_one(conn, p, skip_cached=args.skip_cached):
            fetched += 1
            conn.commit()
            if i < len(preparater) - 1:
                time.sleep(CRAWL_DELAY_S)

    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("last_scrape", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("preparater_count", str(conn.execute("SELECT COUNT(*) FROM preparater").fetchone()[0])),
    )
    conn.commit()

    pcount = conn.execute("SELECT COUNT(*) FROM preparater").fetchone()[0]
    scount = conn.execute("SELECT COUNT(*) FROM seksjoner").fetchone()[0]
    print(f"\nFerdig. {fetched} hentet ny denne gangen. Total i DB: {pcount} preparater, {scount} seksjoner.")
    conn.close()


if __name__ == "__main__":
    main()
