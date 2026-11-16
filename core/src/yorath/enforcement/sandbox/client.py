"""``SandboxHost``: the adapter ``Host`` on top of ``sandbox-runner``. Start and stop are audited
here."""

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from yorath.runtime.adapters.contract import ProcessExit

Notify = Callable[[str, dict], Awaitable[None]]


@dataclass
class SandboxHost:
    http: httpx.AsyncClient
    url: str
    token: str
    execution_id: uuid.UUID
    run_dir: Path
    notify: Notify
    ip: str | None = None

    async def run(
        self,
        command: list[str],
        env: dict[str, str],
        stdin: str,
        timeout_s: float,
        state: ProcessExit,
    ) -> AsyncIterator[str]:
        (self.run_dir / "stdin").write_text(stdin, encoding="utf-8")
        body = {
            "execution_id": str(self.execution_id),
            "command": command,
            "env": env,
            "timeout_s": timeout_s,
        }
        headers = {"Authorization": f"Bearer {self.token}"}
        timeout = httpx.Timeout(10, read=timeout_s + 60)
        async with self.http.stream(
            "POST", f"{self.url}/runs", json=body, headers=headers, timeout=timeout
        ) as r:
            if r.status_code != 200:
                state.exit_code, state.stderr = 125, f"sandbox-runner: HTTP {r.status_code}"
                return
            async for raw in r.aiter_lines():
                msg = json.loads(raw)
                if "line" in msg:
                    yield msg["line"]
                elif msg.get("started"):
                    self.ip = msg.get("ip")
                    await self.notify("started", {"ip": self.ip})
                elif "exit" in msg:
                    state.exit_code = msg["exit"]
                    state.timed_out = bool(msg.get("timed_out"))
                    state.stderr = msg.get("stderr") or msg.get("error") or ""
        await self.notify("stopped", {"exit_code": state.exit_code, "timed_out": state.timed_out})
