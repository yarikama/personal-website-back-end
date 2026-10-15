"""The admin's to-dos, on the welcome page (services/todos.py)."""

from datetime import date
from uuid import UUID

from api.dependencies import CurrentAdmin
from db.dependency import get_async_db
from db.models.todo import AdminTodo
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from schemas.todos import TodoCreate, TodoResponse, TodoUpdate
from services import todos
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


def dump(row: AdminTodo) -> dict:
    return TodoResponse.model_validate(row).model_dump(by_alias=True, mode="json")


async def find(db: AsyncSession, id: UUID) -> AdminTodo:
    row = await db.get(AdminTodo, id)
    if row is None or row.removed:
        raise HTTPException(status_code=404, detail="To-do not found")
    return row


@router.get("/admin/todos")
async def list_todos(
    _admin: CurrentAdmin,
    day: date = Query(..., description="The owner's today, in their local time"),
    db: AsyncSession = Depends(get_async_db),
):
    return {"data": [dump(row) for row in await todos.for_day(db, day)]}


@router.get("/admin/todos/history")
async def todo_history(
    _admin: CurrentAdmin,
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_async_db),
):
    """Past to-dos' text, most recent first, to complete new ones from."""
    return {"data": await todos.history(db, limit)}


@router.post("/admin/todos", status_code=status.HTTP_201_CREATED)
async def add_todo(
    body: TodoCreate, _admin: CurrentAdmin, db: AsyncSession = Depends(get_async_db)
):
    row = AdminTodo(text=body.text, href=body.href, day=body.day)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"data": dump(row)}


@router.patch("/admin/todos/{id}")
async def tick_todo(
    id: UUID,
    body: TodoUpdate,
    _admin: CurrentAdmin,
    db: AsyncSession = Depends(get_async_db),
):
    row = await find(db, id)
    row.done_on = body.day if body.done else None
    await db.commit()
    await db.refresh(row)
    return {"data": dump(row)}


@router.delete("/admin/todos/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_todo(
    id: UUID, _admin: CurrentAdmin, db: AsyncSession = Depends(get_async_db)
):
    # Hidden, not deleted: a daily item would otherwise come back that day.
    (await find(db, id)).removed = True
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
