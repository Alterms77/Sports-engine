# Deploying Sports-Engine on Railway with PostgreSQL

This guide explains how to add a PostgreSQL database to your Sports-Engine
deployment on Railway so the bot persists fixture data and **never re-uses
finished matches** in parlay generation.

---

## Why PostgreSQL?

The bot previously read upcoming soccer fixtures from a local CSV file
(`sports_engine/data/today_matches.csv`). If the CSV became stale (e.g. an
API refresh failed or the container restarted without writing a fresh file),
the bot could include already-finished matches in `/parlay` suggestions.

With PostgreSQL the bot:

1. **Auto-creates** the `fixtures` table on startup (idempotent — safe on
   every deploy).
2. **Upserts** every fixture (including finished ones) on each API refresh.
3. **Queries only upcoming, not-started** fixtures when building parlays.
4. Falls back to the CSV automatically when `DATABASE_URL` is not set (local
   development or if the DB is temporarily unavailable).

---

## Step 1 — Add the PostgreSQL plugin in Railway

1. Open your Railway **project**.
2. Click **+ New** → **Database** → **PostgreSQL**.
3. Railway provisions the database and shows it in your project overview.

---

## Step 2 — Link the database to your worker service

Railway injects database variables into a service through a **Variable
Reference**.

1. In Railway, open your **worker** service.
2. Go to **Variables** → **Add Variable Reference** (or **New Variable** →
   **Add from service/plugin**).
3. Select the **PostgreSQL** plugin you just created.
4. Import at minimum: **`DATABASE_URL`**.

Alternatively you can copy the value of `DATABASE_URL` from the Postgres
plugin's Variables tab and paste it manually into `worker → Variables`.

> **Important:** always use `DATABASE_URL` (the private/internal URL), **not**
> `DATABASE_PUBLIC_URL`. The internal URL routes traffic within Railway's
> private network and avoids egress costs.

---

## Step 3 — Required environment variables

In your worker service Variables, make sure you have **all three**:

```
TOKEN            = <your Telegram Bot token>
API_SPORTS_KEY   = <your API-Sports / API-Football key>
DATABASE_URL     = postgresql://user:password@postgres.railway.internal:5432/railway
```

> `DATABASE_URL` is filled in automatically via the Variable Reference in
> Step 2.  The scheme may start with `postgres://` — the bot normalises it.

---

## Step 4 — Deploy

Trigger a new deployment of the **worker** service (Railway usually does this
automatically when variables change). On startup the bot:

1. Detects `DATABASE_URL` in the environment and logs:
   `PostgreSQL: DATABASE_URL detectado — inicializando tabla fixtures…`
2. Creates the `fixtures` table (and its indices) if they do not exist — no
   manual SQL required.  Logs: `PostgreSQL: tabla fixtures lista ✓`
3. Fetches today's and tomorrow's fixtures from API-Sports and upserts them.
4. Refreshes fixtures every 10 minutes in the background.

You can confirm the table was created by opening the Railway Postgres plugin →
**Data** tab and checking for the `fixtures` table.

---

## Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `TOKEN` | **Yes** | Telegram Bot API token. |
| `API_SPORTS_KEY` | **Yes** | API-Sports / API-Football key for fetching live fixtures. |
| `DATABASE_URL` | **Yes** (for Postgres) | Private connection string from Railway Postgres plugin. |
| `DATABASE_PUBLIC_URL` | No | Public connection string — only needed for external tools (DBeaver, psql from your PC). Do **not** use in the bot. |
| `ALERTS_CHANNEL_ID` | No | Telegram channel ID for daily alert broadcasts. |
| `ODDS_API_KEY` | No | The Odds API key for live bookmaker odds in the auto-scanner. |

---

## Startup log reference

When everything is configured correctly you should see lines like these in
Railway → Deployments → Logs:

```
DATABASE_URL: configured ✓ (Postgres fixture storage enabled)
API_SPORTS_KEY: configured ✓ (live soccer fixtures enabled)
PostgreSQL: DATABASE_URL detectado — inicializando tabla fixtures…
PostgreSQL: tabla fixtures lista ✓
update_matches: upserted 18 fixture(s) to Postgres (total for date: 18)
```

If you see `DATABASE_URL: NOT set` or `API_SPORTS_KEY: NOT set` in the logs,
add the missing variable in Railway → Variables.

---

## Debug CLI

A small command-line tool is included to inspect the fixtures database:

```bash
# From the repo root (requires DATABASE_URL in .env or environment)
python sports_engine/db/fixtures_cli.py
```

---

## How the DB prevents stale fixtures

| Scenario | Behaviour |
|---|---|
| API refresh succeeds | All fixtures (pending + finished) are upserted. Periodic job refreshes today + tomorrow every 10 min. |
| API refresh fails | DB retains fixtures from previous runs; finished ones remain marked `FT/AET/…` and are excluded from parlays. |
| `DATABASE_URL` not set | Bot falls back to CSV mode (local dev behaviour). |
| psycopg not installed | Same fallback — warning logged. |

---

## Local development (no Postgres)

No changes needed. Simply omit `DATABASE_URL` from your `.env` file and the
bot will continue reading from `sports_engine/data/today_matches.csv` as
before.

To test with a local Postgres instance:

```bash
pip install "psycopg[binary]>=3.1"
export DATABASE_URL="postgresql://postgres:password@localhost:5432/sports_engine"
python sports_engine/db/fixtures_cli.py
```

