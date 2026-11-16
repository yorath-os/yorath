"""md agent (§5) → ``AgentCard`` → Agent + Agent Revision by the content sha256.

The file is the source of truth; the database keeps the path, hash and parsed header.
The body is not stored.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.model import Agent, AgentMode, AgentRevision, DataClass, Hosting, RuntimeType

# Custom agents: a volume inside the agents directory, so rev.path stays relative.
CUSTOM_DIR = "custom"
# Connected agents (change connected-agents): cards in the image, disabled until connected
CONNECTED_DIR = "connected"
CONNECTED_RUNTIMES = frozenset({"openclaw", "hermes"})


def is_builtin_path(path: str) -> bool:
    """A built-in agent lives in the image, a custom one in ``agents/custom`` on the volume."""
    return not path.startswith(f"{CUSTOM_DIR}/")


class AgentCard(BaseModel):
    """md agent header. Extra keys (A2A / MCP) are allowed and kept in front_matter."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,98}$")
    description: str = ""
    runtime: Literal["native", "codex", "openclaw", "hermes"] = "native"
    model: str = "local/default"
    models: list[str] = []  # allowed Model Resources; model is the default and always in the list
    mode: AgentMode
    tools: list[str | dict[str, list[str]]] = []
    data: list[str] = []
    max_data_class: DataClass = DataClass.INTERNAL
    triggers: list[Literal["chat", "issue", "schedule", "api"]] = ["chat"]
    hosting: Hosting = Hosting.MANAGED

    @model_validator(mode="after")
    def _default_model_is_allowed(self) -> AgentCard:
        if self.model not in self.models:
            self.models = [self.model, *self.models]
        return self


class AgentFileError(ValueError):
    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path


@dataclass(frozen=True)
class ParsedAgent:
    card: AgentCard
    front_matter: dict[str, Any]
    body: str
    sha256: str


def parse_agent_file(path: Path) -> ParsedAgent:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AgentFileError(path, "no YAML front matter at the start of the file")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise AgentFileError(path, "front matter is not closed with ---")
    try:
        front = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        raise AgentFileError(path, f"YAML error: {exc}") from exc
    if not isinstance(front, dict):
        raise AgentFileError(path, "front matter must be a mapping")
    try:
        card = AgentCard.model_validate(front)
    except ValidationError as exc:
        missing = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise AgentFileError(path, missing) from exc
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ParsedAgent(card=card, front_matter=front, body=text[end + 5 :], sha256=sha)


@dataclass(frozen=True)
class SyncResult:
    name: str
    revision_no: int
    created: bool


async def sync_agents_dir(session: AsyncSession, directory: Path) -> list[SyncResult]:
    """Creates an Agent by ``name`` and a new revision if the file sha256 changed."""
    results: list[SyncResult] = []
    paths = [*sorted(directory.glob("*.md")), *sorted((directory / CONNECTED_DIR).glob("*.md"))]
    for path in paths:
        parsed = parse_agent_file(path)
        results.append(await sync_one(session, path, parsed, directory))
    await session.commit()
    return results


async def sync_custom_agents(
    session: AsyncSession, directory: Path
) -> tuple[list[SyncResult], list[str]]:
    """Custom agents from ``agents/custom``: people write these, errors do not block startup."""
    results: list[SyncResult] = []
    warnings: list[str] = []
    for path in sorted((directory / CUSTOM_DIR).glob("*.md")):
        try:
            parsed = parse_agent_file(path)
            taken = await _taken_by_other_file(session, parsed.card.name, path, directory)
            if taken:
                raise AgentFileError(path, f"name {parsed.card.name} is taken by file {taken}")
            results.append(await sync_one(session, path, parsed, directory))
        except AgentFileError as exc:
            warnings.append(str(exc))
    await session.commit()
    return results, warnings


async def _taken_by_other_file(
    session: AsyncSession, name: str, path: Path, base: Path
) -> str | None:
    latest = await session.scalar(
        select(AgentRevision)
        .join(Agent, Agent.id == AgentRevision.agent_id)
        .where(Agent.name == name)
        .order_by(AgentRevision.revision_no.desc())
        .limit(1)
    )
    if latest is None or latest.path == str(path.relative_to(base)):
        return None
    return latest.path


async def sync_one(
    session: AsyncSession, path: Path, parsed: ParsedAgent, base: Path
) -> SyncResult:
    card = parsed.card
    runtime = await session.scalar(select(RuntimeType).where(RuntimeType.name == card.runtime))
    if runtime is None:
        raise AgentFileError(path, f"runtime {card.runtime!r} is not registered in runtime_types")
    agent = await session.scalar(select(Agent).where(Agent.name == card.name))
    if agent is None:
        agent = Agent(
            name=card.name,
            display_name=card.name,
            description=card.description or None,
            hosting=card.hosting,
            mode=card.mode,
            runtime_type_id=runtime.id,
            # a connected agent stays disabled until the owner connects it
            enabled=card.runtime not in CONNECTED_RUNTIMES,
        )
        session.add(agent)
        await session.flush()
    else:
        agent.description = card.description or None
        agent.hosting = card.hosting
        agent.mode = card.mode
        agent.runtime_type_id = runtime.id

    latest = await session.scalar(
        select(AgentRevision)
        .where(AgentRevision.agent_id == agent.id)
        .order_by(AgentRevision.revision_no.desc())
        .limit(1)
    )
    if latest is not None and latest.content_sha256 == parsed.sha256:
        return SyncResult(card.name, latest.revision_no, created=False)
    revision = AgentRevision(
        agent_id=agent.id,
        revision_no=(latest.revision_no + 1) if latest else 1,
        path=str(path.relative_to(base)),
        content_sha256=parsed.sha256,
        front_matter=parsed.front_matter,
    )
    session.add(revision)
    await session.flush()
    return SyncResult(card.name, revision.revision_no, created=True)
