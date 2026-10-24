"""FastAPI entry point for the goal check-in application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import timezone
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import add_session_middleware, current_user, router as auth_router
from app.config import load_config, missing_required_variables
from app.db import dispose_database, get_db, initialize_database
from app.models import WebhookEvent
from app.webhooks import router as webhook_router


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
config = load_config()
templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    missing = missing_required_variables()
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        raise RuntimeError("Required environment configuration is missing")
    if not config.database_url:
        logger.error("Missing required environment variables: DATABASE_URL")
        raise RuntimeError("Required environment configuration is missing")

    initialize_database(config.database_url)
    try:
        yield
    finally:
        dispose_database()


app = FastAPI(title="Goal Check-in", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(auth_router)
app.include_router(webhook_router)


@app.middleware("http")
async def require_session(request: Request, call_next):
    path = request.url.path
    public_paths = {"/login", "/healthz", "/webhooks/elevenlabs"}
    if path not in public_paths and not path.startswith("/static/"):
        if not request.session.get("user_id"):
            return RedirectResponse("/login", status_code=303)
    return await call_next(request)


add_session_middleware(app)


@app.get("/healthz")
def healthz(db: Session = Depends(get_db)) -> JSONResponse:
    try:
        db.execute(text("SELECT 1"))
        return JSONResponse({"ok": True, "db": True})
    except Exception:
        return JSONResponse({"ok": False, "db": False}, status_code=503)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = current_user(request, db)
    if user is None:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"name": user.name},
    )


@app.get("/admin/events", response_class=HTMLResponse)
def admin_events(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    events = db.scalars(
        select(WebhookEvent)
        .order_by(WebhookEvent.received_at.desc())
        .limit(20)
    ).all()
    kolkata = ZoneInfo("Asia/Kolkata")
    display_events = []
    for event in events:
        received_at = event.received_at
        if received_at is not None and received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
        display_events.append(
            {
                "received_at": received_at.astimezone(kolkata)
                if received_at is not None
                else None,
                "event_type": event.event_type,
                "conversation_id": event.conversation_id,
                "processed_at": event.processed_at.astimezone(kolkata)
                if event.processed_at is not None
                and event.processed_at.tzinfo is not None
                else (
                    event.processed_at.replace(tzinfo=timezone.utc).astimezone(kolkata)
                    if event.processed_at is not None
                    else None
                ),
            }
        )
    return templates.TemplateResponse(
        request=request,
        name="admin_events.html",
        context={"events": display_events},
    )
