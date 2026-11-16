"""``/agents``: the agents screen, owner only: reading, the on/off switch, custom agents."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from yorath.control.agents import connected, custom, service
from yorath.control.agents.schemas import AgentOut, ExecutionOut
from yorath.control.identity.deps import Db, JournalDep, Owner
from yorath.control.journal import AuditEventType
from yorath.control.journal.egress import read_report
from yorath.control.model import Agent

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
async def list_agents(_: Owner, db: Db):
    return await service.list_agents(db)


@router.get("/options", response_model=custom.AgentOptions)
async def agent_options(_: Owner, db: Db):
    return await custom.options(db)


def _raise(exc: custom.CustomAgentError) -> HTTPException:
    return HTTPException(exc.status, detail={"code": exc.code, "message": str(exc)})


async def _saved(db: Db, name: str):
    return next(a for a in await service.list_agents(db) if a.name == name)


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(
    data: custom.AgentCreate, owner: Owner, db: Db, journal: JournalDep, request: Request
):
    try:
        await custom.create(db, journal, request.app.state.settings.agents_dir, data, owner.id)
    except custom.CustomAgentError as exc:
        raise _raise(exc) from exc
    return await _saved(db, data.name)


@router.put("/{name}", response_model=AgentOut)
async def replace_agent(
    name: str, data: custom.AgentForm, owner: Owner, db: Db, journal: JournalDep, request: Request
):
    try:
        await custom.update(
            db, journal, request.app.state.settings.agents_dir, name, data, owner.id
        )
    except custom.CustomAgentError as exc:
        raise _raise(exc) from exc
    return await _saved(db, name)


@router.get("/{name}/source", response_model=custom.AgentSource)
async def agent_source(name: str, _: Owner, db: Db, request: Request):
    try:
        return await custom.source(db, request.app.state.settings.agents_dir, name)
    except custom.CustomAgentError as exc:
        raise _raise(exc) from exc


class AgentPatch(BaseModel):
    enabled: bool


@router.patch("/{name}", response_model=AgentOut)
async def update_agent(
    name: str, data: AgentPatch, owner: Owner, db: Db, journal: JournalDep, request: Request
):
    agent = await db.scalar(select(Agent).where(Agent.name == name))
    if agent is None:
        raise HTTPException(404, detail="agent_not_found")
    state = request.app.state
    if name in state.manifest.connected_agents:
        # connected agent: disabling revokes its key, enabling connects it again
        try:
            if data.enabled:
                report = read_report(state.settings.host_report_dir)
                await connected.connect(
                    db, journal, state.secrets, state.manifest, report, owner, name,
                    connected.ConnectIn(),
                )  # fmt: skip
            else:
                await connected.disconnect(db, journal, state.secrets, state.manifest, owner, name)
        except connected.ConnectedError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code, "message": str(exc)}) from exc
        return next(a for a in await service.list_agents(db) if a.name == name)
    agent.enabled = data.enabled
    await db.commit()
    await journal.audit(
        AuditEventType.AGENT_UPDATED,
        actor_principal_id=owner.id,
        agent_id=agent.id,
        details={"agent": name, "enabled": data.enabled},
    )
    return next(a for a in await service.list_agents(db) if a.name == name)


@router.get("/{name}/executions", response_model=list[ExecutionOut])
async def list_executions(
    name: str, _: Owner, db: Db, limit: Annotated[int, Query(ge=1, le=200)] = 20
):
    agent = await db.scalar(select(Agent).where(Agent.name == name))
    if agent is None:
        raise HTTPException(404, detail="agent_not_found")
    return await service.recent_executions(db, agent, limit)
