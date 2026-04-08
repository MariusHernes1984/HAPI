"""
Kjernejournal — lokal mock-agent som leverer pasientkontekst til orkestratoren.

Denne "agenten" er IKKE en Foundry-agent. Den slår opp i en lokal JSON-fil
og returnerer strukturerte pasientdata som AgentResult, slik at synthesis-steget
kan flette inn personalisert informasjon (f.eks. advare mot NSAIDs for pasienter
på blodfortynnende).
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Fil-lokasjon: lokalt repo har den i ../mock-data, container har den i /app/mock-data
_MODULE_DIR = Path(__file__).resolve().parent
_MOCK_CANDIDATES = [
    _MODULE_DIR.parent / "mock-data" / "pasienter.json",   # lokal repo
    _MODULE_DIR / "mock-data" / "pasienter.json",          # container (/app/mock-data)
]


def _find_mock_path() -> Path:
    for p in _MOCK_CANDIDATES:
        if p.exists():
            return p
    return _MOCK_CANDIDATES[0]  # for feilmelding

_PATIENTS_CACHE: dict[str, dict] | None = None


def load_patients() -> dict[str, dict]:
    """Les mock-pasienter fra JSON og cache i minnet."""
    global _PATIENTS_CACHE
    if _PATIENTS_CACHE is not None:
        return _PATIENTS_CACHE

    mock_path = _find_mock_path()
    if not mock_path.exists():
        logger.warning(f"Mock-pasientfil mangler. Sjekket: {[str(p) for p in _MOCK_CANDIDATES]}")
        _PATIENTS_CACHE = {}
        return _PATIENTS_CACHE

    try:
        data = json.loads(mock_path.read_text(encoding="utf-8"))
        patients = {p["id"]: p for p in data.get("pasienter", [])}
        _PATIENTS_CACHE = patients
        logger.info(f"Lastet {len(patients)} mock-pasienter fra {mock_path}")
        return patients
    except Exception as e:
        logger.error(f"Kunne ikke lese {mock_path}: {e}")
        _PATIENTS_CACHE = {}
        return _PATIENTS_CACHE


def get_patient(patient_id: str) -> Optional[dict]:
    """Hent full pasientdata for en gitt ID."""
    return load_patients().get(patient_id)


def list_patients_summary() -> list[dict]:
    """Kort liste til UI-dropdown: id, navn, alder, kort beskrivelse."""
    patients = load_patients()
    out = []
    for p in patients.values():
        diagnoser = p.get("diagnoser") or []
        meds = p.get("faste_medisiner") or []
        if diagnoser:
            beskrivelse = ", ".join(d["tekst"] for d in diagnoser[:2])
            if len(diagnoser) > 2:
                beskrivelse += f" (+{len(diagnoser) - 2})"
        elif meds:
            beskrivelse = "Ingen aktive diagnoser"
        else:
            beskrivelse = "Frisk"
        out.append({
            "id": p["id"],
            "navn": p["navn"],
            "alder": p["alder"],
            "kjonn": p["kjonn"],
            "beskrivelse": beskrivelse,
        })
    return out


def format_patient_context(patient: dict) -> str:
    """
    Formater pasientdata til en kompakt tekst som synthesis-prompten kan bruke.
    Inkluderer alder, kjønn, diagnoser, medisiner, allergier og merknader.
    """
    kjonn_tekst = {"K": "kvinne", "M": "mann"}.get(patient.get("kjonn", ""), "")
    linjer = [
        f"Pasient {patient['id']}: {patient['navn']}, {patient['alder']} år {kjonn_tekst}",
    ]

    diagnoser = patient.get("diagnoser") or []
    if diagnoser:
        d_tekst = "; ".join(
            f"{d['tekst']} ({d['kodeverk']}:{d['kode']})"
            for d in diagnoser
        )
        linjer.append(f"Diagnoser: {d_tekst}")
    else:
        linjer.append("Diagnoser: ingen registrert")

    meds = patient.get("faste_medisiner") or []
    if meds:
        m_tekst = "; ".join(
            f"{m['navn']} {m['dose']} (ATC {m['atc']}, {m['indikasjon']})"
            for m in meds
        )
        linjer.append(f"Faste medisiner: {m_tekst}")
    else:
        linjer.append("Faste medisiner: ingen")

    allergier = patient.get("allergier") or []
    if allergier:
        linjer.append(f"Allergier: {', '.join(allergier)}")

    merknader = patient.get("merknader")
    if merknader:
        linjer.append(f"Klinisk merknad: {merknader}")

    return "\n".join(linjer)


async def call_kjernejournal_agent(patient_id: str):
    """
    Wrapper som returnerer AgentResult (samme type som orchestrate.call_agent),
    slik at synthesis-pipelinen kan behandle kjernejournal-output på linje med
    de andre agentene.

    Importerer AgentResult lokalt for å unngå sirkulær import.
    """
    from orchestrate import AgentResult

    start = time.monotonic()
    patient = get_patient(patient_id)
    duration = int((time.monotonic() - start) * 1000)

    if not patient:
        return AgentResult(
            agent_name="hapi-kjernejournal-agent",
            output="",
            duration_ms=duration,
            success=False,
            error=f"Pasient {patient_id} finnes ikke",
        )

    context = format_patient_context(patient)
    output = (
        "AKTIV PASIENTKONTEKST fra kjernejournal:\n"
        f"{context}\n"
    )
    logger.info(f"  hapi-kjernejournal-agent: {duration}ms, pasient {patient_id}")
    return AgentResult(
        agent_name="hapi-kjernejournal-agent",
        output=output,
        duration_ms=duration,
        success=True,
    )
