"""Users' persistent API keys for the model endpoint (decision 32, change model-endpoint).

A key belongs to a person, not an agent: it shows who spent how much, and disabling a user
kills their keys. The database keeps only the sha256 and a visible prefix; the key value is
shown once, in the response to creation. Issue and revoke are ``secret.issued`` /
``secret.revoked`` with ``kind = api_key`` and no value.

A key can be narrowed to one agent with the ``api`` trigger (change agent-api): such a key calls
only ``agent/<name>``, not the bare model: least privilege for a bot.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.identity.deps import CurrentUser, Db, JournalDep
from yorath.control.journal import AuditEventType, Journal
from yorath.control.model import Agent, AgentRevision, ApiKey, ModelProvider, ModelResource, User
from yorath.db import utcnow

KEY_PREFIX = "yorath_key_"
SHOWN_CHARS = len(KEY_PREFIX) + 7  # prefix + 7 characters: enough to recognize the key
TOUCH_EVERY = timedelta(minutes=1)  # last_used_at is not written on every request

MODEL_API = "model-api"  # bare model endpoint: a key without an agent, not an agent call

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class AgentNotCallable(Exception):
    """Agent not found, disabled, or its latest revision does not declare the ``api`` trigger."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def api_agent(
    session: AsyncSession, agent_id: uuid.UUID | None = None, *, name: str | None = None
) -> Agent:
    """An agent callable by key: enabled and declares ``triggers: [api]``."""
    query = (
        select(Agent).where(Agent.id == agent_id)
        if agent_id
        else select(Agent).where(Agent.name == name)
    )
    agent = await session.scalar(query)
    if agent is None:
        raise AgentNotCallable("agent_not_found", f"agent {name or agent_id} is not registered")
    if not agent.enabled:
        raise AgentNotCallable("agent_disabled", f"agent {agent.name} is disabled by the owner")
    rev = await session.scalar(
        select(AgentRevision)
        .where(AgentRevision.agent_id == agent.id)
        .order_by(desc(AgentRevision.revision_no))
        .limit(1)
    )
    triggers = (rev.front_matter.get("triggers") or ["chat"]) if rev else []
    if "api" not in triggers or agent.name == MODEL_API:
        raise AgentNotCallable(
            "agent_not_callable",
            f"agent {agent.name} does not declare the api trigger: calls by key are off",
        )
    return agent


async def key_models(session: AsyncSession, key: ApiKey, card_models: list[str]) -> list[str]:
    """Models a key may call: its own list, or — for a key with every model — the endpoint card's
    local models plus the external models connected with the owner's consent."""
    if key.models is not None:
        return list(key.models)
    external = await session.scalars(
        select(ModelResource.name).where(
            ModelResource.enabled.is_(True), ModelResource.provider != ModelProvider.OLLAMA
        )
    )
    return [*card_models, *[n for n in external.all() if n not in card_models]]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _audit(
    journal: Journal | None,
    event: AuditEventType,
    actor_id: uuid.UUID,
    key: ApiKey,
    agent: str | None = None,
) -> None:
    if journal is not None:
        await journal.audit(
            event,
            actor_principal_id=actor_id,
            details={
                "kind": "api_key",
                "key_id": str(key.id),
                "user_id": str(key.user_id),
                "name": key.name,
                "prefix": key.prefix,
                **({"agent": agent} if agent else {}),
            },
        )


async def issue_api_key(
    session: AsyncSession,
    journal: Journal | None,
    user: User,
    name: str,
    agent: Agent | None = None,
    scope: str | None = None,
    models: list[str] | None = None,
) -> tuple[ApiKey, str]:
    """Default ``scope``: ``agent`` with an agent, ``model`` without; ``agent_self`` is only for
    connecting an agent (change connected-agents)."""
    token = KEY_PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(
        user_id=user.id,
        name=name,
        prefix=token[:SHOWN_CHARS],
        token_hash=_hash(token),
        agent_id=agent.id if agent else None,
        scope=scope or ("agent" if agent else "model"),
        models=models,
    )
    session.add(key)
    await session.commit()
    await _audit(journal, AuditEventType.SECRET_ISSUED, user.id, key, agent.name if agent else None)
    return key, token


async def resolve_key(session: AsyncSession, token: str) -> tuple[User, ApiKey] | None:
    """Owner and the key in use; a revoked key or a disabled user gives ``None``."""
    if not token.startswith(KEY_PREFIX):
        return None
    key = await session.scalar(select(ApiKey).where(ApiKey.token_hash == _hash(token)))
    if key is None or key.revoked_at is not None:
        return None
    user = await session.get(User, key.user_id)
    if user is None or not user.is_active:
        return None
    now = utcnow()
    if key.last_used_at is None or now - key.last_used_at >= TOUCH_EVERY:
        key.last_used_at = now
        await session.commit()
    return user, key


async def resolve_api_key(session: AsyncSession, token: str) -> User | None:
    found = await resolve_key(session, token)
    return found[0] if found else None


async def revoke_api_key(
    session: AsyncSession, journal: Journal | None, actor_id: uuid.UUID, key: ApiKey
) -> None:
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        await session.commit()
        await _audit(journal, AuditEventType.SECRET_REVOKED, actor_id, key)


async def revoke_user_keys(
    session: AsyncSession, journal: Journal | None, actor_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    keys = (
        await session.scalars(
            select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
        )
    ).all()
    for key in keys:
        await revoke_api_key(session, journal, actor_id, key)
    return len(keys)


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    agent: str | None = Field(
        default=None, max_length=100, description="agent with the api trigger"
    )
    models: list[str] | None = Field(
        default=None, max_length=100, description="None: every chat model; else these names"
    )


class ApiKeyModelsIn(BaseModel):
    models: list[str] | None = Field(max_length=100)


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    user_id: uuid.UUID
    login: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    agent: str | None = None
    models: list[str] | None = None


class ApiKeyCreated(ApiKeyOut):
    key: str  # the only time the value leaves the core


def _out(key: ApiKey, login: str, agent: str | None = None) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "prefix": key.prefix,
        "user_id": key.user_id,
        "login": login,
        "created_at": key.created_at,
        "last_used_at": key.last_used_at,
        "revoked_at": key.revoked_at,
        "agent": agent,
        "models": key.models,
    }


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(user: CurrentUser, db: Db) -> list[dict]:
    """Own keys; the owner sees the keys of all users."""
    query = (
        select(ApiKey, User.login, Agent.name)
        .join(User, User.id == ApiKey.user_id)
        .outerjoin(Agent, Agent.id == ApiKey.agent_id)
    )
    if user.role != "owner":
        query = query.where(ApiKey.user_id == user.id)
    rows = await db.execute(query.order_by(ApiKey.created_at.desc()))
    return [_out(key, login, agent) for key, login, agent in rows.all()]


@router.get("/agents", response_model=list[str])
async def callable_agents(_: CurrentUser, db: Db) -> list[str]:
    """Agents a key can be issued for: enabled and declaring the ``api`` trigger."""
    from yorath.control.agents.service import latest_revisions

    revisions = await latest_revisions(db)
    agents = (
        await db.scalars(select(Agent).where(Agent.enabled.is_(True)).order_by(Agent.name))
    ).all()
    return [
        a.name
        for a in agents
        if a.name != MODEL_API
        and a.id in revisions
        and "api" in (revisions[a.id].front_matter.get("triggers") or [])
    ]


class KeyModelOut(BaseModel):
    name: str
    context_length: int
    external: bool


@router.get("/models", response_model=list[KeyModelOut])
async def key_model_choices(_: CurrentUser, db: Db) -> list[dict]:
    """Chat models a key can be given: local ones and external ones connected with consent."""
    rows = await db.scalars(
        select(ModelResource)
        .where(ModelResource.enabled.is_(True), ModelResource.name != "local/embeddings")
        .order_by(ModelResource.name)
    )
    return [
        {"name": m.name, "context_length": m.context_length,
         "external": m.provider != ModelProvider.OLLAMA}
        for m in rows.all()
    ]  # fmt: skip


@router.post("", response_model=ApiKeyCreated, status_code=201)
async def create_key(data: ApiKeyIn, user: CurrentUser, db: Db, journal: JournalDep) -> dict:
    agent = None
    if data.agent:
        try:
            agent = await api_agent(db, name=data.agent)
        except AgentNotCallable as exc:
            raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc
    models = await _checked_models(db, data.models)
    key, token = await issue_api_key(db, journal, user, data.name.strip(), agent, models=models)
    return {**_out(key, user.login, agent.name if agent else None), "key": token}


async def _checked_models(db: AsyncSession, names: list[str] | None) -> list[str] | None:
    """Names of chat models of the catalog (or ``local/*``); anything else is refused."""
    if names is None:
        return None
    known = set(
        (await db.scalars(select(ModelResource.name).where(ModelResource.enabled.is_(True)))).all()
    ) - {"local/embeddings"}
    unknown = [n for n in names if n != "local/*" and n not in known]
    if unknown:
        raise HTTPException(
            422, detail={"code": "unknown_model", "message": f"no such model: {', '.join(unknown)}"}
        )
    if not names:
        raise HTTPException(422, detail={"code": "no_models", "message": "pick at least one model"})
    return list(dict.fromkeys(names))


@router.patch("/{key_id}", response_model=ApiKeyOut)
async def set_key_models(
    key_id: uuid.UUID, data: ApiKeyModelsIn, user: CurrentUser, db: Db, journal: JournalDep
) -> dict:
    """Which models a key may call; the key itself stays (same value, same prefix)."""
    key = await db.get(ApiKey, key_id)
    if (
        key is None
        or key.revoked_at is not None
        or (user.role != "owner" and key.user_id != user.id)
    ):
        raise HTTPException(404, detail="api_key_not_found")
    if key.scope != "model":
        raise HTTPException(
            409,
            detail={"code": "not_a_model_key", "message": "this key calls an agent, not models"},
        )
    before = key.models
    key.models = await _checked_models(db, data.models)
    await db.commit()
    await journal.audit(
        AuditEventType.API_KEY_MODELS_CHANGED,
        actor_principal_id=user.id,
        details={"key_id": str(key.id), "name": key.name, "prefix": key.prefix,
                 "before": before, "after": key.models},
    )  # fmt: skip
    login = await db.scalar(select(User.login).where(User.id == key.user_id))
    return _out(key, login or "")


@router.delete("/{key_id}", status_code=204)
async def delete_key(key_id: uuid.UUID, user: CurrentUser, db: Db, journal: JournalDep) -> None:
    key = await db.get(ApiKey, key_id)
    if key is None or (user.role != "owner" and key.user_id != user.id):
        raise HTTPException(
            404, detail="api_key_not_found"
        )  # someone else's key is indistinguishable from a nonexistent one
    await revoke_api_key(db, journal, user.id, key)
