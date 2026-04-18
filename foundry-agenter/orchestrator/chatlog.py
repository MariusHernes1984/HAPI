"""
Chatlog — Azure Table Storage-backed logging of patient-linked chat sessions.

Writes one row per /ask call when both patient_id and user_id are set.
Reads are partitioned by patient_id for efficient retrieval in the dashboard.

Environment:
  CHATLOG_STORAGE_ACCOUNT — storage account name. If unset, the module is
  disabled and all public functions become no-ops (useful for local dev).

Auth: DefaultAzureCredential (az login locally, SystemAssigned MI in prod).
"""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_ACCOUNT = os.environ.get("CHATLOG_STORAGE_ACCOUNT", "").strip()
_TABLE_NAME = "chatlog"
_MAX_PROP_BYTES = 31 * 1024  # Table Storage limit is 32KB per string property

_enabled = bool(_ACCOUNT)
_table_client = None


def _get_table_client():
    global _table_client
    if _table_client is not None:
        return _table_client
    from azure.data.tables import TableServiceClient
    from azure.identity import DefaultAzureCredential

    endpoint = f"https://{_ACCOUNT}.table.core.windows.net"
    service = TableServiceClient(endpoint=endpoint, credential=DefaultAzureCredential())
    _table_client = service.get_table_client(_TABLE_NAME)
    return _table_client


def _truncate(s: Optional[str]) -> str:
    if not s:
        return ""
    b = s.encode("utf-8")
    if len(b) <= _MAX_PROP_BYTES:
        return s
    return b[:_MAX_PROP_BYTES].decode("utf-8", errors="ignore") + "… [trunkert]"


def _row_key(ts_epoch: float) -> str:
    reverse_ts = 9999999999 - int(ts_epoch)
    return f"{reverse_ts:010d}_{uuid.uuid4().hex[:8]}"


async def log_chat(
    patient_id: str,
    user_id: str,
    query: str,
    response: dict,
) -> None:
    """Write one chatlog entry. Silently no-op if disabled or on any error."""
    if not _enabled or not patient_id or not user_id:
        return

    now = datetime.now(timezone.utc)
    routing = response.get("routing") or {}
    agents_used = routing.get("agents") or []

    entity = {
        "PartitionKey": patient_id,
        "RowKey": _row_key(now.timestamp()),
        "user_id": user_id[:100],
        "query": _truncate(query),
        "answer": _truncate(response.get("answer", "")),
        "agents_used": json.dumps(agents_used, ensure_ascii=False),
        "confidence": str(routing.get("confidence") or ""),
        "total_duration_ms": int(response.get("total_duration_ms") or 0),
        "interaksjonssjekk": bool(response.get("interaksjonssjekk")),
        "timestamp_iso": now.isoformat(),
    }

    try:
        table = _get_table_client()
        await asyncio.to_thread(table.create_entity, entity)
        logger.info(f"chatlog write ok: {patient_id} by {user_id}")
    except Exception as e:
        logger.warning(f"chatlog write failed for {patient_id}: {e}")


async def get_chatlog(patient_id: str, limit: int = 50) -> list[dict]:
    """Return chatlog entries for a patient, newest first. Empty list if disabled."""
    if not _enabled or not patient_id:
        return []

    def _fetch():
        table = _get_table_client()
        entities = table.query_entities(
            query_filter=f"PartitionKey eq '{patient_id}'",
            results_per_page=limit,
        )
        out = []
        for e in entities:
            if len(out) >= limit:
                break
            agents = e.get("agents_used", "[]")
            try:
                agents = json.loads(agents) if isinstance(agents, str) else agents
            except json.JSONDecodeError:
                agents = []
            out.append({
                "timestamp_iso": e.get("timestamp_iso", ""),
                "user_id": e.get("user_id", ""),
                "query": e.get("query", ""),
                "answer": e.get("answer", ""),
                "agents_used": agents,
                "confidence": e.get("confidence", ""),
                "total_duration_ms": e.get("total_duration_ms", 0),
                "interaksjonssjekk": bool(e.get("interaksjonssjekk", False)),
            })
        return out

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        logger.warning(f"chatlog read failed for {patient_id}: {e}")
        return []
