import uuid

import pytest

from yorath.control.journal import AuditEventType, GenAI, Journal, new_span_id
from yorath.control.journal.events import agent_attributes, llm_attributes, tool_attributes


def test_llm_attributes_use_otel_genai_keys():
    attrs = llm_attributes(
        provider="ollama",
        request_model="gemma4:e2b",
        input_tokens=120,
        output_tokens=40,
        input_messages=[{"role": "user", "parts": [{"type": "text", "content": "hi"}]}],
    )
    assert attrs[GenAI.OPERATION_NAME] == "chat"
    assert attrs[GenAI.PROVIDER_NAME] == "ollama"
    assert attrs[GenAI.REQUEST_MODEL] == "gemma4:e2b"
    assert attrs[GenAI.INPUT_TOKENS] == 120 and attrs[GenAI.OUTPUT_TOKENS] == 40
    assert GenAI.INPUT_MESSAGES in attrs and GenAI.RESPONSE_MODEL not in attrs
    assert all(k.startswith("gen_ai.") for k in attrs)


def test_tool_and_agent_attributes():
    assert tool_attributes("search_docs")[GenAI.TOOL_NAME] == "search_docs"
    assert agent_attributes("research-agent")[GenAI.OPERATION_NAME] == "invoke_agent"


def test_span_id_is_otel_shaped():
    sid = new_span_id()
    assert len(sid) == 16 and int(sid, 16) >= 0


async def test_audit_rejects_prompts_before_touching_db():
    journal = Journal(sessions=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="prompts"):
        await journal.audit(
            AuditEventType.LLM_CALLED,
            execution_id=uuid.uuid7(),
            details={"prompt": "secret"},
        )


def test_event_types_match_requirements_section_8():
    expected = {
        "execution.started", "execution.finished", "tool.called", "data.accessed",
        "policy.denied", "agent.invoked", "llm.called",
    }  # fmt: skip
    assert expected <= {e.value for e in AuditEventType}
