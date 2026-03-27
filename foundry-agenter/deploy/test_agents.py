"""
Test HAPI-agentene i Azure AI Foundry.

Kjører testscenarioer mot de deployede agentene for å verifisere
at de fungerer korrekt med HAPI MCP Server.

Bruk:
  python test_agents.py
  python test_agents.py --agent hapi-orkestrator
  python test_agents.py --scenario TC-001
"""

import os
import json
import argparse
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

load_dotenv()

PROJECT_ENDPOINT = os.environ.get(
    "PROJECT_ENDPOINT",
    "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
)

TEST_SCENARIOS = [
    {
        "id": "TC-001",
        "agent": "hapi-orkestrator",
        "input": "Hva er anbefalt behandling for KOLS?",
        "expected_keywords": ["KOLS", "anbefaling", "Helsedirektoratet"],
    },
    {
        "id": "TC-002",
        "agent": "hapi-retningslinje-agent",
        "input": "Vis meg retningslinjer for diabetes type 2",
        "expected_keywords": ["diabetes", "retningslinje"],
    },
    {
        "id": "TC-003",
        "agent": "hapi-kodeverk-agent",
        "input": "Hva er ICPC-2-koden for ICD-10 J44?",
        "expected_keywords": ["ICPC-2", "ICD-10", "J44"],
    },
    {
        "id": "TC-004",
        "agent": "hapi-statistikk-agent",
        "input": "Hvilke kvalitetsindikatorer finnes for KOLS?",
        "expected_keywords": ["kvalitetsindikator", "NKI"],
    },
    {
        "id": "TC-005",
        "agent": "hapi-orkestrator",
        "input": "Hvilken antibiotika anbefales for pneumoni?",
        "expected_keywords": ["antibiotika", "pneumoni"],
    },
]


def run_test(client: AIProjectClient, scenario: dict) -> dict:
    """Kjør ett testscenario mot en agent."""
    agent_name = scenario["agent"]
    openai = client.get_openai_client()

    print(f"\n[{scenario['id']}] Agent: {agent_name}")
    print(f"  Input: {scenario['input']}")

    try:
        conversation = openai.conversations.create()

        response = openai.responses.create(
            conversation=conversation.id,
            input=scenario["input"],
            extra_body={
                "agent_reference": {
                    "name": agent_name,
                    "type": "agent_reference",
                }
            },
        )

        output_text = response.output_text
        print(f"  Svar: {output_text[:200]}...")

        # Sjekk om forventede nøkkelord finnes i svaret
        found = [kw for kw in scenario["expected_keywords"] if kw.lower() in output_text.lower()]
        missing = [kw for kw in scenario["expected_keywords"] if kw.lower() not in output_text.lower()]

        passed = len(missing) == 0
        status = "PASS" if passed else "DELVIS"
        print(f"  Status: {status}")
        if missing:
            print(f"  Manglende nøkkelord: {missing}")

        # Rydd opp
        openai.conversations.delete(conversation.id)

        return {
            "id": scenario["id"],
            "agent": agent_name,
            "status": status,
            "found_keywords": found,
            "missing_keywords": missing,
            "response_preview": output_text[:500],
        }

    except Exception as e:
        print(f"  FEIL: {e}")
        return {
            "id": scenario["id"],
            "agent": agent_name,
            "status": "FEIL",
            "error": str(e),
        }


def main():
    parser = argparse.ArgumentParser(description="Test HAPI-agenter")
    parser.add_argument("--agent", help="Kjør kun tester for denne agenten")
    parser.add_argument("--scenario", help="Kjør kun dette scenarioet (f.eks. TC-001)")
    args = parser.parse_args()

    client = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )

    scenarios = TEST_SCENARIOS
    if args.agent:
        scenarios = [s for s in scenarios if s["agent"] == args.agent]
    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] == args.scenario]

    print(f"Kjører {len(scenarios)} testscenario(er)...")

    results = []
    for scenario in scenarios:
        result = run_test(client, scenario)
        results.append(result)

    # Oppsummering
    passed = sum(1 for r in results if r["status"] == "PASS")
    partial = sum(1 for r in results if r["status"] == "DELVIS")
    failed = sum(1 for r in results if r["status"] == "FEIL")

    print(f"\n--- Testresultater ---")
    print(f"  PASS:    {passed}/{len(results)}")
    print(f"  DELVIS:  {partial}/{len(results)}")
    print(f"  FEIL:    {failed}/{len(results)}")

    # Lagre resultater
    output_file = os.path.join(os.path.dirname(__file__), "test_results.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResultater lagret til: {output_file}")


if __name__ == "__main__":
    main()
