"""§8 event types and OTel GenAI semantic conventions attribute keys."""

from enum import StrEnum
from typing import Any


class AuditEventType(StrEnum):
    EXECUTION_STARTED = "execution.started"
    EXECUTION_FINISHED = "execution.finished"
    TOOL_CALLED = "tool.called"
    DATA_ACCESSED = "data.accessed"
    POLICY_DENIED = "policy.denied"
    AGENT_INVOKED = "agent.invoked"
    LLM_CALLED = "llm.called"
    SECRET_ISSUED = "secret.issued"
    SECRET_REVOKED = "secret.revoked"
    EGRESS_ALLOWED = "egress.allowed"
    EGRESS_BLOCKED = "egress.blocked"
    SANDBOX_STARTED = "sandbox.started"
    SANDBOX_STOPPED = "sandbox.stopped"
    OUTCOME_RECORDED = "outcome.recorded"
    AUTH_LOGIN = "auth.login"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_LOGOUT = "auth.logout"
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    DOCUMENT_UPLOADED = "document.uploaded"
    DOCUMENT_DELETED = "document.deleted"
    AGENT_UPDATED = "agent.updated"
    AGENT_CREATED = "agent.created"
    INSTALLATION_RESTORED = "installation.restored"
    MODEL_REGISTERED = "model.registered"
    EGRESS_UNPROTECTED = "egress.unprotected"  # node network bypassed Yorath (change node-egress)
    EGRESS_RESTORED = "egress.restored"
    # connected agents (change connected-agents)
    AGENT_CONNECTED = "agent.connected"
    AGENT_DISCONNECTED = "agent.disconnected"
    AGENT_REMOVED = "agent.removed"
    AGENT_CHANNEL_CHANGED = "agent.channel_changed"
    AGENT_NETWORK_CHANGED = "agent.network_changed"
    AGENT_VERSION_CHANGED = "agent.version_changed"
    # cluster (change cluster-nodes)
    NODE_JOINED = "node.joined"
    NODE_APPROVED = "node.approved"
    NODE_REVOKED = "node.revoked"
    NODE_LOST = "node.lost"
    NODE_BACK = "node.back"
    NODE_OVER_LIMIT = "node.over_limit"  # a node reported more than its limits allow (S6)
    # model catalog and placements (change model-placements)
    MODEL_PLACED = "model.placed"
    MODEL_REMOVED = "model.removed"
    MODEL_MOVED = "model.moved"  # to a server with another window (pre-public-hardening R2)
    NODE_CLASSES_CHANGED = "node.classes_changed"
    # change field-fixes-0-7
    API_KEY_MODELS_CHANGED = "api_key.models_changed"
    AGENT_MODEL_CHANGED = "agent.model_changed"
    AGENT_RESTARTED = "agent.restarted"
    MODEL_DEFAULT_SWITCHED = "model.default_switched"


class Decision(StrEnum):
    PERMIT = "permit"
    DENY = "deny"


class ControlLevel(StrEnum):
    TOOL = "tool"
    SANDBOX = "sandbox"
    GATEWAY = "gateway"  # an agent outside Yorath: only model calls are under control


class GenAI:
    """OTel GenAI attribute keys (opentelemetry.io/docs/specs/semconv/gen-ai/)."""

    OPERATION_NAME = "gen_ai.operation.name"
    PROVIDER_NAME = "gen_ai.provider.name"
    REQUEST_MODEL = "gen_ai.request.model"
    RESPONSE_MODEL = "gen_ai.response.model"
    INPUT_TOKENS = "gen_ai.usage.input_tokens"
    OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    TOOL_NAME = "gen_ai.tool.name"
    AGENT_NAME = "gen_ai.agent.name"
    CONVERSATION_ID = "gen_ai.conversation.id"
    INPUT_MESSAGES = "gen_ai.input.messages"
    OUTPUT_MESSAGES = "gen_ai.output.messages"


def llm_attributes(
    *,
    provider: str,
    request_model: str,
    response_model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    input_messages: list[dict[str, Any]] | None = None,
    output_messages: list[dict[str, Any]] | None = None,
    operation: str = "chat",
) -> dict[str, Any]:
    """``llm.called`` span attributes: prompt and answer live only here, never in the audit log."""
    attrs: dict[str, Any] = {
        GenAI.OPERATION_NAME: operation,
        GenAI.PROVIDER_NAME: provider,
        GenAI.REQUEST_MODEL: request_model,
    }
    if response_model is not None:
        attrs[GenAI.RESPONSE_MODEL] = response_model
    if input_tokens is not None:
        attrs[GenAI.INPUT_TOKENS] = input_tokens
    if output_tokens is not None:
        attrs[GenAI.OUTPUT_TOKENS] = output_tokens
    if input_messages is not None:
        attrs[GenAI.INPUT_MESSAGES] = input_messages
    if output_messages is not None:
        attrs[GenAI.OUTPUT_MESSAGES] = output_messages
    return attrs


def tool_attributes(name: str, **extra: Any) -> dict[str, Any]:
    return {GenAI.OPERATION_NAME: "execute_tool", GenAI.TOOL_NAME: name, **extra}


def agent_attributes(name: str, **extra: Any) -> dict[str, Any]:
    return {GenAI.OPERATION_NAME: "invoke_agent", GenAI.AGENT_NAME: name, **extra}
