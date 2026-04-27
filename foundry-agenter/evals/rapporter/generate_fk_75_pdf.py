"""Fullstendig PDF-rapport for 75-spm × 3-runs Felleskatalogen-eval.

Inneholder:
- Side 1: Tittel + sammendrag + dommer-varians-definisjon
- Per kategori: tabell med score-fordeling
- Per spørsmål (1 side per): spm, fasit, alle 3 runs (score+begrunnelse), konsensus, answer-utdrag
- Stabilitetsanalyse + metodologi
"""
from __future__ import annotations
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
)

HERE = Path(__file__).parent
COMBINED = HERE / "rapport-20260427-2129-fk-75-final-combined.json"
RUNS = [
    HERE / "rapport-20260427-2045-fk-75-final-run1.json",
    HERE / "rapport-20260427-2105-fk-75-final-run2.json",
    HERE / "rapport-20260427-2129-fk-75-final-run3.json",
]
QUESTIONS_FILE = HERE.parent / "eval-questions-felleskatalogen-75.json"
PDF_OUT = HERE / "rapport-20260427-fk-75-fullstendig.pdf"

# Atea-palett
ATEA_BLUE = colors.HexColor("#0072CE")
ATEA_DARK = colors.HexColor("#003B5C")
GREEN_OK = colors.HexColor("#10B981")
GREEN_BG = colors.HexColor("#ECFDF5")
ORANGE = colors.HexColor("#F59E0B")
ORANGE_BG = colors.HexColor("#FFFBEB")
RED = colors.HexColor("#DC2626")
RED_BG = colors.HexColor("#FEF2F2")
GRAY_LIGHT = colors.HexColor("#F3F4F6")
GRAY_MED = colors.HexColor("#9CA3AF")
TEXT_DARK = colors.HexColor("#111827")

SCORE_COLORS = {
    "BESTATT": (GREEN_OK, GREEN_BG),
    "DELVIS": (ORANGE, ORANGE_BG),
    "MANGLER": (ORANGE, ORANGE_BG),
    "FEIL": (RED, RED_BG),
    "FEIL_TEKNISK": (GRAY_MED, GRAY_LIGHT),
    "HALLUSINERING": (RED, RED_BG),
}


def styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=22,
            leading=26, textColor=ATEA_DARK, fontName="Helvetica-Bold",
            alignment=TA_LEFT, spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontSize=13,
            leading=16, textColor=ATEA_BLUE, spaceAfter=2),
        "meta": ParagraphStyle("meta", parent=base["Normal"], fontSize=9, leading=12,
            textColor=GRAY_MED, spaceAfter=12),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=14, leading=18,
            textColor=ATEA_DARK, fontName="Helvetica-Bold",
            spaceBefore=10, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=12, leading=15,
            textColor=ATEA_DARK, fontName="Helvetica-Bold",
            spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=10, leading=14,
            textColor=TEXT_DARK, spaceAfter=6),
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=8, leading=11,
            textColor=GRAY_MED),
        "callout": ParagraphStyle("callout", parent=base["Normal"], fontSize=9,
            leading=13, textColor=TEXT_DARK, leftIndent=8, rightIndent=8,
            backColor=GRAY_LIGHT, borderPadding=8, spaceAfter=10),
        "spm_id": ParagraphStyle("spm_id", parent=base["Normal"], fontSize=10,
            leading=14, fontName="Courier-Bold", textColor=ATEA_BLUE),
        "spm": ParagraphStyle("spm", parent=base["Normal"], fontSize=11, leading=15,
            textColor=TEXT_DARK, fontName="Helvetica-Bold", spaceAfter=4),
        "label": ParagraphStyle("label", parent=base["Normal"], fontSize=8, leading=11,
            textColor=GRAY_MED, fontName="Helvetica-Bold", spaceAfter=2),
        "field": ParagraphStyle("field", parent=base["Normal"], fontSize=9, leading=12,
            textColor=TEXT_DARK, spaceAfter=4),
        "code": ParagraphStyle("code", parent=base["Normal"], fontSize=8, leading=11,
            fontName="Courier", textColor=TEXT_DARK,
            leftIndent=6, rightIndent=6, backColor=GRAY_LIGHT, borderPadding=4,
            spaceAfter=4),
        "begrunnelse": ParagraphStyle("begrunnelse", parent=base["Normal"], fontSize=8,
            leading=11, textColor=TEXT_DARK, spaceAfter=2,
            leftIndent=4),
    }


def load_data():
    combined = json.loads(COMBINED.read_text(encoding="utf-8"))
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in RUNS]
    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))["questions"]
    qmap = {q["id"]: q for q in questions}
    # Build run-id-map for hver run
    run_maps = [{r["id"]: r for r in run["resultater"]} for run in runs]
    return combined, runs, qmap, run_maps


def score_chip(text: str, st):
    """Liten farget badge for score."""
    fg, bg = SCORE_COLORS.get(text, (TEXT_DARK, GRAY_LIGHT))
    style = ParagraphStyle(
        "chip", parent=st["field"],
        fontSize=8, fontName="Helvetica-Bold",
        textColor=fg, backColor=bg,
        borderPadding=3, alignment=TA_CENTER,
    )
    return Paragraph(text, style)


def summary_section(combined, st):
    opp = combined["oppsummering"]
    konsensus_bestatt = opp.get("bestatt", 0)
    delvis = opp.get("delvis", 0)
    feil = opp.get("feil", 0)
    mangler = opp.get("mangler", 0)
    total = combined["metadata"]["antall_spoersmaal"]
    stabilitet = opp.get("unanime_spoersmaal", 0)
    stabilitet_pct = opp.get("stabilitet_prosent", 0)
    routing_ok = opp.get("routing_korrekthet", 0)
    korrekt = opp.get("korrekthetsscore", "—")
    n_runs = opp.get("antall_kjoringer", 3)

    # Snitt fra run-rapporter — combined gir ikke det
    rows = [
        ["Antall spørsmål", str(total)],
        ["Antall kjøringer", f"{n_runs} (totalt {total*n_runs} agent-kall)"],
        ["Konsensus-korrekthetsscore", korrekt],
        ["Konsensus BESTÅTT", f"{konsensus_bestatt} / {total}"],
        ["Konsensus DELVIS / MANGLER / FEIL", f"{delvis} / {mangler} / {feil}"],
        ["Stabilitet (lik score alle runs)", f"{stabilitet} / {total} ({stabilitet_pct:.0f}%)"],
        ["Routing-korrekthet", f"{routing_ok} / {total}"],
        ["Eval-modeller", "syntese gpt-5.4, dommer gpt-5.5"],
    ]
    tbl = Table(rows, colWidths=[70 * mm, 100 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), ATEA_DARK),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        # Highlight korrekthetsscore (rad 2) og BESTÅTT (rad 3) og stabilitet (rad 5)
        ("BACKGROUND", (1, 2), (1, 3), GREEN_BG),
        ("TEXTCOLOR", (1, 2), (1, 3), GREEN_OK),
        ("FONTNAME", (1, 2), (1, 3), "Helvetica-Bold"),
        ("BACKGROUND", (1, 5), (1, 5), GREEN_BG),
        ("TEXTCOLOR", (1, 5), (1, 5), GREEN_OK),
        ("FONTNAME", (1, 5), (1, 5), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BOX", (0, 0), (-1, -1), 0.5, ATEA_DARK),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, GRAY_LIGHT),
    ]))
    return tbl


def per_kat_table(combined, st):
    rows = [[Paragraph(f"<b>{h}</b>", st["field"]) for h in
             ["Kategori", "Total", "BESTÅTT", "DELVIS+", "FEIL+", "Stabil"]]]
    katmap = {}
    for r in combined["resultater"]:
        kat = r["kategori"]
        d = katmap.setdefault(kat, {"total": 0, "bestatt": 0, "delvis": 0,
                                      "feil": 0, "stabil": 0})
        d["total"] += 1
        sc = r["consensus_score"]
        if sc == "BESTATT":
            d["bestatt"] += 1
        elif sc in ("DELVIS", "MANGLER"):
            d["delvis"] += 1
        else:
            d["feil"] += 1
        if r.get("unanimous"):
            d["stabil"] += 1
    for kat in sorted(katmap):
        d = katmap[kat]
        rows.append([
            Paragraph(kat.replace("-", " "), st["field"]),
            Paragraph(str(d["total"]), st["field"]),
            Paragraph(str(d["bestatt"]), st["field"]),
            Paragraph(str(d["delvis"]), st["field"]),
            Paragraph(str(d["feil"]), st["field"]),
            Paragraph(f"{d['stabil']}/{d['total']}", st["field"]),
        ])
    tbl = Table(rows, colWidths=[55 * mm, 18 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm])
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ATEA_DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BOX", (0, 0), (-1, -1), 0.5, ATEA_DARK),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRAY_LIGHT),
    ])
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.add("BACKGROUND", (0, i), (-1, i), GRAY_LIGHT)
    tbl.setStyle(style)
    return tbl


def question_page(qid, q, combined_r, run_maps, qmap, st):
    """Bygg én side med alle detaljer for ett spørsmål."""
    elements = []

    # Header
    header_tbl = Table([
        [Paragraph(qid, st["spm_id"]),
         Paragraph(q.get("kategori", "").replace("-", " "), st["small"]),
         score_chip(combined_r["consensus_score"], st)]
    ], colWidths=[30 * mm, 100 * mm, 30 * mm])
    header_tbl.setStyle(TableStyle([
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "LEFT"),
        ("ALIGN", (2, 0), (2, 0), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, ATEA_DARK),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
    ]))
    elements.append(header_tbl)
    elements.append(Spacer(1, 4))

    # Spørsmål
    elements.append(Paragraph(f"<b>Spørsmål:</b> {q['sporsmal']}", st["body"]))
    if q.get("tema"):
        elements.append(Paragraph(f"<i>Tema: {q['tema']}</i>", st["small"]))

    # Routing
    expected = q.get("forventet_routing", [])
    actual = combined_r.get("actual_routing", [])
    routing_color = "#10B981" if combined_r.get("routing_correct") else "#DC2626"
    routing_html = (
        f'<font color="{routing_color}"><b>'
        f'{"✓" if combined_r.get("routing_correct") else "✗"}</b></font> '
        f'Forventet: <font face="Courier">{expected}</font> · '
        f'Faktisk: <font face="Courier">{actual}</font>'
    )
    elements.append(Paragraph(routing_html, st["field"]))
    elements.append(Spacer(1, 6))

    # Fasit
    fasit = q.get("faktasjekk", {})
    skal = fasit.get("skal_inneholde", [])
    skal_ikke = fasit.get("skal_IKKE_inneholde", [])
    fasit_html = (
        f'<b>Fasit — skal inneholde:</b> '
        f'{", ".join(repr(s) for s in skal) if skal else "(ingen)"}<br/>'
        f'<b>Fasit — skal IKKE inneholde:</b> '
        f'{", ".join(repr(s) for s in skal_ikke) if skal_ikke else "(ingen)"}<br/>'
        f'<b>Kildekrav:</b> {fasit.get("kilde_krav", "(ikke angitt)")}'
    )
    elements.append(Paragraph(fasit_html, st["field"]))
    elements.append(Spacer(1, 6))

    # 3 runs side-by-side
    elements.append(Paragraph("<b>Vurdering per kjøring:</b>", st["label"]))
    run_rows = [[
        Paragraph("<b>Run</b>", st["field"]),
        Paragraph("<b>Score</b>", st["field"]),
        Paragraph("<b>Treff</b>", st["field"]),
        Paragraph("<b>Mangler</b>", st["field"]),
        Paragraph("<b>Begrunnelse (dommer-LLM)</b>", st["field"]),
    ]]
    for i, run_map in enumerate(run_maps, start=1):
        rd = run_map.get(qid, {})
        score = rd.get("score", "?")
        treff = rd.get("treff") or []
        mangler = rd.get("mangler") or []
        begrunn = (rd.get("begrunnelse") or "").strip()
        if len(begrunn) > 280:
            begrunn = begrunn[:277] + "..."
        run_rows.append([
            Paragraph(f"#{i}", st["field"]),
            score_chip(score, st),
            Paragraph(f"{len(treff)}/{rd.get('forventet', '?')}", st["field"]),
            Paragraph(", ".join(mangler) if mangler else "—", st["small"]),
            Paragraph(begrunn or "—", st["small"]),
        ])
    run_tbl = Table(run_rows, colWidths=[10 * mm, 22 * mm, 14 * mm, 40 * mm, 86 * mm])
    run_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ATEA_DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BOX", (0, 0), (-1, -1), 0.5, ATEA_DARK),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRAY_LIGHT),
    ]))
    elements.append(run_tbl)
    elements.append(Spacer(1, 6))

    # Konsensus + answer-utdrag (fra run 1)
    konsensus_html = (
        f'<b>Konsensus:</b> {combined_r["consensus_score"]} '
        f'({"enstemmig" if combined_r.get("unanimous") else f"{combined_r.get('all_scores')}"}) — '
        f'2-av-3 majoritetsstemme'
    )
    elements.append(Paragraph(konsensus_html, st["field"]))

    # Answer preview
    rd0 = run_maps[0].get(qid, {})
    preview = (rd0.get("answer_preview") or "").strip()
    if preview:
        # Erstatt linjeskift med space for kompakt visning
        preview_oneline = preview.replace("\n", " ").replace("\r", " ")
        if len(preview_oneline) > 400:
            preview_oneline = preview_oneline[:397] + "..."
        elements.append(Paragraph("<b>Agent-svar (utdrag, run 1):</b>", st["label"]))
        elements.append(Paragraph(preview_oneline, st["code"]))

    return KeepTogether(elements)


def main():
    combined, runs, qmap, run_maps = load_data()
    st = styles()
    doc = SimpleDocTemplate(
        str(PDF_OUT), pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title="HAPI Felleskatalogen-eval — fullstendig 75-spm rapport",
        author="HAPI / Atea",
    )
    story = []

    # === Side 1 — sammendrag ===
    story.append(Paragraph("HAPI Felleskatalogen-eval", st["title"]))
    story.append(Paragraph("Fullstendig 75-spm × 3-runs rapport", st["subtitle"]))
    story.append(Paragraph(
        "27. april 2026 &nbsp;|&nbsp; Tag: <b>fk-75-final</b> &nbsp;|&nbsp; "
        "Master SHA: <font face='Courier'>49d52c3</font>",
        st["meta"],
    ))

    story.append(Paragraph("Sammendrag", st["h1"]))
    story.append(summary_section(combined, st))
    story.append(Spacer(1, 8))

    # Dommer-varians
    story.append(Paragraph("Hva er dommer-varians?", st["h1"]))
    story.append(Paragraph(
        "<b>Dommer-varians</b> = samme spørsmål gir nær-identisk agent-svar, "
        "men LLM-dommeren tildeler forskjellig score over gjentatte vurderinger. "
        "Dette er normalt — LLM-vurderinger er ikke deterministiske, og terskel-"
        "avgjørelsen mellom BESTÅTT/DELVIS/FEIL flytter seg på subtile språknyanser. "
        "Eksempel fra denne evalen: FK-TR-03 (Triatec ved nyresvikt) fikk "
        "scores [DELVIS, BESTÅTT, BESTÅTT] — agentens svar var nesten ordrett likt "
        "alle tre ganger, men dommeren landet ulikt. Konsensus = BESTÅTT via "
        "2-av-3 majoritet.",
        st["body"],
    ))

    story.append(Paragraph("Per kategori", st["h1"]))
    story.append(per_kat_table(combined, st))
    story.append(Spacer(1, 6))

    # Stabilitetsanalyse
    story.append(Paragraph("Stabilitet og ustabile spørsmål", st["h1"]))
    ustabile = [r for r in combined["resultater"] if not r.get("unanimous")]
    if ustabile:
        rows = [[Paragraph(f"<b>{h}</b>", st["field"]) for h in
                 ["Spm", "Scores (run 1, 2, 3)", "Konsensus", "Diagnose"]]]
        diagnoser = {
            "FK-TR-03": "Naturlig dommer-varians på Triatec nyresvikt-svar",
            "FK-ON-01": "Nettverk-transient i run 1 (FEIL_TEKNISK)",
            "FK-NEG-02": "Retningslinje-agent leverte 'feilaktig' KOLS-svar — utenfor FK-fokus",
            "FK-NEG-03": "Kodeverk-agent fant ikke I48 i HAPI-data",
        }
        for r in ustabile:
            qid = r["id"]
            rows.append([
                Paragraph(qid, st["spm_id"]),
                Paragraph(", ".join(r["all_scores"]), st["small"]),
                score_chip(r["consensus_score"], st),
                Paragraph(diagnoser.get(qid, "Ikke analysert"), st["small"]),
            ])
        ustabile_tbl = Table(rows, colWidths=[22 * mm, 60 * mm, 22 * mm, 76 * mm])
        ustabile_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), ATEA_DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("BOX", (0, 0), (-1, -1), 0.5, ATEA_DARK),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRAY_LIGHT),
        ]))
        story.append(ustabile_tbl)
    else:
        story.append(Paragraph("Alle spørsmål var enstemmige.", st["body"]))

    story.append(PageBreak())

    # === Per-spørsmål-sider ===
    story.append(Paragraph("Detaljer per spørsmål", st["title"]))
    story.append(Paragraph(
        "Hver oppføring viser spørsmålet, fasit-krav, vurdering fra hver av "
        "de 3 dommer-kjøringene, konsensus-score og et utdrag av selve "
        "agent-svaret fra run 1.",
        st["body"],
    ))
    story.append(Spacer(1, 6))

    for r in combined["resultater"]:
        qid = r["id"]
        q = qmap.get(qid)
        if not q:
            continue
        story.append(question_page(qid, q, r, run_maps, qmap, st))
        story.append(Spacer(1, 8))

    # === Metode ===
    story.append(PageBreak())
    story.append(Paragraph("Metodologi", st["title"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Eval-flyt", st["h2"]))
    story.append(Paragraph(
        "Hvert spørsmål sendes til <font face='Courier'>/ask</font>-endepunktet på "
        "HAPI orchestrator (Azure Container Apps, revision <font face='Courier'>0058</font>). "
        "Routeren matcher spørsmålet mot keyword-trigger-lister. Ved match på "
        "Felleskatalogen-trigger kalles <font face='Courier'>hapi-felleskatalogen-agent</font> "
        "i Microsoft Foundry, som bruker MCP-tools "
        "(<font face='Courier'>sok_felleskatalogen</font>, "
        "<font face='Courier'>hent_felleskatalogen_dosering</font>) mot SQLite/FTS5-databasen "
        "<font face='Courier'>felleskatalogen.db</font> bundlet i MCP-imaget. "
        "Agenten omslutter resultatet med <font face='Courier'>[VERBATIM-FELLESKATALOGEN]</font>-"
        "markører. Orchestratoren plukker ut blokken og leverer den uendret til klienten — "
        "ingen LLM-syntese påvirker innholdet.",
        st["body"],
    ))

    story.append(Paragraph("Modell-stack", st["h2"]))
    story.append(Paragraph(
        "<b>Foundry-agent:</b> gpt-5.3-chat &nbsp;|&nbsp; "
        "<b>Syntese:</b> gpt-5.4 &nbsp;|&nbsp; "
        "<b>Router-fallback:</b> gpt-5.3-chat &nbsp;|&nbsp; "
        "<b>Eval-dommer:</b> gpt-5.5 &nbsp; "
        "(A/B-grunnlag i evals/rapporter/rapport-20260426-*.json)",
        st["body"],
    ))

    story.append(Paragraph("Stabilitet", st["h2"]))
    story.append(Paragraph(
        "Stabilitet defineres som andelen spørsmål der dommeren tildelte identisk "
        "score i alle 3 kjøringer. Konsensus er majoritetsstemme over de 3 scorene "
        "(2-av-3 vinner). Stabilitet 95% (71 av 75) — 4 spørsmål endte med uenighet "
        "mellom kjøringer; alle 4 ble håndtert via majoritetsstemme.",
        st["body"],
    ))

    story.append(Paragraph("Lisens og kildedisclaimer", st["h2"]))
    story.append(Paragraph(
        "Innholdet i Felleskatalogen-databasen tilhører Felleskatalogen AS. Denne POC-en "
        "bruker dataene under demo-bruk; kommersiell rollout krever lisensavtale. Alle 18 "
        "preparater i POC-utvalget er scrapet 2026-04-27 med 10 sekunders crawl-delay i "
        "samsvar med robots.txt. Brukeren henvises alltid til full preparatomtale på "
        "felleskatalogen.no før klinisk forskrivning.",
        st["small"],
    ))

    doc.build(story)
    print(f"Skrevet: {PDF_OUT}")
    print(f"Stoerrelse: {PDF_OUT.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
