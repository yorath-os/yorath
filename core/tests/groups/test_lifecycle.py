"""Core side of `yorath` on the host: what a restore would replace and its audit record."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from tests.fixtures.world import open_for_owner, seed_chat
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.distribution.lifecycle import changes_since, record_restore

pytestmark = pytest.mark.group


async def test_changes_since_counts_only_data_after_moment(make_app, session, tmp_path, backends):
    before_seed = datetime.now(UTC)
    api, app = await seed_chat(session, make_app, tmp_path)
    after_seed = datetime.now(UTC)
    await open_for_owner(app, session)

    seeded = await changes_since(session, before_seed)
    assert seeded["documents"] == 2 and seeded["executions"] == 1
    later = await changes_since(session, after_seed)
    assert later == {"executions": 1, "messages": 0, "documents": 0, "audit_events": 1}
    assert await changes_since(session, datetime.now(UTC)) == {
        "executions": 0, "messages": 0, "documents": 0, "audit_events": 0,
    }  # fmt: skip
    await api.aclose()


async def test_restore_is_recorded_in_audit(make_app, session, tmp_path, backends):
    app = make_app(documents_dir=tmp_path / "docs")
    backup_at = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)
    await record_restore(
        app.state.journal,
        backup_at=backup_at,
        version_before="0.2.1",
        version_after="0.2.0",
        replaced={"executions": 3, "messages": 0, "documents": 1, "audit_events": 17},
        safety_backup="/var/lib/yorath/backups/20261110T040000Z-0.2.1-safety",
        reason="rollback",
    )
    event = await session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.INSTALLATION_RESTORED)
    )
    assert event.reason == "rollback"
    assert event.details == {
        "backup_at": "2026-11-10T03:00:00+00:00",
        "version_before": "0.2.1",
        "version_after": "0.2.0",
        "replaced": {"executions": 3, "messages": 0, "documents": 1, "audit_events": 17},
        "safety_backup": "/var/lib/yorath/backups/20261110T040000Z-0.2.1-safety",
    }
