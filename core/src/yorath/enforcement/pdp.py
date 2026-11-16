"""PDP contract and the Cedar implementation (cedarpy). Schema and policy set are parsed once."""

import json
from typing import Any, Protocol

import cedarpy
from pydantic import BaseModel, Field

from yorath.enforcement.schema import CEDAR_SCHEMA, NAMESPACE


class EntityRef(BaseModel):
    type: str = Field(description="type without namespace: User, Agent, DataAsset …")
    id: str

    def cedar(self) -> str:
        return f'{NAMESPACE}::{self.type}::"{self.id}"'

    def json_ref(self) -> dict[str, Any]:
        return {"__entity": {"type": f"{NAMESPACE}::{self.type}", "id": self.id}}


class AuthzRequest(BaseModel):
    principal: EntityRef
    action: str
    resource: EntityRef
    context: dict[str, Any] = {}

    @classmethod
    def build(
        cls,
        principal: EntityRef,
        action: str,
        resource: EntityRef,
        *,
        agent: str,
        context_class: int,
        **extra: Any,
    ) -> AuthzRequest:
        ctx = {
            "agent": EntityRef(type="Agent", id=agent).json_ref(),
            "context_class": context_class,
        }
        return cls(principal=principal, action=action, resource=resource, context={**ctx, **extra})


class Decision(BaseModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list, description="@id of the matching policies")
    deny_by_default: bool = False
    errors: list[str] = Field(default_factory=list)

    @property
    def reason_text(self) -> str:
        if self.allowed:
            return "allowed by policy " + ", ".join(self.reasons)
        if self.deny_by_default:
            return "no permitting policy"
        return "denied by policy " + ", ".join(self.reasons)


class PolicyDecisionPoint(Protocol):
    def authorize(self, request: AuthzRequest, entities: list[dict[str, Any]]) -> Decision: ...

    def load(self, policies_text: str) -> None: ...


class PolicyValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class CedarPDP:
    """Embedded Cedar: validated against the schema on load; an invalid set is not applied."""

    def __init__(self) -> None:
        self._schema = cedarpy.Schema.from_json_str(json.dumps(CEDAR_SCHEMA))
        self._policy_set: cedarpy.PolicySet | None = None
        self._ids: dict[str, str] = {}
        self.policies_text = ""

    @staticmethod
    def validate(policies_text: str) -> list[str]:
        """Parse and validation errors; an empty list means the set is valid."""
        try:
            cedarpy.PolicySet.from_str(policies_text)
        except ValueError as exc:
            return [f"syntax: {exc}"]
        result = cedarpy.validate_policies(policies_text, CEDAR_SCHEMA)
        if result.validation_passed:
            return []
        return [f"{e.policy_id}: {e.error}" for e in result.errors]

    def load(self, policies_text: str) -> None:
        errors = self.validate(policies_text)
        if errors:
            raise PolicyValidationError(errors)
        parsed = json.loads(cedarpy.policies_to_json_str(policies_text))
        self._ids = {
            pid: body.get("annotations", {}).get("id", pid)
            for pid, body in parsed.get("staticPolicies", {}).items()
        }
        self._policy_set = cedarpy.PolicySet.from_str(policies_text)
        self.policies_text = policies_text

    def authorize(self, request: AuthzRequest, entities: list[dict[str, Any]]) -> Decision:
        if self._policy_set is None:
            raise RuntimeError("policy set is not loaded")
        ents = cedarpy.Entities.from_json_str(json.dumps(entities), self._schema)
        result = cedarpy.is_authorized(
            {
                "principal": request.principal.cedar(),
                "action": f'{NAMESPACE}::Action::"{request.action}"',
                "resource": request.resource.cedar(),
                "context": request.context,
            },
            self._policy_set,
            ents,
            self._schema,
        )
        reasons = sorted(self._ids.get(r, r) for r in result.diagnostics.reasons)
        return Decision(
            allowed=result.allowed,
            reasons=reasons,
            deny_by_default=not result.allowed and not reasons,
            errors=[str(e) for e in result.diagnostics.errors],
        )
