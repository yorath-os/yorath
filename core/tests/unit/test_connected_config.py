"""Connected agent configuration (change connected-agents, design §7): merging with the volume."""

import json

import yaml

from yorath.runtime.connected.config import (
    files_for,
    hermes_config,
    merge_dotenv,
    openclaw_config,
)


def test_openclaw_config_points_to_core_and_keeps_secrets_out_of_file():
    cfg = openclaw_config("yorath_key_m", "entry-t", 65536, 18789)
    text = files_for(cfg, {})["openclaw.json"]
    data = json.loads(text)
    provider = data["models"]["providers"]["yorath"]
    assert provider["baseUrl"] == "http://core:8000/v1"
    assert provider["apiKey"] == "${YORATH_API_KEY}"
    assert provider["models"][0]["contextWindow"] == 65536
    assert data["agents"]["defaults"]["model"]["primary"] == "yorath/local/default"
    assert data["gateway"]["http"]["endpoints"]["chatCompletions"]["enabled"] is True
    assert "channels" not in data
    # secret values live only in the container environment, not in the file
    assert "yorath_key_m" not in text and "entry-t" not in text
    assert cfg.env == {"YORATH_API_KEY": "yorath_key_m", "OPENCLAW_GATEWAY_TOKEN": "entry-t"}


def test_openclaw_merge_keeps_owner_settings_and_drops_removed_bot():
    with_bot = openclaw_config("k", "t", 65536, 18789, "123:bot", ["42"])
    first = json.loads(files_for(with_bot, {})["openclaw.json"])
    assert first["channels"]["telegram"]["allowFrom"] == ["42"]
    assert with_bot.env["TELEGRAM_BOT_TOKEN"] == "123:bot"
    # the owner added their own agent settings with the agent's own tools
    first["ui"] = {"theme": "dark"}
    first["channels"]["discord"] = {"enabled": False}
    without_bot = openclaw_config("k2", "t", 65536, 18789)
    merged = json.loads(
        files_for(without_bot, {"openclaw.json": json.dumps(first)})["openclaw.json"]
    )
    assert merged["ui"] == {"theme": "dark"}
    assert merged["channels"] == {"discord": {"enabled": False}}
    assert "TELEGRAM_BOT_TOKEN" not in without_bot.env


def test_openclaw_unreadable_json5_is_replaced():
    cfg = openclaw_config("k", "t", 65536, 18789)
    merged = json.loads(
        files_for(cfg, {"openclaw.json": "{ // a JSON5 comment\n a: 1 }"})["openclaw.json"]
    )
    assert "a" not in merged and merged["gateway"]["port"] == 18789


def test_hermes_merge_replaces_only_managed_parts():
    cfg = hermes_config("yorath_key_h", "entry-h", 65536, 8642, "777:bot", ["1", "2"])
    existing = {
        "config.yaml": "model:\n  default: other\n  temperature: 0.2\nterminal:\n  backend: local\n",
        ".env": "# mine\nOPENROUTER_API_KEY=mine\nYORATH_API_KEY=old\n",
    }
    files = files_for(cfg, existing)
    conf = yaml.safe_load(files["config.yaml"])
    assert conf["model"]["default"] == "local/default" and conf["model"]["temperature"] == 0.2
    assert conf["model"]["base_url"] == "http://core:8000/v1"
    assert conf["terminal"] == {"backend": "local"}
    env = files[".env"]
    assert "OPENROUTER_API_KEY=mine" in env and "# mine" in env
    assert "YORATH_API_KEY=yorath_key_h" in env and "YORATH_API_KEY=old" not in env
    assert "API_SERVER_KEY=entry-h" in env and "TELEGRAM_ALLOWED_USERS=1,2" in env


def test_dotenv_empty_value_removes_line():
    assert merge_dotenv("TELEGRAM_BOT_TOKEN=old\nX=1\n", {"TELEGRAM_BOT_TOKEN": ""}) == "X=1\n"


def test_config_sha_changes_with_key_and_bot():
    a = hermes_config("k1", "t", 65536, 8642).sha()
    assert a == hermes_config("k1", "t", 65536, 8642).sha()
    assert a != hermes_config("k2", "t", 65536, 8642).sha()
    assert a != hermes_config("k1", "t", 65536, 8642, "1:bot").sha()
