"""Runtime contract from the spike: the adapter prepares the run and collects the result.

It is not an Enforcement point: the Gateway checks the model, the sandbox handles network and files.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

RunStatus = Literal["ok", "failed", "timeout", "config_error"]
EventKind = Literal[
    "execution.started",
    "llm.called",
    "tool.called",
    "agent.message",
    "runtime.warning",
    "execution.finished",
]


@dataclass(frozen=True)
class ExecutionSpec:
    execution_id: str
    instructions: str
    task: str
    model: str  # Model Resource name, the Gateway checks it itself
    gateway_url: str
    execution_token: str
    workspace: Path
    run_dir: Path
    timeout_s: float = 1800
    codex_sandbox: str = "danger-full-access"  # the boundary is gVisor (settings.codex_sandbox)


@dataclass(frozen=True)
class RuntimeEvent:
    kind: EventKind
    data: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeProbe:
    version: str
    streams_events: bool


@dataclass
class PreparedRun:
    spec: ExecutionSpec
    command: list[str]
    env: dict[str, str]
    stdin: str
    output_file: Path


@dataclass(frozen=True)
class WorkspaceState:
    branch: str | None = None
    commits: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()


@dataclass
class RunResult:
    status: RunStatus
    exit_code: int | None = None
    error_message: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    summary: str = ""
    workspace: WorkspaceState = field(default_factory=WorkspaceState)
    session_id: str | None = None


@dataclass
class ProcessExit:
    exit_code: int | None
    timed_out: bool = False
    stderr: str = ""


class Host(Protocol):
    """Where the process runs. In production it is the sandbox; ``ProcessHost`` is for development
    and tests."""

    def run(
        self,
        command: list[str],
        env: dict[str, str],
        stdin: str,
        timeout_s: float,
        state: ProcessExit,
    ) -> AsyncIterator[str]: ...


class RuntimeAdapter(Protocol):
    runtime_type: str

    async def probe(self) -> RuntimeProbe: ...

    def prepare(self, spec: ExecutionSpec) -> PreparedRun: ...

    def run(
        self, prepared: PreparedRun, host: Host, result: RunResult
    ) -> AsyncIterator[RuntimeEvent]: ...

    async def collect(self, prepared: PreparedRun, result: RunResult) -> RunResult: ...

    def cleanup(self, prepared: PreparedRun) -> None: ...
