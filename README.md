# OmniMessage: AI-Powered Multi-Channel Messaging Backend

A backend that receives customer messages from Telegram, WhatsApp, Instagram and Facebook Messenger through webhooks, normalizes them into one model, replies automatically with an AI agent, and streams events to agent dashboards in real time over WebSockets.

The FastAPI service serves the interactive API and the included web inbox from one origin.

## Architecture

```
Customer (Telegram / WhatsApp / Instagram / Messenger)
        │  webhook POST
        ▼
FastAPI webhook ── signature / secret verification, rate limiting
        ▼
Channel adapter ── parses the vendor payload into a NormalizedMessage
        ▼
Message pipeline ── dedupe (unique index), store, get-or-create conversation, WebSocket broadcast
        ▼  (background task, webhook returns 200 immediately)
AI agent ── checks global + per-conversation toggles, loads history and knowledge base,
            calls the LLM provider, falls back to "needs human" on low confidence or error
        ▼
Send via channel adapter ── store outbound message, WebSocket broadcast
```

## Features

- **Unified inbox:** filters (channel, status, unread, AI enabled), search, pagination, unread counts
- **AI auto-replies:** global toggle, per-conversation toggle, configurable system prompt and fallback message, knowledge-base grounding
- **Human takeover:** pause the AI on a conversation and reply manually through the right channel
- **Real-time:** authenticated WebSocket (`message.new`, `conversation.updated`, `conversation.needs_human`)
- **Dashboard stats:** conversations by status, messages per channel, AI vs human replies, average response time
- **Security:** JWT access and refresh tokens (rotation), bcrypt passwords, role-based access (admin / agent), Fernet-encrypted channel credentials (redacted in API responses), webhook signature verification (`X-Hub-Signature-256`, Telegram secret token), rate limiting, idempotent webhooks

## Channel status (please read)

| Channel | Status |
|---|---|
| Telegram | **Live**: real Bot API webhook and replies |
| WhatsApp, Instagram, Messenger | Real adapters following Meta's Graph API formats, including GET verification and HMAC signature checks. Verified through the **signed webhook simulator**, not a live Meta app, because live delivery needs Meta app review or business verification. |

## Tech stack

Python, FastAPI, SQLAlchemy 2.0 (async), Alembic, SQLite (PostgreSQL via `DATABASE_URL`), WebSockets, JWT, bcrypt, cryptography (Fernet), httpx, pytest.

## Setup (Windows PowerShell)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set at least:

- `SECRET_KEY`: `python -c "import secrets; print(secrets.token_urlsafe(64))"`
- `ENCRYPTION_KEY`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `LLM_PROVIDER`: `mock` (no key needed), `openai` or `gemini` (add the matching API key)

Then:

```powershell
python scripts/seed.py
python -m uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000/` for the inbox or `http://localhost:8000/docs` for the interactive API docs.

**Seeded users** (demo only, change before any real deployment):
`admin@example.com` and `agent@example.com`. The passwords are set in `scripts/seed.py`.

## Environment variables

See `.env.example` for the full list. Key groups: database, JWT, `ENCRYPTION_KEY`, LLM provider and keys, Telegram, Meta (WhatsApp / Instagram / Messenger), rate limits, `ENABLE_SIMULATOR`, and `DEMO_MODE`.

## Running the demo

1. Set `DEMO_MODE=true`, `ENABLE_SIMULATOR=true`, and optionally `DEMO_PASSWORD` in `.env`, then restart. On startup, the service runs the existing idempotent seed routine and creates `demo@example.com` with role `agent`.
2. Open `/` and use the demo email and password shown on the login card. The password defaults to `demo1234`.
3. To simulate sample traffic, run:
```powershell
   python scripts/simulate_traffic.py --count 5
```
   This sends signed sample payloads for all four channels to the real webhook endpoints. In demo mode, agents can also use the authenticated simulator endpoint.
4. Check `GET /api/v1/conversations` and `GET /api/v1/dashboard/stats`.
5. Connect to `ws://localhost:8000/api/v1/ws`, send `{"type": "auth", "token": "<access_token>"}` and watch events arrive.

Demo credentials are public by design whenever `DEMO_MODE=true`; do not use demo mode or demo passwords for a production service.

## Deploying to Render

The included `render.yaml` creates one web service that serves both the FastAPI API and `ui/index.html`. It runs Alembic migrations at startup, uses a persistent disk for SQLite, and enables demo mode with the simulator for a ready-to-use demo. Render requires a paid Starter web-service plan for the persistent disk. Render generates the JWT secret and seeded admin/agent passwords; change `DEMO_PASSWORD` in the Render dashboard before sharing the service if you do not want the public demo default.

For a non-demo deployment, set `DEMO_MODE=false`, `ENABLE_SIMULATOR=false`, and configure strong secret values and any required provider credentials in the Render dashboard. Never commit real credentials to `.env.example` or source control.

## Telegram live setup

1. Create a bot with **@BotFather** and copy the token.
2. Put `TELEGRAM_BOT_TOKEN` and a random `TELEGRAM_WEBHOOK_SECRET` in `.env`.
3. Expose the server: `ngrok http 8000`.
4. Register the webhook:
```powershell
   python scripts/set_telegram_webhook.py --url https://<your-ngrok-url>/api/v1/webhooks/telegram
```
5. Message your bot and watch the AI reply.

## Tests

```powershell
python -m pytest -q
```

Covers auth, the AI flow (normal, disabled, low confidence, exception, malformed output, duplicates), Telegram and Meta webhooks (signature checks, ignored events, batching), WebSocket auth and broadcasting, the simulator, conversations and the dashboard.

## API reference

- Interactive docs: `/docs`
- Endpoint and event reference: `API_CONTRACT.md`

## Known limitations

- The WebSocket connection manager and rate limiter are in-memory and single-process. Scaling out would use Redis (pub/sub and a shared limiter).
- The included UI is intentionally a lightweight inbox; API docs remain available at `/docs`.
- Meta channels are validated through the simulator, not live Meta delivery.
- (Update this list if any tests are still failing when you submit.)