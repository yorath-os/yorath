"""Data registry on a live database: sources, upload with parsing, class, duplicates, deletion, audit."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.fixtures.corpus import MINIMAL_PDF, OWNER
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import DataAsset, Document

pytestmark = pytest.mark.group


async def owner_api(make_app, tmp_path) -> AsyncClient:
    app = make_app(documents_dir=tmp_path / "docs", max_upload_mb=1)
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await api.post("/auth/setup", json=OWNER)
    return api


@pytest.mark.covers("INV-CLS-4")
async def test_assets_and_upload_flow(make_app, session, tmp_path):
    api = await owner_api(make_app, tmp_path)
    assets = (await api.get("/data/assets")).json()
    assert [a["name"] for a in assets] == ["team-docs"]  # the default source
    team = assets[0]
    assert team["data_class"] == "internal" and team["access_rules"]["roles"] == ["owner", "member"]

    r = await api.post(
        "/data/assets",
        json={"name": "hr-policy", "data_class": "confidential", "access_rules": {"roles": ["hr"]}},
    )
    assert r.status_code == 201
    hr = r.json()
    assert (await api.post("/data/assets", json={"name": "hr-policy"})).status_code == 409

    r = await api.post(
        f"/data/assets/{team['id']}/documents",
        files={"file": ("policy.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "parsed" and doc["data_class"] == "internal" and doc["pages"] == 1
    assert (tmp_path / "docs" / f"{doc['id']}.pdf").is_file()

    detail = (await api.get(f"/data/documents/{doc['id']}")).json()
    assert "Vacation policy" in detail["text_preview"] and detail["text_chars"] > 0

    r = await api.post(
        f"/data/assets/{team['id']}/documents",
        files={"file": ("policy.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert r.status_code == 409 and r.json()["detail"]["document_id"] == doc["id"]

    r = await api.post(
        f"/data/assets/{hr['id']}/documents",
        files={"file": ("notes.md", b"# x\n\ntext", "text/markdown")},
        data={"data_class": "public"},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "class_below_asset"
    r = await api.post(
        f"/data/assets/{hr['id']}/documents",
        files={"file": ("notes.md", b"# x\n\ntext", "text/markdown")},
        data={"data_class": "restricted"},
    )
    assert r.status_code == 201 and r.json()["data_class"] == "restricted"

    r = await api.post(
        f"/data/assets/{team['id']}/documents",
        files={"file": ("scan.pdf", b"not a pdf", "application/pdf")},
    )
    assert r.status_code == 201 and r.json()["status"] == "failed" and "PDF" in r.json()["error"]

    r = await api.post(
        f"/data/assets/{team['id']}/documents",
        files={"file": ("big.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
    )
    assert r.status_code == 413
    r = await api.post(
        f"/data/assets/{team['id']}/documents",
        files={"file": ("sheet.xlsx", b"zip", "application/octet-stream")},
    )
    assert r.status_code == 415

    docs = (await api.get(f"/data/documents?asset_id={team['id']}")).json()
    assert {d["filename"] for d in docs} == {"policy.pdf", "scan.pdf"}
    assets = {a["name"]: a for a in (await api.get("/data/assets")).json()}
    assert assets["team-docs"]["documents"] == 2 and assets["hr-policy"]["documents"] == 1

    assert (await api.delete(f"/data/documents/{doc['id']}")).status_code == 204
    assert not (tmp_path / "docs" / f"{doc['id']}.pdf").exists()
    assert await session.get(Document, doc["id"]) is None

    events = (
        await session.scalars(
            select(AuditEvent.event_type).where(
                AuditEvent.event_type.in_(
                    [AuditEventType.DOCUMENT_UPLOADED, AuditEventType.DOCUMENT_DELETED]
                )
            )
        )
    ).all()
    assert events.count(AuditEventType.DOCUMENT_UPLOADED) == 3
    assert events.count(AuditEventType.DOCUMENT_DELETED) == 1
    deleted = await session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.DOCUMENT_DELETED)
    )
    assert deleted.details["filename"] == "policy.pdf" and deleted.data_class.value == "internal"

    r = await api.patch(f"/data/assets/{hr['id']}", json={"enabled": False, "description": "off"})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert (await session.get(DataAsset, hr["id"])).description == "off"
    await api.aclose()


@pytest.mark.covers("INV-ID-2")
async def test_member_cannot_manage_data(make_app, tmp_path):
    api = await owner_api(make_app, tmp_path)
    await api.post(
        "/users", json={"login": "anna", "password": "anna-pass-1", "display_name": "Anna"}
    )
    await api.post("/auth/logout")
    await api.post("/auth/login", json={"login": "anna", "password": "anna-pass-1"})
    assert (await api.get("/data/assets")).status_code == 403
    assert (await api.get("/data/documents")).status_code == 403
    await api.aclose()
