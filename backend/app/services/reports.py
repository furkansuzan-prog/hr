"""Reporting engine (section 13). Every report carries: period, filters, data sources, calculation time,
formula versions and data-quality information. All numbers come from the KPI engine.
Decision D-14 (docs/USAGE_POLICY.md): every generated report - JSON metadata, XLSX (summary row + page footer), CSV
(metadata + last line) and PDF (under the title + every page footer) - carries the usage-policy notice."""
import csv
import io
import os
from datetime import UTC, date, datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy.orm import Session

from app.analytics import engine
from app.security.access import AccessContext

REPORTS = {
    "monthly_hr_performance": {
        "name": {"tr": "Aylık İK Performans Raporu", "en": "Monthly HR Performance Report", "ru": "Ежемесячный отчёт HR"},
        "kpis": ["KPI-016", "KPI-017", "KPI-001", "KPI-002", "KPI-018", "KPI-003", "KPI-007", "KPI-008", "KPI-009",
                 "KPI-011", "KPI-014A", "KPI-014B", "KPI-019"],
        "by_entity": ["KPI-016", "KPI-001", "KPI-003", "KPI-007"],
        "status": "available",
    },
    "subcontractor_workforce": {
        "name": {"tr": "Alt Yüklenici İş Gücü Raporu", "en": "Subcontractor Workforce Report", "ru": "Отчёт по субподрядчикам"},
        "kpis": ["KPI-016", "KPI-001", "KPI-007", "KPI-009", "KPI-014A"],
        "by_entity": ["KPI-016", "KPI-001", "KPI-007", "KPI-009", "KPI-014A"],
        "entity_types": ["subcontractor"],
        "status": "available",
    },
}
# Decision D-14 / docs/USAGE_POLICY.md - same wording as the UI notice (frontend i18n key usagePolicy.notice)
USAGE_NOTICE = {
    "tr": "Kullanım politikası: Bu çıktı toplu (grup düzeyi) karar desteğidir; ücret, prim, terfi, disiplin veya "
          "bireysel performans değerlendirmesinde kullanılamaz (docs/USAGE_POLICY.md).",
    "en": "Usage policy: this output is aggregate (group-level) decision support; it must not be used for pay, bonus, "
          "promotion, discipline or individual performance evaluation (docs/USAGE_POLICY.md).",
    "ru": "Политика использования: это агрегированная (групповая) поддержка решений; не используется для оплаты, "
          "премий, повышения, дисциплинарных мер или индивидуальной оценки эффективности (docs/USAGE_POLICY.md).",
}


def usage_notice(locale: str | None) -> str:
    return USAGE_NOTICE.get(locale or "en", USAGE_NOTICE["en"])


PLANNED = ["daily_workforce_summary", "weekly_movements", "turnover_analysis", "productivity_analysis",
           "project_capacity", "labour_cost_analysis", "training_competency", "workforce_sustainability",
           "annual_human_capital", "risk_improvement"]


def catalog(locale: str) -> list[dict]:
    out = [{"code": k, "name": v["name"].get(locale, v["name"]["en"]), "status": v["status"],
            "formats": ["xlsx", "csv", "pdf"]} for k, v in REPORTS.items()]
    out += [{"code": c, "name": c.replace("_", " ").title(), "status": "planned", "formats": []} for c in PLANNED]
    return out


def build(db: Session, ctx: AccessContext, code: str, ps: date, pe: date, *, entity_ids: set[int] | None = None,
          project_id: int | None = None) -> dict:
    spec = REPORTS.get(code)
    if spec is None:
        raise KeyError(code)
    kpis, skipped = [], []
    for c in spec["kpis"]:
        d = engine.get_definition(db, c)
        if d.sensitive and not ctx.has("cost.read"):
            skipped.append({"code": c, "reason": "requires cost.read"})
            continue
        kpis.append(engine.evaluate(db, ctx, c, ps, pe, entity_ids=entity_ids, project_id=project_id))
    entity_rows: dict[int, dict] = {}
    for c in spec["by_entity"]:
        if engine.get_definition(db, c).sensitive and not ctx.has("cost.read"):
            continue
        for r in engine.by_entity(db, ctx, c, ps, pe, project_id=project_id):
            le = r["legal_entity"]
            if entity_ids and le["id"] not in entity_ids:
                continue
            if spec.get("entity_types") and le["type"] not in spec["entity_types"]:
                continue
            row = entity_rows.setdefault(le["id"], {"entity": le["name"], "type": le["type"]})
            row[c] = r["value"] if r["status"] == "ok" else r["status"]
    return {
        "report": code, "title": spec["name"].get(ctx.locale, spec["name"]["en"]),
        "metadata": {
            "period_start": ps.isoformat(), "period_end": pe.isoformat(),
            "filters": {"entity_ids": sorted(entity_ids) if entity_ids else "all permitted",
                        "project_id": project_id},
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "generated_by": ctx.email,
            "data_sources": sorted({t for k in kpis for t in (k.get("lineage") or {}).get("tables", [])}),
            "formula_versions": {k["code"]: k["formula"]["version"] for k in kpis},
            "omitted_kpis": skipped,
            "data_quality": {k["code"]: {"status": k["status"], "quality_score": k["quality_score"]} for k in kpis},
            "notice": "Generated from synthetic demo data" if os.getenv("HRSWI_DEMO_DATA", "true") == "true" else None,
            "usage_policy": usage_notice(ctx.locale),
        },
        "kpis": [{"code": k["code"], "name": k["name"], "value": k["value"], "unit": k["unit"], "status": k["status"],
                  "numerator": k["numerator"], "denominator": k["denominator"], "sample_size": k["sample_size"],
                  "quality_score": k["quality_score"], "formula_version": k["formula"]["version"], "note": k["note"]}
                 for k in kpis],
        "by_entity": list(entity_rows.values()),
        "by_entity_columns": [c for c in spec["by_entity"] if not (engine.get_definition(db, c).sensitive
                                                                    and not ctx.has("cost.read"))],
    }


def to_xlsx(rep: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    bold = Font(bold=True)
    head = PatternFill("solid", fgColor="1F3A5F")
    ws.append([rep["title"]])
    ws["A1"].font = Font(bold=True, size=14)
    for k, v in rep["metadata"].items():
        ws.append([k, str(v)])
    ws.append([])
    cols = ["code", "name", "value", "unit", "status", "numerator", "denominator", "sample_size", "quality_score",
            "formula_version", "note"]
    ws.append(cols)
    for c in ws[ws.max_row]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head
    for k in rep["kpis"]:
        ws.append([k.get(c) for c in cols])
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 42
    ws2 = wb.create_sheet("By entity")
    ecols = ["entity", "type"] + rep["by_entity_columns"]
    ws2.append(ecols)
    for c in ws2[1]:
        c.font = bold
    for r in rep["by_entity"]:
        ws2.append([r.get(c) for c in ecols])
    ws2.column_dimensions["A"].width = 40
    notice = rep["metadata"].get("usage_policy")
    if notice:  # D-14: metadata row above + last summary row + printed page footer on both sheets
        ws.append([])
        ws.append([notice])
        ws.cell(ws.max_row, 1).font = Font(italic=True)
        for sheet in (ws, ws2):
            sheet.oddFooter.center.text = notice
            sheet.oddFooter.center.size = 7
    for row in ws.iter_rows():
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=False)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_csv(rep: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# " + rep["title"]])
    for k, v in rep["metadata"].items():
        w.writerow(["# " + k, v])
    cols = ["code", "name", "value", "unit", "status", "sample_size", "quality_score", "formula_version"]
    w.writerow(cols)
    for k in rep["kpis"]:
        w.writerow([k.get(c) for c in cols])
    if rep["metadata"].get("usage_policy"):  # D-14: also the last line, so a truncated paste keeps it visible
        w.writerow(["# " + rep["metadata"]["usage_policy"]])
    return buf.getvalue()


def _font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont("DejaVu", path))
            return "DejaVu"
    return "Helvetica"


def to_pdf(rep: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font = _font()
    styles = getSampleStyleSheet()
    for s in styles.byName.values():
        s.fontName = font
    buf = io.BytesIO()
    md = rep["metadata"]
    notice = md.get("usage_policy")
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), title=rep["title"], subject=notice or "")
    story = [Paragraph(rep["title"], styles["Title"]),
             Paragraph(f"{md['period_start']} – {md['period_end']} · {md['generated_at']} · {md['generated_by']}",
                       styles["Normal"])]
    if md.get("notice"):
        story.append(Paragraph(f"<b>{md['notice']}</b>", styles["Normal"]))
    if notice:
        story.append(Paragraph(f"<i>{notice}</i>", styles["Normal"]))
    story.append(Spacer(1, 10))

    def fmt(v):
        return "—" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v))

    data = [["Code", "KPI", "Value", "Unit", "Status", "n", "Quality", "Formula v."]]
    data += [[k["code"], Paragraph(k["name"], styles["Normal"]), fmt(k["value"]), k["unit"], k["status"],
              fmt(k["sample_size"]), fmt(k["quality_score"]), str(k["formula_version"])] for k in rep["kpis"]]
    t = Table(data, repeatRows=1, colWidths=[60, 250, 70, 60, 90, 50, 50, 55])
    t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 8),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [t, Spacer(1, 12)]
    if rep["by_entity"]:
        cols = ["entity"] + rep["by_entity_columns"]
        ed = [cols] + [[Paragraph(str(r.get("entity")), styles["Normal"])] + [fmt(r.get(c)) for c in cols[1:]]
                       for r in rep["by_entity"]]
        t2 = Table(ed, repeatRows=1)
        t2.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 7),
                                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF5"))]))
        story.append(t2)
    story.append(Spacer(1, 8))
    story.append(Paragraph("Formula versions: " + ", ".join(f"{k} v{v}" for k, v in md["formula_versions"].items()),
                           styles["Normal"]))
    def footer(canvas, _doc):  # D-14: on every page
        if notice:
            canvas.saveState()
            canvas.setFont(font, 7)
            canvas.drawCentredString(_doc.pagesize[0] / 2, 18, notice)
            canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
