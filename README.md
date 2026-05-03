# HAPI MCP Server

MCP-server for Helsedirektoratets HAPI innholdstjenester. Serveren gir AI-agenter tilgang til norske nasjonale faglige retningslinjer, veiledere, anbefalinger, pakkeforløp, kvalitetsindikatorer, legemiddeldata og utvalgte lokale kunnskapsbaser.

<img width="817" height="1162" alt="HAPI infographic" src="https://github.com/user-attachments/assets/7b7a5a30-c07f-46af-94e3-3a61171478fb" />

## Arkitektur

```text
Copilot Studio / Azure AI Foundry / Claude Code
        ↓ (MCP over Streamable HTTP / stdio)
HAPI MCP Server
        ↓ (REST)
Helsedirektoratets HAPI API (api-qa.helsedirektoratet.no)
        ↓
Lokale SQLite-kilder (NDLA og Felleskatalogen POC)
```

## Funksjonalitet

- HAPI-oppslag i retningslinjer, veiledere, pakkeforløp, anbefalinger og kvalitetsindikatorer
- Fritekstsøk og generisk innholdshenting med filtre
- Legemiddeloppslag i FEST-data og interaksjonsoppslag via interaksjoner.no
- NDLA-fagstoff for Helsefremmende arbeid (HS-HEA vg2) fra lokal SQLite/FTS5-database
- Felleskatalogen POC-oppslag for utvalgte preparater fra lokal SQLite/FTS5-database
- HTTP-transport for Copilot Studio / Azure AI Foundry og stdio-transport for Claude Code

## Tilgjengelige MCP-tools

### HAPI innhold

| Tool | Beskrivelse |
|------|-------------|
| `sok_innhold` | Fritekst-søk i Helsedirektoratets innhold |
| `hent_retningslinjer` | Liste over alle nasjonale faglige retningslinjer |
| `hent_retningslinje` | Spesifikk retningslinje etter ID |
| `hent_anbefalinger` | Anbefalinger, filtrerbare på kodeverk, kode og anbefalingstype |
| `hent_anbefaling` | Spesifikk anbefaling etter ID |
| `hent_veiledere` | Liste over alle nasjonale veiledere |
| `hent_veileder` | Spesifikk veileder etter ID |
| `hent_pakkeforlop` | Liste over alle pakkeforløp |
| `hent_pakkeforlop_id` | Spesifikt pakkeforløp etter ID |
| `hent_innhold` | Generisk innholdshenting med filtre |
| `hent_innhold_id` | Spesifikt innhold etter ID |
| `hent_kvalitetsindikatorer` | Nasjonale kvalitetsindikatorer |
| `hent_kvalitetsindikator` | Spesifikk kvalitetsindikator etter ID |
| `hent_endringer` | Endringer siden et gitt tidspunkt |

### Legemidler og interaksjoner

| Tool | Beskrivelse |
|------|-------------|
| `sok_legemidler` | Søk i FEST-legemiddelregisteret etter navn, virkestoff, ATC-kode eller form |
| `hent_legemiddel` | Hent detaljert legemiddelinfo etter ID |
| `sjekk_interaksjoner` | Sjekk legemiddelinteraksjoner mellom to eller flere legemidler/virkestoff |
| `hent_interaksjon` | Hent interaksjonsdetaljer med mekanisme, håndtering og referanser |

### Lokale kunnskapsbaser

| Tool | Beskrivelse |
|------|-------------|
| `sok_ndla_helsefag` | Søk i NDLAs fagstoff for Helsefremmende arbeid (HS-HEA vg2) |
| `hent_ndla_artikkel` | Hent full NDLA-artikkel som tekst eller HTML |
| `hent_ndla_temaer` | Liste over tema og undertema i NDLA-faget |
| `list_ndla_ressurser_for_tema` | Liste ressurser under et NDLA-tema |
| `sok_felleskatalogen` | Søk etter preparat i Felleskatalogen POC-databasen |
| `hent_felleskatalogen_dosering` | Hent verbatim tekst fra valgte Felleskatalogen-seksjoner |
| `list_felleskatalogen_preparater` | Liste alle preparater i POC-utvalget |

## Forutsetninger

- Node.js 22+
- HAPI subscription key fra Helsedirektoratet
- SQLite-databasefiler for NDLA og Felleskatalogen hvis de lokale toolsene skal brukes

## Oppsett

```bash
npm install
npm run build
```

Kopier miljøvariabler fra `.env.example` eller eksporter dem i shell før kjøring.

## Lokale data

NDLA- og Felleskatalogen-tools bruker lokale SQLite-filer. Se egne README-filer for detaljer:

- [`ndla-scraper/README.md`](ndla-scraper/README.md)
- [`felleskatalogen-scraper/README.md`](felleskatalogen-scraper/README.md)

Standardstier:

| Kilde | Standard filsti |
|-------|-----------------|
| NDLA | `ndla-scraper/data/ndla_helsefag.db` |
| Felleskatalogen | `felleskatalogen-scraper/data/felleskatalogen.db` |

Alternativt kan stiene overstyres med `NDLA_DB_PATH` og `FELLESKATALOGEN_DB_PATH`.

## Kjøring

### HTTP-server

Brukes av Copilot Studio, Azure AI Foundry og andre klienter som støtter MCP Streamable HTTP.

```bash
export HAPI_SUBSCRIPTION_KEY=din-hapi-nøkkel
export MCP_API_KEY=din-api-nøkkel
export PORT=3000
npm start
```

Endepunkter:

- `POST /mcp` — MCP Streamable HTTP transport
- `GET /health` — helsesjekk

Hvis `MCP_API_KEY` er satt, må klienten sende nøkkelen i en av disse headerne:

- `x-api-key`
- `api-key`
- `ocp-apim-subscription-key`
- `Authorization: Bearer <nøkkel>`

### Stdio-transport

Brukes for lokale MCP-klienter som Claude Code.

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

## Docker

Docker-imaget bygger TypeScript-prosjektet og forventer at de lokale databasefilene finnes før image bygges:

- `ndla-scraper/data/ndla_helsefag.db`
- `felleskatalogen-scraper/data/felleskatalogen.db`

```bash
docker build -t hapi-mcp-server .
docker run --rm -p 3000:3000 \
  -e HAPI_SUBSCRIPTION_KEY=din-hapi-nøkkel \
  -e MCP_API_KEY=din-api-nøkkel \
  hapi-mcp-server
```

## Deploy

Push til `master` trigger GitHub Actions som bygger Docker-image og pusher til `ghcr.io`. Azure Container Apps kan deretter hente imaget automatisk.

## Miljøvariabler

| Variabel | Beskrivelse | Påkrevd |
|----------|-------------|---------|
| `HAPI_SUBSCRIPTION_KEY` | Subscription key for HAPI API | Ja |
| `MCP_API_KEY` | API-nøkkel for å beskytte MCP-endepunktet | Nei, men anbefalt for HTTP |
| `PORT` | HTTP-port | Nei, default `3000` |
| `NDLA_DB_PATH` | Alternativ sti til NDLA SQLite-database | Nei |
| `FELLESKATALOGEN_DB_PATH` | Alternativ sti til Felleskatalogen SQLite-database | Nei |

## Scripts

| Script | Beskrivelse |
|--------|-------------|
| `npm run build` | Kompilerer TypeScript til `dist/` |
| `npm start` | Starter HTTP-serveren fra `dist/index.js` |
| `npm run start:stdio` | Starter stdio-transport fra `dist/stdio.js` |

## Governance og lisens

- Data fra Helsedirektoratets HAPI API er underlagt vilkårene for API-et og aktuell datakilde.
- NDLA-innhold er CC-BY-SA-4.0 og skal krediteres med kilde-URL.
- Felleskatalogen-data i dette repoet er POC-data. Kommersiell avtale er ikke etablert, og innholdet må ikke distribueres bredt før lisens er på plass.
- Dette er et tredjepartsverktøy og fremstår ikke som Helsedirektoratet selv.

