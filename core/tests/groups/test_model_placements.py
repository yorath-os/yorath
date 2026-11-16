"""Model catalog, model servers and placements (decision 44, change model-placements).

The worker node gateway and its Ollama servers are the third-party stub (``Backends``): there is
no tunnel here. Two real cards and the tunnel were checked on a lab setup.
"""

import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from tests.fixtures.corpus import AGENTS_DIR, OWNER
from yorath.control.agents.loader import sync_agents_dir
from yorath.control.cluster import placements
from yorath.control.cluster import service as cluster
from yorath.control.journal import AuditEvent, AuditEventType, SpanKind
from yorath.control.journal.models import TraceSpan
from yorath.control.model import ModelPlacement, ModelResource, ModelServer, Node
from yorath.control.setup import model_cli
from yorath.control.setup.hardware import HardwareInfo
from yorath.control.setup.inventory import upsert_node
from yorath.control.setup.ollama import OllamaClient

pytestmark = pytest.mark.group

MAIN_KEY = "M" * 43 + "="
KEY_2, KEY_3 = ("A" * 43 + "=", "B" * 43 + "=")
CPU8 = HardwareInfo(vcpu=8, ram_gb=16, disk_free_gb=90)
ALL = ["public", "internal", "confidential", "restricted"]


@pytest.fixture
async def world(make_app, session, backends, tmp_path):
    """Main node without a GPU, an owner, an API key; no chat model is placed yet."""
    app = make_app(agents_dir=AGENTS_DIR)
    app.state.settings.cluster_dir = tmp_path / "cluster"
    owner = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await owner.post("/auth/setup", json=OWNER)
    await sync_agents_dir(session, AGENTS_DIR)
    await upsert_node(session, "main", CPU8)
    await cluster.issue_token(session, None, "203.0.113.5:51820", MAIN_KEY, "-", "https://main")
    key = (await owner.post("/api-keys", json={"name": "k"})).json()["key"]
    user = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    )  # fmt: skip
    anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    w = SimpleNamespace(app=app, owner=owner, user=user, anon=anon)
    yield w
    for client in (owner, user, anon):
        await client.aclose()


async def join(w, name: str, key: str, *, gpu: int, vram_gb: int | None = 23) -> dict:
    token = (await w.owner.post("/cluster/tokens")).json()["token"]
    body = {"token": token, "name": name, "wg_public_key": key, "cpu_count": 18, "ram_gb": 31,
            "gpu_count": gpu, "gpu": "NVIDIA A10" if gpu else None,
            "vram_gb": vram_gb if gpu else None}  # fmt: skip
    r = await w.anon.post("/cluster/join", json=body)
    assert r.status_code == 200, r.text
    assert (await w.owner.post(f"/cluster/nodes/{name}/approve")).status_code == 200
    return r.json()


async def place(w, model: str, **where) -> tuple[int, list[dict]]:
    r = await w.owner.post("/cluster/models/place", json={"model": model, **where})
    if r.status_code != 200:
        return r.status_code, [r.json()["detail"]]
    return 200, [json.loads(line) for line in r.text.splitlines() if line]


async def ask(w, model: str = "local/default"):
    return await w.user.post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hello"}]},
    )


async def catalog(w) -> dict[str, dict]:
    return {m["name"]: m for m in (await w.owner.get("/cluster/models")).json()["models"]}


def where(model: dict) -> list[tuple[str, str, str]]:
    return [(p["node"], p["server"], p["status"]) for p in model["placements"]]


async def audit(session, kind: AuditEventType) -> list[AuditEvent]:
    rows = await session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == kind).order_by(AuditEvent.occurred_at)
    )
    return list(rows.all())


# --- servers ---------------------------------------------------------------------------------


@pytest.mark.covers("INV-PLC-1")
async def test_node_with_two_cards_gets_a_model_server_per_card(world, session):
    await join(world, "node-2", KEY_2, gpu=2)
    servers = (await world.owner.get("/cluster/models")).json()["servers"]
    got = [(s["node"], s["server"], s["gpus"], s["vram_gb"], s["model"]) for s in servers]
    assert got == [
        ("main", "ollama", [], None, None),
        ("node-2", "gpu0", [0], 23, None),
        ("node-2", "gpu1", [1], 23, None),
    ]


async def test_node_report_adds_servers_to_a_node_joined_without_cards(world, session):
    """A node joined by an older version reports its cards with the heartbeat."""
    out = await join(world, "node-2", KEY_2, gpu=0)
    assert [s["node"] for s in (await world.owner.get("/cluster/models")).json()["servers"]] == [
        "main"
    ]
    node = AsyncClient(
        transport=ASGITransport(app=world.app), base_url="http://test",
        headers={"Authorization": f"Bearer {out['node_token']}"},
    )  # fmt: skip
    hardware = {"gpu_count": 1, "gpu": "NVIDIA A10", "vram_gb": 23, "has_model": False}
    r = await node.post("/node/report", json={"instances": [], "hardware": hardware})
    await node.aclose()
    assert r.status_code == 204
    servers = (await world.owner.get("/cluster/models")).json()["servers"]
    assert [(s["node"], s["server"], s["vram_gb"]) for s in servers] == [
        ("main", "ollama", None),
        ("node-2", "gpu0", 23),
    ]


async def test_each_server_gets_its_window_recommendation_and_catalog(world, session):
    """The Models screen picks a server; the core says what fits there (change models-by-server)."""
    await join(world, "node-2", KEY_2, gpu=1, vram_gb=22)
    servers = {s["server"]: s for s in (await world.owner.get("/cluster/models")).json()["servers"]}
    main, gpu = servers["ollama"], servers["gpu0"]
    assert (main["main"], main["window"], main["recommended"]) == (True, 4096, "gemma4:e2b")
    assert (gpu["main"], gpu["window"], gpu["recommended"]) == (False, 65536, "qwen3.5:9b")
    rows = {r["name"]: r for r in gpu["catalog"]}
    assert "embeddinggemma-2:570m" not in rows, "the document search model is not a chat model"
    assert (rows["qwen3.5:9b"]["memory_gb"], rows["qwen3.5:9b"]["fits"]) == (5.7, True)
    assert rows["qwen3.5:9b"]["measured"] == "A10, 4.11.2026; not measured on CPU"
    assert (main_rows := {r["name"]: r for r in main["catalog"]})["gemma4:e2b"]["memory_gb"] == 5.0
    assert main_rows["qwen3.5:9b"]["estimated"] is True, "no RAM measurement on CPU: an estimate"
    assert main_rows["gemma4:e2b"]["estimated"] is False


# --- placing ---------------------------------------------------------------------------------


@pytest.mark.covers("INV-PLC-1")
async def test_model_is_pulled_once_and_placed_on_every_card_of_the_node(world, session, backends):
    await join(world, "node-2", KEY_2, gpu=2)
    code, events = await place(world, "qwen3.5:9b", node="node-2")
    assert code == 200
    assert events[-1] == {
        "status": "placed", "name": "local/qwen3.5:9b", "node": "node-2",
        "servers": ["gpu0", "gpu1"], "default": True, "window": 65536,
    }  # fmt: skip
    # one pull, through the first card's server: the node's servers share the model files
    assert backends.pulls == ["qwen3.5:9b"]
    assert [p for p in backends.node_gateway if p.endswith("/api/pull")] == [
        "10.88.0.2 /ollama/gpu0/api/pull"
    ]
    models = await catalog(world)
    assert list(models) == ["local/qwen3.5:9b"]
    model = models["local/qwen3.5:9b"]
    assert where(model) == [("node-2", "gpu0", "ready"), ("node-2", "gpu1", "ready")]
    assert (model["default"], model["context_length"], model["classes"]) == (True, 65536, ALL)
    # the first placed chat model becomes local/default: the pointer mirrors it
    pointer = await session.scalar(
        select(ModelResource).where(ModelResource.name == "local/default")
    )
    assert (pointer.model_id, pointer.context_length) == ("qwen3.5:9b", 65536)
    assert pointer.alias_of is not None
    placed = await audit(session, AuditEventType.MODEL_PLACED)
    assert [(e.model, e.details["node"], e.details["servers"]) for e in placed] == [
        ("qwen3.5:9b", "node-2", ["gpu0", "gpu1"])
    ]
    assert placed[0].details["source"] == "web" and placed[0].actor_principal_id is not None


@pytest.mark.covers("INV-PLC-2")
async def test_two_calls_at_once_go_to_two_cards(world, session, backends):
    await join(world, "node-2", KEY_2, gpu=2)
    assert (await place(world, "qwen3.5:9b", node="node-2"))[0] == 200
    backends.chat_gate = asyncio.Event()
    first = asyncio.create_task(ask(world))
    second = asyncio.create_task(ask(world))

    def chats() -> list[str]:
        return sorted(p for p in backends.node_gateway if p.endswith("/api/chat"))

    slots = world.app.state.model_slots
    for _ in range(200):
        if sum(slots.in_flight().values()) == 2:
            break
        await asyncio.sleep(0.01)
    # both calls are in flight, each holds its own model server
    assert sorted(slots.in_flight().values()) == [1, 1]
    backends.chat_gate.set()
    assert [r.status_code for r in await asyncio.gather(first, second)] == [200, 200]
    assert chats() == ["10.88.0.2 /ollama/gpu0/api/chat", "10.88.0.2 /ollama/gpu1/api/chat"]
    assert world.app.state.model_slots.in_flight() == {}
    # nobody is in flight: the next call takes the first card again
    backends.chat_gate = None
    assert (await ask(world)).status_code == 200
    assert chats().count("10.88.0.2 /ollama/gpu0/api/chat") == 2


async def test_trace_and_audit_name_requested_and_actual_model_and_server(world, session, backends):
    await join(world, "node-2", KEY_2, gpu=1)
    assert (await place(world, "qwen3.5:9b", node="node-2"))[0] == 200
    assert (await ask(world)).status_code == 200
    span = await session.scalar(select(TraceSpan).where(TraceSpan.kind == SpanKind.LLM))
    assert span.model == "qwen3.5:9b"
    assert span.attributes["yorath.model_requested"] == "local/default"
    assert span.attributes["yorath.model_server"] == "node-2/gpu0"
    called = (await audit(session, AuditEventType.LLM_CALLED))[-1]
    assert called.model == "qwen3.5:9b"
    assert (called.details["requested"], called.details["server"]) == (
        "local/default",
        "node-2/gpu0",
    )
    assert backends.chat[-1]["model"] == "qwen3.5:9b"
    assert backends.chat[-1]["options"]["num_ctx"] == 65536


@pytest.mark.covers("INV-PLC-3")
async def test_model_without_ready_placement_is_refused_and_no_other_model_answers(
    world, session, backends
):
    """The main invariant: a call never goes to another model, even when one is ready."""
    await join(world, "node-2", KEY_2, gpu=1)
    assert (await place(world, "qwen3.5:9b", node="node-2"))[0] == 200
    assert (await place(world, "gemma4:e2b"))[0] == 200  # a second model, ready on the main node
    assert (await ask(world, "local/qwen3.5:9b")).status_code == 200  # anchor: it answers
    calls = len(backends.chat)
    await session.execute(update(Node).where(Node.name == "node-2").values(status="lost"))
    await session.commit()
    r = await ask(world, "local/qwen3.5:9b")
    assert r.status_code == 502 and r.json()["error"]["code"] == "model_unavailable"
    assert "node node-2 is lost" in r.json()["error"]["message"]
    assert len(backends.chat) == calls  # nothing was called: not this model, not gemma
    # the pointer follows its target into the refusal as well
    r = await ask(world, "local/default")
    assert r.status_code == 502 and r.json()["error"]["code"] == "model_unavailable"
    assert len(backends.chat) == calls
    # the other model still answers when asked for by name
    assert (await ask(world, "local/gemma4:e2b")).status_code == 200
    assert backends.chat[-1]["model"] == "gemma4:e2b"


async def test_model_in_node_ollama_without_a_placement_gets_no_calls(world, session, backends):
    """A model somebody pulled on the node by hand is not in the catalog: it is not callable."""
    await join(world, "node-2", KEY_2, gpu=1)
    assert (await place(world, "qwen3.5:9b", node="node-2"))[0] == 200
    backends.tags = ["embeddinggemma-2:570m", "qwen3.5:9b", "rogue:7b"]
    r = await ask(world, "local/rogue:7b")
    assert r.status_code == 502 and r.json()["error"]["code"] == "model_not_found"
    assert "local/rogue:7b" not in await catalog(world)


async def test_failed_pull_leaves_failed_placement_that_takes_no_calls(world, session, backends):
    await join(world, "node-2", KEY_2, gpu=1)
    backends.node_servers = set()  # the node's Ollama does not answer: 404 from the gateway
    code, events = await place(world, "qwen3.5:9b", node="node-2")
    assert code == 200 and "error" in events[-1]
    model = (await catalog(world))["local/qwen3.5:9b"]
    assert [(p["server"], p["status"]) for p in model["placements"]] == [("gpu0", "failed")]
    assert "404" in model["placements"][0]["detail"]
    assert await audit(session, AuditEventType.MODEL_PLACED) == []
    # repeating the command after the node recovers is enough
    backends.node_servers = {"gpu0"}
    code, events = await place(world, "qwen3.5:9b", node="node-2")
    assert events[-1]["status"] == "placed"
    assert where((await catalog(world))["local/qwen3.5:9b"]) == [("node-2", "gpu0", "ready")]


# --- placement rules -------------------------------------------------------------------------


async def test_busy_server_is_refused_unless_replaced(world, session, backends):
    await join(world, "node-2", KEY_2, gpu=2)
    assert (await place(world, "qwen3.5:9b", node="node-2"))[0] == 200
    code, events = await place(world, "qwen3.5:4b", node="node-2", gpu=0)
    assert (code, events[0]["code"]) == (409, "server_busy")
    assert "node-2/gpu0 already serves local/qwen3.5:9b" in events[0]["message"]
    assert backends.pulls == ["qwen3.5:9b"]  # nothing was pulled for the refused placement
    # no free card and no --gpu: refused with the list of what stands where
    code, events = await place(world, "qwen3.5:4b", node="node-2")
    assert (code, events[0]["code"]) == (409, "server_busy")
    code, events = await place(world, "qwen3.5:4b", node="node-2", gpu=0, replace=True)
    assert events[-1]["status"] == "placed" and events[-1]["default"] is False
    models = await catalog(world)
    assert where(models["local/qwen3.5:4b"]) == [("node-2", "gpu0", "ready")]
    assert where(models["local/qwen3.5:9b"]) == [("node-2", "gpu1", "ready")]
    # the replaced model is unloaded from the card it left
    assert backends.unloads == [{"model": "qwen3.5:9b", "keep_alive": 0}]
    assert "10.88.0.2 /ollama/gpu0/api/generate" in backends.node_gateway


async def test_model_has_one_window_on_every_server(world, session, backends):
    await join(world, "node-2", KEY_2, gpu=1)
    assert (await place(world, "qwen3.5:9b", node="node-2"))[0] == 200
    code, events = await place(world, "qwen3.5:9b")  # the main node has no GPU: window 4096
    assert (code, events[0]["code"]) == (409, "window_mismatch")
    assert "65536" in events[0]["message"] and "4096" in events[0]["message"]
    assert where((await catalog(world))["local/qwen3.5:9b"]) == [("node-2", "gpu0", "ready")]


async def test_a_move_keeps_the_old_server_answering_until_the_new_one_is_ready(
    world, session, backends
):
    """R2: a failed pull on the new server leaves the old placement and its window as they were."""
    assert (await place(world, "gemma4:e2b", node="main"))[0] == 200  # window 4096 on the CPU
    await join(world, "node-2", KEY_2, gpu=1)
    backends.node_down.add("10.88.0.2")  # the pull on the new server breaks
    code, events = await place(world, "gemma4:e2b", node="node-2", move=True)
    assert code == 200 and "error" in events[-1]
    model = (await catalog(world))["local/gemma4:e2b"]
    assert model["context_length"] == 4096
    assert ("main", "ollama", "ready") in where(model)
    assert (await ask(world, "local/gemma4:e2b")).status_code == 200, "the old server still answers"
    assert await audit(session, AuditEventType.MODEL_MOVED) == []

    # anchor: the same move with the node up switches window and servers in one step
    backends.node_down.clear()
    code, events = await place(world, "gemma4:e2b", node="node-2", move=True)
    assert events[-1]["status"] == "placed", events[-1]
    model = (await catalog(world))["local/gemma4:e2b"]
    assert model["context_length"] == 65536
    assert where(model) == [("node-2", "gpu0", "ready")]
    [moved] = await audit(session, AuditEventType.MODEL_MOVED)
    assert moved.details["from"] == ["main/ollama"] and moved.details["to"] == "node-2/gpu0"


async def test_a_gpu_added_to_main_after_the_wizard_counts_when_placing_from_the_panel(
    world, session, backends
):
    """R6: placing on main from the panel re-reads main's cards, as the wizard did."""
    settings = world.app.state.settings
    settings.gpu_name, settings.gpu_vram_gb, settings.gpu_count = "NVIDIA A10", 23, 1
    code, events = await place(world, "qwen3.5:9b", node="main")
    assert code == 200 and events[-1]["window"] == 65536, events[-1]
    main = await session.scalar(select(Node).where(Node.role == "main"))
    await session.refresh(main)
    assert (main.gpu_count, main.vram_gb) == (1, 23)


async def test_model_larger_than_the_card_is_refused_unless_forced(world, session, backends):
    await join(world, "node-2", KEY_2, gpu=1, vram_gb=4)  # qwen3.5:9b needs 5.7 GB
    code, events = await place(world, "qwen3.5:9b", node="node-2")
    assert (code, events[0]["code"]) == (409, "does_not_fit")
    assert backends.pulls == []
    code, events = await place(world, "qwen3.5:9b", node="node-2", force=True)
    assert events[-1]["status"] == "placed" and backends.pulls == ["qwen3.5:9b"]


@pytest.mark.covers("INV-PLC-4")
async def test_model_is_not_placed_on_node_that_does_not_allow_its_data_class(
    world, session, backends
):
    await join(world, "node-2", KEY_2, gpu=1)
    r = await world.owner.put(
        "/cluster/nodes/node-2/classes", json={"classes": ["internal", "public"]}
    )
    assert r.json() == {"node": "node-2", "classes": ["public", "internal"]}
    code, events = await place(world, "qwen3.5:9b", node="node-2")
    assert (code, events[0]["code"]) == (403, "node_class_denied")
    assert "confidential, restricted" in events[0]["message"]
    assert backends.pulls == [] and await catalog(world) == {}
    # anchor: the same placement goes through once the node allows every class
    r = await world.owner.put("/cluster/nodes/node-2/classes", json={"classes": ALL})
    assert r.status_code == 200
    assert (await place(world, "qwen3.5:9b", node="node-2"))[0] == 200
    assert backends.pulls == ["qwen3.5:9b"]
    changed = await audit(session, AuditEventType.NODE_CLASSES_CHANGED)
    assert [(e.details["previous"], e.details["classes"]) for e in changed] == [
        (ALL, ["public", "internal"]),
        (["public", "internal"], ALL),
    ]


@pytest.mark.covers("INV-PLC-4")
async def test_node_classes_cannot_be_narrowed_under_a_placed_model(world, session, backends):
    await join(world, "node-2", KEY_2, gpu=1)
    assert (await place(world, "qwen3.5:9b", node="node-2"))[0] == 200
    r = await world.owner.put("/cluster/nodes/node-2/classes", json={"classes": ["public"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "placements_block"
    assert "local/qwen3.5:9b on gpu0" in r.json()["detail"]["message"]
    nodes = {n["name"]: n for n in (await world.owner.get("/cluster/nodes")).json()}
    assert nodes["node-2"]["allowed_data_classes"] == ALL
    r = await world.owner.put("/cluster/nodes/node-2/classes", json={"classes": ["secret"]})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "unknown_class"


# --- the pointer and removal -----------------------------------------------------------------


async def test_default_pointer_moves_only_to_a_model_with_a_ready_placement(
    world, session, backends
):
    await join(world, "node-2", KEY_2, gpu=2)
    assert (await place(world, "qwen3.5:9b", node="node-2", gpu=0))[0] == 200
    assert (await place(world, "qwen3.5:4b", node="node-2", gpu=1))[0] == 200
    assert (await ask(world)).status_code == 200 and backends.chat[-1]["model"] == "qwen3.5:9b"
    r = await world.owner.post("/cluster/models/default", json={"model": "qwen3.5:4b"})
    assert r.json() == {"name": "local/qwen3.5:4b", "model_id": "qwen3.5:4b"}
    assert (await ask(world)).status_code == 200 and backends.chat[-1]["model"] == "qwen3.5:4b"
    assert "10.88.0.2 /ollama/gpu1/api/chat" in backends.node_gateway
    models = await catalog(world)
    assert [(name, m["default"]) for name, m in models.items()] == [
        ("local/qwen3.5:4b", True),
        ("local/qwen3.5:9b", False),
    ]
    registered = await audit(session, AuditEventType.MODEL_REGISTERED)
    assert [(e.model, e.details["previous"]) for e in registered] == [
        ("qwen3.5:9b", None),
        ("qwen3.5:4b", "qwen3.5:9b"),
    ]
    # a model that is not placed anywhere cannot become the default
    r = await world.owner.post("/cluster/models/default", json={"model": "gemma4:e2b"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "model_not_found"
    backends.node_servers = set()
    assert "error" in (await place(world, "gemma4:e2b", node="node-2", gpu=0, replace=True))[1][-1]
    r = await world.owner.post("/cluster/models/default", json={"model": "gemma4:e2b"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_ready_placement"
    assert (await catalog(world))["local/qwen3.5:4b"]["default"] is True


async def test_main_node_without_chat_model_is_control_only(world, session, backends):
    """The main node gives its model away: it keeps the panel, the checks and the journal."""
    assert (await place(world, "gemma4:e2b"))[0] == 200  # as the wizard does
    assert (await ask(world)).status_code == 200 and backends.node_gateway == []
    await join(world, "node-2", KEY_2, gpu=1)
    assert (await place(world, "qwen3.5:9b", node="node-2"))[0] == 200
    # the last placement of the default model is not removed by accident
    r = await world.owner.post("/cluster/models/remove", json={"model": "gemma4:e2b"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "default_would_be_unavailable"
    await world.owner.post("/cluster/models/default", json={"model": "qwen3.5:9b"})
    r = await world.owner.post("/cluster/models/remove", json={"model": "gemma4:e2b"})
    assert r.json() == {"removed": ["main/ollama"]}
    assert backends.unloads == [{"model": "gemma4:e2b", "keep_alive": 0}]
    # the model left the catalog, the main node's server is free, calls go to the node
    assert list(await catalog(world)) == ["local/qwen3.5:9b"]
    servers = (await world.owner.get("/cluster/models")).json()["servers"]
    assert [(s["node"], s["server"], s["model"]) for s in servers] == [
        ("main", "ollama", None),
        ("node-2", "gpu0", "local/qwen3.5:9b"),
    ]
    assert (await ask(world)).status_code == 200
    assert backends.node_gateway[-1] == "10.88.0.2 /ollama/gpu0/api/chat"
    r = await ask(world, "local/gemma4:e2b")
    assert r.status_code == 502 and r.json()["error"]["code"] == "model_not_found"
    removed = await audit(session, AuditEventType.MODEL_REMOVED)
    assert [(e.model, e.details["servers"], e.details["forgotten"]) for e in removed] == [
        ("gemma4:e2b", ["main/ollama"], True)
    ]


async def test_forced_removal_of_default_leaves_pointer_unavailable(world, session, backends):
    assert (await place(world, "gemma4:e2b"))[0] == 200
    r = await world.owner.post(
        "/cluster/models/remove", json={"model": "gemma4:e2b", "force": True}
    )
    assert r.json() == {"removed": ["main/ollama"]}
    calls = len(backends.chat)
    r = await ask(world)
    assert r.status_code == 502 and r.json()["error"]["code"] == "model_unavailable"
    assert "not placed on any model server" in r.json()["error"]["message"]
    assert len(backends.chat) == calls


async def test_api_key_calls_catalog_model_by_name_and_lists_it(world, session, backends):
    assert (await place(world, "gemma4:e2b"))[0] == 200
    listed = [m["id"] for m in (await world.user.get("/v1/models")).json()["data"]]
    assert listed == ["local/default", "local/gemma4:e2b"]  # no embeddings, no other providers
    assert (await ask(world, "local/gemma4:e2b")).status_code == 200
    assert backends.chat[-1]["model"] == "gemma4:e2b"
    r = await ask(world, "local/embeddings")
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_not_allowed"


# --- the command on the server ---------------------------------------------------------------


async def run_cli(w, fn, **kwargs) -> tuple[int, str]:
    out = io.StringIO()
    client = OllamaClient(w.app.state.http, w.app.state.settings.ollama_url)
    args = (out, w.app.state.manifest) if fn is model_cli.pull_on_node else (out,)
    code = await fn(*args, w.app.state.sessions, w.app.state.journal, client, **kwargs)
    return code, out.getvalue()


async def test_cli_places_on_node_lists_and_removes(world, session, backends):
    await join(world, "node-2", KEY_2, gpu=2)
    code, text = await run_cli(
        world, model_cli.pull_on_node, name="qwen3.5:9b", node="node-2", gpu=None,
        force=False, replace=False,
    )  # fmt: skip
    assert code == 0, text
    assert "Done: local/qwen3.5:9b on node-2/gpu0, node-2/gpu1, window 65536" in text
    assert "local/default now points at local/qwen3.5:9b" in text
    assert (await audit(session, AuditEventType.MODEL_PLACED))[0].details["source"] == "host"

    out = io.StringIO()
    await model_cli.show_catalog(out, world.app.state.sessions)
    listing = out.getvalue()
    assert "* local/qwen3.5:9b               65536    node-2/gpu0, node-2/gpu1" in listing
    assert "main/ollama            no GPU                 free" in listing
    assert "node-2/gpu1            NVIDIA A10 23 GB       local/qwen3.5:9b" in listing

    code, text = await run_cli(
        world, model_cli.pull_on_node, name="qwen3.5:4b", node="node-2", gpu=1,
        force=False, replace=False,
    )  # fmt: skip
    assert code == 2 and "error (server_busy)" in text
    code, text = await run_cli(
        world, model_cli.remove, name="qwen3.5:9b", node="node-2", gpu=1, force=False
    )
    assert (code, text) == (0, "Removed local/qwen3.5:9b from node-2/gpu1\n")
    rows = await session.execute(
        select(ModelServer.name).join(
            ModelPlacement, ModelPlacement.model_server_id == ModelServer.id
        )
    )
    assert [name for (name,) in rows.all()] == ["gpu0"]


def test_local_wildcard_in_agent_card_covers_catalog_models_only():
    card = ["local/default", "local/*"]
    assert placements.card_allows(card, "local/qwen3.5:9b") is True
    assert placements.card_allows(card, "local/embeddings") is False
    assert placements.card_allows(card, "openrouter/default") is False
    assert placements.card_allows(["local/default"], "local/qwen3.5:9b") is False


# --- migration of an existing installation ---------------------------------------------------


def _alembic(url: str, *args: str) -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        env={**os.environ, "YORATH_DATABASE_URL": url},
    )


async def test_migration_turns_default_model_on_a_node_into_catalog_and_placement(database_url):
    """An installation of 0.5.0 with ``local/default`` served by a worker node is upgraded: the
    model becomes a catalog entry with a ready placement there; the downgrade restores the node."""
    import uuid

    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from yorath.db import make_engine

    base = make_url(database_url)
    name = f"{base.database}_migration"
    admin = make_engine(base.set(database="postgres").render_as_string(hide_password=False))
    admin = admin.execution_options(isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'drop database if exists "{name}"'))
        await conn.execute(text(f'create database "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    engine = make_engine(url)
    try:
        _alembic(url, "upgrade", "0001_initial")
        main, worker = uuid.uuid7(), uuid.uuid7()
        async with engine.begin() as conn:
            cluster_id = await conn.scalar(text("select id from clusters"))
            for node_id, node, role, gpus, ip in (
                (main, "main", "main", 0, None),
                (worker, "gpu-box", "worker", 2, "10.88.0.2"),
            ):
                await conn.execute(
                    text(
                        "insert into nodes (id, cluster_id, name, cpu_count, ram_gb, gpu_count, "
                        "role, status, overlay_ip) values (:id, :c, :n, 4, 8, :g, :r, 'active', "
                        ":ip)"
                    ),
                    {"id": node_id, "c": cluster_id, "n": node, "g": gpus, "r": role, "ip": ip},
                )
            for model, model_id, node_id, window in (
                ("local/default", "qwen3.5:9b", worker, 65536),
                ("local/embeddings", "embeddinggemma-2:570m", None, 4096),
            ):
                await conn.execute(
                    text(
                        "insert into model_resources (id, name, provider, model_id, "
                        "allowed_data_classes, context_length, node_id) values (:id, :name, "
                        "'ollama', :model_id, '{public,internal}', :window, :node)"
                    ),
                    {"id": uuid.uuid7(), "name": model, "model_id": model_id, "window": window,
                     "node": node_id},
                )  # fmt: skip

        _alembic(url, "upgrade", "head")
        async with engine.connect() as conn:
            models = (
                await conn.execute(
                    text(
                        "select m.name, m.model_id, m.context_length, m.allowed_data_classes, "
                        "t.name from model_resources m left join model_resources t "
                        "on t.id = m.alias_of order by m.name"
                    )
                )
            ).all()
            assert [tuple(r) for r in models] == [
                ("local/default", "qwen3.5:9b", 65536, ["public", "internal"], "local/qwen3.5:9b"),
                ("local/embeddings", "embeddinggemma-2:570m", 4096, ["public", "internal"], None),
                ("local/qwen3.5:9b", "qwen3.5:9b", 65536, ["public", "internal"], None),
            ]
            servers = (
                await conn.execute(
                    text(
                        "select n.name, s.name, s.gpu_indexes from model_servers s "
                        "join nodes n on n.id = s.node_id order by n.name, s.name"
                    )
                )
            ).all()
            assert [tuple(r) for r in servers] == [
                ("gpu-box", "gpu0", [0]),
                ("gpu-box", "gpu1", [1]),
                ("main", "ollama", []),
            ]
            placed = (
                await conn.execute(
                    text(
                        "select m.name, n.name, s.name, p.status from model_placements p "
                        "join model_resources m on m.id = p.model_resource_id "
                        "join model_servers s on s.id = p.model_server_id "
                        "join nodes n on n.id = s.node_id"
                    )
                )
            ).all()
            assert [tuple(r) for r in placed] == [("local/qwen3.5:9b", "gpu-box", "gpu0", "ready")]
            classes = await conn.scalar(text("select allowed_data_classes from nodes limit 1"))
            assert classes == ["public", "internal", "confidential", "restricted"]

        _alembic(url, "downgrade", "0001_initial")
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "select m.name, m.model_id, n.name, m.enabled from model_resources m "
                        "left join nodes n on n.id = m.node_id order by m.name"
                    )
                )
            ).all()
            # the catalog row stays, disabled: executions may reference it
            assert [tuple(r) for r in rows] == [
                ("local/default", "qwen3.5:9b", "gpu-box", True),
                ("local/embeddings", "embeddinggemma-2:570m", None, True),
                ("local/qwen3.5:9b", "qwen3.5:9b", None, False),
            ]
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'drop database if exists "{name}"'))
        await admin.dispose()
