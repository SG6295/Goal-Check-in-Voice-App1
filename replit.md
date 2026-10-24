# Goal Check-in

An access-code-protected goal check-in web app with explicit voice-call consent and signed ElevenLabs webhook intake.

## Run & Operate

- `uvicorn app.main:app --host 0.0.0.0 --port $PORT` — run the FastAPI app (set `PORT=8000` for local runs)
- `pnpm run typecheck` — full typecheck across all packages
- Required environment: `DATABASE_URL`, `SESSION_SECRET`, `ACCESS_CODE`, `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID`, `ELEVENLABS_WEBHOOK_SECRET`
- Optional environment: `CALL_MODE` (`sip` or `web`, default `web`), `MAX_CALLS_PER_GOAL_PER_DAY` (default `3`), `MAX_CONCURRENT_CALLS` (default `4`), `CALL_LOCK_MINUTES` (default `15`)

## Stack

- Python 3.11
- FastAPI, SQLAlchemy 2.x, psycopg 3, Jinja2, plain CSS
- PostgreSQL; all first-stage tables are created at startup with `Base.metadata.create_all`

## Where things live

- `app/main.py` — FastAPI routes and startup lifecycle
- `app/config.py` — environment-only configuration and required-variable checks
- `app/db.py` and `app/models.py` — PostgreSQL engine, sessions, and schema
- `app/auth.py` — access-code login, consent, and session handling
- `app/webhooks.py` — signed ElevenLabs webhook verification and idempotent intake
- `app/templates/` and `app/static/` — server-rendered pages and CSS

## Architecture decisions

- The application uses the required standalone Python stack; the existing TypeScript API and mockup services remain unchanged.
- This first stage intentionally stops before goal management, milestone management, and call initiation.

## Product

- Access-code login with required, timestamped voice-call consent
- PostgreSQL schema prepared for users, goals, milestones, calls, corrections, and webhook events
- Signed, deduplicated ElevenLabs webhook intake and a recent-events page

## User preferences

- Keep the initial scope limited to authentication, schema, health, and webhook intake; do not build goals, milestones, or calling yet.

## Gotchas

- Keep all credentials and access codes in Replit Secrets; do not place their values in source, templates, or tests.
- `DATABASE_URL` prefixes `postgres://` and `postgresql://` are normalized to the psycopg 3 SQLAlchemy dialect.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
