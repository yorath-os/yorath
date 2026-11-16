from yorath.control.agents.service import card_out, control_level_for, normalize_tools


def test_normalize_tools_flattens_targets():
    tools = normalize_tools(
        ["search_docs", {"ask_agent": ["research-agent"]}, {"web_search": None}]
    )
    assert [(t.name, t.targets) for t in tools] == [
        ("search_docs", []),
        ("ask_agent", ["research-agent"]),
        ("web_search", []),
    ]


def test_control_level_follows_runtime():
    assert control_level_for("native").value == "tool"
    assert control_level_for("codex").value == "sandbox"
    # OpenClaw and Hermes are connected agents with their own runtime: Yorath holds the model, entry and network
    assert control_level_for("openclaw").value == "gateway"
    assert control_level_for("hermes").value == "gateway"


def test_card_out_uses_defaults_for_old_front_matter():
    card = card_out({"name": "x", "mode": "delegated"})
    assert card.model == "local/default" and card.tools == [] and card.data == []
    assert card.max_data_class == "internal" and card.triggers == ["chat"]
