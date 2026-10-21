# BotPanel MVP

A first-stage Telegram bot management panel:
- email/password registration and login
- bot token verification through Telegram `getMe`
- encrypted bot token storage (Fernet)
- basic welcome text settings
- webhook-based `/start` and `/help` replies when `PUBLIC_BASE_URL` is HTTPS
- PostgreSQL support and Render Blueprint config

## Important limitations

This is an early MVP, not production-ready. It does not yet include email verification, password reset, CSRF protection, rate limiting, a full audit trail, admin roles, billing, multi-worker webhook queueing, or security review. Only connect bots you own and test with a non-critical bot first. Never use it for spam or unsolicited bulk messaging.

`DEMO_MODE` is a UI/config marker, not a security boundary. Real webhook activation requires a correct public HTTPS URL. The generated Render `FERNET_KEY` must remain stable: changing it makes previously stored tokens unreadable. Keep secrets private and restrict repository access.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy `.env.example` to `.env`, set `FERNET_KEY` to the generated key, and export the variables in your shell. Then:

```bash
uvicorn main:app --reload
```

Open http://127.0.0.1:8000

## Deploy to Render

1. Push this folder to a private GitHub repository.
2. In Render, create a Blueprint from the repository and deploy `render.yaml`.
3. After the first deploy, copy the service's HTTPS URL into the `PUBLIC_BASE_URL` environment variable, without a trailing slash.
4. Redeploy. Keep `DEMO_MODE=true` until security controls and privacy terms are reviewed.
5. Generate secrets through Render; do not paste secrets into source files or commit them to GitHub.

The Blueprint's database plan may need to be adjusted to a plan currently available to your Render account.

## Next development milestones

1. Add CSRF protection, rate limiting, email verification and password reset.
2. Move webhook processing into a durable queue and make update handling idempotent.
3. Add bot commands and safe button/flow builder.
4. Add per-user quotas and audit logs.
5. Add billing only after defining a compliant payment-provider arrangement.
