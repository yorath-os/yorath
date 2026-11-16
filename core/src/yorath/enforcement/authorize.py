"""PEP helper: the PDP decision plus a ``policy.denied`` audit record; the Gateway (stage 3) calls
it."""

import uuid
from typing import Any

from yorath.control.journal import AuditEventType, ControlLevel, Journal
from yorath.control.journal import Decision as AuditDecision
from yorath.control.model import DataClass
from yorath.enforcement.pdp import AuthzRequest, Decision, PolicyDecisionPoint


async def enforce(
    pdp: PolicyDecisionPoint,
    journal: Journal | None,
    request: AuthzRequest,
    entities: list[dict[str, Any]],
    *,
    execution_id: uuid.UUID | None = None,
    actor_principal_id: uuid.UUID | None = None,
    effective_principal_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    agent_revision_id: uuid.UUID | None = None,
    tool: str | None = None,
    data_asset_id: uuid.UUID | None = None,
    data_class: DataClass | None = None,
    model: str | None = None,
    control_level: ControlLevel = ControlLevel.TOOL,
    runtime_type: str | None = None,
    parent_execution_id: uuid.UUID | None = None,
) -> Decision:
    decision = pdp.authorize(request, entities)
    if not decision.allowed and journal is not None:
        await journal.audit(
            AuditEventType.POLICY_DENIED,
            execution_id=execution_id,
            actor_principal_id=actor_principal_id,
            effective_principal_id=effective_principal_id,
            agent_id=agent_id,
            agent_revision_id=agent_revision_id,
            tool=tool,
            data_asset_id=data_asset_id,
            data_class=data_class,
            model=model,
            decision=AuditDecision.DENY,
            reason=decision.reason_text,
            control_level=control_level,
            runtime_type=runtime_type,
            parent_execution_id=parent_execution_id,
            details={
                "action": request.action,
                "resource": request.resource.model_dump(),
                "reasons": decision.reasons,
                "errors": decision.errors,
            },
        )
    return decision
