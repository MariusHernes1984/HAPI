"""
Admin router — endepunkter for å redigere agent-prompts og modeller live.

Auth: HTTPBasic mot ADMIN_PASSWORD (admin_auth.py).
Persistens: Azure Table Storage (agent_config.py).
Foundry-sync: PATCH agent ved save/restore (gjøres inni AgentConfigStore).

Test-spørring bruker Responses API direkte med inline instructions —
slik at testen ikke rører den deployerte Foundry-agenten.
"""

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from admin_auth import verify_admin
import agent_config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])

# Pekes på fra app.py via state — vi setter modul-attributt etter include_router.
PROJECT_ENDPOINT = os.environ.get(
    "PROJECT_ENDPOINT",
    "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
)

STATIC_DIR = Path(__file__).parent / "static"


# --- Foundry-agent-katalogen som admin-UI viser ---

# Henter navn + beskrivelse fra app.py-konstanter, men dupliserer her for
# å unngå sirkulær import. Synkroniseres manuelt hvis listen utvides.
AGENT_CATALOG: list[dict] = [
    {"name": "hapi-retningslinje-agent",  "label": "Retningslinje",   "description": "Retningslinjer, anbefalinger, pakkeforløp, antibiotika"},
    {"name": "hapi-kodeverk-agent",       "label": "Kodeverk",        "description": "ICD-10, ICPC-2, SNOMED, ATC, FEST"},
    {"name": "hapi-statistikk-agent",     "label": "Statistikk",      "description": "Nasjonale kvalitetsindikatorer (NKI), trender"},
    {"name": "hapi-ndla-agent",           "label": "NDLA",            "description": "Helsefagarbeider Vg2 — pensum, prosedyrer"},
    {"name": "hapi-felleskatalogen-agent","label": "Felleskatalogen", "description": "Verbatim doseringssitat fra preparatomtalen"},
    {"name": "hapi-orkestrator",          "label": "Orkestrator",     "description": "LLM-router (kun fallback ved tvetydige spørsmål)"},
    {"name": "hapi-kjernejournal-agent",  "label": "Kjernejournal",   "description": "Mock-pasientdata (lokal agent — ingen Foundry-prompt)"},
    {"name": agent_config.SYNTHESIS_AGENT,"label": "Synthesis",       "description": "LLM-syntese av agent-svar (orchestrate.py)"},
]


# --- Pydantic-modeller ---

class AgentSummary(BaseModel):
    agent_name: str
    label: str
    description: str
    prompt: str = ""
    model: str = ""
    updated_at: str = ""
    updated_by: str = ""
    sync_status: str = "ok"
    foundry_agent_id: str = ""


class SaveRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=30000)
    model: str = Field(..., min_length=1, max_length=100)


class TestRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=30000)
    model: str = Field(..., min_length=1, max_length=100)
    query: str = Field(..., min_length=1, max_length=2000)


class TestResponse(BaseModel):
    answer: str
    duration_ms: int
    model: str


class HistoryEntry(BaseModel):
    row_key: str
    prompt: str
    model: str
    updated_at: str
    updated_by: str


class AuditEntry(BaseModel):
    row_key: str
    user: str
    agent_name: str
    operation: str
    model_old: str = ""
    model_new: str = ""
    prompt_changed: bool = False
    timestamp_iso: str = ""
    note: str = ""


# --- Endpoints ---

@router.get("", include_in_schema=False)
@router.get("/", include_in_schema=False)
async def admin_index():
    """Server admin.html (login skjer i frontend mot /admin/api/login).

    HTML-en er bare et skjelett pluss admin.js — ingen sensitive data eksponeres.
    All auth er på /admin/api/* via HTTPBasic.
    """
    return FileResponse(STATIC_DIR / "admin.html")


@router.get("/api/login")
async def login(_user: str = Depends(verify_admin)):
    """Lett-vekt auth-test brukt av frontend før den henter agentlisten."""
    return {"ok": True, "user": _user}


@router.get("/api/agents", response_model=list[AgentSummary])
async def list_agents(_user: str = Depends(verify_admin)):
    """List alle agenter med gjeldende prompt+model fra Table.

    Hvis Table ikke er aktivert eller mangler en agent, returneres katalogen
    med tomme prompt/model-felt. UI kan da vise "ikke seedet"-status.
    """
    by_name: dict[str, dict] = {}
    if agent_config.is_enabled():
        for entry in await agent_config.alist_all(PROJECT_ENDPOINT):
            by_name[entry["agent_name"]] = entry

    out = []
    for cat in AGENT_CATALOG:
        cur = by_name.get(cat["name"], {})
        out.append(AgentSummary(
            agent_name=cat["name"],
            label=cat["label"],
            description=cat["description"],
            prompt=cur.get("prompt", ""),
            model=cur.get("model", ""),
            updated_at=cur.get("updated_at", ""),
            updated_by=cur.get("updated_by", ""),
            sync_status=cur.get("sync_status", "not-seeded" if not cur else "ok"),
            foundry_agent_id=cur.get("foundry_agent_id", ""),
        ))
    return out


@router.get("/api/agents/{agent_name}", response_model=AgentSummary)
async def get_agent(agent_name: str, _user: str = Depends(verify_admin)):
    """Hent én agent."""
    cat = next((c for c in AGENT_CATALOG if c["name"] == agent_name), None)
    if not cat:
        raise HTTPException(404, f"Ukjent agent: {agent_name}")
    cur = await agent_config.aget_current(PROJECT_ENDPOINT, agent_name) or {}
    return AgentSummary(
        agent_name=agent_name,
        label=cat["label"],
        description=cat["description"],
        prompt=cur.get("prompt", ""),
        model=cur.get("model", ""),
        updated_at=cur.get("updated_at", ""),
        updated_by=cur.get("updated_by", ""),
        sync_status=cur.get("sync_status", "not-seeded" if not cur else "ok"),
        foundry_agent_id=cur.get("foundry_agent_id", ""),
    )


@router.post("/api/agents/{agent_name}", response_model=AgentSummary)
async def save_agent(agent_name: str, req: SaveRequest, user: str = Depends(verify_admin)):
    """Lagre ny prompt/model. Synces til Foundry. Forrige verdi går til history."""
    cat = next((c for c in AGENT_CATALOG if c["name"] == agent_name), None)
    if not cat:
        raise HTTPException(404, f"Ukjent agent: {agent_name}")

    if not agent_config.is_enabled():
        raise HTTPException(503, "AGENT_CONFIG_STORAGE_ACCOUNT er ikke satt.")

    try:
        result = await agent_config.asave(
            PROJECT_ENDPOINT, agent_name, req.prompt, req.model, user
        )
    except Exception as e:
        logger.exception("save_agent feilet")
        raise HTTPException(500, str(e))

    return AgentSummary(
        agent_name=agent_name,
        label=cat["label"],
        description=cat["description"],
        prompt=result["prompt"],
        model=result["model"],
        updated_at=result["updated_at"],
        updated_by=result["updated_by"],
        sync_status=result["sync_status"],
        foundry_agent_id=result.get("foundry_agent_id", ""),
    )


@router.get("/api/agents/{agent_name}/history", response_model=list[HistoryEntry])
async def agent_history(agent_name: str, limit: int = Query(50, ge=1, le=500),
                        _user: str = Depends(verify_admin)):
    """Versjonshistorikk for én agent."""
    if not agent_config.is_enabled():
        return []
    rows = await agent_config.alist_history(PROJECT_ENDPOINT, agent_name, limit)
    return [HistoryEntry(**r) for r in rows]


@router.post("/api/agents/{agent_name}/restore/{row_key}", response_model=AgentSummary)
async def restore_agent(agent_name: str, row_key: str, user: str = Depends(verify_admin)):
    """Rollback til en tidligere versjon."""
    cat = next((c for c in AGENT_CATALOG if c["name"] == agent_name), None)
    if not cat:
        raise HTTPException(404, f"Ukjent agent: {agent_name}")
    if not agent_config.is_enabled():
        raise HTTPException(503, "AGENT_CONFIG_STORAGE_ACCOUNT er ikke satt.")
    try:
        result = await agent_config.arestore(PROJECT_ENDPOINT, agent_name, row_key, user)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("restore feilet")
        raise HTTPException(500, str(e))
    return AgentSummary(
        agent_name=agent_name,
        label=cat["label"],
        description=cat["description"],
        prompt=result["prompt"],
        model=result["model"],
        updated_at=result["updated_at"],
        updated_by=result["updated_by"],
        sync_status=result["sync_status"],
        foundry_agent_id=result.get("foundry_agent_id", ""),
    )


@router.post("/api/agents/{agent_name}/resync", response_model=AgentSummary)
async def resync_agent(agent_name: str, _user: str = Depends(verify_admin)):
    """Manuell re-sync til Foundry etter feilet save."""
    cat = next((c for c in AGENT_CATALOG if c["name"] == agent_name), None)
    if not cat:
        raise HTTPException(404, f"Ukjent agent: {agent_name}")
    if not agent_config.is_enabled():
        raise HTTPException(503, "AGENT_CONFIG_STORAGE_ACCOUNT er ikke satt.")
    try:
        result = await agent_config.aresync(PROJECT_ENDPOINT, agent_name)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("resync feilet")
        raise HTTPException(500, str(e))
    return AgentSummary(
        agent_name=agent_name,
        label=cat["label"],
        description=cat["description"],
        prompt=result["prompt"],
        model=result["model"],
        updated_at=result["updated_at"],
        updated_by=result["updated_by"],
        sync_status=result["sync_status"],
        foundry_agent_id=result.get("foundry_agent_id", ""),
    )


@router.post("/api/agents/{agent_name}/test", response_model=TestResponse)
async def test_agent(agent_name: str, req: TestRequest, user: str = Depends(verify_admin)):
    """
    Test-spørring med inline instructions+model — RØRER IKKE produksjon-agenten.
    Bruker Responses API direkte. Loggføres som test i audit.
    """
    import time
    cat = next((c for c in AGENT_CATALOG if c["name"] == agent_name), None)
    if not cat:
        raise HTTPException(404, f"Ukjent agent: {agent_name}")

    # Audit (best-effort)
    if agent_config.is_enabled():
        await agent_config.awrite_test_audit(
            PROJECT_ENDPOINT, user, agent_name, req.model, req.query
        )

    start = time.monotonic()
    try:
        from azure.identity.aio import DefaultAzureCredential as AsyncCred
        from azure.ai.projects.aio import AIProjectClient as AsyncProjectClient

        async with AsyncCred() as cred:
            async with AsyncProjectClient(endpoint=PROJECT_ENDPOINT, credential=cred) as project:
                openai = project.get_openai_client()
                # Bruker Responses API med inline instructions + model. Tester kun
                # prompten — agentens MCP-tools er ikke tilgjengelig her, men det
                # er bevisst (vi vil isolere prompt-effekten).
                response = await openai.responses.create(
                    model=req.model,
                    instructions=req.prompt,
                    input=req.query,
                )
                answer = response.output_text or ""
    except Exception as e:
        logger.exception("test_agent feilet")
        raise HTTPException(500, f"Test feilet: {e}")

    duration_ms = int((time.monotonic() - start) * 1000)
    return TestResponse(answer=answer, duration_ms=duration_ms, model=req.model)


@router.get("/api/models", response_model=list[str])
async def list_models(_user: str = Depends(verify_admin)):
    """
    Liste over tilgjengelige modell-deployments.

    Forsøker først å hente fra Foundry (hvis SDK støtter listing).
    Faller tilbake til en kjent kuratert liste basert på prosjektets evals.
    """
    fallback = [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.3-chat",
        "gpt-4o",
        "gpt-4.1",
        "gpt-4o-mini",
    ]

    def _try_fetch():
        try:
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential
            with AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential()) as client:
                # SDK-en endrer seg mellom versjoner — prøv flere kjente metodenavn.
                for attr in ("deployments", "connections"):
                    if hasattr(client, attr):
                        listing = getattr(client, attr)
                        if hasattr(listing, "list"):
                            try:
                                items = list(listing.list())
                            except Exception:
                                continue
                            names: set[str] = set()
                            for it in items:
                                n = getattr(it, "name", None) or getattr(it, "model", None)
                                if isinstance(n, str):
                                    names.add(n)
                            if names:
                                return sorted(names)
        except Exception as e:
            logger.warning(f"list_models live-fetch feilet: {e}")
        return None

    live = await asyncio.to_thread(_try_fetch)
    return live or fallback


@router.get("/api/audit", response_model=list[AuditEntry])
async def audit_log(limit: int = Query(100, ge=1, le=500), _user: str = Depends(verify_admin)):
    """Audit-logg (siste 100 handlinger)."""
    if not agent_config.is_enabled():
        return []
    rows = await agent_config.alist_audit(PROJECT_ENDPOINT, limit)
    return [AuditEntry(**r) for r in rows]
