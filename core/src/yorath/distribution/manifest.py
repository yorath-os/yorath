"""``distribution.yaml`` schema (decision 22): one source of versions for Compose and the core."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DistributionInfo(_Strict):
    name: str
    version: str
    schema_version: str = Field(description="Alembic head revision")


class Images(_Strict):
    postgres: str
    ollama: str
    python: str
    node: str
    caddy: str
    squid: str
    ubuntu: str  # base of the yorath-egress node proxy image (change node-egress)


class EgressSettings(_Strict):
    squid: str = "6.14"  # squid-openssl version in the yorath-egress image (change node-egress)


class Runtimes(_Strict):
    codex_cli: str | None = None
    openclaw: str | None = None
    runsc: str | None = None


class ConnectedAgentSpec(_Strict):
    """Catalog connected agent (change connected-agents, decision 34): image and endpoint."""

    repository: str = Field(description="image repository; the node service will not run another")
    version: str = Field(description="tag tested with this Yorath release")
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    port: int = Field(gt=0, lt=65536, description="OpenAI-compatible agent endpoint")
    api_path: str = "/v1/chat/completions"
    health_path: str = "/health"
    model_name: str = Field(description="model name at the agent endpoint")
    min_context: int = Field(default=65536, gt=0, description="minimum model context window")
    address: int = Field(
        ge=10, le=250, description="last address octet in the yorath_agents network"
    )
    state_dir: str = Field(description="state directory in the container, the agent volume")
    user: str | None = Field(default=None, description="uid:gid; None means the image init")
    cap_add: list[str] = []
    memory_mb: int = Field(default=2048, gt=0)
    command: list[str] = []
    license: str = "MIT"
    homepage: str = ""

    def image(self, tag: str | None = None) -> str:
        """A tested version has a digest; a custom one is only a tag of the same repository."""
        if tag is None:
            return f"{self.repository}:{self.version}@{self.digest}"
        return f"{self.repository}:{tag}"


class HardwareTier(_Strict):
    tier: Literal["below_minimum", "minimum", "acceptable", "recommended", "gpu"]
    min_vcpu: int = 0
    min_ram_gb: int = 0
    min_vram_gb: int | None = None
    local_mode: bool
    model: str | None = None
    expected_answer_seconds: float | None = None
    reranking: bool = False
    note: str | None = None


class ModelFootprint(_Strict):
    """Measured model footprint (change model-memory-budgets): runner peak at num_ctx 4096."""

    params_b: float = Field(gt=0, description="parameter count, billions")
    file_gb: float = Field(gt=0, description="model file size in Ollama")
    rss_gb: float | None = Field(default=None, description="peak runner anon-rss on CPU")
    vram_gb: float | None = Field(default=None, description="VRAM footprint on GPU")
    measured_on: str = Field(description="where and when it was measured")


class MemoryPolicy(_Strict):
    reserve_gb: int = Field(
        default=1, ge=0, description="node memory for the core, database, web, OS"
    )
    unknown_factor: float = Field(
        default=1.3, gt=0, description="rss estimate for a model outside the catalog"
    )


class Models(_Strict):
    embeddings: str
    default_cpu: str
    default_gpu: str
    quality_cpu: str | None = None
    default_external: str = Field(description="OpenRouter slug for hybrid mode")
    external_context_max: int = Field(
        default=131072, ge=4096, description="cap of an external model's window from its catalog"
    )
    by_hardware: list[HardwareTier]
    catalog: dict[str, ModelFootprint] = {}
    memory: MemoryPolicy = MemoryPolicy()


class OllamaLimits(_Strict):
    mem_limit: str = Field(
        description="lower bound of the limit; the installer computes it from node RAM"
    )
    keep_alive: str
    max_loaded_models: int = 2
    context_length: int = 4096  # CPU: more makes the runner run out of memory at the 8 GB minimum
    context_length_gpu: int = 65536  # OpenClaw and Hermes need ≥64k (spike connected-agents)


class RagSettings(_Strict):
    """Parameters from spike local-model-cpu: contextual chunking and RRF."""

    chunk_chars: int = 900
    chunk_overlap: int = 150
    top_k: int = 5
    candidates: int = 50
    rrf_k: int = 60
    fts_config: str = "russian"
    embedding_dim: int = 768
    min_similarity: float = (
        0.3  # cosine threshold for vector candidates: otherwise a nearest neighbor always exists
    )


class JournalSettings(_Strict):
    trace_retention_days: int = Field(default=30, ge=1, description="audit is never deleted")


class GatewaySettings(_Strict):
    """Gateway timeouts and the Execution token lifetime for sandbox runtimes
    (mcp-tools-and-gateway)."""

    model_timeout_seconds: float = Field(default=300, gt=0)
    model_first_token_seconds: float = Field(
        default=120, gt=0, description="time to the first model event; then one retry"
    )
    tool_timeout_seconds: float = Field(default=60, gt=0)
    max_output_tokens: int = Field(default=2048, ge=1, description="output limit of one call")
    retry_delay_seconds: float = Field(default=3, ge=0, description="pause before retrying a call")
    model_queue_max: int = Field(
        default=100, ge=1, description="calls waiting for one local model; more is queue_full"
    )
    model_queue_wait_seconds: float = Field(
        default=120, gt=0, description="wait for a free model server; then queue_timeout"
    )
    execution_token_ttl_minutes: int = Field(default=120, ge=1)


class AgentLoopSettings(_Strict):
    """Limits of the native agent loop (change agent-loop-native)."""

    max_steps: int = Field(default=6, ge=1, description="a step is one model call")
    timeout_seconds: float = Field(default=600, gt=0)
    first_step_nudge: bool = Field(
        default=True, description="one retry if the model answered without tools"
    )


class Manifest(_Strict):
    distribution: DistributionInfo
    images: Images
    runtimes: Runtimes
    egress: EgressSettings = EgressSettings()
    models: Models
    ollama: OllamaLimits
    journal: JournalSettings = JournalSettings()
    rag: RagSettings = RagSettings()
    gateway: GatewaySettings = GatewaySettings()
    agent_loop: AgentLoopSettings = AgentLoopSettings()
    connected_agents: dict[str, ConnectedAgentSpec] = {}

    @model_validator(mode="after")
    def _connected_addresses_unique(self) -> Manifest:
        seen: dict[int, str] = {}
        for name, spec in self.connected_agents.items():
            if spec.address in seen:
                raise ValueError(
                    f"connected_agents: {name} and {seen[spec.address]} share an address"
                )
            seen[spec.address] = name
        return self

    def ollama_limit_gb(self, ram_gb: int | None) -> int:
        """Ollama container limit: node RAM minus the reserve, but not below
        ``ollama.mem_limit``."""
        floor = int(str(self.ollama.mem_limit).lower().rstrip("gb") or 0)
        if not ram_gb:
            return floor
        return max(floor, ram_gb - self.models.memory.reserve_gb)

    def tier_for(self, vcpu: int, ram_gb: int, vram_gb: int | None = None) -> HardwareTier:
        """Hardware class for the wizard: the best tier whose minimums are met."""
        best = self.models.by_hardware[0]
        for tier in self.models.by_hardware:
            if tier.min_vram_gb is not None and (vram_gb or 0) < tier.min_vram_gb:
                continue
            if vcpu >= tier.min_vcpu and ram_gb >= tier.min_ram_gb:
                best = tier
        return best


def load_manifest(path: Path) -> Manifest:
    with path.open(encoding="utf-8") as f:
        return Manifest.model_validate(yaml.safe_load(f))
