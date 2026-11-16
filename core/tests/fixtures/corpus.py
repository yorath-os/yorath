"""Test data: documents, reference answers, sample files. No core logic."""

import hashlib
import re
from pathlib import Path

DIM = 768


def fake_vector(text: str) -> list[float]:
    """Bag of words via token hashes: cosine grows with the number of shared words, no model."""
    raw = [0.0] * DIM
    for token in set(re.findall(r"\w+", text.lower())):
        raw[int(hashlib.sha256(token.encode()).hexdigest(), 16) % DIM] = 1.0
    norm = sum(x * x for x in raw) ** 0.5 or 1.0
    return [x / norm for x in raw]


POLICY_MD = """# Vacation policy

## Carryover

You can carry over no more than 56 days, the rest expires on December 31.

## Request

Submit a request 14 days ahead; for a vacation of up to 3 days, 5 calendar days ahead.
"""


SECRET_MD = "# Salaries\n\nMiller's salary is 350000 dollars.\n"


OWNER = {"login": "alex", "password": "owner-pass-1", "display_name": "Alex"}


AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"


REPLY = ["You can carry over ", "no more than 56 days ", "[1]."]


NO_HITS_REPLY = "The documents do not answer this question."


def build_pdf(text: str) -> bytes:
    """A minimal valid one-page PDF with text (with an xref table, pypdf is strict)."""
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


MINIMAL_PDF = build_pdf("Vacation policy: 28 days")


VALID = """---
name: document-assistant
description: Answers questions about documents
runtime: native
model: local/default
mode: delegated
tools:
  - search_docs
  - ask_agent: [research-agent]
data: [team-docs]
max_data_class: confidential
triggers: [chat]
x-a2a-skill: faq
---
Body: instructions for the agent.
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p
