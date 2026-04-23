# NDLA-scraper — Helsefremmende arbeid (HS-HEA vg2)

Henter hele faget "Helsefremmende arbeid (HS-HEA vg2)" fra
[ndla.no](https://ndla.no/f/helsefremmende-arbeid-hs-hea-vg2/9c8c7457bf6f)
via NDLAs offentlige API (`api.ndla.no`) og lagrer det strukturert i SQLite
med FTS5-fritekstsøk.

## Innhold

- **42 temaer/undertemaer** — hierarkisk taksonomi
- **381 ressurser** — fagtekst (245), oppgaver (130), kildemateriell, filmer, spill
- **418 artikler** — full tekst + HTML
- **Lisens:** CC-BY-SA-4.0 — alle treff krediteres NDLA

## Kjør scraper

```bash
# Full refresh (tar ~2 min)
python scrape.py

# Kun hent nye artikler
python scrape.py --incremental
```

Resultat: `data/ndla_helsefag.db` (~30 MB).

## Databaseskjema

| Tabell | Innhold |
|--------|---------|
| `topics` | Temahierarki med breadcrumbs og NDLA-URL |
| `resources` | Fagressurser → peker til `topics` + `articles` |
| `articles` | Full tekst + HTML + metadata + lisens |
| `articles_fts` | FTS5 virtuell tabell (unicode61, no-diakritiske) |
| `meta` | `last_scrape`, `subject_id` |

## MCP-integrasjon

Fire MCP-tool er eksponert i HAPI-serveren (`src/ndla.ts`):

| Tool | Bruk |
|------|------|
| `sok_ndla_helsefag` | Fritekstsøk (FTS5) med snippet + filter på tema/ressurstype |
| `hent_ndla_artikkel` | Full artikkel som tekst eller HTML |
| `hent_ndla_temaer` | Hele tema-treet med ressurstellinger |
| `list_ndla_ressurser_for_tema` | Ressursliste under ett tema (inkl. undertemaer) |

Serveren leter etter DB-en via `NDLA_DB_PATH`, deretter flere kandidatstier
(dev og container). I Dockerfile kopieres DB-en til `/app/dist/ndla_helsefag.db`.

## Oppfriskning

NDLA oppdaterer innhold jevnlig. Kjør scraperen igjen og bygg nytt container-image
for å få oppdatert innhold i produksjon.
