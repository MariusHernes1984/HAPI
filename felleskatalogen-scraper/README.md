# Felleskatalogen-scraper (HAPI POC)

Henter ~18 utvalgte flaggskip-legemidler fra
[felleskatalogen.no](https://www.felleskatalogen.no) og lagrer hovedseksjoner
verbatim i SQLite. Dataen brukes av `hapi-felleskatalogen-agent` til
opt-in doseringsoppslag.

## Lisensstatus

POC-data — kommersiell avtale med Felleskatalogen er IKKE etablert. Innhold
er Felleskatalogens åndsverk; demo må ikke distribueres bredt før lisens er
på plass. robots.txt overholdes (`Crawl-delay: 10s`).

## Hva som scrapes

Flaggskip-utvalg knyttet til mock-pasientene (P-005, P-035 osv.). Ni hoved­
seksjoner per preparat:

- Indikasjoner
- Dosering
- Administrering (når oppgitt)
- Kontraindikasjoner
- Forsiktighetsregler
- Interaksjoner
- Graviditet, amming og fertilitet
- Bivirkninger
- Overdosering
- Egenskaper (når oppgitt)

URL-listen ligger i [`preparater.json`](preparater.json). Legge til nytt
preparat: finn URL via `site:felleskatalogen.no/medisin <virkestoff>`,
verifiser at den ikke er en `/pasienter/`-URL, og legg inn entry.

## Kjør scraper

```bash
# Full hent (10s × 18 = ~3 min)
python scrape.py

# Bare ett preparat
python scrape.py --only Paracet

# Skip preparater allerede i DB
python scrape.py --skip-cached
```

Resultat: `data/felleskatalogen.db` (~6 MB), rå HTML i `data/raw/` (audit, ikke committed).

## DB-skjema

| Tabell | Rolle |
|--------|-------|
| `preparater` | Metadata + scrape-dato + sist endret kilde |
| `seksjoner` | Verbatim HTML + ren tekst per seksjon |
| `preparater_fts` | FTS5 over navn/produsent/virkestoff/atc |
| `meta` | `last_scrape`, `preparater_count` |

## MCP-integrasjon

`src/felleskatalogen.ts` registrerer tre tools:

| Tool | Bruk |
|------|------|
| `sok_felleskatalogen` | Søk etter preparat (FTS5 + LIKE-fallback) |
| `hent_felleskatalogen_dosering` | Returnerer en eller flere seksjoner VERBATIM |
| `list_felleskatalogen_preparater` | Liste alle preparater i POC-utvalget |

## Agent + bypass-syntese

`hapi-felleskatalogen-agent` (definert i `deploy_agents.py`) har strenge
verbatim-regler. Output omsluttes med `[VERBATIM-FELLESKATALOGEN]`-markører
slik at orkestratoren plukker ut innholdet og legger det til ETTER syntesen
— blandes aldri med LLM-genererte ord.

Frontend gjenkjenner blokken og rendrer som adskilt blockquote med
Felleskatalogen-badge.

## Routing — opt-in

Triggers (eksplisitte): `felleskatalogen`, `preparatomtale`, `verifisert dosering`,
`vis dosering`, `slå opp dosering`, `spc`, m.fl. — full liste i
`router.py:FELLESKATALOGEN_TRIGGERS`. Vanlige doseringsspørsmål uten disse
ordene går fortsatt til retningslinje-agenten som før.
