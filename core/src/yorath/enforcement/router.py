"""``/policies`` and ``/authz/check`` for the owner."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from yorath.control.identity.deps import Db, Owner
from yorath.control.model import Policy
from yorath.db import utcnow
from yorath.enforcement.pdp import AuthzRequest, CedarPDP, Decision, PolicyValidationError
from yorath.enforcement.policy_store import combine

router = APIRouter(tags=["policy"])


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    cedar: str
    enabled: bool
    description: str | None
    updated_at: datetime | None


class PolicyIn(BaseModel):
    cedar: str = Field(max_length=100_000)
    enabled: bool = True


class AuthzCheck(BaseModel):
    request: AuthzRequest
    entities: list[dict[str, Any]]


@router.get("/policies", response_model=list[PolicyOut])
async def list_policies(_: Owner, db: Db):
    return (await db.scalars(select(Policy).order_by(Policy.name))).all()


@router.put("/policies/{name}", response_model=PolicyOut)
async def put_policy(name: str, data: PolicyIn, _: Owner, db: Db, request: Request):
    rows = {p.name: p for p in (await db.scalars(select(Policy))).all()}
    parts = {n: p.cedar for n, p in rows.items() if p.enabled and n != name}
    if data.enabled:
        parts[name] = data.cedar
    pdp: CedarPDP = request.app.state.pdp
    try:
        pdp.load(combine(parts))
    except PolicyValidationError as exc:
        raise HTTPException(422, detail={"code": "invalid_policy", "errors": exc.errors}) from exc
    row = rows.get(name)
    if row is None:
        row = Policy(name=name, cedar=data.cedar, enabled=data.enabled, description="custom")
        db.add(row)
    else:
        row.cedar, row.enabled, row.updated_at = data.cedar, data.enabled, utcnow()
        if (row.description or "").startswith("builtin:"):
            row.description = "custom"
    await db.commit()
    return row


@router.post("/authz/check", response_model=Decision)
async def authz_check(data: AuthzCheck, _: Owner, request: Request):
    pdp: CedarPDP = request.app.state.pdp
    try:
        return pdp.authorize(data.request, data.entities)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
