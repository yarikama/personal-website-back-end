"""The owner's current goal, shown large on the admin's welcome page."""

from fastapi import APIRouter

from api.dependencies import CurrentAdmin
from db.dependency import DB
from db.models.admin_setting import AdminSetting
from schemas.goal import GoalResponse, GoalUpdate

router = APIRouter()

KEY = "goal"


def goal(row: AdminSetting | None) -> GoalResponse:
    return GoalResponse(
        text=row.value if row else "", updated_at=row.updated_at if row else None
    )


@router.get("/admin/goal")
async def get_goal(_admin: CurrentAdmin, db: DB):
    return {"data": goal(await db.get(AdminSetting, KEY))}


@router.put("/admin/goal")
async def set_goal(body: GoalUpdate, _admin: CurrentAdmin, db: DB):
    row = await db.get(AdminSetting, KEY)
    if row is None:
        row = AdminSetting(key=KEY, value=body.text)
        db.add(row)
    else:
        row.value = body.text
    await db.commit()
    await db.refresh(row)
    return {"data": goal(row)}
