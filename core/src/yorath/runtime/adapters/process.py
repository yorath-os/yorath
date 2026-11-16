"""A process on this machine. The adapter owns the timeout and kills the whole group: the runtime is
not trusted."""

import asyncio
import contextlib
import os
import signal
from collections.abc import AsyncIterator

from yorath.runtime.adapters.contract import ProcessExit

STDERR_LIMIT = 4000
LINE_LIMIT = 16 * 1024 * 1024  # a Codex event carries the whole command output


class ProcessHost:
    async def run(
        self,
        command: list[str],
        env: dict[str, str],
        stdin: str,
        timeout_s: float,
        state: ProcessExit,
    ) -> AsyncIterator[str]:
        proc = await asyncio.create_subprocess_exec(
            *command,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=LINE_LIMIT,
        )
        assert proc.stdin and proc.stdout and proc.stderr
        proc.stdin.write(stdin.encode())
        proc.stdin.close()
        stderr = asyncio.create_task(proc.stderr.read())
        deadline = asyncio.get_running_loop().time() + timeout_s
        try:
            while True:
                left = deadline - asyncio.get_running_loop().time()
                if left <= 0:
                    raise TimeoutError
                line = await asyncio.wait_for(proc.stdout.readline(), left)
                if not line:
                    break
                yield line.decode(errors="replace").rstrip("\n")
            await asyncio.wait_for(
                proc.wait(), max(deadline - asyncio.get_running_loop().time(), 0.1)
            )
        except TimeoutError:
            state.timed_out = True
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            state.exit_code = await proc.wait()
            if state.timed_out:
                stderr.cancel()
            else:
                state.stderr = (await stderr).decode(errors="replace")[-STDERR_LIMIT:]
