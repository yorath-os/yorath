"""Codex adapter on a fake CLI and JSONL samples from the spike: token, timeout, result from git."""

import stat
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from yorath.runtime.adapters import CodexAdapter, ExecutionSpec, ProcessHost, RunResult
from yorath.runtime.adapters.codex import parse_line

# real `codex exec --json` output
SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "codex"
TOKEN = "yorath_exec_secret"


def fake_cli(tmp_path: Path, body: str) -> str:
    path = tmp_path / "fake-codex"
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def spec(tmp_path: Path, timeout_s: float = 10) -> ExecutionSpec:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    return ExecutionSpec(
        execution_id="e1", instructions="You are a developer.", task="fix the test", model="local/default",
        gateway_url="http://gw:8000/", execution_token=TOKEN, workspace=workspace,
        run_dir=tmp_path / "run", timeout_s=timeout_s,
    )  # fmt: skip


async def drive(adapter, prepared):
    result = RunResult(status="failed")
    events = [e async for e in adapter.run(prepared, ProcessHost(), result)]
    return events, await adapter.collect(prepared, result)


def test_prepare_keeps_token_out_of_command_env_and_workspace(tmp_path):
    prepared = CodexAdapter().prepare(spec(tmp_path))
    assert TOKEN not in " ".join(prepared.command) and TOKEN not in str(prepared.env)
    token = tmp_path / "run" / "token"
    assert token.read_text() == TOKEN and stat.S_IMODE(token.stat().st_mode) == 0o600
    config = (tmp_path / "run" / "home" / "config.toml").read_text()
    assert 'base_url = "http://gw:8000/gateway/v1"' in config and 'wire_api = "responses"' in config
    assert TOKEN not in config
    assert (tmp_path / "run" / "home" / "AGENTS.md").read_text() == "You are a developer."
    assert list((tmp_path / "ws").iterdir()) == []
    assert prepared.stdin == "fix the test" and prepared.command[-1] == "-"


def test_default_sandbox_mode_is_full_access(tmp_path):
    prepared = CodexAdapter().prepare(spec(tmp_path))
    config = (tmp_path / "run" / "home" / "config.toml").read_text()
    assert 'sandbox_mode = "danger-full-access"' in config
    assert prepared.command[prepared.command.index("-s") + 1] == "danger-full-access"


def test_install_setting_selects_workspace_write(tmp_path):
    CodexAdapter().prepare(replace(spec(tmp_path), codex_sandbox="workspace-write"))
    config = (tmp_path / "run" / "home" / "config.toml").read_text()
    assert 'sandbox_mode = "workspace-write"' in config and "network_access = true" in config


def test_prepare_does_not_inherit_secrets_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "leak")
    assert "leak" not in str(CodexAdapter().prepare(spec(tmp_path)).env)


def test_cleanup_is_idempotent(tmp_path):
    adapter = CodexAdapter()
    prepared = adapter.prepare(spec(tmp_path))
    adapter.cleanup(prepared)
    adapter.cleanup(prepared)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(
    ("sample", "kinds"),
    [
        (
            "codex-exec-ok.jsonl",
            {"execution.started", "tool.called", "agent.message", "llm.called"},
        ),
        ("codex-exec-failed.jsonl", {"execution.finished"}),
    ],
)
def test_real_samples_normalize(sample, kinds):
    lines = (SAMPLES / sample).read_text(encoding="utf-8").splitlines()
    events = [e for e in map(parse_line, lines) if e]
    assert kinds <= {e.kind for e in events}


def test_garbage_lines_are_skipped():
    assert parse_line("not json") is None and parse_line("[1]") is None
    assert parse_line('{"type":"new.event"}') is None


async def test_success_sums_usage_and_reads_output_file(tmp_path):
    ok = (SAMPLES / "codex-exec-ok.jsonl").as_posix()
    cli = fake_cli(tmp_path, f'cat {ok}; cat "$0" > /dev/null; exit 0')
    adapter = CodexAdapter(cli)
    prepared = adapter.prepare(spec(tmp_path))
    prepared.output_file.write_text("summary from the file", encoding="utf-8")
    events, result = await drive(adapter, prepared)
    assert (
        result.status == "ok"
        and result.exit_code == 0
        and result.summary == "summary from the file"
    )
    assert result.session_id and result.usage["input_tokens"] > 0
    assert events[-1].kind == "execution.finished"


async def test_turn_failed_with_zero_exit_is_failure(tmp_path):
    line = '{"type":"turn.failed","error":{"message":"the model refused"}}'
    adapter = CodexAdapter(fake_cli(tmp_path, f"echo '{line}'; exit 0"))
    _, result = await drive(adapter, adapter.prepare(spec(tmp_path)))
    assert result.status == "failed" and result.error_message == "the model refused"


async def test_config_error_uses_stderr(tmp_path):
    adapter = CodexAdapter(fake_cli(tmp_path, "echo 'bad config' >&2; exit 1"))
    _, result = await drive(adapter, adapter.prepare(spec(tmp_path)))
    assert result.status == "failed" and "bad config" in result.error_message


async def test_timeout_kills_process_group(tmp_path):
    marker = tmp_path / "child-alive"
    body = f'(sleep 2; touch {marker}) & echo \'{{"type":"turn.started"}}\'; sleep 30'
    adapter = CodexAdapter(fake_cli(tmp_path, body))
    started = time.monotonic()
    _, result = await drive(adapter, adapter.prepare(spec(tmp_path, timeout_s=0.5)))
    assert result.status == "timeout" and time.monotonic() - started < 5
    time.sleep(2.2)
    assert not marker.exists()  # the child process is killed with the group


async def test_workspace_result_comes_from_git_not_model_words(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    git = ["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    (workspace / "a.txt").write_text("a")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    line = '{"type":"item.completed","item":{"type":"agent_message","text":"I made a commit"}}'
    adapter = CodexAdapter(fake_cli(tmp_path, f"echo '{line}'; exit 0"))
    (workspace / "b.txt").write_text("b")
    _, result = await drive(adapter, adapter.prepare(spec(tmp_path)))
    assert result.summary == "I made a commit"
    assert result.workspace.commits == () and result.workspace.changed_files == ("b.txt",)
    assert result.workspace.branch == "main"
