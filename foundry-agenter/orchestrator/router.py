"""
Router — klassifiserer brukerens intensjon og bestemmer hvilke agenter som skal kalles.

Bruker keyword-matching som primær metode (rask, deterministisk).
Fallback til LLM-routing via orkestrator-agenten for tvetydige spørsmål.
"""

import re
from dataclasses import dataclass, field

# --- Agent-navn ---
RETNINGSLINJE = "hapi-retningslinje-agent"
KODEVERK = "hapi-kodeverk-agent"
STATISTIKK = "hapi-statistikk-agent"
KJERNEJOURNAL = "hapi-kjernejournal-agent"

# --- Routing-regler ---

KEYWORD_RULES: list[tuple[list[str], list[str]]] = [
    # (keywords, agents)
    # Retningslinje-triggere
    (
        ["behandling", "anbefaling", "retningslinje", "retningslinjer",
         "pakkeforlop", "pakkeforloep", "veileder", "faglig rad",
         "faglige raad", "rundskriv", "antibiotika", "dosering",
         "foerstevalg", "forstevalg", "forstevalgfor",
         "kols", "diabetes", "astma", "kreft", "depresjon", "angst",
         "hjertesvikt", "hypertensjon", "blodtrykk", "slag",
         "diagnose", "symptom", "symptomer", "sykdom",
         "hjelpe", "hva kan jeg", "hva bor jeg", "hva burde"],
        [RETNINGSLINJE],
    ),
    # Kodeverk-triggere
    (
        ["icd-10", "icd10", "icpc-2", "icpc2", "snomed", "atc",
         "takstkode", "kodeverk", "kode", "mapping", "mapper",
         "legemiddel", "virkestoff", "fest", "medisin"],
        [KODEVERK],
    ),
    # Statistikk-triggere
    (
        ["kvalitetsindikator", "nki", "statistikk", "indikator",
         "maaloppnaaelse", "trend", "nasjonalt maal", "rate",
         "andel", "prosent",
         # Vanlige NKI-domener
         "trombolyse", "ventetid", "overlevelse", "reinnleggelse",
         "hoftebrudd", "keisersnitt", "komplikasjonsrate",
         "30-dagers", "epikrisetid", "pasientsikkerhet"],
        [STATISTIKK],
    ),
]

# Diagnosekode-mønster som trigger kodeverk først
CODE_PATTERN = re.compile(
    r"\b([A-Z]\d{2}(?:\.\d{1,2})?)\b"  # ICD-10: J44, J44.1
    r"|\b(R\d{2})\b"                      # ICPC-2: R95
    r"|\b(\d{5,18})\b"                    # SNOMED CT: 13645005
    r"|\b([A-Z]\d{2}[A-Z]{2}\d{2})\b",   # ATC: J01CA04
    re.IGNORECASE,
)

# Sammensatte spørsmål — nøkkelfaser som trigger flere agenter
COMPOUND_TRIGGERS = [
    (["behandling", "statistikk"], [RETNINGSLINJE, STATISTIKK]),
    (["behandling", "kvalitet"], [RETNINGSLINJE, STATISTIKK]),
    (["retningslinje", "kode"], [RETNINGSLINJE, KODEVERK]),
    (["komplett", "oversikt"], [RETNINGSLINJE, KODEVERK, STATISTIKK]),
    (["alt om", ""], [RETNINGSLINJE, KODEVERK, STATISTIKK]),
    (["sykehus", "maal"], [STATISTIKK, RETNINGSLINJE]),
    (["over", "under", "nasjonalt"], [STATISTIKK]),
    # Legemiddel + behandling — trenger både kodeverk og retningslinje
    (["legemiddel", "retningslinje"], [KODEVERK, RETNINGSLINJE]),
    (["legemiddel", "behandling"], [KODEVERK, RETNINGSLINJE]),
    (["medisin", "retningslinje"], [KODEVERK, RETNINGSLINJE]),
]

# Legemiddelnavn som trigger kodeverk (alltid) + retningslinje (kun med behandlings-intent)
DRUG_NAMES = [
    "ozempic", "wegovy", "metformin", "eliquis", "xarelto",
    "paracetamol", "ibux", "ibuprofen", "voltaren",
]

# A2: NKI/statistikk-kontekst — når disse er tilstede, skal sykdomsord
# IKKE trigge retningslinje i keyword-steget
STATISTIKK_CONTEXT = [
    "kvalitetsindikator", "nki", "statistikk", "indikator",
    "maaloppnaaelse", "nasjonalt maal", "andel", "maalte verdi",
]

# A3: Kodeverk-kontekst — når disse er tilstede, skal sykdomsord
# IKKE trigge retningslinje i keyword-steget
KODEVERK_CONTEXT = [
    "icd-10", "icd10", "icpc-2", "icpc2", "atc-kode", "atc kode",
    "kodeverk", "mapping", "takstkode", "snomed",
]

# A4: Behandlings-intent — drug names trigger retningslinje KUN med disse
TREATMENT_INTENT_KEYWORDS = [
    "behandling", "anbefaling", "retningslinje", "bruk", "forskrive",
    "dosering", "indikasjon", "terapi", "hva sier", "hva anbefaler",
]


@dataclass
class RoutingDecision:
    """Resultat av routing-logikken."""
    agents: list[str] = field(default_factory=list)
    requires_code_lookup: bool = False
    detected_codes: list[str] = field(default_factory=list)
    confidence: str = "høy"  # høy, middels, lav
    reasoning: str = ""


def route(query: str, patient_id: str | None = None) -> RoutingDecision:
    """
    Klassifiser spørsmål og bestem hvilke agenter som trengs.

    Args:
        query: Brukerens spørsmål
        patient_id: Valgfri aktiv pasient-ID. Hvis satt, legges kjernejournal-
                    agenten alltid til i routing-listen.

    Returns:
        RoutingDecision med agenter, koder og konfidens.
    """
    q = query.lower()
    decision = RoutingDecision()

    # Steg 1: Sjekk for diagnosekoder i spørsmålet
    code_matches = CODE_PATTERN.findall(query)
    if code_matches:
        codes = [c for group in code_matches for c in group if c]
        decision.detected_codes = codes
        decision.requires_code_lookup = True
        decision.agents.append(KODEVERK)
        decision.reasoning += f"Fant kode(r): {codes}. "

    # Steg 1b: Sjekk om spørsmålet nevner et kjent legemiddelnavn
    # A4: Alltid kodeverk, men retningslinje KUN med behandlings-intent
    for drug in DRUG_NAMES:
        if drug in q:
            if KODEVERK not in decision.agents:
                decision.agents.append(KODEVERK)
            has_treatment_intent = any(kw in q for kw in TREATMENT_INTENT_KEYWORDS)
            if has_treatment_intent and RETNINGSLINJE not in decision.agents:
                decision.agents.append(RETNINGSLINJE)
                decision.reasoning += f"Legemiddel '{drug}' + behandlings-intent — kodeverk+retningslinje. "
            else:
                decision.reasoning += f"Legemiddel '{drug}' — kun kodeverk (ingen behandlings-intent). "
            break

    # Steg 2: Sjekk sammensatte triggere først
    for triggers, agents in COMPOUND_TRIGGERS:
        if all(t in q for t in triggers if t):
            for a in agents:
                if a not in decision.agents:
                    decision.agents.append(a)
            decision.reasoning += f"Sammensatt spørsmål ({', '.join(triggers)}). "
            break

    # Bestem kontekst for å unngå over-routing
    has_statistikk_context = any(kw in q for kw in STATISTIKK_CONTEXT)
    has_kodeverk_context = any(kw in q for kw in KODEVERK_CONTEXT)

    # Steg 3: Keyword-matching (med kontekst-undertrykkelse)
    if len(decision.agents) <= 1:
        for keywords, agents in KEYWORD_RULES:
            if any(kw in q for kw in keywords):
                for a in agents:
                    if a in decision.agents:
                        continue
                    # A2: Blokker retningslinje når statistikk-kontekst er tilstede
                    if a == RETNINGSLINJE and has_statistikk_context:
                        continue
                    # A3: Blokker retningslinje når kodeverk-kontekst er tilstede
                    if a == RETNINGSLINJE and has_kodeverk_context:
                        continue
                    # A5: "medisin" i behandlingskontekst skal ikke trigge kodeverk
                    if a == KODEVERK and "medisin" in q and not has_kodeverk_context:
                        # Sjekk om "medisin" er eneste kodeverk-trigger
                        kodeverk_kws = KEYWORD_RULES[1][0]  # kodeverk keywords
                        other_kodeverk_matches = [kw for kw in kodeverk_kws if kw in q and kw != "medisin"]
                        if not other_kodeverk_matches:
                            continue
                    decision.agents.append(a)

    # Steg 4: Fallback — hvis ingen treff, bruk retningslinje som default
    if not decision.agents:
        decision.agents = [RETNINGSLINJE]
        decision.confidence = "lav"
        decision.reasoning += "Ingen eksakt match — bruker retningslinje-agent som default. "

    # Steg 4b: Kjernejournal — trigges av aktiv pasient, ikke nøkkelord
    if patient_id:
        if KJERNEJOURNAL not in decision.agents:
            decision.agents.append(KJERNEJOURNAL)
        decision.reasoning += f"Aktiv pasient {patient_id} — kjernejournal-agent aktivert. "

    # Steg 5: Sett konfidens
    if len(decision.agents) == 1 and decision.confidence != "lav":
        decision.confidence = "høy"
        decision.reasoning += f"Entydig routing til {decision.agents[0]}."
    elif len(decision.agents) > 1:
        decision.confidence = "middels"
        decision.reasoning += f"Flere agenter: {', '.join(decision.agents)}."

    return decision


# --- LLM-routing (fallback) ---

LLM_ROUTING_PROMPT = """Du er en ruter. Klassifiser spørsmålet og returner KUN en JSON-liste med agenter.

Agenter:
- hapi-retningslinje-agent: behandling, anbefalinger, retningslinjer, pakkeforløp, antibiotika
- hapi-kodeverk-agent: kodeverk (ICD-10, ICPC-2, SNOMED, ATC), legemiddeldata, kode-mapping
- hapi-statistikk-agent: nasjonale kvalitetsindikatorer (NKI), statistikk, trender


Svar BARE med JSON: {"agents": ["agent-navn-1", "agent-navn-2"]}

Spørsmål: {query}"""


def route_with_llm(query: str, openai_client) -> RoutingDecision:
    """
    Bruk LLM for å rute tvetydige spørsmål.
    Kalles kun når keyword-routing har lav konfidens.
    """
    import json as json_mod

    response = openai_client.responses.create(
        model="gpt-5.3-chat",
        input=LLM_ROUTING_PROMPT.format(query=query),
    )

    decision = RoutingDecision(confidence="middels", reasoning="LLM-routing. ")

    try:
        text = response.output_text.strip()
        # Ekstraher JSON fra svaret
        start = text.index("{")
        end = text.rindex("}") + 1
        data = json_mod.loads(text[start:end])
        decision.agents = data.get("agents", [RETNINGSLINJE])
    except (ValueError, json_mod.JSONDecodeError):
        decision.agents = [RETNINGSLINJE]
        decision.confidence = "lav"
        decision.reasoning += "Kunne ikke parse LLM-svar, fallback til retningslinje."

    return decision
