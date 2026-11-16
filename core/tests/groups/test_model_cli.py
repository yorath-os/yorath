"""`yorath model` on the server: the same pull as the wizard, no owner password, with audit."""

import io

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import ModelResource
from yorath.control.setup import hardware, model_cli
from yorath.control.setup.hardware import HardwareInfo
from yorath.control.setup.ollama import OllamaClient

pytestmark = pytest.mark.group

A10 = HardwareInfo(vcpu=18, ram_gb=31, disk_free_gb=90, gpu_name="NVIDIA A10", vram_gb=22)
CPU8 = HardwareInfo(vcpu=8, ram_gb=8, disk_free_gb=90)


async def run_pull(app, hw, name=None, force=False) -> tuple[int, str]:
    out = io.StringIO()
    client = OllamaClient(app.state.http, app.state.settings.ollama_url)
    code = await model_cli.pull(
        out, app.state.manifest, hw, app.state.sessions, app.state.journal, client,
        name=name, force=force,
    )  # fmt: skip
    return code, out.getvalue()


async def registered(session) -> AuditEvent | None:
    return await session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.MODEL_REGISTERED)
        .order_by(AuditEvent.occurred_at.desc())
    )  # fmt: skip


async def test_pull_without_name_installs_recommended_with_gpu_window(make_app, session, backends):
    app = make_app()
    code, text = await run_pull(app, A10)
    assert code == 0 and backends.pulls == ["qwen3.5:9b"]
    row = await session.scalar(select(ModelResource).where(ModelResource.name == "local/default"))
    assert (row.model_id, row.context_length) == ("qwen3.5:9b", 65536)
    assert "Done: local/default → qwen3.5:9b, window 65536" in text

    event = await registered(session)
    assert event.actor_principal_id is None and event.model == "qwen3.5:9b"
    assert event.details["source"] == "host" and event.details["previous"] is None


async def test_pull_replaces_previous_model_and_unloads_it(make_app, session, backends):
    app = make_app()
    assert (await run_pull(app, A10, name="gemma4:e2b"))[0] == 0
    assert backends.unloads == []  # anchor: there was no previous model
    assert (await run_pull(app, A10, name="qwen3.5:9b"))[0] == 0
    assert backends.unloads == [{"model": "gemma4:e2b", "keep_alive": 0}]
    session.expire_all()
    assert (await registered(session)).details["previous"] == "gemma4:e2b"


async def test_pull_refuses_model_that_does_not_fit_unless_forced(make_app, session, backends):
    app = make_app()
    code, text = await run_pull(app, CPU8, name="qwen3.5:9b")
    assert code == 2 and backends.pulls == [] and "--force" in text
    assert await registered(session) is None
    code, _ = await run_pull(app, CPU8, name="qwen3.5:9b", force=True)
    assert code == 0 and backends.pulls == ["qwen3.5:9b"]


async def test_model_without_tools_is_registered_with_warning(make_app, session, backends):
    backends.capabilities = ["completion"]
    code, text = await run_pull(make_app(), A10, name="hf.co/someone/chat-GGUF:Q4_K_M")
    assert code == 0 and "not verified by Yorath" in text
    assert "cannot call tools" in text
    assert (await registered(session)).details["capabilities"] == ["completion"]


async def test_wizard_pull_is_audited_with_owner(make_app, session, backends):
    app = make_app()
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    owner = {"login": "owner", "password": "password-1", "display_name": "Owner"}
    me = (await api.post("/auth/setup", json=owner)).json()
    r = await api.post("/setup/models/pull", json={"model": "gemma4:e2b"})
    assert r.status_code == 200 and r.text.strip().endswith('"name": "local/default"}')
    event = await registered(session)
    assert str(event.actor_principal_id) == me["id"] and event.details["source"] == "web"
    await api.aclose()


async def test_status_suggests_recommended_model(make_app, session, backends):
    app = make_app()
    await run_pull(app, A10, name="gemma4:e2b")
    out = io.StringIO()
    await model_cli.show_status(out, app.state.manifest, A10, app.state.sessions)
    text = out.getvalue()
    assert "Main model (local/default): gemma4:e2b, window 65536" in text
    assert "Recommended for this server: qwen3.5:9b" in text and "yorath model pull" in text


async def test_list_says_default_not_main_for_a_model_placed_elsewhere(make_app, session, backends):
    """The default model on a worker node is not "on" the main node: the list says "default"."""
    app = make_app()
    await run_pull(app, A10, name="gemma4:e2b")
    backends.tags = ["embeddinggemma-2:570m", "qwen3.5:4b"]  # gemma4:e2b is not on this server
    out = io.StringIO()
    client = OllamaClient(app.state.http, app.state.settings.ollama_url)
    await model_cli.show_list(out, app.state.manifest, A10, app.state.sessions, client)
    rows = {
        line.split()[0].lstrip("*"): line for line in out.getvalue().splitlines() if "GB" in line
    }
    gemma = next(v for k, v in rows.items() if "gemma4:e2b" in v)
    assert " default " in gemma and "pulled" not in gemma and " main " not in gemma
    qwen4 = next(v for v in rows.values() if "qwen3.5:4b" in v)
    assert " pulled " in qwen4 and "default" not in qwen4


async def test_models_screen_gets_same_catalog_as_cli(make_app, session, backends, monkeypatch):
    # the host is a third party here: CI runners have 2 vCPU, the A10 server has 18
    monkeypatch.setattr(hardware, "cpu_count", lambda: A10.vcpu)
    app = make_app(gpu_name="NVIDIA A10", gpu_vram_gb=22, node_ram_gb=31)
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    owner = {"login": "owner", "password": "password-1", "display_name": "Owner"}
    await api.post("/auth/setup", json=owner)
    data = (await api.get("/setup/models")).json()
    assert data["window"] == 65536
    first = data["catalog"][0]
    assert first["name"] == "qwen3.5:9b" and first["recommended"] and first["fits"] is True
    assert first["memory_gb"] == 5.7  # video memory on GPU, as in yorath model list
    await api.aclose()


async def test_pull_on_gpu_refuses_model_larger_than_vram(make_app, session, backends):
    app = make_app(gpu_name="small", gpu_vram_gb=4, node_ram_gb=64)
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    owner = {"login": "owner", "password": "password-1", "display_name": "Owner"}
    await api.post("/auth/setup", json=owner)
    ok = await api.post("/setup/models/pull", json={"model": "qwen3.5:4b"})
    assert ok.status_code == 200  # anchor: 2.8 GB fits in 4 GB
    r = await api.post("/setup/models/pull", json={"model": "qwen3.5:9b"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "does_not_fit"
    assert backends.pulls == [
        "qwen3.5:4b"
    ]  # 64 GB RAM would be enough, but the model lives in video memory
    await api.aclose()
