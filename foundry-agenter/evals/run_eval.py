"""
HAPI Agent Evaluering v2 — LLM-basert faktasjekk.

Bruker GPT-5.3 som dommer for aa vurdere om agentsvar er korrekte,
i stedet for naiv keyword-matching som ga for mange falske positiver.

Rapporter lagres automatisk i evals/rapporter/ for progresjonssporing.

Bruk:
  python run_eval.py                          # Kjoer alle HAPI-spoersmaal (default)
  python run_eval.py --ids EVAL-001 EVAL-005  # Kjoer spesifikke
  python run_eval.py --kategori kodeverk      # Kjoer en kategori
  python run_eval.py --tag v2-llm             # Legg til tag i filnavn
  python run_eval.py --runs 3                 # Kjoer 3 ganger, generer konsensusrapport
"""

import asyncio
import json
import time
import sys
import os
import argparse
import logging
from collections import Counter
from pathlib import Path
from datetime import datetime

from azure.identity.aio import (
    AzureCliCredential as AsyncCliCredential,
    ChainedTokenCredential as AsyncChainedCredential,
    DefaultAzureCredential as AsyncCredential,
    ManagedIdentityCredential as AsyncManagedIdentityCredential,
)
from azure.ai.projects.aio import AIProjectClient as AsyncProjectClient

logger = logging.getLogger(__name__)

# Legg til orchestrator-mappen i path for routing
sys.path.insert(0, str(Path(__file__).parent.parent / "orchestrator"))
from router import route

PROJECT_ENDPOINT = os.environ.get(
    "PROJECT_ENDPOINT",
    "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
)

EVAL_DIR = Path(__file__).parent
EVAL_FILE = EVAL_DIR / "eval-questions-hapi.json"
RAPPORTER_DIR = EVAL_DIR / "rapporter"


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
- Vaer streng paa medisinsk korrekthet men rettferdig paa kontekst

{datakvalitet_tillegg}"""


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

    # Bygg datakvalitet-tillegg for kjente FEST/MCP-begrensninger
    datakvalitet_tillegg = ""
    if faktasjekk.get("godta_manglende_data"):
        begrunnelse = faktasjekk.get("godta_manglende_data_begrunnelse", "")
        datakvalitet_tillegg = (
            f"\nKJENT DATABEGRENSNING:\n"
            f"Dette spoersmaalet har en kjent begrensning i datakilden (FEST/MCP).\n"
            f"{begrunnelse}\n"
            f"Hvis agenten aerlig rapporterer at data ikke ble funnet i MCP, eller trofast siterer\n"
            f"MCP-data (selv om dataen er feil/ufullstendig), skal dette vurderes som BESTATT\n"
            f"— ikke MANGLER eller FEIL. Agenten straffes IKKE for datakvalitetsproblemer i FEST/MCP."
        )

    prompt = JUDGE_PROMPT.format(
        question=question,
        answer=answer[:3000],  # Begrens for aa unngaa for stort input
        skal_inneholde=skal_inneholde or "(ingen spesifikke krav)",
        skal_ikke_inneholde=skal_ikke or "(ingen)",
        kilde_krav=kilde,
        datakvalitet_tillegg=datakvalitet_tillegg,
    )

    try:
        openai = project.get_openai_client()
        response = await openai.responses.create(
            model="gpt-5.3-chat",
            input=prompt,
        )
        text = response.output_text.strip()

        # Ekstraher JSON fra svaret
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError(f"Ingen JSON funnet i LLM-svar: {text[:200]}")
        result = json.loads(text[start:end + 1])

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


async def _acquire_credential(max_retries: int = 3, delay: float = 2.0):
    """Acquire Azure credential with retry logic and fallback.

    Tries DefaultAzureCredential first (which includes AzureCliCredential
    among others). On failure, retries up to *max_retries* times with a
    short delay.  If all retries fail, falls back to an explicit
    ChainedTokenCredential with AzureCliCredential and
    ManagedIdentityCredential so we cover both local-dev and CI scenarios.
    """
    last_error = None

    # --- Attempt 1-N: DefaultAzureCredential ---
    for attempt in range(1, max_retries + 1):
        try:
            cred = AsyncCredential()
            # Force an early token acquisition to surface auth errors now
            # rather than in the middle of the eval run.
            await cred.get_token("https://management.azure.com/.default")
            safe_print(f"  Credential acquired (DefaultAzureCredential, attempt {attempt})")
            return cred
        except Exception as e:
            last_error = e
            safe_print(
                f"  Credential attempt {attempt}/{max_retries} failed: "
                f"{type(e).__name__}: {str(e)[:120]}"
            )
            # Close the failed credential to avoid resource leaks
            try:
                await cred.close()
            except Exception:
                pass
            if attempt < max_retries:
                safe_print(f"  Retrying in {delay}s...")
                await asyncio.sleep(delay)
                delay *= 1.5  # gentle back-off

    # --- Fallback: explicit ChainedTokenCredential ---
    safe_print("  DefaultAzureCredential exhausted, trying fallback chain...")
    try:
        fallback = AsyncChainedCredential(
            AsyncCliCredential(),
            AsyncManagedIdentityCredential(),
        )
        await fallback.get_token("https://management.azure.com/.default")
        safe_print("  Credential acquired (fallback ChainedTokenCredential)")
        return fallback
    except Exception as e2:
        safe_print(f"  Fallback credential also failed: {type(e2).__name__}: {str(e2)[:120]}")
        try:
            await fallback.close()
        except Exception:
            pass
        raise RuntimeError(
            f"All credential strategies failed. "
            f"Last DefaultAzureCredential error: {last_error}. "
            f"Fallback error: {e2}"
        ) from e2


async def _retry_on_auth_error(coro_factory, max_retries: int = 2, delay: float = 1.5):
    """Retry an async callable if it fails with a credential/timeout error.

    *coro_factory* must be a zero-arg callable that returns a new awaitable
    each time (e.g. ``lambda: call_agent(project, name, q)``).
    """
    last_error = None
    auth_error_keywords = ("timed out", "credential", "token", "authentication", "unauthorized")

    for attempt in range(1, max_retries + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_error = e
            err_lower = str(e).lower()
            is_auth = any(kw in err_lower for kw in auth_error_keywords)
            if is_auth and attempt < max_retries:
                safe_print(f"    Auth-related error (attempt {attempt}), retrying in {delay}s...")
                await asyncio.sleep(delay)
                continue
            raise
    raise last_error  # should not reach here, but just in case


async def run_eval(questions: list[dict]) -> list[dict]:
    """Kjoer evaluering mot alle spoersmaal med LLM-faktasjekk."""
    results = []

    cred = await _acquire_credential()
    try:
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

                # Kall ALLE routede agenter parallelt (som produksjons-orkestratoren)
                agents_to_call = agents if agents else ["hapi-retningslinje-agent"]
                agent_tasks = [
                    _retry_on_auth_error(
                        lambda a=a, s=q["sporsmal"]: call_agent(project, a, s)
                    )
                    for a in agents_to_call
                ]

                try:
                    agent_results = await asyncio.gather(*agent_tasks, return_exceptions=True)
                except Exception as agent_err:
                    agent_results = [{"success": False, "output": "", "duration_ms": 0, "error": str(agent_err)}]

                # Samle vellykkede resultater
                successful_outputs = []
                total_duration = 0
                for idx, ar in enumerate(agent_results):
                    if isinstance(ar, Exception):
                        safe_print(f"  {agents_to_call[idx]}: FEIL ({ar})")
                        continue
                    if ar.get("success") and ar.get("output"):
                        agent_label = agents_to_call[idx].replace("hapi-", "").replace("-agent", "").title()
                        successful_outputs.append(f"--- {agent_label} ---\n{ar['output']}")
                        total_duration = max(total_duration, ar["duration_ms"])
                        safe_print(f"  {agents_to_call[idx]}: {ar['duration_ms']}ms, {len(ar['output'])} tegn")
                    else:
                        safe_print(f"  {agents_to_call[idx]}: FEIL ({ar.get('error', 'tomt svar')})")

                # Syntetiser hvis flere agenter svarte
                if len(successful_outputs) > 1:
                    combined = "\n\n".join(successful_outputs)
                    agent_names = ", ".join(
                        a.replace("hapi-", "").replace("-agent", "")
                        for a in agents_to_call
                        if any(a in o for o in successful_outputs)
                    ) or "hapi"
                    synth_prompt = (
                        f"Du er HAPI Helseassistent. Kombiner agentsvarene til ett svar paa norsk.\n\n"
                        f"Spoersmaal: {q['sporsmal']}\n\n{combined}\n\n"
                        f"REGLER:\n"
                        f"1. BEVAR ALL PRESIS DATA: ATC-koder, ICD-10-koder, prosenttall, doser "
                        f"og preparatnavn skal gjengis ORDRETT. Aldri utelat en kode eller et tall.\n"
                        f"2. LOGISK REKKEFOEGLE: diagnose/kode -> behandling -> statistikk/NKI\n"
                        f"3. IKKE BLAND DOMENER: Retningslinje-innhold er ikke NKI-data.\n"
                        f"4. Behold faglig presisjon. Ikke legg til egen kunnskap.\n"
                        f"5. Oppgi kilde: Helsedirektoratet (agenter: {agent_names})."
                    )
                    try:
                        openai = project.get_openai_client()
                        synth_resp = await openai.responses.create(
                            model="gpt-5.3-chat",
                            input=synth_prompt,
                        )
                        result = {"success": True, "output": synth_resp.output_text, "duration_ms": total_duration}
                        safe_print(f"  Syntetisert fra {len(successful_outputs)} agenter")
                    except Exception as synth_err:
                        # Fallback: konkatener
                        result = {"success": True, "output": "\n\n".join(successful_outputs), "duration_ms": total_duration}
                        safe_print(f"  Syntese feilet, bruker konkatenering: {synth_err}")
                elif len(successful_outputs) == 1:
                    ar = next(a for a in agent_results if not isinstance(a, Exception) and a.get("success") and a.get("output"))
                    result = ar
                else:
                    err_msg = "; ".join(
                        str(ar) if isinstance(ar, Exception) else ar.get("error", "tomt")
                        for ar in agent_results
                    )
                    result = {"success": False, "output": "", "duration_ms": 0, "error": err_msg}

                if result["success"]:
                    safe_print(f"  Svar mottatt: {result['duration_ms']}ms, {len(result['output'])} tegn")

                    # LLM-basert faktasjekk (med retry ved auth-feil)
                    safe_print(f"  Faktasjekk (LLM)...")
                    try:
                        fact_check = await _retry_on_auth_error(
                            lambda s=q["sporsmal"], o=result["output"], f=q["faktasjekk"]: (
                                llm_fact_check(project, s, o, f)
                            )
                        )
                    except Exception as fc_err:
                        fact_check = {
                            "score": "FEIL_TEKNISK",
                            "treff": [], "mangler": [], "feil_funnet": [],
                            "kilde_ok": False,
                            "begrunnelse": f"Faktasjekk feilet etter retries: {fc_err}",
                        }

                    score = fact_check["score"]

                    # Post-processing: oppgrader score for kjente databegrensninger
                    has_data_limitation = q.get("faktasjekk", {}).get("godta_manglende_data", False)
                    if has_data_limitation and score in ("MANGLER", "FEIL"):
                        # Sjekk om agenten var aerlig om manglende data (ikke hallusinerte)
                        has_hall = score == "HALLUSINERING" or any(
                            "fabriker" in f.lower() or "hallus" in f.lower() or "diktet" in f.lower()
                            for f in fact_check.get("feil_funnet", [])
                        )
                        if not has_hall:
                            original_score = score
                            score = "BESTATT"
                            fact_check["score"] = score
                            fact_check["begrunnelse"] = (
                                f"[DATAKVALITET] Oppgradert fra {original_score}. "
                                f"Kjent FEST/MCP-begrensning: {q.get('kjent_databegrensning', '')}. "
                                f"Opprinnelig: {fact_check.get('begrunnelse', '')}"
                            )

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

                    result_entry = {
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
                    }
                    if q.get("kjent_databegrensning"):
                        result_entry["kjent_databegrensning"] = q["kjent_databegrensning"]
                    results.append(result_entry)
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
    finally:
        await cred.close()

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


# --- Score ordering (best to worst) for conservative consensus tie-breaking ---
SCORE_ORDER = ["BESTATT", "DELVIS", "MANGLER", "FEIL", "HALLUSINERING", "FEIL_TEKNISK"]
SCORE_RANK = {s: i for i, s in enumerate(SCORE_ORDER)}


def _consensus_score(scores: list[str]) -> str:
    """Determine consensus score from multiple runs.

    Returns the most common score. On tie, returns the worse score
    (conservative approach) based on SCORE_ORDER.
    """
    counts = Counter(scores)
    max_count = max(counts.values())
    # All scores that share the highest count
    tied = [s for s, c in counts.items() if c == max_count]
    if len(tied) == 1:
        return tied[0]
    # Tie: pick the worst score (highest rank index)
    return max(tied, key=lambda s: SCORE_RANK.get(s, len(SCORE_ORDER)))


def build_combined_report(
    all_run_results: list[list[dict]],
    tag: str = "",
) -> str:
    """Build and save a combined consensus report from multiple runs.

    Returns the filepath of the saved combined report.
    """
    n_runs = len(all_run_results)

    # Collect question IDs from first run (order preserved)
    question_ids = [r["id"] for r in all_run_results[0]]

    combined_results = []
    unanimous_count = 0

    for idx, qid in enumerate(question_ids):
        # Gather per-run data for this question
        run_scores = []
        run_details = []
        for run_idx, run_results in enumerate(all_run_results):
            r = run_results[idx]
            run_scores.append(r["score"])
            run_details.append({
                "run": run_idx + 1,
                "score": r["score"],
                "begrunnelse": r.get("begrunnelse", ""),
                "duration_ms": r.get("duration_ms", 0),
            })

        consensus = _consensus_score(run_scores)
        is_unanimous = len(set(run_scores)) == 1

        if is_unanimous:
            unanimous_count += 1

        # Use first run's metadata as base
        base = all_run_results[0][idx]
        combined_results.append({
            "id": qid,
            "kategori": base.get("kategori", ""),
            "tema": base.get("tema", ""),
            "consensus_score": consensus,
            "unanimous": is_unanimous,
            "all_scores": run_scores,
            "run_details": run_details,
            "routing_correct": base.get("routing_correct", False),
            "actual_routing": base.get("actual_routing", []),
            "expected_routing": base.get("expected_routing", []),
        })

    total = len(combined_results)
    stability = unanimous_count / total * 100 if total else 0

    # Consensus-based score distribution
    by_score = Counter(r["consensus_score"] for r in combined_results)
    bestatt = by_score.get("BESTATT", 0) + by_score.get("DELVIS", 0)

    # Print combined summary
    safe_print(f"\n{'='*60}")
    safe_print(f"KOMBINERT KONSENSUSRAPPORT ({n_runs} kjoringer)")
    safe_print(f"{'='*60}")
    safe_print(f"\nStabilitet: {unanimous_count}/{total} spoersmaal lik i alle kjoringer ({stability:.0f}%)")
    safe_print(f"Konsensus-korrekthetsscore: {bestatt}/{total} ({bestatt/total*100:.0f}%)")
    safe_print(f"\nKonsensus-fordeling:")
    for score_name in SCORE_ORDER:
        count = by_score.get(score_name, 0)
        if count > 0:
            pct = count / total * 100
            safe_print(f"  {score_name}: {count} ({pct:.0f}%)")

    # Show unstable questions
    unstable = [r for r in combined_results if not r["unanimous"]]
    if unstable:
        safe_print(f"\nUstabile spoersmaal ({len(unstable)}):")
        for r in unstable:
            scores_str = ", ".join(r["all_scores"])
            safe_print(f"  {r['id']}: [{scores_str}] -> konsensus: {r['consensus_score']}")

    # Save combined report
    RAPPORTER_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    tag_suffix = f"-{tag}" if tag else ""
    filename = f"rapport-{timestamp}{tag_suffix}-combined.json"
    filepath = RAPPORTER_DIR / filename

    report = {
        "metadata": {
            "tidspunkt": datetime.now().isoformat(),
            "versjon": "v2-llm",
            "antall_spoersmaal": total,
            "antall_kjoringer": n_runs,
            "tag": tag or None,
            "eval_metode": "LLM-faktasjekk (gpt-5.3-chat) — konsensus",
        },
        "oppsummering": {
            "korrekthetsscore": f"{bestatt}/{total} ({bestatt/total*100:.0f}%)",
            "bestatt": by_score.get("BESTATT", 0),
            "delvis": by_score.get("DELVIS", 0),
            "mangler": by_score.get("MANGLER", 0),
            "feil": by_score.get("FEIL", 0),
            "hallusinering": by_score.get("HALLUSINERING", 0),
            "teknisk_feil": by_score.get("FEIL_TEKNISK", 0),
            "stabilitet_prosent": round(stability, 1),
            "unanime_spoersmaal": unanimous_count,
            "routing_korrekthet": sum(1 for r in combined_results if r.get("routing_correct", False)),
        },
        "resultater": combined_results,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    safe_print(f"\nKombinert rapport lagret: {filepath}")
    return str(filepath)


def main():
    parser = argparse.ArgumentParser(description="HAPI Agent Evaluering v2 (LLM-faktasjekk)")
    parser.add_argument("--ids", nargs="+", help="Kjoer spesifikke spoersmaal (f.eks. EVAL-001 EVAL-005)")
    parser.add_argument("--kategori", help="Kjoer en kategori (retningslinje, kodeverk, statistikk, pasient, sammensatt)")
    parser.add_argument("--tag", default="", help="Tag for rapportfilnavn (f.eks. v2-llm, post-mcp-fix)")
    parser.add_argument("--runs", type=int, default=1, help="Antall ganger aa kjoere eval (default: 1). Flere kjoringer gir konsensusrapport.")
    args = parser.parse_args()

    eval_file = EVAL_FILE
    safe_print(f"Evalueringsfil: {eval_file.name}")

    with open(eval_file) as f:
        data = json.load(f)

    questions = data["questions"]

    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids]
    elif args.kategori:
        questions = [q for q in questions if q["kategori"] == args.kategori]

    n_runs = max(1, args.runs)

    safe_print(f"HAPI Agent Evaluering v2 (LLM-faktasjekk) -- {len(questions)} spoersmaal")
    if n_runs > 1:
        safe_print(f"Antall kjoringer: {n_runs} (konsensusrapport genereres)")
    safe_print(f"Fokus: Korrekthet og fravaaer av feilinformasjon")
    safe_print(f"{'='*60}")

    if n_runs == 1:
        # Original single-run behaviour
        results = asyncio.run(run_eval(questions))
        print_summary(results)
        save_report(results, tag=args.tag)
    else:
        # Multi-run with consensus
        all_run_results = []
        for run_num in range(1, n_runs + 1):
            safe_print(f"\n{'#'*60}")
            safe_print(f"# KJOERING {run_num}/{n_runs}")
            safe_print(f"{'#'*60}")

            results = asyncio.run(run_eval(questions))
            print_summary(results)

            # Save individual run report with -runN suffix
            run_tag = f"{args.tag}-run{run_num}" if args.tag else f"run{run_num}"
            save_report(results, tag=run_tag)

            all_run_results.append(results)

        # Generate combined consensus report
        build_combined_report(all_run_results, tag=args.tag)


if __name__ == "__main__":
    main()
