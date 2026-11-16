"""Which model server takes a call, and who waits (decision 44, changes model-placements and
model-queue).

A model server has slots (one today): a call goes to a server only when a slot is free, so it
starts at once. Otherwise it waits here, in the queue of its model on the main node, and takes
the first slot that frees on any ready placement of that model. The queue is served round-robin
between participants (the effective principal of the call) and first-come within one participant,
so a burst from one client does not hold everybody else.

Everything lives in the core's memory: a core restart drops the waiting calls together with their
connections (decision 44; a durable queue is Later).
"""

import asyncio
import time
import uuid
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.cluster.placements import Candidate, candidates
from yorath.control.model import ModelProvider, ModelResource
from yorath.control.setup.ollama import LOCAL_EMBEDDINGS
from yorath.db import utcnow
from yorath.enforcement.gateway.errors import GatewayError

REFRESH_SECONDS = 1.0  # while somebody waits, placements are re-read this often
MAIN_SUSPECT_SECONDS = 30.0  # the main node has no heartbeat to clear a failed server


@dataclass(frozen=True)
class Lease:
    """Where the call goes: ``url`` is None for a provider that is not a model server."""

    url: str | None = None
    server: str | None = None  # node/server, for the trace
    server_id: uuid.UUID | None = None
    waited_ms: int = 0
    ahead: int = 0  # calls that were waiting in front of this one


def choose(
    free: list[Candidate], participant: str, busy: dict[uuid.UUID, int], last: dict[uuid.UUID, str]
) -> Candidate:
    """Among servers with a free slot: the one that served this participant last (its prompt
    cache is warm), otherwise the least busy; the first one on a tie."""
    for candidate in free:
        if last.get(candidate.server_id) == participant:
            return candidate
    return min(free, key=lambda c: busy.get(c.server_id, 0) / max(1, c.slots))


@dataclass(eq=False)  # a place in the line is this very call, not an equal-looking one
class Waiter:
    participant: str
    future: asyncio.Future[Candidate]


@dataclass
class ModelQueue:
    """Waiting calls of one model: a line per participant and the round-robin order."""

    lines: dict[str, deque[Waiter]] = field(default_factory=dict)
    ring: deque[str] = field(default_factory=deque)
    ready: list[Candidate] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    refreshed_at: float = 0.0

    def __len__(self) -> int:
        return sum(len(line) for line in self.lines.values())

    def push(self, waiter: Waiter, *, front: bool = False) -> None:
        line = self.lines.get(waiter.participant)
        if line is None:
            line = self.lines[waiter.participant] = deque()
            self.ring.append(waiter.participant)
        if front:
            line.appendleft(waiter)
        else:
            line.append(waiter)

    def pop_next(self) -> Waiter | None:
        """The first call of the next participant in the ring; that participant goes to the end."""
        if not self.ring:
            return None
        participant = self.ring.popleft()
        line = self.lines[participant]
        waiter = line.popleft()
        if line:
            self.ring.append(participant)
        else:
            del self.lines[participant]
        return waiter

    def remove(self, waiter: Waiter) -> bool:
        line = self.lines.get(waiter.participant)
        if line is None or waiter not in line:
            return False
        line.remove(waiter)
        if not line:
            del self.lines[waiter.participant]
            self.ring.remove(waiter.participant)
        return True


class Reservation:
    """A place for one call: a slot at once (``lease`` is set) or a place in the queue.

    ``release`` is called exactly once by the owner, whatever happened: it frees the slot or
    leaves the queue, and hands a slot that was granted meanwhile to the next waiter.
    """

    def __init__(
        self, router: SlotRouter, model: ModelResource, participant: str,
        lease: Lease | None, waiter: Waiter | None = None, ahead: int = 0,
    ) -> None:  # fmt: skip
        self._router, self._model, self._participant = router, model, participant
        self.lease = lease
        self.ahead = ahead
        self._waiter = waiter
        self._taken: uuid.UUID | None = lease.server_id if lease else None
        self._released = False

    async def wait(self) -> Lease:
        """Waits for a slot; ``queue_timeout`` after the limit, ``model_unavailable`` when the
        model lost its last ready placement while waiting."""
        if self.lease is not None:
            return self.lease
        assert self._waiter is not None
        started = time.monotonic()
        chosen = await self._router._wait(self._model, self._waiter, started)  # noqa: SLF001
        self._taken = chosen.server_id
        self.lease = Lease(
            url=chosen.url, server=chosen.label, server_id=chosen.server_id,
            waited_ms=int((time.monotonic() - started) * 1000), ahead=self.ahead,
        )  # fmt: skip
        return self.lease

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        router = self._router
        if self._waiter is not None and self._taken is None:
            queue = router._queues.get(self._model.id)  # noqa: SLF001
            if queue is not None:
                queue.remove(self._waiter)
            future = self._waiter.future
            if future.done() and not future.cancelled() and future.exception() is None:
                self._taken = future.result().server_id  # granted while we were leaving
            elif not future.done():
                future.cancel()
        if self._taken is not None:
            router._free(self._taken, self._model.id)  # noqa: SLF001


class SlotRouter:
    def __init__(
        self,
        sessions: Callable[[], AsyncSession],
        ollama_url: Callable[[], str],
        *,
        max_waiting: int = 100,
        wait_seconds: float = 120.0,
    ) -> None:
        self._sessions = sessions
        self._ollama_url = ollama_url  # read on every call: the main node's Ollama address
        # limits from the manifest (gateway.model_queue_max, gateway.model_queue_wait_seconds)
        self.max_waiting = max_waiting
        self.wait_seconds = wait_seconds
        self._busy: Counter[uuid.UUID] = Counter()
        self._last: dict[uuid.UUID, str] = {}  # server → the participant it served last
        self._suspect: dict[uuid.UUID, datetime] = {}  # server → when a call failed to reach it
        self._queues: dict[uuid.UUID, ModelQueue] = {}

    # --- what the panel shows ---------------------------------------------------------------

    def in_flight(self) -> dict[uuid.UUID, int]:
        return {k: v for k, v in self._busy.items() if v}

    def waiting(self) -> dict[uuid.UUID, int]:
        return {model_id: len(q) for model_id, q in self._queues.items() if len(q)}

    # --- one call ---------------------------------------------------------------------------

    async def reserve(
        self, model: ModelResource, participant: str, *, front: bool = False
    ) -> Reservation:
        """A slot now or a place in the model's queue. ``model`` is the catalog model, never a
        pointer. No ready placement is a denial with the reasons: the call never goes to another
        model. ``front`` puts a retried call first among its participant's calls."""
        if model.provider is not ModelProvider.OLLAMA:
            return Reservation(self, model, participant, Lease())
        if model.name == LOCAL_EMBEDDINGS:  # embeddings live in the main node's Ollama
            return Reservation(self, model, participant, Lease(url=self._ollama_url()))
        queue = self._queues.setdefault(model.id, ModelQueue())
        await self._refresh(model, queue, force=True)
        if not queue.ready:
            raise self._unavailable(model, queue)
        if len(queue) == 0:  # nobody waits: a free slot is taken without the queue
            free = self._free_servers(queue)
            if free:
                chosen = self._grant(free, participant)
                lease = Lease(url=chosen.url, server=chosen.label, server_id=chosen.server_id)
                return Reservation(self, model, participant, lease)
        ahead = len(queue)
        if ahead >= self.max_waiting:
            raise GatewayError(
                "queue_full",
                f"model {model.name} already has {ahead} calls waiting: try again later",
            )
        waiter = Waiter(participant, asyncio.get_running_loop().create_future())
        queue.push(waiter, front=front)
        self._dispatch(queue)  # a slot may have been freed between the checks
        return Reservation(self, model, participant, None, waiter, ahead)

    def mark_failed(self, lease: Lease) -> None:
        """The call could not reach the server: it is out of the choice until its node reports
        again (a main node server: for a short time)."""
        if lease.server_id is None:
            return
        self._suspect[lease.server_id] = utcnow()
        for queue in self._queues.values():
            queue.ready = [c for c in queue.ready if c.server_id != lease.server_id]

    # --- internals --------------------------------------------------------------------------

    def _unavailable(self, model: ModelResource, queue: ModelQueue) -> GatewayError:
        why = "; ".join(queue.reasons) if queue.reasons else "it is not placed on any model server"
        return GatewayError("model_unavailable", f"model {model.name} is unavailable: {why}")

    def _suspected(self, candidate: Candidate) -> bool:
        since = self._suspect.get(candidate.server_id)
        if since is None:
            return False
        if candidate.main:
            cleared = (utcnow() - since).total_seconds() > MAIN_SUSPECT_SECONDS
        else:
            cleared = candidate.seen_at is not None and candidate.seen_at > since
        if cleared:
            del self._suspect[candidate.server_id]
        return not cleared

    async def _refresh(self, model: ModelResource, queue: ModelQueue, *, force: bool) -> None:
        now = time.monotonic()
        if not force and now - queue.refreshed_at < REFRESH_SECONDS / 2:
            return
        async with self._sessions() as s:
            ready, reasons = await candidates(s, self._ollama_url(), model)
        kept = []
        for candidate in ready:
            if self._suspected(candidate):
                reasons.append(f"{candidate.label} did not answer")
            else:
                kept.append(candidate)
        queue.ready, queue.reasons, queue.refreshed_at = kept, reasons, now

    def _free_servers(self, queue: ModelQueue) -> list[Candidate]:
        return [c for c in queue.ready if self._busy[c.server_id] < c.slots]

    def _grant(self, free: list[Candidate], participant: str) -> Candidate:
        chosen = choose(free, participant, self._busy, self._last)
        self._busy[chosen.server_id] += 1
        self._last[chosen.server_id] = participant
        return chosen

    def _dispatch(self, queue: ModelQueue) -> None:
        """Hands free slots to the waiting calls, the next participant of the ring first."""
        while len(queue):
            free = self._free_servers(queue)
            if not free:
                return
            waiter = queue.pop_next()
            if waiter is None:
                return
            if waiter.future.done():  # its owner left while it was in the line
                continue
            waiter.future.set_result(self._grant(free, waiter.participant))

    def _free(self, server_id: uuid.UUID, model_id: uuid.UUID) -> None:
        self._busy[server_id] -= 1
        queue = self._queues.get(model_id)
        if queue is not None:
            self._dispatch(queue)

    async def _wait(self, model: ModelResource, waiter: Waiter, started: float) -> Candidate:
        queue = self._queues[model.id]
        deadline = started + self.wait_seconds
        while True:
            if waiter.future.done():  # granted while placements were being re-read
                return waiter.future.result()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GatewayError(
                    "queue_timeout",
                    f"no model server of {model.name} got free in {self.wait_seconds:.0f} s",
                )
            try:
                return await asyncio.wait_for(
                    asyncio.shield(waiter.future), min(REFRESH_SECONDS, remaining)
                )
            except TimeoutError:
                # a node may have gone or come back while we wait
                await self._refresh(model, queue, force=False)
                if not queue.ready:
                    raise self._unavailable(model, queue) from None
                self._dispatch(queue)
