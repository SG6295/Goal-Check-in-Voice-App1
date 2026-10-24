"""Session middleware and access-code login."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import load_config
from app.db import get_db
from app.models import User


DEFAULT_PERSONA = (
    "You are a pragmatic project manager trained in SMART goals and work breakdown "
    "structures. You are warm but direct. You check that each milestone is specific, "
    "measurable and dated. When something slips, you ask for the next smallest "
    "deliverable and the date it will be done."
)

CONSENT_TEXT = (
    "I agree to receive automated AI voice calls about my goal at this number "
    "between 09:00 and 21:00 IST. I can say stop on any call to opt out."
)

templates = Jinja2Templates(directory="app/templates")
router = APIRouter()


class HostAwareSessionMiddleware:
    """Apply Starlette sessions with secure cookies outside localhost."""

    def __init__(self, app: Any, secret_key: str) -> None:
        self.app = app
        self.secret_key = secret_key

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        host_header = dict(scope.get("headers", [])).get(b"host", b"")
        host = host_header.decode("latin-1").split(":", 1)[0].strip("[]").lower()
        is_localhost = host in {"localhost", "127.0.0.1", "::1"}
        middleware = SessionMiddleware(
            self.app,
            secret_key=self.secret_key,
            https_only=not is_localhost,
            same_site="lax",
        )
        await middleware(scope, receive, send)


def phone_to_e164(value: str) -> str | None:
    phone = re.sub(r"[\s-]", "", value)
    if re.fullmatch(r"\d{10}", phone):
        phone = f"+91{phone}"
    if re.fullmatch(r"\+91[6-9]\d{9}", phone):
        return phone
    return None


def add_session_middleware(app: Any) -> None:
    session_secret = load_config().session_secret
    if session_secret:
        app.add_middleware(
            HostAwareSessionMiddleware,
            secret_key=session_secret,
        )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "errors": {},
            "values": {},
            "consent_text": CONSENT_TEXT,
        },
    )


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    access_code: str = Form(default=""),
    name: str = Form(default=""),
    phone: str = Form(default=""),
    consent: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> Response:
    config = load_config()
    errors: dict[str, str] = {}
    clean_name = name.strip()
    normalized_phone = phone_to_e164(phone)

    if not config.access_code or not secrets.compare_digest(
        access_code.encode("utf-8"), (config.access_code or "").encode("utf-8")
    ):
        errors["access_code"] = "That access code is not correct."
    if not clean_name:
        errors["name"] = "Enter your name."
    elif len(clean_name) > 80:
        errors["name"] = "Name must be 80 characters or fewer."
    if normalized_phone is None:
        errors["phone"] = "Enter a valid Indian mobile number."
    if consent != "on":
        errors["consent"] = "Consent is required to continue."

    if errors:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            status_code=400,
            context={
                "errors": errors,
                "values": {"name": name, "phone": phone},
                "consent_text": CONSENT_TEXT,
            },
        )

    consent_at = datetime.now(timezone.utc)
    statement = (
        insert(User)
        .values(
            name=clean_name,
            phone_e164=normalized_phone,
            persona=DEFAULT_PERSONA,
            consent_at=consent_at,
            consent_text_version="v1",
            consent_ip=request.client.host if request.client else None,
        )
        .on_conflict_do_update(
            index_elements=[User.phone_e164],
            set_={"consent_at": consent_at},
        )
        .returning(User.id)
    )
    try:
        user_id = db.execute(statement).scalar_one()
        db.commit()
    except Exception:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            status_code=503,
            context={
                "errors": {"form": "We could not sign you in. Please try again."},
                "values": {"name": name, "phone": phone},
                "consent_text": CONSENT_TEXT,
            },
        )

    request.session["user_id"] = user_id
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        return None
    return db.scalar(select(User).where(User.id == user_id))
