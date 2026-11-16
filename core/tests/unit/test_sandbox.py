"""Runner on a fake Docker: the boundary does not depend on the request, no container is left."""

import json
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from yorath.enforcement.sandbox.client import SandboxHost
from yorath.enforcement.sandbox.egress import summarize
from yorath.enforcement.sandbox.runner import RunnerSettings, create_runner
from yorath.runtime.adapters import ProcessExit

TOKEN = {"Authorization": "Bearer secret"}
EXECUTION = str(uuid.uuid7())


class FakeContainer:
    def __init__(self, lines=(b'{"a":1}\n',), hang=False):
        self.attrs = {
            "NetworkSettings": {"Networks": {"yorath_sandnet": {"IPAddress": "172.30.0.9"}}}
        }
        self.lines, self.hang = lines, hang
        self.killed = self.removed = False

    def reload(self):
        pass

    def logs(self, stream=False, follow=False, stdout=True, stderr=False):
        if not stream:
            return b"error"
        if self.hang:
            import time

            def forever():
                while not self.killed:
                    time.sleep(0.02)
                    yield b""

            return forever()
        return iter(self.lines)

    def wait(self):
        return {"StatusCode": 0}

    def kill(self):
        self.killed = True

    def remove(self, force=False):
        self.removed = True


class FakeDocker:
    def __init__(self, container=None, error=None):
        self.container, self.error, self.calls = container or FakeContainer(), error, []
        self.containers = self

    def run(self, **options):
        self.calls.append(options)
        if self.error:
            raise self.error
        return self.container


def client(docker, **overrides) -> TestClient:
    settings = RunnerSettings(token="secret", dir=Path("/sbx"), **overrides)
    return TestClient(create_runner(settings, lambda: docker))


def post(api, **body):
    data = {"execution_id": EXECUTION, "command": ["codex", "exec"], **body}
    return api.post("/runs", json=data, headers=TOKEN)


def parsed(r) -> list[dict]:
    return [json.loads(line) for line in r.text.splitlines()]


@pytest.mark.covers("INV-SBX-1")
def test_request_cannot_change_boundary():
    docker = FakeDocker()
    r = post(
        client(docker),
        image="evil",
        runtime="runc",
        volumes={"/": {"bind": "/host"}},
        privileged=True,
        env={"CODEX_HOME": "/h", "OPENAI_API_KEY": "leak", "LD_PRELOAD": "x", "NO_PROXY": "*"},
    )
    assert r.status_code == 200
    options = docker.calls[0]
    assert options["image"] == "yorath-sandbox:latest" and options["runtime"] == "runsc"
    assert options["network"] == "yorath_sandnet" and "privileged" not in options
    assert list(options["volumes"]) == [f"/sbx/{EXECUTION}"]
    assert "/var/run/docker.sock" not in str(options)
    env = options["environment"]
    assert env["CODEX_HOME"] == "/h" and "OPENAI_API_KEY" not in env and "LD_PRELOAD" not in env
    assert env["NO_PROXY"] == "localhost,127.0.0.1" and env["HTTPS_PROXY"].endswith(":3128")
    assert options["pids_limit"] == 512 and options["security_opt"] == ["no-new-privileges"]


@pytest.mark.covers("INV-SBX-2")
def test_runner_requires_token():
    docker = FakeDocker()
    api = client(docker)
    body = {"execution_id": EXECUTION, "command": ["x"]}
    assert api.post("/runs", json=body).status_code == 401
    assert api.post("/runs", json=body, headers={"Authorization": "Bearer no"}).status_code == 401
    assert docker.calls == []
    empty = TestClient(create_runner(RunnerSettings(token=""), lambda: docker))
    assert empty.post("/runs", json=body, headers={"Authorization": "Bearer "}).status_code == 401


def test_path_tricks_are_refused():
    docker = FakeDocker()
    assert post(client(docker), execution_id="../../etc").status_code == 422
    assert docker.calls == []


@pytest.mark.covers("INV-SBX-2")
def test_missing_runtime_reports_error_instead_of_running():
    docker = FakeDocker(error=RuntimeError("unknown or invalid runtime name: runsc"))
    events = parsed(post(client(docker)))
    assert events == [
        {"exit": 125, "timed_out": False, "error": "unknown or invalid runtime name: runsc"}
    ]


@pytest.mark.covers("INV-SBX-3")
def test_lines_stream_and_container_is_removed():
    container = FakeContainer(lines=(b'{"a":1}\n{"b"', b":2}\n"))
    events = parsed(post(client(FakeDocker(container))))
    assert events[0] == {"started": True, "ip": "172.30.0.9"}
    assert [e["line"] for e in events if "line" in e] == ['{"a":1}', '{"b":2}']
    assert events[-1]["exit"] == 0 and container.removed


@pytest.mark.covers("INV-SBX-3")
def test_timeout_kills_and_removes():
    container = FakeContainer(hang=True)
    events = parsed(post(client(FakeDocker(container)), timeout_s=0.2))
    assert events[-1] == {"exit": 137, "timed_out": True}
    assert container.killed and container.removed


@pytest.mark.covers("INV-SBX-4")
def test_egress_summary_is_per_container():
    log = [
        "1.0 5 172.30.0.9 TCP_TUNNEL/200 100 CONNECT github.com:443 - HIER_DIRECT/1.1.1.1 -",
        "1.1 5 172.30.0.9 TCP_TUNNEL/200 100 CONNECT github.com:443 - HIER_DIRECT/1.1.1.1 -",
        "1.2 1 172.30.0.9 TCP_DENIED/403 10 CONNECT example.com:443 - HIER_NONE/- text/html",
        "1.3 1 172.30.0.8 TCP_DENIED/403 10 CONNECT other.org:443 - HIER_NONE/- text/html",
        "1.4 1 172.30.0.9 TCP_DENIED/403 10 GET http://evil.test/x - HIER_NONE/- text/html",
        "1.5 9 172.30.0.9 TCP_MISS/200 900 POST http://core:8000/gateway/v1/responses - HIER_DIRECT/core -",
        "1.6 1 172.30.0.9 TCP_DENIED/403 10 POST http://core:8000/auth/login - HIER_NONE/- text/html",
        "garbage",
    ]
    assert summarize(log, "172.30.0.9") == {
        ("github.com", True): 2,
        ("example.com", False): 1,
        ("evil.test", False): 1,
        ("core", True): 1,
        ("core", False): 1,
    }
    assert summarize(log, "172.30.0.9", since=1.3) == {
        ("evil.test", False): 1,
        ("core", True): 1,
        ("core", False): 1,
    }


async def test_sandbox_host_relays_lines_exit_and_notifications(tmp_path):
    lines = [
        {"started": True, "ip": "172.30.0.9"},
        {"line": "first"},
        {"exit": 3, "timed_out": False, "stderr": "bad"},
    ]
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines) + "\n")

    notes = []

    async def notify(kind, data):
        notes.append((kind, data))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        host = SandboxHost(http, "http://r", "t", uuid.uuid7(), tmp_path, notify)
        state = ProcessExit(exit_code=None)
        got = [x async for x in host.run(["c"], {"HOME": "/h"}, "task", 5, state)]
    assert got == ["first"] and state.exit_code == 3 and state.stderr == "bad"
    assert (tmp_path / "stdin").read_text(encoding="utf-8") == "task"
    assert seen["auth"] == "Bearer t" and host.ip == "172.30.0.9"
    assert [n[0] for n in notes] == ["started", "stopped"]


async def test_runner_refusal_is_failure(tmp_path):
    async def notify(kind, data):
        pass

    transport = httpx.MockTransport(lambda r: httpx.Response(401))
    async with httpx.AsyncClient(transport=transport) as http:
        host = SandboxHost(http, "http://r", "t", uuid.uuid7(), tmp_path, notify)
        state = ProcessExit(exit_code=None)
        assert [x async for x in host.run(["c"], {}, "", 5, state)] == []
    assert state.exit_code == 125 and "401" in state.stderr


def test_egress_allow_list_is_git_and_package_registries():
    """The allow-list is a separate squid file; it holds the Git provider and the npm and PyPI registries, nothing wider."""
    root = Path(__file__).resolve().parents[3] / "installer"
    lines = (root / "egress-allow.txt").read_text(encoding="utf-8").splitlines()
    domains = [line for line in lines if line.strip() and not line.startswith("#")]
    assert domains == [
        ".github.com",
        ".githubusercontent.com",
        "registry.npmjs.org",
        "pypi.org",
        "files.pythonhosted.org",
    ]
    conf = (root / "squid.conf").read_text(encoding="utf-8")
    assert 'acl allowed dstdomain "/etc/squid/egress-allow.txt"' in conf
    assert "http_access deny all" in conf
