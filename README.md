# HAPI MCP Server

MCP server for Helsedirektoratets HAPI innholdstjenester — gir AI-agenter tilgang til norske nasjonale faglige retningslinjer, veiledere, anbefalinger og pakkeforløp.

<img width="1024" height="1536" alt="Bud4fgRsjns (1)" src="https://github.com/user-attachments/assets/7b4983b8-e7f1-450f-8c99-2e7872ae6dea" />


## Arkitektur

```
Copilot Studio / AI Foundry / Claude Code
        ↓ (MCP over Streamable HTTP / stdio)
Azure Container Apps
        ↓ (REST)
Helsedirektoratets HAPI API (api-qa.helsedirektoratet.no)
```

## Tilgjengelige tools

| Tool | Beskrivelse |
|------|-------------|
| `sok_innhold` | Fritekst-søk i alt innhold |
| `hent_retningslinjer` | Liste over alle nasjonale faglige retningslinjer |
| `hent_retningslinje` | Spesifikk retningslinje etter ID (inkl. kapitler og anbefalinger) |
| `hent_anbefalinger` | Anbefalinger, filtrerbare på kodeverk (ICPC-2, ICD-10) og kode |
| `hent_anbefaling` | Spesifikk anbefaling etter ID |
| `hent_veiledere` | Liste over alle nasjonale veiledere |
| `hent_veileder` | Spesifikk veileder etter ID |
| `hent_pakkeforlop` | Liste over alle pakkeforløp |
| `hent_pakkeforlop_id` | Spesifikt pakkeforløp etter ID |
| `hent_innhold` | Generisk innholdshenting med filtre (infotype, kodeverk, målgruppe) |
| `hent_innhold_id` | Spesifikt innhold etter ID |
| `hent_kvalitetsindikatorer` | Nasjonale kvalitetsindikatorer |
| `hent_kvalitetsindikator` | Spesifikk kvalitetsindikator etter ID |
| `hent_endringer` | Endringer siden et gitt tidspunkt |
| `sok_legemidler` | Søk i FEST-legemiddelregisteret etter navn, virkestoff, ATC-kode eller form |
| `hent_legemiddel` | Hent detaljert legemiddelinfo etter ID (virkestoff, styrke, pakninger) |
| `sjekk_interaksjoner` | Sjekk legemiddelinteraksjoner (FEST/SLV) — faregrad, klinisk konsekvens |
| `hent_interaksjon` | Hent interaksjonsdetaljer — mekanisme, håndtering, PubMed-referanser |

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
