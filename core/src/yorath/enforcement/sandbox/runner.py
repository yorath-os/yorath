"""``sandbox-runner``: the only service with the Docker socket.

A request names only the Execution, the command and the timeout. Image, runtime, network and limits
come from the service settings: a compromised core must not turn into root on the host.
"""

import asyncio
import contextlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ALLOWED_ENV = {"CODEX_HOME", "HOME", "LANG"}
MAX_TIMEOUT_S = 4 * 3600
LABEL = "yorath.sandbox"


class RunnerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YORATH_SANDBOX_", extra="ignore")

    token: str = ""
    image: str = "yorath-sandbox:latest"
    runtime: str = "runsc"
    network: str = "yorath_sandnet"
    dir: Path = Path("/var/lib/yorath/sandbox")
    proxy_url: str = "http://172.30.0.2:3128"
    no_proxy: str = "localhost,127.0.0.1"  # the core is reachable only through the proxy too
    memory: str = "2g"
    cpus: float = 2.0
    pids: int = 512


class RunRequest(BaseModel):
    execution_id: uuid.UUID
    command: list[str] = Field(min_length=1)
    env: dict[str, str] = {}
    timeout_s: float = Field(default=1800, gt=0, le=MAX_TIMEOUT_S)


def container_options(settings: RunnerSettings, data: RunRequest) -> dict[str, Any]:
    """All boundary parameters in one place: the request affects only the command."""
    run_dir = settings.dir / str(data.execution_id)
    env = {k: v for k, v in data.env.items() if k in ALLOWED_ENV}
    env |= {
        "HTTPS_PROXY": settings.proxy_url,
        "HTTP_PROXY": settings.proxy_url,
        "NO_PROXY": settings.no_proxy,
    }
    stdin = run_dir / "stdin"
    return {
        "image": settings.image,
        "command": ["sh", "-c", 'exec "$@" < "$0"', str(stdin), *data.command],
        "runtime": settings.runtime,
        "network": settings.network,
        "environment": env,
        "volumes": {str(run_dir): {"bind": str(run_dir), "mode": "rw"}},
        "working_dir": str(run_dir),
        "mem_limit": settings.memory,
        "nano_cpus": int(settings.cpus * 1e9),
        "pids_limit": settings.pids,
        "security_opt": ["no-new-privileges"],
        "labels": {LABEL: str(data.execution_id)},
        "detach": True,
    }


def _line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


def _container_ip(container: Any, network: str) -> str | None:
    container.reload()
    nets = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    return (nets.get(network) or {}).get("IPAddress") or None


def _remove(container: Any) -> None:
    with contextlib.suppress(Exception):
        container.remove(force=True)


async def _stream(client: Any, settings: RunnerSettings, data: RunRequest) -> AsyncIterator[bytes]:
    try:
        container = await asyncio.to_thread(
            client.containers.run, **container_options(settings, data)
        )
    except Exception as exc:  # running without the sandbox must not happen: only an error report
        yield _line({"exit": 125, "timed_out": False, "error": str(exc)[:500]})
        return
    try:
        ip = await asyncio.to_thread(_container_ip, container, settings.network)
        yield _line({"started": True, "ip": ip})
        logs = iter(container.logs(stream=True, follow=True, stdout=True, stderr=False))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + data.timeout_s
        pending = b""
        timed_out = False
        while True:
            left = deadline - loop.time()
            if left <= 0:
                timed_out = True
                break
            try:
                part = await asyncio.wait_for(asyncio.to_thread(next, logs, None), left)
            except TimeoutError:
                timed_out = True
                break
            if part is None:
                break
            pending += part
            *lines, pending = pending.split(b"\n")
            for raw in lines:
                yield _line({"line": raw.decode(errors="replace")})
        if timed_out:
            await asyncio.to_thread(container.kill)
            yield _line({"exit": 137, "timed_out": True})
            return
        if pending:
            yield _line({"line": pending.decode(errors="replace")})
        status = await asyncio.to_thread(container.wait)
        stderr = await asyncio.to_thread(container.logs, stdout=False, stderr=True)
        yield _line(
            {
                "exit": status.get("StatusCode", 1),
                "timed_out": False,
                "stderr": stderr.decode(errors="replace")[-4000:],
            }
        )
    finally:
        await asyncio.to_thread(_remove, container)


def create_runner(settings: RunnerSettings, client_factory: Callable[[], Any]) -> FastAPI:
    app = FastAPI(title="yorath-sandbox-runner")

    def auth(authorization: str = Header(default="")) -> None:
        scheme, _, token = authorization.partition(" ")
        expected = settings.token
        if not expected or scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
            raise HTTPException(401, detail="sandbox_token_invalid")

    @app.post("/runs", dependencies=[Depends(auth)])
    async def run(data: RunRequest):
        return StreamingResponse(
            _stream(client_factory(), settings, data), media_type="application/x-ndjson"
        )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


def main() -> None:
    import docker
    import uvicorn

    settings = RunnerSettings()
    uvicorn.run(create_runner(settings, docker.from_env), host="0.0.0.0", port=8090)
