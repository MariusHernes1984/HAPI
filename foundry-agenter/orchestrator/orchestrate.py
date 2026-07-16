"""
Orchestrate — kaller sub-agenter parallelt og syntetiserer resultater.

Flyten:
  1. Router bestemmer hvilke agenter som trengs
  2. Agentene kalles parallelt via asyncio
  3. Resultater samles og sendes til syntese-steget
  4. Endelig svar returneres til bruker
"""

import asyncio
import json
import os
import re
import time
import logging
from dataclasses import dataclass, field
from urllib.parse import quote

from azure.identity.aio import DefaultAzureCredential as AsyncCredential
from azure.ai.projects.aio import AIProjectClient as AsyncProjectClient

from router import route, route_with_llm, RoutingDecision, KJERNEJOURNAL, INTERAKSJON
import kjernejournal
from llm_client import acomplete_text
from legemiddel_lexicon import (
    LEGEMIDDEL_ALIASES,
    GRUPPE_ALIASES,
    extract_mentioned_meds,
    extract_mentioned_meds_detailed,
)

logger = logging.getLogger(__name__)

# Modell brukt i syntese-steget. Default oppgradert til gpt-5.4
# (apr 2026): 11% raskere enn 5.3, samme kvalitetsskår på NDLA-evalen.
# Kan overstyres via SYNTH_MODEL env-var (f.eks. =gpt-5.5 for A/B-test).
# Se evals/rapporter/rapport-20260426-1111-synth-5.4.json for grunnlaget.
SYNTH_MODEL = os.environ.get("SYNTH_MODEL", "gpt-5.4")

# Hybrid-overstyring: bruk en annen modell når hapi-statistikk-agent er blant
# kildene. Bakgrunn: 30-spm A/B 2026-06-01 viste at claude-opus-4-8 eliminerte
# 1 HALLUSINERING i statistikk-svar mens gpt-5.4 var bedre på retningslinje-
# syntese. Hybriden ruter til Claude bare når statistikk er involvert (der
# hallusinering er mest skadelig), og beholder gpt-5.4 ellers. Tom = av.
# Se evals/rapporter/rapport-20260601-2208-hapi30-claude-opus-4-8.json
SYNTH_MODEL_STATISTIKK = os.environ.get("SYNTH_MODEL_STATISTIKK", "")

# Hot-reload av synthesis-prompt+modell fra Azure Table (admin-menyen).
# Cache i 60s for å unngå Table-kall ved hver request. Faller tilbake til
# konstantene over hvis Table ikke er aktivert eller raden ikke finnes.
_SYNTH_OVERRIDE_TTL_S = 60
_synth_override_cache: dict = {"expires_at": 0.0, "prompt": None, "model": None}


def _get_synth_override() -> tuple[str | None, str | None]:
    """Returner (prompt, model) fra Table — eller (None, None) hvis ingen override.

    Bruker enkel TTL-cache for å holde overhead nede; admin-endring er synlig
    innen 60s uten restart.
    """
    now = time.monotonic()
    if now < _synth_override_cache["expires_at"]:
        return _synth_override_cache["prompt"], _synth_override_cache["model"]

    prompt = None
    model = None
    try:
        import agent_config  # lokal import — agent_config kan være no-op uten storage
        if agent_config.is_enabled():
            endpoint = os.environ.get(
                "PROJECT_ENDPOINT",
                "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
            )
            cur = agent_config.get_store(endpoint).get_current(agent_config.SYNTHESIS_AGENT)
            if cur:
                prompt = cur.get("prompt") or None
                model = cur.get("model") or None
    except Exception as e:
        logger.warning(f"synthesis override read feilet: {e}")

    _synth_override_cache["expires_at"] = now + _SYNTH_OVERRIDE_TTL_S
    _synth_override_cache["prompt"] = prompt
    _synth_override_cache["model"] = model
    return prompt, model


@dataclass
class AgentResult:
    """Resultat fra en enkelt agent."""
    agent_name: str
    output: str
    duration_ms: int
    success: bool
    error: str | None = None
    # Valgfri strukturert nyttelast (brukes av lokale agenter, f.eks.
    # interaksjonsagentens FEST-data til frontend-kort). Aldri LLM-generert.
    data: dict | None = None


@dataclass
class OrchestrationResult:
    """Samlet resultat fra orkestreringen."""
    final_answer: str
    routing: RoutingDecision
    agent_results: list[AgentResult] = field(default_factory=list)
    total_duration_ms: int = 0
    interaksjonssjekk: bool = False


# --- Konfigurasjon ---

SYNTHESIS_PROMPT = """Du er HAPI Helseassistent — du formidler kunnskap fra Helsedirektoratet til helsepersonell.
Du har mottatt svar fra interne fagkilder og skal sette dem sammen til ETT sammenhengende svar på norsk.

Brukerens spørsmål: {query}
{patient_block}
Interne fagkilder (skal IKKE nevnes for brukeren):
{agent_outputs}

REGLER FOR SVARET TIL BRUKEREN:
1. BEVAR ALL PRESIS DATA: ATC-koder, ICD-10-koder, ICPC-2-koder, prosenttall,
   doseringsanbefalinger, preparatnavn og datoer skal gjengis ORDRETT fra fagkildene.
   Aldri utelat en kode eller et tall som ble oppgitt.

2. LOGISK REKKEFØLGE: diagnose/kode -> behandling/retningslinje -> statistikk/NKI

3. IKKE BLAND DOMENER: Presenter aldri retningslinje-innhold som NKI-indikatorer
   eller kodeverk-data som behandlingsanbefalinger. Hold domenene separate.

4. KONFLIKTHÅNDTERING: Hvis kildene gir motstridende info, presenter begge
   versjoner og påpek uoverensstemmelsen.

5. Behold faglig presisjon — ikke endre meningsinnhold. Ikke legg til egen kunnskap.

6. Hold svaret konsist men komplett. Bruk overskrifter etter TEMA (f.eks. "Diagnose",
   "Behandling", "Dosering", "Oppfølging") — IKKE etter agent eller kilde.

7. SØMLØS SAMMENFLETTING: Skriv som ÉN fagperson som svarer en kollega. Du skal:
   - ALDRI bruke overskrifter som "## Retningslinje-agent", "## Kodeverk-agent", "## Statistikk-agent"
   - ALDRI nevne at det er flere agenter, fagkilder eller "intern"-kilder
   - ALDRI nevne ordene "HAPI", "MCP", "agent", "verktøy", "API", "MCP-server"
   - Flett kunnskapen sømløst som om én klinisk fagperson skrev hele svaret

8. Avslutt med en kort, ren kildelinje (ingen tekniske detaljer).
   Hvis interaksjonsdata fra FEST/SLV er brukt:
   "Kilder: Helsedirektoratet · Interaksjonsdata fra FEST/Statens legemiddelverk"
   Ellers: "Kilde: Helsedirektoratet"

9. Du skal ALDRI si at du brukte web-søk.

10. PERSONALISERING VED AKTIV PASIENT: Hvis en pasientkontekst er oppgitt over,
    MÅ du vurdere pasientens diagnoser, faste medisiner og allergier opp mot
    spørsmålet. Hvis noe er relevant (f.eks. blodfortynnende + smertestillende,
    astma + NSAIDs, nyresvikt + dosering) — nevn det EKSPLISITT og advar om
    kontraindikasjoner/interaksjoner. Bruk pasientens navn eller "pasienten"
    naturlig i svaret. Ikke nevn ordet "kjernejournal" — bare fletter inn
    opplysningene som kliniske fakta. Hvis ingen pasientkontekst er oppgitt:
    svar generelt.

11. ÆRLIG OM DATAGAP I FEST: Hvis interaksjons-blokken sier "DATAGAP" eller
    "MERK (datagap)", MÅ du formidle dette ærlig — ikke skjul det og ikke
    erstatt det med vag tekst som "vurder interaksjoner". Navngi legemidlene
    konkret, og si at FEST ikke har en registrert interaksjon for paret,
    at dette ikke utelukker klinisk relevans, og at preparatomtale bør sjekkes.
    Hold det kort og kliniker-vennlig, ikke en punktliste av farmakologiske
    mekanismer. Et tomt FEST-oppslag skal formidles som et tomt FEST-oppslag."""

SOURCE_FOOTER = "\n\n---\n*Kilde: Helsedirektoratet*"
SOURCE_FOOTER_INTERAKSJON = "\n\n---\n*Kilder: Helsedirektoratet · Interaksjonsdata fra FEST/Statens legemiddelverk*"

INTERAKSJON_URL = "https://www.interaksjoner.no/Analyze.asp"

# LEGEMIDDEL_ALIASES/GRUPPE_ALIASES og ekstraksjon bor i legemiddel_lexicon.py
# (delt med router.py). Re-importert øverst; _extract_mentioned_meds beholdes
# som alias for bakoverkompatibilitet.
_extract_mentioned_meds = extract_mentioned_meds

FAREGRAD_LABELS = {
    4: "BØR IKKE KOMBINERES",
    3: "TA FORHOLDSREGLER",
    2: "MODERAT RISIKO",
    1: "LAV RISIKO",
}


async def _fetch_interaksjoner(medikament_navn: list[str]) -> dict | None:
    """Hent rå interaksjonsdata fra interaksjoner.no. None ved HTTP-/nettverksfeil."""
    søkeord = " ".join(medikament_navn)
    url = f"{INTERAKSJON_URL}?PreparatNavn={quote(søkeord)}"

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning(f"Interaksjoner.no returnerte {resp.status}")
                    return None
                return await resp.json(content_type=None)
    except ImportError:
        # Fallback: synkront kall via urllib
        import urllib.request
        import json as _json
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return _json.loads(resp.read())
        except Exception as e:
            logger.warning(f"Interaksjonssjekk feilet (urllib): {e}")
            return None
    except Exception as e:
        logger.warning(f"Interaksjonssjekk feilet: {e}")
        return None


async def _sjekk_interaksjoner(
    medikament_navn: list[str],
    asked_meds: list[str] | None = None,
) -> str | None:
    """
    Kall interaksjoner.no med en kombinert liste av medisiner
    (pasientens faste + nevnte i spørsmål/agent-output).

    Args:
        medikament_navn: Hele listen som sendes til API-et (faste + nye).
        asked_meds: Legemidler brukeren eksplisitt nevnte i spørsmålet.
                    Brukes til å rapportere datagap når API gjenkjenner dem
                    men ikke har noen registrert interaksjon.

    Returnerer en formatert tekstblokk for syntese-prompten, eller None
    hvis ingen data å rapportere.
    """
    if len(medikament_navn) < 2:
        return None  # Trenger minst 2 legemidler for interaksjonssjekk

    data = await _fetch_interaksjoner(medikament_navn)
    if data is None:
        return None
    return _format_interaksjoner(data, medikament_navn, asked_meds)


def _format_interaksjoner(
    data: dict,
    medikament_navn: list[str],
    asked_meds: list[str] | None = None,
) -> str | None:
    """Formater rå interaksjonsdata til tekstblokk for syntese-prompten.

    Skilt ut fra _sjekk_interaksjoner slik at interaksjonsagenten kan
    gjenbruke samme henting/formatering — output er uendret for /ask-stien.
    """
    interactions = data.get("Interactions") or []
    # Filtrer bort tomme
    interactions = [ix for ix in interactions if ix.get("ATC1")]

    # Bygg navn→ATC-map fra Recognized — ATC er uniform, navn varierer
    # mellom norsk (Recognized.Word) og engelsk (Interactions.Name1/Name2).
    recognized = data.get("Recognized") or []
    name_to_atc: dict[str, str] = {}
    for r in recognized:
        word = (r.get("Word") or "").lower()
        atc = (r.get("ATC") or "").replace(" ", "").upper()
        if word and atc:
            name_to_atc[word] = atc
    recognized_words = set(name_to_atc.keys())

    if not interactions:
        # Datagap: brukeren spurte eksplisitt om et legemiddel som API-et
        # gjenkjente, men FEST har ingen registrert interaksjon.
        if asked_meds:
            recognized_asked = [
                m for m in asked_meds if m.lower() in recognized_words
            ]
            if recognized_asked:
                linjer = [
                    "INTERAKSJONSSJEKK FRA FEST/SLV — DATAGAP:",
                    f"  Kombinasjon sjekket: {', '.join(medikament_navn)}.",
                    f"  Spurt legemiddel: {', '.join(recognized_asked)}.",
                    "  FEST har ingen registrert interaksjon for denne kombinasjonen.",
                    "  Dette utelukker ikke klinisk relevans — sjekk preparatomtale for",
                    "  CYP-effekt, QT-forlengelse og proteinbinding.",
                ]
                return "\n".join(linjer)
        return None

    linjer = [f"INTERAKSJONSDATA FRA FEST/SLV ({len(interactions)} funnet):"]
    for ix in interactions:
        level = ix.get("Level", 0)
        label = FAREGRAD_LABELS.get(level, f"Ukjent ({level})")
        linjer.append(
            f"  ⚠ {ix.get('Name1', '?')} ({ix.get('ATC1', '?')}) ↔ "
            f"{ix.get('Name2', '?')} ({ix.get('ATC2', '?')}): "
            f"Faregrad {level} ({label}) — {ix.get('Description', '')}"
        )
        if ix.get("Situation"):
            linjer.append(f"    Merk: {ix['Situation']}")

    # Hvis brukeren spurte om et spesifikt legemiddel, sjekk via ATC-kode
    # (ikke navn — FEST bruker norske navn i Recognized og engelske i
    # Interactions, så navn-sammenligning er upålitelig).
    if asked_meds:
        recognized_asked = [m for m in asked_meds if m.lower() in recognized_words]
        if recognized_asked:
            asked_atcs = {
                name_to_atc[m.lower()] for m in recognized_asked
                if m.lower() in name_to_atc
            }
            # Normaliser ATC-koder fra Interactions (fjerner mellomrom)
            def _norm_atc(code: str) -> str:
                return (code or "").replace(" ", "").upper()

            def _atc_match(ix_code: str) -> bool:
                # FEST bruker gruppe-ATC i Interactions (M01AE) men full kode i
                # Recognized (M01AE01) — prefiks-match begge veier, ellers får
                # gruppetreff en selvmotsigende "datagap"-merknad (jf. IX-010).
                n = _norm_atc(ix_code)
                if not n:
                    return False
                return any(a.startswith(n) or n.startswith(a) for a in asked_atcs)

            asked_in_ix = any(
                _atc_match(ix.get("ATC1", "")) or _atc_match(ix.get("ATC2", ""))
                for ix in interactions
            )
            if not asked_in_ix:
                linjer.append(
                    f"  MERK (datagap): FEST har ingen registrert interaksjon mellom "
                    f"{', '.join(recognized_asked)} og pasientens faste medisiner. "
                    f"Dette utelukker ikke klinisk relevans — sjekk preparatomtale."
                )

    return "\n".join(linjer)


# --- Lokal interaksjonsagent (pasientløs) ---
# Ingen Foundry-agent: kjøres i orchestratoren som KJERNEJOURNAL-mønsteret.
# Gjør "hapi-interaksjon-agent" (annonsert i /agents og UI) til en ekte,
# rutbar agent for spørsmål uten aktiv pasient.

def _parse_interaksjoner_structured(
    data: dict,
    checked: list[str],
    group_map: dict[str, str],
) -> dict:
    """Strukturert representasjon av FEST-responsen for frontend-kort.

    Alle verdier kopieres deterministisk fra API-responsen — aldri LLM.
    """
    interactions = [ix for ix in (data.get("Interactions") or []) if ix.get("ATC1")]
    recognized = [
        r.get("Word") for r in (data.get("Recognized") or []) if r.get("Word")
    ]
    cards = []
    for ix in interactions:
        level = ix.get("Level", 0)
        cards.append({
            "name1": ix.get("Name1", "?"),
            "atc1": (ix.get("ATC1") or "").replace(" ", "").upper(),
            "name2": ix.get("Name2", "?"),
            "atc2": (ix.get("ATC2") or "").replace(" ", "").upper(),
            "level": level,
            "label": FAREGRAD_LABELS.get(level, f"Ukjent ({level})"),
            "description": ix.get("Description", ""),
            "situation": ix.get("Situation", ""),
        })
    group_notes = [
        f"«{word}» er sjekket som {norm} — representativt for gruppen, "
        f"ikke nødvendigvis brukerens konkrete legemiddel"
        for norm, word in sorted(group_map.items())
    ]
    return {
        "interactions": cards,
        "checked": checked,
        "recognized": recognized,
        "datagap": not cards,
        "group_notes": group_notes,
        "unavailable": False,
    }


def _render_interaksjon_answer(structured: dict) -> str:
    """Deterministisk brukersvar når interaksjonsagenten er eneste kilde.

    Bygget utelukkende fra FEST-feltene — ingen LLM-tekst, ingen doseringsråd.
    """
    lines = ["## Interaksjonssjekk (FEST/Statens legemiddelverk)", ""]
    lines.append(f"Kombinasjon sjekket: {', '.join(structured['checked'])}.")

    if structured.get("unavailable"):
        lines += [
            "",
            "Interaksjonstjenesten var utilgjengelig — sjekken kunne ikke gjennomføres.",
            "Dette skal IKKE tolkes som at kombinasjonen er trygg. Sjekk preparatomtale,",
            "eller prøv igjen senere.",
        ]
    elif structured["interactions"]:
        for c in structured["interactions"]:
            lines.append("")
            lines.append(
                f"**{c['name1']} ({c['atc1']}) ↔ {c['name2']} ({c['atc2']}) — "
                f"Faregrad {c['level']}: {c['label']}**"
            )
            if c["description"]:
                lines.append(c["description"])
            if c["situation"]:
                lines.append(f"*Merk: {c['situation']}*")
    else:
        lines += [
            "",
            "FEST har ingen registrert interaksjon for denne kombinasjonen.",
            "Dette utelukker ikke klinisk relevans — sjekk preparatomtale for",
            "CYP-effekt, QT-forlengelse og proteinbinding.",
        ]

    for note in structured.get("group_notes", []):
        lines += ["", f"*{note}.*"]

    lines += [
        "",
        "Interaksjonssjekk er beslutningsstøtte og erstatter ikke klinisk vurdering.",
    ]
    return "\n".join(lines)


def _format_interaksjon_kort(structured: dict) -> str:
    """Marker-blokk med strukturert interaksjonsdata som frontend rendrer
    som fargekodede faregrad-kort. JSON-en er deterministisk fra FEST."""
    try:
        payload = json.dumps(structured, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""
    return "\n\n[INTERAKSJON-KORT]\n" + payload + "\n[/INTERAKSJON-KORT]"


async def call_interaksjon_agent(query: str) -> AgentResult:
    """Lokal interaksjonsagent: sjekk legemidlene nevnt i spørsmålet mot FEST/SLV."""
    start = time.monotonic()

    meds, group_map = extract_mentioned_meds_detailed([query])
    meds_sorted = sorted(meds)
    if len(meds_sorted) < 2:
        return AgentResult(
            agent_name=INTERAKSJON,
            output="",
            duration_ms=0,
            success=False,
            error="Færre enn 2 gjenkjente legemidler i spørsmålet",
        )

    data = await _fetch_interaksjoner(meds_sorted)
    duration = int((time.monotonic() - start) * 1000)

    if data is None:
        # Ærlig utilgjengelighet — aldri stille utelatt (kan ellers leses som "trygt")
        structured = {
            "interactions": [], "checked": meds_sorted, "recognized": [],
            "datagap": False, "group_notes": [], "unavailable": True,
        }
        output = (
            "INTERAKSJONSSJEKK UTILGJENGELIG: kilden (interaksjoner.no/FEST) svarte ikke. "
            "Dette skal IKKE tolkes som at kombinasjonen er trygg — sjekk preparatomtale "
            "eller prøv igjen senere."
        )
        logger.warning(f"  {INTERAKSJON}: kilde utilgjengelig ({duration}ms)")
        return AgentResult(
            agent_name=INTERAKSJON, output=output, duration_ms=duration,
            success=True, data=structured,
        )

    structured = _parse_interaksjoner_structured(data, meds_sorted, group_map)
    text = _format_interaksjoner(data, meds_sorted, asked_meds=list(meds))
    if text is None:
        # Ingen interaksjoner og ingen gjenkjente spurte legemidler i responsen
        text = (
            f"INTERAKSJONSSJEKK FRA FEST/SLV: ingen registrert interaksjon funnet for "
            f"kombinasjonen {', '.join(meds_sorted)}. Dette utelukker ikke klinisk "
            f"relevans — sjekk preparatomtale."
        )

    logger.info(
        f"  {INTERAKSJON}: {duration}ms, {len(structured['interactions'])} interaksjon(er)"
    )
    return AgentResult(
        agent_name=INTERAKSJON, output=text, duration_ms=duration,
        success=True, data=structured,
    )


def _extract_med_names(patient_output: str) -> list[str]:
    """Ekstraher medikamentnavn fra kjernejournal-output."""
    # Matcher navn før dose-parentes, f.eks. "Warfarin (Marevan) 2.5 mg"
    # og enkle navn som "Ramipril 5 mg"
    names = []
    meds_match = re.search(r"Faste medisiner:\s*(.+)", patient_output)
    if not meds_match:
        return names
    meds_line = meds_match.group(1)
    # Splitt på ";" og hent første ord(ene) fra hvert segment
    for segment in meds_line.split(";"):
        segment = segment.strip()
        if not segment:
            continue
        # Ta alt før dose (tall + mg/ml/etc) eller "(ATC"
        name = re.split(r"\s+\d+[\.,]?\d*\s*(?:mg|ml|µg|IE|g|mcg)|(?:\(ATC)", segment)[0].strip()
        # Fjern parentes-alias f.eks. "(Marevan)" — behold hovednavnet
        base = re.split(r"\s*\(", name)[0].strip()
        if base and base.lower() != "ingen":
            names.append(base)
    return names


AGENT_TIMEOUT_S = 120  # Maks ventetid per agent-kall (sekunder)
AGENT_MAX_RETRIES = 2  # Antall forsøk per agent


async def _call_agent_once(
    project: AsyncProjectClient,
    agent_name: str,
    query: str,
) -> AgentResult:
    """Kall en Foundry-agent (ett forsøk)."""
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

        # Rydd opp (ignorer feil ved sletting — conversation kan allerede være slettet)
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
    - Retry: Prøv på nytt ved timeout eller nettverksfeil (opp til AGENT_MAX_RETRIES).
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
                logger.warning(f"  {agent_name}: retryable feil (forsøk {attempt}), prøver igjen...")
                await asyncio.sleep(2)
                last_result = result
                continue
            return result

        except asyncio.TimeoutError:
            duration = int(AGENT_TIMEOUT_S * 1000)
            logger.warning(f"  {agent_name}: TIMEOUT etter {AGENT_TIMEOUT_S}s (forsøk {attempt})")
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
            logger.error(f"  {agent_name}: uventet feil (forsøk {attempt}): {e}")
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
        "hapi-kjernejournal-agent": "Kjernejournal-agent",
        "hapi-ndla-agent": "NDLA-agent",
    }
    return labels.get(name, name)


# --- Statistikk hallusinerings-guard ---

# Mønster som indikerer suspekte NKI-tall (prosenter med desimal, tertial-referanser).
# Matcher både "%" og utskrevet "prosent" — HAPI-30-regresjonen 16. juli viste at
# agenten skriver "59,9 prosent", som slapp forbi det rene %-mønsteret (EVAL-023).
_SUSPECT_NKI_PCT = re.compile(r'\d{1,3}[,.]\d\s*(?:%|prosent)', re.IGNORECASE)
_SUSPECT_TERTIAL = re.compile(r'\d\.\s*tertial\s+\d{4}', re.IGNORECASE)  # "2. tertial 2022"
_SUSPECT_YEAR_PCT = re.compile(r'(i|fra|per|år)\s+\d{4}.*?\d{1,3}[,.]\d\s*(?:%|prosent)', re.IGNORECASE)
_STATISTIKK_DISCLAIMER = (
    "\n\nFor oppdaterte tallverdier, se Helsedirektoratets statistikkbank "
    "(https://www.helsedirektoratet.no/statistikk)."
)


def _sanitize_statistikk(output: str) -> str:
    """Erstatt suspekte NKI-tallverdier fra statistikk-agent med trygg henvisning.

    Strategien:
    - Erstatter spesifikke prosent-tall (59,9%) med '[tall ikke verifisert]'
    - Erstatter tertial-referanser med generisk tekst
    - Legger til disclaimer om statistikkbanken
    """
    if not (_SUSPECT_NKI_PCT.search(output) or _SUSPECT_TERTIAL.search(output)):
        return output  # Ingen suspekte tall — returner uendret

    sanitized = output
    # Erstatt "59,9 %" -> "[se statistikkbanken for oppdaterte tall]"
    sanitized = _SUSPECT_NKI_PCT.sub(
        '[se statistikkbanken for oppdaterte tall]', sanitized
    )
    # Erstatt "2. tertial 2022" -> "[periode]"
    sanitized = _SUSPECT_TERTIAL.sub('[nyeste periode]', sanitized)

    # Legg til disclaimer hvis ikke allerede der
    if 'statistikkbank' not in sanitized.lower().split('for oppdaterte tall')[-1]:
        sanitized += _STATISTIKK_DISCLAIMER

    logger.info("  Statistikk-guard: suspekte NKI-tall sanitisert")
    return sanitized


# --- Felleskatalogen-bypass ---
# Felleskatalogen-agenten omslutter sitt verbatim-svar med disse markørene
# slik at vi enkelt kan plukke ut bare den verbatim-bevarte delen.
_FK_MARKER_RE = re.compile(
    r"\[VERBATIM-FELLESKATALOGEN\](.*?)\[/VERBATIM-FELLESKATALOGEN\]",
    re.DOTALL,
)


def _format_felleskatalogen_block(results: list[AgentResult]) -> str:
    """Pakk Felleskatalogen-output(s) som adskilt verbatim-blokk.

    Frontend rendrer dette som blockquote med tydelig kildemerking. Synthesis-
    laget får ALDRI se denne teksten — den blandes ikke med LLM-genererte ord.
    """
    if not results:
        return ""
    parts = []
    for r in results:
        if not r.success or not r.output:
            continue
        # Trekk ut bare det som er omsluttet av markørene; om markører mangler
        # bruker vi hele agent-output verbatim (agent-prompten påbyr markørene,
        # men vi er tilgivende).
        m = _FK_MARKER_RE.search(r.output)
        verbatim = m.group(1).strip() if m else r.output.strip()
        parts.append(verbatim)
    if not parts:
        return ""
    body = "\n\n---\n\n".join(parts)
    # Frontend bruker disse markørene til å gjenkjenne blokken og
    # rendre den som <div class="felleskatalogen-quote">.
    return (
        "\n\n[FELLESKATALOGEN-VERBATIM]\n"
        + body
        + "\n[/FELLESKATALOGEN-VERBATIM]"
    )


def _append_fk(answer: str, fk_block: str) -> str:
    """Legg til Felleskatalogen-blokken etter syntese-svaret."""
    if not fk_block:
        return answer
    return answer.rstrip() + "\n" + fk_block


async def synthesize(
    project: AsyncProjectClient,
    query: str,
    results: list[AgentResult],
    event_queue: "asyncio.Queue | None" = None,
) -> tuple[str, bool]:
    """Kombiner agent-resultater til ett svar via LLM.

    Returns:
        (answer, has_interaksjoner) — svartekst og om interaksjonssjekk ble brukt.
    """
    successful = [r for r in results if r.success and r.output]

    # Sanitiser statistikk-agent output FØR syntese
    for r in successful:
        if r.agent_name == "hapi-statistikk-agent":
            r.output = _sanitize_statistikk(r.output)

    # Separer Felleskatalogen-output FØR vi sjekker for tom successful-liste:
    # output er allerede verbatim sitat fra preparatomtalen og skal aldri
    # gjennom LLM-syntesen. Hentes ut og appendes til svaret nederst.
    felleskatalogen_results = [
        r for r in successful if r.agent_name == "hapi-felleskatalogen-agent"
    ]
    felleskatalogen_block = _format_felleskatalogen_block(felleskatalogen_results)
    successful = [r for r in successful if r.agent_name != "hapi-felleskatalogen-agent"]

    # Separer den lokale interaksjonsagenten (pasientløs FEST-sjekk).
    # Tekstblokken injiseres i syntesen; strukturert data blir frontend-kort.
    interaksjon_agent_results = [r for r in successful if r.agent_name == INTERAKSJON]
    successful = [r for r in successful if r.agent_name != INTERAKSJON]
    ix_agent = interaksjon_agent_results[0] if interaksjon_agent_results else None
    ix_card = _format_interaksjon_kort(ix_agent.data) if ix_agent and ix_agent.data else ""

    # Hvis Felleskatalogen/interaksjonsagenten var eneste vellykkete kilder,
    # returner deterministisk — ingen syntese, ingen LLM.
    if not successful:
        if ix_agent:
            answer = (
                _render_interaksjon_answer(ix_agent.data)
                if ix_agent.data else ix_agent.output
            )
            answer = answer + SOURCE_FOOTER_INTERAKSJON + ix_card
            return _append_fk(answer, felleskatalogen_block), True
        if felleskatalogen_block:
            return felleskatalogen_block, False
        return "Beklager, ingen av agentene klarte å hente data for dette spørsmålet.", False

    # Separer kjernejournal-output fra de andre fagkildene
    journal_results = [r for r in successful if r.agent_name == KJERNEJOURNAL]
    knowledge_results = [r for r in successful if r.agent_name != KJERNEJOURNAL]

    interaksjon_block = ""
    has_interaksjoner = False

    if journal_results:
        journal_output = journal_results[0].output

        # Automatisk interaksjonssjekk mot FEST/SLV
        # 1) Pasientens faste medisiner
        patient_meds = _extract_med_names(journal_output)
        # 2) Medisiner eksplisitt nevnt i brukerens spørsmål (ikke agent-output)
        asked_meds = _extract_mentioned_meds([query])
        # 3) Medisiner nevnt i fagkilde-output (kan gi ekstra kontekst)
        agent_mentioned = _extract_mentioned_meds([r.output for r in knowledge_results])
        # 4) Kombiner alle og dedupliser (case-insensitive)
        seen = {m.lower() for m in patient_meds}
        for m in asked_meds + agent_mentioned:
            if m.lower() not in seen:
                patient_meds.append(m)
                seen.add(m.lower())
        all_meds = patient_meds

        if all_meds:
            logger.info(f"  Interaksjonssjekk for {len(all_meds)} medisiner: {all_meds} (asked={asked_meds})")
            _emit(event_queue, {"type": "interaksjonssjekk"})
            ix_result = await _sjekk_interaksjoner(all_meds, asked_meds=asked_meds)
            if ix_result:
                interaksjon_block = f"\n{ix_result}\n"
                has_interaksjoner = True
                if "DATAGAP" in ix_result or "datagap" in ix_result:
                    logger.info(f"  FEST-datagap rapportert — injiserer i syntese")
                else:
                    logger.info(f"  Interaksjoner funnet — injiserer i syntese")

        patient_block = (
            "\nAKTIV PASIENT (bruk dette til å personalisere svaret):\n"
            + journal_output
            + interaksjon_block
            + "\nVIKTIG: Hvis pasientens medisiner, diagnoser eller allergier er "
            "relevant for spørsmålet, MÅ du nevne det eksplisitt og advare om "
            "kontraindikasjoner eller interaksjoner. Hvis INTERAKSJONSDATA er oppgitt "
            "over, bruk denne informasjonen — den er evidensbasert fra FEST/Statens "
            "legemiddelverk. Nevn faregrad og klinisk konsekvens i svaret.\n"
        )
    else:
        patient_block = ""

    # Interaksjonsagentens funn injiseres i syntesen sammen med fagkildene
    # (pasientløs sti — med aktiv pasient dekker inline-sjekken over dette).
    if ix_agent:
        patient_block += (
            "\nINTERAKSJONSSJEKK (FEST/SLV) for legemidlene i spørsmålet:\n"
            + ix_agent.output
            + "\nVIKTIG: Gjengi faregrad og klinisk konsekvens fra interaksjonsdataene "
            "eksakt. Hvis blokken viser DATAGAP eller at kilden var utilgjengelig, "
            "formidl dette ærlig (jf. regel 11).\n"
        )
        has_interaksjoner = True

    footer = SOURCE_FOOTER_INTERAKSJON if has_interaksjoner else SOURCE_FOOTER

    # Hvis ingen fagkunnskap-kilder (bare journal eller tom), fallback
    if not knowledge_results:
        if journal_results:
            return _append_fk(journal_results[0].output + footer + ix_card, felleskatalogen_block), has_interaksjoner
        if felleskatalogen_block:
            return felleskatalogen_block, False
        return "Beklager, ingen av agentene klarte å hente data for dette spørsmålet.", False

    # Hvis bare én fagkunnskap-kilde og ingen pasient/interaksjonsdata, bruk direkte
    if len(knowledge_results) == 1 and not journal_results and not ix_agent:
        return _append_fk(knowledge_results[0].output + footer, felleskatalogen_block), False

    # Syntetiser via LLM
    agent_outputs = ""
    agent_names_list = []
    for r in knowledge_results:
        label = _agent_label(r.agent_name)
        agent_names_list.append(label)
        agent_outputs += f"\n--- {label} ---\n{r.output}\n"

    agent_names = ", ".join(agent_names_list)

    # Hot-reload synthesis-prompt+modell fra admin-menyen (Azure Table).
    # Faller stille tilbake til konstantene hvis override mangler eller bryter.
    override_prompt, override_model = _get_synth_override()
    synth_template = override_prompt or SYNTHESIS_PROMPT
    synth_model = override_model or SYNTH_MODEL

    # Hybrid-overstyring: ruter til SYNTH_MODEL_STATISTIKK hvis statistikk-agenten
    # er blant kildene OG admin-overstyring ikke er satt. Hallusinering-risiko
    # ved tall er størst i statistikk-syntese; Claude er mer disiplinert der.
    if SYNTH_MODEL_STATISTIKK and not override_model:
        if any(r.agent_name == "hapi-statistikk-agent" for r in knowledge_results):
            logger.info(
                f"Hybrid: bytter syntese-modell til {SYNTH_MODEL_STATISTIKK} "
                f"(statistikk-agent involvert)"
            )
            synth_model = SYNTH_MODEL_STATISTIKK
    try:
        prompt = synth_template.format(
            query=query,
            patient_block=patient_block,
            agent_outputs=agent_outputs,
            agent_names=agent_names,
        )
    except (KeyError, IndexError, ValueError) as fmt_err:
        logger.warning(f"Synthesis override format-feil ({fmt_err}) — bruker default")
        synth_template = SYNTHESIS_PROMPT
        synth_model = SYNTH_MODEL
        prompt = synth_template.format(
            query=query,
            patient_block=patient_block,
            agent_outputs=agent_outputs,
            agent_names=agent_names,
        )

    _emit(event_queue, {"type": "synthesis_start", "model": synth_model})
    try:
        # acomplete_text ruter til Anthropic Messages API hvis synth_model starter med
        # "claude-", ellers Azure OpenAI Responses API som før.
        output_text = await acomplete_text(project, model=synth_model, prompt=prompt)
        return _append_fk(output_text + ix_card, felleskatalogen_block), has_interaksjoner
    except Exception as e:
        logger.error(f"Syntese feilet ({synth_model}): {e}")
        # Fallback: konkatener fagkunnskap-resultatene
        parts = [r.output for r in knowledge_results]
        return _append_fk("\n\n".join(parts) + SOURCE_FOOTER + ix_card, felleskatalogen_block), has_interaksjoner


def _emit(queue: "asyncio.Queue | None", event: dict) -> None:
    """Legg en SSE-hendelse på køen — no-op uten kø, så /ask-stien er uendret."""
    if queue is None:
        return
    try:
        queue.put_nowait(event)
    except Exception:
        pass


async def orchestrate(
    project_endpoint: str,
    query: str,
    use_llm_routing: bool = False,
    patient_id: str | None = None,
    event_queue: "asyncio.Queue | None" = None,
) -> OrchestrationResult:
    """
    Hovedfunksjon: rut, kall agenter parallelt, syntetiser.

    Args:
        project_endpoint: Azure AI Foundry prosjekt-URL
        query: Brukerens spørsmål
        use_llm_routing: Bruk LLM for routing ved lav konfidens
        event_queue: Valgfri kø for SSE-hendelser (routing/agent_start/
                     agent_result/synthesis_start). None = ingen emisjon.

    Returns:
        OrchestrationResult med endelig svar og metadata
    """
    start = time.monotonic()

    # Steg 1: Routing
    logger.info(f"Routing: '{query[:80]}...' (patient_id={patient_id})")
    decision = route(query, patient_id=patient_id)
    logger.info(f"  -> {decision.agents} (konfidens: {decision.confidence})")
    _emit(event_queue, {
        "type": "routing", "agents": decision.agents, "confidence": decision.confidence,
    })

    # Valgfritt: LLM-routing ved lav konfidens
    if use_llm_routing and decision.confidence == "lav":
        async with AsyncCredential() as cred:
            async with AsyncProjectClient(
                endpoint=project_endpoint, credential=cred
            ) as project:
                openai = project.get_openai_client()
                decision = route_with_llm(query, openai)
                logger.info(f"  LLM re-routing -> {decision.agents}")
                _emit(event_queue, {
                    "type": "routing", "agents": decision.agents,
                    "confidence": decision.confidence,
                })

    # Steg 2: Kall agenter parallelt
    logger.info(f"Kaller {len(decision.agents)} agent(er) parallelt...")

    async def _run_and_emit(coro, agent_name: str) -> AgentResult:
        _emit(event_queue, {"type": "agent_start", "agent": agent_name})
        res = await coro
        _emit(event_queue, {
            "type": "agent_result", "agent": agent_name,
            "success": res.success, "duration_ms": res.duration_ms,
        })
        return res

    async with AsyncCredential() as cred:
        async with AsyncProjectClient(
            endpoint=project_endpoint, credential=cred
        ) as project:
            tasks = []
            for agent_name in decision.agents:
                if agent_name == KJERNEJOURNAL:
                    # Lokalt oppslag — ikke Foundry-agent
                    coro = kjernejournal.call_kjernejournal_agent(patient_id)
                elif agent_name == INTERAKSJON:
                    # Lokal interaksjonsagent — FEST-sjekk uten Foundry
                    coro = call_interaksjon_agent(query)
                else:
                    coro = call_agent(project, agent_name, query)
                tasks.append(_run_and_emit(coro, agent_name))
            results = await asyncio.gather(*tasks)

            # Steg 3: Syntetiser
            logger.info("Syntetiserer svar...")
            final_answer, has_interaksjoner = await synthesize(
                project, query, list(results), event_queue=event_queue
            )

    total_ms = int((time.monotonic() - start) * 1000)

    return OrchestrationResult(
        final_answer=final_answer,
        routing=decision,
        agent_results=list(results),
        total_duration_ms=total_ms,
        interaksjonssjekk=has_interaksjoner,
    )


# --- CLI for testing ---

async def _main():
    """Kjør orkestrering fra kommandolinjen."""
    import os
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    endpoint = os.environ.get(
        "PROJECT_ENDPOINT",
        "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
    )

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Hva er anbefalt behandling for KOLS?"

    print(f"\nSpørsmål: {query}\n")

    result = await orchestrate(endpoint, query)

    print(f"\n{'='*60}")
    print(f"SVAR:\n")
    print(result.final_answer)
    print(f"\n{'='*60}")
    print(f"Routing: {result.routing.agents} (konfidens: {result.routing.confidence})")
    print(f"Tid: {result.total_duration_ms}ms")
    for r in result.agent_results:
        status = "OK" if r.success else f"FEIL: {r.error}"
        print(f"  {r.agent_name}: {r.duration_ms}ms - {status}")


if __name__ == "__main__":
    asyncio.run(_main())
