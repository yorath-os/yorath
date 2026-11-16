"""Scenarios of §12 items 4–6 on the built-in policy set, no database (entities from ORM objects)."""

import pytest

from tests.fixtures.pdp_cases import (
    CONFIDENTIAL,
    DA,
    DA_CARD,
    ENTITIES,
    INTERNAL,
    OPENROUTER,
    PAGEY_SVC,
    SCENARIOS,
)
from yorath.enforcement import entities as E
from yorath.enforcement.pdp import CedarPDP, PolicyValidationError
from yorath.enforcement.policy_store import builtin_policies, combine


@pytest.fixture(scope="module")
def pdp():
    p = CedarPDP()
    p.load(combine(builtin_policies()))
    return p


@pytest.mark.parametrize(
    ("name", "request_", "allowed", "reasons"), SCENARIOS, ids=[s[0] for s in SCENARIOS]
)
@pytest.mark.covers("INV-POL-2")
def test_default_policies(pdp, name, request_, allowed, reasons):
    d = pdp.authorize(request_, ENTITIES)
    assert d.errors == []
    assert d.allowed is allowed, d
    assert d.reasons == reasons, d
    assert d.deny_by_default is (not allowed and not reasons)


def test_reason_text_distinguishes_forbid_and_default_deny(pdp):
    forbid = pdp.authorize(SCENARIOS[7][1], ENTITIES)
    default = pdp.authorize(SCENARIOS[1][1], ENTITIES)
    assert forbid.reason_text.startswith("denied by policy agent-class-ceiling")
    assert default.reason_text == "no permitting policy"


def test_invalid_policy_is_rejected_and_old_set_stays(pdp):
    bad = (
        combine(builtin_policies())
        + "\npermit(principal, action, resource) when { principal.nope == 1 };"
    )
    with pytest.raises(PolicyValidationError, match="nope"):
        pdp.load(bad)
    with pytest.raises(PolicyValidationError, match="syntax"):
        pdp.load("permit(")
    assert pdp.authorize(SCENARIOS[0][1], ENTITIES).allowed is True


def test_agent_entity_from_card():
    e = E.agent_entity(DA, DA_CARD)
    assert e["attrs"]["allowed_tools"] == ["search_docs", "query_records", "ask_agent"]
    assert [a["__entity"]["id"] for a in e["attrs"]["allowed_agents"]] == ["research-agent"]
    assert [a["__entity"]["id"] for a in e["attrs"]["allowed_data"]][0] == "hr-vacations"
    assert e["attrs"]["max_class"] == CONFIDENTIAL
    assert E.model_entity(OPENROUTER)["attrs"]["max_class"] == INTERNAL
    assert E.principal_ref(PAGEY_SVC).type == "ServiceAccount"
