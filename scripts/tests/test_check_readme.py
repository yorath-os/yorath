"""Every rule of scripts/check-readme.py: the real README passes (positive anchor), one mutation fails it."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_readme", REPO / "scripts" / "check-readme.py")
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copy(REPO / "README.md", tmp_path / "README.md")
    shutil.copytree(REPO / "docs", tmp_path / "docs", ignore=shutil.ignore_patterns("design", "spike", "testing", "img"))
    return tmp_path


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new, 1))


def test_the_real_readme_passes(repo: Path) -> None:
    assert cr.check(repo) == []


def test_install_moved_below_what_you_get_fails(repo: Path) -> None:
    r = repo / "README.md"
    text = r.read_text()
    install = text[text.index("## Install"):text.index("## Quick start")]
    text = text.replace(install, "")
    text = text.replace("## Security", install + "## Security")
    r.write_text(text)
    assert any(e.startswith("sections:") for e in cr.check(repo))


def test_install_command_differs_from_facts_fails(repo: Path) -> None:
    edit(repo / "README.md", "curl -fsSL https://get.yorath.io | sh", "curl -sSL https://get.yorath.io | bash")
    assert any("install command" in e for e in cr.check(repo))


def test_slogan_changed_in_facts_only_fails(repo: Path) -> None:
    edit(repo / "docs" / "facts.yaml", "slogan: AI Infrastructure. Finally, an OS.", "slogan: Your AI, your servers.")
    assert any("slogan" in e for e in cr.check(repo))


def test_advantage_changed_in_the_file_only_fails(repo: Path) -> None:
    edit(repo / "docs" / "advantages.yaml", "Install agents with Yorath.", "Install agents from the panel.")
    errs = cr.check(repo)
    assert any(e.startswith("advantages: Connect point") for e in errs)


def test_federation_in_what_you_get_fails(repo: Path) -> None:
    edit(repo / "README.md", "Works with: OpenClaw", "Several clusters under one control — federation.\n\nWorks with: OpenClaw")
    assert any("federation" in e for e in cr.check(repo))


def test_docs_link_without_a_file_fails_naming_it(repo: Path) -> None:
    edit(repo / "README.md", "https://yorath.io/docs/cluster/add-a-server/", "https://yorath.io/docs/cluster/join/")
    assert any("https://yorath.io/docs/cluster/join/" in e for e in cr.check(repo))


def test_recipe_changed_on_the_docs_page_only_fails_naming_it(repo: Path) -> None:
    edit(repo / "docs" / "agents" / "codex-cli.md", 'wire_api = "responses"', 'wire_api = "chat"')
    assert any(e.startswith("recipes: 'Connect Codex'") for e in cr.check(repo))


def test_version_number_fails(repo: Path) -> None:
    edit(repo / "README.md", "## Install\n", "## Install\n\nVersion 0.6.0.\n")
    assert any(e.startswith("version:") for e in cr.check(repo))


def test_install_md_without_an_agent_pass_fails_and_with_it_passes(repo: Path) -> None:
    edit(repo / "README.md", "Open the link the installer prints",
         "Or tell your agent: Install Yorath on my server following https://yorath.io/install.md\n\nOpen the link the installer prints")
    gate = repo / "launch-gate.yaml"
    data = {"conditions": {10: {"title": "an agent passed install.md", "status": "open"}}}
    gate.write_text(yaml.safe_dump(data, allow_unicode=True))
    assert any(e.startswith("install.md:") for e in cr.check(repo, gate))
    data["conditions"][10].update(status="done", date="2026-11-20", evidence="install-md-pass-codex.log")
    gate.write_text(yaml.safe_dump(data, allow_unicode=True))
    assert not any(e.startswith("install.md:") for e in cr.check(repo, gate))
    assert not any(e.startswith("install.md:") for e in cr.check(repo)), "no gate file: the rule is not checked"
