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

SYNTHESIS_PROMPT = """Du er HAPI Helseassistent — et multi-agent orkestreringsystem.
Du kombinerer svar fra spesialiserte agenter til ett sammenhengende svar paa norsk.

Brukerens spoersmaal: {query}

Foelgende agenter ble kalt via HAPI MCP Server (Helsedirektoratets API):
{agent_outputs}

REGLER:
1. BEVAR ALL PRESIS DATA: ATC-koder, ICD-10-koder, ICPC-2-koder, prosenttall,
   doseringsanbefalinger, preparatnavn og datoer skal gjengis ORDRETT fra agentsvarene.
   Aldri utelat en kode eller et tall som en agent oppga.

2. LOGISK REKKEFOEGLE: diagnose/kode -> behandling/retningslinje -> statistikk/NKI

3. IKKE BLAND DOMENER: Presenter aldri retningslinje-innhold som NKI-indikatorer
   eller kodeverk-data som behandlingsanbefalinger. Hold domenene separate.

4. KONFLIKTHAANDTERING: Hvis agenter gir motstridende info, presenter begge
   versjoner og paapek uoverensstemmelsen.

5. Behold faglig presisjon — ikke endre meningsinnhold. Ikke legg til egen kunnskap.

6. Hold svaret konsist men komplett. Bruk overskrifter for aa strukturere.

7. Avslutt ALLTID svaret med en kildelinje:
   "Kilde: Helsedirektoratets retningslinjer via HAPI (agenter: {agent_names})"

8. Du skal ALDRI si at du ikke har orkestrert agenter — det HAR du.
9. Du skal ALDRI si at du brukte web-soek — du brukte KUN HAPI MCP Server."""

SOURCE_FOOTER = "\n\n---\n*Kilde: Helsedirektoratets database via HAPI MCP Server (agent: {agent_name})*"


AGENT_TIMEOUT_S = 120  # Maks ventetid per agent-kall (sekunder)
AGENT_MAX_RETRIES = 2  # Antall forsoek per agent


async def _call_agent_once(
    project: AsyncProjectClient,
    agent_name: str,
    query: str,
) -> AgentResult:
    """Kall en Foundry-agent (ett forsoek)."""
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


async def call_agent(
    project: AsyncProjectClient,
    agent_name: str,
    query: str,
) -> AgentResult:
    """Kall en Foundry-agent med timeout og retry.

    - Timeout: Avbryt kall som tar over AGENT_TIMEOUT_S sekunder.
    - Retry: Proev paa nytt ved timeout eller nettverksfeil (opp til AGENT_MAX_RETRIES).
    """
    retryable_keywords = ("timed out", "timeout", "credential", "token",
                          "connection", "502", "503", "504")
    last_result = None

    for attempt in range(1, AGENT_MAX_RETRIES + 1):
        try:
            result = await asyncio.wait_for(
                _call_agent_once(project, agent_name, query),
                timeout=AGENT_TIMEOUT_S,
            )
            if result.success:
                return result

            # Agent returnerte feil — sjekk om den er retryable
            err_lower = (result.error or "").lower()
            if any(kw in err_lower for kw in retryable_keywords) and attempt < AGENT_MAX_RETRIES:
                logger.warning(f"  {agent_name}: retryable feil (forsoek {attempt}), proever igjen...")
                await asyncio.sleep(2)
                last_result = result
                continue
            return result

        except asyncio.TimeoutError:
            duration = int(AGENT_TIMEOUT_S * 1000)
            logger.warning(f"  {agent_name}: TIMEOUT etter {AGENT_TIMEOUT_S}s (forsoek {attempt})")
            last_result = AgentResult(
                agent_name=agent_name,
                output="",
                duration_ms=duration,
                success=False,
                error=f"Timeout etter {AGENT_TIMEOUT_S}s",
            )
            if attempt < AGENT_MAX_RETRIES:
                await asyncio.sleep(2)
                continue

        except Exception as e:
            duration = 0
            logger.error(f"  {agent_name}: uventet feil (forsoek {attempt}): {e}")
            last_result = AgentResult(
                agent_name=agent_name,
                output="",
                duration_ms=duration,
                success=False,
                error=str(e),
            )
            if attempt < AGENT_MAX_RETRIES:
                await asyncio.sleep(2)
                continue

    return last_result


def _agent_label(name: str) -> str:
    """Lag lesbart agentnavn."""
    labels = {
        "hapi-retningslinje-agent": "Retningslinje-agent",
        "hapi-kodeverk-agent": "Kodeverk-agent",
        "hapi-statistikk-agent": "Statistikk-agent",
    }
    return labels.get(name, name)


async def synthesize(
    project: AsyncProjectClient,
    query: str,
    results: list[AgentResult],
) -> str:
    """Kombiner agent-resultater til ett svar via LLM."""
    successful = [r for r in results if r.success and r.output]

    if not successful:
        return "Beklager, ingen av agentene klarte aa hente data for dette spoersmalet."

    # Hvis bare ett resultat, legg til kildemarkering
    if len(successful) == 1:
        r = successful[0]
        footer = SOURCE_FOOTER.format(agent_name=_agent_label(r.agent_name))
        return r.output + footer

    # Flere resultater — syntetiser via LLM
    agent_outputs = ""
    agent_names_list = []
    for r in successful:
        label = _agent_label(r.agent_name)
        agent_names_list.append(label)
        agent_outputs += f"\n--- {label} ---\n{r.output}\n"

    agent_names = ", ".join(agent_names_list)
    prompt = SYNTHESIS_PROMPT.format(
        query=query,
        agent_outputs=agent_outputs,
        agent_names=agent_names,
    )

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
            parts.append(f"## {_agent_label(r.agent_name)}\n{r.output}")
        footer = f"\n\n---\n*Kilde: Helsedirektoratets database via HAPI MCP Server*"
        return "\n\n".join(parts) + footer


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
