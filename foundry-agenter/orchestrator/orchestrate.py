"""
Orchestrate — kaller sub-agenter parallelt og syntetiserer resultater.

Flyten:
  1. Router bestemmer hvilke agenter som trengs
  2. Agentene kalles parallelt via asyncio
  3. Resultater samles og sendes til syntese-steget
  4. Endelig svar returneres til bruker
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field

from azure.identity.aio import DefaultAzureCredential as AsyncCredential
from azure.ai.projects.aio import AIProjectClient as AsyncProjectClient

from router import route, route_with_llm, RoutingDecision

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Resultat fra en enkelt agent."""
    agent_name: str
    output: str
    duration_ms: int
    success: bool
    error: str | None = None


@dataclass
class OrchestrationResult:
    """Samlet resultat fra orkestreringen."""
    final_answer: str
    routing: RoutingDecision
    agent_results: list[AgentResult] = field(default_factory=list)
    total_duration_ms: int = 0


# --- Konfigurasjon ---

SYNTHESIS_PROMPT = """Du er en medisinsk oppsummerings-agent. Kombiner svarene fra spesialiserte agenter
til ett sammenhengende, klinisk nyttig svar paa norsk.

Brukerens spoersmaal: {query}

Svar fra agentene:
{agent_outputs}

REGLER:
- Kombiner informasjonen logisk (diagnose -> behandling -> statistikk)
- Behold faglig presisjon — ikke endre meningsinnhold
- Oppgi kilde: Helsedirektoratet
- Hvis agenter gir motstridende info, paapek det
- Hold svaret konsist men komplett
- Bruk overskrifter for aa strukturere svaret"""


async def call_agent(
    project: AsyncProjectClient,
    agent_name: str,
    query: str,
) -> AgentResult:
    """Kall en Foundry-agent og returner resultatet."""
    start = time.monotonic()

    try:
        openai = project.get_openai_client()
        conversation = await openai.conversations.create()

        response = await openai.responses.create(
            conversation=conversation.id,
            input=query,
            extra_body={
                "agent_reference": {
                    "name": agent_name,
                    "type": "agent_reference",
                }
            },
        )

        output = response.output_text
        duration = int((time.monotonic() - start) * 1000)

        # Rydd opp (ignorer feil ved sletting — conversation kan allerede vaere slettet)
        try:
            await openai.conversations.delete(conversation.id)
        except Exception:
            pass

        logger.info(f"  {agent_name}: {duration}ms, {len(output)} tegn")

        return AgentResult(
            agent_name=agent_name,
            output=output,
            duration_ms=duration,
            success=True,
        )

    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        logger.error(f"  {agent_name} FEIL: {e}")
        return AgentResult(
            agent_name=agent_name,
            output="",
            duration_ms=duration,
            success=False,
            error=str(e),
        )


async def synthesize(
    project: AsyncProjectClient,
    query: str,
    results: list[AgentResult],
) -> str:
    """Kombiner agent-resultater til ett svar via LLM."""
    # Hvis bare ett resultat, returner direkte
    successful = [r for r in results if r.success and r.output]
    if len(successful) == 1:
        return successful[0].output

    if not successful:
        return "Beklager, ingen av agentene klarte aa hente data for dette spoersmalet."

    # Bygg agent-output-tekst
    agent_outputs = ""
    for r in successful:
        label = r.agent_name.replace("hapi-", "").replace("-agent", "").upper()
        agent_outputs += f"\n--- {label} ---\n{r.output}\n"

    prompt = SYNTHESIS_PROMPT.format(query=query, agent_outputs=agent_outputs)

    try:
        openai = project.get_openai_client()
        response = await openai.responses.create(
            model="gpt-5.3-chat",
            input=prompt,
        )
        return response.output_text
    except Exception as e:
        logger.error(f"Syntese feilet: {e}")
        # Fallback: konkatener resultatene
        parts = []
        for r in successful:
            parts.append(f"## {r.agent_name}\n{r.output}")
        return "\n\n".join(parts)


async def orchestrate(
    project_endpoint: str,
    query: str,
    use_llm_routing: bool = False,
) -> OrchestrationResult:
    """
    Hovedfunksjon: rut, kall agenter parallelt, syntetiser.

    Args:
        project_endpoint: Azure AI Foundry prosjekt-URL
        query: Brukerens spoersmaal
        use_llm_routing: Bruk LLM for routing ved lav konfidens

    Returns:
        OrchestrationResult med endelig svar og metadata
    """
    start = time.monotonic()

    # Steg 1: Routing
    logger.info(f"Routing: '{query[:80]}...'")
    decision = route(query)
    logger.info(f"  -> {decision.agents} (konfidens: {decision.confidence})")

    # Valgfritt: LLM-routing ved lav konfidens
    if use_llm_routing and decision.confidence == "lav":
        async with AsyncCredential() as cred:
            async with AsyncProjectClient(
                endpoint=project_endpoint, credential=cred
            ) as project:
                openai = project.get_openai_client()
                decision = route_with_llm(query, openai)
                logger.info(f"  LLM re-routing -> {decision.agents}")

    # Steg 2: Kall agenter parallelt
    logger.info(f"Kaller {len(decision.agents)} agent(er) parallelt...")

    async with AsyncCredential() as cred:
        async with AsyncProjectClient(
            endpoint=project_endpoint, credential=cred
        ) as project:
            tasks = [
                call_agent(project, agent_name, query)
                for agent_name in decision.agents
            ]
            results = await asyncio.gather(*tasks)

            # Steg 3: Syntetiser
            logger.info("Syntetiserer svar...")
            final_answer = await synthesize(project, query, list(results))

    total_ms = int((time.monotonic() - start) * 1000)

    return OrchestrationResult(
        final_answer=final_answer,
        routing=decision,
        agent_results=list(results),
        total_duration_ms=total_ms,
    )


# --- CLI for testing ---

async def _main():
    """Kjor orkestrering fra kommandolinjen."""
    import os
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    endpoint = os.environ.get(
        "PROJECT_ENDPOINT",
        "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
    )

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Hva er anbefalt behandling for KOLS?"

    print(f"\nSpoersmaal: {query}\n")

    result = await orchestrate(endpoint, query)

    safe = lambda s: s.encode("ascii", errors="replace").decode("ascii")

    print(f"\n{'='*60}")
    print(f"SVAR:\n")
    print(safe(result.final_answer))
    print(f"\n{'='*60}")
    print(f"Routing: {result.routing.agents} (konfidens: {result.routing.confidence})")
    print(f"Tid: {result.total_duration_ms}ms")
    for r in result.agent_results:
        status = "OK" if r.success else f"FEIL: {r.error}"
        print(f"  {r.agent_name}: {r.duration_ms}ms - {status}")


if __name__ == "__main__":
    asyncio.run(_main())
