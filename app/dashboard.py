"""Goal, milestone and persona forms; all data is scoped to the session user."""
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import DEFAULT_PERSONA, current_user, templates
from app.db import get_db
from app.models import Goal, Milestone

router = APIRouter()
IST = ZoneInfo("Asia/Kolkata")


def signed_in_user(request, db):
    user = current_user(request, db)
    if user is None:
        raise HTTPException(401, "Please sign in again.")
    return user


def clean_persona(value):
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    while "{{" in value or "}}" in value:
        value = value.replace("{{", "").replace("}}", "")
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def validate_goal(form, today):
    errors, rows = {}, []
    title = str(form.get("title", "")).strip()
    definition = str(form.get("done_definition", "")).strip()
    for field, value, limit in (
        ("title", title, 120), ("done_definition", definition, 500)
    ):
        if not value or len(value) > limit:
            errors[field] = f"Required; maximum {limit} characters."
    try:
        deadline = date.fromisoformat(str(form.get("deadline", "")))
        if deadline < today:
            errors["deadline"] = "Deadline must be today or later (IST)."
    except ValueError:
        deadline = None
        errors["deadline"] = "Enter a valid deadline."
    for i in range(1, 6):
        name = str(form.get(f"milestone_title_{i}", "")).strip()
        raw_date = str(form.get(f"milestone_date_{i}", "")).strip()
        if not name and not raw_date:
            continue
        if not name or len(name) > 80:
            errors[f"milestone_{i}"] = "Provide a title of 1–80 characters and a due date."
        try:
            due = date.fromisoformat(raw_date)
            if due < today or (deadline and due > deadline):
                errors[f"milestone_{i}"] = "Due date must be between today and the goal deadline."
        except ValueError:
            due = None
            errors[f"milestone_{i}"] = "Provide a valid due date and title."
        if name and due:
            rows.append((name, due))
    if len(rows) < 3:
        errors["milestones"] = "Fill at least three milestone rows."
    return errors, title, definition, deadline, rows


def render_dashboard(request, db, user, errors=None, values=None, status=200):
    from app.calls import availability
    goal = db.scalar(select(Goal).where(Goal.user_id == user.id))
    milestones = db.scalars(
        select(Milestone).join(Goal, Milestone.goal_id == Goal.id)
        .where(Goal.user_id == user.id)
        .order_by(Milestone.due_date, Milestone.position)
    ).all() if goal else []
    return templates.TemplateResponse(
        request=request, name="home.html", status_code=status,
        context=dict(user=user, goal=goal, milestones=milestones,
                     calling=availability(db, user, goal) if goal else None,
                     flash=request.scope.get("session", {}).pop("flash", None),
                     errors=errors or {}, values=values or {},
                     today=datetime.now(IST).date().isoformat(), ist=IST),
    )


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if user is None:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)
    return render_dashboard(request, db, user)


@router.post("/goal")
async def create_goal(request: Request, db: Session = Depends(get_db)):
    user = signed_in_user(request, db)
    form = await request.form()
    if db.scalar(select(Goal.id).where(Goal.user_id == user.id)):
        return render_dashboard(request, db, user, {"form": "You already have a goal; a second goal is not allowed."}, status=409)
    errors, title, definition, deadline, rows = validate_goal(form, datetime.now(IST).date())
    if errors:
        return render_dashboard(request, db, user, errors, dict(form), 400)
    try:
        goal = Goal(user_id=user.id, title=title, done_definition=definition, deadline=deadline)
        db.add(goal)
        db.flush()
        for position, (name, due) in enumerate(rows, 1):
            db.add(Milestone(goal_id=goal.id, position=position, title=name,
                             due_date=due, status="not_started", status_source="user",
                             status_updated_at=datetime.now(IST)))
        db.commit()
    except IntegrityError:
        db.rollback()
        return render_dashboard(request, db, user, {"form": "The goal could not be saved. You may already have a goal; refresh before trying again."}, dict(form), 409)
    return RedirectResponse("/", status_code=303)


@router.post("/milestones/{milestone_id}/done")
def mark_done(milestone_id: int, request: Request, db: Session = Depends(get_db)):
    user = signed_in_user(request, db)
    milestone = db.scalar(
        select(Milestone).join(Goal, Milestone.goal_id == Goal.id)
        .where(Goal.user_id == user.id, Milestone.id == milestone_id)
    )
    if milestone is None:
        raise HTTPException(404, "Milestone not found")
    milestone.status = "done"
    milestone.status_source = "user"
    milestone.status_updated_at = datetime.now(IST)
    db.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/persona")
async def save_persona(request: Request, db: Session = Depends(get_db)):
    user = signed_in_user(request, db)
    form = await request.form()
    value = DEFAULT_PERSONA if form.get("action") == "reset" else clean_persona(str(form.get("persona", "")))
    if not value or len(value) > 500:
        return render_dashboard(request, db, user, {"persona": "Enter 1–500 characters after cleaning."}, {"persona": value}, 400)
    user.persona = value
    db.commit()
    return RedirectResponse("/", status_code=303)
