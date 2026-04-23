"""
Orchestrate — kaller sub-agenter parallelt og syntetiserer resultater.

Flyten:
  1. Router bestemmer hvilke agenter som trengs
  2. Agentene kalles parallelt via asyncio
  3. Resultater samles og sendes til syntese-steget
  4. Endelig svar returneres til bruker
"""

import asyncio
import re
import time
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import quote

from azure.identity.aio import DefaultAzureCredential as AsyncCredential
from azure.ai.projects.aio import AIProjectClient as AsyncProjectClient

from router import route, route_with_llm, RoutingDecision, KJERNEJOURNAL
import kjernejournal

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

# Alias-ordbok: vanlige legemiddelnavn, merkenavn og gruppenavn → normalisert navn
# for interaksjoner.no.  Brukes til å fange medisiner nevnt i spørsmål og agent-output.
LEGEMIDDEL_ALIASES: dict[str, str] = {
    # NSAIDs
    "ibux": "Ibuprofen", "ibuprofen": "Ibuprofen", "brufen": "Ibuprofen",
    "voltaren": "Diklofenak", "diklofenak": "Diklofenak", "diclofenac": "Diklofenak",
    "naproxen": "Naproxen", "napren": "Naproxen",
    "piroksikam": "Piroksikam", "meloksikam": "Meloksikam",
    "celecoxib": "Celecoxib", "celebra": "Celecoxib",
    "indometacin": "Indometacin",
    "aspirin": "Acetylsalisylsyre", "acetylsalisylsyre": "Acetylsalisylsyre",
    "albyl": "Acetylsalisylsyre", "asa": "Acetylsalisylsyre",
    # Paracetamol
    "paracetamol": "Paracetamol", "paracet": "Paracetamol", "panodil": "Paracetamol",
    # Opioider
    "tramadol": "Tramadol", "kodein": "Kodein", "morfin": "Morfin",
    "oxycontin": "Oksykodon", "oksykodon": "Oksykodon", "oxynorm": "Oksykodon",
    "palexia": "Tapentadol", "tapentadol": "Tapentadol",
    # Antikoagulantia
    "warfarin": "Warfarin", "marevan": "Warfarin",
    "eliquis": "Apiksaban", "apiksaban": "Apiksaban", "apixaban": "Apiksaban",
    "xarelto": "Rivaroksaban", "rivaroksaban": "Rivaroksaban", "rivaroxaban": "Rivaroksaban",
    "pradaxa": "Dabigatran", "dabigatran": "Dabigatran",
    # Platehemmere
    "klopidogrel": "Klopidogrel", "plavix": "Klopidogrel",
    "dipyridamol": "Dipyridamol", "persantin": "Dipyridamol",
    # Betablokkere
    "metoprolol": "Metoprolol", "selo-zok": "Metoprolol",
    "atenolol": "Atenolol", "propranolol": "Propranolol",
    "bisoprolol": "Bisoprolol", "karvedilol": "Karvedilol",
    # ACE-hemmere / ARB
    "ramipril": "Ramipril", "enalapril": "Enalapril", "lisinopril": "Lisinopril",
    "losartan": "Losartan", "valsartan": "Valsartan", "candesartan": "Kandesartan",
    # Statiner
    "atorvastatin": "Atorvastatin", "simvastatin": "Simvastatin",
    "rosuvastatin": "Rosuvastatin",
    # Diabetes
    "metformin": "Metformin", "insulin": "Insulin",
    "ozempic": "Semaglutid", "semaglutid": "Semaglutid",
    "jardiance": "Empagliflozin", "empagliflozin": "Empagliflozin",
    "forxiga": "Dapagliflozin", "dapagliflozin": "Dapagliflozin",
    # Steroider
    "prednisolon": "Prednisolon", "prednison": "Prednisolon",
    "deksametason": "Deksametason", "kortison": "Prednisolon",
    "metylprednisolon": "Metylprednisolon",
    # Antibiotika
    "amoxicillin": "Amoxicillin", "penicillin": "Penicillin",
    "ciprofloxacin": "Ciprofloxacin", "doksycyklin": "Doksycyklin",
    "erytromycin": "Erytromycin", "metronidazol": "Metronidazol",
    "trimetoprim": "Trimetoprim", "klindamycin": "Klindamycin",
    "klaritromycin": "Klaritromycin", "klacid": "Klaritromycin",
    "azitromycin": "Azitromycin", "azitromax": "Azitromycin",
    "fenoksymetylpenicillin": "Fenoksymetylpenicillin", "apocillin": "Fenoksymetylpenicillin",
    # Antidepressiva / psykofarmaka
    "sertralin": "Sertralin", "escitalopram": "Escitalopram",
    "fluoksetin": "Fluoksetin", "venlafaksin": "Venlafaksin",
    "mirtazapin": "Mirtazapin", "duloksetin": "Duloksetin",
    # Antipsykotika (ofte involvert i QT/CYP-interaksjoner)
    "klozapin": "Klozapin", "leponex": "Klozapin",
    "olanzapin": "Olanzapin", "zyprexa": "Olanzapin",
    "quetiapin": "Quetiapin", "seroquel": "Quetiapin",
    "risperidon": "Risperidon", "risperdal": "Risperidon",
    "aripiprazol": "Aripiprazol", "abilify": "Aripiprazol",
    "haloperidol": "Haloperidol", "haldol": "Haloperidol",
    # Diuretika
    "furosemid": "Furosemid", "hydroklortiazid": "Hydroklortiazid",
    "spironolakton": "Spironolakton",
    # PPI
    "omeprazol": "Omeprazol", "pantoprazol": "Pantoprazol",
    "esomeprazol": "Esomeprazol", "lanzoprazol": "Lanzoprazol",
    # Annet
    "alendronat": "Alendronat", "levaxin": "Levotyroksin",
    "levotyroksin": "Levotyroksin",
    "ventoline": "Salbutamol", "salbutamol": "Salbutamol",
    "amlodipin": "Amlodipin", "nifedipin": "Nifedipin",
    "gabapentin": "Gabapentin", "pregabalin": "Pregabalin", "lyrica": "Pregabalin",
    "karbamazepin": "Karbamazepin", "fenytoin": "Fenytoin",
    "litium": "Litium", "valproat": "Valproat",
    "digoksin": "Digoksin", "amiodaron": "Amiodaron",
}

# Gruppenavn → representativt legemiddel (for å trigge interaksjonssjekk)
GRUPPE_ALIASES: dict[str, str] = {
    "nsaid": "Ibuprofen", "nsaids": "Ibuprofen",
    "betablokker": "Metoprolol", "betablokkere": "Metoprolol",
    "ace-hemmer": "Ramipril", "ace-hemmere": "Ramipril",
    "statin": "Atorvastatin", "statiner": "Atorvastatin",
    "kortikosteroid": "Prednisolon", "kortikosteroider": "Prednisolon",
    "ssri": "Sertralin", "snri": "Venlafaksin",
    "opioid": "Tramadol", "opioider": "Tramadol",
    "blodfortynnende": "Warfarin",
    "platehemmer": "Klopidogrel", "platehemmere": "Klopidogrel",
}


def _extract_mentioned_meds(texts: list[str]) -> list[str]:
    """
    Skann fritekst (spørsmål + agent-output) for kjente legemiddelnavn.

    Returnerer en deduplisert liste med normaliserte navn som kan brukes
    mot interaksjoner.no sammen med pasientens faste medisiner.
    """
    found: set[str] = set()

    combined = " ".join(texts).lower()
    # Fjern noen tegn som kan hindre matching
    combined = re.sub(r"[/\-–]", " ", combined)

    # Sjekk enkeltord mot alias-ordbøkene
    words = set(re.findall(r"[a-zæøå]+", combined))
    for word in words:
        if word in LEGEMIDDEL_ALIASES:
            found.add(LEGEMIDDEL_ALIASES[word])
        elif word in GRUPPE_ALIASES:
            found.add(GRUPPE_ALIASES[word])

    # Sjekk også flerords-aliaser (f.eks. "selo-zok" → "selo zok" etter normalisering)
    for alias, norm in LEGEMIDDEL_ALIASES.items():
        if " " in alias and alias in combined:
            found.add(norm)

    return list(found)


FAREGRAD_LABELS = {
    4: "BØR IKKE KOMBINERES",
    3: "TA FORHOLDSREGLER",
    2: "MODERAT RISIKO",
    1: "LAV RISIKO",
}


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

    søkeord = " ".join(medikament_navn)
    url = f"{INTERAKSJON_URL}?PreparatNavn={quote(søkeord)}"

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning(f"Interaksjoner.no returnerte {resp.status}")
                    return None
                data = await resp.json(content_type=None)
    except ImportError:
        # Fallback: synkront kall via urllib
        import urllib.request
        import json as _json
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read())
        except Exception as e:
            logger.warning(f"Interaksjonssjekk feilet (urllib): {e}")
            return None
    except Exception as e:
        logger.warning(f"Interaksjonssjekk feilet: {e}")
        return None

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
            asked_in_ix = any(
                (_norm_atc(ix.get("ATC1", "")) in asked_atcs or
                 _norm_atc(ix.get("ATC2", "")) in asked_atcs)
                for ix in interactions
            )
            if not asked_in_ix:
                linjer.append(
                    f"  MERK (datagap): FEST har ingen registrert interaksjon mellom "
                    f"{', '.join(recognized_asked)} og pasientens faste medisiner. "
                    f"Dette utelukker ikke klinisk relevans — sjekk preparatomtale."
                )

    return "\n".join(linjer)


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

# Mønster som indikerer suspekte NKI-tall (prosenter med desimal, tertial-referanser)
_SUSPECT_NKI_PCT = re.compile(r'\d{1,3}[,.]\d\s*%')      # f.eks. "59,9 %"
_SUSPECT_TERTIAL = re.compile(r'\d\.\s*tertial\s+\d{4}', re.IGNORECASE)  # "2. tertial 2022"
_SUSPECT_YEAR_PCT = re.compile(r'(i|fra|per|år)\s+\d{4}.*?\d{1,3}[,.]\d\s*%', re.IGNORECASE)
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


async def synthesize(
    project: AsyncProjectClient,
    query: str,
    results: list[AgentResult],
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

    if not successful:
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

    footer = SOURCE_FOOTER_INTERAKSJON if has_interaksjoner else SOURCE_FOOTER

    # Hvis ingen fagkunnskap-kilder (bare journal eller tom), fallback
    if not knowledge_results:
        if journal_results:
            return journal_results[0].output + footer, has_interaksjoner
        return "Beklager, ingen av agentene klarte å hente data for dette spørsmålet.", False

    # Hvis bare én fagkunnskap-kilde og ingen pasient, bruk direkte
    if len(knowledge_results) == 1 and not journal_results:
        return knowledge_results[0].output + footer, False

    # Syntetiser via LLM
    agent_outputs = ""
    agent_names_list = []
    for r in knowledge_results:
        label = _agent_label(r.agent_name)
        agent_names_list.append(label)
        agent_outputs += f"\n--- {label} ---\n{r.output}\n"

    agent_names = ", ".join(agent_names_list)
    prompt = SYNTHESIS_PROMPT.format(
        query=query,
        patient_block=patient_block,
        agent_outputs=agent_outputs,
        agent_names=agent_names,
    )

    try:
        openai = project.get_openai_client()
        response = await openai.responses.create(
            model="gpt-5.3-chat",
            input=prompt,
        )
        return response.output_text, has_interaksjoner
    except Exception as e:
        logger.error(f"Syntese feilet: {e}")
        # Fallback: konkatener fagkunnskap-resultatene
        parts = [r.output for r in knowledge_results]
        return "\n\n".join(parts) + SOURCE_FOOTER, has_interaksjoner


async def orchestrate(
    project_endpoint: str,
    query: str,
    use_llm_routing: bool = False,
    patient_id: str | None = None,
) -> OrchestrationResult:
    """
    Hovedfunksjon: rut, kall agenter parallelt, syntetiser.

    Args:
        project_endpoint: Azure AI Foundry prosjekt-URL
        query: Brukerens spørsmål
        use_llm_routing: Bruk LLM for routing ved lav konfidens

    Returns:
        OrchestrationResult med endelig svar og metadata
    """
    start = time.monotonic()

    # Steg 1: Routing
    logger.info(f"Routing: '{query[:80]}...' (patient_id={patient_id})")
    decision = route(query, patient_id=patient_id)
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
            tasks = []
            for agent_name in decision.agents:
                if agent_name == KJERNEJOURNAL:
                    # Lokalt oppslag — ikke Foundry-agent
                    tasks.append(kjernejournal.call_kjernejournal_agent(patient_id))
                else:
                    tasks.append(call_agent(project, agent_name, query))
            results = await asyncio.gather(*tasks)

            # Steg 3: Syntetiser
            logger.info("Syntetiserer svar...")
            final_answer, has_interaksjoner = await synthesize(project, query, list(results))

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
