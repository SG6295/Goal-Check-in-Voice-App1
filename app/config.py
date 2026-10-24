"""Environment-only application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


REQUIRED_VARIABLES = (
    "DATABASE_URL",
    "SESSION_SECRET",
    "ACCESS_CODE",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_AGENT_ID",
    "ELEVENLABS_PHONE_NUMBER_ID",
    "ELEVENLABS_WEBHOOK_SECRET",
)


def missing_required_variables() -> list[str]:
    return [
        name
        for name in REQUIRED_VARIABLES
        if not os.environ.get(name, "").strip()
    ]


def normalize_database_url(value: str) -> str:
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def _integer_setting(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class AppConfig:
    database_url: str | None
    session_secret: str | None
    access_code: str | None
    elevenlabs_api_key: str | None
    elevenlabs_agent_id: str | None
    elevenlabs_phone_number_id: str | None
    elevenlabs_webhook_secret: str | None
    call_mode: str
    max_calls_per_goal_per_day: int
    max_concurrent_calls: int
    call_lock_minutes: int


def load_config() -> AppConfig:
    call_mode = os.environ.get("CALL_MODE", "web")
    if call_mode not in {"sip", "web"}:
        raise ValueError("CALL_MODE must be either 'sip' or 'web'")

    database_url = os.environ.get("DATABASE_URL")
    return AppConfig(
        database_url=normalize_database_url(database_url) if database_url else None,
        session_secret=os.environ.get("SESSION_SECRET"),
        access_code=os.environ.get("ACCESS_CODE"),
        elevenlabs_api_key=os.environ.get("ELEVENLABS_API_KEY"),
        elevenlabs_agent_id=os.environ.get("ELEVENLABS_AGENT_ID"),
        elevenlabs_phone_number_id=os.environ.get("ELEVENLABS_PHONE_NUMBER_ID"),
        elevenlabs_webhook_secret=os.environ.get("ELEVENLABS_WEBHOOK_SECRET"),
        call_mode=call_mode,
        max_calls_per_goal_per_day=_integer_setting(
            "MAX_CALLS_PER_GOAL_PER_DAY", 3
        ),
        max_concurrent_calls=_integer_setting("MAX_CONCURRENT_CALLS", 4),
        call_lock_minutes=_integer_setting("CALL_LOCK_MINUTES", 15),
    )
