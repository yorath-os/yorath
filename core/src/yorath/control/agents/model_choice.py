"""The model an agent uses, chosen in its settings (change field-fixes-0-7).

Every agent — built-in, own or connected — uses ``local/default`` unless its md file names another
model; the owner can choose any chat model of the catalog in the agent's settings, the external ones
connected with consent included. The choice is an operational setting next to the agent, like its
switch: the md file and its Agent Revision stay as they are, and every Execution still records the
model actually used. A connected agent gets the chosen name in its own configuration.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.cluster.placements import card_allows
from yorath.control.journal import AuditEventType, Journal
from yorath.control.model import Agent, ModelResource, get_setting, set_setting
from yorath.distribution.manifest import Manifest

SETTING = "agent_models"  # {agent name: model name}


class ModelChoiceError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


async def chosen(session: AsyncSession) -> dict[str, str]:
    return dict(await get_setting(session, SETTING, {}) or {})


async def agent_model(session: AsyncSession, name: str) -> str | None:
    return (await chosen(session)).get(name)


def with_model(card: Any, model: str | None) -> Any:
    """The card with the chosen model as its default and in its allowed list."""
    if not model:
        return card
    models = card.models if card_allows(card.models, model) else [model, *card.models]
    return card.model_copy(update={"model": model, "models": models})


async def set_agent_model(
    session: AsyncSession, journal: Journal, manifest: Manifest, owner_id: Any, name: str,
    model: str | None,
) -> str | None:  # fmt: skip
    agent = await session.scalar(select(Agent).where(Agent.name == name))
    if agent is None:
        raise ModelChoiceError(404, "agent_not_found", f"agent {name} is not registered")
    if model is not None:
        row = await session.scalar(
            select(ModelResource).where(
                ModelResource.name == model,
                ModelResource.enabled.is_(True),
                ModelResource.name != "local/embeddings",
            )
        )
        if row is None:
            raise ModelChoiceError(422, "unknown_model", f"no chat model {model} in the catalog")
        spec = manifest.connected_agents.get(name)
        if spec is not None and row.context_length < spec.min_context:
            raise ModelChoiceError(
                409, "context_too_small",
                f"{name} needs a context window of {spec.min_context:,} tokens, "
                f"the model has {row.context_length:,}",
            )  # fmt: skip
    models = await chosen(session)
    before = models.get(name)
    if model is None:
        models.pop(name, None)
    else:
        models[name] = model
    await set_setting(session, SETTING, models)
    await journal.audit(
        AuditEventType.AGENT_MODEL_CHANGED,
        actor_principal_id=owner_id,
        agent_id=agent.id,
        details={"agent": name, "before": before, "after": model},
    )
    return model
