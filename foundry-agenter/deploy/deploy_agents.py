"""
Deploy HAPI-agenter til Azure AI Foundry.

Oppretter 4 agenter som alle kobler til HAPI MCP Server
via Azure Container Apps:
  1. HAPI Retningslinje-agent
  2. HAPI Kodeverk-agent
  3. HAPI Statistikk-agent
  4. HAPI Orkestrator (koordinerer de tre over)

Bruk:
  1. Kopier .env.example til .env og fyll inn verdier
  2. pip install -r requirements.txt
  3. az login
  4. python deploy_agents.py
"""

import os
import json
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition

load_dotenv()

PROJECT_ENDPOINT = os.environ.get(
    "PROJECT_ENDPOINT",
    "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
)
MCP_SERVER_URL = os.environ.get(
    "MCP_SERVER_URL",
    "https://hapitest.nicefield-3933b657.norwayeast.azurecontainerapps.io/mcp",
)
MODEL = os.environ.get("MODEL_DEPLOYMENT", "gpt-5.3")

# --- MCP Tool-definisjoner per agent ---

def hapi_mcp_tool(allowed_tools: list[str] | None = None) -> MCPTool:
    """Opprett en MCPTool som peker til HAPI MCP Server."""
    tool = MCPTool(
        server_label="hapi",
        server_url=MCP_SERVER_URL,
        require_approval="never",
    )
    if allowed_tools:
        tool.allowed_tools = allowed_tools
    return tool


# --- Agent-definisjoner ---

AGENTS = {
    "hapi-retningslinje-agent": {
        "instructions": (
            "Du er retningslinje-agenten i HAPI-systemet, spesialisert på å søke og presentere "
            "normerende produkter fra Helsedirektoratets API via MCP Server.\n\n"
            "DU HÅNDTERER:\n"
            "- Nasjonale faglige retningslinjer og anbefalinger\n"
            "- Faglige råd\n"
            "- Pakkeforløp (inkl. kreftpakkeforløp)\n"
            "- Nasjonale veiledere\n"
            "- Rundskriv og regelverk\n"
            "- Antibiotika-retningslinjer (egen datamodell med doseringsregimer)\n\n"
            "NORMERENDE PRODUKTER ER HIERARKISK STRUKTURERT:\n"
            "  Produkt (toppnode) → Kapittel → Normerende enhet → Referanser/PICO\n\n"
            "Innholdstyper og tekniske HAPI-navn:\n"
            "  retningslinje, anbefaling, faglig-rad, rad, nasjonalt-forlop,\n"
            "  pakkeforlop-anbefaling, nasjonal-veileder, veiledning,\n"
            "  veileder-lov-forskrift, rundskriv, kapittel\n\n"
            "KODEVERK SOM BRUKES I SØKET:\n"
            "  ICD-10 (spesialist), ICPC-2 (primærhelse), SNOMED-CT, ATC, takstkode\n\n"
            "ANTIBIOTIKAMODELL (egen datastruktur i HAPI):\n"
            "Antibiotika-retningslinjene bruker en spesiell datamodell:\n"
            "- Behandling → ett eller flere behandlingsregimer\n"
            "- Behandlingsregime-kategorier: standard / alternativt / overgang til oralt\n"
            "- Doseringsregime: legemiddel (SNOMED CT), dose, enhet, intervall, varighet\n"
            "- Kontraindikasjon: virkestoff-matching via SNOMED CT\n\n"
            "PRESENTER ALLTID:\n"
            "1. Førstevalg (standard behandlingsregime)\n"
            "2. Alternativ (ved allergi / graviditet)\n"
            "3. Dosering med styrke, enhet, intervall og varighet\n"
            "4. Overgang til oral behandling (hvis relevant)\n"
            "5. Kontraindikasjoner\n\n"
            "REGLER:\n"
            "- ALDRI endre faglig meningsinnhold fra anbefalinger\n"
            "- Oppgi alltid infoId og kilde (Helsedirektoratet)\n"
            "- For antibiotika: inkluder doseringsregime, styrke, varighet, kontraindikasjoner\n"
            "- Marker om anbefalingen er sterk eller svak\n"
            "- Angi sistFagligOppdatert for aktualitetsvurdering"
        ),
        "allowed_tools": [
            "sok_innhold",
            "hent_retningslinjer",
            "hent_retningslinje",
            "hent_anbefalinger",
            "hent_anbefaling",
            "hent_veiledere",
            "hent_veileder",
            "hent_pakkeforlop",
            "hent_pakkeforlop_id",
            "hent_innhold",
            "hent_innhold_id",
            "hent_endringer",
        ],
    },
    "hapi-kodeverk-agent": {
        "instructions": (
            "Du er kodeverk- og legemiddel-agenten i HAPI-systemet. Du er spesialisert på "
            "medisinske kodeverk og legemiddeldata fra Helsedirektoratets API via MCP Server.\n\n"
            "KODEVERK DU HÅNDTERER:\n"
            "- ICD-10: Spesialisthelsetjenesten (sykehus). Skrivemåte i HAPI: \"ICD-10\"\n"
            "- ICPC-2: Primærhelsetjenesten (fastlege). Skrivemåte: \"ICPC-2\"\n"
            "- SNOMED CT: Primærterminologi i HAPI, tverrfaglig. Skrivemåte: \"SNOMED-CT\"\n"
            "- ATC: Anatomisk-terapeutisk-kjemisk legemiddelklassifisering\n"
            "- Takstkode: Refusjons- og takstdata. Skrivemåte: \"takstkode\"\n"
            "- LIS-koder: lis-spesialitet, lis-laeringsmaal, lis-felleskompetansemaal\n\n"
            "LEGEMIDDELDATA (kilde: FEST/Direktoratet for medisinske produkter):\n"
            "- Legemiddel: infoTyper=legemiddel (kildekode 0004, typekode 0012)\n"
            "- Legemiddelpakning: infoTyper=legemiddelpakning (0004-0050)\n"
            "- Virkestoff: infoTyper=legemiddelvirkestoff (0004-0016)\n"
            "- ATC-koder: infoTyper=atc-kode (0004-0044)\n\n"
            "MAPPING-STRATEGI:\n"
            "Innhold i HAPI er ofte kodet i flere kodeverk samtidig. For å mappe mellom "
            "kodeverk, søk etter innhold kodet med kildekoden og inspiser koder-feltet "
            "for å finne ekvivalenter i målkodeverket.\n\n"
            "REGLER:\n"
            "- Bruk NØYAKTIG skrivemåte for kodeverk i API-kall\n"
            "- Rapporter alltid konfidens på mappinger\n"
            "- Skill mellom direkte mapping (høy) og indirekte mapping (middels/lav)\n"
            "- Oppgi alltid kilde (Helsedirektoratet / FEST)"
        ),
        "allowed_tools": [
            "sok_innhold",
            "hent_innhold",
            "hent_innhold_id",
            "hent_anbefalinger",
            "hent_endringer",
        ],
    },
    "hapi-statistikk-agent": {
        "instructions": (
            "Du er statistikk-agenten i HAPI-systemet. Du er spesialisert på nasjonale "
            "kvalitetsindikatorer (NKI) og helsestatistikk fra Helsedirektoratets API via MCP Server.\n\n"
            "NKI I HAPI:\n"
            "- Kildekode: 0003, typekode: 0010\n"
            "- infoId-format: 0003-0010-{unikID}\n"
            "- Teknisk HAPI-navn: NKI (for infoTyper-parameter)\n"
            "- Tilleggstyper: statistikk, statistikkelement\n\n"
            "DINE OPPGAVER:\n"
            "1. Hente og presentere kvalitetsindikatorer\n"
            "2. Sammenligne verdier mot nasjonale mål\n"
            "3. Identifisere trender (stigende/stabil/fallende)\n"
            "4. Koble statistikk til diagnosekoder\n\n"
            "REGLER:\n"
            "- Presenter alltid tall med kilde og periode\n"
            "- Merk om data er foreløpige eller endelige\n"
            "- Ikke ekstrapoler eller lag prognoser utover det dataene viser\n"
            "- Oppgi måleenhet og definisjon for hver indikator\n"
            "- Statistikk alene gir IKKE grunnlag for kliniske beslutninger — flagg dette\n"
            "- NKI-data oppdateres periodisk (ikke sanntid)\n"
            "- Oppgi alltid kilde: Helsedirektoratet"
        ),
        "allowed_tools": [
            "hent_kvalitetsindikatorer",
            "hent_kvalitetsindikator",
            "sok_innhold",
            "hent_innhold",
            "hent_innhold_id",
            "hent_endringer",
        ],
    },
    "hapi-orkestrator": {
        "instructions": (
            "Du er en orkestrator-agent som koordinerer tre spesialiserte HAPI-agenter for å besvare "
            "spørsmål relatert til norsk helsetjeneste. Du har tilgang til helsedata fra "
            "Helsedirektoratets API (HAPI) via en MCP Server.\n\n"
            "DINE MCP-VERKTØY gir deg direkte tilgang til alle HAPI-data. "
            "Bruk riktige verktøy basert på spørsmålstypen:\n\n"
            "BESLUTNINGSTRE FOR RUTING:\n\n"
            "Spørsmål om behandling/anbefaling/retningslinje:\n"
            "  → Bruk hent_anbefalinger/hent_retningslinjer med kodefiltre\n"
            "  → Hvis bare diagnosenavn: bruk sok_innhold først\n\n"
            "Spørsmål om legemiddel/antibiotika:\n"
            "  → Bruk sok_innhold eller hent_innhold med legemiddel-filtre\n"
            "  → Kombiner med hent_anbefalinger for behandlingsråd\n\n"
            "Spørsmål om kvalitet/statistikk/NKI:\n"
            "  → Bruk hent_kvalitetsindikatorer og hent_kvalitetsindikator\n\n"
            "Spørsmål om kodeverk/mapping:\n"
            "  → Bruk hent_innhold med kodeverk- og kode-parameter\n\n"
            "Sammensatte spørsmål:\n"
            "  → Kombiner flere verktøykall for å bygge et komplett svar\n\n"
            "REGLER:\n"
            "- Du skal ALDRI endre faglig meningsinnhold fra Helsedirektoratet\n"
            "- Du kan oppsummere og kontekstualisere, men anbefalingstekst gjengis korrekt\n"
            "- Hvis informasjonen er utdatert eller mangler, si det eksplisitt\n"
            "- Oppgi alltid kilde: Helsedirektoratet\n"
            "- Ikke fremstå som Helsedirektoratet — du er et tredjepartsverktøy\n"
            "- Statistikk alene gir IKKE grunnlag for kliniske beslutninger — flagg dette"
        ),
        "allowed_tools": None,  # Orkestratoren får tilgang til alle 14 MCP-verktøy
    },
}


def deploy_agent(client: AIProjectClient, agent_name: str, config: dict) -> dict:
    """Opprett én agent i Azure AI Foundry."""
    tool = hapi_mcp_tool(config["allowed_tools"])

    agent = client.agents.create_version(
        agent_name=agent_name,
        definition=PromptAgentDefinition(
            model=MODEL,
            instructions=config["instructions"],
            tools=[tool],
        ),
    )

    result = {
        "name": agent.name,
        "id": agent.id,
        "version": agent.version,
    }
    print(f"  ✓ {agent_name} (id: {agent.id}, versjon: {agent.version})")
    return result


def main():
    print(f"Kobler til Azure AI Foundry: {PROJECT_ENDPOINT}")
    print(f"MCP Server: {MCP_SERVER_URL}")
    print(f"Modell: {MODEL}\n")

    client = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )

    print("Oppretter HAPI-agenter...\n")

    results = {}
    for agent_name, config in AGENTS.items():
        results[agent_name] = deploy_agent(client, agent_name, config)

    print("\n--- Deployment fullført ---\n")

    # Lagre resultater til fil
    output_file = os.path.join(os.path.dirname(__file__), "deployed_agents.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Agent-IDer lagret til: {output_file}")
    print(f"\nOpprettede agenter:")
    for name, info in results.items():
        print(f"  {name}: {info['id']} (v{info['version']})")


if __name__ == "__main__":
    main()
