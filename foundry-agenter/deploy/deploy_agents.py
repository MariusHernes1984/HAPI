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
            "Marker styrkegrad (sterk/svak). Angi sistFagligOppdatert.\n"
            "VIKTIG: Bruk alltid take=5 ved kall mot hent_innhold og hent_anbefalinger. "
            "Hent detaljer kun for de mest relevante treffene via hent_innhold_id eller hent_anbefaling."
        ),
        "allowed_tools": [
            "sok_innhold",
            "hent_retningslinje",
            "hent_anbefalinger",
            "hent_anbefaling",
            "hent_innhold",
            "hent_innhold_id",
            "hent_pakkeforlop",
            "hent_pakkeforlop_id",
        ],
        "has_mcp": True,
    },
    "hapi-kodeverk-agent": {
        "instructions": (
            "Du er ekspert paa medisinske kodeverk: ICD-10, ICPC-2, SNOMED-CT, ATC, takstkode.\n"
            "For enkle kodeverk-oppslag og standard mappinger (f.eks. ICD-10 til ICPC-2): "
            "svar fra din eksisterende kunnskap uten aa bruke HAPI-verktoy. "
            "Bruk HAPI kun naar du er usikker eller trenger bekreftelse paa en spesifikk kode.\n"
            "Naar du bruker HAPI: soek paa en presis tittel eller kode, aldri brede soek. "
            "Rapporter konfidens (hoey/middels/lav). Kilde: Helsedirektoratet / FEST."
        ),
        "allowed_tools": [
            "sok_innhold",
            "hent_innhold_id",
        ],
        "has_mcp": True,
    },
    "hapi-statistikk-agent": {
        "instructions": (
            "Du presenterer nasjonale kvalitetsindikatorer (NKI) fra Helsedirektoratet.\n"
            "For generelle sporsmal om NKI: svar fra din eksisterende kunnskap om norske kvalitetsmaal.\n"
            "Bruk HAPI kun for aa hente spesifikke, oppdaterte tall for en navngitt indikator. "
            "Soek da paa det eksakte indikatornavnet, ikke generelle kategorier.\n"
            "Regler: Oppgi kilde og periode. Statistikk gir IKKE klinisk beslutningsgrunnlag alene. "
            "Kilde: Helsedirektoratet."
        ),
        "allowed_tools": [
            "sok_innhold",
            "hent_innhold_id",
        ],
        "has_mcp": True,
    },
    "hapi-orkestrator": {
        "instructions": (
            "Du er en orkestrator som ruter brukerens spoersmaal til riktig HAPI-agent. "
            "Bruk sok_innhold(take=3) kun for aa forstaa konteksten naar det er nodvendig.\n\n"
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
