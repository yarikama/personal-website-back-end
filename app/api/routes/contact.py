"""
The contact form (POST /contact, public) and the owner's inbox of its
messages (/admin/contact: list, mark read or replied, delete). Each new
message is also emailed to the owner (services/notify.py).
"""

from uuid import UUID

from api.crud import get_or_404
from api.dependencies import CurrentAdmin
from api.dependencies.rate_limit import rate_limit
from core.paginator import paginate
from db.dependency import DB
from db.models.contact import ContactMessage
from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query
from schemas.contact import ContactCreate, ContactResponse, ContactUpdate
from services.notify import NewMessage, send_contact_notification
from services.rate_limit import CONTACT
from sqlalchemy import select

router = APIRouter()


@router.post(
    "/contact",
    status_code=201,
    dependencies=[Depends(rate_limit(CONTACT, "messages"))],
)
async def create_contact(
    contact: ContactCreate,
    background: BackgroundTasks,
    db: DB,
):
    db_contact = ContactMessage(**contact.model_dump())
    db.add(db_contact)
    await db.commit()
    await db.refresh(db_contact)
    # After the response: the visitor does not wait for Gmail, and a failed
    # email cannot fail a message that is already saved.
    background.add_task(
        send_contact_notification,
        NewMessage(contact.name, contact.email, contact.subject, contact.message),
    )
    return {
        "data": {
            "id": str(db_contact.id),
            "message": "Thank you for your message. I'll get back to you soon!",
        }
    }


@router.get("/admin/contact", status_code=200)
async def get_contacts(
    _admin: CurrentAdmin,
    db: DB,
    read: bool | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    query = select(ContactMessage)
    if read is not None:
        query = query.where(ContactMessage.read == read)
    query = query.order_by(ContactMessage.created_at.desc())
    return await paginate(db, query, ContactResponse, offset, limit)


@router.patch("/admin/contact/{id}")
async def update_contact(
    contact_update: ContactUpdate,
    _admin: CurrentAdmin,
    db: DB,
    id: UUID = Path(...),
):
    db_contact = await get_or_404(db, ContactMessage, id, "Contact message")
    for field, value in contact_update.model_dump(exclude_unset=True).items():
        setattr(db_contact, field, value)

    await db.commit()
    await db.refresh(db_contact)
    return {"data": ContactResponse.model_validate(db_contact)}


@router.delete("/admin/contact/{id}", status_code=204)
async def delete_contact(
    _admin: CurrentAdmin,
    db: DB,
    id: UUID = Path(...),
):
    await db.delete(await get_or_404(db, ContactMessage, id, "Contact message"))
    await db.commit()
