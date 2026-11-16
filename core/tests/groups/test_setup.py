"""Setup wizard on a live database: setup code, hardware, models via a fake Ollama,
OpenRouter, secrets, inventory, bootstrap."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from tests.fixtures.corpus import AGENTS_DIR, OWNER
from tests.fixtures.world import ask
from yorath.control.agents.loader import sync_agents_dir
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import ModelResource, Node, Policy, Secret, get_setting
from yorath.control.setup.hardware import HardwareInfo
from yorath.control.setup.inventory import upsert_node
from yorath.enforcement.secrets import DbSecretStore, SecretStoreUnavailable

pytestmark = pytest.mark.group


async def owner_client(make_app, **overrides) -> AsyncClient:
    app = make_app(**overrides)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await client.post("/auth/setup", json={**OWNER, "code": overrides.get("setup_code")})
    return client


@pytest.mark.covers("INV-ID-5")
async def test_setup_code_is_enforced(make_app, session):
    app = make_app(setup_code="ABCD1234")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        assert (await api.post("/auth/setup", json=OWNER)).status_code == 403
        r = await api.post("/auth/setup", json={**OWNER, "code": "WRONG"})
        assert r.status_code == 403 and r.json()["detail"] == "invalid_setup_code"
        r = await api.post("/auth/setup", json={**OWNER, "code": "ABCD1234"})
        assert r.status_code == 201
    failed = await session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.event_type == AuditEventType.AUTH_LOGIN_FAILED)
    )
    assert failed == 2


async def test_hardware_and_models_and_pull(make_app, session):
    api = await owner_client(make_app)
    hw = (await api.get("/setup/hardware")).json()
    assert hw["tier"]["tier"] in {"below_minimum", "minimum", "acceptable", "recommended", "gpu"}
    assert hw["hardware"]["vcpu"] >= 1 and hw["minimum"] == {"vcpu": 4, "ram_gb": 8}

    models = (await api.get("/setup/models")).json()
    assert models["recommended"]["embeddings"] == "embeddinggemma-2:570m"
    assert models["installed"] == ["embeddinggemma-2:570m"] and models["registered"] == []

    async with api.stream("POST", "/setup/models/pull", json={"model": "gemma4:e2b"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) async for line in r.aiter_lines() if line]
    assert events[1]["completed"] == 500 and events[-2]["status"] == "success"
    assert events[-1] == {"status": "registered", "name": "local/default"}

    await api.post(
        "/setup/models/pull", json={"model": "embeddinggemma-2:570m", "role": "embeddings"}
    )
    rows = {m.name: m for m in (await session.scalars(select(ModelResource))).all()}
    assert rows["local/default"].model_id == "gemma4:e2b"
    assert set(rows["local/default"].allowed_data_classes) == {
        "public",
        "internal",
        "confidential",
        "restricted",
    }
    assert rows["local/embeddings"].model_id == "embeddinggemma-2:570m"
    # re-registration is idempotent
    await api.post("/setup/models/pull", json={"model": "gemma4:e2b"})
    # the pointer, the catalog model behind it and embeddings
    names = sorted((await session.scalars(select(ModelResource.name))).all())
    assert names == ["local/default", "local/embeddings", "local/gemma4:e2b"]

    status = (await api.get("/setup/status")).json()
    assert status["models"]["chat"] == "gemma4:e2b" and status["setup_completed"] is False
    assert (await api.post("/setup/complete")).json()["setup_completed"] is True
    assert (await api.get("/setup/status")).json()["setup_completed"] is True
    await api.aclose()


async def test_region_block_is_not_called_invalid_key(make_app, session, backends):
    backends.openrouter_denied = True
    api = await owner_client(
        make_app,
        secret_key=Fernet.generate_key().decode(),
        openrouter_url="http://openrouter/api/v1",
    )
    r = await api.put("/setup/openrouter", json={"api_key": "sk-or-valid-key", "consent": True})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "provider_access_denied"
    assert "regional block" in r.json()["detail"]["message"]
    assert await session.scalar(select(func.count()).select_from(Secret)) == 0
    await api.aclose()


@pytest.mark.covers("INV-MOD-6", "INV-SEC-1")
async def test_openrouter_requires_consent_and_encrypts_key(make_app, session):
    key = Fernet.generate_key().decode()
    api = await owner_client(make_app, secret_key=key, openrouter_url="http://openrouter/api/v1")
    r = await api.put("/setup/openrouter", json={"api_key": "sk-or-valid-key", "consent": False})
    assert r.status_code == 422 and r.json()["detail"] == "consent_required"
    r = await api.put("/setup/openrouter", json={"api_key": "sk-or-bad-key-xxx", "consent": True})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_api_key"
    assert await session.scalar(select(func.count()).select_from(Secret)) == 0

    r = await api.put("/setup/openrouter", json={"api_key": "sk-or-valid-key", "consent": True})
    assert r.status_code == 200
    assert (
        r.json()["model"]["classes"] == ["public", "internal"]
        and "leave the server" in r.json()["leaves_server"]
    )
    secret = await session.scalar(select(Secret).where(Secret.name == "openrouter_api_key"))
    assert "sk-or-valid-key" not in secret.ciphertext
    assert (await api.get("/setup/status")).json()["openrouter_configured"] is True
    assert await get_setting(session, "openrouter_consent") is True
    await api.aclose()


async def test_openrouter_without_secret_key_is_refused(make_app):
    api = await owner_client(make_app, openrouter_url="http://openrouter/api/v1")
    r = await api.put("/setup/openrouter", json={"api_key": "sk-or-valid-key", "consent": True})
    assert r.status_code == 503 and r.json()["detail"] == "secret_store_unavailable"
    await api.aclose()


@pytest.mark.covers("INV-SEC-1")
async def test_secret_store_roundtrip_and_wrong_key(sessions):
    k1, k2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    store = DbSecretStore(sessions, k1)
    await store.put("token", "value-1")
    await store.put("token", "value-2")
    assert await store.get("token") == "value-2" and "token" in await store.names()
    with pytest.raises(SecretStoreUnavailable):
        await DbSecretStore(sessions, k2).get("token")
    with pytest.raises(SecretStoreUnavailable):
        await DbSecretStore(sessions, None).put("x", "y")
    await store.delete("token")
    assert await store.get("token") is None


async def test_inventory_upsert(session):
    hw = HardwareInfo(vcpu=4, ram_gb=8, disk_free_gb=40)
    await upsert_node(session, "local", hw)
    node = await upsert_node(
        session,
        "local",
        HardwareInfo(vcpu=8, ram_gb=16, disk_free_gb=40, gpu_name="A10", vram_gb=24),
    )
    rows = (await session.scalars(select(Node).where(Node.name == "local"))).all()
    assert len(rows) == 1 and rows[0].cpu_count == 8 and rows[0].gpu == "A10" and node.updated_at
    assert rows[0].gpu_count == 0  # the card count was not passed

    two = HardwareInfo(vcpu=8, ram_gb=16, disk_free_gb=40, gpu_name="A10", vram_gb=24, gpu_count=2)
    assert (await upsert_node(session, "local", two)).gpu_count == 2
    assert (await upsert_node(session, "local", hw)).gpu_count == 0


@pytest.mark.parametrize(
    ("env", "count"),
    [
        ({"gpu_name": "A10", "gpu_count": 2}, 2),
        ({"gpu_name": "A10"}, 1),
        ({}, 0),
        ({"gpu_count": ""}, 0),
    ],
)
def test_gpu_count_from_installer(env, count):
    from yorath.control.setup.hardware import detect
    from yorath.settings import Settings

    assert detect(Settings(build_sha="test", **env)).gpu_count == count


@pytest.mark.covers("INV-DIST-3")
async def test_bootstrap_on_fresh_database(database_url, session):
    env = {**os.environ, "YORATH_DATABASE_URL": database_url}
    r = subprocess.run(
        [sys.executable, "-m", "yorath.cli", "bootstrap"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "bootstrap: ok" in r.stdout
    assert await session.scalar(select(func.count()).select_from(Policy)) >= 4
    assert await session.scalar(select(func.count()).select_from(Node)) == 1


@pytest.mark.covers("INV-MOD-7")
async def test_budget_blocks_model_that_does_not_fit_and_unloads_previous(make_app, backends):
    backends.tags = []
    api = await owner_client(make_app, node_ram_gb=8)
    models = (await api.get("/setup/models")).json()
    budget = models["budgets"]["gemma4:e2b"]
    assert budget["fits"] is True and budget["limit_gb"] == 7 and budget["estimated"] is False
    assert models["budgets"]["qwen3.5:9b"]["fits"] is False  # 9b on CPU does not fit in 7 GB
    assert models["budgets"]["qwen3.5:9b"]["estimated"] is True

    r = await api.post("/setup/models/pull", json={"model": "qwen3.5:9b"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "does_not_fit"

    assert (await api.post("/setup/models/pull", json={"model": "gemma4:e2b"})).status_code == 200
    assert backends.unloads == []  # there was no previous model
    r = await api.post("/setup/models/pull", json={"model": "qwen3.5:9b", "force": True})
    assert r.status_code == 200 and r.text.strip().endswith('"name": "local/default"}')
    assert backends.unloads == [{"model": "gemma4:e2b", "keep_alive": 0}]
    await api.post("/setup/models/pull", json={"model": "qwen3.5:9b", "force": True})
    assert len(backends.unloads) == 1  # the same model is not unloaded
    await api.aclose()


async def test_openrouter_prices_from_catalog(make_app, session, backends):
    pricing = {"prompt": "0.000002", "completion": "0.00001"}
    backends.openrouter_models = [{"id": "openai/gpt-5.6-luna", "pricing": pricing}]
    key = Fernet.generate_key().decode()
    api = await owner_client(make_app, secret_key=key, openrouter_url="http://openrouter/api/v1")
    r = await api.put("/setup/openrouter", json={"api_key": "sk-or-valid-key", "consent": True})
    assert r.status_code == 200 and r.json()["warnings"] == []
    row = await session.scalar(
        select(ModelResource).where(ModelResource.name == "openrouter/default")
    )
    assert float(row.input_price_per_mtok) == 2 and float(row.output_price_per_mtok) == 10
    resources = (await api.get("/setup/status")).json()["resources"]
    external = next(r for r in resources if r["name"] == "openrouter/default")
    assert external["classes"] == ["public", "internal"]
    assert float(external["input_price_per_mtok"]) == 2
    await api.aclose()


async def test_openrouter_catalog_without_model_warns(make_app):
    key = Fernet.generate_key().decode()
    api = await owner_client(make_app, secret_key=key, openrouter_url="http://openrouter/api/v1")
    r = await api.put("/setup/openrouter", json={"api_key": "sk-or-valid-key", "consent": True})
    assert r.status_code == 200 and r.json()["warnings"] == ["pricing_unavailable"]
    await api.aclose()


@pytest.mark.parametrize(("gpu", "window"), [("A10", 65536), (None, 4096)])
async def test_local_model_window_follows_hardware(make_app, session, backends, gpu, window):
    api = await owner_client(make_app, gpu_name=gpu, gpu_vram_gb=24 if gpu else None)
    await api.post("/setup/models/pull", json={"model": "gemma4:e2b", "force": True})
    row = await session.scalar(select(ModelResource).where(ModelResource.name == "local/default"))
    assert row.context_length == window

    await sync_agents_dir(session, AGENTS_DIR)
    conversation = (await api.post("/chat/conversations", json={})).json()["id"]
    backends.chat.clear()
    await ask(api, conversation, "what is a vacation?")
    assert backends.chat and {b["options"]["num_ctx"] for b in backends.chat} == {window}


async def test_pull_registers_model_after_client_drops_stream(make_app, session):
    """The progress stream broke (proxy, browser tab): pull and registration still finish."""
    import asyncio

    api = await owner_client(make_app)
    app = api._transport.app  # noqa: SLF001 — the same app as the client with the owner cookie
    cookie = "; ".join(f"{k}={v}" for k, v in api.cookies.items())
    body = json.dumps({"model": "gemma4:e2b"}).encode()
    first = asyncio.Event()
    sent: list[dict] = []

    async def receive():
        if not sent:
            sent.append({})
            return {"type": "http.request", "body": body, "more_body": False}
        await first.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            first.set()  # the client got the first line and left

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/setup/models/pull", "raw_path": b"/setup/models/pull",
        "root_path": "", "query_string": b"", "server": ("test", 80), "client": ("127.0.0.1", 1),
        "headers": [(b"host", b"test"), (b"content-type", b"application/json"),
                    (b"cookie", cookie.encode())],
    }  # fmt: skip
    await app(scope, receive, send)
    assert first.is_set()
    await asyncio.gather(*getattr(app.state, "pulls", []))
    row = await session.scalar(select(ModelResource).where(ModelResource.name == "local/default"))
    assert row is not None and row.model_id == "gemma4:e2b"
    await api.aclose()
