import pytest

from tests.fixtures.corpus import VALID, write
from yorath.control.agents.loader import AgentCard, AgentFileError, parse_agent_file
from yorath.control.model import AgentMode, DataClass


def test_parses_card_and_keeps_extra_keys(tmp_path):
    parsed = parse_agent_file(write(tmp_path, "da.md", VALID))
    card = parsed.card
    assert card.name == "document-assistant"
    assert card.mode is AgentMode.DELEGATED
    assert card.max_data_class is DataClass.CONFIDENTIAL
    assert card.tools == ["search_docs", {"ask_agent": ["research-agent"]}]
    assert parsed.front_matter["x-a2a-skill"] == "faq"
    assert parsed.body.startswith("Body:")
    assert len(parsed.sha256) == 64


def test_sha_changes_with_body(tmp_path):
    a = parse_agent_file(write(tmp_path, "a.md", VALID))
    b = parse_agent_file(write(tmp_path, "b.md", VALID + "\nOne more line.\n"))
    assert a.sha256 != b.sha256 and a.front_matter == b.front_matter


def test_missing_required_field_names_file(tmp_path):
    text = VALID.replace("mode: delegated\n", "")
    path = write(tmp_path, "broken.md", text)
    with pytest.raises(AgentFileError, match=r"broken\.md: mode"):
        parse_agent_file(path)


def test_no_front_matter(tmp_path):
    with pytest.raises(AgentFileError, match="front matter"):
        parse_agent_file(write(tmp_path, "plain.md", "# just text\n"))


def test_bad_name(tmp_path):
    with pytest.raises(AgentFileError, match="name"):
        parse_agent_file(write(tmp_path, "x.md", VALID.replace("document-assistant", "Doc A")))


def test_default_model_is_always_allowed():
    base = {"name": "a-b", "mode": "delegated"}
    assert AgentCard.model_validate(base).models == ["local/default"]
    card = AgentCard.model_validate({**base, "models": ["openrouter/default"]})
    assert card.models == ["local/default", "openrouter/default"]
    both = {**base, "models": ["local/default", "openrouter/default"]}
    assert AgentCard.model_validate(both).models == both["models"]
