# TestNow API

Run the API locally with `docker compose up --build`, then apply production schema migrations with `alembic upgrade head` from this directory. Railway runs the migration command before API deployment.

Required production configuration is in the root `.env.example`. The worker requires `OPENROUTER_API_KEY`; imports safely remain failed/reviewable when that key is absent.
