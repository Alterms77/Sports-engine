# Deploying Sports-Engine on Railway

This guide covers every step needed to deploy the bot on Railway, including
all required and optional environment variables.

---

## Quick start (minimum viable deploy)

Set **exactly three** variables in Railway → your service → **Variables**:

```
TOKEN          = <your Telegram Bot token from @BotFather>
API_SPORTS_KEY = <your API-Football key from dashboard.api-football.com>
DATABASE_URL   = <auto-filled by Railway Postgres plugin — see Step 1>
```

That's it. Every other variable is optional with sensible defaults.

---

## Step 1 — Add a PostgreSQL database (recommended)

A Postgres database lets the bot remember fixture results across restarts so
it never suggests already-finished matches in parlays.

1. Open your Railway **project**.
2. Click **+ New** → **Database** → **PostgreSQL**.
3. Railway provisions the database instantly.
4. Open your **bot service** → **Variables** → **Add Variable Reference**.
5. Select the PostgreSQL plugin and import **`DATABASE_URL`**.

> Always use `DATABASE_URL` (private/internal URL), **not** `DATABASE_PUBLIC_URL`.

The bot auto-creates all required tables on startup (idempotent `IF NOT EXISTS`).
No manual SQL needed.

When `DATABASE_URL` is not set the bot falls back to CSV storage — perfectly
fine for local development and testing.

---

## Step 2 — Set variables in Railway

Go to your service → **Variables** and add the values below.
Copy variable names from `.env.example` at the repo root (never commit real secrets).

### Required

| Variable | Where to get it |
|---|---|
| `TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |

> `TOKEN` is the only truly required variable. Without it the bot refuses to
> start and logs a clear error listing what is missing.

### Recommended (degraded mode without these)

| Variable | Default | Where to get it / notes |
|---|---|---|
| `API_SPORTS_KEY` | _(none)_ | [dashboard.api-football.com](https://dashboard.api-football.com) — live soccer fixtures |
| `DATABASE_URL` | _(none)_ | Auto-injected by Railway Postgres plugin — persistent fixture storage |

### Optional — more data sources

| Variable | Default | Description |
|---|---|---|
| `FOOTBALL_DATA_TOKEN` | _(none)_ | [football-data.org](https://www.football-data.org) token; falls back to `API_SPORTS_KEY` |
| `SPORTRADAR_API_KEY` | _(none)_ | [developer.sportradar.com](https://developer.sportradar.com) — richer NBA/NFL/MLB stats |
| `SPORTRADAR_ACCESS` | `trial` | `"trial"` or `""` (production licence) |
| `ODDS_API_KEY` | _(none)_ | [the-odds-api.com](https://the-odds-api.com) — live bookmaker odds for auto-scanner |
| `POSTGRES_URL` | _(none)_ | Alternative Postgres URL name (fallback when `DATABASE_URL` is absent) |

### Optional — Telegram

| Variable | Default | Description |
|---|---|---|
| `ALERTS_CHANNEL_ID` | _(none)_ | Channel ID (e.g. `-1001234567890`) for daily broadcast alerts |

### Optional — auto-scanner tuning

| Variable | Default | Description |
|---|---|---|
| `AUTO_SCAN_INTERVAL` | `300` | Seconds between full scan cycles (5 min). Increase on free Odds API tier. |
| `AUTO_SCAN_MIN_EV` | `5.0` | Minimum EV % to send an alert (avoids low-value noise) |
| `AUTO_SCAN_DEDUP_TTL` | `3600` | Seconds before a duplicate alert can be re-sent (1 hour) |

### Optional — parlay calibration

| Variable | Default | Description |
|---|---|---|
| `PARLAY_CAL_WINDOW` | `200` | Rolling window of recent legs used for calibration |
| `PARLAY_EWMA_DECAY` | `0.95` | EWMA decay factor (`< 1` = more recency weight; `1.0` = simple mean) |

### Optional — runtime

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `PORT` | `8080` | HTTP port for Railway health checks (Railway sets this automatically) |

---

## Step 3 — Deploy

Push a commit or click **Deploy** in Railway. On startup the bot logs:

```
TOKEN: configured ✓ (Telegram bot ready)
API_SPORTS_KEY: configured ✓ (live soccer fixtures enabled)
DATABASE_URL: configured ✓ (Postgres fixture storage enabled)
PostgreSQL: DATABASE_URL detectado — inicializando tablas…
PostgreSQL: tablas listas ✓ (fixtures, bot_subscribers, tracked_markets)
update_matches: upserted 18 fixture(s) to Postgres (total for date: 18)
🤖 Bot corriendo — 5 deportes + datos en vivo…
```

If `TOKEN` is missing the bot exits immediately with:

```
❌ Missing required environment variable(s):
  • TOKEN  (Telegram Bot API token)

Set them in Railway → Variables (or in your .env file locally).
```

If `API_SPORTS_KEY` or `DATABASE_URL` is absent you will see warning lines but
the bot still starts and operates in degraded mode (model-based predictions,
CSV storage).

---

## Local development

```bash
# 1. Clone and install
git clone https://github.com/Alterms77/Sports-engine.git
cd Sports-engine
pip install -r requirements.txt

# 2. Create your .env from the template
cp .env.example .env
# Edit .env and fill in at least TOKEN (and optionally the API keys)

# 3. Run the bot
python sports_engine/bot/bot.py
```

The `.env` file is listed in `.gitignore` — it will never be committed.

---

## How the DB prevents stale fixtures

| Scenario | Behaviour |
|---|---|
| API refresh succeeds | All fixtures (pending + finished) are upserted. Periodic job refreshes today + tomorrow every 10 min. |
| API refresh fails | DB retains fixtures from previous runs; finished ones remain marked `FT/AET/…` and are excluded from parlays. |
| `DATABASE_URL` not set | Bot falls back to CSV mode (local dev behaviour). |
| psycopg not installed | Same fallback — warning logged. |

---

## Debug CLI

Inspect the fixtures database from the command line:

```bash
# Requires DATABASE_URL in .env or environment
python sports_engine/db/fixtures_cli.py
```

