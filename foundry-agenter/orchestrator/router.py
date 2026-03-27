"""
Router — klassifiserer brukerens intensjon og bestemmer hvilke agenter som skal kalles.

Bruker keyword-matching som primaer metode (rask, deterministisk).
Fallback til LLM-routing via orkestrator-agenten for tvetydige spoersmaal.
"""

import re
from dataclasses import dataclass, field

# --- Agent-navn ---
RETNINGSLINJE = "hapi-retningslinje-agent"
KODEVERK = "hapi-kodeverk-agent"
STATISTIKK = "hapi-statistikk-agent"

# --- Routing-regler ---

KEYWORD_RULES: list[tuple[list[str], list[str]]] = [
    # (keywords, agents)
    # Retningslinje-triggere
    (
        ["behandling", "anbefaling", "retningslinje", "retningslinjer",
         "pakkeforlop", "pakkeforloep", "veileder", "faglig rad",
         "faglige raad", "rundskriv", "antibiotika", "dosering",
         "foerstevalg", "forstevalg", "forstevalgfor"],
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
         "andel", "prosent"],
        [STATISTIKK],
    ),
]

# Diagnosekode-moenster som trigger kodeverk foerst
CODE_PATTERN = re.compile(
    r"\b([A-Z]\d{2}(?:\.\d{1,2})?)\b"  # ICD-10: J44, J44.1
    r"|\b(R\d{2})\b"                      # ICPC-2: R95
    r"|\b(\d{5,18})\b"                    # SNOMED CT: 13645005
    r"|\b([A-Z]\d{2}[A-Z]{2}\d{2})\b",   # ATC: J01CA04
    re.IGNORECASE,
)

# Sammensatte spoersmaal — noekkelfaser som trigger flere agenter
COMPOUND_TRIGGERS = [
    (["behandling", "statistikk"], [RETNINGSLINJE, STATISTIKK]),
    (["behandling", "kvalitet"], [RETNINGSLINJE, STATISTIKK]),
    (["retningslinje", "kode"], [RETNINGSLINJE, KODEVERK]),
    (["komplett", "oversikt"], [RETNINGSLINJE, KODEVERK, STATISTIKK]),
    (["alt om", ""], [RETNINGSLINJE, KODEVERK, STATISTIKK]),
]


@dataclass
class RoutingDecision:
    """Resultat av routing-logikken."""
    agents: list[str] = field(default_factory=list)
    requires_code_lookup: bool = False
    detected_codes: list[str] = field(default_factory=list)
    confidence: str = "hoey"  # hoey, middels, lav
    reasoning: str = ""


def route(query: str) -> RoutingDecision:
    """
    Klassifiser spoersmaal og bestem hvilke agenter som trengs.

    Returns:
        RoutingDecision med agenter, koder og konfidens.
    """
    q = query.lower()
    decision = RoutingDecision()

    # Steg 1: Sjekk for diagnosekoder i spoersmaalet
    code_matches = CODE_PATTERN.findall(query)
    if code_matches:
        codes = [c for group in code_matches for c in group if c]
        decision.detected_codes = codes
        decision.requires_code_lookup = True
        decision.agents.append(KODEVERK)
        decision.reasoning += f"Fant kode(r): {codes}. "

    # Steg 2: Sjekk sammensatte triggere foerst
    for triggers, agents in COMPOUND_TRIGGERS:
        if all(t in q for t in triggers if t):
            for a in agents:
                if a not in decision.agents:
                    decision.agents.append(a)
            decision.reasoning += f"Sammensatt spoersmaal ({', '.join(triggers)}). "
            break

    # Steg 3: Keyword-matching
    if len(decision.agents) <= 1:
        for keywords, agents in KEYWORD_RULES:
            if any(kw in q for kw in keywords):
                for a in agents:
                    if a not in decision.agents:
                        decision.agents.append(a)

    # Steg 4: Fallback — hvis ingen treff, bruk retningslinje som default
    if not decision.agents:
        decision.agents = [RETNINGSLINJE]
        decision.confidence = "lav"
        decision.reasoning += "Ingen eksakt match — bruker retningslinje-agent som default. "

    # Steg 5: Sett konfidens
    if len(decision.agents) == 1 and decision.confidence != "lav":
        decision.confidence = "hoey"
        decision.reasoning += f"Entydig routing til {decision.agents[0]}."
    elif len(decision.agents) > 1:
        decision.confidence = "middels"
        decision.reasoning += f"Flere agenter: {', '.join(decision.agents)}."

    return decision


# --- LLM-routing (fallback) ---

LLM_ROUTING_PROMPT = """Du er en ruter. Klassifiser spoersmalet og returner KUN en JSON-liste med agenter.

Agenter:
- hapi-retningslinje-agent: behandling, anbefalinger, retningslinjer, pakkeforloep, antibiotika
- hapi-kodeverk-agent: kodeverk (ICD-10, ICPC-2, SNOMED, ATC), legemiddeldata, kode-mapping
- hapi-statistikk-agent: nasjonale kvalitetsindikatorer (NKI), statistikk, trender

Svar BARE med JSON: {"agents": ["agent-navn-1", "agent-navn-2"]}

Spoersmaal: {query}"""


def route_with_llm(query: str, openai_client) -> RoutingDecision:
    """
    Bruk LLM for aa rute tvetydige spoersmaal.
    Kalles kun naar keyword-routing har lav konfidens.
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
