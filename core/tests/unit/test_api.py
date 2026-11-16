async def ok():
    return True, "fine"


async def down():
    return False, "ConnectError: refused"


async def test_health_ok(app, client):
    app.state.checks = {"db": ok, "ollama": ok}
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["checks"]["db"]["ok"] is True


async def test_health_degraded_when_ollama_down(app, client):
    app.state.checks = {"db": ok, "ollama": down}
    r = await client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["ollama"]["ok"] is False and body["checks"]["db"]["ok"] is True


async def test_version_reports_manifest(app, client):
    r = await client.get("/version")
    assert r.status_code == 200
    body = r.json()
    m = app.state.manifest
    assert body["distribution"]["version"] == m.distribution.version
    assert body["images"] == m.images.model_dump()
    assert body["schema_matches_manifest"] is True
    assert body["build"] == "test"
    assert body["plugins"] == []
