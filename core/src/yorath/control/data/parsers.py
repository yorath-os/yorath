"""Parsing documents into text: pypdf, python-docx, Markdown/text. Docling is Later."""

from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED = {".pdf", ".docx", ".md", ".txt"}
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


@dataclass
class ParsedDocument:
    text: str
    pages: int | None = None
    warnings: list[str] = field(default_factory=list)


class ParseError(ValueError):
    pass


def extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def parse(path: Path, filename: str) -> ParsedDocument:
    ext = extension(filename)
    if ext == ".pdf":
        return _parse_pdf(path)
    if ext == ".docx":
        return _parse_docx(path)
    if ext in {".md", ".txt"}:
        return _parse_text(path)
    raise ParseError(
        f"format {ext or 'without an extension'} is not supported (PDF, DOCX, MD, TXT)"
    )


def _parse_pdf(path: Path) -> ParsedDocument:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except (PyPdfError, ValueError, OSError) as exc:
        raise ParseError(f"PDF cannot be read: {exc}") from exc
    text = "\n\n".join(p for p in pages if p)
    if not text.strip():
        raise ParseError("the PDF has no text layer (a scan?); there is no OCR in the MVP")
    warnings = [f"pages without text: {sum(1 for p in pages if not p)}"] if not all(pages) else []
    return ParsedDocument(text=text, pages=len(pages), warnings=warnings)


def _parse_docx(path: Path) -> ParsedDocument:
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001 — python-docx raises various types
        raise ParseError(f"DOCX cannot be read: {exc}") from exc
    parts: list[str] = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name if paragraph.style is not None else "") or ""
            if style.startswith("Heading"):
                level = "".join(ch for ch in style if ch.isdigit()) or "1"
                parts.append(f"{'#' * min(int(level), 6)} {text}")
            else:
                parts.append(text)
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            for row in table.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                if any(cells):
                    parts.append("| " + " | ".join(cells) + " |")
    text = "\n\n".join(parts)
    if not text.strip():
        raise ParseError("the DOCX has no text")
    return ParsedDocument(text=text)


def _parse_text(path: Path) -> ParsedDocument:
    text = path.read_bytes().decode("utf-8", errors="replace").strip()
    if not text:
        raise ParseError("the file is empty")
    return ParsedDocument(text=text)
