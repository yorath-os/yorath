"""`yorath model list`: what fits on this server and what is recommended."""

from yorath.control.setup.hardware import HardwareInfo
from yorath.control.setup.model_cli import model_rows
from yorath.distribution.manifest import load_manifest
from yorath.settings import get_settings

A10 = HardwareInfo(vcpu=18, ram_gb=31, disk_free_gb=90, gpu_name="NVIDIA A10", vram_gb=22)
CPU8 = HardwareInfo(vcpu=8, ram_gb=8, disk_free_gb=90)


def rows_by_name(hw, installed=(), current=None):
    m = load_manifest(get_settings().manifest_path)
    return m, {r.name: r for r in model_rows(m, hw, list(installed), current)}


def test_gpu_server_recommends_gpu_model_first_and_counts_vram():
    m, rows = rows_by_name(A10, installed=["gemma4:e2b"], current="gemma4:e2b")
    first = model_rows(m, A10, ["gemma4:e2b"], "gemma4:e2b")[0]
    assert first.name == "qwen3.5:9b" and first.recommended
    assert rows["qwen3.5:9b"].memory_gb == 5.7 and rows["qwen3.5:9b"].fits is True
    assert rows["gemma4:e2b"].current and rows["gemma4:e2b"].installed
    assert m.models.embeddings not in rows  # embeddings are not the main model


def test_cpu_server_marks_big_model_as_not_fitting():
    _, rows = rows_by_name(CPU8)
    assert rows["gemma4:e2b"].fits is True and rows["gemma4:e2b"].recommended  # anchor
    assert rows["qwen3.5:9b"].fits is False and not rows["qwen3.5:9b"].recommended


def test_installed_model_outside_catalog_is_listed_as_unverified():
    _, rows = rows_by_name(A10, installed=["hf.co/someone/model-abliterated-GGUF:Q4_K_M"])
    row = rows["hf.co/someone/model-abliterated-GGUF:Q4_K_M"]
    assert row.installed and row.measured == "not verified by Yorath" and not row.recommended
