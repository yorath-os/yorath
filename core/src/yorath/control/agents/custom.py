"""Custom agents from the panel: the core assembles the header, so permissions can't be typed in.

The file in ``agents/custom`` stays the source of truth (decision 25); saving makes a new revision.
"""

import os
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.agents.loader import (
    CUSTOM_DIR,
    AgentFileError,
    SyncResult,
    is_builtin_path,
    parse_agent_file,
    sync_one,
)
from yorath.control.journal import AuditEventType, Journal
from yorath.control.model import (
    Agent,
    AgentRevision,
    AssetKind,
    DataAsset,
    DataClass,
    ModelProvider,
    ModelResource,
)

# ask_agent is unavailable: the MVP has a single inter-agent route (§4).
# a data tool without its own source finds nothing: the PDP denies every other asset
NEEDS_SOURCE = (
    ("search_docs", AssetKind.DOCUMENTS, "document source"),
    ("query_records", AssetKind.RECORDS, "table source"),
)

CUSTOM_TOOLS = {
    "search_docs": "Document search",
    "query_records": "Employee data from tables",
    "web_search": "Web search",
}
DEFAULT_MODEL = "local/default"
EMBEDDINGS_SUFFIX = "/embeddings"


class CustomAgentError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


class AgentForm(BaseModel):
    description: str = Field(default="", max_length=500)
    instructions: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)
    ]
    data: list[str] = []
    tools: list[str] = []
    models: list[str] = []
    api: bool = False  # api trigger: call with an API key narrowed to the agent (change agent-api)


class AgentCreate(AgentForm):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,98}$")


class AgentSource(BaseModel):
    name: str
    path: str
    builtin: bool
    text: str
    instructions: str


class ToolOption(BaseModel):
    name: str
    description: str


class AssetOption(BaseModel):
    name: str
    data_class: str
    kind: str


class ModelOption(BaseModel):
    name: str
    model_id: str
    external: bool


class AgentOptions(BaseModel):
    tools: list[ToolOption]
    assets: list[AssetOption]
    models: list[ModelOption]


async def _chat_models(session: AsyncSession) -> list[ModelResource]:
    rows = (
        await session.scalars(
            select(ModelResource)
            .where(ModelResource.enabled.is_(True))
            .order_by(ModelResource.name)
        )
    ).all()
    return [m for m in rows if not m.name.endswith(EMBEDDINGS_SUFFIX)]


async def options(session: AsyncSession) -> AgentOptions:
    assets = (
        await session.scalars(
            select(DataAsset).where(DataAsset.enabled.is_(True)).order_by(DataAsset.name)
        )
    ).all()
    return AgentOptions(
        tools=[ToolOption(name=n, description=d) for n, d in CUSTOM_TOOLS.items()],
        assets=[
            AssetOption(name=a.name, data_class=a.data_class.value, kind=a.kind.value)
            for a in assets
        ],
        models=[
            ModelOption(
                name=m.name, model_id=m.model_id, external=m.provider is not ModelProvider.OLLAMA
            )
            for m in await _chat_models(session)
        ],
    )


async def _render(session: AsyncSession, name: str, form: AgentForm) -> str:
    """Validates the form fields and builds the md file text."""
    if unknown := sorted(set(form.tools) - set(CUSTOM_TOOLS)):
        raise CustomAgentError(422, "tool_not_allowed", f"tool is not available: {unknown[0]}")
    assets = {
        a.name: a
        for a in (await session.scalars(select(DataAsset).where(DataAsset.enabled.is_(True)))).all()
    }
    if unknown := sorted(set(form.data) - set(assets)):
        raise CustomAgentError(422, "asset_not_found", f"no such source: {unknown[0]}")
    kinds = {assets[n].kind for n in form.data}
    for tool, kind, what in NEEDS_SOURCE:
        if tool in form.tools and kind not in kinds:
            raise CustomAgentError(
                422,
                "source_required",
                f"{tool} searches only the selected sources: select at least one {what}",
            )
    available = {m.name for m in await _chat_models(session)}
    chosen = form.models or [DEFAULT_MODEL]
    if unknown := sorted(set(chosen) - available):
        raise CustomAgentError(422, "model_not_found", f"no such model: {unknown[0]}")
    ceiling = max(
        [DataClass.INTERNAL, *(assets[n].data_class for n in form.data)], key=lambda c: c.rank
    )
    front = {
        "name": name,
        "description": form.description.strip(),
        "runtime": "native",
        "model": DEFAULT_MODEL if DEFAULT_MODEL in chosen else chosen[0],
        "models": chosen,
        "mode": "delegated",
        "tools": form.tools,
        "data": form.data,
        "max_data_class": ceiling.value,
        "triggers": ["chat", "api"] if form.api else ["chat"],
    }
    head = yaml.safe_dump(front, allow_unicode=True, sort_keys=False)
    return f"---\n{head}---\n{form.instructions}\n"


def _write(path: Path, text: str) -> None:
    """Atomic replace: the agent never reads half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _restore(path: Path, previous: str | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        _write(path, previous)


async def _save(
    session: AsyncSession, base: Path, path: Path, text: str, previous: str | None
) -> SyncResult:
    """The file and the database are not one transaction, so the file rollback is explicit."""
    _write(path, text)
    try:
        result = await sync_one(session, path, parse_agent_file(path), base)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        _restore(path, previous)
        raise CustomAgentError(409, "agent_exists", "agent name is taken") from exc
    except AgentFileError as exc:
        await session.rollback()
        _restore(path, previous)
        raise CustomAgentError(422, "agent_invalid", str(exc)) from exc
    except BaseException:
        await session.rollback()
        _restore(path, previous)
        raise
    return result


async def _agent(session: AsyncSession, name: str) -> Agent:
    agent = await session.scalar(select(Agent).where(Agent.name == name))
    if agent is None:
        raise CustomAgentError(404, "agent_not_found", f"agent {name} does not exist")
    return agent


async def _latest(session: AsyncSession, agent: Agent) -> AgentRevision:
    rev = await session.scalar(
        select(AgentRevision)
        .where(AgentRevision.agent_id == agent.id)
        .order_by(AgentRevision.revision_no.desc())
        .limit(1)
    )
    if rev is None:
        raise CustomAgentError(404, "agent_not_found", f"agent {agent.name} has no revision")
    return rev


def is_builtin(rev: AgentRevision) -> bool:
    return is_builtin_path(rev.path)


async def _audit(
    session: AsyncSession,
    journal: Journal,
    kind: AuditEventType,
    name: str,
    result: SyncResult,
    owner_id: uuid.UUID,
) -> None:
    agent = await _agent(session, name)
    rev = await _latest(session, agent)
    await journal.audit(
        kind,
        actor_principal_id=owner_id,
        agent_id=agent.id,
        details={"agent": name, "revision": result.revision_no, "sha256": rev.content_sha256},
    )


async def create(
    session: AsyncSession, journal: Journal, base: Path, form: AgentCreate, owner_id: uuid.UUID
) -> None:
    if await session.scalar(select(Agent.id).where(Agent.name == form.name)):
        raise CustomAgentError(409, "agent_exists", f"agent {form.name} already exists")
    text = await _render(session, form.name, form)
    path = base / CUSTOM_DIR / f"{form.name}.md"
    result = await _save(session, base, path, text, None)
    agent = await _agent(session, form.name)
    agent.owner_id = owner_id
    await session.commit()
    await _audit(session, journal, AuditEventType.AGENT_CREATED, form.name, result, owner_id)


async def update(
    session: AsyncSession,
    journal: Journal,
    base: Path,
    name: str,
    form: AgentForm,
    owner_id: uuid.UUID,
) -> None:
    rev = await _latest(session, await _agent(session, name))
    if is_builtin(rev):
        raise CustomAgentError(
            403, "builtin_agent", "a built-in agent cannot be changed from the panel"
        )
    text = await _render(session, name, form)
    path = base / rev.path
    previous = path.read_text(encoding="utf-8") if path.is_file() else None
    result = await _save(session, base, path, text, previous)
    await _audit(session, journal, AuditEventType.AGENT_UPDATED, name, result, owner_id)


async def source(session: AsyncSession, base: Path, name: str) -> AgentSource:
    rev = await _latest(session, await _agent(session, name))
    path = base / rev.path
    if not path.is_file():
        raise CustomAgentError(404, "agent_file_missing", f"file {rev.path} not found")
    try:
        instructions = parse_agent_file(path).body.strip()
    except AgentFileError:
        instructions = ""
    return AgentSource(
        name=name,
        path=rev.path,
        builtin=is_builtin(rev),
        text=path.read_text(encoding="utf-8"),
        instructions=instructions,
    )
