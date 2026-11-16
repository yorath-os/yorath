"""Queue of calls to a local model in the Gateway (decision 44, change model-queue).

A call goes to a model server only when it has a free slot; the rest wait on the main node,
round-robin between participants. The node gateway and its Ollama servers are the third-party
stub: ``backends.chat_gate`` holds a call "in flight" until the test lets it go.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.fixtures.corpus import AGENTS_DIR, OWNER
from tests.fixtures.world import ask, seed_chat
from yorath.control.agents.loader import sync_agents_dir
from yorath.control.cluster import service as cluster
from yorath.control.journal import AuditEvent, AuditEventType, SpanKind
from yorath.control.journal.models import TraceSpan
from yorath.control.model import Execution
from yorath.control.setup.hardware import HardwareInfo
from yorath.control.setup.inventory import upsert_node

pytestmark = pytest.mark.group

MAIN_KEY = "M" * 43 + "="
KEYS = {"node-2": "A" * 43 + "=", "node-3": "B" * 43 + "="}
MEMBER = {"login": "anna", "password": "anna-pass-1"}


def bearer(app, key: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    )  # fmt: skip


@pytest.fixture
async def world(make_app, session, backends, tmp_path):
    """Main node without a GPU, an owner and a member, each with an API key."""
    app = make_app(agents_dir=AGENTS_DIR)
    app.state.settings.cluster_dir = tmp_path / "cluster"
    owner = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await owner.post("/auth/setup", json=OWNER)
    await sync_agents_dir(session, AGENTS_DIR)
    await upsert_node(session, "main", HardwareInfo(vcpu=8, ram_gb=16, disk_free_gb=90))
    await cluster.issue_token(session, None, "203.0.113.5:51820", MAIN_KEY, "-", "https://main")
    key_a = (await owner.post("/api-keys", json={"name": "a"})).json()["key"]
    assert (await owner.post("/users", json={**MEMBER, "display_name": "Anna"})).status_code == 201
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    assert (await member.post("/auth/login", json=MEMBER)).status_code == 200
    key_b = (await member.post("/api-keys", json={"name": "b"})).json()["key"]
    anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    a, b = bearer(app, key_a), bearer(app, key_b)
    w = SimpleNamespace(app=app, owner=owner, a=a, b=b, anon=anon, slots=app.state.model_slots)
    yield w
    for client in (owner, member, anon, a, b):
        await client.aclose()


async def join(w, name: str, *, gpu: int) -> dict:
    token = (await w.owner.post("/cluster/tokens")).json()["token"]
    body = {"token": token, "name": name, "wg_public_key": KEYS[name], "cpu_count": 18,
            "ram_gb": 31, "gpu_count": gpu, "gpu": "NVIDIA A10", "vram_gb": 23}  # fmt: skip
    r = await w.anon.post("/cluster/join", json=body)
    assert r.status_code == 200, r.text
    assert (await w.owner.post(f"/cluster/nodes/{name}/approve")).status_code == 200
    return r.json()


async def place(w, model: str, node: str) -> None:
    r = await w.owner.post("/cluster/models/place", json={"model": model, "node": node})
    assert r.status_code == 200 and '"status": "placed"' in r.text, r.text


def call(client, text: str = "hello", model: str = "local/default"):
    return client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": text}]},
    )


async def until(condition, what: str) -> None:
    for _ in range(300):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"did not happen: {what}")


def waiting(w) -> int:
    return sum(w.slots.waiting().values())


def flying(w) -> int:
    return sum(w.slots.in_flight().values())


def chats(backends) -> list[str]:
    return [p for p in backends.node_gateway if p.endswith("/api/chat")]


def asked(backends) -> list[str]:
    """Texts of the calls in the order the model servers got them."""
    return [body["messages"][-1]["content"] for body in backends.chat]


async def audit(session, kind: AuditEventType) -> list[AuditEvent]:
    rows = await session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == kind).order_by(AuditEvent.occurred_at)
    )
    return list(rows.all())


# --- waiting and handing out -----------------------------------------------------------------


@pytest.mark.covers("INV-QUE-1")
async def test_call_waits_on_the_main_node_until_a_slot_frees(world, session, backends):
    await join(world, "node-2", gpu=2)
    await place(world, "qwen3.5:9b", "node-2")
    backends.chat_gate = asyncio.Event()
    first, second = (asyncio.create_task(call(world.a, t)) for t in ("one", "two"))
    await until(lambda: flying(world) == 2, "two calls in flight")
    third = asyncio.create_task(call(world.a, "three"))
    await until(lambda: waiting(world) == 1, "the third call in the queue")
    # the waiting call was not sent to any model server, and its Execution says so
    assert sorted(world.slots.in_flight().values()) == [1, 1]
    statuses = (await session.scalars(select(Execution.status))).all()
    assert sorted(s.value for s in statuses) == ["queued", "running", "running"]
    listed = (await world.owner.get("/cluster/models")).json()
    assert [(m["name"], m["queued"]) for m in listed["models"]] == [("local/qwen3.5:9b", 1)]
    assert [(s["server"], s["in_flight"]) for s in listed["servers"]] == [
        ("ollama", 0),
        ("gpu0", 1),
        ("gpu1", 1),
    ]

    backends.chat_gate.set()
    assert [r.status_code for r in await asyncio.gather(first, second, third)] == [200] * 3
    assert waiting(world) == 0 and flying(world) == 0
    assert len(chats(backends)) == 3 and sorted(asked(backends)) == ["one", "three", "two"]
    session.expire_all()
    statuses = (await session.scalars(select(Execution.status))).all()
    assert [s.value for s in statuses] == ["succeeded"] * 3
    # the trace of the call that waited says how long and behind how many
    spans = (await session.scalars(select(TraceSpan).where(TraceSpan.kind == SpanKind.LLM))).all()
    waited = [s.attributes for s in spans if "yorath.queue_wait_ms" in s.attributes]
    assert len(waited) == 1 and waited[0]["yorath.queue_ahead"] == 0
    assert waited[0]["yorath.queue_wait_ms"] >= 0
    called = await audit(session, AuditEventType.LLM_CALLED)
    assert sum("queue_wait_ms" in e.details for e in called) == 1


@pytest.mark.covers("INV-QUE-2")
async def test_burst_of_one_participant_does_not_hold_another(world, session, backends):
    """Round-robin between participants: A, B, A, A — not A, A, A, B."""
    await join(world, "node-2", gpu=1)
    await place(world, "qwen3.5:9b", "node-2")
    backends.chat_gate = asyncio.Event()
    tasks = [asyncio.create_task(call(world.a, "a0"))]
    await until(lambda: flying(world) == 1, "a0 in flight")
    for number, (client, text) in enumerate(
        [(world.a, "a1"), (world.a, "a2"), (world.a, "a3"), (world.b, "b1")], start=1
    ):
        tasks.append(asyncio.create_task(call(client, text)))
        await until(lambda n=number: waiting(world) == n, f"{text} in the queue")
    backends.chat_gate.set()
    assert [r.status_code for r in await asyncio.gather(*tasks)] == [200] * 5
    assert asked(backends) == ["a0", "a1", "b1", "a2", "a3"]


async def test_client_that_leaves_the_queue_takes_no_slot(world, session, backends):
    await join(world, "node-2", gpu=1)
    await place(world, "qwen3.5:9b", "node-2")
    backends.chat_gate = asyncio.Event()
    first = asyncio.create_task(call(world.a, "stays"))
    await until(lambda: flying(world) == 1, "the first call in flight")
    gone = asyncio.create_task(call(world.a, "leaves"))
    behind = asyncio.create_task(call(world.b, "behind"))
    await until(lambda: waiting(world) == 2, "two calls in the queue")
    gone.cancel()
    with pytest.raises(asyncio.CancelledError):
        await gone
    await until(lambda: waiting(world) == 1, "the left call out of the queue")
    backends.chat_gate.set()
    assert [r.status_code for r in await asyncio.gather(first, behind)] == [200, 200]
    # the call that left never reached a model server
    assert asked(backends) == ["stays", "behind"]
    assert waiting(world) == 0 and flying(world) == 0


# --- limits ----------------------------------------------------------------------------------


async def test_full_queue_refuses_at_once(world, session, backends):
    await join(world, "node-2", gpu=1)
    await place(world, "qwen3.5:9b", "node-2")
    world.slots.max_waiting = 1
    backends.chat_gate = asyncio.Event()
    first = asyncio.create_task(call(world.a, "in flight"))
    await until(lambda: flying(world) == 1, "the first call in flight")
    second = asyncio.create_task(call(world.a, "waits"))
    await until(lambda: waiting(world) == 1, "the second call in the queue")
    r = await call(world.b, "too many")
    assert r.status_code == 429 and r.json()["error"]["code"] == "queue_full"
    assert waiting(world) == 1  # it did not join the queue
    backends.chat_gate.set()
    assert [r.status_code for r in await asyncio.gather(first, second)] == [200, 200]
    assert asked(backends) == ["in flight", "waits"]
    failed = [e for e in await audit(session, AuditEventType.LLM_CALLED) if "error" in e.details]
    assert [(e.details["error"], e.details["status"]) for e in failed] == [("queue_full", "failed")]


async def test_call_that_waits_too_long_gets_queue_timeout(world, session, backends):
    await join(world, "node-2", gpu=1)
    await place(world, "qwen3.5:9b", "node-2")
    world.slots.wait_seconds = 0.3
    backends.chat_gate = asyncio.Event()
    first = asyncio.create_task(call(world.a, "long answer"))
    await until(lambda: flying(world) == 1, "the first call in flight")
    r = await call(world.b, "waits in vain")
    assert r.status_code == 503 and r.json()["error"]["code"] == "queue_timeout"
    assert waiting(world) == 0 and flying(world) == 1  # out of the queue, the slot untouched
    backends.chat_gate.set()
    assert (await first).status_code == 200
    assert asked(backends) == ["long answer"]
    failed = [e for e in await audit(session, AuditEventType.LLM_CALLED) if "error" in e.details]
    assert [e.details["error"] for e in failed] == ["queue_timeout"]
    session.expire_all()
    statuses = sorted(s.value for s in (await session.scalars(select(Execution.status))).all())
    assert statuses == ["failed", "succeeded"]


@pytest.mark.covers("INV-QUE-1")
async def test_checks_come_before_the_queue(world, session, backends):
    """A call that would be denied never takes a place in the queue."""
    await join(world, "node-2", gpu=1)
    await place(world, "qwen3.5:9b", "node-2")
    backends.chat_gate = asyncio.Event()
    first = asyncio.create_task(call(world.a, "in flight"))
    await until(lambda: flying(world) == 1, "the first call in flight")
    r = await call(world.b, "foreign model", model="local/embeddings")
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_not_allowed"
    r = await world.anon.post(
        "/v1/chat/completions",
        json={"model": "local/default", "messages": [{"role": "user", "content": "no key"}]},
    )
    assert r.status_code == 401
    assert waiting(world) == 0
    backends.chat_gate.set()
    assert (await first).status_code == 200


# --- which server ----------------------------------------------------------------------------


async def test_free_server_that_served_the_participant_last_is_preferred(world, session, backends):
    await join(world, "node-2", gpu=2)
    await place(world, "qwen3.5:9b", "node-2")
    backends.chat_gate = asyncio.Event()
    a = asyncio.create_task(call(world.a, "a first"))
    await until(lambda: flying(world) == 1, "a in flight")
    b = asyncio.create_task(call(world.b, "b first"))
    await until(lambda: flying(world) == 2, "b in flight")
    backends.chat_gate.set()
    await asyncio.gather(a, b)
    # a took the first server, b the second one (the stub records them as they are let go)
    assert sorted(chats(backends)) == [
        "10.88.0.2 /ollama/gpu0/api/chat",
        "10.88.0.2 /ollama/gpu1/api/chat",
    ]
    backends.chat_gate = None
    # both servers are free: b goes back to gpu1, not to the first free one
    assert (await call(world.b, "b second")).status_code == 200
    assert chats(backends)[-1] == "10.88.0.2 /ollama/gpu1/api/chat"
    assert (await call(world.a, "a second")).status_code == 200
    assert chats(backends)[-1] == "10.88.0.2 /ollama/gpu0/api/chat"


# --- a server that does not answer -----------------------------------------------------------


@pytest.mark.covers("INV-QUE-3")
async def test_unreachable_server_is_left_out_and_the_call_repeats_on_another_placement(
    world, session, backends
):
    out2 = await join(world, "node-2", gpu=1)
    await join(world, "node-3", gpu=1)
    await place(world, "qwen3.5:9b", "node-2")
    await place(world, "qwen3.5:9b", "node-3")
    backends.node_down = {"10.88.0.2"}  # the node died, its heartbeat has not expired yet
    r = await call(world.a, "survives")
    assert r.status_code == 200, r.text
    assert backends.refused == ["10.88.0.2 /ollama/gpu0/api/chat"]
    assert chats(backends) == ["10.88.0.3 /ollama/gpu0/api/chat"]
    spans = (
        await session.scalars(
            select(TraceSpan).where(TraceSpan.kind == SpanKind.LLM).order_by(TraceSpan.started_at)
        )
    ).all()
    assert [s.attributes.get("yorath.model_server") for s in spans] == [
        "node-2/gpu0",
        "node-3/gpu0",
    ]
    assert spans[1].attributes["yorath.failed_server"] == "node-2/gpu0"
    called = (await audit(session, AuditEventType.LLM_CALLED))[-1]
    assert (called.details["failed_server"], called.details["retries"]) == ("node-2/gpu0", 1)
    # the next calls do not even try the server, whoever asks
    assert (await call(world.b, "second")).status_code == 200
    assert backends.refused == ["10.88.0.2 /ollama/gpu0/api/chat"]
    assert chats(backends)[-1] == "10.88.0.3 /ollama/gpu0/api/chat"
    # the node reports again: its server is back in the choice
    backends.node_down = set()
    node = bearer(world.app, out2["node_token"])
    assert (await node.post("/node/report", json={"instances": []})).status_code == 204
    await node.aclose()
    # node-3 is busy with b (its last server), so a takes node-2 instead of waiting
    backends.chat_gate = asyncio.Event()
    held = asyncio.create_task(call(world.b, "third"))
    await until(lambda: flying(world) == 1, "b in flight on node-3")
    other = asyncio.create_task(call(world.a, "fourth"))
    await until(lambda: flying(world) == 2, "a in flight on node-2")
    assert waiting(world) == 0
    backends.chat_gate.set()
    assert [r.status_code for r in await asyncio.gather(held, other)] == [200, 200]
    assert sorted(chats(backends)[-2:]) == [
        "10.88.0.2 /ollama/gpu0/api/chat",
        "10.88.0.3 /ollama/gpu0/api/chat",
    ]


@pytest.mark.covers("INV-QUE-3")
async def test_unreachable_only_placement_is_a_refusal_not_another_model(world, session, backends):
    await join(world, "node-2", gpu=1)
    await place(world, "qwen3.5:9b", "node-2")
    await place(world, "gemma4:e2b", "main")  # a second model, ready on the main node
    assert (await call(world.a, "anchor", model="local/qwen3.5:9b")).status_code == 200
    sent = len(backends.chat)
    backends.node_down = {"10.88.0.2"}
    r = await call(world.a, "lost", model="local/qwen3.5:9b")
    assert r.status_code == 502 and r.json()["error"]["code"] == "model_unavailable"
    assert "node-2/gpu0 did not answer" in r.json()["error"]["message"]
    assert len(backends.chat) == sent  # no model answered: not this one, not gemma
    assert waiting(world) == 0 and flying(world) == 0


# --- the chat shows the wait -----------------------------------------------------------------


async def test_chat_tells_that_the_answer_waits_for_the_model(
    make_app, session, backends, tmp_path
):
    api, app = await seed_chat(session, make_app, tmp_path)
    key = (await api.post("/api-keys", json={"name": "k"})).json()["key"]
    client = bearer(app, key)
    backends.chat_gate = asyncio.Event()
    busy = asyncio.create_task(call(client, "holds the only model server"))
    await until(lambda: sum(app.state.model_slots.in_flight().values()) == 1, "the call in flight")
    conversation = (await api.post("/chat/conversations", json={})).json()["id"]
    question = asyncio.create_task(ask(api, conversation, "How many vacation days are there?"))
    await until(lambda: sum(app.state.model_slots.waiting().values()) == 1, "the chat in queue")
    backends.chat_gate.set()
    events = await question
    assert (await busy).status_code == 200
    names = [name for name, _ in events]
    assert ("queued", {"ahead": 0}) in events
    # the wait comes before the answer, and the answer still comes
    assert names.index("queued") < names.index("token") and "done" in names
    await client.aclose()
    await api.aclose()


def test_queue_refusals_have_their_http_statuses():
    from yorath.enforcement.gateway.errors import QUEUE_STATUS

    assert json.dumps(QUEUE_STATUS, sort_keys=True) == '{"queue_full": 429, "queue_timeout": 503}'
