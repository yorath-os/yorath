"""Running chat answers: the Execution lives in a core task, the SSE stream is only a subscription
to it.

State is in process memory: there is one core, the outcome is written to ``executions`` and
``messages``
anyway, and the event buffer is needed only while the answer runs. After a core restart a running
answer
does not continue; ``sweep_interrupted`` closes it.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from yorath.db import utcnow

Event = tuple[str, dict[str, Any]]
STOPPED_BY_USER = "stopped by the user"
STOPPED_BY_CORE = "stopped by the core"


class AnswerInProgress(Exception):
    """An answer is already running in the conversation."""


@dataclass(eq=False)
class Run:
    conversation_id: uuid.UUID
    started_at: datetime = field(default_factory=utcnow)
    events: list[Event] = field(default_factory=list)
    queues: set[asyncio.Queue[Event | None]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    done: bool = False
    stop_reason: str | None = None

    def publish(self, event: Event) -> None:
        # buffer and queues in one synchronous step: a subscriber never misses or repeats an event
        self.events.append(event)
        for queue in self.queues:
            queue.put_nowait(event)

    def close(self) -> None:
        self.done = True
        for queue in self.queues:
            queue.put_nowait(None)

    async def subscribe(self) -> AsyncIterator[Event]:
        """Events from the start of the answer, then live ones to the end. Dropping the subscription
        does not touch the answer."""
        backlog = list(self.events)
        if self.done:
            for event in backlog:
                yield event
            return
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self.queues.add(queue)
        try:
            for event in backlog:
                yield event
            while (queued := await queue.get()) is not None:
                yield queued
        finally:
            self.queues.discard(queue)


class ChatRuns:
    """At most one running answer per conversation."""

    def __init__(self) -> None:
        self._runs: dict[uuid.UUID, Run] = {}

    def get(self, conversation_id: uuid.UUID) -> Run | None:
        return self._runs.get(conversation_id)

    def __contains__(self, conversation_id: object) -> bool:
        return conversation_id in self._runs

    def start(self, conversation_id: uuid.UUID, produce: Callable[[Run], Awaitable[None]]) -> Run:
        if conversation_id in self._runs:
            raise AnswerInProgress
        run = Run(conversation_id)
        self._runs[conversation_id] = run

        async def main() -> None:
            try:
                await produce(run)
            finally:
                # leave the registry first, then end the stream: a question after done gets no 409
                self._runs.pop(conversation_id, None)
                run.close()

        run.task = asyncio.get_running_loop().create_task(main())
        return run

    async def cancel(self, conversation_id: uuid.UUID, reason: str = STOPPED_BY_USER) -> bool:
        """Stops the answer and waits for it to be finalized."""
        run = self._runs.get(conversation_id)
        if run is None or run.task is None:
            return False
        run.stop_reason = reason
        run.task.cancel()
        await asyncio.wait({run.task})
        return True

    async def shutdown(self) -> None:
        for run in list(self._runs.values()):
            if run.task is not None:
                run.stop_reason = STOPPED_BY_CORE
                run.task.cancel()
        tasks = [r.task for r in self._runs.values() if r.task is not None]
        if tasks:
            await asyncio.wait(tasks)


__all__ = ["STOPPED_BY_CORE", "STOPPED_BY_USER", "AnswerInProgress", "ChatRuns", "Run"]
