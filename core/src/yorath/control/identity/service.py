"""Identity operations: users, login, sessions, service accounts. Audit goes through the Journal."""

import hashlib
import logging
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.agents.loader import CONNECTED_RUNTIMES
from yorath.control.identity.passwords import hash_password
from yorath.control.identity.providers import PROVIDERS
from yorath.control.identity.schemas import UserCreate, UserUpdate
from yorath.control.journal import AuditEventType, Journal
from yorath.control.model import Agent, AgentMode, RuntimeType, ServiceAccount, User, UserSession
from yorath.db import utcnow

log = logging.getLogger(__name__)


class IdentityError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def count_users(session: AsyncSession) -> int:
    return (await session.scalar(select(func.count()).select_from(User))) or 0


async def create_user(
    session: AsyncSession,
    journal: Journal | None,
    data: UserCreate,
    *,
    actor: User | None,
) -> User:
    if await session.scalar(select(User).where(User.login == data.login)):
        raise IdentityError("login_taken", "this login is already taken")
    if data.manager_id and not await session.get(User, data.manager_id):
        raise IdentityError("manager_not_found", "manager not found")
    user = User(
        login=data.login,
        display_name=data.display_name,
        password_hash=hash_password(data.password),
        role=data.role,
        department=data.department,
        manager_id=data.manager_id,
        attributes=data.attributes,
    )
    session.add(user)
    await session.commit()
    if journal:
        await journal.audit(
            AuditEventType.USER_CREATED,
            actor_principal_id=actor.id if actor else user.id,
            details={"login": user.login, "role": user.role, "user_id": str(user.id)},
        )
    return user


async def update_user(
    session: AsyncSession, journal: Journal | None, user: User, data: UserUpdate, *, actor: User
) -> User:
    changes = data.model_dump(exclude_unset=True)
    if "password" in changes:
        user.password_hash = hash_password(changes.pop("password"))
        changes["password"] = "***"
    if changes.get("manager_id") and not await session.get(User, changes["manager_id"]):
        raise IdentityError("manager_not_found", "manager not found")
    for key, value in changes.items():
        if key != "password":
            setattr(user, key, value)
    if changes.get("is_active") is False:
        await _revoke_all_sessions(session, user.id)
    actor_id, user_id = actor.id, user.id
    await session.commit()
    if changes.get("is_active") is False:
        from yorath.control.identity.api_keys import revoke_user_keys  # api_keys → deps → service

        await revoke_user_keys(session, journal, actor_id, user_id)
        await session.refresh(user)
    if journal:
        await journal.audit(
            AuditEventType.USER_UPDATED,
            actor_principal_id=actor_id,
            details={"user_id": str(user_id), "changes": {k: str(v) for k, v in changes.items()}},
        )
    return user


async def authenticate(
    session: AsyncSession, journal: Journal | None, login: str, password: str
) -> User | None:
    """The user if the password is right, otherwise None. A failure is audited with the login."""
    user = await session.scalar(select(User).where(User.login == login))
    ok = False
    if user is not None and user.is_active:
        provider = PROVIDERS.get(user.provider)
        if provider is None:
            log.warning("identity provider %r is not registered (user %s)", user.provider, login)
        else:
            ok = await provider.authenticate(session, user, password)
            await session.commit()
    if journal:
        await journal.audit(
            AuditEventType.AUTH_LOGIN if ok else AuditEventType.AUTH_LOGIN_FAILED,
            actor_principal_id=user.id if (ok and user) else None,
            details={"login": login},
        )
    return user if ok else None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def open_session(
    session: AsyncSession, user: User, ttl: timedelta
) -> tuple[UserSession, str]:
    token = secrets.token_urlsafe(32)
    row = UserSession(user_id=user.id, token_hash=_hash_token(token), expires_at=utcnow() + ttl)
    session.add(row)
    await session.commit()
    return row, token


async def resolve_session(session: AsyncSession, token: str) -> User | None:
    """User by cookie token; None if the session is missing, expired, revoked or user inactive."""
    row = await session.scalar(
        select(UserSession).where(UserSession.token_hash == _hash_token(token))
    )
    now = utcnow()
    if row is None or row.revoked_at is not None or row.expires_at < now:
        return None
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    row.last_seen_at = now
    await session.commit()
    return user


async def revoke_session(session: AsyncSession, journal: Journal | None, token: str) -> None:
    row = await session.scalar(
        select(UserSession).where(UserSession.token_hash == _hash_token(token))
    )
    if row is None or row.revoked_at is not None:
        return
    row.revoked_at = utcnow()
    await session.commit()
    if journal:
        await journal.audit(AuditEventType.AUTH_LOGOUT, actor_principal_id=row.user_id)


async def _revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    rows = await session.scalars(
        select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    )
    now = utcnow()
    for row in rows:
        row.revoked_at = now


async def ensure_service_accounts(session: AsyncSession) -> tuple[list[str], list[str]]:
    """A service account for every autonomous agent without one. Returns (created, warnings)."""
    created: list[str] = []
    warnings: list[str] = []
    # for connected agents the connection creates the account (change connected-agents): before it
    # there may be no owner yet, and nothing to warn about
    agents = (
        await session.scalars(
            select(Agent)
            .join(RuntimeType, RuntimeType.id == Agent.runtime_type_id)
            .where(Agent.mode == AgentMode.AUTONOMOUS, RuntimeType.name.not_in(CONNECTED_RUNTIMES))
        )
    ).all()
    first_owner = await session.scalar(
        select(User).where(User.role == "owner").order_by(User.created_at).limit(1)
    )
    for agent in agents:
        existing = await session.scalar(
            select(ServiceAccount).where(ServiceAccount.agent_id == agent.id)
        )
        if existing:
            continue
        owner_id = agent.owner_id or (first_owner.id if first_owner else None)
        if owner_id is None:
            warnings.append(
                f"{agent.name}: service account not created: no user with the owner role"
            )
            continue
        session.add(
            ServiceAccount(
                display_name=f"{agent.name}-svc", owner_user_id=owner_id, agent_id=agent.id
            )
        )
        created.append(agent.name)
    await session.commit()
    return created, warnings
