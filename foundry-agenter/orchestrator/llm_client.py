"""LLM-klient som ruter mellom Azure OpenAI Responses API og Anthropic Foundry Messages API.

Modeller som starter med "claude-" går til AnthropicFoundry SDK med Entra-token.
Alle andre går til Azure OpenAI Responses API (via project.get_openai_client()).

Brukes av orchestrate.py (syntese) og router.py (LLM-routing) for å gjøre det mulig
å A/B-teste claude-opus-4-8 mot gpt-5.4 / gpt-5.3-chat uten å endre kalle-stedene.

Endepunkt:
    PROJECT_ENDPOINT = .../api/projects/<name>  →  Anthropic base = .../anthropic
    (kan overstyres med ANTHROPIC_BASE_URL env-var)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Lazy-cachede Anthropic-klienter. Bygges først gang en claude-* modell brukes.
_SYNC_CLIENT: Any = None
_ASYNC_CLIENT: Any = None
_ASYNC_AVAILABLE: bool | None = None  # None = ikke testet, True/False = resultat

CLAUDE_MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "4096"))


def _anthropic_base_url() -> str:
    """Avled Anthropic base URL fra PROJECT_ENDPOINT, eller bruk ANTHROPIC_BASE_URL."""
    explicit = os.environ.get("ANTHROPIC_BASE_URL")
    if explicit:
        return explicit
    project = os.environ.get(
        "PROJECT_ENDPOINT",
        "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
    )
    # PROJECT_ENDPOINT er ".../api/projects/<name>" — Anthropic ligger på "/anthropic"
    base = project.split("/api/projects/")[0]
    return f"{base}/anthropic"


def _token_provider():
    """Bygg Entra-token-provider for scope https://ai.azure.com/.default."""
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    return get_bearer_token_provider(
        DefaultAzureCredential(), "https://ai.azure.com/.default"
    )


def _get_sync_client():
    global _SYNC_CLIENT
    if _SYNC_CLIENT is None:
        from anthropic import AnthropicFoundry  # type: ignore
        _SYNC_CLIENT = AnthropicFoundry(
            azure_ad_token_provider=_token_provider(),
            base_url=_anthropic_base_url(),
        )
        logger.info(f"AnthropicFoundry sync client initialized: {_anthropic_base_url()}")
    return _SYNC_CLIENT


def _get_async_client():
    """Returner AsyncAnthropicFoundry hvis tilgjengelig, ellers None (caller bruker to_thread)."""
    global _ASYNC_CLIENT, _ASYNC_AVAILABLE
    if _ASYNC_AVAILABLE is False:
        return None
    if _ASYNC_CLIENT is not None:
        return _ASYNC_CLIENT
    try:
        from anthropic import AsyncAnthropicFoundry  # type: ignore
        _ASYNC_CLIENT = AsyncAnthropicFoundry(
            azure_ad_token_provider=_token_provider(),
            base_url=_anthropic_base_url(),
        )
        _ASYNC_AVAILABLE = True
        logger.info(f"AsyncAnthropicFoundry initialized: {_anthropic_base_url()}")
        return _ASYNC_CLIENT
    except ImportError:
        logger.info("AsyncAnthropicFoundry ikke tilgjengelig — bruker to_thread på sync client")
        _ASYNC_AVAILABLE = False
        return None


def _extract_text(message: Any) -> str:
    """Plukk ut tekst fra Anthropic message.content (liste av content blocks)."""
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            parts.append(getattr(block, "text", ""))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


async def acomplete_text(project: Any, model: str, prompt: str) -> str:
    """Async LLM-kall — claude-* via Anthropic, ellers via Azure OpenAI Responses API.

    `project` er AIProjectClient (sync eller async) — vi henter openai-klienten kun
    hvis modellen ikke er Claude.
    """
    if model.startswith("claude-"):
        async_client = _get_async_client()
        if async_client is not None:
            msg = await async_client.messages.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=CLAUDE_MAX_TOKENS,
            )
        else:
            # Fallback: kjør sync-klient i thread-pool
            sync_client = _get_sync_client()
            msg = await asyncio.to_thread(
                sync_client.messages.create,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=CLAUDE_MAX_TOKENS,
            )
        return _extract_text(msg)

    openai = project.get_openai_client()
    response = await openai.responses.create(model=model, input=prompt)
    return response.output_text


def complete_text(openai_client: Any, model: str, prompt: str) -> str:
    """Sync LLM-kall — claude-* via Anthropic, ellers via openai_client.responses.create.

    `openai_client` brukes kun for gpt-*-modeller; for claude-* bygges/cached Anthropic
    selv.
    """
    if model.startswith("claude-"):
        client = _get_sync_client()
        msg = client.messages.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=CLAUDE_MAX_TOKENS,
        )
        return _extract_text(msg)

    response = openai_client.responses.create(model=model, input=prompt)
    return response.output_text
