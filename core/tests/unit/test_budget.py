import importlib.util
from pathlib import Path

import pytest

from yorath.control.setup.budget import Footprint, fits, footprint, params_from_name
from yorath.distribution.manifest import load_manifest
from yorath.settings import get_settings

MANIFEST = load_manifest(get_settings().manifest_path)


def test_known_model_uses_measured_footprint():
    f = footprint("qwen3.5:4b", MANIFEST)
    assert f.rss_gb == 3.8 and not f.estimated and "cache-ram 0" in f.basis


def test_catalog_model_without_rss_is_estimated_from_neighbour():
    f = footprint("qwen3.5:9b", MANIFEST)  # the catalog has the file, rss on CPU is not measured
    assert f.estimated and f.rss_gb is not None and f.rss_gb > 6.3 and "KV as for" in f.basis


def test_unknown_model_with_size_and_without():
    f = footprint("llama3.3:8b", MANIFEST, size_bytes=5 * 1024**3)
    assert (
        f.estimated
        and f.rss_gb == 8.2  # the nearest measured one is gemma4:e2b, 4.6B
        and "KV as for gemma4:e2b" in f.basis
    )
    g = footprint("mystery:latest", MANIFEST)
    assert g.estimated and g.rss_gb is None and "unknown" in g.basis
    h = footprint("mystery:latest", MANIFEST, size_bytes=2 * 1024**3)
    assert h.estimated and h.rss_gb == 4.6


def test_params_from_name():
    assert params_from_name("qwen3.5:4b") == 4.0
    assert params_from_name("gemma4:e2b") == 2.0
    assert params_from_name("embeddinggemma-2:570m") is None


@pytest.mark.covers("INV-DIST-2")
def test_fits_and_limit():
    chat = footprint("gemma4:e2b", MANIFEST)
    emb = footprint("embeddinggemma-2:570m", MANIFEST)
    assert fits(chat, emb, 6) is True and fits(chat, emb, 5) is False
    assert fits(Footprint("x", None, None, True, ""), emb, 7) is None
    assert MANIFEST.ollama_limit_gb(8) == 7 and MANIFEST.ollama_limit_gb(16) == 15
    assert MANIFEST.ollama_limit_gb(32) == 31 and MANIFEST.ollama_limit_gb(None) == 7


@pytest.mark.covers("INV-DIST-2")
def test_render_env_limit_rule():
    spec = importlib.util.spec_from_file_location(
        "render_env", Path(__file__).resolve().parents[3] / "installer" / "render-env.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    raw = {"ollama": {"mem_limit": "7g"}, "models": {"memory": {"reserve_gb": 1}}}
    assert module.ollama_limit(raw, 8) == "7g" and module.ollama_limit(raw, 32) == "31g"
    assert module.ollama_limit(raw, None) == "7g" and module.ollama_limit(raw, 7) == "7g"
