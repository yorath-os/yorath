import pytest

from yorath.distribution.manifest import load_manifest
from yorath.schema import alembic_head
from yorath.settings import get_settings


def test_manifest_loads_and_is_pinned():
    m = load_manifest(get_settings().manifest_path)
    assert m.distribution.name == "yorath"
    assert m.images.ollama.startswith("ollama/ollama:0.")
    assert m.images.postgres.startswith("pgvector/pgvector:pg")
    assert m.runtimes.runsc and m.runtimes.codex_cli


@pytest.mark.covers("INV-DIST-1")
def test_schema_version_matches_alembic_head():
    m = load_manifest(get_settings().manifest_path)
    assert alembic_head() == m.distribution.schema_version


def test_hardware_matrix_from_spike():
    m = load_manifest(get_settings().manifest_path)
    assert m.tier_for(vcpu=1, ram_gb=1).local_mode is False
    assert m.tier_for(vcpu=4, ram_gb=8).tier == "minimum"
    assert m.tier_for(vcpu=8, ram_gb=8).tier == "acceptable"
    assert m.tier_for(vcpu=16, ram_gb=16).tier == "recommended"
    # A10: nvidia-smi 23028 MiB, the installer writes whole GiB: 22, not the 24 on the box
    gpu = m.tier_for(vcpu=18, ram_gb=32, vram_gb=22)
    assert gpu.tier == "gpu" and gpu.model == m.models.default_gpu and gpu.reranking
    assert m.tier_for(vcpu=18, ram_gb=32, vram_gb=8).tier == "recommended"  # too little VRAM: CPU
    assert m.ollama.max_loaded_models == 2 and m.ollama.context_length == 4096


def test_journal_retention_from_manifest():
    m = load_manifest(get_settings().manifest_path)
    assert m.journal.trace_retention_days == 30
