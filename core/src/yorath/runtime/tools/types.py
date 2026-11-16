"""Tool contract: a result with a class label, and the context the Gateway provides.

The Runtime Plane does not import the Gateway: ``ToolContext`` is a structural protocol that
the Enforcement Plane ``ExecutionContext`` implements.
"""

import uuid
from typing import Any, Protocol

from pydantic import BaseModel

from yorath.control.agents.loader import AgentCard
from yorath.control.journal import ControlLevel
from yorath.control.model import Agent, AgentRevision, DataClass, Principal


class Citation(BaseModel):
    n: int
    document_id: str
    filename: str
    heading_path: str
    asset: str
    data_class: str
    url: str | None = None


class ToolResult(BaseModel):
    """What a tool gives the Gateway: text for the model, result class, citations, decisions."""

    content: str
    data_class: DataClass | None = None
    citations: list[Citation] = []
    assets_allowed: list[str] = []
    assets_denied: list[str] = []
    is_error: bool = False
    meta: dict[str, Any] = {}


class ToolContext(Protocol):
    """What a tool needs to know about the Execution to ask the PDP on behalf of the right
    principal."""

    @property
    def execution_id(self) -> uuid.UUID: ...

    @property
    def effective_principal(self) -> Principal: ...

    @property
    def agent(self) -> Agent: ...

    @property
    def revision(self) -> AgentRevision: ...

    @property
    def card(self) -> AgentCard: ...

    @property
    def control_level(self) -> ControlLevel: ...

    def audit_fields(self) -> dict[str, Any]: ...
