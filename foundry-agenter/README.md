# HAPI Foundry-agenter

Multi-agent-system bygget på [Helsedirektoratets API (HAPI)](https://api.helsedirektoratet.no) og deployet til **Azure AI Foundry Agent Service**. Agentene gir klinisk beslutningsstøtte basert på nasjonale faglige retningslinjer, kodeverk og kvalitetsindikatorer.

---

## Arkitektur

```
Bruker
  │
  ▼
HAPI Orkestrator          ← Ruter forespørsler til riktig agent
  ├── HAPI Retningslinje-agent  ← Retningslinjer, anbefalinger, pakkeforløp, antibiotika
  ├── HAPI Kodeverk-agent       ← ICD-10, ICPC-2, SNOMED CT, ATC, legemiddeldata
  ├── HAPI Statistikk-agent     ← Nasjonale kvalitetsindikatorer (NKI)
  └── HAPI Kjernejournal-agent  ← Pasientens journal (mock, 50 pasienter)
         │
         ▼
  HAPI MCP Server (Azure Container Apps)
         │
         ▼
  api.helsedirektoratet.no
```

---

## Agenter

### HAPI Orkestrator
**Navn:** `hapi-orkestrator`

Koordinerer forespørsler og ruter til riktig spesialistagent. Bruker `sok_innhold` for å forstå kontekst, men henter ikke data selv.

| Ruter til | Når |
|---|---|
| `hapi-retningslinje-agent` | Behandling, anbefalinger, retningslinjer, pakkeforløp, antibiotika |
| `hapi-kodeverk-agent` | Kodeverk (ICD-10/ICPC-2/SNOMED/ATC), mapping, legemiddeldata |
| `hapi-statistikk-agent` | Kvalitetsindikatorer (NKI), statistikk, trender |
| `hapi-kjernejournal-agent` | Aktiv pasient valgt i dropdown → journal-kontekst |

**MCP-verktøy:** `sok_innhold`

---

### HAPI Retningslinje-agent
**Navn:** `hapi-retningslinje-agent`

Søker og presenterer normerende produkter fra Helsedirektoratet.

**Håndterer:**
- Nasjonale faglige retningslinjer og anbefalinger
- Faglige råd
- Pakkeforløp (inkl. kreftpakkeforløp)
- Nasjonale veiledere og rundskriv
- Antibiotika-retningslinjer (med doseringsregimer, styrke, kontraindikasjoner)

**MCP-verktøy:** `sok_innhold`, `hent_retningslinjer`, `hent_retningslinje`, `hent_anbefalinger`, `hent_anbefaling`, `hent_innhold`, `hent_innhold_id`

---

### HAPI Kodeverk-agent
**Navn:** `hapi-kodeverk-agent`

Slår opp og mapper medisinske kodeverk og henter legemiddeldata.

**Håndterer:**
- ICD-10, ICPC-2, SNOMED CT, ATC, takstkode, LIS-koder
- Legemiddeldata fra FEST (virkestoff, pakning, ATC-klassifisering)
- Kode-mapping på tvers av kodeverk med konfidensgradering

**MCP-verktøy:** `sok_innhold`, `hent_innhold`, `hent_innhold_id`, `hent_anbefalinger`

---

### HAPI Statistikk-agent
**Navn:** `hapi-statistikk-agent`

Henter og presenterer nasjonale kvalitetsindikatorer (NKI).

**Håndterer:**
- Nasjonale kvalitetsindikatorer (NKI)
- Sammenligning mot nasjonale mål
- Trendidentifikasjon (stigende/stabil/fallende)
- Kobling mellom statistikk og diagnosekoder

**MCP-verktøy:** `hent_kvalitetsindikatorer`, `hent_kvalitetsindikator`, `sok_innhold`

---

### HAPI Kjernejournal-agent
**Navn:** `hapi-kjernejournal-agent`

Lokal mock-agent (ikke Foundry) som returnerer pasientens journaldata. Aktiveres automatisk når en pasient er valgt i dropdown.

**Håndterer:**
- Diagnoser, faste medisiner, allergier og kliniske merknader for 50 fiktive pasienter
- Personalisert kontekst til syntese-steget slik at anbefalinger tar hensyn til pasientens legemidler og kontraindikasjoner

**Pasientkategorier (50 stk):**

| Kategori | Antall | ID-er |
|---|---|---|
| Blodfortynnende (warfarin/DOAK) | 10 | P-001 – P-010 |
| Diabetes type 2 | 8 | P-011 – P-018 |
| KOLS | 6 | P-019 – P-024 |
| Nyresvikt (CKD) | 5 | P-025 – P-029 |
| Astma | 5 | P-030 – P-034 |
| Polyfarmasi eldre | 3 | P-035 – P-037 |
| Friske / lett sykdom | 13 | P-038 – P-050 |

**Datakilde:** `mock-data/pasienter.json` (fiktive data, ingen ekte personopplysninger)

---

## MCP Server

HAPI MCP Serveren kjører i **Azure Container Apps**, men eksponeres utad via **Azure API Management**.

| Variabel | Verdi |
|---|---|
| `MCP_SERVER_URL` | `https://<apim-gateway>.azure-api.net/mcp` |
| Auth | API Management subscription key (`Ocp-Apim-Subscription-Key`) |

---

## Deploy

### Forutsetninger
- Python 3.10+
- Azure CLI (`az login`)
- Tilgang til Azure AI Foundry-prosjektet

### Kjør deployment

```bash
cd foundry-agenter/deploy
pip install -r requirements.txt
az login
python deploy_agents.py
```

Skriptet oppretter alle 4 agenter og lagrer agent-IDer til `deployed_agents.json`.

### Konfigurasjon

Rediger `.env` eller sett miljøvariabler:

```env
PROJECT_ENDPOINT=https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem
MCP_SERVER_URL=https://<apim-gateway>.azure-api.net/mcp
MODEL_DEPLOYMENT=gpt-5.3-chat
```

---

## Testing

### Kjør alle tester (5 scenarioer)

```bash
cd foundry-agenter/deploy
python test_agents.py
```

### Kjør spesifikk agent eller scenario

```bash
python test_agents.py --agent hapi-orkestrator
python test_agents.py --scenario TC-001
```

### Testscenarioer

| ID | Agent | Spørsmål |
|---|---|---|
| TC-001 | hapi-orkestrator | Hva er anbefalt behandling for KOLS? |
| TC-002 | hapi-retningslinje-agent | Vis retningslinjer for diabetes type 2 |
| TC-003 | hapi-kodeverk-agent | Hva er ICPC-2-koden for ICD-10 J44? |
| TC-004 | hapi-statistikk-agent | Hvilke kvalitetsindikatorer finnes for KOLS? |
| TC-005 | hapi-orkestrator | Hvilken antibiotika anbefales for pneumoni? |

---

## Test-spørsmål (100 stk)

Mappen `test-questions/` inneholder 100 ferdiglagde spørsmål for demo og testing:

```
test-questions/
└── sporsmal.json    ← 100 spørsmål kategorisert etter agent og tema
```

### Fordeling

| Kategori | Antall | Beskrivelse |
|---|---|---|
| Retningslinjer | 25 | Behandling, anbefalinger, pakkeforløp |
| Antibiotika | 5 | Doseringsregimer, allergi-alternativer |
| Kodeverk | 20 | Kodeoppslag og mapping |
| Legemidler | 10 | Virkestoff, ATC, preparater |
| Statistikk (NKI) | 15 | Kvalitetsindikatorer og mål |
| Sammensatt | 25 | Involverer 2–3 agenter, høy demo-verdi |

### Bruk i demo

For å kjøre et enkelt spørsmål mot orkestratoren:

```python
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
openai = client.get_openai_client()

conversation = openai.conversations.create()
response = openai.responses.create(
    conversation=conversation.id,
    input="Gi meg en komplett klinisk beslutningsstøtte for hjertesvikt",
    extra_body={"agent_reference": {"name": "hapi-orkestrator", "type": "agent_reference"}},
)
print(response.output_text)
```

---

## Filstruktur

```
foundry-agenter/
├── README.md
├── agenter/
│   ├── orkestrator-agent.json       ← Agent-konfigurasjon
│   ├── retningslinje-agent.json
│   ├── kodeverk-agent.json
│   └── statistikk-agent.json
├── deploy/
│   ├── deploy_agents.py             ← Deployment-skript
│   ├── test_agents.py               ← Testskript
│   ├── requirements.txt
│   ├── .env.example
│   └── deployed_agents.json         ← Generert ved deploy
├── mock-data/
│   ├── pasienter.json               ← 50 fiktive pasienter (JSON)
│   ├── generate_pasient_pdf.py      ← PDF-generator
│   └── HAPI-mock-pasienter.pdf      ← Pasientoversikt (generert)
├── orchestrator/
│   ├── app.py                       ← FastAPI web-app
│   ├── orchestrate.py               ← Multi-agent orkestrering + syntese
│   ├── router.py                    ← Spørsmålsrouter
│   ├── kjernejournal.py             ← Lokal mock-agent for pasientjournal
│   ├── static/index.html            ← Web-UI
│   └── Dockerfile
└── test-questions/
    └── sporsmal.json                ← 100 demo/testspørsmål
```

---

## Governance og lisens

- **Kilde:** Alle data fra Helsedirektoratets API (HAPI) er underlagt [NLOD (Norsk lisens for offentlige data)](https://data.norge.no/nlod)
- **Faglig innhold:** Anbefalinger gjengis uendret — aldri endre faglig meningsinnhold
- **Kildeangivelse:** Alle svar skal oppgi "Kilde: Helsedirektoratet"
- **Ansvar:** Dette er et tredjepartsverktøy og fremstår ikke som Helsedirektoratet selv
