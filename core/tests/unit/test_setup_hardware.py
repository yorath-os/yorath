from yorath.control.setup.hardware import HardwareInfo, assess, cpu_count, mem_total_gb
from yorath.distribution.manifest import load_manifest
from yorath.settings import get_settings


def test_cpu_count_respects_cgroup_quota():
    assert cpu_count("200000 100000", host_count=8) == 2
    assert cpu_count("max 100000", host_count=8) == 8
    assert cpu_count("", host_count=4) == 4
    assert cpu_count("50000 100000", host_count=8) == 1


def test_mem_total_rounds_to_gb():
    assert mem_total_gb("MemTotal:        8123456 kB\nMemFree: 1 kB\n") == 8
    assert mem_total_gb("MemTotal:       16290000 kB\n") == 16


def test_assess_tiers_from_manifest():
    m = load_manifest(get_settings().manifest_path)
    minimum = assess(m, HardwareInfo(vcpu=4, ram_gb=8, disk_free_gb=50))
    assert minimum["tier"]["tier"] == "minimum" and minimum["local_mode"] is True
    assert minimum["recommended_model"] == "gemma4:e2b" and minimum["expected_answer_seconds"] == 19
    assert minimum["embeddings_model"] == m.models.embeddings
    below = assess(m, HardwareInfo(vcpu=2, ram_gb=4, disk_free_gb=50))
    assert below["tier"]["tier"] == "below_minimum" and below["local_mode"] is False
    assert below["external_model"] == m.models.default_external
    gpu = assess(m, HardwareInfo(vcpu=18, ram_gb=32, disk_free_gb=50, gpu_name="A10", vram_gb=22))
    assert gpu["tier"]["tier"] == "gpu" and gpu["recommended_model"] == m.models.default_gpu
