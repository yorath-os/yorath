"""Codex CLI adapter: per-Execution config, JSONL into ``RuntimeEvent``, result from git.

The model goes only through the Model Gateway (decision 21). The token is a file: the environment
would be visible to the agent's child processes, and the command line to ``ps``.
"""

import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from yorath import PRODUCT_NAME
from yorath.runtime.adapters.contract import (
    ExecutionSpec,
    Host,
    PreparedRun,
    ProcessExit,
    RunResult,
    RuntimeEvent,
    RuntimeProbe,
    WorkspaceState,
)

INHERITED_ENV = ("PATH", "HOME", "LANG", "TMPDIR")
TOOL_ITEMS = {"command_execution", "file_change", "mcp_tool_call", "web_search"}


def _toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_config(spec: ExecutionSpec, token_path: Path) -> str:
    return f"""model = {_toml(spec.model)}
model_provider = "yorath"
approval_policy = "never"
sandbox_mode = {_toml(spec.codex_sandbox)}

[sandbox_workspace_write]
network_access = true

[model_providers.yorath]
name = "{PRODUCT_NAME} Model Gateway"
base_url = {_toml(spec.gateway_url.rstrip("/") + "/gateway/v1")}
wire_api = "responses"

[model_providers.yorath.auth]
command = "cat"
args = [{_toml(str(token_path))}]
"""


def parse_line(line: str) -> RuntimeEvent | None:
    """A JSONL line into an event; garbage and unknown types do not stop the run."""
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind == "thread.started":
        return RuntimeEvent("execution.started", {"session_id": raw.get("thread_id")}, raw)
    if kind == "turn.completed":
        return RuntimeEvent("llm.called", {"usage": raw.get("usage") or {}}, raw)
    if kind == "turn.failed":
        message = (raw.get("error") or {}).get("message", "turn.failed")
        return RuntimeEvent("execution.finished", {"failed": True, "error": message}, raw)
    if kind == "error":
        return RuntimeEvent("runtime.warning", {"message": raw.get("message", "")}, raw)
    if kind != "item.completed":
        return None
    return _item(raw.get("item") or {}, raw)


def _item(item: dict[str, Any], raw: dict[str, Any]) -> RuntimeEvent | None:
    kind = item.get("type")
    if kind == "agent_message":
        return RuntimeEvent("agent.message", {"text": item.get("text", "")}, raw)
    if kind == "error":
        return RuntimeEvent("runtime.warning", {"message": item.get("message", "")}, raw)
    if kind in TOOL_ITEMS:
        data = {
            "kind": kind,
            "command": item.get("command"),
            "exit_code": item.get("exit_code"),
            "status": item.get("status"),
        }
        if kind == "file_change":
            data["paths"] = [str(c.get("path", "")) for c in item.get("changes") or []]
        return RuntimeEvent("tool.called", data, raw)
    return None


def _add_usage(total: dict[str, int], usage: dict[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value


async def _git(workspace: Path, *args: str) -> str | None:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(workspace), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    out, _ = await proc.communicate()
    return out.decode(errors="replace").strip() if proc.returncode == 0 else None


async def read_workspace(workspace: Path, base_ref: str = "@{upstream}") -> WorkspaceState:
    """The result is the git state, not the model's words (contract rule 3)."""
    branch = await _git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    log = await _git(workspace, "log", "--format=%H", f"{base_ref}..HEAD")
    status = await _git(workspace, "status", "--porcelain")
    changed = tuple(line[3:] for line in (status or "").splitlines())
    return WorkspaceState(branch=branch, commits=tuple((log or "").split()), changed_files=changed)


class CodexAdapter:
    runtime_type = "codex"

    def __init__(self, binary: str = "codex") -> None:
        self._binary = binary

    async def probe(self) -> RuntimeProbe:
        proc = await asyncio.create_subprocess_exec(
            self._binary, "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"{self._binary} --version: exit code {proc.returncode}")
        return RuntimeProbe(version=out.decode().split()[-1], streams_events=True)

    def prepare(self, spec: ExecutionSpec) -> PreparedRun:
        home = spec.run_dir / "home"
        home.mkdir(parents=True, exist_ok=True)
        token_path = spec.run_dir / "token"
        token_path.touch(mode=0o600)
        token_path.write_text(spec.execution_token, encoding="utf-8")
        (home / "config.toml").write_text(render_config(spec, token_path), encoding="utf-8")
        (home / "AGENTS.md").write_text(spec.instructions, encoding="utf-8")
        output = spec.run_dir / "last-message.md"
        command = [
            self._binary, "exec", "--json", "--ephemeral", "--skip-git-repo-check",
            "--ignore-rules", "--color", "never", "-C", str(spec.workspace),
            "-s", spec.codex_sandbox,
            "-o", str(output), "-",
        ]  # fmt: skip
        env = {k: os.environ[k] for k in INHERITED_ENV if k in os.environ}
        env |= {"CODEX_HOME": str(home), "HOME": str(spec.run_dir)}
        return PreparedRun(spec, command, env, spec.task, output)

    async def run(
        self, prepared: PreparedRun, host: Host, result: RunResult
    ) -> AsyncIterator[RuntimeEvent]:
        state = ProcessExit(exit_code=None)
        failed: str | None = None
        lines = host.run(
            prepared.command, prepared.env, prepared.stdin, prepared.spec.timeout_s, state
        )
        async for line in lines:
            event = parse_line(line)
            if event is None:
                continue
            if event.kind == "execution.started":
                result.session_id = event.data.get("session_id")
            elif event.kind == "llm.called":
                _add_usage(result.usage, event.data["usage"])
            elif event.kind == "agent.message":
                result.summary = event.data["text"]
            elif event.kind == "execution.finished":
                failed = event.data["error"]
            yield event
        result.exit_code = state.exit_code
        if state.timed_out:
            result.status, result.error_message = "timeout", "adapter timeout"
        elif failed or state.exit_code != 0:
            result.status = "failed"
            result.error_message = failed or state.stderr.strip() or f"exit code {state.exit_code}"
        else:
            result.status = "ok"
        yield RuntimeEvent(
            "execution.finished", {"status": result.status, "error": result.error_message}
        )

    async def collect(self, prepared: PreparedRun, result: RunResult) -> RunResult:
        if prepared.output_file.is_file():
            result.summary = (
                prepared.output_file.read_text(encoding="utf-8").strip() or result.summary
            )
        result.workspace = await read_workspace(prepared.spec.workspace)
        return result

    def cleanup(self, prepared: PreparedRun) -> None:
        shutil.rmtree(prepared.spec.run_dir, ignore_errors=True)
