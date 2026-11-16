"""Node network through Yorath (change node-egress): proxy log → summary, bans and protection in the audit."""

import os

import pytest
from sqlalchemy import select

from tests.fixtures.world import seed_chat
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.journal.egress import ingest, parse_line, source_names, track_state
from yorath.control.journal.models import EgressFlow

pytestmark = pytest.mark.group

T = 1794491932.859  # 12.11.2026 ~16:38 UTC
LINES = [
    f"{T} 172.18.0.2 NONE_NONE/0 CONNECT 8.47.69.0:443 example.com - 0",
    f"{T + 0.2} 172.18.0.2 TCP_TUNNEL/200 CONNECT example.com:443 example.com 8.47.69.0 5399",
    f"{T + 1} 172.18.0.2 TCP_MISS/200 GET http://example.com/ - 8.47.69.0 1256",
    f"{T + 2} 172.17.0.1 TCP_TUNNEL/200 CONNECT example.org:443 example.org 8.6.112.0 5390",
    f"{T + 3} 172.19.0.10 TCP_DENIED/403 GET http://evil.test/x - - 3922",
]
REPORT = {"host_ips": ["172.17.0.1"], "container_ips": {"openclaw-gw": ["172.18.0.2"]}}


def test_parse_line():
    assert parse_line(LINES[0]) is None  # the peek step is not a transfer
    tunnel = parse_line(LINES[1])
    assert (tunnel.source_ip, tunnel.domain, tunnel.bytes, tunnel.blocked) == (
        "172.18.0.2",
        "example.com",
        5399,
        False,
    )
    assert parse_line(LINES[2]).domain == "example.com"  # HTTP: the domain from the URL
    assert parse_line(LINES[4]).blocked is True
    assert parse_line("garbage") is None
    probe = f"{T} 127.0.0.1 NONE_NONE_ABORTED/0 CONNECT 127.0.0.1:3130 - - 0"
    assert parse_line(probe) is None  # the service probing the proxy port is not network access


async def flows(session):
    rows = (
        await session.scalars(select(EgressFlow).order_by(EgressFlow.domain, EgressFlow.source))
    ).all()
    return [(f.source, f.domain, f.requests, f.blocked, f.bytes) for f in rows]


async def test_ingest_aggregates_reads_on_and_survives_rotation(make_app, session, tmp_path):
    _, app = await seed_chat(session, make_app, tmp_path)
    log = tmp_path / "access.log"
    log.write_text("\n".join(LINES[:3]) + "\n" + LINES[3][:20])  # the last line is incomplete
    names = source_names(REPORT)
    await ingest(session, app.state.journal, log, names, "local")
    assert await flows(session) == [("openclaw-gw", "example.com", 2, 0, 6655)]

    with log.open("a") as f:
        f.write(LINES[3][20:] + "\n" + LINES[4] + "\n")
    await ingest(session, app.state.journal, log, names, "local")
    session.expire_all()
    assert await flows(session) == [
        ("172.19.0.10", "evil.test", 1, 1, 3922),
        ("openclaw-gw", "example.com", 2, 0, 6655),
        ("host", "example.org", 1, 0, 5390),
    ]
    blocked = (
        await session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == AuditEventType.EGRESS_BLOCKED)
        )
    ).all()
    assert [e.details["domain"] for e in blocked] == ["evil.test"]

    # rotation: a new file (another inode) is read from the start, old lines are not duplicated
    os.replace(log, tmp_path / "access.log.0")
    log.write_text(LINES[1] + "\n")
    await ingest(session, app.state.journal, log, names, "local")
    session.expire_all()
    assert ("openclaw-gw", "example.com", 3, 0, 12054) in await flows(session)


@pytest.mark.covers("INV-NET-1")
async def test_protection_changes_are_audited(make_app, session, tmp_path):
    _, app = await seed_chat(session, make_app, tmp_path)
    on = {
        "egress": {
            "mode": "on",
            "table": True,
            "intact": True,
            "proxy": True,
            "enforced": True,
            "alarm": None,
        }
    }
    removed = {"egress": {**on["egress"], "table": False, "intact": False, "enforced": False}}
    unset = {"egress": {"mode": "unset"}}
    j = app.state.journal
    assert (
        await track_state(session, j, unset, "local") is None
    )  # control was never turned on: stay silent
    assert await track_state(session, j, on, "local") is True  # the first turn-on has no event
    assert await track_state(session, j, removed, "local") is False
    assert await track_state(session, j, removed, "local") is False  # no repeat
    assert await track_state(session, j, on, "local") is True
    events = (await session.scalars(select(AuditEvent).where(
        AuditEvent.event_type.in_([AuditEventType.EGRESS_UNPROTECTED, AuditEventType.EGRESS_RESTORED])
    ).order_by(AuditEvent.occurred_at))).all()  # fmt: skip
    assert [(e.event_type, e.details["table"]) for e in events] == [
        (AuditEventType.EGRESS_UNPROTECTED, False),
        (AuditEventType.EGRESS_RESTORED, True),
    ]


async def test_owner_reads_egress_summary(make_app, session, tmp_path):
    owner, app = await seed_chat(session, make_app, tmp_path)
    log = tmp_path / "access.log"
    log.write_text("\n".join(LINES) + "\n")
    await ingest(session, app.state.journal, log, source_names(REPORT), "local")
    rows = (await owner.get("/journal/egress?hours=720")).json()
    assert {(r["source"], r["domain"], r["requests"], r["blocked"]) for r in rows} == {
        ("openclaw-gw", "example.com", 2, 0),
        ("host", "example.org", 1, 0),
        ("172.19.0.10", "evil.test", 1, 1),
    }
    await owner.aclose()
