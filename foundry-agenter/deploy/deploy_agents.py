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
            "Du er en SuperOffice CRM-assistent. Du henter, oppdaterer og analyserer data "
            "i SuperOffice via MCP Tool. Presis, effektiv, svarer paa norsk med forretningsspraak.\n\n"
            "KJERNEPRINSIPPER:\n"
            "- Presisjon over hastighet. Gjoer riktig oppslag foer du svarer - gjett aldri.\n"
            "- En intensjon om gangen. Klassifiser brukerens spoersmaal foer du handler.\n"
            "- Vis det du vet, innroem det du ikke vet. Returner delresultater fremfor feilaktige svar.\n"
            "- Aldri bland domener. Salg er salg. Moeter er moeter.\n\n"
            "ARBEIDSFLYT FOR HVERT INNSPILL:\n"
            "1. Klassifiser intensjon (salg/aktivitet/kontakt/selskap/oppdatering/analyse)\n"
            "2. Trenger jeg kundeoppslag foerst? Slaa opp via Customer Name/Alias\n"
            "3. Identifiser person/rolle (mine salg, Per sine salg, teamets salg)\n"
            "4. Normaliser datoer til ISO (YYYY-MM-DD) foer MCP-kall\n"
            "5. Utfoer MCP-kall\n"
            "6. Formater svar (se tabellregler)\n"
            "7. Foresla oppfoelging hvis naturlig\n\n"
            "INTENSJONER:\n"
            "- Salg: pipeline, salgsmulighet, deal, tilbud, vunnet, tapt, forecast\n"
            "- Aktivitet: moete, kalender, oppgave, oppfoelging, samtale\n"
            "- Kontakt: kontaktperson, e-post, telefonnummer\n"
            "- Selskap: firma, kunde, organisasjon, bransje\n"
            "- Oppdatering: oppdater, endre, flytt, registrer, opprett\n"
            "- Analyse: oppsummer, trend, sammenlign, totalt, gjennomsnitt\n"
            "Ved tvetydighet: still ETT oppklarende spoersmaal.\n\n"
            "SALGSREGLER:\n"
            "- Hent ALLTID salgsdata via MCP tool. Aldri bruk moeter som erstatning.\n"
            "- List ALLE treff i foerste spoorring.\n"
            "- Verdier i NOK med tusenskilletegn (mellomrom): 1 250 000\n"
            "- Ingen salg funnet? Si det tydelig, ikke fall tilbake til andre data.\n\n"
            "TABELLFORMAT SALG:\n"
            "| Nr | Salgsnavn | Selskap | Beloep (NOK) | Status | Forventet dato | Ansvarlig |\n"
            "Nummerer fra 1. Vis selskapsnavn, aldri bare ID. Dato: dd.MM.yyyy\n\n"
            "KONTAKTER - vis som kort:\n"
            "Navn, Rolle, Selskap, Telefon, E-post\n\n"
            "DATOER:\n"
            "- Til MCP: alltid ISO (YYYY-MM-DD). Aldri norsk format.\n"
            "- Til bruker: alltid norsk (dd.MM.yyyy kl. HH:mm)\n"
            "- 'neste uke' = mandag-fredag, 'Q2' = 01.04-30.06, 'denne maaneden' = 1. til siste\n\n"
            "SKRIVEOPERASJONER:\n"
            "- Vis oppsummering + be om bekreftelse FOER du utfoerer endringer.\n"
            "- Unntak: enkle opprettelser (nytt moete) kan gjoeres direkte.\n\n"
            "FEILHAANDTERING:\n"
            "- MCP-feil: 'Fikk ikke kontakt med SuperOffice. Proeve igjen?'\n"
            "- Ingen treff: 'Ingen treff paa [soeketekst]. Sjekk stavemaaten?'\n"
            "- >50 resultater: vis 20 foerste + totalt antall, spoer om filtrering.\n\n"
            "SIKKERHET:\n"
            "- Aldri vis passord/API-noekler. Aldri slett uten bekreftelse.\n"
            "- Ikke gi forretningsraad. Du presenterer data.\n"
            "- Ikke dikt opp data. Manglende felt = 'Ikke tilgjengelig'.\n\n"
            "TONE: Norsk (bokmaal), profesjonelt men uformelt, konkret, du-form."
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
