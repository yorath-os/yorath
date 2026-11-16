"""FastAPI dependencies: DB session, journal, current user, role check."""

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.identity.service import count_users, resolve_session
from yorath.control.journal import Journal
from yorath.control.model import User

SESSION_COOKIE = "yorath_session"


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessions() as session:
        yield session


def get_journal(request: Request) -> Journal:
    return request.app.state.journal


Db = Annotated[AsyncSession, Depends(get_db)]
JournalDep = Annotated[Journal, Depends(get_journal)]


async def current_user(request: Request, db: Db) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    user = await resolve_session(db, token) if token else None
    if user is None:
        if await count_users(db) == 0:
            raise HTTPException(404, detail="setup_required")
        raise HTTPException(401, detail="not_authenticated")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*roles: str) -> Callable[..., Coroutine[Any, Any, User]]:
    async def check(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(403, detail="forbidden")
        return user

    return check


Owner = Annotated[User, Depends(require_role("owner"))]
