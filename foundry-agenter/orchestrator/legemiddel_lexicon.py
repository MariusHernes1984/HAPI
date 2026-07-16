"""
Legemiddel-lexicon — normalisering av legemiddelnavn nevnt i fritekst.

Flyttet ut fra orchestrate.py slik at både orchestrate.py og router.py kan
bruke ordbøkene uten sirkulær import (orchestrate importerer router).
"""

import re

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


def extract_mentioned_meds_detailed(texts: list[str]) -> tuple[list[str], dict[str, str]]:
    """
    Skann fritekst for kjente legemiddelnavn.

    Returnerer (normaliserte navn, gruppe-map) der gruppe-map viser hvilke
    navn som kom fra et GRUPPE-alias (f.eks. {"Sertralin": "ssri"}) slik at
    presentasjonslaget kan merke treffet som representativt for gruppen —
    ikke som brukerens konkrete legemiddel.
    """
    found: set[str] = set()
    group_map: dict[str, str] = {}

    combined = " ".join(texts).lower()
    # Fjern noen tegn som kan hindre matching
    combined = re.sub(r"[/\-–]", " ", combined)

    # Sjekk enkeltord mot alias-ordbøkene
    words = set(re.findall(r"[a-zæøå]+", combined))
    for word in words:
        if word in LEGEMIDDEL_ALIASES:
            found.add(LEGEMIDDEL_ALIASES[word])
        elif word in GRUPPE_ALIASES:
            norm = GRUPPE_ALIASES[word]
            found.add(norm)
            group_map.setdefault(norm, word)

    # Sjekk også flerords-aliaser (f.eks. "selo-zok" → "selo zok" etter normalisering)
    for alias, norm in LEGEMIDDEL_ALIASES.items():
        if " " in alias and alias in combined:
            found.add(norm)

    # Et direkte navnetreff overstyrer gruppe-markering (spurte man på
    # "sertralin" OG "ssri" er treffet ikke bare representativt).
    for word in words:
        if word in LEGEMIDDEL_ALIASES:
            group_map.pop(LEGEMIDDEL_ALIASES[word], None)

    return list(found), group_map


def extract_mentioned_meds(texts: list[str]) -> list[str]:
    """Som extract_mentioned_meds_detailed, men bare navnelisten (bakoverkompatibel)."""
    meds, _ = extract_mentioned_meds_detailed(texts)
    return meds
