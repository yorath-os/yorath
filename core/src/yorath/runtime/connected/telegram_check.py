"""Telegram bot check of a connected agent (change telegram-check).

The node service asks Telegram ``getMe`` with the agent's bot token by the agent's own network path:
its container is in Docker's network, so HTTPS goes through the node proxy like the agent's. The
answer tells the owner what the agent's log would: Telegram is not reachable from this server, the
token is rejected, or the agent's internet is off. The token never leaves this module: the result
carries the bot name, a state and at most a status code or an error class.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

log = logging.getLogger("yorath.node_agent")

API = "https://api.telegram.org"
TIMEOUT_S = 10
RECHECK_S = 600  # a result older than 10 minutes is checked again


def check(token: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """``getMe`` → ``ok`` (with ``bot``), ``rejected`` (401/404) or ``unreachable``."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        # trust_env=False: the host's provider proxy is not the agent's path
        with httpx.Client(timeout=TIMEOUT_S, trust_env=False, transport=transport) as http:
            r = http.get(f"{API}/bot{token}/getMe")
    except httpx.HTTPError as exc:
        return {"state": "unreachable", "detail": type(exc).__name__, "checked_at": now}
    if r.status_code in (401, 404):
        return {"state": "rejected", "detail": f"HTTP {r.status_code}", "checked_at": now}
    if r.status_code == 200:
        try:
            body = r.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and body.get("ok"):
            bot = (body.get("result") or {}).get("username")
            return {"state": "ok", "bot": str(bot)[:64] if bot else None, "checked_at": now}
    # 5xx from the node proxy (it could not reach Telegram) or anything unexpected on the way
    return {"state": "unreachable", "detail": f"HTTP {r.status_code}", "checked_at": now}


def bot_token(item: dict[str, Any]) -> str | None:
    """The bot token from the agent's desired configuration (OpenClaw env, Hermes .env)."""
    config = item.get("config") or {}
    for part in ("env", "dotenv"):
        token = (config.get(part) or {}).get("TELEGRAM_BOT_TOKEN")
        if token:
            return str(token)
    return None


@dataclass
class TelegramChecks:
    """Last result per agent; a new token, a new owner request or an internet switch checks now."""

    transport: httpx.BaseTransport | None = None
    clock: Any = time.monotonic
    _last: dict[str, tuple[tuple[str, str, bool], float, dict[str, Any]]] = field(
        default_factory=dict
    )

    def result(self, item: dict[str, Any], internet_off: bool) -> dict[str, Any] | None:
        token = bot_token(item)
        agent = item["agent"]
        if not token:
            self._last.pop(agent, None)
            return None
        key = (hashlib.sha256(token.encode()).hexdigest(), str(item.get("telegram_check", "")),
               internet_off)  # fmt: skip
        last = self._last.get(agent)
        if last is not None and last[0] == key and self.clock() - last[1] < RECHECK_S:
            return last[2]
        if internet_off:
            found = {"state": "internet_off",
                     "checked_at": datetime.now(UTC).isoformat(timespec="seconds")}  # fmt: skip
        else:
            found = check(token, self.transport)
        if last is None or last[2].get("state") != found["state"]:
            log.info("agent %s: Telegram check %s %s", agent, found["state"],
                     found.get("detail") or found.get("bot") or "")  # fmt: skip
        self._last[agent] = (key, self.clock(), found)
        return found
