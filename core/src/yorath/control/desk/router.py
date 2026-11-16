"""``/desk`` and ``/tasks``: the Desk and the task manager, read-only, owner only."""

from typing import Annotated

from fastapi import APIRouter, Query

from yorath.control.desk import views
from yorath.control.identity.deps import Db, Owner
from yorath.control.model import ExecutionTrigger

router = APIRouter(tags=["desk"])


@router.get("/desk/summary", response_model=views.DeskSummary)
async def desk_summary(_: Owner, db: Db):
    return await views.desk_summary(db)


@router.get("/tasks", response_model=views.TaskList)
async def list_tasks(
    _: Owner,
    db: Db,
    status: views.TaskGroup = views.TaskGroup.ALL,
    agent: str | None = None,
    trigger: ExecutionTrigger | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    return await views.list_tasks(db, group=status, agent=agent, trigger=trigger, limit=limit)
