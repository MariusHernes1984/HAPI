"""
NDLA scraper for "Helsefremmende arbeid (HS-HEA vg2)".

Henter hele taksonomien og alle artikler fra NDLAs offentlige API, og
lagrer strukturert i SQLite med FTS5 for fritekstsøk.

Kilde: https://ndla.no/f/helsefremmende-arbeid-hs-hea-vg2/9c8c7457bf6f
Subject-URN: urn:subject:1:1b7155ae-9670-4972-b438-fd1375875ac1
Lisens: CC-BY-SA-4.0 (innholdet må krediteres NDLA)

Kjøres:
    python scrape.py              # full refresh
    python scrape.py --incremental # hopp over artikler som finnes
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SUBJECT_ID = "urn:subject:1:1b7155ae-9670-4972-b438-fd1375875ac1"
SUBJECT_SLUG = "helsefremmende-arbeid-hs-hea-vg2"
API_BASE = "https://api.ndla.no"
NDLA_BASE = "https://ndla.no"
LANGUAGE = "nb"

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "ndla_helsefag.db"

USER_AGENT = "HAPI-NDLA-Scraper/1.0 (+https://github.com/Atea)"


def http_get_json(url: str, retries: int = 3) -> Any:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_err}")


SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    name TEXT NOT NULL,
    path TEXT,
    url TEXT,
    breadcrumbs TEXT,
    content_uri TEXT,
    node_type TEXT,
    context_id TEXT,
    depth INTEGER
);
CREATE INDEX IF NOT EXISTS idx_topics_parent ON topics(parent_id);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    introduction TEXT,
    meta_description TEXT,
    content_html TEXT,
    content_text TEXT,
    article_type TEXT,
    license TEXT,
    license_url TEXT,
    authors TEXT,
    tags TEXT,
    grep_codes TEXT,
    updated TEXT,
    published TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    article_id INTEGER,
    name TEXT NOT NULL,
    resource_types TEXT,
    primary_type TEXT,
    url TEXT,
    path TEXT,
    context_id TEXT,
    FOREIGN KEY (topic_id) REFERENCES topics(id)
);
CREATE INDEX IF NOT EXISTS idx_resources_topic ON resources(topic_id);
CREATE INDEX IF NOT EXISTS idx_resources_article ON resources(article_id);
CREATE INDEX IF NOT EXISTS idx_resources_type ON resources(primary_type);

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title,
    introduction,
    content_text,
    tags,
    content='articles',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 1'
);

CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, introduction, content_text, tags)
    VALUES (new.id, new.title, new.introduction, new.content_text, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, introduction, content_text, tags)
    VALUES ('delete', old.id, old.title, old.introduction, old.content_text, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, introduction, content_text, tags)
    VALUES ('delete', old.id, old.title, old.introduction, old.content_text, old.tags);
    INSERT INTO articles_fts(rowid, title, introduction, content_text, tags)
    VALUES (new.id, new.title, new.introduction, new.content_text, new.tags);
END;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    no_tags = _TAG_RE.sub(" ", raw)
    unescaped = html.unescape(no_tags)
    return _WS_RE.sub(" ", unescaped).strip()


def compute_depth(path: str | None) -> int:
    if not path:
        return 0
    return path.count("/topic:") + path.count("/subject:")


def fetch_topics() -> list[dict[str, Any]]:
    url = f"{API_BASE}/taxonomy/v1/nodes/{SUBJECT_ID}/nodes?recursive=true&language={LANGUAGE}"
    print(f"[topics] GET {url}", flush=True)
    return http_get_json(url)


def fetch_resources() -> list[dict[str, Any]]:
    url = f"{API_BASE}/taxonomy/v1/nodes/{SUBJECT_ID}/resources?recursive=true&language={LANGUAGE}"
    print(f"[resources] GET {url}", flush=True)
    return http_get_json(url)


def fetch_article(article_id: int) -> dict[str, Any] | None:
    url = f"{API_BASE}/article-api/v2/articles/{article_id}?language={LANGUAGE}"
    try:
        return http_get_json(url)
    except RuntimeError as e:
        print(f"  ! article {article_id} failed: {e}", flush=True)
        return None


def topic_parent(topic: dict[str, Any]) -> str | None:
    path = topic.get("path") or ""
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None
    try:
        idx = segments.index(topic["id"].split(":", 1)[-1]) if topic["id"].split(":", 1)[-1] in segments else -1
    except Exception:
        idx = -1
    # Simpler: parent is the previous segment if it starts with topic: or subject:
    for i in range(len(segments) - 1, -1, -1):
        seg = segments[i]
        own_tail = topic["id"].replace("urn:", "")
        if seg == own_tail and i > 0:
            prev = segments[i - 1]
            return f"urn:{prev}"
    return None


def insert_topics(conn: sqlite3.Connection, topics: list[dict[str, Any]]) -> None:
    rows = []
    # Include the subject itself as the root
    rows.append((
        SUBJECT_ID,
        None,
        "Helsefremmende arbeid (HS-HEA vg2)",
        f"/subject:1:{SUBJECT_ID.split(':')[-1]}",
        f"{NDLA_BASE}/f/{SUBJECT_SLUG}/9c8c7457bf6f",
        json.dumps(["Helsefremmende arbeid (HS-HEA vg2)"], ensure_ascii=False),
        None,
        "SUBJECT",
        "9c8c7457bf6f",
        0,
    ))
    for t in topics:
        tid = t["id"]
        parent = topic_parent(t) or SUBJECT_ID
        url = t.get("url") or t.get("defaultUrl") or ""
        if url and url.startswith("/"):
            url = urljoin(NDLA_BASE, url)
        rows.append((
            tid,
            parent,
            t.get("name") or t.get("baseName") or "",
            t.get("path"),
            url,
            json.dumps(t.get("breadcrumbs", []), ensure_ascii=False),
            t.get("contentUri"),
            t.get("nodeType", "TOPIC"),
            t.get("contextId"),
            compute_depth(t.get("path")),
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO topics "
        "(id, parent_id, name, path, url, breadcrumbs, content_uri, node_type, context_id, depth) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def resource_topic_id(resource: dict[str, Any]) -> str | None:
    path = resource.get("path") or ""
    segs = [s for s in path.split("/") if s]
    # last segment starting with topic: is the owning topic
    for seg in reversed(segs):
        if seg.startswith("topic:"):
            return f"urn:{seg}"
    return None


def insert_resources(conn: sqlite3.Connection, resources: list[dict[str, Any]]) -> None:
    rows = []
    for r in resources:
        rid = r["id"]
        topic_id = resource_topic_id(r)
        if not topic_id:
            continue
        content_uri = r.get("contentUri") or ""
        article_id = None
        if content_uri.startswith("urn:article:"):
            try:
                article_id = int(content_uri.split(":")[-1])
            except ValueError:
                article_id = None
        rt_names = [rt.get("name") for rt in r.get("resourceTypes", []) if rt.get("name")]
        url = r.get("url") or r.get("defaultUrl") or ""
        if url and url.startswith("/"):
            url = urljoin(NDLA_BASE, url)
        rows.append((
            rid,
            topic_id,
            article_id,
            r.get("name") or r.get("baseName") or "",
            json.dumps(rt_names, ensure_ascii=False),
            rt_names[0] if rt_names else None,
            url,
            r.get("path"),
            r.get("contextId"),
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO resources "
        "(id, topic_id, article_id, name, resource_types, primary_type, url, path, context_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def insert_article(conn: sqlite3.Connection, article: dict[str, Any]) -> None:
    title = (article.get("title") or {}).get("title", "")
    intro = (article.get("introduction") or {}).get("introduction", "")
    meta_desc = (article.get("metaDescription") or {}).get("metaDescription", "")
    content_html = (article.get("content") or {}).get("content", "")
    tags_obj = article.get("tags") or {}
    tags = tags_obj.get("tags", []) if isinstance(tags_obj, dict) else []
    copyright_ = article.get("copyright") or {}
    license_ = (copyright_.get("license") or {})
    creators = [c.get("name") for c in copyright_.get("creators", []) if c.get("name")]
    processors = [c.get("name") for c in copyright_.get("processors", []) if c.get("name")]
    authors = creators + processors

    conn.execute(
        "INSERT OR REPLACE INTO articles "
        "(id, title, introduction, meta_description, content_html, content_text, article_type, "
        " license, license_url, authors, tags, grep_codes, updated, published, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            article["id"],
            title,
            intro,
            meta_desc,
            content_html,
            strip_html(content_html),
            article.get("articleType"),
            license_.get("license"),
            license_.get("url"),
            json.dumps(authors, ensure_ascii=False),
            json.dumps(tags, ensure_ascii=False),
            json.dumps(article.get("grepCodes", []), ensure_ascii=False),
            article.get("updated"),
            article.get("published"),
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        ),
    )


def existing_article_ids(conn: sqlite3.Connection) -> set[int]:
    return {row[0] for row in conn.execute("SELECT id FROM articles")}


def scrape(incremental: bool = False) -> None:
    conn = init_db(DB_PATH)
    conn.execute("BEGIN")
    try:
        topics = fetch_topics()
        insert_topics(conn, topics)
        print(f"[topics] stored {len(topics)} topics", flush=True)

        resources = fetch_resources()
        insert_resources(conn, resources)
        print(f"[resources] stored {len(resources)} resources", flush=True)

        article_ids: list[int] = []
        seen: set[int] = set()
        for r in resources:
            cu = r.get("contentUri") or ""
            if cu.startswith("urn:article:"):
                try:
                    aid = int(cu.split(":")[-1])
                except ValueError:
                    continue
                if aid not in seen:
                    seen.add(aid)
                    article_ids.append(aid)
        # Include topic articles (info-pages for each topic)
        for t in topics:
            cu = t.get("contentUri") or ""
            if cu.startswith("urn:article:"):
                try:
                    aid = int(cu.split(":")[-1])
                except ValueError:
                    continue
                if aid not in seen:
                    seen.add(aid)
                    article_ids.append(aid)

        if incremental:
            have = existing_article_ids(conn)
            todo = [a for a in article_ids if a not in have]
        else:
            todo = article_ids

        print(f"[articles] fetching {len(todo)} / {len(article_ids)} articles", flush=True)
        for i, aid in enumerate(todo, 1):
            art = fetch_article(aid)
            if art:
                insert_article(conn, art)
            if i % 25 == 0:
                print(f"  ... {i}/{len(todo)}", flush=True)
                conn.commit()
                conn.execute("BEGIN")
            time.sleep(0.05)  # vær snill mot API-et

        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("last_scrape", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("subject_id", SUBJECT_ID),
        )
        conn.commit()
        print(f"[done] {DB_PATH}", flush=True)
        # summary
        ac = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        tc = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        rc = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
        print(f"[stats] topics={tc} resources={rc} articles={ac}", flush=True)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--incremental", action="store_true", help="Hopp over artikler som allerede finnes")
    args = ap.parse_args()
    scrape(incremental=args.incremental)


if __name__ == "__main__":
    main()
