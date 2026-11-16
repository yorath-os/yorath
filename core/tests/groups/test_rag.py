"""Index and search on a live database with fake embeddings: the access filter comes before the query."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from tests.fixtures.corpus import DIM, OWNER, POLICY_MD, SECRET_MD, fake_vector
from tests.fixtures.world import register_model
from yorath.control.agents.loader import AgentCard
from yorath.control.model import (
    Agent,
    AgentMode,
    Chunk,
    DataClass,
    Document,
    DocumentStatus,
    Hosting,
    ModelProvider,
    User,
)
from yorath.enforcement.pdp import CedarPDP
from yorath.enforcement.policy_store import builtin_policies, combine
from yorath.runtime.rag.indexer import IndexError_, index_document
from yorath.runtime.rag.search import search

pytestmark = pytest.mark.group


class FakeEmbed:
    model = "fake"

    async def embed(self, texts):
        return [fake_vector(t) for t in texts]


async def seed(session, make_app, tmp_path):
    """Owner, two sources (team-docs internal, hr-salaries restricted), documents, models."""
    app = make_app(documents_dir=tmp_path / "docs")
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await api.post("/auth/setup", json=OWNER)
    await register_model(session, "local/embeddings", ModelProvider.OLLAMA, "fake", list(DataClass))
    assets = {a["name"]: a for a in (await api.get("/data/assets")).json()}
    r = await api.post("/data/assets", json={"name": "hr-salaries", "data_class": "restricted"})
    assets["hr-salaries"] = r.json()
    r = await api.post(
        f"/data/assets/{assets['team-docs']['id']}/documents",
        files={"file": ("hr-policy.md", POLICY_MD.encode(), "text/markdown")},
    )
    policy_id = uuid.UUID(r.json()["id"])
    r = await api.post(
        f"/data/assets/{assets['hr-salaries']['id']}/documents",
        files={"file": ("salaries.md", SECRET_MD.encode(), "text/markdown")},
    )
    salaries_id = uuid.UUID(r.json()["id"])
    await app.state.indexer.wait_all()
    owner = await session.scalar(select(User).where(User.role == "owner"))
    return api, app, owner, policy_id, salaries_id


def agent_with(data: list[str], max_class: str = "confidential") -> tuple[Agent, AgentCard]:
    card = AgentCard.model_validate(
        {
            "name": "document-assistant",
            "mode": "delegated",
            "data": data,
            "max_data_class": max_class,
        }
    )
    agent = Agent(name="document-assistant", display_name="DA", hosting=Hosting.MANAGED,
                  mode=AgentMode.DELEGATED, runtime_type_id=uuid.uuid7())  # fmt: skip
    return agent, card


@pytest.fixture
def pdp():
    p = CedarPDP()
    p.load(combine(builtin_policies()))
    return p


async def test_upload_triggers_indexing_and_chunks(make_app, session, tmp_path):
    api, app, owner, policy_id, salaries_id = await seed(session, make_app, tmp_path)
    doc = await session.get(Document, policy_id)
    await session.refresh(doc)
    assert doc.status is DocumentStatus.INDEXED and doc.error is None
    chunks = (
        await session.scalars(
            select(Chunk).where(Chunk.document_id == policy_id).order_by(Chunk.ordinal)
        )
    ).all()
    assert [c.heading_path for c in chunks] == [
        "Vacation policy > Carryover",
        "Vacation policy > Request",
    ]
    assert chunks[0].text.startswith("[hr-policy > Vacation policy > Carryover]")
    assert chunks[0].data_class is DataClass.INTERNAL and len(chunks[0].embedding) == DIM
    # reindexing is idempotent, deleting the document removes its chunks
    r = await api.post(f"/data/documents/{policy_id}/index")
    assert r.status_code == 200 and r.json()["chunks"] == 2
    assert (
        await session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == policy_id)
        )
        == 2
    )
    await api.delete(f"/data/documents/{policy_id}")
    assert (
        await session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == policy_id)
        )
        == 0
    )
    await api.aclose()


@pytest.mark.covers("INV-CLS-1", "INV-POL-2")
async def test_search_filters_by_agent_and_class(make_app, session, tmp_path, pdp):
    api, app, owner, policy_id, salaries_id = await seed(session, make_app, tmp_path)
    params = app.state.manifest.rag.model_copy(update={"min_similarity": 0.2})  # bag of words

    agent, card = agent_with(["team-docs", "hr-salaries"], max_class="confidential")
    res = await search(session, pdp, FakeEmbed(), "how many vacation days can I carry over", principal=owner,
                       agent=agent, card=card, params=params)  # fmt: skip
    assert res.hits and res.hits[0].heading_path == "Vacation policy > Carryover"
    assert "56" in res.hits[0].text and res.max_data_class is DataClass.INTERNAL
    # a restricted source above the confidential agent ceiling → deny already at the source level
    assert res.allowed_assets == ["team-docs"]
    assert "agent-class-ceiling" in {d.name: d for d in res.denied}["hr-salaries"].reasons
    # the salary does not get in even on an exact word
    res = await search(session, pdp, FakeEmbed(), "Miller salary", principal=owner,
                       agent=agent, card=card, params=params)  # fmt: skip
    assert all(h.asset_name != "hr-salaries" for h in res.hits)

    # the source is not given to the agent → deny with a reason, no hits from it
    agent2, card2 = agent_with(["team-docs"], max_class="restricted")
    res = await search(session, pdp, FakeEmbed(), "Miller salary", principal=owner,
                       agent=agent2, card=card2, params=params)  # fmt: skip
    denied = {d.name: d for d in res.denied}
    assert (
        "hr-salaries" in denied and "agent-not-allowed-for-asset" in denied["hr-salaries"].reasons
    )
    assert all(h.asset_name == "team-docs" for h in res.hits)

    # the agent may use everything and the ceiling is restricted → the salary is found, result label restricted
    agent3, card3 = agent_with(["team-docs", "hr-salaries"], max_class="restricted")
    res = await search(session, pdp, FakeEmbed(), "Miller salary", principal=owner,
                       agent=agent3, card=card3, params=params)  # fmt: skip
    assert res.hits[0].asset_name == "hr-salaries" and res.max_data_class is DataClass.RESTRICTED

    # a user without the role the source is given to → deny by user rights
    r = await api.post(
        "/users",
        json={
            "login": "guest",
            "password": "guest-pass-1",
            "display_name": "Guest",
            "role": "guest",
        },
    )
    guest = await session.get(User, uuid.UUID(r.json()["id"]))
    res = await search(
        session,
        pdp,
        FakeEmbed(),
        "carry over",
        principal=guest,
        agent=agent3,
        card=card3,
        params=params,
    )
    assert res.hits == [] and all(not d.allowed for d in res.decisions)
    assert all(d.reason_text == "no permitting policy" for d in res.decisions)

    # the owner's debug search through the API
    r = await api.get("/data/search", params={"q": "how many days ahead to submit a request"})
    assert r.status_code == 200
    body = r.json()
    assert body["hits"][0]["heading_path"] == "Vacation policy > Request"
    assert {a["name"] for a in body["assets"]} == {"team-docs", "hr-salaries"}
    await api.aclose()


async def test_indexing_without_embeddings_model_fails_honestly(make_app, session, tmp_path):
    app = make_app(documents_dir=tmp_path / "docs")
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await api.post("/auth/setup", json=OWNER)
    assets = (await api.get("/data/assets")).json()
    r = await api.post(
        f"/data/assets/{assets[0]['id']}/documents",
        files={"file": ("a.md", b"# A\n\ntext", "text/markdown")},
    )
    doc_id = r.json()["id"]
    await app.state.indexer.wait_all()
    doc = await session.get(Document, uuid.UUID(doc_id))
    await session.refresh(doc)
    assert doc.status is DocumentStatus.PARSED  # indexing did not start: there is no model
    r = await api.post(f"/data/documents/{doc_id}/index")
    assert r.status_code == 409 and "embeddings" in r.json()["detail"]
    with pytest.raises(IndexError_):
        await index_document(session, FakeEmbed(), uuid.uuid7(), app.state.manifest.rag)
    await api.aclose()
