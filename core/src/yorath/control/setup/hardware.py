"""Node resources and hardware class from the manifest matrix (spike local-model-cpu)."""

import math
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from yorath.distribution.manifest import Manifest
from yorath.settings import Settings

CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
MEMINFO = Path("/proc/meminfo")


@dataclass(frozen=True)
class HardwareInfo:
    vcpu: int
    ram_gb: int
    disk_free_gb: int
    gpu_name: str | None = None
    vram_gb: int | None = None
    gpu_count: int = 0


def cpu_count(cpu_max: str | None = None, host_count: int | None = None) -> int:
    """Cores, taking the cgroup v2 quota into account (``max 100000`` or ``200000 100000``)."""
    host = host_count or os.cpu_count() or 1
    text = cpu_max if cpu_max is not None else (CPU_MAX.read_text() if CPU_MAX.is_file() else "")
    parts = text.split()
    if len(parts) == 2 and parts[0] != "max":
        return max(1, min(host, math.ceil(int(parts[0]) / int(parts[1]))))
    return host


def mem_total_gb(meminfo: str | None = None) -> int:
    text = meminfo if meminfo is not None else (MEMINFO.read_text() if MEMINFO.is_file() else "")
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            kb = int(line.split()[1])
            return max(1, round(kb / 1024 / 1024))
    try:  # macOS and others without /proc: sysconf
        return max(1, round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3))
    except ValueError, OSError, AttributeError:
        return 0


def detect(settings: Settings) -> HardwareInfo:
    return HardwareInfo(
        vcpu=cpu_count(),
        ram_gb=settings.node_ram_gb or mem_total_gb(),
        disk_free_gb=shutil.disk_usage("/").free // (1024**3),
        gpu_name=settings.gpu_name,
        vram_gb=settings.gpu_vram_gb,
        # old installations know only the GPU name: count one card
        gpu_count=settings.gpu_count
        if settings.gpu_count is not None
        else int(bool(settings.gpu_name)),
    )


def assess(manifest: Manifest, hw: HardwareInfo) -> dict[str, Any]:
    tier = manifest.tier_for(hw.vcpu, hw.ram_gb, hw.vram_gb)
    minimum = next(t for t in manifest.models.by_hardware if t.tier == "minimum")
    return {
        "hardware": asdict(hw),
        "tier": tier.model_dump(),
        "local_mode": tier.local_mode,
        "recommended_model": tier.model or manifest.models.default_cpu,
        "embeddings_model": manifest.models.embeddings,
        "expected_answer_seconds": tier.expected_answer_seconds,
        "minimum": {"vcpu": minimum.min_vcpu, "ram_gb": minimum.min_ram_gb},
        "external_model": manifest.models.default_external,
        "limit_gb": manifest.ollama_limit_gb(hw.ram_gb),
    }


def local_window(manifest: Manifest, hw: HardwareInfo) -> int:
    """Local model context window by hardware: on a GPU, external clients' requests are not cut."""
    limits = manifest.ollama
    return limits.context_length_gpu if hw.gpu_name else limits.context_length
