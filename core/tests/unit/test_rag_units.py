from yorath.control.model.data import EMBEDDING_DIM, FTS_CONFIG
from yorath.distribution.manifest import load_manifest
from yorath.runtime.rag.chunking import chunk_document, split_by_headings, split_text
from yorath.runtime.rag.embeddings import normalize
from yorath.runtime.rag.fusion import rrf
from yorath.settings import get_settings

MD = """# Vacations

Introduction.

## Carryover

No more than 56 days.

## Request

5 days ahead.
"""


def test_split_by_headings_builds_paths():
    sections = split_by_headings(MD)
    assert [s[0] for s in sections] == ["Vacations", "Vacations > Carryover", "Vacations > Request"]
    assert sections[1][1] == "No more than 56 days."


def test_chunk_document_prefix_and_overlap():
    chunks = chunk_document("hr-policy.md", MD, chunk_chars=900, overlap=150)
    assert chunks[1].text.startswith("[hr-policy > Vacations > Carryover]\n")
    long = "words " * 400  # 2400 chars without paragraphs
    pieces = split_text(long, chunk_chars=900, overlap=150)
    assert len(pieces) >= 3 and all(len(p) <= 900 for p in pieces)
    assert pieces[1].startswith(pieces[0][900 - 150 :][:20])


def test_pdf_pages_get_page_paths():
    text = "page one\n\npage two\n\npage three"
    chunks = chunk_document("scan.pdf", text, pages=3)
    assert [c.heading_path for c in chunks] == ["p. 1", "p. 2", "p. 3"]
    assert chunks[2].text.startswith("[scan > p. 3]")


def test_rrf_prefers_items_in_both_rankings():
    assert rrf([["a", "b", "c"], ["c", "a", "d"]], k=60)[:2] == ["a", "c"]
    assert rrf([[], []]) == []


def test_normalize():
    v = normalize([3.0, 4.0])
    assert abs(v[0] - 0.6) < 1e-9 and abs(v[1] - 0.8) < 1e-9
    assert normalize([0.0, 0.0]) == [0.0, 0.0]


def test_manifest_matches_index_schema():
    m = load_manifest(get_settings().manifest_path)
    assert m.rag.embedding_dim == EMBEDDING_DIM and m.rag.fts_config == FTS_CONFIG
    assert m.rag.chunk_chars == 900 and m.rag.top_k == 5
