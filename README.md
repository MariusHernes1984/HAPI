# HAPI MCP Server

MCP server for Helsedirektoratets HAPI innholdstjenester — gir AI-agenter tilgang til norske nasjonale faglige retningslinjer, veiledere, anbefalinger og pakkeforløp.

<img width="817" height="1162" alt="HAPI infographic" src="https://github.com/user-attachments/assets/7b7a5a30-c07f-46af-94e3-3a61171478fb" />


## Arkitektur

```
Copilot Studio / AI Foundry / Claude Code
        ↓ (MCP over Streamable HTTP / stdio)
Azure Container Apps
        ↓ (REST)
Helsedirektoratets HAPI API (api-qa.helsedirektoratet.no)
```

## Tilgjengelige tools

| Tool | Beskrivelse | Kilde |
|------|-------------|-------|
| `sok_innhold` | Fritekst-søk i alt innhold | HAPI API `api-qa.helsedirektoratet.no/innhold/sok/infobit` |
| `hent_retningslinjer` | Liste over alle nasjonale faglige retningslinjer | HAPI API `api-qa.helsedirektoratet.no/innhold/retningslinjer` |
| `hent_retningslinje` | Spesifikk retningslinje etter ID (inkl. kapitler og anbefalinger) | HAPI API `api-qa.helsedirektoratet.no/innhold/retningslinjer/{id}` |
| `hent_anbefalinger` | Anbefalinger, filtrerbare på kodeverk (ICPC-2, ICD-10) og kode | HAPI API `api-qa.helsedirektoratet.no/innhold/anbefalinger` |
| `hent_anbefaling` | Spesifikk anbefaling etter ID | HAPI API `api-qa.helsedirektoratet.no/innhold/anbefalinger/{id}` |
| `hent_veiledere` | Liste over alle nasjonale veiledere | HAPI API `api-qa.helsedirektoratet.no/innhold/veiledere` |
| `hent_veileder` | Spesifikk veileder etter ID | HAPI API `api-qa.helsedirektoratet.no/innhold/veiledere/{id}` |
| `hent_pakkeforlop` | Liste over alle pakkeforløp | HAPI API `api-qa.helsedirektoratet.no/innhold/pakkeforløp` |
| `hent_pakkeforlop_id` | Spesifikt pakkeforløp etter ID | HAPI API `api-qa.helsedirektoratet.no/innhold/pakkeforløp/{id}` |
| `hent_innhold` | Generisk innholdshenting med filtre (infotype, kodeverk, målgruppe) | HAPI API `api-qa.helsedirektoratet.no/innhold/innhold` |
| `hent_innhold_id` | Spesifikt innhold etter ID | HAPI API `api-qa.helsedirektoratet.no/innhold/innhold/{id}` |
| `hent_kvalitetsindikatorer` | Nasjonale kvalitetsindikatorer | HAPI API `api-qa.helsedirektoratet.no/innhold/kvalitetsindikatorer` |
| `hent_kvalitetsindikator` | Spesifikk kvalitetsindikator etter ID | HAPI API `api-qa.helsedirektoratet.no/innhold/kvalitetsindikatorer/{id}` |
| `hent_endringer` | Endringer siden et gitt tidspunkt | HAPI API `api-qa.helsedirektoratet.no/innhold/GetChanges` |
| `sok_legemidler` | Søk i FEST-legemiddelregisteret etter navn, virkestoff, ATC-kode eller form | HAPI API `api-qa.helsedirektoratet.no/legemidler/legemiddelvirkestoff` (cachet i minne) |
| `hent_legemiddel` | Hent detaljert legemiddelinfo etter ID (virkestoff, styrke, pakninger) | HAPI API `api-qa.helsedirektoratet.no/legemidler/legemiddelvirkestoff/{id}` |
| `sjekk_interaksjoner` | Sjekk legemiddelinteraksjoner (FEST/SLV) — faregrad, klinisk konsekvens | FEST-interaksjonsdata fra Statens legemiddelverk via `interaksjoner.no` |
| `hent_interaksjon` | Hent interaksjonsdetaljer — mekanisme, håndtering, PubMed-referanser | FEST-interaksjonsdata fra Statens legemiddelverk via `interaksjoner.azurewebsites.net` |
| `sok_ndla_helsefag` | Søk i NDLAs fagstoff for Helsefremmende arbeid (HS-HEA vg2) | Lokal SQLite/FTS-database bygget av `ndla-scraper/scrape.py` fra NDLA |
| `hent_ndla_artikkel` | Hent full NDLA-artikkel basert på artikkel-ID | Lokal SQLite-database bygget av `ndla-scraper/scrape.py` fra NDLA |
| `hent_ndla_temaer` | Liste alle tema og undertema i NDLAs Helsefremmende arbeid | Lokal SQLite-database bygget av `ndla-scraper/scrape.py` fra NDLA |
| `list_ndla_ressurser_for_tema` | List ressurser under et gitt NDLA-tema | Lokal SQLite-database bygget av `ndla-scraper/scrape.py` fra NDLA |
| `sok_felleskatalogen` | Søk etter preparat i Felleskatalogen-databasen | Lokal SQLite/FTS-database bygget av `felleskatalogen-scraper/scrape.py` fra Felleskatalogen.no |
| `hent_felleskatalogen_dosering` | Hent verbatim tekst fra én eller flere seksjoner av Felleskatalogen-preparatomtalen | Lokal SQLite-database bygget av `felleskatalogen-scraper/scrape.py` fra Felleskatalogen.no |
| `list_felleskatalogen_preparater` | Liste alle preparater i Felleskatalogen-demo-databasen | Lokal SQLite-database bygget av `felleskatalogen-scraper/scrape.py` fra Felleskatalogen.no |

## Forutsetninger

- Node.js 22+
- HAPI subscription key fra Helsedirektoratet

## Oppsett

```bash
npm install
npm run build
```

## Kjøring

### HTTP-server (for Copilot Studio / AI Foundry)

```bash
export HAPI_SUBSCRIPTION_KEY=din-hapi-nøkkel
export MCP_API_KEY=din-api-nøkkel
export PORT=3000
npm start
```

Endepunkter:
- `POST /mcp` — MCP Streamable HTTP transport
- `GET /health` — Helsesjekk

### Stdio-transport (for Claude Code)

```bash
export HAPI_SUBSCRIPTION_KEY=din-hapi-nøkkel
npm run start:stdio
```

## Koble til AI-plattformer

### Copilot Studio

Settings → Actions → Add action → MCP Server

| Felt | Verdi |
|------|-------|
| Server name | `hapi-helsedirektoratet` |
| Server URL | `https://<din-app>.azurecontainerapps.io/mcp` |
| Authentication | API Key, header: `x-api-key` |

### Azure AI Foundry

Legg til Remote MCP Server:

| Felt | Verdi |
|------|-------|
| Endpoint | `https://<din-app>.azurecontainerapps.io/mcp` |
| Authentication | API Key |

### Claude Code

```bash
claude mcp add hapi-helsedirektoratet -- node dist/stdio.js
```

Eller i `.claude/settings.json`:

```json
{
  "mcpServers": {
    "hapi-helsedirektoratet": {
      "command": "node",
      "args": ["dist/stdio.js"],
      "env": {
        "HAPI_SUBSCRIPTION_KEY": "din-hapi-nøkkel"
      }
    }
  }
}
```

## Deploy

Push til `master` trigger automatisk GitHub Actions som bygger Docker-image og pusher til `ghcr.io`. Azure Container Apps henter imaget automatisk.

## Miljøvariabler

| Variabel | Beskrivelse | Påkrevd |
|----------|-------------|---------|
| `HAPI_SUBSCRIPTION_KEY` | Subscription key for HAPI API | Ja |
| `MCP_API_KEY` | API-nøkkel for å beskytte MCP-endepunktet | Nei (anbefalt) |
| `PORT` | HTTP-port (default: 3000) | Nei |

## Lisens

Privat — Helsedirektoratet QA-miljø.
