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


def _format_allergier(allergier: list) -> str:
    """Støtter både gammel (strenger) og ny (dict med agens/reaksjon/alvorlighet)."""
    parts = []
    for a in allergier:
        if isinstance(a, str):
            parts.append(a)
        elif isinstance(a, dict):
            txt = a.get("agens", "ukjent")
            details = []
            if a.get("reaksjon"):
                details.append(a["reaksjon"])
            if a.get("alvorlighet"):
                details.append(a["alvorlighet"])
            if details:
                txt += f" ({', '.join(details)})"
            parts.append(txt)
    return ", ".join(parts)


def format_patient_context(patient: dict) -> str:
    """
    Formater pasientdata til en kompakt tekst som synthesis-prompten kan bruke.
    Inkluderer alder, kjønn, diagnoser, medisiner, allergier, vitale parametere,
    prøvesvar, funksjonsnivå, kritisk info og pågående forløp — når disse finnes.
    Gamle mock-pasienter uten de nye feltene fungerer uendret.
    """
    kjonn_tekst = {"K": "kvinne", "M": "mann"}.get(patient.get("kjonn", ""), "")
    linjer = [
        f"Pasient {patient['id']}: {patient['navn']}, {patient['alder']} år {kjonn_tekst}",
    ]

    fastlege = patient.get("fastlege")
    if fastlege:
        linjer.append(f"Fastlege: {fastlege}")

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

    historiske = patient.get("historiske_medisiner") or []
    if historiske:
        h_tekst = "; ".join(
            f"{h['navn']} (seponert {h.get('seponert','?')}, grunn: {h.get('grunn','')})"
            for h in historiske
        )
        linjer.append(f"Seponerte medisiner: {h_tekst}")

    allergier = patient.get("allergier") or []
    if allergier:
        linjer.append(f"Allergier: {_format_allergier(allergier)}")

    kritisk = patient.get("kritisk_info") or {}
    if kritisk:
        biter = []
        if kritisk.get("cpr_status"):
            biter.append(f"HLR-status: {kritisk['cpr_status']}")
        for impl in kritisk.get("implantater", []):
            txt = impl.get("type", "implantat")
            if impl.get("aar"):
                txt += f" ({impl['aar']})"
            if impl.get("mr_sikker") is False:
                txt += " – IKKE MR-sikker"
            biter.append(txt)
        for kontrast in kritisk.get("kontrastreaksjoner", []) or kritisk.get("kontrastallergier", []):
            biter.append(f"Kontrast: {kontrast}")
        if kritisk.get("donor") is not None:
            biter.append("Organdonor: ja" if kritisk["donor"] else "Organdonor: nei")
        if biter:
            linjer.append(f"Kritisk info: {'; '.join(biter)}")

    malinger = patient.get("siste_malinger") or {}
    if malinger:
        biter = []
        for key, label in [("blodtrykk", "BT"), ("puls", "puls"), ("vekt", "vekt"),
                           ("hoyde", "høyde"), ("bmi", "BMI"), ("so2", "SpO2")]:
            if malinger.get(key) is not None:
                biter.append(f"{label} {malinger[key]}")
        if malinger.get("dato"):
            biter.append(f"(dato: {malinger['dato']})")
        if biter:
            linjer.append(f"Vitale parametere: {', '.join(biter)}")

    prover = patient.get("prover") or []
    if prover:
        p_tekst = "; ".join(
            f"{p['analyse']} {p['verdi']}{' '+p['enhet'] if p.get('enhet') else ''}"
            + (f" (ref {p['referanse']})" if p.get("referanse") else "")
            + (f" {p['dato']}" if p.get("dato") else "")
            for p in prover
        )
        linjer.append(f"Relevante prøvesvar: {p_tekst}")

    vaksiner = patient.get("vaksiner") or []
    if vaksiner:
        v_tekst = "; ".join(
            f"{v['type']} ({v.get('siste','?')})" for v in vaksiner
        )
        linjer.append(f"Vaksinehistorikk: {v_tekst}")

    funksjon = patient.get("funksjon") or {}
    if funksjon:
        biter = []
        for key in ["adl", "kognitiv_status", "mobilitet", "fallrisiko"]:
            if funksjon.get(key):
                biter.append(f"{key}: {funksjon[key]}")
        if biter:
            linjer.append(f"Funksjonsnivå: {'; '.join(biter)}")

    sosial = patient.get("sosial") or {}
    if sosial:
        biter = []
        if sosial.get("bor"):
            biter.append(f"bor: {sosial['bor']}")
        if sosial.get("pleietjenester"):
            biter.append(f"pleietjenester: {', '.join(sosial['pleietjenester'])}")
        if biter:
            linjer.append(f"Sosiale forhold: {'; '.join(biter)}")

    forlop = patient.get("aktive_forlop") or []
    if forlop:
        f_tekst = "; ".join(
            f"{f['type']}" + (f" (neste: {f['neste_kontroll']})" if f.get("neste_kontroll") else "")
            for f in forlop
        )
        linjer.append(f"Aktive forløp: {f_tekst}")

    innleggelser = patient.get("innleggelser") or []
    if innleggelser:
        i_tekst = "; ".join(
            f"{i.get('aarsak','?')} ({i.get('dato','?')})" for i in innleggelser[:3]
        )
        linjer.append(f"Siste innleggelser: {i_tekst}")

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
