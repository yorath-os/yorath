"""Connected agent configuration (design §7): what Yorath writes into the agent volume before start.

The core builds an ``AgentConfig``, the managed keys and secrets; the node service merges them with
what
is already in the volume (``merge_*``) and writes the files. Keys Yorath does not touch are kept:
the owner can configure the agent with its own tools. The functions are pure: tests check them,
and the node service and the core use the same ones.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import yaml

from yorath import PRODUCT_NAME

CORE_URL = "http://core:8000/v1"
MODEL = "local/default"
ENV_KEY = "YORATH_API_KEY"


@dataclass(frozen=True)
class AgentConfig:
    """Node service input: files (path in the volume → content after merging) and the container
    environment."""

    runtime: str
    managed: dict[str, Any]  # managed part of the configuration
    env: dict[str, str] = field(default_factory=dict)  # container environment (secrets)
    dotenv: dict[str, str] = field(default_factory=dict)  # .env lines in the volume (Hermes)

    def sha(self) -> str:
        """Fingerprint: a change of key, token, channel or model recreates the container."""
        raw = json.dumps(
            {"r": self.runtime, "m": self.managed, "e": self.env, "d": self.dotenv},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode()).hexdigest()


def openclaw_config(
    model_key: str,
    entry_token: str,
    context: int,
    port: int,
    telegram_token: str | None = None,
    telegram_users: list[str] | None = None,
) -> AgentConfig:
    """``openclaw.json``: the ``yorath`` provider, the default model, a gateway with a token and an
    OpenAI endpoint.

    Secrets are substituted as ``${VAR}`` from the container environment, the file does not hold
    them. Telegram only
    if a token is set: the channel section turns the channel on.
    """
    managed: dict[str, Any] = {
        "gateway": {
            "mode": "local",
            "port": port,
            "bind": "lan",
            "auth": {"mode": "token", "token": "${OPENCLAW_GATEWAY_TOKEN}"},
            "http": {"endpoints": {"chatCompletions": {"enabled": True}}},
        },
        "models": {
            "mode": "merge",
            "providers": {
                "yorath": {
                    "baseUrl": CORE_URL,
                    "apiKey": "${" + ENV_KEY + "}",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": MODEL,
                            "name": f"{PRODUCT_NAME} default",
                            "reasoning": False,
                            "input": ["text"],
                            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                            "contextWindow": context,
                            "maxTokens": 8192,
                        }
                    ],
                }
            },
        },
        "agents": {"defaults": {"model": {"primary": f"yorath/{MODEL}"}}},
    }
    env = {ENV_KEY: model_key, "OPENCLAW_GATEWAY_TOKEN": entry_token}
    if telegram_token:
        telegram: dict[str, Any] = {"enabled": True, "botToken": "${TELEGRAM_BOT_TOKEN}"}
        if telegram_users:
            telegram |= {"dmPolicy": "allowlist", "allowFrom": list(telegram_users)}
        managed["channels"] = {"telegram": telegram}
        env["TELEGRAM_BOT_TOKEN"] = telegram_token
    return AgentConfig("openclaw", managed, env=env)


def hermes_config(
    model_key: str,
    entry_token: str,
    context: int,
    port: int,
    telegram_token: str | None = None,
    telegram_users: list[str] | None = None,
) -> AgentConfig:
    """``config.yaml`` (the ``model`` block) and ``.env`` lines: model key, API server, Telegram."""
    managed = {
        "model": {
            "provider": "custom",
            "base_url": CORE_URL,
            "default": MODEL,
            "key_env": ENV_KEY,
            "context_length": context,
        }
    }
    dotenv = {
        ENV_KEY: model_key,
        "API_SERVER_ENABLED": "true",
        "API_SERVER_HOST": "0.0.0.0",  # noqa: S104 — agents network, endpoint behind a token
        "API_SERVER_PORT": str(port),
        "API_SERVER_KEY": entry_token,
        "TELEGRAM_BOT_TOKEN": telegram_token or "",
        "TELEGRAM_ALLOWED_USERS": ",".join(telegram_users or []),
    }
    return AgentConfig("hermes", managed, dotenv=dotenv)


BUILDERS = {"openclaw": openclaw_config, "hermes": hermes_config}

# Top-level keys Yorath owns: on merge they are replaced whole within
# their paths, the rest of the file stays as it was.
_OPENCLAW_PATHS = (
    ("gateway", "mode"), ("gateway", "port"), ("gateway", "bind"), ("gateway", "auth"),
    ("gateway", "http", "endpoints", "chatCompletions"),
    ("models", "providers", "yorath"), ("agents", "defaults", "model", "primary"),
)  # fmt: skip


def _get(d: dict[str, Any], path: tuple[str, ...]) -> Any:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _set(d: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    for k in path[:-1]:
        nxt = d.get(k)
        if not isinstance(nxt, dict):
            nxt = d[k] = {}
        d = nxt
    d[path[-1]] = value


def merge_openclaw(existing: str | None, managed: dict[str, Any]) -> str:
    """Merges the managed paths into the current ``openclaw.json``.

    A file that does not parse as JSON (OpenClaw allows JSON5 with comments) is replaced
    whole: the strict OpenClaw schema will not forgive a half-understood file.
    """
    try:
        current = json.loads(existing) if existing else {}
        if not isinstance(current, dict):
            current = {}
    except ValueError:
        current = {}
    for path in _OPENCLAW_PATHS:
        value = _get(managed, path)
        if value is not None:
            _set(current, path, value)
    channel = _get(managed, ("channels", "telegram"))
    if channel is not None:
        _set(current, ("channels", "telegram"), channel)
    elif isinstance(current.get("channels"), dict):
        current["channels"].pop("telegram", None)  # the bot was turned off in Yorath
        if not current["channels"]:
            current.pop("channels")
    return json.dumps(current, ensure_ascii=False, indent=2) + "\n"


def merge_hermes_yaml(existing: str | None, managed: dict[str, Any]) -> str:
    try:
        current = yaml.safe_load(existing) if existing else {}
        if not isinstance(current, dict):
            current = {}
    except yaml.YAMLError:
        current = {}
    model = current.get("model")
    current["model"] = {**(model if isinstance(model, dict) else {}), **managed["model"]}
    return yaml.safe_dump(current, allow_unicode=True, sort_keys=False)


def merge_dotenv(existing: str | None, values: dict[str, str]) -> str:
    """``.env`` lines: managed keys are replaced, an empty value removes the line."""
    out: list[str] = []
    seen: set[str] = set()
    for line in (existing or "").splitlines():
        comment = line.lstrip().startswith("#")
        key = line.split("=", 1)[0].strip() if "=" in line and not comment else None
        if key in values:
            seen.add(key)
            if values[key]:
                out.append(f"{key}={values[key]}")
            continue
        out.append(line)
    out += [f"{k}={v}" for k, v in values.items() if k not in seen and v]
    return "\n".join(out) + "\n"


def files_for(config: AgentConfig, existing: dict[str, str | None]) -> dict[str, str]:
    """Files in the agent volume after merging: file name relative to the state directory."""
    if config.runtime == "openclaw":
        return {"openclaw.json": merge_openclaw(existing.get("openclaw.json"), config.managed)}
    if config.runtime == "hermes":
        return {
            "config.yaml": merge_hermes_yaml(existing.get("config.yaml"), config.managed),
            ".env": merge_dotenv(existing.get(".env"), config.dotenv),
        }
    raise ValueError(f"unknown connected agent runtime: {config.runtime}")
