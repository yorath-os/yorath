"""HR demo set: a vacation table, a policy and three users for acceptance criterion 4."""

import secrets
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.data import service as data
from yorath.control.data.schemas import AssetCreate
from yorath.control.data.service import DataError
from yorath.control.identity import service as identity
from yorath.control.identity.schemas import UserCreate
from yorath.control.journal import Journal
from yorath.control.model import AssetKind, DataAsset, DataClass, Document, User

FILES = Path(__file__).parent
ASSET = AssetCreate(
    name="hr-vacations",
    description="HR demo set: a fictional vacation table",
    data_class=DataClass.CONFIDENTIAL,
    kind=AssetKind.RECORDS,
)


@dataclass(frozen=True)
class Persona:
    login: str
    display_name: str
    title: str
    employee_id: str
    department: str
    role: str = "member"
    assigned_departments: tuple[str, ...] = ()


PERSONAS = (
    Persona("anna.parker", "Anna Parker", "Employee", "E002", "Engineering"),
    Persona("max.lewis", "Max Lewis", "Manager", "E010", "Engineering"),
    Persona("olivia.newman", "Olivia Newman", "HRBP", "E008", "HR",
            role="hrbp", assigned_departments=("Field Sales",)),
)  # fmt: skip


class DemoUser(BaseModel):
    login: str
    password: str
    display_name: str
    title: str


class DemoResult(BaseModel):
    users: list[DemoUser]
    rows: int


async def enable(
    session: AsyncSession,
    journal: Journal | None,
    *,
    owner: User,
    documents_dir: Path,
    max_bytes: int,
) -> tuple[DemoResult, Document | None]:
    """Everything is checked before the first write: a refusal leaves nothing behind."""
    logins = [p.login for p in PERSONAS]
    taken = await session.scalar(select(User.login).where(User.login.in_(logins)).limit(1))
    exists = await session.scalar(select(DataAsset.id).where(DataAsset.name == ASSET.name))
    if taken or exists:
        raise DataError("already_enabled", "the HR demo set is already enabled")

    asset = await data.create_asset(session, ASSET, owner)
    rows = await data.upload_records(
        session, journal, asset,
        filename="hr-vacations.csv", data=(FILES / "hr-vacations.csv").read_bytes(), actor=owner,
    )  # fmt: skip
    doc: Document | None = None
    try:
        doc = await data.upload_document(
            session, journal, await data.ensure_default_asset(session, owner),
            filename="hr-vacation-policy.md", data=(FILES / "hr-vacation-policy.md").read_bytes(),
            data_class=None, actor=owner, documents_dir=documents_dir, max_bytes=max_bytes,
        )  # fmt: skip
    except DataError as exc:
        if exc.code != "duplicate":  # the owner already uploaded the policy: not an error
            raise
    users = []
    for p in PERSONAS:
        password = secrets.token_urlsafe(12)
        await identity.create_user(
            session,
            journal,
            UserCreate(
                login=p.login,
                password=password,
                display_name=p.display_name,
                role=p.role,
                department=p.department,
                attributes={
                    "employee_id": p.employee_id,
                    "assigned_departments": list(p.assigned_departments),
                },
            ),
            actor=owner,
        )
        users.append(
            DemoUser(login=p.login, password=password, display_name=p.display_name, title=p.title)
        )
    return DemoResult(users=users, rows=rows), doc
