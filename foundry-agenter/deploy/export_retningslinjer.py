"""
Export alle retningslinjer fra HAPI QA API til Markdown-filer.

Flyt:
  1. Hent liste over alle 98 retningslinjer
  2. For hver retningslinje: hent publikasjonsstruktur (kapitler + anbefalinger)
  3. For hver anbefaling/kapittel: hent full tekst via /innhold/{id}
  4. Konverter til Markdown og lagre som én fil per retningslinje

Output: ./retningslinjer_export/<slug>.md

Bruk:
  python export_retningslinjer.py [--output-dir ./retningslinjer_export] [--max N]
"""

import argparse
import asyncio
import json
import logging
import re
import time
from html import unescape
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# --- Konfigurasjon ---

BASE_URL = "https://api-qa.helsedirektoratet.no/innhold"
SUBSCRIPTION_KEY = "db6f9e9beab6428c8caddb65f7718f0d"
HEADERS = {
    "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
    "Accept": "application/json",
    "Cache-Control": "no-cache",
}

# Rate limiting: maks samtidige kall
MAX_CONCURRENT = 3
DELAY_BETWEEN_BATCHES = 1.0  # sekunder mellom batcher av innholds-kall


# --- HTML → Markdown konvertering (enkel) ---

def html_to_markdown(html: str) -> str:
    """Konverter enkel HTML til Markdown. Bevarer faglig innhold."""
    if not html:
        return ""

    text = html

    # Overskrifter
    for i in range(6, 0, -1):
        text = re.sub(rf'<h{i}[^>]*>(.*?)</h{i}>', lambda m: f'\n{"#" * i} {m.group(1).strip()}\n', text, flags=re.DOTALL)

    # Lister
    text = re.sub(r'<li[^>]*>(.*?)</li>', lambda m: f'- {m.group(1).strip()}', text, flags=re.DOTALL)
    text = re.sub(r'<[ou]l[^>]*>', '\n', text)
    text = re.sub(r'</[ou]l>', '\n', text)

    # Tabeller (enkel konvertering)
    text = re.sub(r'<tr[^>]*>(.*?)</tr>', lambda m: '| ' + m.group(1).strip() + ' |', text, flags=re.DOTALL)
    text = re.sub(r'<t[hd][^>]*>(.*?)</t[hd]>', lambda m: m.group(1).strip() + ' | ', text, flags=re.DOTALL)

    # Avsnitt og linjeskift
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<p[^>]*>', '\n\n', text)
    text = re.sub(r'</p>', '', text)

    # Bold/italic
    text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.DOTALL)

    # Sub/superscript (bevar for faglig presisjon)
    text = re.sub(r'<sub[^>]*>(.*?)</sub>', r'_\1', text, flags=re.DOTALL)
    text = re.sub(r'<sup[^>]*>(.*?)</sup>', r'^(\1)', text, flags=re.DOTALL)

    # Lenker
    text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.DOTALL)

    # Fjern gjenværende HTML-tags
    text = re.sub(r'<[^>]+>', '', text)

    # HTML entities
    text = unescape(text)

    # Rydd opp whitespace
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    return text


def slugify(title: str) -> str:
    """Lag filnavn-vennlig slug fra tittel."""
    slug = title.lower()
    slug = re.sub(r'[æ]', 'ae', slug)
    slug = re.sub(r'[ø]', 'o', slug)
    slug = re.sub(r'[å]', 'aa', slug)
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug[:80]


# --- API-kall ---

async def fetch_json(client: httpx.AsyncClient, url: str, retries: int = 3) -> dict | list | None:
    """Hent JSON fra HAPI API med retry."""
    for attempt in range(1, retries + 1):
        try:
            resp = await client.get(url, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"  Rate limited, venter {wait}s...")
                await asyncio.sleep(wait)
                continue
            else:
                logger.error(f"  HTTP {resp.status_code} for {url}")
                return None
        except Exception as e:
            logger.error(f"  Feil (forsøk {attempt}): {e}")
            if attempt < retries:
                await asyncio.sleep(1)
    return None


async def fetch_all_retningslinjer(client: httpx.AsyncClient) -> list[dict]:
    """Hent liste over alle retningslinjer."""
    data = await fetch_json(client, f"{BASE_URL}/retningslinjer")
    if not data:
        logger.error("Kunne ikke hente retningslinjer!")
        return []
    logger.info(f"Hentet {len(data)} retningslinjer")
    return data


async def fetch_publikasjon_struktur(client: httpx.AsyncClient, struktur_id: str) -> dict | None:
    """Hent publikasjonsstruktur (kapittel-tre)."""
    return await fetch_json(client, f"{BASE_URL}/publikasjoner/{struktur_id}")


async def fetch_innhold(client: httpx.AsyncClient, innhold_id: str) -> dict | None:
    """Hent fullt innhold for én anbefaling/kapittel."""
    return await fetch_json(client, f"{BASE_URL}/innhold/{innhold_id}")


# --- Tre-traversering ---

def extract_node_ids(node: dict) -> list[str]:
    """Ekstraher alle innhold-IDer fra publikasjons-treet (DFS)."""
    ids = []
    infobit = node.get("infobit") or {}
    if infobit and infobit.get("id"):
        ids.append(infobit["id"])
    for barn in node.get("barn", []):
        ids.extend(extract_node_ids(barn))
    return ids


def build_markdown_tree(node: dict, innhold_map: dict, depth: int = 0) -> str:
    """Bygg Markdown fra publikasjons-treet."""
    parts = []
    infobit = node.get("infobit") or {}
    node_id = infobit.get("id", "")

    if node_id and node_id in innhold_map:
        content = innhold_map[node_id]
        tittel = content.get("tittel", "")
        tekst = content.get("tekst", "")
        data = content.get("data", {}) or {}

        # Overskriftsnivå basert på dybde (min ## for kapitler)
        heading_level = min(depth + 2, 5)  # ## for kapitler, ### for anbefalinger, osv.

        if tittel:
            parts.append(f'\n{"#" * heading_level} {tittel}\n')

        # Styrkegrad for anbefalinger
        styrke = data.get("styrke", "")
        if styrke:
            parts.append(f'**Styrkegrad:** {styrke}\n')

        # Koder
        koder = content.get("koder") or []
        if koder:
            kode_strs = []
            for k in koder:
                if isinstance(k, dict) and k.get("kode"):
                    kode_strs.append(f'{k.get("kodeverk", "")}: {k.get("kode", "")}')
                elif isinstance(k, str):
                    kode_strs.append(k)
            if kode_strs:
                parts.append(f'**Koder:** {", ".join(kode_strs)}\n')

        # Hovedtekst
        if tekst:
            md_tekst = html_to_markdown(tekst)
            if md_tekst:
                parts.append(f'\n{md_tekst}\n')

        # Praktisk info
        praktisk = data.get("praktisk", "")
        if praktisk:
            md = html_to_markdown(praktisk)
            if md:
                parts.append(f'\n### Praktisk\n\n{md}\n')

        # Rasjonale
        rasjonale = data.get("rasjonale", "")
        if rasjonale:
            md = html_to_markdown(rasjonale)
            if md:
                parts.append(f'\n### Begrunnelse\n\n{md}\n')

        # Nøkkelinfo
        nokkel = data.get("nokkelInfo", "")
        if nokkel:
            if isinstance(nokkel, str):
                md = html_to_markdown(nokkel)
            elif isinstance(nokkel, dict):
                md = html_to_markdown(json.dumps(nokkel, ensure_ascii=False))
            else:
                md = str(nokkel)
            if md:
                parts.append(f'\n### Nøkkelinformasjon\n\n{md}\n')

        # Behandlingsregimer (viktig for legemiddel-anbefalinger)
        regimer = data.get("behandlingsRegimer") or data.get("behandlingsregimer") or []
        if regimer:
            parts.append('\n### Behandlingsregimer\n')
            for reg in regimer:
                if isinstance(reg, dict):
                    kat = reg.get("kategori", "standard")
                    parts.append(f'\n**{kat.title()}:**\n')
                    for dos in reg.get("doseringsregimer", []):
                        lem = dos.get("legemiddel", {})
                        navn = lem.get("term", "Ukjent")
                        dose = dos.get("dose", "")
                        enhet = dos.get("enhet", "")
                        intervall = dos.get("intervall", "")
                        varighet = dos.get("varighet", "")
                        parts.append(f'- {navn}: {dose} {enhet} {intervall}')
                        if varighet:
                            parts.append(f' i {varighet}')
                        parts.append('\n')

    # Rekursivt for barn
    for barn in node.get("barn", []):
        child_md = build_markdown_tree(barn, innhold_map, depth + 1)
        if child_md.strip():
            parts.append(child_md)

    return "".join(parts)


# --- Hovedeksport ---

async def export_retningslinje(
    client: httpx.AsyncClient,
    retningslinje: dict,
    output_dir: Path,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Eksporter én retningslinje til Markdown."""
    tittel = retningslinje.get("tittel", "Ukjent")
    ret_id = retningslinje.get("id", "")
    slug = slugify(tittel)

    async with semaphore:
        logger.info(f"  Eksporterer: {tittel[:60]}...")
        start = time.monotonic()

        # Finn strukturId fra links
        struktur_id = None
        for link in (retningslinje.get("links") or []):
            if link.get("rel") == "publikasjon":
                struktur_id = link.get("strukturId")
                break

        if not struktur_id:
            logger.warning(f"  Ingen publikasjonsstruktur for {tittel[:60]}")
            # Lag minimal Markdown fra retningslinje-metadata
            md = _minimal_markdown(retningslinje)
            output_file = output_dir / f"{slug}.md"
            output_file.write_text(md, encoding="utf-8")
            return {"id": ret_id, "tittel": tittel, "file": str(output_file), "anbefalinger": 0, "chars": len(md)}

        # Hent publikasjonsstruktur
        pub = await fetch_publikasjon_struktur(client, struktur_id)
        if not pub or "rot" not in pub:
            logger.warning(f"  Tom struktur for {tittel[:60]}")
            md = _minimal_markdown(retningslinje)
            output_file = output_dir / f"{slug}.md"
            output_file.write_text(md, encoding="utf-8")
            return {"id": ret_id, "tittel": tittel, "file": str(output_file), "anbefalinger": 0, "chars": len(md)}

        rot = pub["rot"]

        # Samle alle innhold-IDer fra treet
        all_ids = extract_node_ids(rot)
        logger.info(f"    {len(all_ids)} noder å hente")

        # Hent innhold for alle noder (batched, respekterer rate limits)
        innhold_map = {}
        batch_size = 5
        for i in range(0, len(all_ids), batch_size):
            batch = all_ids[i:i + batch_size]
            tasks = [fetch_innhold(client, nid) for nid in batch]
            results = await asyncio.gather(*tasks)
            for nid, result in zip(batch, results):
                if result:
                    innhold_map[nid] = result
            if i + batch_size < len(all_ids):
                await asyncio.sleep(DELAY_BETWEEN_BATCHES)

        # Bygg Markdown
        md_parts = []

        # Metadata-header
        md_parts.append(f'# {tittel}\n\n')

        url = retningslinje.get("url", "")
        if url:
            md_parts.append(f'**Kilde:** [{url}]({url})\n')

        dato = retningslinje.get("sistFagligOppdatert", "")
        if dato:
            md_parts.append(f'**Sist faglig oppdatert:** {dato[:10]}\n')

        eier = retningslinje.get("eier", [])
        if eier:
            md_parts.append(f'**Utgiver:** {", ".join(eier)}\n')

        tema = retningslinje.get("tema", [])
        if tema:
            md_parts.append(f'**Tema:** {", ".join(tema)}\n')

        koder = retningslinje.get("koder") or []
        if koder:
            kode_strs = []
            for k in koder:
                if isinstance(k, dict) and k.get("kode"):
                    kode_strs.append(f'{k.get("kodeverk", "")}: {k.get("kode", "")}')
                elif isinstance(k, str):
                    kode_strs.append(k)
            if kode_strs:
                md_parts.append(f'**Koder:** {", ".join(kode_strs)}\n')

        md_parts.append('\n---\n\n')

        # Innhold fra treet
        tree_md = build_markdown_tree(rot, innhold_map)
        md_parts.append(tree_md)

        # Footer
        md_parts.append(f'\n\n---\n*Eksportert fra Helsedirektoratets retningslinje-API ({time.strftime("%Y-%m-%d")})*\n')

        md = "".join(md_parts)

        # Skriv fil
        output_file = output_dir / f"{slug}.md"
        output_file.write_text(md, encoding="utf-8")

        duration = time.monotonic() - start
        logger.info(f"    ✓ {tittel[:50]}: {len(md)} tegn, {len(all_ids)} noder, {duration:.1f}s")

        return {
            "id": ret_id,
            "tittel": tittel,
            "file": str(output_file),
            "anbefalinger": len(all_ids),
            "chars": len(md),
        }


def _minimal_markdown(retningslinje: dict) -> str:
    """Lag minimal Markdown for retningslinjer uten publikasjonsstruktur."""
    tittel = retningslinje.get("tittel", "Ukjent")
    parts = [f'# {tittel}\n\n']

    url = retningslinje.get("url", "")
    if url:
        parts.append(f'**Kilde:** [{url}]({url})\n')

    dato = retningslinje.get("sistFagligOppdatert", "")
    if dato:
        parts.append(f'**Sist faglig oppdatert:** {dato[:10]}\n')

    tekst = retningslinje.get("tekst", "")
    if tekst:
        parts.append(f'\n{html_to_markdown(tekst)}\n')

    intro = retningslinje.get("intro", "")
    if intro:
        parts.append(f'\n{html_to_markdown(intro)}\n')

    parts.append(f'\n---\n*Eksportert fra Helsedirektoratets retningslinje-API ({time.strftime("%Y-%m-%d")})*\n')
    return "".join(parts)


async def main():
    parser = argparse.ArgumentParser(description="Eksporter retningslinjer fra HAPI til Markdown")
    parser.add_argument("--output-dir", default="./retningslinjer_export", help="Output-mappe")
    parser.add_argument("--max", type=int, default=0, help="Maks antall retningslinjer (0 = alle)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Vis detaljer")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Demp httpx-logging (veldig verbose)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Eksporterer retningslinjer til {output_dir.resolve()}")

    async with httpx.AsyncClient() as client:
        # Steg 1: Hent alle retningslinjer
        retningslinjer = await fetch_all_retningslinjer(client)
        if not retningslinjer:
            return

        # Filtrer kun gjeldende
        gjeldende = [r for r in retningslinjer if r.get("status") == "Gjeldende"]
        logger.info(f"  {len(gjeldende)} av {len(retningslinjer)} er 'Gjeldende'")

        if args.max > 0:
            gjeldende = gjeldende[:args.max]
            logger.info(f"  Begrenset til {args.max} retningslinjer")

        # Steg 2: Eksporter parallelt (med semaphore for rate limiting)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        start = time.monotonic()

        tasks = [
            export_retningslinje(client, r, output_dir, semaphore)
            for r in gjeldende
        ]
        results = await asyncio.gather(*tasks)

        duration = time.monotonic() - start

        # Steg 3: Rapport
        successful = [r for r in results if r]
        total_chars = sum(r["chars"] for r in successful)
        total_anbefalinger = sum(r["anbefalinger"] for r in successful)

        logger.info(f"\n{'='*60}")
        logger.info(f"EKSPORT FULLFØRT")
        logger.info(f"  Retningslinjer: {len(successful)}/{len(gjeldende)}")
        logger.info(f"  Anbefalinger/noder: {total_anbefalinger}")
        logger.info(f"  Totalt: {total_chars:,} tegn ({total_chars/1024/1024:.1f} MB)")
        logger.info(f"  Tid: {duration:.0f}s")
        logger.info(f"  Output: {output_dir.resolve()}")

        # Lagre manifest
        manifest = {
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "retningslinjer_total": len(retningslinjer),
            "retningslinjer_gjeldende": len(gjeldende),
            "retningslinjer_eksportert": len(successful),
            "total_anbefalinger": total_anbefalinger,
            "total_chars": total_chars,
            "duration_s": round(duration, 1),
            "files": successful,
        }
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"  Manifest: {manifest_path}")

        # Vis topp-10 største
        by_size = sorted(successful, key=lambda r: r["chars"], reverse=True)
        logger.info(f"\nTopp 10 største retningslinjer:")
        for r in by_size[:10]:
            logger.info(f"  {r['chars']:>8,} tegn | {r['anbefalinger']:>3} noder | {r['tittel'][:60]}")


if __name__ == "__main__":
    asyncio.run(main())
