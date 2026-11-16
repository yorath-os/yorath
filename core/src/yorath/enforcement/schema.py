"""Cedar schema: domain model entities and Gateway actions.

Principal is only ``User`` or ``ServiceAccount`` (decision 27); the executing agent comes
in ``context.agent``; data class is a ``Long`` rank 0–3 so "not above" can be compared.
"""

from typing import Any

NAMESPACE = "Yorath"
SCHEMA_VERSION = "1"

ACTIONS = {
    "read_doc": ["DataAsset"],
    "query_record": ["Record"],
    "use_tool": ["Tool"],
    "call_model": ["Model"],
    "invoke_agent": ["Agent"],
}

_CONTEXT = {
    "type": "Record",
    "attributes": {
        "agent": {"type": "Entity", "name": "Agent"},
        "context_class": {"type": "Long"},
    },
}


def _record(attrs: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Record", "attributes": attrs}


def _entity(name: str, required: bool = True) -> dict[str, Any]:
    spec: dict[str, Any] = {"type": "Entity", "name": name}
    if not required:
        spec["required"] = False  # `required` is not allowed inside a Set, so attributes only
    return spec


def _set(element: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Set", "element": element}


CEDAR_SCHEMA: dict[str, Any] = {
    NAMESPACE: {
        "entityTypes": {
            "User": {
                "shape": _record(
                    {
                        "role": {"type": "String"},
                        "department": {"type": "String"},
                        "manager": _entity("User", required=False),
                        "assigned_departments": _set({"type": "String"}),
                    }
                )
            },
            "ServiceAccount": {
                "shape": _record({"agent": _entity("Agent"), "owner": _entity("User")})
            },
            "Agent": {
                "shape": _record(
                    {
                        "name": {"type": "String"},
                        "mode": {"type": "String"},
                        "max_class": {"type": "Long"},
                        "allowed_data": _set(_entity("DataAsset")),
                        "allowed_agents": _set(_entity("Agent")),
                        "allowed_tools": _set({"type": "String"}),
                    }
                )
            },
            "DataAsset": {
                "shape": _record(
                    {
                        "class": {"type": "Long"},
                        "kind": {"type": "String"},
                        "allowed_roles": _set({"type": "String"}),
                        "allowed_departments": _set({"type": "String"}),
                        "owner": _entity("User", required=False),
                    }
                )
            },
            "Record": {
                "memberOfTypes": ["DataAsset"],
                "shape": _record(
                    {
                        "class": {"type": "Long"},
                        "employee": _entity("User", required=False),
                        "department": {"type": "String", "required": False},
                        "manager": _entity("User", required=False),
                    }
                ),
            },
            "Model": {
                "shape": _record({"max_class": {"type": "Long"}, "provider": {"type": "String"}})
            },
            "Tool": {"shape": _record({"name": {"type": "String"}, "max_class": {"type": "Long"}})},
        },
        "actions": {
            name: {
                "appliesTo": {
                    "principalTypes": ["User", "ServiceAccount"],
                    "resourceTypes": resources,
                    "context": _CONTEXT,
                }
            }
            for name, resources in ACTIONS.items()
        },
    }
}
