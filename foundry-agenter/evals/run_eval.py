"""
HAPI Agent Evaluering — kjoerer 30 evalueringsspoersmaal og verifiserer korrekthet.

Fokus: Svarene maa vaere KORREKTE og IKKE inneholde feilinformasjon.

Bruk:
  python run_eval.py                    # Kjoer alle 30
  python run_eval.py --ids EVAL-001 EVAL-005  # Kjoer spesifikke
  python run_eval.py --kategori kodeverk      # Kjoer en kategori
  python run_eval.py --output rapport.json    # Lagre rapport
"""

import asyncio
import json
import time
import sys
import os
import argparse
from pathlib import Path

from azure.identity.aio import DefaultAzureCredential as AsyncCredential
from azure.ai.projects.aio import AIProjectClient as AsyncProjectClient

# Legg til orchestrator-mappen i path for routing
sys.path.insert(0, str(Path(__file__).parent.parent / "orchestrator"))
from router import route

PROJECT_ENDPOINT = os.environ.get(
    "PROJECT_ENDPOINT",
    "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
)

EVAL_FILE = Path(__file__).parent / "eval-questions.json"


def safe_print(text: str):
    """Print som haandterer unicode paa Windows."""
    print(text.encode("ascii", errors="replace").decode("ascii"))


async def call_agent(project, agent_name: str, query: str) -> dict:
    """Kall en agent og returner svar med metadata."""
    start = time.monotonic()
    try:
        openai = project.get_openai_client()
        conv = await openai.conversations.create()
        resp = await openai.responses.create(
            conversation=conv.id,
            input=query,
            extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
        )
        output = resp.output_text
        duration = int((time.monotonic() - start) * 1000)
        try:
            await openai.conversations.delete(conv.id)
        except Exception:
            pass
        return {"success": True, "output": output, "duration_ms": duration}
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return {"success": False, "output": "", "duration_ms": duration, "error": str(e)}


def check_facts(answer: str, faktasjekk: dict) -> dict:
    """Sjekk om svaret inneholder forventede fakta og IKKE inneholder feil."""
    answer_lower = answer.lower()
    results = {
        "treff": [],
        "mangler": [],
        "feil_funnet": [],
        "kilde_ok": False,
    }

    # Sjekk skal_inneholde
    for fact in faktasjekk.get("skal_inneholde", []):
        # Sjekk med fleisible matching — bruk noekkerlord
        keywords = [w.lower() for w in fact.split() if len(w) > 3]
        # Krev at minst 60% av noekkerlordene finnes
        matches = sum(1 for kw in keywords if kw in answer_lower)
        threshold = max(1, int(len(keywords) * 0.6))
        if matches >= threshold:
            results["treff"].append(fact)
        else:
            results["mangler"].append(fact)

    # Sjekk skal_IKKE_inneholde
    for bad_fact in faktasjekk.get("skal_IKKE_inneholde", []):
        keywords = [w.lower() for w in bad_fact.split() if len(w) > 3]
        matches = sum(1 for kw in keywords if kw in answer_lower)
        # Feil hvis >70% av noekkerlordene finnes
        threshold = max(1, int(len(keywords) * 0.7))
        if matches >= threshold:
            results["feil_funnet"].append(bad_fact)

    # Sjekk kildekrav
    kilde = faktasjekk.get("kilde_krav", "").lower()
    kilde_ord = ["helsedirektoratet", "hapi", "nki", "retningslinje"]
    results["kilde_ok"] = any(k in answer_lower for k in kilde_ord)

    return results


def score_result(fact_check: dict, faktasjekk: dict) -> str:
    """Gi en samlet vurdering: BESTATT, DELVIS, FEIL, HALLUSINERING."""
    total_expected = len(faktasjekk.get("skal_inneholde", []))
    treff = len(fact_check["treff"])
    feil = len(fact_check["feil_funnet"])

    if feil > 0:
        return "HALLUSINERING" if not fact_check["kilde_ok"] else "FEIL"
    if total_expected == 0:
        return "BESTATT"
    ratio = treff / total_expected
    if ratio >= 0.7 and fact_check["kilde_ok"]:
        return "BESTATT"
    if ratio >= 0.4:
        return "DELVIS"
    return "MANGLER"


async def run_eval(questions: list[dict]) -> list[dict]:
    """Kjoer evaluering mot alle spoersmaal."""
    results = []

    async with AsyncCredential() as cred:
        async with AsyncProjectClient(endpoint=PROJECT_ENDPOINT, credential=cred) as project:
            for i, q in enumerate(questions, 1):
                qid = q["id"]
                safe_print(f"\n[{i}/{len(questions)}] {qid}: {q['sporsmal'][:80]}...")

                # Route
                routing = route(q["sporsmal"])
                agents = routing.agents
                safe_print(f"  Routing -> {agents} (konfidens: {routing.confidence})")

                # Sjekk routing korrekthet
                expected_routing = q.get("forventet_routing", [])
                routing_correct = set(agents) == set(expected_routing)

                # Kall foerste agent (eller orkestrator-logikk)
                agent_to_call = agents[0] if agents else "hapi-retningslinje-agent"
                result = await call_agent(project, agent_to_call, q["sporsmal"])

                if result["success"]:
                    safe_print(f"  Svar mottatt: {result['duration_ms']}ms, {len(result['output'])} tegn")

                    # Faktasjekk
                    fact_check = check_facts(result["output"], q["faktasjekk"])
                    score = score_result(fact_check, q["faktasjekk"])

                    status_icon = {"BESTATT": "OK", "DELVIS": "~~", "MANGLER": "!!", "FEIL": "XX", "HALLUSINERING": "!!"}
                    safe_print(f"  [{status_icon.get(score, '??')}] {score}")
                    if fact_check["treff"]:
                        safe_print(f"      Treff: {len(fact_check['treff'])}/{len(q['faktasjekk'].get('skal_inneholde', []))}")
                    if fact_check["mangler"]:
                        for m in fact_check["mangler"]:
                            safe_print(f"      Mangler: {m[:80]}")
                    if fact_check["feil_funnet"]:
                        for f in fact_check["feil_funnet"]:
                            safe_print(f"      FEIL: {f[:80]}")
                    if not fact_check["kilde_ok"]:
                        safe_print(f"      Kilde IKKE oppgitt")

                    results.append({
                        "id": qid,
                        "kategori": q["kategori"],
                        "tema": q["tema"],
                        "score": score,
                        "routing_correct": routing_correct,
                        "actual_routing": agents,
                        "expected_routing": expected_routing,
                        "treff": len(fact_check["treff"]),
                        "forventet": len(q["faktasjekk"].get("skal_inneholde", [])),
                        "mangler": fact_check["mangler"],
                        "feil": fact_check["feil_funnet"],
                        "kilde_ok": fact_check["kilde_ok"],
                        "duration_ms": result["duration_ms"],
                        "answer_length": len(result["output"]),
                    })
                else:
                    safe_print(f"  [XX] FEIL: {result.get('error', 'ukjent')[:100]}")
                    results.append({
                        "id": qid,
                        "kategori": q["kategori"],
                        "tema": q["tema"],
                        "score": "FEIL_TEKNISK",
                        "error": result.get("error", ""),
                        "routing_correct": routing_correct,
                        "actual_routing": agents,
                        "expected_routing": expected_routing,
                        "duration_ms": result["duration_ms"],
                    })

    return results


def print_summary(results: list[dict]):
    """Skriv ut oppsummering av evalueringen."""
    safe_print(f"\n{'='*60}")
    safe_print("EVALUERINGSRAPPORT")
    safe_print(f"{'='*60}")

    total = len(results)
    by_score = {}
    for r in results:
        s = r["score"]
        by_score[s] = by_score.get(s, 0) + 1

    safe_print(f"\nTotalt: {total} spoersmaal")
    for score_name in ["BESTATT", "DELVIS", "MANGLER", "FEIL", "HALLUSINERING", "FEIL_TEKNISK"]:
        count = by_score.get(score_name, 0)
        if count > 0:
            pct = count / total * 100
            safe_print(f"  {score_name}: {count} ({pct:.0f}%)")

    # Korrekthetsscore
    bestatt = by_score.get("BESTATT", 0) + by_score.get("DELVIS", 0)
    safe_print(f"\nKorrekthetsscore: {bestatt}/{total} ({bestatt/total*100:.0f}%)")

    # Feil og hallusinering
    feil_total = by_score.get("FEIL", 0) + by_score.get("HALLUSINERING", 0)
    if feil_total > 0:
        safe_print(f"\n!! ADVARSEL: {feil_total} svar inneholder feilinformasjon:")
        for r in results:
            if r["score"] in ("FEIL", "HALLUSINERING"):
                safe_print(f"  - {r['id']} ({r['tema']}): {', '.join(r.get('feil', []))[:100]}")

    # Routing-korrekthet
    routing_ok = sum(1 for r in results if r.get("routing_correct", False))
    safe_print(f"\nRouting-korrekthet: {routing_ok}/{total} ({routing_ok/total*100:.0f}%)")

    # Per kategori
    safe_print(f"\nPer kategori:")
    categories = {}
    for r in results:
        cat = r["kategori"]
        if cat not in categories:
            categories[cat] = {"total": 0, "bestatt": 0}
        categories[cat]["total"] += 1
        if r["score"] in ("BESTATT", "DELVIS"):
            categories[cat]["bestatt"] += 1

    for cat, data in sorted(categories.items()):
        pct = data["bestatt"] / data["total"] * 100
        safe_print(f"  {cat}: {data['bestatt']}/{data['total']} ({pct:.0f}%)")

    # Gjennomsnittlig responstid
    durations = [r["duration_ms"] for r in results if "duration_ms" in r]
    if durations:
        avg = sum(durations) / len(durations)
        safe_print(f"\nGjennomsnittlig responstid: {avg/1000:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="HAPI Agent Evaluering")
    parser.add_argument("--ids", nargs="+", help="Kjoer spesifikke spoersmaal (f.eks. EVAL-001 EVAL-005)")
    parser.add_argument("--kategori", help="Kjoer en kategori (retningslinje, kodeverk, statistikk, pasient, sammensatt)")
    parser.add_argument("--output", help="Lagre rapport til JSON-fil")
    args = parser.parse_args()

    with open(EVAL_FILE) as f:
        data = json.load(f)

    questions = data["questions"]

    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids]
    elif args.kategori:
        questions = [q for q in questions if q["kategori"] == args.kategori]

    safe_print(f"HAPI Agent Evaluering — {len(questions)} spoersmaal")
    safe_print(f"Fokus: Korrekthet og fravaaer av feilinformasjon")
    safe_print(f"{'='*60}")

    results = asyncio.run(run_eval(questions))
    print_summary(results)

    if args.output:
        report = {
            "tidspunkt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "antall": len(results),
            "resultater": results,
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        safe_print(f"\nRapport lagret til {args.output}")


if __name__ == "__main__":
    main()
