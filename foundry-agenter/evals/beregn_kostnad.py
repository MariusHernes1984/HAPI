"""Backfill token-bruk og kostnad i NOK til alle eval-rapporter.

Bruker snittall fra App Insights-data (583 spoersmaal over 20 rapporter):
  - 12 080 input tokens / spoersmaal
  - 380 output tokens / spoersmaal

Priser (gpt-5.3-chat, USD->NOK kurs 11):
  - Input:  $1.75 / 1M  -> 19.25 NOK / 1M
  - Output: $14.00 / 1M -> 154.00 NOK / 1M

For rapporter med direkte App Insights-treff brukes maalte tall; ellers estimat.
Resultatet skrives som ny "statistikk"-blokk i rapport-JSON.
"""
import json
import glob
import os

SNITT_INPUT_PER_Q = 12080
SNITT_OUTPUT_PER_Q = 380
NOK_PER_INPUT_TOKEN = 19.25 / 1_000_000
NOK_PER_OUTPUT_TOKEN = 154.00 / 1_000_000

# Maalte verdier fra KQL-join (input_tokens, output_tokens)
MAALT = {
    "20260328-1000": (392101, 14856),
    "20260328-1120": (1289, 52),
    "20260328-1532": (1469024, 30970),
    "20260328-1637": (159114, 3406),
    "20260328-1750": (107209, 4610),
    "20260330-0928": (303949, 15581),
    "20260331-2032": (622180, 25701),
    "20260331-2245": (336642, 14127),
    "20260331-2305": (54999, 4396),
    "20260401-1755": (120683, 5854),
    "20260401-2115": (631267, 19814),
    "20260402-1557": (299806, 11443),
    "20260402-1903": (127833, 3629),
    "20260402-1934": (1177580, 35386),
    "20260403-1859": (8225, 1150),
    "20260404-1918": (370594, 12876),
    "20260404-2044": (607804, 10700),
    "20260407-0731": (47189, 2763),
    "20260407-0758": (45610, 1498),
    "20260407-0829": (159726, 2810),
}


def beregn_kostnad(input_tok: int, output_tok: int) -> float:
    return round(input_tok * NOK_PER_INPUT_TOKEN + output_tok * NOK_PER_OUTPUT_TOKEN, 4)


def main():
    rapport_dir = os.path.join(os.path.dirname(__file__), "rapporter")
    filer = sorted(glob.glob(os.path.join(rapport_dir, "rapport-*.json")))
    oppdatert = 0
    for f in filer:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            print(f"SKIP {os.path.basename(f)}: {e}")
            continue

        n_q = d.get("metadata", {}).get("antall_spoersmaal") or len(d.get("resultater", []))
        if n_q == 0:
            continue

        # Combined-rapporter aggregerer run1+2+3 -> alltid estimat
        is_combined = "combined" in os.path.basename(f)
        key = os.path.basename(f).replace("rapport-", "")[:13]

        if key in MAALT and not is_combined:
            tin, tout = MAALT[key]
            kilde = "maalt-app-insights"
        else:
            tin = n_q * SNITT_INPUT_PER_Q
            tout = n_q * SNITT_OUTPUT_PER_Q
            kilde = "estimert-snitt"

        kost = beregn_kostnad(tin, tout)
        d["statistikk"] = {
            "tokens_input": tin,
            "tokens_output": tout,
            "tokens_total": tin + tout,
            "kostnad_nok": kost,
            "kostnad_per_spoersmaal_nok": round(kost / n_q, 4),
            "kilde": kilde,
            "prismodell": {
                "model": "gpt-5.3-chat",
                "input_usd_per_1m": 1.75,
                "output_usd_per_1m": 14.00,
                "kurs_usd_nok": 11.0,
            },
        }
        json.dump(d, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        oppdatert += 1

    print(f"Oppdatert {oppdatert} rapporter.")


if __name__ == "__main__":
    main()
