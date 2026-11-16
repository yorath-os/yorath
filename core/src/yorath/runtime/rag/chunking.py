"""Contextual chunking from spike local-model-cpu: the heading path at the start of every chunk."""

import re
from dataclasses import dataclass

HEADING = re.compile(r"^(#{1,4})\s+(.*)")


@dataclass(frozen=True)
class ChunkText:
    heading_path: str
    text: str  # with the [path] prefix


def split_by_headings(text: str) -> list[tuple[str, str]]:
    """Splits Markdown-like text by headings → (heading path, section body)."""
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append((" > ".join(h for _, h in stack), body))
        buf.clear()

    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip().strip("*")
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            buf.append(line)
    flush()
    return sections


def split_text(text: str, chunk_chars: int, overlap: int) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= chunk_chars:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            while len(p) > chunk_chars:
                chunks.append(p[:chunk_chars])
                p = p[chunk_chars - overlap :]
            buf = p
    if buf:
        chunks.append(buf)
    return chunks


def chunk_document(
    filename: str,
    text: str,
    *,
    chunk_chars: int = 900,
    overlap: int = 150,
    pages: int | None = None,
) -> list[ChunkText]:
    """Chunks prefixed with `[file > section]`; a PDF without headings gets the path `p. N`."""
    stem = filename.rsplit(".", 1)[0]
    sections = split_by_headings(text)
    if pages and pages > 1 and all(not path for path, _ in sections):
        parts = [p for p in text.split("\n\n") if p.strip()]
        sections = (
            [(f"p. {i}", body) for i, body in enumerate(parts, start=1)]
            if len(parts) == pages
            else sections
        )
    out: list[ChunkText] = []
    for heading_path, body in sections:
        prefix = stem + (f" > {heading_path}" if heading_path else "")
        for piece in split_text(body, chunk_chars, overlap):
            out.append(ChunkText(heading_path=heading_path, text=f"[{prefix}]\n{piece}"))
    return out
