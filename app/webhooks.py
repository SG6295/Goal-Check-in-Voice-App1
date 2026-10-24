"""ElevenLabs webhook signature verification and idempotent event intake."""

from __future__ import annotations

import logging
from typing import Any

from elevenlabs import ElevenLabs
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import load_config
from app.db import get_db
from app.models import WebhookEvent


logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhooks/elevenlabs")
async def receive_elevenlabs_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    body = await request.body()
    config = load_config()

    try:
        client = ElevenLabs(api_key=config.elevenlabs_api_key)
        event = client.webhooks.construct_event(
            rawBody=body.decode("utf-8"),
            sig_header=request.headers.get("elevenlabs-signature"),
            secret=config.elevenlabs_webhook_secret,
        )
    except Exception:
        return JSONResponse({"error": "invalid signature"}, status_code=401)

    event_type = event.get("type")
    if event_type == "post_call_audio":
        return JSONResponse({"status": "received"})

    data: Any = event.get("data")
    conversation_id = data.get("conversation_id") if isinstance(data, dict) else None
    event_timestamp = event.get("event_timestamp")
    try:
        statement = (
            insert(WebhookEvent)
            .values(
                conversation_id=conversation_id,
                event_type=event_type,
                event_timestamp=event_timestamp,
                payload=event,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    WebhookEvent.conversation_id,
                    WebhookEvent.event_type,
                ]
            )
            .returning(WebhookEvent.id)
        )
        inserted_id = db.execute(statement).scalar_one_or_none()
        db.commit()
    except Exception:
        db.rollback()
        logger.error(
            "Failed to store ElevenLabs webhook conversation_id=%s event_type=%s",
            conversation_id,
            event_type,
        )
        return JSONResponse({"error": "database write failed"}, status_code=500)

    return JSONResponse(
        {
            "status": "received",
            "duplicate": inserted_id is None,
        }
    )
