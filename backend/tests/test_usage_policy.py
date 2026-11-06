"""Decision D-14 (PO-7): platform outputs are aggregate decision support and are never used for pay, bonus, promotion,
discipline or individual performance evaluation (docs/USAGE_POLICY.md). Every generated report carries the notice -
JSON metadata, XLSX (metadata row, last summary row, page footer of both sheets), CSV (metadata + last line) and PDF
(text under the title, every page footer, document subject)."""
import csv
import io

from openpyxl import load_workbook

from app.services import reports

DEMO = "admin@demo.local"
P = {"period_start": "2025-11-01", "period_end": "2026-10-31"}
URL = "/api/v1/reports/monthly_hr_performance"


def test_usage_notice_languages():
    assert set(reports.USAGE_NOTICE) == {"tr", "en", "ru"}
    for lang, words in (("tr", ("ücret", "prim", "terfi", "disiplin", "bireysel")),
                        ("en", ("pay", "bonus", "promotion", "discipline", "individual")),
                        ("ru", ("оплаты", "премий", "повышения", "дисциплинарных", "индивидуальной"))):
        text = reports.usage_notice(lang)
        assert all(w in text for w in words), lang
        assert "docs/USAGE_POLICY.md" in text
    assert reports.usage_notice("de") == reports.usage_notice(None) == reports.USAGE_NOTICE["en"]


def test_every_report_format_carries_usage_notice(client, auth):
    h = auth(DEMO)
    j = client.get(URL, params=P, headers=h).json()
    notice = j["metadata"]["usage_policy"]
    assert notice in reports.USAGE_NOTICE.values()

    x = client.get(URL, params={**P, "format": "xlsx"}, headers=h)
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    summary = wb["Summary"]
    cells = [c.value for row in summary.iter_rows() for c in row if c.value]
    assert cells.count(notice) >= 2  # metadata row ("usage_policy", notice) + last summary row
    assert summary.cell(summary.max_row, 1).value == notice
    assert all(wb[s].oddFooter.center.text == notice for s in ("Summary", "By entity"))

    c = client.get(URL, params={**P, "format": "csv"}, headers=h)
    rows = list(csv.reader(io.StringIO(c.text)))
    assert ["# usage_policy", notice] in rows
    assert rows[-1] == ["# " + notice]

    p = client.get(URL, params={**P, "format": "pdf"}, headers=h)
    assert p.status_code == 200 and p.content[:4] == b"%PDF"
    from pypdf import PdfReader

    pdf = PdfReader(io.BytesIO(p.content))
    assert pdf.metadata.subject == notice
    for page in pdf.pages:  # footer on every page
        assert "docs/USAGE_POLICY.md" in page.extract_text()


def test_usage_notice_follows_report_locale():
    rep = {"title": "T", "metadata": {"period_start": "2026-01-01", "period_end": "2026-01-31",
                                      "generated_at": "x", "generated_by": "y", "formula_versions": {},
                                      "usage_policy": reports.usage_notice("ru")},
           "kpis": [], "by_entity": [], "by_entity_columns": []}
    assert list(csv.reader(io.StringIO(reports.to_csv(rep))))[-1] == ["# " + reports.USAGE_NOTICE["ru"]]
    wb = load_workbook(io.BytesIO(reports.to_xlsx(rep)))
    assert wb["Summary"].oddFooter.center.text == reports.USAGE_NOTICE["ru"]
