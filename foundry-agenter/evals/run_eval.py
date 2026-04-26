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

import aiohttp
from azure.identity.aio import (
    AzureCliCredential as AsyncCliCredential,
    ChainedTokenCredential as AsyncChainedCredential,
    DefaultAzureCredential as AsyncCredential,
    ManagedIdentityCredential as AsyncManagedIdentityCredential,
)
from azure.ai.projects.aio import AIProjectClient as AsyncProjectClient

logger = logging.getLogger(__name__)

# Legg til orchestrator-mappen i path (brukes ikke lenger for routing — orchestrator styrer det via /ask)
sys.path.insert(0, str(Path(__file__).parent.parent / "orchestrator"))

PROJECT_ENDPOINT = os.environ.get(
    "PROJECT_ENDPOINT",
    "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
)

# HAPI Orchestrator /ask endepunkt — bruker produksjons-flyten slik at routing,
# kjernejournal-kontekst og interaksjonssjekk aktiveres automatisk.
HAPI_URL = os.environ.get(
    "HAPI_URL",
    "https://hapi-orchestrator.nicefield-3933b657.norwayeast.azurecontainerapps.io",
).rstrip("/")

EVAL_DIR = Path(__file__).parent
EVAL_FILE = EVAL_DIR / "eval-questions-hapi.json"
RAPPORTER_DIR = EVAL_DIR / "rapporter"

# --- Eval-dommermodell (kan overstyres via JUDGE_MODEL env-var) ---
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "gpt-5.3-chat")

# --- Token-bruk og kostnad — pris pr. modell ---
PRISER_USD_PER_1M = {
    "gpt-5.3-chat": {"input": 1.75, "output": 14.00},
    "gpt-5.4": {"input": 2.50, "output": 15.00},
    "gpt-5.5": {"input": 5.00, "output": 30.00},
}
PRIS_INPUT_USD_PER_1M = PRISER_USD_PER_1M.get(JUDGE_MODEL, PRISER_USD_PER_1M["gpt-5.3-chat"])["input"]
PRIS_OUTPUT_USD_PER_1M = PRISER_USD_PER_1M.get(JUDGE_MODEL, PRISER_USD_PER_1M["gpt-5.3-chat"])["output"]
USD_NOK = 11.0
NOK_PER_INPUT_TOKEN = PRIS_INPUT_USD_PER_1M * USD_NOK / 1_000_000
NOK_PER_OUTPUT_TOKEN = PRIS_OUTPUT_USD_PER_1M * USD_NOK / 1_000_000

# Mutable counter; nullstilles ved start av hvert run_eval-kall.
TOKEN_USAGE = {"input": 0, "output": 0, "calls": 0}


def _add_usage(resp) -> None:
    """Plukk token-tall fra et OpenAI Responses-svar."""
    try:
        u = getattr(resp, "usage", None)
        if u is None:
            return
        tin = getattr(u, "input_tokens", 0) or 0
        tout = getattr(u, "output_tokens", 0) or 0
        TOKEN_USAGE["input"] += int(tin)
        TOKEN_USAGE["output"] += int(tout)
        TOKEN_USAGE["calls"] += 1
    except Exception:
        pass


def _build_statistikk(usage: dict) -> dict:
    tin = usage.get("input", 0)
    tout = usage.get("output", 0)
    kost = round(tin * NOK_PER_INPUT_TOKEN + tout * NOK_PER_OUTPUT_TOKEN, 4)
    return {
        "tokens_input": tin,
        "tokens_output": tout,
        "tokens_total": tin + tout,
        "llm_kall": usage.get("calls", 0),
        "kostnad_nok": kost,
        "kilde": "maalt-runtime",
        "prismodell": {
            "model": JUDGE_MODEL,
            "input_usd_per_1m": PRIS_INPUT_USD_PER_1M,
            "output_usd_per_1m": PRIS_OUTPUT_USD_PER_1M,
            "kurs_usd_nok": USD_NOK,
        },
    }


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
- BESTATT: Svaret dekker minst 80% av forventede fakta, ingen reelle feil, kilde oppgitt
- DELVIS: Svaret dekker 40-80% av forventede fakta, ingen reelle feil
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
            model=JUDGE_MODEL,
            input=prompt,
        )
        _add_usage(response)
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


async def call_orchestrator(query: str, patient_id: str | None) -> dict:
    """Kall HAPI-orchestratorens /ask-endepunkt.

    Bruker produksjons-flyten: router -> agenter -> kjernejournal-kontekst
    -> automatisk interaksjonssjekk -> synthesis. Returnerer hele AskResponse
    pluss suksess-flag.
    """
    url = f"{HAPI_URL}/ask"
    payload = {"query": query, "patient_id": patient_id}
    start = time.monotonic()
    try:
        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                duration = int((time.monotonic() - start) * 1000)
                if resp.status != 200:
                    err = await resp.text()
                    return {
                        "success": False,
                        "error": f"HTTP {resp.status}: {err[:200]}",
                        "total_duration_ms": duration,
                    }
                data = await resp.json()
                data["success"] = True
                if "total_duration_ms" not in data:
                    data["total_duration_ms"] = duration
                return data
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return {
            "success": False,
            "error": f"{type(e).__name__}: {e}",
            "total_duration_ms": duration,
        }


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


async def run_eval(questions: list[dict]) -> tuple[list[dict], dict]:
    """Kjoer evaluering mot alle spoersmaal med LLM-faktasjekk.

    Returnerer (results, token_usage) der token_usage er en kopi av
    TOKEN_USAGE-telleren etter at runet er ferdig.
    """
    results = []

    # Nullstill token-teller for dette runet
    TOKEN_USAGE["input"] = 0
    TOKEN_USAGE["output"] = 0
    TOKEN_USAGE["calls"] = 0

    cred = await _acquire_credential()
    try:
        async with AsyncProjectClient(endpoint=PROJECT_ENDPOINT, credential=cred) as project:
            for i, q in enumerate(questions, 1):
                qid = q["id"]
                pid = q.get("patient_id")
                pid_str = f" [patient={pid}]" if pid else ""
                safe_print(f"\n[{i}/{len(questions)}] {qid}{pid_str}: {q['sporsmal'][:80]}...")

                expected_routing = q.get("forventet_routing", [])

                # Call orchestrator (produksjons-flyt: router + kjernejournal + interaksjonssjekk + syntese)
                ask_result = await call_orchestrator(q["sporsmal"], pid)

                if ask_result.get("success"):
                    routing_info = ask_result.get("routing") or {}
                    agents = routing_info.get("agents", []) or []
                    confidence = routing_info.get("confidence", "?")
                    safe_print(f"  Routing -> {agents} (konfidens: {confidence})")
                    routing_correct = set(agents) == set(expected_routing)

                    result = {
                        "success": True,
                        "output": ask_result.get("answer", ""),
                        "duration_ms": int(ask_result.get("total_duration_ms", 0) or 0),
                    }
                    ik = ask_result.get("interaksjonssjekk")
                    if ik:
                        safe_print(f"  Interaksjonssjekk aktivert")
                    safe_print(f"  Orchestrator svarte: {result['duration_ms']}ms, {len(result['output'])} tegn")
                else:
                    agents = []
                    routing_correct = False
                    result = {
                        "success": False,
                        "output": "",
                        "duration_ms": int(ask_result.get("total_duration_ms", 0) or 0),
                        "error": ask_result.get("error", "unknown"),
                    }
                    safe_print(f"  Orchestrator FEIL: {ask_result.get('error', 'unknown')[:120]}")

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

    return results, dict(TOKEN_USAGE)


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


def save_report(results: list[dict], tag: str = "", usage: dict | None = None):
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
            "eval_metode": f"LLM-faktasjekk ({JUDGE_MODEL})",
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

    if usage is not None:
        stat = _build_statistikk(usage)
        if total:
            stat["kostnad_per_spoersmaal_nok"] = round(stat["kostnad_nok"] / total, 4)
        report["statistikk"] = stat

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
    all_usages: list[dict] | None = None,
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
            "eval_metode": f"LLM-faktasjekk ({JUDGE_MODEL}) — konsensus",
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

    if all_usages:
        agg = {
            "input": sum(u.get("input", 0) for u in all_usages),
            "output": sum(u.get("output", 0) for u in all_usages),
            "calls": sum(u.get("calls", 0) for u in all_usages),
        }
        stat = _build_statistikk(agg)
        # Aggregert: alle kjoringer x antall spoersmaal
        n_total = total * n_runs
        if n_total:
            stat["kostnad_per_spoersmaal_nok"] = round(stat["kostnad_nok"] / n_total, 4)
        report["statistikk"] = stat

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
    parser.add_argument("--file", help="Sti til eval-JSON (default: eval-questions-hapi.json)")
    args = parser.parse_args()

    eval_file = Path(args.file).resolve() if args.file else EVAL_FILE
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
        results, usage = asyncio.run(run_eval(questions))
        print_summary(results)
        save_report(results, tag=args.tag, usage=usage)
    else:
        # Multi-run with consensus
        all_run_results = []
        all_usages = []
        for run_num in range(1, n_runs + 1):
            safe_print(f"\n{'#'*60}")
            safe_print(f"# KJOERING {run_num}/{n_runs}")
            safe_print(f"{'#'*60}")

            results, usage = asyncio.run(run_eval(questions))
            print_summary(results)

            # Save individual run report with -runN suffix
            run_tag = f"{args.tag}-run{run_num}" if args.tag else f"run{run_num}"
            save_report(results, tag=run_tag, usage=usage)

            all_run_results.append(results)
            all_usages.append(usage)

        # Generate combined consensus report
        build_combined_report(all_run_results, tag=args.tag, all_usages=all_usages)


if __name__ == "__main__":
    main()
