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

1. **Upserts** every fixture (including finished ones) on each API refresh.
2. **Queries only upcoming, not-started** fixtures when building parlays.
3. Falls back to the CSV automatically when `DATABASE_URL` is not set (local
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

## Step 3 — Verify

In `worker → Variables` you should now see:

```
DATABASE_URL = postgresql://user:password@postgres.railway.internal:5432/railway
```

The scheme may start with `postgres://` — the bot normalises it automatically.

---

## Step 4 — Deploy

Trigger a new deployment of the **worker** service (Railway usually does this
automatically when variables change). On startup the bot:

1. Detects `DATABASE_URL` in the environment.
2. Creates the `fixtures` table (and its indices) if it does not exist — no
   manual SQL required.
3. Upserts fixtures on every API refresh cycle.

You can confirm the table was created by opening the Railway Postgres plugin →
**Data** tab and checking for the `fixtures` table.

---

## Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | **Yes** (for Postgres) | Private connection string from Railway Postgres plugin. |
| `DATABASE_PUBLIC_URL` | No | Public connection string — only needed for external tools (DBeaver, psql from your PC). Do **not** use in the bot. |
| `API_SPORTS_KEY` | Yes | API-Sports / API-Football key for fetching live fixtures. |
| `TELEGRAM_TOKEN` | Yes | Telegram Bot API token. |

---

## Debug CLI

A small command-line tool is included to inspect the fixtures database:

```bash
# From the repo root (requires DATABASE_URL in .env or environment)
python sports_engine/db/fixtures_cli.py
```

Sample output:

```
Sports-Engine Fixtures CLI
======================================================================
Database: connected ✓

Upcoming not-started fixtures: 12

Next 24 hours (3 fixture(s)):
  [NS  ] Real Madrid               vs Barcelona              | La Liga                         | 2025-06-01 19:00 UTC
  [NS  ] Manchester City            vs Arsenal                | Premier League                  | 2025-06-01 14:00 UTC
  [NS  ] PSG                        vs Lyon                   | Ligue 1                         | 2025-06-01 20:00 UTC

Latest finished fixtures (up to 10):
  [FT  ] Atletico Madrid            vs Getafe                 | La Liga                         | 2025-05-31 19:00 UTC
```

---

## How the DB prevents stale fixtures

| Scenario | Behaviour |
|---|---|
| API refresh succeeds | All fixtures (pending + finished) are upserted. |
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
