"""Model memory budget (change model-memory-budgets): footprint from the catalog or an estimate.

One rule for the wizard and reports: a known model uses the measured ``rss_gb``; an unknown one
``file_gb × unknown_factor + KV of the nearest by parameters``, flagged ``estimated``.
"""

import re
from dataclasses import dataclass

from yorath.distribution.manifest import Manifest, ModelFootprint

PARAMS_RE = re.compile(r":(\d+(?:\.\d+)?)b\b", re.IGNORECASE)
GB = 1024**3


@dataclass(frozen=True)
class Footprint:
    model: str
    rss_gb: float | None
    vram_gb: float | None
    estimated: bool
    basis: str


def params_from_name(model: str) -> float | None:
    """``qwen3.5:4b`` → 4.0; ``gemma4:e2b`` → 2.0 (e2b means effective 2B); no tag gives None."""
    m = PARAMS_RE.search(model) or re.search(r":e(\d+)b\b", model)
    return float(m.group(1)) if m else None


def nearest(
    catalog: dict[str, ModelFootprint], params_b: float
) -> tuple[str, ModelFootprint] | None:
    """The catalog entry nearest by parameter count with a measured rss."""
    known = [(n, f) for n, f in catalog.items() if f.rss_gb is not None]
    if not known:
        return None
    return min(known, key=lambda item: abs(item[1].params_b - params_b))


def footprint(model: str, manifest: Manifest, size_bytes: int | None = None) -> Footprint:
    catalog = manifest.models.catalog
    known = catalog.get(model)
    if known is not None and known.rss_gb is not None:
        return Footprint(model, known.rss_gb, known.vram_gb, False, known.measured_on)
    params = known.params_b if known else params_from_name(model)
    file_gb = known.file_gb if known else (size_bytes / GB if size_bytes else None)
    neighbour = nearest(catalog, params) if params is not None else None
    if file_gb is not None and neighbour is not None:
        name, f = neighbour
        kv_gb = max(
            0.0, (f.rss_gb or 0) - f.file_gb
        )  # runner growth above weights for the nearest model
        factor = manifest.models.memory.unknown_factor
        basis = f"estimate: file {file_gb:.1f} GB × {factor} + KV as for {name}"
        vram = known.vram_gb if known else None
        return Footprint(model, round(file_gb * factor + kv_gb, 1), vram, True, basis)
    if neighbour is not None:
        name, f = neighbour
        return Footprint(
            model, f.rss_gb, f.vram_gb, True, f"estimate from the nearest model {name}"
        )
    if file_gb is not None:
        rss = round(file_gb * manifest.models.memory.unknown_factor + 2.0, 1)
        return Footprint(
            model, rss, None, True, f"estimate: file {file_gb:.1f} GB, KV unknown (+2 GB)"
        )
    return Footprint(
        model, None, None, True, "footprint unknown: the model is not in the catalog and not pulled"
    )


def fits(chat: Footprint, embeddings: Footprint, limit_gb: int) -> bool | None:
    """Do the chat model and embeddings fit the container limit together; None: nothing to count."""
    if chat.rss_gb is None:
        return None
    return chat.rss_gb + (embeddings.rss_gb or 0) <= limit_gb
