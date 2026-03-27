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
MODEL = os.environ.get("MODEL_DEPLOYMENT", "gpt-5.3-chat")

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
            "Du soeker og presenterer normerende produkter fra Helsedirektoratet via HAPI MCP Server.\n"
            "Haandterer: retningslinjer, anbefalinger, faglige raad, pakkeforloep, veiledere, rundskriv.\n"
            "Kodeverk: ICD-10, ICPC-2, SNOMED-CT, ATC, takstkode.\n"
            "For antibiotika: inkluder foerstevalg, alternativ, dosering, varighet, kontraindikasjoner.\n"
            "Regler: Aldri endre faglig innhold. Oppgi infoId og kilde (Helsedirektoratet). "
            "Marker styrkegrad (sterk/svak). Angi sistFagligOppdatert."
        ),
        "allowed_tools": [
            "sok_innhold",
            "hent_retningslinjer",
            "hent_retningslinje",
            "hent_anbefalinger",
            "hent_anbefaling",
            "hent_innhold",
            "hent_innhold_id",
        ],
        "has_mcp": True,
    },
    "hapi-kodeverk-agent": {
        "instructions": (
            "Du gjor oppslag i medisinske kodeverk og legemiddeldata via HAPI MCP Server.\n"
            "Kodeverk (bruk EKSAKT skrivemate): ICD-10, ICPC-2, SNOMED-CT, ATC, takstkode.\n"
            "Legemiddeldata fra FEST: infoTyper=legemiddel, legemiddelpakning, legemiddelvirkestoff, atc-kode.\n"
            "Mapping: Soek innhold kodet med kildekode, inspiser koder-feltet for ekvivalenter. "
            "Rapporter konfidens (hoey/middels/lav). Oppgi alltid kilde."
        ),
        "allowed_tools": [
            "sok_innhold",
            "hent_innhold",
            "hent_innhold_id",
            "hent_anbefalinger",
        ],
        "has_mcp": True,
    },
    "hapi-statistikk-agent": {
        "instructions": (
            "Du henter nasjonale kvalitetsindikatorer (NKI) og helsestatistikk via HAPI MCP Server.\n"
            "NKI: infoTyper=NKI, kildekode 0003, typekode 0010.\n"
            "Oppgaver: hent indikatorer, sammenlign mot maal, identifiser trender.\n"
            "Regler: Oppgi tall med kilde og periode. Ikke ekstrapoler. "
            "Statistikk alene gir IKKE grunnlag for kliniske beslutninger. Kilde: Helsedirektoratet."
        ),
        "allowed_tools": [
            "hent_kvalitetsindikatorer",
            "hent_kvalitetsindikator",
            "sok_innhold",
        ],
        "has_mcp": True,
    },
    "hapi-orkestrator": {
        "instructions": (
            "Du er en orkestrator som ruter brukerens spoersmaal til riktig HAPI-agent. "
            "Du har IKKE direkte tilgang til HAPI-data. Bruk sok_innhold kun for aa forstaa konteksten.\n\n"
            "Agenter du ruter til:\n"
            "- hapi-retningslinje-agent: behandling, anbefalinger, retningslinjer, pakkeforloep, antibiotika\n"
            "- hapi-kodeverk-agent: kodeverk (ICD-10/ICPC-2/SNOMED/ATC), mapping, legemiddeldata\n"
            "- hapi-statistikk-agent: kvalitetsindikatorer (NKI), statistikk, trender\n\n"
            "Regler: Aldri endre faglig innhold. Oppgi kilde: Helsedirektoratet. "
            "Ikke fremstaa som Helsedirektoratet. "
            "Gjenta alltid brukerens noekkelbegreper (diagnose, legemiddel, tema) i svaret."
        ),
        "allowed_tools": [
            "sok_innhold",
        ],
        "has_mcp": True,
    },
}


def deploy_agent(client: AIProjectClient, agent_name: str, config: dict) -> dict:
    """Opprett en agent i Azure AI Foundry."""
    tools = []
    if config.get("has_mcp"):
        tools.append(hapi_mcp_tool(config["allowed_tools"]))

    agent = client.agents.create_version(
        agent_name=agent_name,
        definition=PromptAgentDefinition(
            model=MODEL,
            instructions=config["instructions"],
            tools=tools,
        ),
    )

    result = {
        "name": agent.name,
        "id": agent.id,
        "version": agent.version,
    }
    print(f"  OK: {agent_name} (id: {agent.id}, versjon: {agent.version})")
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
