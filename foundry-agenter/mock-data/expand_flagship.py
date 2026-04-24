"""
Utvider 10 utvalgte flaggskip-pasienter med full klinisk struktur
(kritisk_info, vitale, prøvesvar, vaksiner, funksjon, sosial, aktive forløp,
innleggelser, seponerte medisiner, strukturerte allergier).

De 65 andre pasientene beholdes uendret som "enkle" cases.

Kjør:
    python expand_flagship.py
"""
from __future__ import annotations
import json
from pathlib import Path

HERE = Path(__file__).parent
PASIENTER_JSON = HERE / "pasienter.json"

# --- Utvidede felt per pasient ---

EXPANSIONS: dict[str, dict] = {
    "P-035": {
        "fastlege": "Dr. Kjersti Tangen, Legesenter Oslo vest",
        "kritisk_info": {
            "cpr_status": "Full HLR",
            "implantater": [
                {"type": "Pacemaker (DDDR)", "aar": 2020, "mr_sikker": False}
            ],
            "kontrastreaksjoner": ["Forsiktighet — CKD3b"],
            "donor": False,
        },
        "siste_malinger": {
            "blodtrykk": "128/78",
            "puls": 72,
            "vekt": 78,
            "hoyde": 175,
            "bmi": 25.5,
            "so2": 94,
            "dato": "2026-03-15",
        },
        "prover": [
            {"analyse": "INR", "verdi": 2.4, "dato": "2026-04-12", "referanse": "2.0–3.0"},
            {"analyse": "eGFR", "verdi": 45, "enhet": "ml/min/1.73m²", "dato": "2026-03-10"},
            {"analyse": "HbA1c", "verdi": 55, "enhet": "mmol/mol", "dato": "2026-02-10", "referanse": "<53"},
            {"analyse": "NT-proBNP", "verdi": 1820, "enhet": "ng/L", "dato": "2026-03-10"},
            {"analyse": "Kalium", "verdi": 4.7, "enhet": "mmol/L", "dato": "2026-03-10"},
        ],
        "vaksiner": [
            {"type": "Influensa", "siste": "2025-10-08"},
            {"type": "Pneumokokk 23-valent", "siste": "2021-04"},
            {"type": "Covid-19 (Comirnaty)", "siste": "2024-09-15"},
        ],
        "funksjon": {
            "adl": "selvhjulpen",
            "kognitiv_status": "klar",
            "mobilitet": "selvstendig, gangstokk ved lengre turer",
            "fallrisiko": "middels",
        },
        "sosial": {
            "bor": "med ektefelle i enebolig",
            "pleietjenester": ["Hjemmesykepleie 1x/uke (medisindosett)"],
            "parorende": [{"relasjon": "Ektefelle", "kontakt": "primær"}],
        },
        "aktive_forlop": [
            {"type": "INR-kontroll", "frekvens": "månedlig", "neste_kontroll": "2026-05-12"},
            {"type": "Hjertesvikt-oppfølging", "neste_kontroll": "2026-06-20"},
            {"type": "Diabetes-oppfølging", "neste_kontroll": "2026-05-28"},
        ],
        "innleggelser": [
            {"dato": "2025-08-14", "aarsak": "Dekompensert hjertesvikt", "varighet_dager": 6},
        ],
        "historiske_medisiner": [
            {"navn": "Atenolol", "seponert": "2024-03", "grunn": "Akkumulasjon ved CKD – byttet til metoprolol"},
        ],
    },

    "P-036": {
        "fastlege": "Dr. Henrik Lund, Frogner legesenter",
        "kritisk_info": {
            "cpr_status": "Ikke HLR (forhåndsbestemmelse 2024)",
            "implantater": [],
            "donor": False,
        },
        "siste_malinger": {
            "blodtrykk": "142/82",
            "puls": 76,
            "vekt": 58,
            "hoyde": 160,
            "bmi": 22.7,
            "dato": "2026-03-28",
        },
        "prover": [
            {"analyse": "MMSE", "verdi": 19, "dato": "2026-02-05", "referanse": "24–30 normalt"},
            {"analyse": "Kalsium (korrigert)", "verdi": 2.38, "enhet": "mmol/L", "dato": "2026-02-05"},
            {"analyse": "Vitamin D (25-OH)", "verdi": 48, "enhet": "nmol/L", "dato": "2026-02-05"},
            {"analyse": "TSH", "verdi": 2.1, "enhet": "mIE/L", "dato": "2026-01-20"},
            {"analyse": "eGFR", "verdi": 58, "enhet": "ml/min/1.73m²", "dato": "2026-02-05"},
        ],
        "vaksiner": [
            {"type": "Influensa", "siste": "2025-10-12"},
            {"type": "Pneumokokk 23-valent", "siste": "2018-09"},
        ],
        "funksjon": {
            "adl": "trenger hjelp med stell, påkledning og måltider",
            "kognitiv_status": "moderat demens (MMSE 19)",
            "mobilitet": "rullator inne, rullestol ute",
            "fallrisiko": "høy",
        },
        "sosial": {
            "bor": "omsorgsbolig (Villa Victoria)",
            "pleietjenester": ["Hjemmesykepleie 4x/dag", "Trygghetsalarm", "Matombringing"],
            "parorende": [{"relasjon": "Datter", "kontakt": "primær"}, {"relasjon": "Sønn", "kontakt": "sekundær"}],
        },
        "aktive_forlop": [
            {"type": "Demens-oppfølging", "neste_kontroll": "2026-06-10"},
            {"type": "DEXA-scan (osteoporose)", "frekvens": "årlig", "neste_kontroll": "2026-09-01"},
        ],
        "innleggelser": [
            {"dato": "2025-11-03", "aarsak": "Fall hjemme, hodekontusjon", "varighet_dager": 2},
        ],
        "historiske_medisiner": [
            {"navn": "Zopiklon", "seponert": "2023-11", "grunn": "Økt fallrisiko hos eldre med demens (Beers-kriterier)"},
            {"navn": "Oksazepam", "seponert": "2024-06", "grunn": "Sedasjon + fallrisiko"},
        ],
    },

    "P-052": {
        "fastlege": "Dr. Hans Grindem, Majorstuen legesenter",
        "kritisk_info": {
            "cpr_status": "Full HLR (palliativ diskusjon pågår)",
            "implantater": [],
            "donor": False,
        },
        "siste_malinger": {
            "blodtrykk": "138/84",
            "puls": 82,
            "vekt": 82,
            "hoyde": 178,
            "bmi": 25.9,
            "dato": "2026-04-08",
        },
        "prover": [
            {"analyse": "PSA", "verdi": 85, "enhet": "µg/L", "dato": "2026-04-02", "referanse": "<4"},
            {"analyse": "Kalsium (korrigert)", "verdi": 2.55, "enhet": "mmol/L", "dato": "2026-04-02", "referanse": "2.15–2.55"},
            {"analyse": "Hemoglobin", "verdi": 11.2, "enhet": "g/dL", "dato": "2026-04-02", "referanse": "13.5–17.0"},
            {"analyse": "ALP", "verdi": 185, "enhet": "U/L", "dato": "2026-04-02", "referanse": "<115"},
            {"analyse": "eGFR", "verdi": 72, "enhet": "ml/min/1.73m²", "dato": "2026-04-02"},
        ],
        "vaksiner": [
            {"type": "Influensa", "siste": "2025-10-03"},
            {"type": "Pneumokokk 13-valent (PCV13)", "siste": "2023-11"},
            {"type": "Covid-19 (Spikevax)", "siste": "2025-09-22"},
        ],
        "funksjon": {
            "adl": "selvhjulpen, noe gangsmerter",
            "kognitiv_status": "klar",
            "mobilitet": "selvstendig, krykke ved behov",
            "fallrisiko": "middels (metastaser i skjelett)",
        },
        "sosial": {
            "bor": "med ektefelle",
            "pleietjenester": ["Hjemmesykepleie 3x/uke (smertemedisinering)"],
            "parorende": [{"relasjon": "Ektefelle", "kontakt": "primær"}],
        },
        "aktive_forlop": [
            {"type": "Pakkeforløp prostatakreft (stadium IV)", "startet": "2024-06-01", "neste_kontroll": "2026-05-14"},
            {"type": "Smerteklinikk", "frekvens": "hver 6. uke", "neste_kontroll": "2026-05-20"},
            {"type": "Palliativt team", "status": "aktiv siden 2025-11"},
        ],
        "innleggelser": [
            {"dato": "2024-10-22", "aarsak": "Patologisk fraktur lårhals (operert m/ margnagle)", "varighet_dager": 9},
        ],
        "historiske_medisiner": [
            {"navn": "Paracetamol 1g x 4", "seponert": "2024-09", "grunn": "Utilstrekkelig smertelindring – byttet til oksykodon depot"},
        ],
    },

    "P-064": {
        "fastlege": "Dr. Anne Lise Dahl, Grünerløkka legesenter",
        "kritisk_info": {
            "cpr_status": "Full HLR",
            "implantater": [
                {"type": "Proximal femur plate (glideskrue)", "aar": 2026, "mr_sikker": True}
            ],
            "donor": True,
        },
        "siste_malinger": {
            "blodtrykk": "140/85",
            "puls": 88,
            "vekt": 62,
            "hoyde": 163,
            "bmi": 23.3,
            "so2": 96,
            "dato": "2026-04-23",
        },
        "prover": [
            {"analyse": "Hemoglobin", "verdi": 10.8, "enhet": "g/dL", "dato": "2026-04-22", "referanse": "12.0–16.0"},
            {"analyse": "eGFR", "verdi": 68, "enhet": "ml/min/1.73m²", "dato": "2026-04-19"},
            {"analyse": "CRP", "verdi": 28, "enhet": "mg/L", "dato": "2026-04-22", "referanse": "<5"},
            {"analyse": "Anti-FXa (Enoksaparin)", "verdi": 0.35, "enhet": "IE/ml", "dato": "2026-04-21", "referanse": "profylakse 0.2–0.5"},
            {"analyse": "Kalsium", "verdi": 2.28, "enhet": "mmol/L", "dato": "2026-04-19"},
            {"analyse": "Vitamin D (25-OH)", "verdi": 42, "enhet": "nmol/L", "dato": "2026-04-19"},
        ],
        "vaksiner": [
            {"type": "Influensa", "siste": "2025-10-15"},
            {"type": "Pneumokokk 23-valent", "siste": "2019-11"},
            {"type": "Covid-19", "siste": "2024-10-02"},
        ],
        "funksjon": {
            "adl": "pre-op selvhjulpen; post-op trenger hjelp med stell og påkledning",
            "kognitiv_status": "klar",
            "mobilitet": "rullator m/ fysioterapeut, vektbelastning som tolerert",
            "fallrisiko": "høy (post-op, osteoporose)",
        },
        "sosial": {
            "bor": "alene i leilighet 2. etg uten heis",
            "pleietjenester": ["Ingen før innleggelse — planlagt hjemmesykepleie og trygghetsalarm ved utskrivning"],
            "parorende": [{"relasjon": "Datter", "kontakt": "primær"}, {"relasjon": "Sønn", "kontakt": "sekundær"}],
        },
        "aktive_forlop": [
            {"type": "Innleggelse hoftefraktur (operert dag 0)", "startet": "2026-04-18", "status": "postop dag 5"},
            {"type": "Rehabilitering (planlagt)", "neste_kontroll": "2026-04-28 (overføres til Cathinka Guldberg)"},
            {"type": "Osteoporose-utredning (DEXA + oppstart)", "neste_kontroll": "2026-05-25"},
        ],
        "innleggelser": [
            {"dato": "2026-04-18", "aarsak": "Hoftefraktur etter fall hjemme (operert innen 24t)", "varighet_dager": 10},
        ],
        "historiske_medisiner": [],
    },

    "P-005": {
        "allergier": [
            {
                "agens": "Penicillin",
                "reaksjon": "Urtikaria + mildt leppeødem",
                "alvorlighet": "moderat",
                "bekreftet": "klinisk anamnese",
                "dato": "2015-06",
            }
        ],
        "fastlege": "Dr. Torgeir Bru, Sandvika legesenter",
        "kritisk_info": {
            "cpr_status": "Full HLR",
            "implantater": [],
            "donor": True,
        },
        "siste_malinger": {
            "blodtrykk": "124/78",
            "puls": 76,
            "vekt": 68,
            "hoyde": 170,
            "bmi": 23.5,
            "so2": 98,
            "dato": "2026-03-01",
        },
        "prover": [
            {"analyse": "Anti-FXa (Dabigatran trough)", "verdi": 72, "enhet": "ng/ml", "dato": "2026-03-01", "referanse": "trough 28–215"},
            {"analyse": "eGFR", "verdi": 82, "enhet": "ml/min/1.73m²", "dato": "2026-03-01"},
            {"analyse": "Hemoglobin", "verdi": 13.2, "enhet": "g/dL", "dato": "2026-03-01"},
            {"analyse": "D-dimer", "verdi": 0.4, "enhet": "mg/L", "dato": "2026-02-05", "referanse": "<0.5"},
        ],
        "vaksiner": [
            {"type": "Influensa", "siste": "2025-10-20"},
            {"type": "Covid-19", "siste": "2024-11-01"},
        ],
        "funksjon": {
            "adl": "selvhjulpen",
            "kognitiv_status": "klar",
            "mobilitet": "fullt selvstendig",
            "fallrisiko": "lav",
        },
        "sosial": {
            "bor": "med ektefelle",
            "pleietjenester": [],
            "parorende": [{"relasjon": "Ektefelle", "kontakt": "primær"}],
        },
        "aktive_forlop": [
            {"type": "Antikoagulasjonskontroll (anti-FXa)", "frekvens": "hver 6. mnd", "neste_kontroll": "2026-09-05"},
            {"type": "LE-oppfølging (post-trombotisk vurdering)", "neste_kontroll": "2026-05-15"},
        ],
        "innleggelser": [
            {"dato": "2024-11-08", "aarsak": "Lungeemboli (bilateral subsegmental)", "varighet_dager": 6},
        ],
        "historiske_medisiner": [
            {"navn": "Enoksaparin 80 mg x 2 (s.c.)", "seponert": "2024-11-18", "grunn": "Overgang til DOAK etter stabilisering"},
        ],
    },

    "P-062": {
        "fastlege": "Dr. Mette Ryde, St. Hanshaugen legesenter",
        "kritisk_info": {
            "cpr_status": "Full HLR",
            "implantater": [],
            "donor": True,
        },
        "siste_malinger": {
            "blodtrykk": "118/72",
            "puls": 84,
            "vekt": 78,
            "hoyde": 168,
            "bmi": 27.6,
            "dato": "2026-04-15",
        },
        "prover": [
            {"analyse": "HbA1c", "verdi": 42, "enhet": "mmol/mol", "dato": "2026-04-10", "referanse": "mål <42 ved graviditet"},
            {"analyse": "Fastende glukose", "verdi": 5.4, "enhet": "mmol/L", "dato": "2026-04-15", "referanse": "<5.3 ved svangerskapsdiabetes"},
            {"analyse": "TSH", "verdi": 2.1, "enhet": "mIE/L", "dato": "2026-03-02", "referanse": "0.1–2.5 (gravid)"},
            {"analyse": "eGFR", "verdi": 105, "enhet": "ml/min/1.73m²", "dato": "2026-03-02"},
            {"analyse": "Hemoglobin", "verdi": 11.8, "enhet": "g/dL", "dato": "2026-04-10", "referanse": "11.0–14.0 (gravid)"},
        ],
        "vaksiner": [
            {"type": "dTp (gravid uke 27)", "siste": "2026-03-29"},
            {"type": "Influensa", "siste": "2025-10-10"},
            {"type": "Covid-19", "siste": "2024-09-05"},
        ],
        "funksjon": {
            "adl": "selvstendig",
            "kognitiv_status": "klar",
            "mobilitet": "fullt selvstendig",
            "fallrisiko": "lav",
        },
        "sosial": {
            "bor": "med samboer",
            "pleietjenester": [],
            "parorende": [{"relasjon": "Samboer", "kontakt": "primær"}],
        },
        "aktive_forlop": [
            {"type": "Svangerskapskontroll (uke 30, første barn)", "neste_kontroll": "2026-05-02"},
            {"type": "Jordmor-oppfølging", "frekvens": "hver 2. uke"},
            {"type": "Ultralyd uke 32", "neste_kontroll": "2026-05-09"},
            {"type": "Endokrinolog (svangerskapsdiabetes)", "neste_kontroll": "2026-05-20"},
        ],
        "innleggelser": [],
        "historiske_medisiner": [],
    },

    "P-068": {
        "fastlege": "Dr. Petter Hov, Ullevål legesenter + Barnediabetes-poliklinikk OUS",
        "kritisk_info": {
            "cpr_status": "Full HLR",
            "implantater": [
                {"type": "Kontinuerlig glukosemonitor (Dexcom G7)", "aar": 2025, "mr_sikker": False}
            ],
            "donor": False,
        },
        "siste_malinger": {
            "blodtrykk": "105/65",
            "puls": 88,
            "vekt": 42,
            "hoyde": 152,
            "bmi": 18.2,
            "dato": "2026-04-05",
        },
        "prover": [
            {"analyse": "HbA1c", "verdi": 68, "enhet": "mmol/mol", "dato": "2026-04-05", "referanse": "mål <53 (barn/ungdom)"},
            {"analyse": "Ketoner (kapillært)", "verdi": 0.3, "enhet": "mmol/L", "dato": "2026-04-05", "referanse": "<0.6"},
            {"analyse": "TSH", "verdi": 2.4, "enhet": "mIE/L", "dato": "2026-01-12"},
            {"analyse": "Anti-TPO", "verdi": 18, "enhet": "IE/ml", "dato": "2026-01-12", "referanse": "<34"},
            {"analyse": "Anti-transglutaminase (cøliaki-screening)", "verdi": 2, "enhet": "U/ml", "dato": "2026-01-12", "referanse": "<7"},
        ],
        "vaksiner": [
            {"type": "Barnevaksinasjonsprogram", "siste": "komplett t.o.m. 10-år"},
            {"type": "HPV (Gardasil 9, dose 2)", "siste": "2024-11-18"},
            {"type": "Influensa", "siste": "2025-10-28"},
        ],
        "funksjon": {
            "adl": "aldersvarende selvstendig",
            "kognitiv_status": "klar, 7. klasse",
            "mobilitet": "aktiv (fotball 2x/uke)",
            "fallrisiko": "lav",
        },
        "sosial": {
            "bor": "med mor, far og 2 søsken",
            "pleietjenester": ["Foreldre administrerer insulin og CGM"],
            "parorende": [{"relasjon": "Mor", "kontakt": "primær"}, {"relasjon": "Far", "kontakt": "sekundær"}],
        },
        "aktive_forlop": [
            {"type": "Barnediabetes-poliklinikk OUS", "frekvens": "hver 3. mnd", "neste_kontroll": "2026-06-14"},
            {"type": "Diabetessykepleier (CGM-opplæring)", "neste_kontroll": "2026-05-08"},
            {"type": "Årlig screening (cøliaki/tyreoidea)", "neste_kontroll": "2027-01-12"},
        ],
        "innleggelser": [
            {"dato": "2022-09-03", "aarsak": "Debut type 1 diabetes (DKA)", "varighet_dager": 5},
        ],
        "historiske_medisiner": [],
    },

    "P-057": {
        "fastlege": "Dr. Øystein Ness, Grønland legesenter + DPS Oslo Sør",
        "kritisk_info": {
            "cpr_status": "Full HLR",
            "implantater": [],
            "donor": False,
        },
        "siste_malinger": {
            "blodtrykk": "132/84",
            "puls": 88,
            "vekt": 95,
            "hoyde": 178,
            "bmi": 30.0,
            "qtc_ms": 448,
            "dato": "2026-04-03",
        },
        "prover": [
            {"analyse": "Klozapin (serum)", "verdi": 350, "enhet": "ng/ml", "dato": "2026-04-02", "referanse": "350–600"},
            {"analyse": "Nøytrofile granulocytter", "verdi": 3.8, "enhet": "×10⁹/L", "dato": "2026-04-02", "referanse": ">2.0 ved klozapin"},
            {"analyse": "Leukocytter", "verdi": 7.2, "enhet": "×10⁹/L", "dato": "2026-04-02"},
            {"analyse": "HbA1c", "verdi": 48, "enhet": "mmol/mol", "dato": "2026-03-10", "referanse": "<42 normalt"},
            {"analyse": "LDL-kolesterol", "verdi": 3.2, "enhet": "mmol/L", "dato": "2026-03-10", "referanse": "<3.0 (intervensjon)"},
            {"analyse": "QTc (EKG)", "verdi": 448, "enhet": "ms", "dato": "2026-04-02", "referanse": "<450 menn"},
        ],
        "vaksiner": [
            {"type": "Influensa", "siste": "2025-10-18"},
            {"type": "Covid-19", "siste": "2023-11-02"},
        ],
        "funksjon": {
            "adl": "selvstendig med støtte i egen leilighet",
            "kognitiv_status": "negative symptomer dominerer, stabilt",
            "mobilitet": "selvstendig",
            "fallrisiko": "lav",
        },
        "sosial": {
            "bor": "alene i kommunal leilighet (oppfølging fra bolig-team)",
            "pleietjenester": ["Dag-tilbud 3x/uke", "Psykiatrisk sykepleier ukentlig"],
            "parorende": [{"relasjon": "Mor", "kontakt": "primær"}],
        },
        "aktive_forlop": [
            {"type": "DPS-oppfølging (psykolog + lege)", "frekvens": "hver 2. uke", "neste_kontroll": "2026-05-02"},
            {"type": "Klozapin-overvåkning (blodprøver)", "frekvens": "månedlig"},
            {"type": "Somatisk oppfølging (vekt, lipider, diabetes)", "frekvens": "hver 3. mnd", "neste_kontroll": "2026-06-10"},
        ],
        "innleggelser": [
            {"dato": "2023-07-22", "aarsak": "Psykose-tilbakefall (akuttpsykiatri)", "varighet_dager": 21},
        ],
        "historiske_medisiner": [
            {"navn": "Olanzapin", "seponert": "2021-06", "grunn": "Utilstrekkelig effekt på psykotiske symptomer"},
            {"navn": "Risperidon", "seponert": "2020-11", "grunn": "Bivirkninger (ekstrapyramidale)"},
        ],
    },

    "P-070": {
        "fastlege": "Dr. Kjell Brenna, Tøyen legesenter + LAR-poliklinikk OUS Aker",
        "kritisk_info": {
            "cpr_status": "Full HLR",
            "implantater": [],
            "donor": False,
        },
        "siste_malinger": {
            "blodtrykk": "130/82",
            "puls": 74,
            "vekt": 74,
            "hoyde": 178,
            "bmi": 23.4,
            "qtc_ms": 465,
            "dato": "2026-03-18",
        },
        "prover": [
            {"analyse": "Metadon (serum trough)", "verdi": 285, "enhet": "ng/ml", "dato": "2026-03-18", "referanse": "trough 150–400"},
            {"analyse": "ALAT", "verdi": 32, "enhet": "U/L", "dato": "2026-03-01", "referanse": "<50"},
            {"analyse": "Hep C RNA", "verdi": "ikke-påvisbart", "dato": "2026-02-15", "referanse": "SVR12 oppnådd 2023-04"},
            {"analyse": "HIV-test", "verdi": "negativ", "dato": "2026-02-15"},
            {"analyse": "QTc (EKG)", "verdi": 465, "enhet": "ms", "dato": "2026-03-18", "referanse": "<450 menn (grenseverdi)"},
            {"analyse": "Anti-HBs", "verdi": 145, "enhet": "mIE/ml", "dato": "2025-09-01", "referanse": ">10 = immunitet"},
        ],
        "vaksiner": [
            {"type": "Hepatitt A+B (Twinrix, fullvaksinert)", "siste": "2022-08"},
            {"type": "Tetanus/difteri", "siste": "2023-11"},
            {"type": "Influensa", "siste": "2025-10-25"},
        ],
        "funksjon": {
            "adl": "selvstendig",
            "kognitiv_status": "klar",
            "mobilitet": "selvstendig, arbeider deltid (vaktmester)",
            "fallrisiko": "lav",
        },
        "sosial": {
            "bor": "med samboer (også i LAR)",
            "pleietjenester": ["LAR-poliklinikk månedlig", "Sosialkurator ved NAV"],
            "parorende": [{"relasjon": "Samboer", "kontakt": "primær"}, {"relasjon": "Sønn (13 år, fra tidl. forhold)"}],
        },
        "aktive_forlop": [
            {"type": "LAR-oppfølging", "frekvens": "månedlig", "neste_kontroll": "2026-05-10"},
            {"type": "Hep C-oppfølging (SVR-bekreftet)", "frekvens": "årlig", "neste_kontroll": "2027-02-15"},
            {"type": "EKG-kontroll (QTc-overvåkning)", "frekvens": "hver 6. mnd", "neste_kontroll": "2026-09-18"},
        ],
        "innleggelser": [
            {"dato": "2019-06-02", "aarsak": "Heroin-overdose (før LAR)", "varighet_dager": 3},
        ],
        "historiske_medisiner": [
            {"navn": "Buprenorfin/nalokson (Subutex)", "seponert": "2020-04", "grunn": "Byttet til metadon pga utilstrekkelig mettelse"},
            {"navn": "Sofosbuvir/velpatasvir (Epclusa)", "seponert": "2023-04", "grunn": "Hep C-kur fullført (SVR12 oppnådd)"},
        ],
    },

    "P-054": {
        "fastlege": "Dr. Helge Vik, Bærum sykehus onkologi + fastlege Dr. Anita Fossum",
        "kritisk_info": {
            "cpr_status": "Palliativ diskusjon pågår (sannsynlig HLR-minus snart)",
            "implantater": [
                {"type": "Port-a-cath (v. subclavia dxt.)", "aar": 2025, "mr_sikker": True}
            ],
            "donor": False,
        },
        "siste_malinger": {
            "blodtrykk": "132/78",
            "puls": 92,
            "vekt": 55,
            "hoyde": 165,
            "bmi": 20.2,
            "so2": 91,
            "dato": "2026-04-12",
        },
        "prover": [
            {"analyse": "PD-L1 ekspresjon (TPS)", "verdi": "90%", "dato": "2025-10-02", "referanse": "høy ≥50%"},
            {"analyse": "LDH", "verdi": 345, "enhet": "U/L", "dato": "2026-04-08", "referanse": "<245"},
            {"analyse": "CRP", "verdi": 32, "enhet": "mg/L", "dato": "2026-04-08", "referanse": "<5"},
            {"analyse": "Hemoglobin", "verdi": 10.5, "enhet": "g/dL", "dato": "2026-04-08"},
            {"analyse": "Nøytrofile granulocytter", "verdi": 2.2, "enhet": "×10⁹/L", "dato": "2026-04-08"},
            {"analyse": "eGFR", "verdi": 68, "enhet": "ml/min/1.73m²", "dato": "2026-04-08"},
            {"analyse": "Kortisol morgen", "verdi": 380, "enhet": "nmol/L", "dato": "2026-04-08", "referanse": "140–700 (immunterapi-monitorering)"},
        ],
        "vaksiner": [
            {"type": "Influensa", "siste": "2025-10-05"},
            {"type": "Pneumokokk 13+23", "siste": "2023-09"},
            {"type": "Covid-19", "siste": "2025-09-18"},
        ],
        "funksjon": {
            "adl": "selvhjulpen inne, bruker oksygen ved anstrengelse",
            "kognitiv_status": "klar",
            "mobilitet": "gangnedsatt, bruker rullator ute",
            "fallrisiko": "middels",
        },
        "sosial": {
            "bor": "med ektefelle i leilighet",
            "pleietjenester": ["Hjemmesykepleie 2x/dag", "Kreftsykepleier ukentlig", "Oksygentilskudd hjemme"],
            "parorende": [{"relasjon": "Ektefelle", "kontakt": "primær"}, {"relasjon": "Datter", "kontakt": "sekundær"}],
        },
        "aktive_forlop": [
            {"type": "Pakkeforløp lungekreft (stadium IV, metastatisk)", "startet": "2025-10-15"},
            {"type": "Onkologisk poliklinikk (immunterapi)", "frekvens": "hver 3. uke", "neste_kontroll": "2026-05-05"},
            {"type": "Palliativ poliklinikk", "frekvens": "hver 4. uke", "neste_kontroll": "2026-05-12"},
            {"type": "Lungerehabilitering KOLS", "status": "pausert under aktiv onkologi"},
        ],
        "innleggelser": [
            {"dato": "2026-01-08", "aarsak": "Febril nøytropeni (under immunterapi-syklus 4)", "varighet_dager": 5},
            {"dato": "2025-10-02", "aarsak": "Diagnostisk — bronchoskopi + stadieinndeling", "varighet_dager": 2},
        ],
        "historiske_medisiner": [
            {"navn": "Cisplatin + pemetreksed", "seponert": "2025-12", "grunn": "Progresjon etter 4 sykluser – byttet til immunterapi"},
        ],
    },
}


def deep_merge(base: dict, patch: dict) -> dict:
    """Grundt merge: patch-felt overskriver base-felt."""
    out = dict(base)
    for k, v in patch.items():
        out[k] = v
    return out


def main() -> None:
    with open(PASIENTER_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    pasienter = data["pasienter"]
    updated = []
    count = 0
    for p in pasienter:
        pid = p["id"]
        if pid in EXPANSIONS:
            merged = deep_merge(p, EXPANSIONS[pid])
            updated.append(merged)
            count += 1
            print(f"  ✓ Utvidet {pid}: {p['navn']} ({len(EXPANSIONS[pid])} nye felt)")
        else:
            updated.append(p)

    data["pasienter"] = updated

    with open(PASIENTER_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\n{count}/{len(EXPANSIONS)} pasienter utvidet. {len(pasienter)} totalt bevart.")
    print(f"Skrevet: {PASIENTER_JSON}")


if __name__ == "__main__":
    main()
