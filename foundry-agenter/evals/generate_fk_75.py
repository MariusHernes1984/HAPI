"""Generer 75-spm Felleskatalogen-eval fra strukturert Python-data.

3 spm per preparat × 18 preparater = 54
+ 21 edge cases / negative tester / cross-cutting

Hvert spm har skal_inneholde-fraser plukket fra faktisk scrapet preparatomtale
(verifiser ved å kjøre: sqlite3 felleskatalogen.db).
"""
from __future__ import annotations
import json
from pathlib import Path

OUT = Path(__file__).parent / "eval-questions-felleskatalogen-75.json"


def q(qid, kategori, sporsmal, skal_inneholde, agent="hapi-felleskatalogen-agent",
      forventet_routing=None, skal_ikke=None, kilde="Felleskatalogen", tema=""):
    """Bygg ett spm-objekt."""
    return {
        "id": qid,
        "kategori": kategori,
        "agent": agent,
        "sporsmal": sporsmal,
        "tema": tema or kategori,
        "forventet_routing": forventet_routing or [agent],
        "faktasjekk": {
            "skal_inneholde": skal_inneholde,
            "skal_IKKE_inneholde": skal_ikke or [],
            "kilde_krav": kilde,
        },
    }


# Kort prefix-konvensjon: 2-bokstavsforkortelse
PREP_QS = []

# 1. Paracet (paracetamol, N02BE01)
PREP_QS += [
    q("FK-PA-01", "smerte-feber", "Vis dosering ifølge Felleskatalogen for paracetamol til barn",
      ["15 mg/kg", "Maks. døgndose er 75 mg/kg", "felleskatalogen.no"], tema="Paracet barn"),
    q("FK-PA-02", "smerte-feber", "Vis kontraindikasjoner for Paracet ifølge Felleskatalogen",
      ["Overfølsomhet", "felleskatalogen.no"], tema="Paracet kontra"),
    q("FK-PA-03", "smerte-feber", "Vis dosering ifølge Felleskatalogen for paracetamol til voksne over 50 kg",
      ["1-2 brusetabletter", "500 mg", "Maks. døgndose er 4 g", "felleskatalogen.no"], tema="Paracet voksne >50 kg"),
]

# 2. Eliquis (apixaban, B01AF02)
PREP_QS += [
    q("FK-EL-01", "antikoagulasjon", "Vis dosering for Eliquis ifølge Felleskatalogen ved atrieflimmer",
      ["5 mg 2", "ikke-valvulær atrieflimmer", "felleskatalogen.no"], tema="Apixaban NVAF"),
    q("FK-EL-02", "antikoagulasjon", "Hva er kontraindikasjoner for Eliquis ifølge Felleskatalogen?",
      ["Aktiv", "blødning", "felleskatalogen.no"], tema="Apixaban kontra"),
    q("FK-EL-03", "antikoagulasjon", "Vis dosereduksjon ved Eliquis ifølge Felleskatalogen for eldre",
      ["alder", "80", "kroppsvekt", "60 kg", "felleskatalogen.no"], tema="Apixaban dosereduksjon eldre"),
]

# 3. Marevan (warfarin, B01AA03)
PREP_QS += [
    q("FK-MA-01", "antikoagulasjon", "Hva sier preparatomtalen om initialdosering av Warfarin?",
      ["3 tabletter", "7,5 mg", "1. og 2. dag", "felleskatalogen.no"], tema="Marevan initial"),
    q("FK-MA-02", "antikoagulasjon", "Vis kontraindikasjoner for Marevan ifølge Felleskatalogen",
      ["Graviditet", "1. trimester", "felleskatalogen.no"], tema="Marevan kontra"),
    q("FK-MA-03", "antikoagulasjon", "Hva sier Felleskatalogen om Marevan ved demens og rusmisbruk?",
      ["rusmisbruk", "alkoholisme", "demens", "felleskatalogen.no"], tema="Marevan forsiktighet kognitiv"),
]

# 4. Xarelto (rivaroksaban, B01AF01)
PREP_QS += [
    q("FK-XA-01", "antikoagulasjon", "Vis dosering for Xarelto ifølge Felleskatalogen ved akutt koronarsyndrom",
      ["2,5 mg 2 ganger daglig", "ASA", "felleskatalogen.no"], tema="Xarelto ACS"),
    q("FK-XA-02", "antikoagulasjon", "Vis kontraindikasjoner for Xarelto ifølge Felleskatalogen",
      ["Overfølsomhet", "blødning", "felleskatalogen.no"], tema="Xarelto kontra"),
    q("FK-XA-03", "antikoagulasjon", "Vis forsiktighetsregler for Xarelto ifølge Felleskatalogen",
      ["blødning", "felleskatalogen.no"], tema="Xarelto forsiktighet"),
]

# 5. Pradaxa (dabigatran, B01AE07)
PREP_QS += [
    q("FK-PR-01", "antikoagulasjon", "SPC for dabigatran og nyrefunksjon",
      ["ClCR", "30 ml/minutt", "felleskatalogen.no"], tema="Pradaxa nyrefunksjon"),
    q("FK-PR-02", "antikoagulasjon", "Vis dosering for Pradaxa ifølge Felleskatalogen ved atrieflimmer",
      ["atrieflimmer", "felleskatalogen.no"], tema="Pradaxa NVAF"),
    q("FK-PR-03", "antikoagulasjon", "Vis kontraindikasjoner for Pradaxa ifølge Felleskatalogen",
      ["Overfølsomhet", "felleskatalogen.no"], tema="Pradaxa kontra"),
]

# 6. Lixiana (edoksaban, B01AF03)
PREP_QS += [
    q("FK-LI-01", "antikoagulasjon", "Vis dosering for Lixiana ifølge Felleskatalogen ved atrieflimmer",
      ["60 mg 1 gang daglig", "felleskatalogen.no"], tema="Lixiana NVAF"),
    q("FK-LI-02", "antikoagulasjon", "Vis kontraindikasjoner for Lixiana ifølge Felleskatalogen",
      ["aktiv blødning", "felleskatalogen.no"], tema="Lixiana kontra"),
    q("FK-LI-03", "antikoagulasjon", "Vis forsiktighetsregler for Lixiana ifølge Felleskatalogen",
      ["Blødningsrisiko", "felleskatalogen.no"], tema="Lixiana blødning"),
]

# 7. Klexane (enoksaparin, B01AB05)
PREP_QS += [
    q("FK-KL-01", "antikoagulasjon", "Vis dosering for Klexane ifølge Felleskatalogen for sporbarhet",
      ["preparatnavn og batchnummer", "felleskatalogen.no"], tema="Klexane sporbarhet"),
    q("FK-KL-02", "antikoagulasjon", "Vis kontraindikasjoner for Klexane ifølge Felleskatalogen",
      ["enoksaparin", "felleskatalogen.no"], tema="Klexane kontra"),
    q("FK-KL-03", "antikoagulasjon", "Hva sier Felleskatalogen om utskifting av enoksaparin og andre LMWH?",
      ["lavmolekylære hepariner", "felleskatalogen.no"], tema="Klexane LMWH-bytte"),
]

# 8. Selo-Zok (metoprolol, C07AB02)
PREP_QS += [
    q("FK-SZ-01", "kardiovaskulær", "Vis dosering ifølge Felleskatalogen for metoprolol ved hypertensjon",
      ["50-100 mg", "døgnet", "felleskatalogen.no"], tema="Selo-Zok hypertensjon"),
    q("FK-SZ-02", "kardiovaskulær", "Vis kontraindikasjoner for Selo-Zok ifølge Felleskatalogen",
      ["Overfølsomhet", "felleskatalogen.no"], tema="Selo-Zok kontra"),
    q("FK-SZ-03", "kardiovaskulær", "Slå opp dosering for Selo-Zok ved angina ifølge Felleskatalogen",
      ["mg", "felleskatalogen.no"], tema="Selo-Zok angina"),
]

# 9. Furix (furosemid, C03CA01)
PREP_QS += [
    q("FK-FU-01", "diuretika", "Slå opp dosering for Furix ved ødem",
      ["20-40 mg", "morgenen", "felleskatalogen.no"], tema="Furix ødem"),
    q("FK-FU-02", "diuretika", "Vis kontraindikasjoner for Furix ifølge Felleskatalogen",
      ["Overfølsomhet", "Anuri", "felleskatalogen.no"], tema="Furix kontra"),
    q("FK-FU-03", "diuretika", "Hva sier Felleskatalogen om Furix og sulfonamid-allergi?",
      ["sulfonamid", "felleskatalogen.no"], tema="Furix sulfonamid"),
]

# 10. Triatec (ramipril, C09AA05)
PREP_QS += [
    q("FK-TR-01", "kardiovaskulær", "Vis dosering for Triatec ifølge Felleskatalogen ved hypertensjon",
      ["Individuell", "felleskatalogen.no"], tema="Triatec hypertensjon"),
    q("FK-TR-02", "kardiovaskulær", "Vis kontraindikasjoner for Triatec ifølge Felleskatalogen",
      ["Overfølsomhet", "felleskatalogen.no"], tema="Triatec kontra"),
    q("FK-TR-03", "kardiovaskulær", "Hva sier preparatomtalen om Triatec ved nyresvikt?",
      ["nyre", "felleskatalogen.no"], tema="Triatec nyrefunksjon"),
]

# 11. Lipitor (atorvastatin, C10AA05)
PREP_QS += [
    q("FK-LP-01", "lipidsenkende", "Vis dosering for atorvastatin ifølge Felleskatalogen",
      ["individualiseres", "LDL", "felleskatalogen.no"], tema="Lipitor LDL"),
    q("FK-LP-02", "lipidsenkende", "Vis kontraindikasjoner for Lipitor ifølge Felleskatalogen",
      ["graviditet", "amming", "felleskatalogen.no"], tema="Lipitor kontra graviditet"),
    q("FK-LP-03", "lipidsenkende", "Vis forsiktighetsregler for Lipitor ifølge Felleskatalogen ved leverfunksjon",
      ["Leverfunksjonstest", "transaminase", "felleskatalogen.no"], tema="Lipitor lever"),
]

# 12. Jardiance (empagliflozin, A10BK03)
PREP_QS += [
    q("FK-JA-01", "diabetes", "Vis dosering for empagliflozin ifølge Felleskatalogen",
      ["10 mg 1 gang daglig", "eGFR", "felleskatalogen.no"], tema="Jardiance startdose"),
    q("FK-JA-02", "diabetes", "Vis kontraindikasjoner for Jardiance ifølge Felleskatalogen",
      ["Overfølsomhet", "felleskatalogen.no"], tema="Jardiance kontra"),
    q("FK-JA-03", "diabetes", "Hva sier preparatomtalen om Jardiance ved diabetes type 1?",
      ["diabetes", "type 1", "ketoacidose", "felleskatalogen.no"], tema="Jardiance DM1 fare"),
]

# 13. Aricept (donepezil, N06DA02)
PREP_QS += [
    q("FK-AR-01", "demens", "Vis dosering for donepezil ifølge Felleskatalogen",
      ["Alzheimers", "omsorgsperson", "felleskatalogen.no"], tema="Aricept Alzheimers"),
    q("FK-AR-02", "demens", "Vis kontraindikasjoner for Aricept ifølge Felleskatalogen",
      ["piperidinderivater", "felleskatalogen.no"], tema="Aricept piperidin"),
    q("FK-AR-03", "demens", "Hva sier Felleskatalogen om Aricept ved anestesi?",
      ["anestesi", "donepezil", "felleskatalogen.no"], tema="Aricept anestesi"),
]

# 14. Leponex (klozapin, N05AH02)
PREP_QS += [
    q("FK-LE-01", "psykiatri", "Verifisert dosering for klozapin ifølge Felleskatalogen",
      ["lavest mulig effektive dose", "titrering", "felleskatalogen.no"], tema="Leponex titrering"),
    q("FK-LE-02", "psykiatri", "Vis kontraindikasjoner for Leponex ifølge Felleskatalogen",
      ["agranulocytose", "blodkontroll", "felleskatalogen.no"], tema="Leponex agranulocytose"),
    q("FK-LE-03", "psykiatri", "Hva sier Felleskatalogen om blodmonitorering ved Leponex?",
      ["agranulocytose", "blodmonitorering", "felleskatalogen.no"], tema="Leponex blodmon"),
]

# 15. Zoloft (sertralin, N06AB06)
PREP_QS += [
    q("FK-ZO-01", "psykiatri", "Vis dosering for sertralin ifølge Felleskatalogen",
      ["50 mg daglig", "25 mg daglig", "Panikklidelse", "felleskatalogen.no"], tema="Zoloft startdose"),
    q("FK-ZO-02", "psykiatri", "Vis dosering for Zoloft ifølge Felleskatalogen ved depresjon",
      ["Depresjon", "50 mg", "felleskatalogen.no"], tema="Zoloft depresjon"),
    q("FK-ZO-03", "psykiatri", "Vis dosering for Zoloft ifølge Felleskatalogen ved panikklidelse",
      ["25 mg", "Panikklidelse", "felleskatalogen.no"], tema="Zoloft panikk"),
]

# 16. Fosamax (alendronat, M05BA04)
PREP_QS += [
    q("FK-FO-01", "osteoporose", "Vis dosering for Fosamax ifølge Felleskatalogen",
      ["70 mg", "1 gang i uken", "felleskatalogen.no"], tema="Fosamax ukentlig"),
    q("FK-FO-02", "osteoporose", "Vis kontraindikasjoner for Fosamax ifølge Felleskatalogen",
      ["Hypokalsemi", "felleskatalogen.no"], tema="Fosamax hypokalsemi"),
    q("FK-FO-03", "osteoporose", "Hva sier Felleskatalogen om Fosamax og oppreist stilling?",
      ["oppreist", "1 / 2 time", "felleskatalogen.no"], tema="Fosamax oppreist"),
]

# 17. OxyContin (oksykodon depot, N02AA05)
PREP_QS += [
    q("FK-OC-01", "smerte-opiat", "Vis dosering for OxyContin ifølge Felleskatalogen",
      ["12. time", "individuelt", "felleskatalogen.no"], tema="OxyContin depot"),
    q("FK-OC-02", "smerte-opiat", "Vis kontraindikasjoner for OxyContin ifølge Felleskatalogen",
      ["Paralytisk ileus", "felleskatalogen.no"], tema="OxyContin kontra"),
    q("FK-OC-03", "smerte-opiat", "Hva sier Felleskatalogen om respirasjonsdepresjon ved OxyContin?",
      ["Respirasjonsdepresjon", "felleskatalogen.no"], tema="OxyContin respdep"),
]

# 18. OxyNorm (oksykodon hurtig, N02AA05)
PREP_QS += [
    q("FK-ON-01", "smerte-opiat", "Vis dosering for OxyNorm ifølge Felleskatalogen som akuttbehandling",
      ["akuttbehandling", "depotpreparat", "felleskatalogen.no"], tema="OxyNorm akutt"),
    q("FK-ON-02", "smerte-opiat", "Vis kontraindikasjoner for OxyNorm ifølge Felleskatalogen",
      ["Overfølsomhet", "felleskatalogen.no"], tema="OxyNorm kontra"),
    q("FK-ON-03", "smerte-opiat", "Hva sier preparatomtalen om OxyNorm titrering?",
      ["titrer", "felleskatalogen.no"], tema="OxyNorm titrering"),
]

# --- Edge cases / cross-cutting (8 spm) ---
EDGE_QS = [
    q("FK-EDGE-01", "trigger-variant", "Preparatomtale for paracetamol",
      ["Paracet", "felleskatalogen.no"], tema="Trigger 'preparatomtale' alene"),
    q("FK-EDGE-02", "trigger-variant", "Hva er SPC for warfarin?",
      ["Marevan", "felleskatalogen.no"], tema="Trigger 'SPC'"),
    q("FK-EDGE-03", "trigger-variant", "Verifisert dosering Lipitor",
      ["atorvastatin", "felleskatalogen.no"], tema="Trigger 'verifisert'"),
    q("FK-EDGE-04", "cross-section", "Vis dosering OG kontraindikasjoner for Eliquis ifølge Felleskatalogen",
      ["5 mg 2", "Aktiv", "blødning", "felleskatalogen.no"], tema="Multi-seksjon"),
    q("FK-EDGE-05", "cross-section", "Vis Felleskatalogen-data for Marevan: dosering og forsiktighetsregler",
      ["3 tabletter", "felleskatalogen.no"], tema="Multi-seksjon"),
    q("FK-EDGE-06", "indikasjons-spesifikk", "Vis dosering for Eliquis ifølge Felleskatalogen ved DVT",
      ["10 mg", "DVT", "felleskatalogen.no"], tema="Apixaban DVT"),
    q("FK-EDGE-07", "indikasjons-spesifikk", "Vis dosering for Eliquis ifølge Felleskatalogen ved hofteprotesekirurgi",
      ["2,5 mg", "hofteprotese", "felleskatalogen.no"], tema="Apixaban hofteprotese"),
    q("FK-EDGE-08", "indikasjons-spesifikk", "Vis dosering for Lipitor ifølge Felleskatalogen ved hyperkolesterolemi",
      ["LDL", "felleskatalogen.no"], tema="Lipitor LDL-mål"),
]

# --- Negative tester (8 spm) ---
NEG_QS = [
    # Routing-negativer (uten trigger → ikke FK)
    q("FK-NEG-01", "negativ-routing", "Hvor mye paracetamol kan en voksen ta?",
      ["Helsedirektoratet"], skal_ikke=["[FELLESKATALOGEN-VERBATIM]"],
      agent="hapi-retningslinje-agent",
      forventet_routing=["hapi-retningslinje-agent"],
      kilde="Helsedirektoratet", tema="Uten trigger → retningslinje"),
    q("FK-NEG-02", "negativ-routing", "Hva er anbefalt behandling ved KOLS-eksaserbasjon?",
      ["KOLS"], skal_ikke=["[FELLESKATALOGEN-VERBATIM]"],
      agent="hapi-retningslinje-agent",
      forventet_routing=["hapi-retningslinje-agent"],
      kilde="Helsedirektoratet", tema="KOLS uten trigger"),
    q("FK-NEG-03", "negativ-routing", "Hva er ICD-10 koden for atrieflimmer?",
      ["I48"], skal_ikke=["[FELLESKATALOGEN-VERBATIM]"],
      agent="hapi-kodeverk-agent",
      forventet_routing=["hapi-kodeverk-agent"],
      kilde="Helsedirektoratet", tema="Kode-spm uten trigger"),
    # Data-negativer (trigger aktiv men preparat ikke i POC)
    q("FK-NEG-04", "negativ-data", "Vis dosering ifølge Felleskatalogen for Ozempic",
      ["ikke i"], skal_ikke=["Ozempic 0,25 mg", "Ozempic 1 mg"],
      tema="Ozempic ikke i POC"),
    q("FK-NEG-05", "negativ-data", "Vis dosering ifølge Felleskatalogen for Wegovy",
      ["ikke i"], skal_ikke=["Wegovy 0,25 mg", "semaglutid 2,4 mg"],
      tema="Wegovy ikke i POC"),
    q("FK-NEG-06", "negativ-data", "Preparatomtale for Tradolan",
      ["ikke i"], skal_ikke=["Tradolan 50 mg", "tramadol 50 mg"],
      tema="Tradolan ikke i POC"),
    q("FK-NEG-07", "negativ-data", "Vis dosering ifølge Felleskatalogen for Diovan",
      ["ikke i"], skal_ikke=["Diovan 80 mg", "valsartan"],
      tema="Diovan ikke i POC"),
    q("FK-NEG-08", "negativ-data", "SPC for Cipralex",
      ["ikke i"], skal_ikke=["Cipralex 10 mg", "escitalopram"],
      tema="Cipralex ikke i POC"),
]

# --- Robusthet (5 spm — typos, varianter) ---
ROBUST_QS = [
    q("FK-ROB-01", "robusthet", "vis dosering ifølge felleskatalogen for paracetamol",
      ["15 mg/kg", "felleskatalogen.no"], tema="Lowercase trigger"),
    q("FK-ROB-02", "robusthet", "Vis dosering for METOPROLOL ifølge Felleskatalogen",
      ["50-100 mg", "felleskatalogen.no"], tema="UPPERCASE virkestoff"),
    q("FK-ROB-03", "robusthet", "felleskatalogen dosering Marevan",
      ["3 tabletter", "felleskatalogen.no"], tema="Telegrafstil"),
    q("FK-ROB-04", "robusthet", "Hva sier preparatomtalen om Eliquis 5 mg dosering?",
      ["5 mg", "felleskatalogen.no"], tema="Med styrke i spm"),
    q("FK-ROB-05", "robusthet", "Vis Felleskatalogen-info for Lipitor",
      ["atorvastatin", "felleskatalogen.no"], tema="Generisk 'info' med trigger"),
]

ALL_QS = PREP_QS + EDGE_QS + NEG_QS + ROBUST_QS

assert len(PREP_QS) == 54, f"PREP_QS er {len(PREP_QS)}, forventet 54"
assert len(EDGE_QS) == 8
assert len(NEG_QS) == 8
assert len(ROBUST_QS) == 5
assert len(ALL_QS) == 75, f"Total er {len(ALL_QS)}, forventet 75"

doc = {
    "name": "HAPI Felleskatalogen-eval (utvidet)",
    "description": (
        "75 spm for stabilitetssjekk av Felleskatalogen-agenten. "
        "54 per-preparat (3 spm × 18 preparater), 8 edge cases, 8 negative "
        "tester, 5 robusthetsvarianter. Designet for å kjøres med --runs 3 "
        "(225 totale kjøringer)."
    ),
    "version": "2.0",
    "dato": "2026-04-27",
    "kilde_grunnlag": (
        "Felleskatalogen.no — POC-utvalg 18 flaggskip-legemidler (scrapet 2026-04-27). "
        "Negative-data-spm bruker preparater bevisst utenfor POC-utvalget."
    ),
    "evalueringskriterier": {
        "verbatim_krav": "Svaret skal inneholde ordrett tekst fra preparatomtalen.",
        "kilde_krav": "Hvert FK-svar skal ha 'felleskatalogen.no' URL.",
        "routing_krav": "Triggers MÅ aktivere FK-agenten. Spm uten trigger skal IKKE.",
        "stabilitet_krav": "Score skal være konsistent på tvers av kjøringer (3× repetisjon).",
    },
    "questions": ALL_QS,
}

OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Skrevet: {OUT}")
print(f"Antall spm: {len(ALL_QS)}")
from collections import Counter
print(f"Kategori-distribusjon: {dict(Counter(q['kategori'] for q in ALL_QS))}")
