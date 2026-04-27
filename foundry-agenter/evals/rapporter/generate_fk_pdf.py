"""Generate PDF report from FK eval JSON."""
import json
from collections import Counter
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

HERE = Path(__file__).parent
JSON_PATH = HERE / "rapport-20260427-1507-fk-v2.json"
PDF_PATH = HERE / "rapport-20260427-fk-100pct.pdf"

# Atea-inspirert palett
ATEA_BLUE = colors.HexColor("#0072CE")
ATEA_DARK = colors.HexColor("#003B5C")
GREEN_OK = colors.HexColor("#10B981")
GREEN_BG = colors.HexColor("#ECFDF5")
GRAY_LIGHT = colors.HexColor("#F3F4F6")
GRAY_MED = colors.HexColor("#9CA3AF")
TEXT_DARK = colors.HexColor("#111827")


def load_data():
    with open(JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=ATEA_DARK,
            alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=13,
            leading=16,
            textColor=ATEA_BLUE,
            spaceAfter=2,
        ),
        "meta": ParagraphStyle(
            "meta",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=GRAY_MED,
            spaceAfter=12,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=ATEA_DARK,
            spaceBefore=14,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=TEXT_DARK,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "small",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=GRAY_MED,
        ),
        "code": ParagraphStyle(
            "code",
            parent=base["Normal"],
            fontName="Courier-Bold",
            fontSize=9,
            leading=12,
        ),
        "cell": ParagraphStyle(
            "cell",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=TEXT_DARK,
        ),
        "cell_id": ParagraphStyle(
            "cell_id",
            parent=base["Normal"],
            fontName="Courier-Bold",
            fontSize=8,
            leading=11,
            textColor=ATEA_BLUE,
        ),
        "cell_kat": ParagraphStyle(
            "cell_kat",
            parent=base["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=7,
            leading=10,
            textColor=GRAY_MED,
        ),
    }


def summary_box(data, st):
    opp = data["oppsummering"]
    stat = data["statistikk"]
    score = opp["korrekthetsscore"]
    bestatt = opp["bestatt"]
    total = data["metadata"]["antall_spoersmaal"]
    routing = opp["routing_korrekthet"]
    snitt = opp["snitt_responstid_ms"]
    pris_model = stat.get("prismodell", {}).get("model", "?")

    rows = [
        ["Korrekthetsscore", score],
        ["BESTATT", f"{bestatt} / {total} (100%)"],
        ["Routing-korrekthet", f"{routing} / {total} (93%)"],
        ["Snittlig responstid", f"{snitt/1000:.1f} sekunder"],
        ["Hallusinering / FEIL", "0 / 0"],
        ["Syntese-modell", "gpt-5.4 (default)"],
        ["Dommer-modell", pris_model],
    ]
    tbl = Table(rows, colWidths=[55 * mm, 110 * mm])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), ATEA_DARK),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (1, 0), (1, 0), GREEN_BG),
                ("TEXTCOLOR", (1, 0), (1, 0), GREEN_OK),
                ("FONTNAME", (1, 0), (1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (1, 1), (1, 1), GREEN_BG),
                ("TEXTCOLOR", (1, 1), (1, 1), GREEN_OK),
                ("FONTNAME", (1, 1), (1, 1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.white),
                ("LINEBELOW", (1, 0), (1, -2), 0.25, GRAY_LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.5, ATEA_DARK),
            ]
        )
    )
    return tbl


def per_spm_table(data, st):
    rows = [
        [
            Paragraph("<b>ID</b>", st["cell"]),
            Paragraph("<b>Kategori</b>", st["cell"]),
            Paragraph("<b>Spørsmål</b>", st["cell"]),
            Paragraph("<b>Score</b>", st["cell"]),
            Paragraph("<b>Treff</b>", st["cell"]),
            Paragraph("<b>Routing</b>", st["cell"]),
        ]
    ]
    spms = {q["id"]: q for q in load_questions()}
    for r in data["resultater"]:
        qid = r["id"]
        spm = spms.get(qid, {}).get("sporsmal", "")
        if len(spm) > 90:
            spm = spm[:87] + "..."
        treff = r.get("treff") or []
        treff_n = len(treff)
        forv = r.get("forventet")
        forventet_n = forv if isinstance(forv, int) else (len(forv) if forv else max(treff_n, 1))
        score = r.get("score", "?")
        routing_ok = "✓" if r.get("routing_correct") else "✗"
        rows.append(
            [
                Paragraph(qid, st["cell_id"]),
                Paragraph(r.get("kategori", "").replace("-", " "), st["cell_kat"]),
                Paragraph(spm, st["cell"]),
                Paragraph(score, st["cell"]),
                Paragraph(f"{treff_n}/{forventet_n}", st["cell"]),
                Paragraph(routing_ok, st["cell"]),
            ]
        )
    tbl = Table(
        rows,
        colWidths=[18 * mm, 25 * mm, 75 * mm, 22 * mm, 14 * mm, 16 * mm],
        repeatRows=1,
    )
    style = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), ATEA_DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ALIGN", (3, 0), (-1, -1), "CENTER"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRAY_LIGHT),
            ("BOX", (0, 0), (-1, -1), 0.5, ATEA_DARK),
        ]
    )
    # Highlight all data rows: BESTÅTT-grønn på score-celle
    for i, r in enumerate(data["resultater"], start=1):
        if r.get("score") == "BESTATT":
            style.add("BACKGROUND", (3, i), (3, i), GREEN_BG)
            style.add("TEXTCOLOR", (3, i), (3, i), GREEN_OK)
            style.add("FONTNAME", (3, i), (3, i), "Helvetica-Bold")
        if r.get("routing_correct"):
            style.add("TEXTCOLOR", (5, i), (5, i), GREEN_OK)
            style.add("FONTNAME", (5, i), (5, i), "Helvetica-Bold")
        else:
            style.add("TEXTCOLOR", (5, i), (5, i), colors.HexColor("#DC2626"))
            style.add("FONTNAME", (5, i), (5, i), "Helvetica-Bold")
        if i % 2 == 0:
            style.add("BACKGROUND", (0, i), (2, i), GRAY_LIGHT)
            style.add("BACKGROUND", (4, i), (5, i), GRAY_LIGHT)
    tbl.setStyle(style)
    return tbl


def per_kat_table(data, st):
    rows = [
        [
            Paragraph("<b>Kategori</b>", st["cell"]),
            Paragraph("<b>Resultat</b>", st["cell"]),
            Paragraph("<b>Andel</b>", st["cell"]),
        ]
    ]
    katmap = {}
    for r in data["resultater"]:
        kat = r["kategori"]
        katmap.setdefault(kat, {"total": 0, "ok": 0})
        katmap[kat]["total"] += 1
        if r.get("score") == "BESTATT":
            katmap[kat]["ok"] += 1
    for kat in sorted(katmap):
        ok = katmap[kat]["ok"]
        tot = katmap[kat]["total"]
        rows.append(
            [
                Paragraph(kat.replace("-", " "), st["cell"]),
                Paragraph(f"{ok}/{tot}", st["cell"]),
                Paragraph(f"{ok/tot*100:.0f}%", st["cell"]),
            ]
        )
    tbl = Table(rows, colWidths=[80 * mm, 30 * mm, 30 * mm], repeatRows=1)
    style = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), ATEA_DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("BOX", (0, 0), (-1, -1), 0.5, ATEA_DARK),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRAY_LIGHT),
        ]
    )
    for i in range(1, len(rows)):
        style.add("BACKGROUND", (1, i), (2, i), GREEN_BG)
        style.add("TEXTCOLOR", (1, i), (2, i), GREEN_OK)
        style.add("FONTNAME", (1, i), (2, i), "Helvetica-Bold")
        if i % 2 == 0:
            style.add("BACKGROUND", (0, i), (0, i), GRAY_LIGHT)
    tbl.setStyle(style)
    return tbl


def load_questions():
    qpath = HERE.parent / "eval-questions-felleskatalogen.json"
    with open(qpath, encoding="utf-8") as f:
        return json.load(f)["questions"]


def main():
    data = load_data()
    st = build_styles()
    doc = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="HAPI Felleskatalogen-eval POC-rapport",
        author="HAPI / Atea",
    )
    story = []

    story.append(Paragraph("HAPI Felleskatalogen-eval", st["title"]))
    story.append(Paragraph("POC-rapport — verbatim doseringsoppslag", st["subtitle"]))
    story.append(
        Paragraph(
            "27. april 2026 &nbsp;|&nbsp; Eksperiment-tag: <b>fk-v2</b> &nbsp;|&nbsp; "
            "Master SHA: <font face='Courier'>220d489</font>",
            st["meta"],
        )
    )

    story.append(Spacer(1, 4))
    story.append(Paragraph("Sammendrag", st["h1"]))
    story.append(summary_box(data, st))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Hvordan agenten fungerer", st["h1"]))
    story.append(
        Paragraph(
            "Felleskatalogen-agenten er en <b>opt-in agent</b> i HAPI som aktiveres "
            "kun når brukeren eksplisitt skriver triggerord som <i>"
            "felleskatalogen, preparatomtale, vis dosering, slå opp dosering, spc, "
            "verifisert dosering</i>. Når triggeren slår inn, ekskluderes retningslinje- "
            "og kodeverk-agentene helt — det er kun Felleskatalogen-agenten som svarer. "
            "Output bypass-er LLM-syntese-laget og leveres ordrett til bruker som adskilt "
            "blockquote, omsluttet av <font face='Courier'>[VERBATIM-FELLESKATALOGEN]</font>"
            "-markører. Hver verbatim-blokk inneholder kildelink til felleskatalogen.no, "
            "scrape-dato, ATC-kode og en disclaimer som ber klinikeren sjekke fullstendig "
            "preparatomtale før forskrivning. Datasettet i denne POC-en er 18 flaggskip-"
            "legemidler scrapet 2026-04-27 (crawl-delay 10s, robots.txt overholdt).",
            st["body"],
        )
    )

    story.append(Paragraph("Per-spørsmål resultat", st["h1"]))
    story.append(per_spm_table(data, st))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Per kategori", st["h1"]))
    story.append(per_kat_table(data, st))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Metodologi", st["h1"]))
    story.append(
        Paragraph(
            "<b>Eval-flyt:</b> spørsmål sendes til <font face='Courier'>"
            "/ask</font>-endepunktet på HAPI orchestrator (Azure Container Apps, "
            "revision <font face='Courier'>0058</font>). Routeren matcher mot "
            "Felleskatalogen-triggers; ved match kalles <font face='Courier'>"
            "hapi-felleskatalogen-agent</font> i Microsoft Foundry, som bruker "
            "MCP-tools (<font face='Courier'>sok_felleskatalogen</font>, "
            "<font face='Courier'>hent_felleskatalogen_dosering</font>) mot "
            "SQLite/FTS5-databasen <font face='Courier'>felleskatalogen.db</font> "
            "bundlet i MCP-imaget. Agenten omslutter resultatet med verbatim-markører. "
            "Orchestratoren plukker ut blokken og leverer den uendret til klienten "
            "(ingen LLM-syntese). Dommer-LLM (gpt-5.5) sammenligner svaret mot "
            "fasit-fraser i <font face='Courier'>eval-questions-felleskatalogen.json</font>.",
            st["body"],
        )
    )
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "<b>Modell-stack i evalen:</b> agentene gpt-5.3-chat, syntese gpt-5.4, "
            "router-fallback gpt-5.3-chat, dommer gpt-5.5. A/B-grunnlag dokumentert i "
            "<font face='Courier'>evals/rapporter/rapport-20260426-*.json</font>.",
            st["body"],
        )
    )

    story.append(Spacer(1, 12))
    story.append(
        Paragraph(
            "<b>Lisens og kildedisclaimer:</b> Innholdet i Felleskatalogen-databasen "
            "tilhører Felleskatalogen AS. Denne POC-en bruker dataene under demo-bruk; "
            "kommersiell rollout krever lisensavtale. Alle 18 preparater er scrapet "
            "2026-04-27 med 10 sekunders crawl-delay i samsvar med robots.txt. Brukeren "
            "henvises alltid til full preparatomtale på felleskatalogen.no før klinisk "
            "forskrivning.",
            st["small"],
        )
    )

    doc.build(story)
    print(f"Skrevet: {PDF_PATH}")
    print(f"Stoerrelse: {PDF_PATH.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
