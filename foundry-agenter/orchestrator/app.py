"""
FastAPI-app for HAPI Agent Orchestrator.

Endepunkter:
  POST /ask          — Send spoersmaal, faa orkestrert svar
  POST /ask/stream   — Streaming-versjon (SSE)
  GET  /health       — Helsesjekk
  GET  /agents       — List tilgjengelige agenter

Deploy som Azure Container App:
  docker build -t hapi-orchestrator .
  az containerapp up --name hapi-orchestrator --source .
"""

import os
import re
import json
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from orchestrate import orchestrate, OrchestrationResult
from router import route, RETNINGSLINJE, KODEVERK, STATISTIKK

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ENDPOINT = os.environ.get(
    "PROJECT_ENDPOINT",
    "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
)


# --- Models ---

class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="Spoersmaal til HAPI-agentene")
    use_llm_routing: bool = Field(False, description="Bruk LLM for routing ved lav konfidens")


class AgentResultResponse(BaseModel):
    agent_name: str
    output: str
    duration_ms: int
    success: bool
    error: str | None = None


class RoutingResponse(BaseModel):
    agents: list[str]
    confidence: str
    reasoning: str
    detected_codes: list[str]


class AskResponse(BaseModel):
    answer: str
    routing: RoutingResponse
    agent_results: list[AgentResultResponse]
    total_duration_ms: int


class HealthResponse(BaseModel):
    status: str
    project_endpoint: str
    agents: list[str]


# --- App ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"HAPI Orchestrator starting — endpoint: {PROJECT_ENDPOINT}")
    yield
    logger.info("HAPI Orchestrator shutting down")


app = FastAPI(
    title="HAPI Agent Orchestrator",
    description="Orkestrerer spesialiserte HAPI-agenter for norsk helsedata",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


# --- Endepunkter ---

@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest):
    """Send et spoersmaal og faa et orkestrert svar fra HAPI-agentene."""
    logger.info(f"POST /ask: {request.query[:80]}")

    try:
        result: OrchestrationResult = await orchestrate(
            project_endpoint=PROJECT_ENDPOINT,
            query=request.query,
            use_llm_routing=request.use_llm_routing,
        )

        return AskResponse(
            answer=result.final_answer,
            routing=RoutingResponse(
                agents=result.routing.agents,
                confidence=result.routing.confidence,
                reasoning=result.routing.reasoning,
                detected_codes=result.routing.detected_codes,
            ),
            agent_results=[
                AgentResultResponse(
                    agent_name=r.agent_name,
                    output=r.output[:2000] if r.output else "",
                    duration_ms=r.duration_ms,
                    success=r.success,
                    error=r.error,
                )
                for r in result.agent_results
            ],
            total_duration_ms=result.total_duration_ms,
        )

    except Exception as e:
        logger.error(f"Orchestration feilet: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ask/stream")
async def ask_stream(request: AskRequest):
    """Streaming-versjon — sender partial results via SSE."""

    async def event_generator():
        import json

        # Steg 1: Routing
        decision = route(request.query)
        yield f"data: {json.dumps({'type': 'routing', 'agents': decision.agents, 'confidence': decision.confidence})}\n\n"

        # Steg 2: Kall agenter
        result = await orchestrate(
            project_endpoint=PROJECT_ENDPOINT,
            query=request.query,
            use_llm_routing=request.use_llm_routing,
        )

        for r in result.agent_results:
            yield f"data: {json.dumps({'type': 'agent_result', 'agent': r.agent_name, 'success': r.success, 'duration_ms': r.duration_ms})}\n\n"

        # Steg 3: Endelig svar
        yield f"data: {json.dumps({'type': 'final_answer', 'answer': result.final_answer, 'total_duration_ms': result.total_duration_ms})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    """Helsesjekk."""
    return HealthResponse(
        status="ok",
        project_endpoint=PROJECT_ENDPOINT,
        agents=[RETNINGSLINJE, KODEVERK, STATISTIKK],
    )


@app.get("/agents")
async def list_agents():
    """List tilgjengelige agenter og deres formaal."""
    return {
        "agents": [
            {
                "name": RETNINGSLINJE,
                "description": "Retningslinjer, anbefalinger, pakkeforloep, antibiotika",
                "mcp_tools": ["sok_innhold", "hent_retningslinje", "hent_anbefalinger", "hent_anbefaling",
                              "hent_innhold", "hent_innhold_id", "hent_pakkeforlop", "hent_pakkeforlop_id"],
            },
            {
                "name": KODEVERK,
                "description": "Kodeverk (ICD-10, ICPC-2, SNOMED, ATC), legemiddeldata, mapping",
                "mcp_tools": ["sok_innhold", "hent_innhold_id"],
            },
            {
                "name": STATISTIKK,
                "description": "Nasjonale kvalitetsindikatorer (NKI), statistikk, trender",
                "mcp_tools": ["sok_innhold", "hent_innhold_id"],
            },
        ]
    }


EVALS_DIR = Path("/app/evals/rapporter")


def _is_crm_result(r: dict) -> bool:
    """Return True if a single eval result involves a CRM agent."""
    for a in r.get("actual_routing", r.get("forventet_routing", r.get("expected_routing", []))):
        if "crm" in a.lower():
            return True
    return r.get("kategori", "").lower() == "crm"


def _hapi_stats(resultater: list[dict]) -> dict | None:
    """Compute stats from only the HAPI results, excluding CRM and FEIL_TEKNISK. Returns None if <1 HAPI result."""
    hapi = [r for r in resultater if not _is_crm_result(r)]
    if not hapi:
        return None
    # Skip reports where majority is FEIL_TEKNISK (broken runs)
    scores = [(r.get("score") or r.get("consensus_score") or "").upper() for r in hapi]
    teknisk_count = sum(1 for s in scores if "FEIL_TEKNISK" in s)
    if teknisk_count > len(hapi) / 2:
        return None
    # Exclude FEIL_TEKNISK results from scoring
    hapi = [r for r, s in zip(hapi, scores) if "FEIL_TEKNISK" not in s]
    if not hapi:
        return None
    total = len(hapi)
    scores = [(r.get("score") or r.get("consensus_score") or "").upper() for r in hapi]
    bestatt = sum(1 for s in scores if "BESTATT" in s)
    delvis = sum(1 for s in scores if "DELVIS" in s)
    mangler = sum(1 for s in scores if "MANGLER" in s)
    feil = sum(1 for s in scores if "FEIL" in s)
    hallusinering = sum(1 for s in scores if "HALLUSINERING" in s)
    pct = round((bestatt + delvis) / total * 100) if total else 0
    routing_ok = sum(1 for r in hapi if r.get("routing_correct", True))
    return {
        "antall": total, "korrekthet_pct": pct,
        "bestatt": bestatt, "delvis": delvis, "mangler": mangler,
        "feil": feil, "hallusinering": hallusinering,
        "routing_korrekthet": routing_ok, "total": total,
    }


@app.get("/api/evals/summary")
async def evals_summary():
    """Return eval report summaries over time for the stats dashboard."""
    summaries = []
    if not EVALS_DIR.exists():
        return {"reports": summaries}

    for f in sorted(EVALS_DIR.glob("rapport-*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        resultater = data.get("resultater", [])
        if not resultater:
            continue

        stats = _hapi_stats(resultater)
        if not stats or stats["antall"] <= 10:
            continue

        meta = data.get("metadata", {})
        tidspunkt = meta.get("tidspunkt") or data.get("tidspunkt")

        summaries.append({
            "file": f.name,
            "tidspunkt": tidspunkt,
            "tag": meta.get("tag", ""),
            "versjon": meta.get("versjon", ""),
            **stats,
        })

    return {"reports": summaries}


@app.get("/api/evals/report/{filename}")
async def eval_report_detail(filename: str):
    """Return full detail for a single eval report."""
    if not re.match(r'^rapport-[\w\-]+\.json$', filename):
        raise HTTPException(status_code=400, detail="Ugyldig filnavn")
    filepath = EVALS_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Rapport ikke funnet")
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    # Filter out CRM results
    if "resultater" in data:
        data["resultater"] = [r for r in data["resultater"] if not _is_crm_result(r)]
    return data


@app.get("/stats", include_in_schema=False)
async def stats_page():
    """Serve eval statistics page."""
    return FileResponse(STATIC_DIR / "stats.html")


@app.get("/", include_in_schema=False)
async def root():
    """Serve chat UI."""
    return FileResponse(STATIC_DIR / "index.html")


# --- Kjor lokalt ---

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
