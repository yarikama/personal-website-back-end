from fastapi import APIRouter

from api.routes import (
    ask,
    ask_questions,
    auth,
    autocomplete,
    categories,
    contact,
    csp,
    github,
    goal,
    lab_notes,
    projects,
    todos,
    upload,
    visitors,
)

router = APIRouter()
router.include_router(auth.router, tags=["auth"], prefix="/v1")
router.include_router(categories.router, tags=["categories"], prefix="/v1")
router.include_router(projects.router, tags=["projects"], prefix="/v1")
router.include_router(lab_notes.router, tags=["lab-notes"], prefix="/v1")
router.include_router(contact.router, tags=["contact"], prefix="/v1")
router.include_router(csp.router, tags=["security"], prefix="/v1")
router.include_router(upload.router, tags=["upload"], prefix="/v1")
router.include_router(autocomplete.router, tags=["autocomplete"], prefix="/v1")
router.include_router(ask.router, tags=["ask"], prefix="/v1")
router.include_router(ask_questions.router, tags=["ask"], prefix="/v1")
router.include_router(todos.router, tags=["admin"], prefix="/v1")
router.include_router(github.router, tags=["admin"], prefix="/v1")
router.include_router(goal.router, tags=["admin"], prefix="/v1")
router.include_router(visitors.router, tags=["admin"], prefix="/v1")
