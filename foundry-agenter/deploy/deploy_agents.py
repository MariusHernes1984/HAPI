"""
Deploy HAPI-agenter til Azure AI Foundry.

Oppretter 5 agenter:
  1. HAPI Retningslinje-agent  (MCP: HAPI)
  2. HAPI Kodeverk-agent       (MCP: HAPI)
  3. HAPI Statistikk-agent     (MCP: HAPI)
  4. HAPI Orkestrator           (ren ruter, ingen verktoy)
  5. CRM Kundealias-agent       (Azure AI Search: kundealias-crm)

Bruk:
  1. Kopier .env.example til .env og fyll inn verdier
  2. pip install -r requirements.txt
  3. az login
  4. python deploy_agents.py
  5. python deploy_agents.py --only crm-kundealias-agent  (deploy kun CRM)
"""

import os
import sys
import json
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    MCPTool,
    PromptAgentDefinition,
    AzureAISearchTool,
    AzureAISearchToolResource,
    AISearchIndexResource,
    AzureAISearchQueryType,
)

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

# Azure AI Search (for CRM-agent)
SEARCH_CONNECTION_NAME = os.environ.get("SEARCH_CONNECTION_NAME", "kateaisearchd2ftyf")
SEARCH_INDEX_NAME = os.environ.get("SEARCH_INDEX_NAME", "kundealias-crm")

# --- Tool-definisjoner ---

def ai_search_tool(client: AIProjectClient) -> AzureAISearchTool:
    """Opprett en AzureAISearchTool for Kundealias CRM-indeksen."""
    connection = client.connections.get(SEARCH_CONNECTION_NAME)
    return AzureAISearchTool(
        azure_ai_search=AzureAISearchToolResource(
            indexes=[
                AISearchIndexResource(
                    project_connection_id=connection.id,
                    index_name=SEARCH_INDEX_NAME,
                    query_type=AzureAISearchQueryType.SIMPLE,
                    top_k=10,
                ),
            ]
        )
    )


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
            "Du er en orkestrator som besvarer helsefoersmaal ved aa kombinere din fagkunnskap "
            "med tydelig routing til spesialiserte HAPI-agenter.\n\n"
            "For hvert spoersmaal:\n"
            "1. Besvar spoersmalet fra din eksisterende kunnskap om norsk helsetjeneste.\n"
            "2. Oppgi hvilken HAPI-agent som ville haandtert den aktuelle delen:\n"
            "   - hapi-retningslinje-agent: behandling, anbefalinger, retningslinjer, pakkeforloep, antibiotika\n"
            "   - hapi-kodeverk-agent: kodeverk (ICD-10/ICPC-2/SNOMED/ATC), legemiddeldata\n"
            "   - hapi-statistikk-agent: nasjonale kvalitetsindikatorer (NKI), statistikk\n\n"
            "Regler: Aldri endre faglig innhold. Oppgi kilde: Helsedirektoratet. "
            "Ikke fremstaa som Helsedirektoratet. "
            "Gjenta alltid brukerens noekkelbegreper i svaret."
        ),
        "allowed_tools": [],
        "has_mcp": False,
    },
    "crm-kundealias-agent": {
        "instructions": (
            "Du er en CRM-assistent som hjelper med aa slaa opp kundeinformasjon "
            "fra Kundealias CRM-databasen via Azure AI Search.\n\n"
            "Du kan svare paa spoersmaal om:\n"
            "- Kundenavn og kundealias\n"
            "- SuperOffice-IDer\n"
            "- ACP-IDer\n"
            "- Partnercenter-GUIDer\n\n"
            "Naar du faar et spoersmaal:\n"
            "1. Soek i indeksen med kundenavn, alias eller ID\n"
            "2. Presenter resultatene tydelig med alle tilgjengelige felt\n"
            "3. Hvis flere treff: list alle og spoer om bruker vil ha mer detalj\n\n"
            "Svar alltid paa norsk. Oppgi kilde: Kundealias CRM (Dataverse)."
        ),
        "has_mcp": False,
        "has_search": True,
    },
}


def deploy_agent(client: AIProjectClient, agent_name: str, config: dict) -> dict:
    """Opprett en agent i Azure AI Foundry."""
    tools = []
    if config.get("has_mcp"):
        tools.append(hapi_mcp_tool(config["allowed_tools"]))
    if config.get("has_search"):
        tools.append(ai_search_tool(client))

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
    # --only flag: deploy kun en spesifikk agent
    only_agent = None
    if "--only" in sys.argv:
        idx = sys.argv.index("--only")
        if idx + 1 < len(sys.argv):
            only_agent = sys.argv[idx + 1]

    print(f"Kobler til Azure AI Foundry: {PROJECT_ENDPOINT}")
    print(f"MCP Server: {MCP_SERVER_URL}")
    print(f"Modell: {MODEL}\n")

    client = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )

    agents_to_deploy = AGENTS
    if only_agent:
        if only_agent not in AGENTS:
            print(f"FEIL: Ukjent agent '{only_agent}'")
            print(f"Tilgjengelige: {', '.join(AGENTS.keys())}")
            sys.exit(1)
        agents_to_deploy = {only_agent: AGENTS[only_agent]}

    print(f"Oppretter {len(agents_to_deploy)} agent(er)...\n")

    results = {}
    for agent_name, config in agents_to_deploy.items():
        results[agent_name] = deploy_agent(client, agent_name, config)

    print("\n--- Deployment fullfort ---\n")

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
