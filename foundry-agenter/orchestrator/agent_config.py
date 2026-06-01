"""
Agent configuration store — Table Storage source-of-truth + Foundry sync.

Tabeller:
  agentconfig          — gjeldende prompt+model per agent (PK=agent_name, RK="current")
  agentconfig_history  — versjonshistorikk (PK=agent_name, RK=reverse-ts+uuid)
  admin_audit          — handlingslogg (PK=YYYY-MM, RK=reverse-ts+uuid)

Foundry-sync skjer ved save/restore: PATCH instructions+model på den
navngitte agenten i Foundry. Synthesis er ikke en Foundry-agent og
synces kun til Table — orchestrate.py leser den derfra.

Env: AGENT_CONFIG_STORAGE_ACCOUNT (eller CHATLOG_STORAGE_ACCOUNT som fallback).
Auth: DefaultAzureCredential (az login lokalt, MI i prod).
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

# --- Konfigurasjon ---

_ACCOUNT = (
    os.environ.get("AGENT_CONFIG_STORAGE_ACCOUNT", "").strip()
    or os.environ.get("CHATLOG_STORAGE_ACCOUNT", "").strip()
)
_CONFIG_TABLE = "agentconfig"
_HISTORY_TABLE = "agentconfighistory"
_AUDIT_TABLE = "adminaudit"
_MAX_PROP_BYTES = 31 * 1024  # Table Storage 32KB limit per string property

# Synthesis er ikke en Foundry-agent — har eget pseudo-navn i Table.
SYNTHESIS_AGENT = "hapi-synthesis"

_enabled = bool(_ACCOUNT)
_service_client = None
_agent_id_cache: dict[str, str] = {}


def is_enabled() -> bool:
    return _enabled


def _get_service():
    global _service_client
    if _service_client is not None:
        return _service_client
    from azure.data.tables import TableServiceClient
    from azure.identity import DefaultAzureCredential

    endpoint = f"https://{_ACCOUNT}.table.core.windows.net"
    _service_client = TableServiceClient(endpoint=endpoint, credential=DefaultAzureCredential())
    return _service_client


def _get_table(name: str):
    """Hent table client og opprett tabellen om den ikke finnes."""
    service = _get_service()
    table = service.get_table_client(name)
    try:
        service.create_table_if_not_exists(name)
    except Exception:
        pass
    return table


def _truncate(s: Optional[str]) -> str:
    if not s:
        return ""
    b = s.encode("utf-8")
    if len(b) <= _MAX_PROP_BYTES:
        return s
    return b[:_MAX_PROP_BYTES].decode("utf-8", errors="ignore") + "… [trunkert]"


def _row_key(ts_epoch: float) -> str:
    """Reverse-timestamp + 8-char uuid → nyeste først ved standard sortering."""
    reverse_ts = 9999999999 - int(ts_epoch)
    return f"{reverse_ts:010d}_{uuid.uuid4().hex[:8]}"


# --- Foundry sync ---

def _foundry_current_definition(agent_name: str, project_endpoint: str) -> Optional[dict]:
    """
    Hent definition-dict (kind, model, instructions, tools) fra nåværende
    aktive versjon av Foundry-agenten. Returnerer None ved feil.
    """
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        with AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential()) as client:
            versions = list(client.agents.list_versions(agent_name))
            if not versions:
                return None
            # Foundry returnerer nyeste versjon først; foretrekk status='active' om sett
            active = next(
                (v for v in versions if (v.as_dict().get("status") or "").lower() == "active"),
                versions[0],
            )
            d = active.as_dict()
            _agent_id_cache[agent_name] = d.get("id") or agent_name
            return d.get("definition") or None
    except Exception as e:
        logger.warning(f"foundry_current_definition({agent_name}) feilet: {e}")
        return None


def _sync_to_foundry(agent_name: str, prompt: str, model: str, project_endpoint: str) -> tuple[bool, str]:
    """
    Lag en ny versjon av Foundry-agenten med oppdatert instructions+model.
    Beholder eksisterende tools-binding fra forrige versjon. Returnerer (ok, message).
    Synthesis-agenten finnes ikke i Foundry — returner (True, "skipped").
    """
    if agent_name == SYNTHESIS_AGENT:
        return True, "synthesis is local — no Foundry sync needed"

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        with AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential()) as client:
            # Hent forrige definition for å beholde tools/kind
            versions = list(client.agents.list_versions(agent_name))
            if not versions:
                return False, f"ingen versjoner funnet for {agent_name}"
            prev = versions[0].as_dict()
            prev_def = prev.get("definition") or {}

            new_def = {
                "kind": prev_def.get("kind", "prompt_agent"),
                "model": model,
                "instructions": prompt,
            }
            # Behold tools hvis de fantes
            if "tools" in prev_def:
                new_def["tools"] = prev_def["tools"]

            # SDK exposes create_version(agent_name, definition, ...). Kall med kwargs
            # for å være robust mot mindre signatur-variasjoner mellom versjoner.
            client.agents.create_version(agent_name=agent_name, definition=new_def)
        return True, "ok"
    except Exception as e:
        logger.error(f"Foundry sync feilet for {agent_name}: {e}")
        return False, str(e)


# --- AgentConfigStore ---

class AgentConfigStore:
    """Table-backed config store med Foundry-sync."""

    def __init__(self, project_endpoint: str):
        self.project_endpoint = project_endpoint

    def _config_table(self):
        return _get_table(_CONFIG_TABLE)

    def _history_table(self):
        return _get_table(_HISTORY_TABLE)

    def _audit_table(self):
        return _get_table(_AUDIT_TABLE)

    # --- read ---

    def get_current(self, agent_name: str) -> Optional[dict]:
        """Returner gjeldende config for én agent. None hvis ikke seedet."""
        if not _enabled:
            return None
        try:
            entity = self._config_table().get_entity(agent_name, "current")
            return {
                "agent_name": agent_name,
                "prompt": entity.get("prompt", ""),
                "model": entity.get("model", ""),
                "updated_at": entity.get("updated_at", ""),
                "updated_by": entity.get("updated_by", ""),
                "sync_status": entity.get("sync_status", "ok"),
                "foundry_agent_id": entity.get("foundry_agent_id", ""),
                "description": entity.get("description", ""),
            }
        except Exception:
            return None

    def list_all(self) -> list[dict]:
        """Liste alle current-rader. Returnerer en liste sortert på agent_name."""
        if not _enabled:
            return []
        try:
            entities = self._config_table().query_entities("RowKey eq 'current'")
            out = []
            for e in entities:
                out.append({
                    "agent_name": e.get("PartitionKey", ""),
                    "prompt": e.get("prompt", ""),
                    "model": e.get("model", ""),
                    "updated_at": e.get("updated_at", ""),
                    "updated_by": e.get("updated_by", ""),
                    "sync_status": e.get("sync_status", "ok"),
                    "foundry_agent_id": e.get("foundry_agent_id", ""),
                    "description": e.get("description", ""),
                })
            out.sort(key=lambda x: x["agent_name"])
            return out
        except Exception as e:
            logger.warning(f"list_all feilet: {e}")
            return []

    def list_history(self, agent_name: str, limit: int = 50) -> list[dict]:
        """Versjonshistorikk for én agent — nyeste først."""
        if not _enabled:
            return []
        try:
            entities = self._history_table().query_entities(
                f"PartitionKey eq '{agent_name}'",
                results_per_page=limit,
            )
            out = []
            for e in entities:
                if len(out) >= limit:
                    break
                out.append({
                    "row_key": e.get("RowKey", ""),
                    "prompt": e.get("prompt", ""),
                    "model": e.get("model", ""),
                    "updated_at": e.get("updated_at", ""),
                    "updated_by": e.get("updated_by", ""),
                })
            return out
        except Exception as e:
            logger.warning(f"list_history({agent_name}) feilet: {e}")
            return []

    def get_history_entry(self, agent_name: str, row_key: str) -> Optional[dict]:
        if not _enabled:
            return None
        try:
            e = self._history_table().get_entity(agent_name, row_key)
            return {
                "row_key": row_key,
                "prompt": e.get("prompt", ""),
                "model": e.get("model", ""),
                "updated_at": e.get("updated_at", ""),
                "updated_by": e.get("updated_by", ""),
            }
        except Exception:
            return None

    def list_audit(self, limit: int = 100) -> list[dict]:
        """Audit-logg, nyeste først (på tvers av PK YYYY-MM)."""
        if not _enabled:
            return []
        try:
            entities = self._audit_table().list_entities(results_per_page=limit)
            out = []
            for e in entities:
                if len(out) >= limit * 3:  # lese litt mer for sortering
                    break
                out.append({
                    "row_key": e.get("RowKey", ""),
                    "user": e.get("user", ""),
                    "agent_name": e.get("agent_name", ""),
                    "operation": e.get("operation", ""),
                    "model_old": e.get("model_old", ""),
                    "model_new": e.get("model_new", ""),
                    "prompt_changed": bool(e.get("prompt_changed", False)),
                    "timestamp_iso": e.get("timestamp_iso", ""),
                    "note": e.get("note", ""),
                })
            # RowKey starter med reverse-timestamp → leksikografisk sortering = kronologisk
            out.sort(key=lambda x: x["row_key"])
            return out[:limit]
        except Exception as e:
            logger.warning(f"list_audit feilet: {e}")
            return []

    # --- write ---

    def upsert_initial(self, agent_name: str, prompt: str, model: str,
                       foundry_agent_id: str = "", description: str = "") -> None:
        """Seed-funksjon — fyller current-raden uten å skrive history eller røre Foundry."""
        if not _enabled:
            return
        now = datetime.now(timezone.utc)
        entity = {
            "PartitionKey": agent_name,
            "RowKey": "current",
            "prompt": _truncate(prompt),
            "model": model,
            "updated_at": now.isoformat(),
            "updated_by": "seed",
            "sync_status": "ok",
            "foundry_agent_id": foundry_agent_id,
            "description": description,
        }
        self._config_table().upsert_entity(entity)

    def save(self, agent_name: str, prompt: str, model: str, user: str,
             skip_foundry_sync: bool = False) -> dict:
        """
        Lagre ny prompt/model:
          1. Hent gjeldende → flytt til history
          2. Upsert ny som current (sync_status=pending)
          3. Sync til Foundry
          4. Oppdater sync_status (ok eller pending med error)
          5. Skriv audit-rad
        Returnerer den nye current-raden.
        """
        if not _enabled:
            raise RuntimeError("AGENT_CONFIG_STORAGE_ACCOUNT er ikke satt — kan ikke lagre")

        now = datetime.now(timezone.utc)
        prev = self.get_current(agent_name) or {}

        # 1. Flytt forrige til history (hvis det var noe)
        if prev:
            try:
                self._history_table().create_entity({
                    "PartitionKey": agent_name,
                    "RowKey": _row_key(now.timestamp()),
                    "prompt": _truncate(prev.get("prompt", "")),
                    "model": prev.get("model", ""),
                    "updated_at": prev.get("updated_at", ""),
                    "updated_by": prev.get("updated_by", ""),
                })
            except Exception as e:
                logger.warning(f"history insert feilet for {agent_name}: {e}")

        # 2. Upsert ny som current
        new_entity = {
            "PartitionKey": agent_name,
            "RowKey": "current",
            "prompt": _truncate(prompt),
            "model": model,
            "updated_at": now.isoformat(),
            "updated_by": (user or "")[:100],
            "sync_status": "pending",
            "foundry_agent_id": prev.get("foundry_agent_id", ""),
            "description": prev.get("description", ""),
        }
        self._config_table().upsert_entity(new_entity)

        # 3. Sync til Foundry
        sync_msg = "skipped"
        if skip_foundry_sync:
            new_entity["sync_status"] = "skipped"
        else:
            ok, sync_msg = _sync_to_foundry(agent_name, prompt, model, self.project_endpoint)
            new_entity["sync_status"] = "ok" if ok else f"error: {sync_msg[:200]}"
            self._config_table().upsert_entity(new_entity)

        # 4. Audit
        self._write_audit(
            user=user,
            agent_name=agent_name,
            operation="save",
            prompt_old=prev.get("prompt", ""),
            prompt_new=prompt,
            model_old=prev.get("model", ""),
            model_new=model,
            note=sync_msg,
        )

        return {
            "agent_name": agent_name,
            "prompt": prompt,
            "model": model,
            "updated_at": now.isoformat(),
            "updated_by": user,
            "sync_status": new_entity["sync_status"],
            "foundry_agent_id": new_entity["foundry_agent_id"],
        }

    def restore(self, agent_name: str, history_row_key: str, user: str) -> dict:
        """Rollback til en historikk-rad. Skriver via save() så audit blir riktig."""
        entry = self.get_history_entry(agent_name, history_row_key)
        if not entry:
            raise ValueError(f"Historikk-rad ikke funnet: {agent_name}/{history_row_key}")
        return self.save(
            agent_name=agent_name,
            prompt=entry["prompt"],
            model=entry["model"],
            user=f"{user} (restore from {history_row_key})",
        )

    def resync(self, agent_name: str) -> dict:
        """Manuell re-sync: skriv current til Foundry på nytt (etter feilet save)."""
        cur = self.get_current(agent_name)
        if not cur:
            raise ValueError(f"Ingen current-rad for {agent_name}")
        ok, msg = _sync_to_foundry(agent_name, cur["prompt"], cur["model"], self.project_endpoint)
        cur["sync_status"] = "ok" if ok else f"error: {msg[:200]}"
        self._config_table().upsert_entity({
            "PartitionKey": agent_name,
            "RowKey": "current",
            "prompt": _truncate(cur["prompt"]),
            "model": cur["model"],
            "updated_at": cur["updated_at"],
            "updated_by": cur["updated_by"],
            "sync_status": cur["sync_status"],
            "foundry_agent_id": cur["foundry_agent_id"],
            "description": cur.get("description", ""),
        })
        return cur

    def _write_audit(self, *, user: str, agent_name: str, operation: str,
                     prompt_old: str = "", prompt_new: str = "",
                     model_old: str = "", model_new: str = "",
                     note: str = "") -> None:
        if not _enabled:
            return
        now = datetime.now(timezone.utc)
        try:
            self._audit_table().create_entity({
                "PartitionKey": now.strftime("%Y-%m"),
                "RowKey": _row_key(now.timestamp()),
                "user": (user or "")[:100],
                "agent_name": agent_name,
                "operation": operation,
                "model_old": model_old,
                "model_new": model_new,
                "prompt_changed": prompt_old.strip() != prompt_new.strip(),
                "prompt_old_len": len(prompt_old or ""),
                "prompt_new_len": len(prompt_new or ""),
                "timestamp_iso": now.isoformat(),
                "note": (note or "")[:500],
            })
        except Exception as e:
            logger.warning(f"audit write feilet: {e}")

    def write_test_audit(self, user: str, agent_name: str, model: str, query: str) -> None:
        """Logg en test-spørring (ikke en mutasjon — kun for sporbarhet)."""
        self._write_audit(
            user=user,
            agent_name=agent_name,
            operation="test",
            model_old="",
            model_new=model,
            note=f"test-query: {query[:200]}",
        )


# --- Async wrappers (FastAPI endpoints kjører i async event loop) ---

_store_singleton: Optional[AgentConfigStore] = None


def get_store(project_endpoint: str) -> AgentConfigStore:
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = AgentConfigStore(project_endpoint)
    return _store_singleton


async def aget_current(project_endpoint: str, agent_name: str) -> Optional[dict]:
    return await asyncio.to_thread(get_store(project_endpoint).get_current, agent_name)


async def alist_all(project_endpoint: str) -> list[dict]:
    return await asyncio.to_thread(get_store(project_endpoint).list_all)


async def alist_history(project_endpoint: str, agent_name: str, limit: int = 50) -> list[dict]:
    return await asyncio.to_thread(get_store(project_endpoint).list_history, agent_name, limit)


async def asave(project_endpoint: str, agent_name: str, prompt: str, model: str, user: str) -> dict:
    return await asyncio.to_thread(
        get_store(project_endpoint).save, agent_name, prompt, model, user, False
    )


async def arestore(project_endpoint: str, agent_name: str, row_key: str, user: str) -> dict:
    return await asyncio.to_thread(get_store(project_endpoint).restore, agent_name, row_key, user)


async def aresync(project_endpoint: str, agent_name: str) -> dict:
    return await asyncio.to_thread(get_store(project_endpoint).resync, agent_name)


async def alist_audit(project_endpoint: str, limit: int = 100) -> list[dict]:
    return await asyncio.to_thread(get_store(project_endpoint).list_audit, limit)


async def awrite_test_audit(project_endpoint: str, user: str, agent_name: str, model: str, query: str) -> None:
    await asyncio.to_thread(
        get_store(project_endpoint).write_test_audit, user, agent_name, model, query
    )
