"""The owner's current goal, shown large on the admin's welcome page."""

from api.dependencies import CurrentAdmin
from db.dependency import get_db
from db.models.admin_setting import AdminSetting
from fastapi import APIRouter, Depends
from schemas.goal import GoalResponse, GoalUpdate
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

KEY = "goal"


def dump(row: AdminSetting | None) -> dict:
    goal = GoalResponse(
        text=row.value if row else "", updated_at=row.updated_at if row else None
    )
    return goal.model_dump(by_alias=True, mode="json")


@router.get("/admin/goal")
async def get_goal(_admin: CurrentAdmin, db: AsyncSession = Depends(get_db)):
    return {"data": dump(await db.get(AdminSetting, KEY))}


@router.put("/admin/goal")
async def set_goal(
    body: GoalUpdate, _admin: CurrentAdmin, db: AsyncSession = Depends(get_db)
):
    row = await db.get(AdminSetting, KEY)
    if row is None:
        row = AdminSetting(key=KEY, value=body.text)
        db.add(row)
    else:
        row.value = body.text
    await db.commit()
    await db.refresh(row)
    return {"data": dump(row)}
