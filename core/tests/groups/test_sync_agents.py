"""Revisions from md files: a new revision only when the sha256 changes."""

import pytest
from sqlalchemy import select

from tests.fixtures.corpus import VALID, write
from yorath.control.agents.loader import AgentFileError, sync_agents_dir
from yorath.control.model import Agent, AgentRevision

pytestmark = pytest.mark.group


async def test_sync_creates_revisions_only_on_change(session, tmp_path):
    import uuid

    tag = uuid.uuid7().hex[-6:]
    da = VALID.replace("document-assistant", f"da-{tag}")
    research = da.replace(f"da-{tag}", f"research-{tag}").replace("delegated", "delegated")
    write(tmp_path, "document-assistant.md", da)
    write(tmp_path, "research-agent.md", research)

    first = await sync_agents_dir(session, tmp_path)
    assert [(r.name, r.revision_no, r.created) for r in first] == [
        (f"da-{tag}", 1, True),
        (f"research-{tag}", 1, True),
    ]

    second = await sync_agents_dir(session, tmp_path)
    assert all(not r.created for r in second)

    write(tmp_path, "document-assistant.md", da + "\nNew instruction.\n")
    third = await sync_agents_dir(session, tmp_path)
    assert {(r.name, r.revision_no, r.created) for r in third} == {
        (f"da-{tag}", 2, True),
        (f"research-{tag}", 1, False),
    }

    agent = await session.scalar(select(Agent).where(Agent.name == f"da-{tag}"))
    revs = (
        await session.scalars(
            select(AgentRevision)
            .where(AgentRevision.agent_id == agent.id)
            .order_by(AgentRevision.revision_no)
        )
    ).all()
    assert [r.revision_no for r in revs] == [1, 2] and revs[0].content_sha256 != revs[
        1
    ].content_sha256
    assert revs[1].path == "document-assistant.md" and revs[1].front_matter["name"] == f"da-{tag}"


async def test_sync_rejects_broken_file(session, tmp_path):
    write(tmp_path, "broken.md", VALID.replace("mode: delegated\n", ""))
    with pytest.raises(AgentFileError, match="broken.md"):
        await sync_agents_dir(session, tmp_path)
    await session.rollback()
