"""Response shapes for the My Agents screen: Agent, Revision and Execution are shown separately."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolOut(BaseModel):
    name: str
    targets: list[str] = []


class CardOut(BaseModel):
    """md file header from the revision ``front_matter``; the dict is read with defaults."""

    model: str
    models: list[str]
    tools: list[ToolOut]
    data: list[str]
    max_data_class: str
    triggers: list[str]


class RevisionOut(BaseModel):
    id: uuid.UUID
    revision_no: int
    path: str
    content_sha256: str
    created_at: datetime
    card: CardOut


class PrincipalOut(BaseModel):
    id: uuid.UUID
    display_name: str
    kind: str


class ExecutionSummary(BaseModel):
    total: int = 0
    by_status: dict[str, int] = {}
    last_at: datetime | None = None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    display_name: str
    description: str | None
    hosting: str
    mode: str
    enabled: bool
    builtin: bool  # from the distribution, not from agents/custom
    runtime: str
    control_level: str
    owner: PrincipalOut | None
    service_account: PrincipalOut | None
    revision: RevisionOut | None
    executions: ExecutionSummary
    cost_usd: Decimal
    model: str | None = None  # chosen in the agent's settings; None: the md file's model


class OutcomeOut(BaseModel):
    kind: str
    status: str
    ref: str | None
    useful: bool | None


class ExecutionOut(BaseModel):
    id: uuid.UUID
    status: str
    trigger: str
    created_at: datetime
    finished_at: datetime | None
    initiated_by: PrincipalOut | None
    effective_principal: PrincipalOut | None
    revision_no: int
    model: str | None
    usage: dict[str, Any] | None
    cost_usd: Decimal | None
    cost_estimated: bool
    max_data_class_seen: str | None
    parent_execution_id: uuid.UUID | None
    outcome: OutcomeOut | None
