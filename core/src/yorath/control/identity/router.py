"""``/auth/*`` and ``/users``. The session cookie is HttpOnly, SameSite=Lax, Secure per setting."""

import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from yorath.control.identity import service
from yorath.control.identity.deps import SESSION_COOKIE, Db, JournalDep, Owner
from yorath.control.identity.schemas import (
    AnonymousOut,
    LoginIn,
    MeOut,
    SetupIn,
    UserCreate,
    UserOut,
    UserUpdate,
)
from yorath.control.identity.service import IdentityError, resolve_session
from yorath.control.journal import AuditEventType
from yorath.control.model import User

router = APIRouter(tags=["identity"])


def _set_cookie(request: Request, response: Response, token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


async def _login_response(request: Request, response: Response, db, user: User) -> UserOut:
    ttl = timedelta(hours=request.app.state.settings.session_ttl_hours)
    _, token = await service.open_session(db, user, ttl)
    _set_cookie(request, response, token)
    return UserOut.model_validate(user)


@router.post("/auth/setup", response_model=UserOut, status_code=201)
async def setup(data: SetupIn, request: Request, response: Response, db: Db, journal: JournalDep):
    if await service.count_users(db) > 0:
        raise HTTPException(409, detail="already_set_up")
    expected = request.app.state.settings.setup_code
    if expected and not (data.code and secrets.compare_digest(data.code, expected)):
        await journal.audit(AuditEventType.AUTH_LOGIN_FAILED, details={"login": "<setup>"})
        raise HTTPException(403, detail="invalid_setup_code")
    user = await service.create_user(
        db,
        journal,
        UserCreate(
            login=data.login, password=data.password, display_name=data.display_name, role="owner"
        ),
        actor=None,
    )
    # agents were synced at core start, when there was no owner yet
    await service.ensure_service_accounts(db)
    return await _login_response(request, response, db, user)


@router.post("/auth/login", response_model=UserOut)
async def login(data: LoginIn, request: Request, response: Response, db: Db, journal: JournalDep):
    user = await service.authenticate(db, journal, data.login, data.password)
    if user is None:
        raise HTTPException(401, detail="invalid_credentials")
    return await _login_response(request, response, db, user)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, db: Db, journal: JournalDep) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await service.revoke_session(db, journal, token)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/auth/me", response_model=MeOut | AnonymousOut)
async def me(request: Request, db: Db):
    """No session gives 200: not being logged in when a page opens is not an error."""
    token = request.cookies.get(SESSION_COOKIE)
    user = await resolve_session(db, token) if token else None
    if user is None:
        return AnonymousOut(setup_required=await service.count_users(db) == 0)
    return MeOut.model_validate(user)


@router.get("/users", response_model=list[UserOut])
async def list_users(_: Owner, db: Db):
    return (await db.scalars(select(User).order_by(User.created_at))).all()


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(data: UserCreate, owner: Owner, db: Db, journal: JournalDep):
    try:
        return await service.create_user(db, journal, data, actor=owner)
    except IdentityError as exc:
        raise HTTPException(409 if exc.code == "login_taken" else 422, detail=exc.code) from exc


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID, data: UserUpdate, owner: Owner, db: Db, journal: JournalDep
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, detail="user_not_found")
    try:
        return await service.update_user(db, journal, user, data, actor=owner)
    except IdentityError as exc:
        raise HTTPException(422, detail=exc.code) from exc
