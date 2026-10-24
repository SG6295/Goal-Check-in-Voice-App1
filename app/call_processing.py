"""Transactional processing of verified ElevenLabs events."""
import re
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy import select, update
from app.models import Call, Goal, Milestone, User


def normalize_status(value):
    if value is None:
        return None
    value = re.sub(r"[ -]", "_", str(value).strip().lower())
    value = {"complete": "done", "completed": "done", "finished": "done",
             "behind": "at_risk", "delayed": "at_risk"}.get(value, value)
    return value if value in {"done", "on_track", "at_risk", "blocked", "not_started", "not_applicable"} else "unclear"


def process_event(db, event, record):
    data = event.get("data") or {}
    now = datetime.now(timezone.utc)
    conversation_id = data.get("conversation_id")
    call = db.scalar(select(Call).where(Call.conversation_id == conversation_id).with_for_update()) if conversation_id else None
    if call is None:
        raw_id = ((data.get("conversation_initiation_client_data") or {})
                  .get("dynamic_variables") or {}).get("call_id")
        try:
            call_id = UUID(str(raw_id))
        except (ValueError, TypeError):
            call_id = None
        if call_id:
            call = db.scalar(select(Call).where(Call.id == call_id).with_for_update())
    if call is None:
        record.processed_at = now
        return
    if conversation_id and call.conversation_id is None:
        call.conversation_id = conversation_id
    if event["type"] == "post_call_transcription":
        analysis = data.get("analysis") or {}
        results = analysis.get("data_collection_results") or {}

        def field(name):
            value = results.get(name)
            return value.get("value") if isinstance(value, dict) else value

        first, second = field("m1_status"), field("m2_status")
        call.m1_status_raw = str(first) if first is not None else None
        call.m2_status_raw = str(second) if second is not None else None
        call.m1_status, call.m2_status = normalize_status(first), normalize_status(second)
        call.transcript = data.get("transcript")
        duration = (data.get("metadata") or {}).get("call_duration_secs")
        call.duration_secs = int(duration) if duration is not None else None
        summary = field("summary_note")
        if summary is None:
            summary = analysis.get("transcript_summary")
        call.summary_note = str(summary) if summary is not None else None
        blocker = field("blocker")
        call.blocker = None if blocker is None or str(blocker).strip().lower() == "none" else str(blocker)
        opt_out = field("opt_out")
        call.opt_out = opt_out is True or opt_out == "true"
        unreviewed = {None, "unclear", "not_applicable"}
        call.state = "no_review" if (
            call.m1_status in unreviewed and call.m2_status in unreviewed
            and call.duration_secs is not None and call.duration_secs < 25
        ) else "completed"
        for milestone_id, status in [(call.milestone_1_id, call.m1_status), (call.milestone_2_id, call.m2_status)]:
            if milestone_id and status not in unreviewed:
                db.execute(update(Milestone).where(
                    Milestone.id == milestone_id, Milestone.goal_id == call.goal_id,
                    Milestone.goal_id.in_(select(Goal.id).where(Goal.user_id == call.user_id)),
                ).values(status=status, status_source="agent", status_updated_at=now))
        if call.opt_out:
            db.execute(update(User).where(User.id == call.user_id).values(opted_out_at=now))
    elif event["type"] == "call_initiation_failure":
        reason = data.get("failure_reason")
        call.state = {"no-answer": "no_answer", "busy": "busy"}.get(reason, "failed")
        body = ((data.get("metadata") or {}).get("body") or {})
        call.failure_reason = " ".join(str(v) for v in
            (reason, body.get("sip_status_code"), body.get("error_reason")) if v is not None)
    else:
        record.processed_at = now
        return
    call.ended_at = now
    # A delayed event must not unlock a newer active call for the same goal.
    db.flush()
    newer_active = db.scalar(select(Call.id).where(
        Call.goal_id == call.goal_id, Call.id != call.id,
        Call.state.in_(("initiating", "in_progress"))).limit(1))
    if not newer_active:
        db.execute(update(Goal).where(Goal.id == call.goal_id).values(call_lock_until=None))
    record.processed_at = now
