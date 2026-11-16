"""Installer TLS mode (change model-endpoint): which address and directive the Caddyfile gets."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "installer" / "render-env.py"


@pytest.fixture(scope="module")
def tls_env():
    spec = importlib.util.spec_from_file_location("render_env", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.tls_env


IP = {"YORATH_PUBLIC_IP": "192.168.1.50"}


@pytest.mark.parametrize(
    ("env", "mode", "site", "directive", "sni"),
    [
        (IP, "internal", "192.168.1.50, localhost", "tls internal", "default_sni 192.168.1.50"),
        (
            {**IP, "YORATH_HOSTNAME": "yorath.home.arpa"},
            "internal",
            "yorath.home.arpa, localhost",
            "tls internal",
            "",
        ),
        ({**IP, "YORATH_DOMAIN": "ai.example.com"}, "acme", "ai.example.com", "", ""),
        (
            {
                **IP,
                "YORATH_HOSTNAME": "yorath.corp.example.com",
                "YORATH_TLS_CERT": "c",
                "YORATH_TLS_KEY": "k",
            },
            "files",
            "yorath.corp.example.com",
            "tls /certs/cert.pem /certs/key.pem",
            "",
        ),
        ({**IP, "YORATH_DOMAIN": "ai.example.com", "YORATH_TLS": "off"}, "off", ":80", "", ""),
    ],
)
def test_tls_mode(tls_env, env, mode, site, directive, sni):
    out = tls_env(env)
    assert (out["YORATH_TLS_MODE"], out["YORATH_SITE"], out["YORATH_TLS_DIRECTIVE"]) == (
        mode,
        site,
        directive,
    )
    assert out["YORATH_CADDY_SNI"] == sni
    assert out["YORATH_COOKIE_SECURE"] == ("false" if mode == "off" else "true")
    assert out["YORATH_CA_FILE"] == ("root.crt" if mode == "internal" else "none")


def test_tls_is_on_unless_explicitly_off(tls_env):
    assert tls_env({})["YORATH_TLS_MODE"] == "internal"
    assert tls_env({"YORATH_TLS": ""})["YORATH_TLS_MODE"] == "internal"
    assert tls_env({"YORATH_TLS": "OFF"})["YORATH_TLS_MODE"] == "off"


def test_acme_email_only_for_domain(tls_env):
    assert (
        tls_env({"YORATH_DOMAIN": "ai.example.com", "YORATH_ACME_EMAIL": "a@b.c"})[
            "YORATH_CADDY_EMAIL"
        ]
        == "email a@b.c"
    )
    assert tls_env({**IP, "YORATH_ACME_EMAIL": "a@b.c"})["YORATH_CADDY_EMAIL"] == ""


def test_tunnel_name_only_for_internal_ca(tls_env):
    """An SSH tunnel arrives at localhost: the name is added only where our own CA will sign it."""
    assert tls_env({})["YORATH_SITE"] == "localhost"  # no IP, so no repeated name
    for env in ({**IP, "YORATH_DOMAIN": "ai.example.com"}, {**IP, "YORATH_TLS": "off"}):
        assert "localhost" not in tls_env(env)["YORATH_SITE"]


def test_env_with_secrets_is_private(tmp_path):
    """.env.local with its keys is copied into .env: only root may read it (0600),
    including when the file is left over from a version where it was 0644."""
    spec = importlib.util.spec_from_file_location("render_env", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    out = tmp_path / ".env"
    out.write_text("old")
    out.chmod(0o644)
    module.write_private(out, "YORATH_SECRET_KEY=x\n")
    assert out.read_text() == "YORATH_SECRET_KEY=x\n"
    assert out.stat().st_mode & 0o777 == 0o600
