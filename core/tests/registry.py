"""Invariant registry: identifiers are parsed from the document, there is no copy in Python (R9)."""

import re
from functools import lru_cache
from pathlib import Path

INVARIANTS_DOC = Path(__file__).resolve().parent / "invariants.md"
INVARIANT_RE = re.compile(r"\bINV-[A-Z]+-\d+\b")


@lru_cache(maxsize=1)
def known_ids() -> frozenset[str]:
    if not INVARIANTS_DOC.is_file():
        raise RuntimeError(f"invariant registry not found: {INVARIANTS_DOC}")
    return frozenset(INVARIANT_RE.findall(INVARIANTS_DOC.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def main_ids() -> frozenset[str]:
    """Main domain invariants marked ⭐: a mutation is mandatory for them."""
    text = INVARIANTS_DOC.read_text(encoding="utf-8")
    return frozenset(re.findall(r"\b(INV-[A-Z]+-\d+) ⭐", text))
