"""Generer PDF-oversikt over alle mock-pasienter."""

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    PageBreak,
)

HERE = Path(__file__).parent
DATA = json.loads((HERE / "pasienter.json").read_text(encoding="utf-8"))
PATIENTS = DATA["pasienter"]
OUT = HERE / "HAPI-mock-pasienter.pdf"


def fmt_diagnoser(p):
    ds = p.get("diagnoser") or []
    if not ds:
        return "<i>Frisk</i>"
    return "<br/>".join(
        f"{d['tekst']} ({d['kodeverk']}:{d['kode']})" for d in ds
    )


def fmt_meds(p):
    meds = p.get("faste_medisiner") or []
    if not meds:
        return "&mdash;"
    return "<br/>".join(f"{m['navn']} {m['dose']}" for m in meds)


def fmt_allergier(p):
    a = p.get("allergier") or []
    return ", ".join(a) if a else "&mdash;"


def main():
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=14 * mm,
        bottomMargin=12 * mm,
        title="HAPI Mock-pasienter",
        author="HAPI demo",
    )

    styles = getSampleStyleSheet()
    cell = ParagraphStyle(
        "cell", parent=styles["Normal"], fontName="Helvetica", fontSize=7.5, leading=9
    )
    cell_bold = ParagraphStyle(
        "cellb", parent=cell, fontName="Helvetica-Bold"
    )
    header_style = ParagraphStyle(
        "h", parent=cell, fontName="Helvetica-Bold", textColor=colors.white, fontSize=8
    )
    title_style = ParagraphStyle(
        "title", parent=styles["Title"], fontSize=18, spaceAfter=4
    )
    sub_style = ParagraphStyle(
        "sub",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.grey,
        spaceAfter=10,
    )

    story = []
    story.append(Paragraph("HAPI &ndash; Mock-pasienter for kjernejournal-demo", title_style))
    story.append(
        Paragraph(
            "50 fiktive pasienter til bruk i HAPI multi-agent-demo. "
            "Ingen ekte persondata. Skjema: diagnose &rarr; faste medisiner &rarr; allergier/merknader.",
            sub_style,
        )
    )

    headers = [
        Paragraph("ID", header_style),
        Paragraph("Navn", header_style),
        Paragraph("Alder", header_style),
        Paragraph("K/M", header_style),
        Paragraph("Diagnoser", header_style),
        Paragraph("Faste medisiner", header_style),
        Paragraph("Allergier", header_style),
        Paragraph("Klinisk merknad", header_style),
    ]

    rows = [headers]
    for p in PATIENTS:
        rows.append(
            [
                Paragraph(f"<b>{p['id']}</b>", cell),
                Paragraph(p["navn"], cell_bold),
                Paragraph(str(p["alder"]), cell),
                Paragraph(p["kjonn"], cell),
                Paragraph(fmt_diagnoser(p), cell),
                Paragraph(fmt_meds(p), cell),
                Paragraph(fmt_allergier(p), cell),
                Paragraph(p.get("merknader") or "&mdash;", cell),
            ]
        )

    # Kolonnebredder - landskap A4 = 297mm, minus 24mm margin = 273mm
    col_widths = [
        15 * mm,   # ID
        32 * mm,   # Navn
        10 * mm,   # Alder
        8 * mm,    # K/M
        55 * mm,   # Diagnoser
        68 * mm,   # Medisiner
        20 * mm,   # Allergier
        65 * mm,   # Merknader
    ]

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6e6e")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5faf9")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(table)

    # Oppsummering side 2
    story.append(PageBreak())
    story.append(Paragraph("Oppsummering etter kategori", title_style))
    story.append(Spacer(1, 6))

    summary = [
        ["Kategori", "Antall", "Pasient-ID-er"],
        ["Blodfortynnende (warfarin/DOAK)", "10", "P-001 til P-010"],
        ["Diabetes type 2", "8", "P-011 til P-018"],
        ["KOLS", "6", "P-019 til P-024"],
        ["Nyresvikt (CKD)", "5", "P-025 til P-029"],
        ["Astma", "5", "P-030 til P-034"],
        ["Polyfarmasi eldre (5+ medisiner)", "3", "P-035, P-036, P-037"],
        ["Friske / mild sykdom", "13", "P-038 til P-050"],
    ]
    summary_rows = [[Paragraph(c, cell_bold if i == 0 else cell) for c in row] for i, row in enumerate(summary)]
    summary_table = Table(summary_rows, colWidths=[90 * mm, 25 * mm, 80 * mm])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6e6e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5faf9")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(summary_table)

    story.append(Spacer(1, 14))
    story.append(Paragraph("Demo-pasienter med h&oslash;y klinisk verdi", title_style))
    demo = [
        ["ID", "Hvorfor interessant"],
        ["P-001 Kari Nordmann", "Warfarin + atrieflimmer &mdash; klassisk NSAID-kontraindikasjon"],
        ["P-030 Eivind Tangen", "ASA-indusert astma (AERD) &mdash; NSAIDs kan utl&oslash;se alvorlig bronkospasme"],
        ["P-028 K&aring;re Birkeland", "Dobbel kontraindikasjon (CKD stadium 4 + DOAK)"],
        ["P-035 Olav Refsdal", "7 faste medisiner &mdash; komplett multimorbiditet og polyfarmasi"],
        ["P-036 Randi Nesb&oslash;", "87 &aring;r, demens &mdash; STOPP/START-kandidat, antikolinerg byrde"],
        ["P-013 Wenche Moen", "DM2 + CKD &mdash; metformin seponert, trigger full 4-agent-orkestrering"],
        ["P-038/P-050", "Friske kontroller &mdash; viser at systemet ikke overtolker"],
    ]
    demo_rows = [[Paragraph(c, cell_bold if i == 0 else cell) for c in row] for i, row in enumerate(demo)]
    demo_table = Table(demo_rows, colWidths=[55 * mm, 160 * mm])
    demo_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6e6e")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5faf9")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(demo_table)

    # Side 3 - 10 demo-spoersmaal
    story.append(PageBreak())
    story.append(Paragraph("10 demo-sp&oslash;rsm&aring;l &ndash; multi-agent-verdi", title_style))
    story.append(
        Paragraph(
            "Spesielt designet for &aring; vise kjernejournalens verdi kombinert med de &oslash;vrige agentene "
            "(retningslinje, kodeverk, statistikk). Spm 1 er klassikeren, spm 7 aktiverer alle fire, "
            "spm 10 er kontroll uten aktiv pasient.",
            sub_style,
        )
    )

    questions = [
        ["#", "Pasient", "Sp\u00f8rsm\u00e5l", "Agenter", "Demo-verdi"],
        [
            "1",
            "<b>P-001</b> Kari Nordmann<br/><i>warfarin, atrieflimmer</i>",
            "&quot;Pasienten har vondt i kneet etter en falltur. Hva kan hun ta?&quot;",
            "retningslinje + journal",
            "<b>Klassikeren.</b> Generelt svar ville foresl&aring;tt ibux. Personalisert svar advarer mot NSAIDs pga warfarin og anbefaler paracetamol.",
        ],
        [
            "2",
            "<b>P-030</b> Eivind Tangen<br/><i>ASA-indusert astma (AERD)</i>",
            "&quot;Han plages av hodepine. Hva kan han bruke?&quot;",
            "retningslinje + journal",
            "<b>Dramatisk kontraindikasjon.</b> ASA og NSAIDs kan utl&oslash;se alvorlig bronkospasme. Uten journal hadde han f&aring;tt standard r&aring;d om ibuprofen.",
        ],
        [
            "3",
            "<b>P-012</b> Bj&oslash;rn Andersen<br/><i>DM type 2 + fedme</i>",
            "&quot;Han trenger en kur prednisolon for akutt isjias. Noe &aring; tenke p&aring;?&quot;",
            "retningslinje + journal",
            "<b>Steroider &times; diabetes.</b> Krever tett blodsukker-monitorering og evt. midlertidig dosejustering av antidiabetika.",
        ],
        [
            "4",
            "<b>P-025</b> Siri Fossum<br/><i>CKD stadium 4, eGFR 22</i>",
            "&quot;Hun har en ukomplisert UVI. Hva er f&oslash;rstevalg antibiotika?&quot;",
            "retningslinje + kodeverk + journal",
            "<b>Renal dosejustering.</b> F&oslash;rstevalg som nitrofurantoin er kontraindisert ved eGFR &lt;30. Krever alternativ + dosejustering.",
        ],
        [
            "5",
            "<b>P-036</b> Randi Nesb&oslash;<br/><i>87 &aring;r, demens, 6 faste medisiner</i>",
            "&quot;Hjemmesykepleien melder at hun er forvirret og urolig om kveldene. Hva tenker dere?&quot;",
            "retningslinje + journal",
            "<b>Polyfarmasi-vurdering.</b> Antikolinerg byrde + STOPP/START-kriterier i stedet for &aring; automatisk legge til neuroleptika.",
        ],
        [
            "6",
            "<b>P-021</b> Harald R&oslash;nning<br/><i>KOLS GOLD + hypertensjon</i>",
            "&quot;B&oslash;r han starte med betablokker for &aring; roe ned hjertefrekvensen?&quot;",
            "retningslinje + journal",
            "<b>Selektivitet teller.</b> Ikke-selektive betablokkere kontraindisert ved KOLS, men kardioselektive (bisoprolol/metoprolol) er trygge.",
        ],
        [
            "7",
            "<b>P-013</b> Wenche Moen<br/><i>DM2 + CKD stadium 3</i>",
            "&quot;HbA1c er 64 mmol/mol. B&oslash;r jeg starte metformin igjen, hva er ATC-koden, og finnes det nasjonale m&aring;l for HbA1c-kontroll?&quot;",
            "<b>alle 4 agenter</b>",
            "<b>Full orkestrering.</b> Metformin allerede seponert pga eGFR 42 &mdash; alternativer fra retningslinje, ATC fra kodeverk, NKI for HbA1c fra statistikk, journal blokkerer feil anbefaling.",
        ],
        [
            "8",
            "<b>P-016</b> Arild Johansen<br/><i>DM2 + iskemisk hjertesykdom p&aring; Albyl-E</i>",
            "&quot;Han har influensafeber og verker i hele kroppen. Kan han ta ibux noen dager?&quot;",
            "retningslinje + journal",
            "<b>Hjertesykdom + ASA.</b> NSAIDs frar&aring;des ved iskemisk hjertesykdom og kombinert med ASA &oslash;ker bl&oslash;dningsrisiko og motvirker platehemming.",
        ],
        [
            "9",
            "<b>P-003</b> Astrid Berg<br/><i>post-slag p&aring; Xarelto</i>",
            "&quot;Hun skal til tannlegen for tannekstraksjon neste uke. M&aring; vi gj&oslash;re noe med medisinene?&quot;",
            "retningslinje + journal",
            "<b>Periprosedyral DOAK-h&aring;ndtering.</b> Vurdere midlertidig pause vs. bl&oslash;dningsrisiko, koordinering med tannlege &mdash; DOAK krever annen tiln&aelig;rming enn warfarin.",
        ],
        [
            "10",
            "<i>Ingen aktiv pasient</i>",
            "&quot;Hva er ICD-10-koden for KOLS, hva sier retningslinjene om f&oslash;rstelinjebehandling, og finnes det kvalitetsindikatorer for KOLS-oppf&oslash;lging?&quot;",
            "retningslinje + kodeverk + statistikk",
            "<b>Kontroll uten journal.</b> Viser at multi-agent-verdien st&aring;r p&aring; egne ben uten pasient &mdash; og at kjernejournal-agenten ikke &laquo;st&oslash;yer&raquo; n&aring;r det ikke er relevant.",
        ],
    ]

    q_rows = []
    for i, row in enumerate(questions):
        style = header_style if i == 0 else cell
        q_rows.append([Paragraph(c, style) for c in row])

    q_col_widths = [
        8 * mm,    # #
        40 * mm,   # Pasient
        85 * mm,   # Sporsmal
        40 * mm,   # Agenter
        100 * mm,  # Demo-verdi
    ]
    q_table = Table(q_rows, colWidths=q_col_widths, repeatRows=1)
    q_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6e6e")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5faf9")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(q_table)

    story.append(Spacer(1, 10))
    tip_style = ParagraphStyle(
        "tip",
        parent=cell,
        fontSize=8.5,
        leading=11,
        leftIndent=6,
        textColor=colors.HexColor("#333333"),
    )
    story.append(Paragraph("<b>Tips for presentasjon:</b>", cell_bold))
    story.append(
        Paragraph(
            "&bull; Kj&oslash;r sp&oslash;rsm&aring;l <b>#1 f&oslash;rst uten</b> &aring; velge pasient &rarr; f&aring; generelt svar. "
            "Velg deretter P-001 og still det <b>samme</b> sp&oslash;rsm&aring;let &rarr; se den personaliserte advarselen.",
            tip_style,
        )
    )
    story.append(
        Paragraph(
            "&bull; Sp&oslash;rsm&aring;l <b>#7</b> er den mest imponerende fordi det aktiverer hele kjeden.",
            tip_style,
        )
    )
    story.append(
        Paragraph(
            "&bull; Sp&oslash;rsm&aring;l <b>#2 (AERD)</b> og <b>#9 (DOAK + tannlege)</b> er kliniske detaljer som "
            "ofte glipper i daglig praksis &mdash; perfekt for &aring; illustrere &laquo;hadde du tenkt p&aring; dette?&raquo;.",
            tip_style,
        )
    )

    doc.build(story)
    print(f"Skrev {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
