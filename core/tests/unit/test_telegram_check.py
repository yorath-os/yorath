"""Telegram bot check by the node service (change telegram-check); Telegram is a mocked third party."""

import logging

import httpx

from yorath.runtime.connected.config import hermes_config, openclaw_config
from yorath.runtime.connected.telegram_check import TelegramChecks, bot_token, check

TOKEN = "123456:secret-bot-token-AAA"


def telegram(status: int, body: dict | None = None, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.url.path)
        return httpx.Response(status, json=body or {})

    return httpx.MockTransport(handler)


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("timed out", request=request)


def item(token: str | None = TOKEN, nonce: str = "") -> dict:
    cfg = hermes_config("key", "entry", 65536, 8642, token, ["42"])
    return {"agent": "hermes", "telegram_check": nonce,
            "config": {"managed": cfg.managed, "env": cfg.env, "dotenv": cfg.dotenv}}  # fmt: skip


def test_telegram_answers_are_read_into_four_states():
    ok = check(TOKEN, telegram(200, {"ok": True, "result": {"username": "yorath_bot"}}))
    assert (ok["state"], ok["bot"]) == ("ok", "yorath_bot")
    assert check(TOKEN, telegram(401, {"ok": False}))["state"] == "rejected"
    assert check(TOKEN, telegram(404, {"ok": False}))["state"] == "rejected"
    proxy = check(TOKEN, telegram(503))  # the node proxy could not reach Telegram
    assert (proxy["state"], proxy["detail"]) == ("unreachable", "HTTP 503")
    timeout = check(TOKEN, httpx.MockTransport(unreachable))
    assert (timeout["state"], timeout["detail"]) == ("unreachable", "ConnectTimeout")


def test_the_token_is_asked_but_never_reported(caplog):
    seen: list[str] = []
    caplog.set_level(logging.INFO, "yorath.node_agent")
    checks = TelegramChecks(transport=telegram(401, seen=seen))
    found = checks.result(item(), internet_off=False)
    assert seen == [f"/bot{TOKEN}/getMe"]  # anchor: the token did go to Telegram
    assert TOKEN not in str(found) and TOKEN not in caplog.text
    assert "Telegram check rejected" in caplog.text


def test_the_bot_token_comes_from_either_agent_config():
    assert bot_token(item()) == TOKEN
    cfg = openclaw_config("key", "entry", 65536, 18789, TOKEN)
    assert bot_token({"config": {"env": cfg.env, "dotenv": cfg.dotenv}}) == TOKEN
    assert bot_token(item(token=None)) is None  # Hermes writes an empty line without a bot


def test_checked_again_on_a_new_request_a_new_token_or_after_ten_minutes():
    seen: list[str] = []
    now = [1000.0]
    checks = TelegramChecks(transport=telegram(503, seen=seen), clock=lambda: now[0])
    checks.result(item(), internet_off=False)
    checks.result(item(), internet_off=False)
    assert len(seen) == 1, "the same bot is not asked on every pass"
    checks.result(item(nonce="a1"), internet_off=False)
    assert len(seen) == 2, "Check again"
    checks.result(item(token="777:other", nonce="a1"), internet_off=False)
    assert len(seen) == 3, "a new token"
    now[0] += 601
    checks.result(item(token="777:other", nonce="a1"), internet_off=False)
    assert len(seen) == 4, "ten minutes later"


def test_internet_off_is_reported_without_asking_telegram():
    seen: list[str] = []
    checks = TelegramChecks(transport=telegram(200, {"ok": True, "result": {}}, seen=seen))
    assert checks.result(item(), internet_off=True)["state"] == "internet_off"
    assert seen == []
    assert checks.result(item(), internet_off=False)["state"] == "ok"  # anchor: turned back on
    assert checks.result(item(token=None), internet_off=False) is None
