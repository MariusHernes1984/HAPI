"""
HAPI Agent Evaluering v2 — LLM-basert faktasjekk.

Bruker GPT-5.3 som dommer for aa vurdere om agentsvar er korrekte,
i stedet for naiv keyword-matching som ga for mange falske positiver.

Rapporter lagres automatisk i evals/rapporter/ for progresjonssporing.

Bruk:
  python run_eval.py                          # Kjoer alle 30
  python run_eval.py --ids EVAL-001 EVAL-005  # Kjoer spesifikke
  python run_eval.py --kategori kodeverk      # Kjoer en kategori
  python run_eval.py --tag v2-llm             # Legg til tag i filnavn
"""

import asyncio
import json
import time
import sys
import os
import argparse
from pathlib import Path
from datetime import datetime

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
RAPPORTER_DIR = Path(__file__).parent / "rapporter"


def safe_print(text: str):
    """Print som haandterer unicode paa Windows."""
    print(text.encode("ascii", errors="replace").decode("ascii"))


# --- LLM-basert faktasjekk ---

JUDGE_PROMPT = """Du er en medisinsk faktasjekker. Vurder om svaret fra en helseagent er korrekt.

SPOERSMAAL: {question}

AGENTENS SVAR:
{answer}

FORVENTEDE FAKTA (skal vaere med):
{skal_inneholde}

FEIL SOM IKKE SKAL FOREKOMME:
{skal_ikke_inneholde}

KILDEKRAV: {kilde_krav}

Vurder svaret og returner KUN gyldig JSON (ingen annen tekst):
{{
  "score": "BESTATT|DELVIS|MANGLER|FEIL|HALLUSINERING",
  "treff": ["liste over forventede fakta som ER dekket i svaret"],
  "mangler": ["liste over forventede fakta som MANGLER i svaret"],
  "feil_funnet": ["liste over faktiske feil eller hallusinasjoner i svaret - kun REELLE feil, ikke ord som forekommer i annen kontekst"],
  "kilde_ok": true/false,
  "begrunnelse": "kort forklaring paa maks 2 setninger"
}}

VIKTIGE REGLER FOR VURDERING:
- BESTATT: Svaret dekker minst 70% av forventede fakta, ingen reelle feil, kilde oppgitt
- DELVIS: Svaret dekker 40-70% av forventede fakta, ingen reelle feil
- MANGLER: Svaret dekker under 40% av forventede fakta, men ingen feil
- FEIL: Svaret inneholder medisinsk feilinformasjon
- HALLUSINERING: Svaret fabrikkerer data som ikke finnes i kildene
- Et ord som "penicillin" i konteksten "penicillinallergi" er IKKE det samme som aa anbefale penicillin
- Vurder MENINGEN i svaret, ikke bare enkeltord
- Vaer streng paa medisinsk korrekthet men rettferdig paa kontekst"""


async def llm_fact_check(
    project: AsyncProjectClient,
    question: str,
    answer: str,
    faktasjekk: dict,
) -> dict:
    """Bruk LLM til aa vurdere om agentsvaret er korrekt."""
    skal_inneholde = "\n".join(f"- {f}" for f in faktasjekk.get("skal_inneholde", []))
    skal_ikke = "\n".join(f"- {f}" for f in faktasjekk.get("skal_IKKE_inneholde", []))
    kilde = faktasjekk.get("kilde_krav", "Ikke spesifisert")

    prompt = JUDGE_PROMPT.format(
        question=question,
        answer=answer[:3000],  # Begrens for aa unngaa for stort input
        skal_inneholde=skal_inneholde or "(ingen spesifikke krav)",
        skal_ikke_inneholde=skal_ikke or "(ingen)",
        kilde_krav=kilde,
    )

    try:
        openai = project.get_openai_client()
        response = await openai.responses.create(
            model="gpt-5.3-chat",
            input=prompt,
        )
        text = response.output_text.strip()

        # Ekstraher JSON fra svaret
        start = text.index("{")
        end = text.rindex("}") + 1
        result = json.loads(text[start:end])

        # Valider at alle noedvendige felter finnes
        result.setdefault("score", "MANGLER")
        result.setdefault("treff", [])
        result.setdefault("mangler", [])
        result.setdefault("feil_funnet", [])
        result.setdefault("kilde_ok", False)
        result.setdefault("begrunnelse", "")

        return result

    except Exception as e:
        return {
            "score": "FEIL_TEKNISK",
            "treff": [],
            "mangler": [],
            "feil_funnet": [],
            "kilde_ok": False,
            "begrunnelse": f"LLM-faktasjekk feilet: {e}",
        }


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


async def run_eval(questions: list[dict]) -> list[dict]:
    """Kjoer evaluering mot alle spoersmaal med LLM-faktasjekk."""
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

                # Kall foerste agent
                agent_to_call = agents[0] if agents else "hapi-retningslinje-agent"
                result = await call_agent(project, agent_to_call, q["sporsmal"])

                if result["success"]:
                    safe_print(f"  Svar mottatt: {result['duration_ms']}ms, {len(result['output'])} tegn")

                    # LLM-basert faktasjekk
                    safe_print(f"  Faktasjekk (LLM)...")
                    fact_check = await llm_fact_check(
                        project, q["sporsmal"], result["output"], q["faktasjekk"]
                    )

                    score = fact_check["score"]
                    icons = {"BESTATT": "OK", "DELVIS": "~~", "MANGLER": "!!", "FEIL": "XX", "HALLUSINERING": "XX"}
                    safe_print(f"  [{icons.get(score, '??')}] {score}")

                    if fact_check.get("begrunnelse"):
                        safe_print(f"      {fact_check['begrunnelse'][:120]}")
                    if fact_check["treff"]:
                        safe_print(f"      Treff: {len(fact_check['treff'])}/{len(q['faktasjekk'].get('skal_inneholde', []))}")
                    if fact_check["mangler"]:
                        for m in fact_check["mangler"]:
                            safe_print(f"      Mangler: {m[:80]}")
                    if fact_check["feil_funnet"]:
                        for f in fact_check["feil_funnet"]:
                            safe_print(f"      FEIL: {f[:80]}")

                    results.append({
                        "id": qid,
                        "kategori": q["kategori"],
                        "tema": q["tema"],
                        "score": score,
                        "begrunnelse": fact_check.get("begrunnelse", ""),
                        "routing_correct": routing_correct,
                        "actual_routing": agents,
                        "expected_routing": expected_routing,
                        "treff": fact_check["treff"],
                        "forventet": len(q["faktasjekk"].get("skal_inneholde", [])),
                        "mangler": fact_check["mangler"],
                        "feil": fact_check["feil_funnet"],
                        "kilde_ok": fact_check["kilde_ok"],
                        "duration_ms": result["duration_ms"],
                        "answer_length": len(result["output"]),
                        "answer_preview": result["output"][:300],
                    })
                else:
                    safe_print(f"  [XX] FEIL: {result.get('error', 'ukjent')[:100]}")
                    results.append({
                        "id": qid,
                        "kategori": q["kategori"],
                        "tema": q["tema"],
                        "score": "FEIL_TEKNISK",
                        "begrunnelse": result.get("error", ""),
                        "routing_correct": routing_correct,
                        "actual_routing": agents,
                        "expected_routing": expected_routing,
                        "duration_ms": result["duration_ms"],
                    })

    return results


def print_summary(results: list[dict]):
    """Skriv ut oppsummering av evalueringen."""
    safe_print(f"\n{'='*60}")
    safe_print("EVALUERINGSRAPPORT (LLM-faktasjekk)")
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
                safe_print(f"  - {r['id']} ({r['tema']}): {r.get('begrunnelse', '')[:100]}")

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


def save_report(results: list[dict], tag: str = ""):
    """Lagre rapport til evals/rapporter/ med tidsstempel."""
    RAPPORTER_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    tag_suffix = f"-{tag}" if tag else ""
    filename = f"rapport-{timestamp}{tag_suffix}.json"
    filepath = RAPPORTER_DIR / filename

    total = len(results)
    by_score = {}
    for r in results:
        by_score[r["score"]] = by_score.get(r["score"], 0) + 1

    bestatt = by_score.get("BESTATT", 0) + by_score.get("DELVIS", 0)

    report = {
        "metadata": {
            "tidspunkt": datetime.now().isoformat(),
            "versjon": "v2-llm",
            "antall_spoersmaal": total,
            "tag": tag or None,
            "eval_metode": "LLM-faktasjekk (gpt-5.3-chat)",
        },
        "oppsummering": {
            "korrekthetsscore": f"{bestatt}/{total} ({bestatt/total*100:.0f}%)",
            "bestatt": by_score.get("BESTATT", 0),
            "delvis": by_score.get("DELVIS", 0),
            "mangler": by_score.get("MANGLER", 0),
            "feil": by_score.get("FEIL", 0),
            "hallusinering": by_score.get("HALLUSINERING", 0),
            "teknisk_feil": by_score.get("FEIL_TEKNISK", 0),
            "routing_korrekthet": sum(1 for r in results if r.get("routing_correct", False)),
            "snitt_responstid_ms": int(sum(r.get("duration_ms", 0) for r in results) / total) if total else 0,
        },
        "resultater": results,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    safe_print(f"\nRapport lagret: {filepath}")
    return filepath


def main():
    parser = argparse.ArgumentParser(description="HAPI Agent Evaluering v2 (LLM-faktasjekk)")
    parser.add_argument("--ids", nargs="+", help="Kjoer spesifikke spoersmaal (f.eks. EVAL-001 EVAL-005)")
    parser.add_argument("--kategori", help="Kjoer en kategori (retningslinje, kodeverk, statistikk, pasient, sammensatt)")
    parser.add_argument("--tag", default="", help="Tag for rapportfilnavn (f.eks. v2-llm, post-mcp-fix)")
    args = parser.parse_args()

    with open(EVAL_FILE) as f:
        data = json.load(f)

    questions = data["questions"]

    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids]
    elif args.kategori:
        questions = [q for q in questions if q["kategori"] == args.kategori]

    safe_print(f"HAPI Agent Evaluering v2 (LLM-faktasjekk) -- {len(questions)} spoersmaal")
    safe_print(f"Fokus: Korrekthet og fravaaer av feilinformasjon")
    safe_print(f"{'='*60}")

    results = asyncio.run(run_eval(questions))
    print_summary(results)
    save_report(results, tag=args.tag)


if __name__ == "__main__":
    main()
