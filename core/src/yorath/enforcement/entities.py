"""Cedar entities from database rows. Agent, DataAsset, Model, Tool are addressed by name; User,
ServiceAccount, Record by UUID. A record's class is denormalized (Cedar does not walk parents)."""

import uuid
from typing import Any

from yorath.control.agents.loader import AgentCard
from yorath.control.model import (
    Agent,
    DataAsset,
    DataClass,
    ModelResource,
    Principal,
    PrincipalKind,
    ServiceAccount,
    User,
)
from yorath.enforcement.pdp import EntityRef
from yorath.enforcement.schema import NAMESPACE


def _uid(type_: str, id_: Any) -> dict[str, str]:
    return {"type": f"{NAMESPACE}::{type_}", "id": str(id_)}


def _ref(type_: str, id_: Any) -> dict[str, Any]:
    return {"__entity": _uid(type_, id_)}


def _entity(
    type_: str, id_: Any, attrs: dict[str, Any], parents: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    return {"uid": _uid(type_, id_), "attrs": attrs, "parents": parents or []}


def principal_ref(principal: Principal) -> EntityRef:
    kind = "User" if principal.kind is PrincipalKind.USER else "ServiceAccount"
    return EntityRef(type=kind, id=str(principal.id))


def user_entity(user: User) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "role": user.role,
        "department": user.department or "",
        "assigned_departments": list(user.attributes.get("assigned_departments", [])),
    }
    if user.manager_id:
        attrs["manager"] = _ref("User", user.manager_id)
    return _entity("User", user.id, attrs)


def service_account_entity(account: ServiceAccount, agent_name: str) -> dict[str, Any]:
    return _entity(
        "ServiceAccount",
        account.id,
        {"agent": _ref("Agent", agent_name), "owner": _ref("User", account.owner_user_id)},
    )


def agent_entity(agent: Agent, card: AgentCard) -> dict[str, Any]:
    from yorath.control.agents.service import normalize_tools

    normalized = normalize_tools(card.tools)
    tools = [t.name for t in normalized]
    agents = [a for t in normalized if t.name == "ask_agent" for a in t.targets]
    return _entity(
        "Agent",
        agent.name,
        {
            "name": agent.name,
            "mode": agent.mode.value,
            "max_class": card.max_data_class.rank,
            "allowed_data": [_ref("DataAsset", d) for d in card.data],
            "allowed_agents": [_ref("Agent", a) for a in agents],
            "allowed_tools": tools,
        },
    )


def data_asset_entity(asset: DataAsset) -> dict[str, Any]:
    rules = asset.access_rules or {}
    attrs: dict[str, Any] = {
        "class": asset.data_class.rank,
        "kind": asset.kind.value,
        "allowed_roles": list(rules.get("roles", [])),
        "allowed_departments": list(rules.get("departments", [])),
    }
    if asset.owner_id:
        attrs["owner"] = _ref("User", asset.owner_id)
    return _entity("DataAsset", asset.name, attrs)


def record_entity(
    asset_name: str,
    record_id: str | uuid.UUID,
    data_class: DataClass,
    *,
    employee_id: uuid.UUID | None = None,
    department: str | None = None,
    manager_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    attrs: dict[str, Any] = {"class": data_class.rank}
    if employee_id:
        attrs["employee"] = _ref("User", employee_id)
    if department is not None:
        attrs["department"] = department
    if manager_id:
        attrs["manager"] = _ref("User", manager_id)
    return _entity("Record", record_id, attrs, parents=[_uid("DataAsset", asset_name)])


def model_entity(model: ModelResource) -> dict[str, Any]:
    ranks = [DataClass(c).rank for c in model.allowed_data_classes]
    return _entity(
        "Model",
        model.name,
        {"max_class": max(ranks) if ranks else -1, "provider": model.provider.value},
    )


def tool_entity(name: str, max_class: DataClass = DataClass.RESTRICTED) -> dict[str, Any]:
    return _entity("Tool", name, {"name": name, "max_class": max_class.rank})
