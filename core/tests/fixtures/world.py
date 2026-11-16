"""World builders for groups: owner, agents, models, documents, chat requests."""

import json
import uuid
from decimal import Decimal

from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.fixtures.corpus import AGENTS_DIR, OWNER, POLICY_MD, SECRET_MD
from yorath.control.agents.loader import sync_agents_dir
from yorath.control.model import (
    Agent,
    AgentMode,
    AgentRevision,
    DataClass,
    ExecutionTrigger,
    Hosting,
    ModelProvider,
    RuntimeType,
    User,
)
from yorath.control.setup.ollama import register_model as _register_model
from yorath.enforcement.gateway import load_agent, open_context


async def place_on_main(session, model):
    """A ready placement of the model on the main node's model server (decision 44).

    Worlds that are not about the catalog keep ``local/default`` as a plain model that is its own
    target; a chat model is called only through a placement, so it gets one here.
    """
    from sqlalchemy import select

    from yorath.control.cluster.placements import ensure_servers
    from yorath.control.model import ModelPlacement, Node, PlacementStatus
    from yorath.control.setup.hardware import HardwareInfo
    from yorath.control.setup.inventory import upsert_node

    main = await session.scalar(select(Node).where(Node.role == "main"))
    if main is None:
        main = await upsert_node(session, "main", HardwareInfo(vcpu=4, ram_gb=8, disk_free_gb=40))
    server = (await ensure_servers(session, main))[0]
    found = await session.scalar(
        select(ModelPlacement).where(
            ModelPlacement.model_resource_id == model.id,
            ModelPlacement.model_server_id == server.id,
        )
    )
    if found is None:
        session.add(
            ModelPlacement(
                model_resource_id=model.id, model_server_id=server.id,
                status=PlacementStatus.READY, progress=100,
            )
        )  # fmt: skip
    await session.commit()


async def register_model(session, name, provider, model_id, classes, **kwargs):
    """``register_model`` of the product, plus a placement on the main node for a local chat
    model: without one the Gateway refuses the call (``model_unavailable``)."""
    row = await _register_model(session, name, provider, model_id, classes, **kwargs)
    if provider is ModelProvider.OLLAMA and name != "local/embeddings":
        await place_on_main(session, row)
    return row


async def read_sse(response) -> list[tuple[str, dict]]:
    events, event = [], None
    async for line in response.aiter_lines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            events.append((event, json.loads(line[6:])))
            event = None
    return events


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events, event = [], None
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            events.append((event, json.loads(line[6:])))
            event = None
    return events


async def ask(api, conversation_id: str, text: str):
    async with api.stream(
        "POST", f"/chat/conversations/{conversation_id}/messages", json={"content": text}
    ) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        return await read_sse(r)


def event(events, name: str) -> dict:
    return next(p for e, p in events if e == name)


async def ask_model(api, conversation_id: str, text: str, model: str):
    async with api.stream(
        "POST",
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": text, "model": model},
    ) as r:
        return await read_sse(r)


async def open_for_owner(app, session):
    owner = await session.scalar(select(User).where(User.role == "owner"))
    agent, rev, card, body = await load_agent(session, "document-assistant", AGENTS_DIR)
    ctx = await open_context(
        session,
        app.state.journal,
        agent=agent,
        revision=rev,
        card=card,
        body=body,
        actor=owner,
        effective_principal=owner,
        trigger=ExecutionTrigger.USER_MESSAGE,
    )
    app.state.contexts.add(ctx)
    return ctx


def suffix() -> str:
    return uuid.uuid7().hex[-8:]


async def make_user(session, login: str, **kw) -> User:
    user = User(display_name=login, login=f"{login}-{suffix()}", **kw)
    session.add(user)
    await session.flush()
    return user


async def make_agent(session, name: str, mode: AgentMode, runtime: str = "native") -> AgentRevision:
    rt = await session.scalar(select(RuntimeType).where(RuntimeType.name == runtime))
    agent = Agent(
        name=f"{name}-{suffix()}",
        display_name=name,
        hosting=Hosting.MANAGED,
        mode=mode,
        runtime_type_id=rt.id,
    )
    session.add(agent)
    await session.flush()
    rev = AgentRevision(
        agent_id=agent.id,
        revision_no=1,
        path=f"{name}.md",
        content_sha256=uuid.uuid7().hex * 2,
        front_matter={"name": name, "mode": mode.value},
    )
    session.add(rev)
    await session.flush()
    return rev


async def seed_chat(session, make_app, tmp_path, *, external_classes=None, agents_dir=AGENTS_DIR):
    """Owner, models, team-docs with the policy and hr-salaries, closed to the agent."""
    app = make_app(documents_dir=tmp_path / "docs", agents_dir=agents_dir)
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await api.post("/auth/setup", json=OWNER)
    await sync_agents_dir(session, agents_dir)
    await register_model(
        session, "local/embeddings", ModelProvider.OLLAMA, "fake-embed", list(DataClass)
    )
    await register_model(
        session, "local/default", ModelProvider.OLLAMA, "fake-chat",
        external_classes or list(DataClass), is_default=True,
    )  # fmt: skip
    assets = {a["name"]: a for a in (await api.get("/data/assets")).json()}
    r = await api.post("/data/assets", json={"name": "hr-salaries", "data_class": "internal"})
    assets["hr-salaries"] = r.json()
    await api.post(f"/data/assets/{assets['team-docs']['id']}/documents",
                   files={"file": ("hr-policy.md", POLICY_MD.encode(), "text/markdown")})  # fmt: skip
    await api.post(f"/data/assets/{assets['hr-salaries']['id']}/documents",
                   files={"file": ("salaries.md", SECRET_MD.encode(), "text/markdown")})  # fmt: skip
    await app.state.indexer.wait_all()
    return api, app


async def external_chat(session, make_app, tmp_path, secret_class: str | None = None):
    """Chat where an external model with a price is available; team-docs gets a document of the needed class."""
    app = make_app(documents_dir=tmp_path / "docs", agents_dir=AGENTS_DIR,
                   secret_key=Fernet.generate_key().decode())  # fmt: skip
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await api.post("/auth/setup", json=OWNER)
    await sync_agents_dir(session, AGENTS_DIR)
    await register_model(
        session, "local/embeddings", ModelProvider.OLLAMA, "fake-embed", list(DataClass)
    )
    await register_model(
        session,
        "local/default",
        ModelProvider.OLLAMA,
        "fake-chat",
        list(DataClass),
        is_default=True,
    )
    await register_model(session, "openrouter/default", ModelProvider.OPENROUTER, "openai/x",
                         [DataClass.PUBLIC, DataClass.INTERNAL], prices=(Decimal(2), Decimal(10)))  # fmt: skip
    await app.state.secrets.put("openrouter_api_key", "sk-or-test")
    asset = next(a for a in (await api.get("/data/assets")).json() if a["name"] == "team-docs")
    data = {"data_class": secret_class} if secret_class else {}
    await api.post(f"/data/assets/{asset['id']}/documents", data=data,
                   files={"file": ("hr-policy.md", POLICY_MD.encode(), "text/markdown")})  # fmt: skip
    await app.state.indexer.wait_all()
    return api


async def seed_web(session, make_app, backends):
    """Web search: store key, stub answer about vacation."""
    backends.tool_query = "vacation in the USA"
    backends.reply = ["Vacation is 28 days [1]."]
    app = make_app(agents_dir=AGENTS_DIR, secret_key=Fernet.generate_key().decode(),
                   tavily_url="http://tavily")  # fmt: skip
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await api.post("/auth/setup", json=OWNER)
    await sync_agents_dir(session, AGENTS_DIR)
    await register_model(
        session, "local/default", ModelProvider.OLLAMA, "fake-chat", list(DataClass)
    )
    return api, app


async def seed_demo(session, make_app, tmp_path):
    """Owner, models, agents; the HR demo set is not enabled yet."""
    app = make_app(documents_dir=tmp_path / "docs", agents_dir=AGENTS_DIR)
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await api.post("/auth/setup", json=OWNER)
    await sync_agents_dir(session, AGENTS_DIR)
    await register_model(
        session, "local/embeddings", ModelProvider.OLLAMA, "fake-embed", list(DataClass)
    )
    await register_model(
        session,
        "local/default",
        ModelProvider.OLLAMA,
        "fake-chat",
        list(DataClass),
        is_default=True,
    )
    return api, app


async def ask_as(app, backends, user: dict, name: str = "Miller") -> list:
    """A real login with the issued password and a question on which the model calls query_records."""
    backends.chat.clear()
    backends.tool_call = ("query_records", {"name": name})
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await api.post("/auth/login", json={"login": user["login"], "password": user["password"]})
    assert r.status_code == 200
    conversation = (await api.post("/chat/conversations", json={})).json()["id"]
    events = await ask(api, conversation, f"How many vacation days does {name} have left?")
    await api.aclose()
    assert event(events, "tool")["name"] == "query_records"
    return events


async def post_and_leave(app, path: str, body: dict, key: str, leave_after: bytes) -> bytes:
    """POST over raw ASGI; the client leaves (``http.disconnect``) as soon as it gets the chunk with
    ``leave_after``, like Hermes, which drops the connection right after the last answer chunk.

    httpx ASGITransport returns the whole body and cannot disconnect, so the call goes directly.
    """
    import asyncio
    import json

    payload, got, sent = json.dumps(body).encode(), [], asyncio.Event()
    requested = False

    async def receive():
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await sent.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            got.append(message.get("body", b""))
            if leave_after in message.get("body", b""):
                sent.set()

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "client": ("127.0.0.1", 1), "server": ("test", 80),
        "headers": [(b"host", b"test"), (b"content-type", b"application/json"),
                    (b"authorization", f"Bearer {key}".encode())],
    }  # fmt: skip
    await app(scope, receive, send)
    return b"".join(got)
