"""SIP admission control and user-owned call history."""
from datetime import datetime, timedelta, time
from types import SimpleNamespace
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update, func, or_, text
from sqlalchemy.orm import object_session
from sqlalchemy.orm import Session

from app.auth import current_user, templates
from app.config import load_config
from app.db import get_db
from app.models import Call, Goal, Milestone
from app.elevenlabs_client import place_call
from app.call_processing import process_event

router = APIRouter()
IST = ZoneInfo("Asia/Kolkata")
ACTIVE = ("initiating", "in_progress")


def now_ist():
    return datetime.now(IST)


def sync_call(call):
    if not call.conversation_id or call.state not in ACTIVE and not (
        call.state == "failed" and call.failure_reason == "no_webhook"
    ):
        return False
    db = object_session(call)
    if db is None:
        return False
    config = load_config()
    try:
        response = httpx.get(
            f"https://api.elevenlabs.io/v1/convai/conversations/{quote(call.conversation_id, safe='')}",
            headers={"xi-api-key": config.elevenlabs_api_key},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    if not isinstance(data, dict) or data.get("status") not in {"done", "failed"}:
        return False
    event = {"type": "post_call_transcription", "data": data}
    record = SimpleNamespace(processed_at=None)
    try:
        process_event(db, event, record)
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False


def sync_user_calls(db, user_id):
    calls = db.scalars(select(Call).where(
        Call.user_id == user_id, Call.conversation_id.is_not(None),
        or_(Call.state.in_(ACTIVE),
            (Call.state == "failed") & (Call.failure_reason == "no_webhook")),
    ).order_by(Call.requested_at)).all()
    for call in calls:
        sync_call(call)


def owned_user(request, db):
    user = current_user(request, db)
    if user is None:
        raise HTTPException(401, "Please sign in again")
    return user


def pending(db, goal_id, user_id):
    return db.scalars(
        select(Milestone).join(Goal, Milestone.goal_id == Goal.id)
        .where(Goal.id == goal_id, Goal.user_id == user_id, Milestone.status != "done")
        .order_by(Milestone.due_date, Milestone.position).limit(2)
    ).all()


def used_today(db, goal_id, now):
    start = datetime.combine(now.astimezone(IST).date(), time.min, IST)
    return db.scalar(select(func.count()).select_from(Call).where(
        Call.goal_id == goal_id, Call.requested_at >= start,
        Call.requested_at < start + timedelta(days=1),
        or_(Call.failure_reason.is_(None), ~Call.failure_reason.startswith("api_error", autoescape=True)),
    ))


def basic_reason(user, milestones, config, now):
    if user.consent_at is None:
        return "Consent is required before calling"
    if user.opted_out_at is not None:
        return "You opted out of calls"
    if not 9 <= now.astimezone(IST).hour < 21:
        return "Calls are available between 09:00 and 21:00 IST"
    if not milestones:
        return "All milestones are done"
    if config.call_mode != "sip":
        return "Phone calling is not enabled"
    return None


def availability(db, user, goal):
    config, now = load_config(), now_ist()
    used = used_today(db, goal.id, now)
    reason = basic_reason(user, pending(db, goal.id, user.id), config, now)
    cutoff = now - timedelta(minutes=config.call_lock_minutes)
    # Preview the state after the POST's stale sweep; GET does not mutate data.
    active = db.scalar(select(func.count()).select_from(Call).where(
        Call.state.in_(ACTIVE), Call.requested_at >= cutoff))
    stale_for_goal = db.scalar(select(Call.id).where(
        Call.goal_id == goal.id, Call.state.in_(ACTIVE), Call.requested_at < cutoff).limit(1))
    if not reason and active >= config.max_concurrent_calls:
        reason = "All lines are busy, try again in a minute"
    if not reason and used >= config.max_calls_per_goal_per_day:
        reason = "Daily call limit reached"
    if not reason and goal.call_lock_until and goal.call_lock_until >= now and not stale_for_goal:
        reason = "A call for this goal is already in progress"
    return dict(used=used, maximum=config.max_calls_per_goal_per_day, reason=reason)


def flash(request, message):
    request.session["flash"] = message
    return RedirectResponse("/", status_code=303)


def dynamic_variables(user, goal, milestones, call_id):
    first = milestones[0]
    second = milestones[1] if len(milestones) > 1 else None
    return {key: str(value) for key, value in dict(
        user_name=user.name.split()[0], persona=user.persona, goal_id=goal.id,
        call_id=call_id, goal_title=goal.title,
        goal_deadline=goal.deadline.strftime("%d %B %Y").lstrip("0"),
        milestone_1_title=first.title, milestone_1_due=first.due_date.strftime("%d %B").lstrip("0"),
        milestone_2_title=second.title if second else "none",
        milestone_2_due=second.due_date.strftime("%d %B").lstrip("0") if second else "none",
    ).items()}


@router.post("/goals/{goal_id}/call")
def initiate(goal_id: int, request: Request, db: Session = Depends(get_db)):
    user = owned_user(request, db)
    goal = db.scalar(select(Goal).where(Goal.id == goal_id, Goal.user_id == user.id))
    if goal is None:
        raise HTTPException(404, "Goal not found")
    config, now = load_config(), now_ist()
    reason = basic_reason(user, pending(db, goal.id, user.id), config, now)
    if reason:
        return flash(request, reason)
    stale = db.scalars(update(Call).where(
        Call.state.in_(ACTIVE),
        Call.requested_at < now - timedelta(minutes=config.call_lock_minutes),
    ).values(state="failed", failure_reason="no_webhook", ended_at=now)
        .returning(Call.goal_id)).all()
    if stale:
        db.execute(update(Goal).where(Goal.id.in_(stale)).values(call_lock_until=None))
    db.commit()

    db.execute(text("SELECT pg_advisory_xact_lock(8675309)"))
    active = db.scalar(select(func.count()).select_from(Call).where(Call.state.in_(ACTIVE)))
    if active >= config.max_concurrent_calls:
        db.rollback()
        return flash(request, "All lines are busy, try again in a minute")
    now = now_ist()
    if used_today(db, goal.id, now) >= config.max_calls_per_goal_per_day:
        db.rollback()
        return flash(request, "Daily call limit reached")
    locked = db.scalar(update(Goal).where(
        Goal.id == goal.id, or_(Goal.call_lock_until.is_(None), Goal.call_lock_until < now),
    ).values(call_lock_until=now + timedelta(minutes=config.call_lock_minutes)).returning(Goal.id))
    if locked is None:
        db.rollback()
        return flash(request, "A call for this goal is already in progress")
    db.refresh(user)
    milestones = pending(db, goal.id, user.id)
    reason = basic_reason(user, milestones, config, now)
    if reason:
        db.rollback()
        return flash(request, reason)
    call_id = uuid4()
    variables = dynamic_variables(user, goal, milestones, call_id)
    phone = user.phone_e164
    db.add(Call(id=call_id, user_id=user.id, goal_id=goal.id, state="initiating",
                mode="sip", milestone_1_id=milestones[0].id,
                milestone_2_id=milestones[1].id if len(milestones) > 1 else None,
                requested_at=now))
    db.commit()
    result, error = place_call(config, phone, variables)
    call = db.scalar(select(Call).where(Call.id == call_id).with_for_update())
    if not error:
        if call.conversation_id is None:
            call.conversation_id = result.get("conversation_id")
        call.sip_call_id = result.get("sip_call_id")
    # A fast webhook may already have completed this call; never undo it.
    if call.state == "initiating":
        if error:
            call.state, call.failure_reason, call.ended_at = "failed", error, now_ist()
            db.execute(update(Goal).where(Goal.id == goal_id).values(call_lock_until=None))
        else:
            call.state = "in_progress"
    db.commit()
    return flash(request, "Call could not be placed" if error else "Calling you now")


@router.get("/calls")
def history(request: Request, db: Session = Depends(get_db)):
    user = owned_user(request, db)
    sync_user_calls(db, user.id)
    calls = db.scalars(select(Call).where(Call.user_id == user.id)
                      .order_by(Call.requested_at.desc())).all()
    return templates.TemplateResponse(request=request, name="calls.html",
                                      context={"calls": calls, "detail": False, "ist": IST})


@router.get("/calls/{call_id}")
def detail(call_id: UUID, request: Request, db: Session = Depends(get_db)):
    user = owned_user(request, db)
    call = db.scalar(select(Call).where(Call.id == call_id, Call.user_id == user.id))
    if call is None:
        raise HTTPException(404, "Call not found")
    turns = []
    for turn in call.transcript or []:
        if not isinstance(turn, dict):
            continue
        try:
            seconds = max(0, int(float(turn.get("time_in_call_secs", 0))))
        except (TypeError, ValueError):
            seconds = 0
        turns.append(dict(speaker="Agent" if turn.get("role") == "agent" else "You",
                          message=turn.get("message") or "",
                          time=f"{seconds // 60}:{seconds % 60:02}"))
    return templates.TemplateResponse(request=request, name="calls.html",
        context={"calls": [call], "detail": True, "turns": turns, "ist": IST})
