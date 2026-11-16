"""Node network journal (change node-egress, decision 36): proxy access.log → summary and audit.

The node proxy (Squid in the host network) writes a line per connection:
``<time> <source> <result>/<code> <method> <URL> <SNI> <destination> <bytes>``. Only denials
and protection state changes go to the audit log; allowed traffic goes as an hourly
"source → domain" summary to ``egress_flows`` (kept for ``journal.trace_retention_days``). Source
names come from the ``yorath scan`` report: container and host IPs.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yorath.control.journal.events import AuditEventType
from yorath.control.journal.journal import Journal
from yorath.control.journal.models import EgressFlow
from yorath.control.model import get_setting, set_setting
from yorath.runtime.connected.limits import BLOCKED_EVENTS

log = logging.getLogger(__name__)

OFFSET_KEY = "egress_log_offset"
STATE_KEY = "egress_protected"
HOST = "host"
# NONE_*: the peek step of HTTPS interception (the transfer itself is the next line), a drop
# before connecting, the yorath-egress port check of the proxy: not network calls


@dataclass(frozen=True)
class Entry:
    at: datetime
    source_ip: str
    status: str
    domain: str
    bytes: int
    blocked: bool


def parse_line(line: str) -> Entry | None:
    """A node proxy log line; service peek lines and garbage give ``None``."""
    parts = line.split()
    if len(parts) < 8:
        return None
    ts, src, status, method, url, sni = parts[:6]
    size = parts[7] if len(parts) > 7 else "0"
    if status.startswith("NONE"):
        return None
    domain = sni if sni not in ("-", "") else ""
    if not domain:
        host = url.split(":", 1)[0] if method == "CONNECT" else (urlsplit(url).hostname or "")
        domain = host
    try:
        at = datetime.fromtimestamp(float(ts), UTC)
        size_n = int(size) if size.isdigit() else 0
    except ValueError:
        return None
    if not domain:
        return None
    return Entry(
        at=at,
        source_ip=src,
        status=status,
        domain=domain.lower()[:300],
        bytes=size_n,
        blocked=status.startswith("TCP_DENIED") or status.endswith("/403"),
    )


def source_names(report: dict[str, Any], agents: dict[str, str] | None = None) -> dict[str, str]:
    """IP → source name: connected agents by fixed catalog addresses, containers by the
    ``yorath scan`` report, host addresses as "host". An agent name beats a container name."""
    names = {ip: HOST for ip in report.get("host_ips", [])}
    for name, ips in (report.get("container_ips") or {}).items():
        for ip in ips:
            names[ip] = name
    names.update(agents or {})
    names.setdefault("127.0.0.1", HOST)
    names.setdefault("::1", HOST)
    return names


def read_report(report_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((report_dir / "host.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}


async def ingest(
    session: AsyncSession,
    journal: Journal | None,
    log_path: Path,
    names: dict[str, str],
    node: str,
) -> int:
    """New proxy log lines → hourly summary; denials → ``egress.blocked``.

    The read position is stored in settings together with the inode: after rotation the file is
    read from the start.
    """
    try:
        stat = log_path.stat()
    except OSError:
        return 0
    state = await get_setting(session, OFFSET_KEY, {}) or {}
    offset = state.get("offset", 0) if state.get("inode") == stat.st_ino else 0
    if offset > stat.st_size:
        offset = 0
    with log_path.open("rb") as f:
        f.seek(offset)
        chunk = f.read(8 * 1024 * 1024)  # at most 8 MB per pass, the rest next time
    end = chunk.rfind(b"\n") + 1  # an unfinished line is read to the end next time
    lines = chunk[:end].decode(errors="replace").splitlines()
    await ingest_lines(session, journal, lines, names, node)
    await set_setting(session, OFFSET_KEY, {"inode": stat.st_ino, "offset": offset + end})
    await session.commit()
    return len(lines)


async def ingest_lines(
    session: AsyncSession, journal: Journal | None, lines: list[str], names: dict[str, str],
    node: str,
) -> None:  # fmt: skip
    """Proxy log lines → hourly summary; denials → ``egress.blocked``.

    Lines come from the main node proxy file (``ingest``) or the worker node service (``POST
    /node/egress``, change cluster-nodes): a node's network journal goes to the main node, internet
    traffic goes out from the node.
    """
    flows: dict[tuple[datetime, str, str], dict[str, Any]] = {}
    blocked: list[Entry] = []
    for line in lines:
        entry = parse_line(line)
        if entry is None:
            continue
        hour = entry.at.replace(minute=0, second=0, microsecond=0)
        key = (hour, entry.source_ip, entry.domain)
        f_ = flows.setdefault(
            key, {"requests": 0, "blocked": 0, "bytes": 0, "first": entry.at, "last": entry.at}
        )
        f_["requests"] += 1
        f_["blocked"] += int(entry.blocked)
        f_["bytes"] += entry.bytes
        f_["first"] = min(f_["first"], entry.at)
        f_["last"] = max(f_["last"], entry.at)
        if entry.blocked:
            blocked.append(entry)
    for (hour, ip, domain), f_ in flows.items():
        stmt = insert(EgressFlow).values(
            hour=hour, node=node, source_ip=ip, source=names.get(ip, ip), domain=domain,
            requests=f_["requests"], blocked=f_["blocked"], bytes=f_["bytes"],
            first_at=f_["first"], last_at=f_["last"],
        )  # fmt: skip
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["hour", "node", "source_ip", "domain"],
                set_={
                    "requests": EgressFlow.requests + stmt.excluded.requests,
                    "blocked": EgressFlow.blocked + stmt.excluded.blocked,
                    "bytes": EgressFlow.bytes + stmt.excluded.bytes,
                    "last_at": stmt.excluded.last_at,
                    "source": stmt.excluded.source,
                },
            )
        )
    await session.commit()
    if journal is not None:
        # a flood of denials stays in the hourly summary above; the audit gets the first ones
        # (pre-public-hardening S6): a node cannot bury the journal under its lines
        if len(blocked) > BLOCKED_EVENTS:
            log.warning("egress: %d denials from %s, %d go to the audit", len(blocked), node,
                        BLOCKED_EVENTS)  # fmt: skip
        for e in blocked[:BLOCKED_EVENTS]:
            await journal.audit(
                AuditEventType.EGRESS_BLOCKED,
                details={"node": node, "source": names.get(e.source_ip, e.source_ip),
                         "source_ip": e.source_ip, "domain": e.domain, "at": e.at.isoformat()},
            )  # fmt: skip


def protected(report: dict[str, Any]) -> bool | None:
    """Is the node protected: ``None`` if control was never on (nothing to lose), else yes/no."""
    egress = report.get("egress") or {}
    mode = egress.get("mode")
    if mode in (None, "unset"):
        return None
    return mode == "on" and bool(egress.get("enforced")) and not egress.get("alarm")


async def track_state(
    session: AsyncSession, journal: Journal | None, report: dict[str, Any], node: str
) -> bool | None:
    """Protection state change → ``egress.unprotected`` / ``egress.restored`` in the audit log."""
    now = protected(report)
    before = await get_setting(session, STATE_KEY, None)
    if now is None or now == before:
        return now
    await set_setting(session, STATE_KEY, now)
    if journal is not None and (before is not None or now is False):
        egress = report.get("egress") or {}
        await journal.audit(
            AuditEventType.EGRESS_RESTORED if now else AuditEventType.EGRESS_UNPROTECTED,
            details={"node": node, "mode": egress.get("mode"), "table": egress.get("table"),
                     "intact": egress.get("intact"), "proxy": egress.get("proxy"),
                     "alarm": egress.get("alarm"), "checked_at": report.get("checked_at")},
        )  # fmt: skip
    return now


async def egress_loop(
    sessions: async_sessionmaker[AsyncSession],
    journal: Journal,
    log_path: Path,
    report_dir: Path,
    node: str,
    interval_s: float = 60,
    agents: dict[str, str] | None = None,
) -> None:
    """Core background task: proxy log and protection state once a minute; errors don't kill it."""
    while True:
        try:
            report = read_report(report_dir)
            async with sessions() as s:
                await ingest(s, journal, log_path, source_names(report, agents), node)
                await track_state(s, journal, report, node)
        except Exception:  # noqa: BLE001
            log.exception("egress journal failed")
        await asyncio.sleep(interval_s)
