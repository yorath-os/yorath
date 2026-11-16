import pytest

from tests.fixtures.corpus import MINIMAL_PDF
from yorath.control.data.parsers import ParseError, parse
from yorath.control.model import DataClass


def test_pdf_text_and_pages(tmp_path):
    p = tmp_path / "policy.pdf"
    p.write_bytes(MINIMAL_PDF)
    parsed = parse(p, "policy.pdf")
    assert "Vacation policy" in parsed.text and parsed.pages == 1


def test_docx_headings_and_tables(tmp_path):
    import docx

    d = docx.Document()
    d.add_heading("Vacations", level=1)
    d.add_paragraph("An employee is entitled to 28 days.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Miller", "28"
    t.cell(1, 0).text, t.cell(1, 1).text = "Parker", "30"
    p = tmp_path / "vac.docx"
    d.save(str(p))
    parsed = parse(p, "vac.docx")
    assert parsed.text.startswith("# Vacations")
    assert "An employee is entitled to 28 days." in parsed.text and "| Miller | 28 |" in parsed.text


def test_markdown_and_errors(tmp_path):
    md = tmp_path / "a.md"
    md.write_text("# Heading\n\ntext", encoding="utf-8")
    assert parse(md, "a.md").text == "# Heading\n\ntext"
    broken = tmp_path / "b.pdf"
    broken.write_bytes(b"not a pdf at all")
    with pytest.raises(ParseError, match="PDF"):
        parse(broken, "b.pdf")
    with pytest.raises(ParseError, match="is not supported"):
        parse(md, "a.xlsx")
    empty = tmp_path / "e.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ParseError, match="empty"):
        parse(empty, "e.txt")


def test_data_class_ranks():
    assert DataClass.PUBLIC.rank < DataClass.INTERNAL.rank < DataClass.CONFIDENTIAL.rank
