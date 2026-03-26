"""
PostgreSQL storage for Sports-Engine (Railway deployment).

Falls back gracefully when DATABASE_URL is absent or when psycopg is not
installed, so local / CSV-only mode continues to work unchanged.

Usage
-----
    from core.db import is_available, ensure_table, upsert_fixtures, get_upcoming_fixtures

    if is_available():
        ensure_table()
        upsert_fixtures(fixture_rows)
        upcoming = get_upcoming_fixtures(hours=24)
"""

import json as _json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status constants (mirrors update_matches.py)
# ---------------------------------------------------------------------------
NOT_STARTED_STATUSES: frozenset = frozenset({"NS", "TBD", "PST", "SUSP", "INT"})
FINISHED_STATUSES: frozenset = frozenset({"FT", "AET", "PEN", "AWD", "WO", "ABD", "CANC"})

# ---------------------------------------------------------------------------
# DDL — one statement per execute() (psycopg v3 requirement)
# ---------------------------------------------------------------------------
_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS fixtures (
        id           SERIAL PRIMARY KEY,
        provider     TEXT        NOT NULL DEFAULT 'apisports',
        fixture_id   TEXT        NOT NULL,
        sport        TEXT        NOT NULL DEFAULT 'soccer',
        league_id    INTEGER,
        league_name  TEXT,
        home_team    TEXT        NOT NULL,
        away_team    TEXT        NOT NULL,
        kickoff_utc  TIMESTAMPTZ,
        status_short TEXT        NOT NULL DEFAULT 'NS',
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_provider_fixture UNIQUE (provider, fixture_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fixtures_kickoff ON fixtures (kickoff_utc)",
    "CREATE INDEX IF NOT EXISTS idx_fixtures_status  ON fixtures (status_short)",
]

# ---------------------------------------------------------------------------
# DDL — bot_subscribers table (persists /alertas subscriptions)
# ---------------------------------------------------------------------------
_DDL_SUBSCRIBERS = [
    """
    CREATE TABLE IF NOT EXISTS bot_subscribers (
        user_id  BIGINT PRIMARY KEY,
        added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
]

# ---------------------------------------------------------------------------
# DDL — tracked_markets table (persists /addmarket entries)
# ---------------------------------------------------------------------------
_DDL_TRACKED_MARKETS = [
    """
    CREATE TABLE IF NOT EXISTS tracked_markets (
        id         SERIAL PRIMARY KEY,
        sport      TEXT        NOT NULL DEFAULT '',
        event      TEXT        NOT NULL DEFAULT '',
        market     TEXT        NOT NULL DEFAULT '',
        player     TEXT        NOT NULL DEFAULT '',
        odds_json  TEXT        NOT NULL DEFAULT '[]',
        added_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_tracked_market UNIQUE (event, market, player)
    )
    """,
]

_UPSERT_SQL = """
INSERT INTO fixtures (
    provider, fixture_id, sport, league_id, league_name,
    home_team, away_team, kickoff_utc, status_short, last_seen_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
ON CONFLICT (provider, fixture_id) DO UPDATE SET
    league_id    = EXCLUDED.league_id,
    league_name  = EXCLUDED.league_name,
    home_team    = EXCLUDED.home_team,
    away_team    = EXCLUDED.away_team,
    kickoff_utc  = EXCLUDED.kickoff_utc,
    status_short = EXCLUDED.status_short,
    last_seen_at = NOW()
"""

_UPCOMING_SQL = """
SELECT
    fixture_id, provider, sport, league_id, league_name,
    home_team, away_team, kickoff_utc, status_short
FROM fixtures
WHERE
    status_short = ANY(%s)
    AND kickoff_utc > NOW()
    AND kickoff_utc < %s
ORDER BY kickoff_utc
"""

_COUNT_UPCOMING_SQL = """
SELECT COUNT(*) FROM fixtures
WHERE status_short = ANY(%s) AND kickoff_utc > NOW()
"""

_LATEST_FINISHED_SQL = """
SELECT fixture_id, provider, home_team, away_team, league_name, kickoff_utc, status_short
FROM fixtures
WHERE status_short = ANY(%s)
ORDER BY last_seen_at DESC
LIMIT %s
"""

# ---------------------------------------------------------------------------
# Lazy psycopg availability check
# ---------------------------------------------------------------------------
_psycopg_ok: Optional[bool] = None


def _psycopg_available() -> bool:
    """Return True if psycopg (v3) can be imported."""
    global _psycopg_ok
    if _psycopg_ok is None:
        try:
            import psycopg  # noqa: F401
            _psycopg_ok = True
        except ImportError:
            _psycopg_ok = False
            logger.warning(
                "psycopg (v3) not installed — Postgres fixture storage disabled. "
                "Install with: pip install 'psycopg[binary]>=3.1'"
            )
    return _psycopg_ok


def _get_database_url() -> Optional[str]:
    """Return DATABASE_URL (or POSTGRES_URL) from the environment, or None."""
    return os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")


def is_available() -> bool:
    """Return True when both DATABASE_URL and psycopg (v3) are present."""
    return bool(_get_database_url()) and _psycopg_available()


def _connect():
    """Open and return a new psycopg v3 connection."""
    import psycopg

    url = _get_database_url()
    # psycopg v3 accepts both postgres:// and postgresql:// schemes
    if url and url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return psycopg.connect(url)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_table() -> bool:
    """Create ALL application tables and indices if they do not exist.

    Idempotent (IF NOT EXISTS) — safe to call on every startup.
    Returns True on success, False on failure.
    """
    if not is_available():
        return False
    all_stmts = _DDL_STATEMENTS + _DDL_SUBSCRIBERS + _DDL_TRACKED_MARKETS
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                for stmt in all_stmts:
                    cur.execute(stmt)
        logger.info("db.ensure_table: all tables ready (fixtures, bot_subscribers, tracked_markets)")
        return True
    except Exception as exc:
        logger.error("db.ensure_table failed: %s", exc)
        return False


def upsert_fixtures(rows: list) -> int:
    """Upsert a list of fixture dicts into the fixtures table.

    Each dict should be built with :func:`build_fixture_row`.
    Returns the number of rows processed, or -1 on error.
    """
    if not rows:
        return 0
    if not is_available():
        return 0
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        _UPSERT_SQL,
                        (
                            row["provider"],
                            row["fixture_id"],
                            row["sport"],
                            row.get("league_id"),
                            row.get("league_name"),
                            row["home_team"],
                            row["away_team"],
                            row.get("kickoff_utc"),
                            row["status_short"],
                        ),
                    )
        logger.info("db.upsert_fixtures: upserted %d fixture(s)", len(rows))
        return len(rows)
    except Exception as exc:
        logger.error("db.upsert_fixtures failed: %s", exc)
        return -1


def get_upcoming_fixtures(hours: int = 24) -> list:
    """Return upcoming fixture dicts from Postgres.

    Filters:

    * ``status_short`` in :data:`NOT_STARTED_STATUSES`
    * ``kickoff_utc > NOW()``
    * ``kickoff_utc < NOW() + hours``

    Returns an empty list on any error or when the DB is unavailable, so
    callers can fall back to CSV mode without extra error-handling.
    """
    if not is_available():
        return []
    end_time = datetime.now(timezone.utc) + timedelta(hours=hours)
    try:
        from psycopg.rows import dict_row

        with _connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(_UPCOMING_SQL, (list(NOT_STARTED_STATUSES), end_time))
                rows = cur.fetchall()
        logger.info(
            "db.get_upcoming_fixtures: %d fixture(s) in next %dh",
            len(rows),
            hours,
        )
        return rows
    except Exception as exc:
        logger.error("db.get_upcoming_fixtures failed: %s", exc)
        return []


def get_upcoming_count() -> Optional[int]:
    """Return the count of upcoming not-started fixtures, or None on error."""
    if not is_available():
        return None
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_COUNT_UPCOMING_SQL, (list(NOT_STARTED_STATUSES),))
                result = cur.fetchone()
        return result[0] if result else 0
    except Exception as exc:
        logger.error("db.get_upcoming_count failed: %s", exc)
        return None


def get_latest_finished(limit: int = 10) -> list:
    """Return the most recently seen finished fixtures.

    Returns an empty list on error or when DB is unavailable.
    """
    if not is_available():
        return []
    try:
        from psycopg.rows import dict_row

        with _connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(_LATEST_FINISHED_SQL, (list(FINISHED_STATUSES), limit))
                return cur.fetchall()
    except Exception as exc:
        logger.error("db.get_latest_finished failed: %s", exc)
        return []


def build_fixture_row(
    fixture_id,
    home: str,
    away: str,
    league_id: Optional[int],
    league_name: str,
    kickoff_utc,
    status_short: str,
    sport: str = "soccer",
    provider: str = "apisports",
) -> dict:
    """Return a dict ready to pass to :func:`upsert_fixtures`."""
    return {
        "provider":    provider,
        "fixture_id":  str(fixture_id),
        "sport":       sport,
        "league_id":   league_id,
        "league_name": league_name,
        "home_team":   home,
        "away_team":   away,
        "kickoff_utc": kickoff_utc,
        "status_short": status_short,
    }


# ---------------------------------------------------------------------------
# Bot subscribers CRUD
# ---------------------------------------------------------------------------

def get_subscribers() -> List[int]:
    """Return all subscribed chat_ids from Postgres.

    Returns an empty list when DB is unavailable or on any error, so
    callers can fall back to the JSON file without extra error-handling.
    """
    if not is_available():
        return []
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM bot_subscribers ORDER BY added_at")
                return [row[0] for row in cur.fetchall()]
    except Exception as exc:
        logger.error("db.get_subscribers failed: %s", exc)
        return []


def add_subscriber(user_id: int) -> bool:
    """Add a subscriber.  No-op if already present.  Returns True on success."""
    if not is_available():
        return False
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO bot_subscribers (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (user_id,),
                )
        return True
    except Exception as exc:
        logger.error("db.add_subscriber failed: %s", exc)
        return False


def remove_subscriber(user_id: int) -> bool:
    """Remove a subscriber.  Returns True on success (even if not found)."""
    if not is_available():
        return False
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM bot_subscribers WHERE user_id = %s",
                    (user_id,),
                )
        return True
    except Exception as exc:
        logger.error("db.remove_subscriber failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Tracked markets CRUD
# ---------------------------------------------------------------------------

def get_tracked_markets_raw() -> List[dict]:
    """Return all tracked markets as raw dicts (Postgres → JSON → odds_feed format).

    Returns an empty list on error or when DB is unavailable.
    """
    if not is_available():
        return []
    try:
        from psycopg.rows import dict_row
        with _connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT sport, event, market, player, odds_json, "
                    "       to_char(added_at, 'YYYY-MM-DD\"T\"HH24:MI:SS') AS added_at, "
                    "       to_char(updated_at, 'YYYY-MM-DD\"T\"HH24:MI:SS') AS updated_at "
                    "FROM tracked_markets ORDER BY added_at"
                )
                rows = cur.fetchall()
        result = []
        for row in rows:
            try:
                odds = _json.loads(row["odds_json"])
            except Exception:
                odds = []
            result.append({
                "sport":      row["sport"],
                "event":      row["event"],
                "market":     row["market"],
                "player":     row["player"],
                "odds":       odds,
                "added_at":   row["added_at"],
                "updated_at": row["updated_at"],
            })
        return result
    except Exception as exc:
        logger.error("db.get_tracked_markets_raw failed: %s", exc)
        return []


def save_tracked_market(
    sport: str,
    event: str,
    market: str,
    player: str,
    odds: list,
) -> bool:
    """Upsert a tracked market.  Returns True on success."""
    if not is_available():
        return False
    try:
        odds_json = _json.dumps(odds, ensure_ascii=False)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tracked_markets (sport, event, market, player, odds_json, updated_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (event, market, player) DO UPDATE SET
                        sport      = EXCLUDED.sport,
                        odds_json  = EXCLUDED.odds_json,
                        updated_at = NOW()
                    """,
                    (sport, event, market, player, odds_json),
                )
        return True
    except Exception as exc:
        logger.error("db.save_tracked_market failed: %s", exc)
        return False


def remove_tracked_market(event: str, market: str, player: str = "") -> bool:
    """Remove a specific tracked market.  Returns True on success."""
    if not is_available():
        return False
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM tracked_markets WHERE event = %s AND market = %s AND player = %s",
                    (event, market, player),
                )
        return True
    except Exception as exc:
        logger.error("db.remove_tracked_market failed: %s", exc)
        return False


def clear_tracked_markets() -> int:
    """Remove ALL tracked markets.  Returns count removed, or -1 on error."""
    if not is_available():
        return -1
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM tracked_markets")
                count = cur.fetchone()[0]
                cur.execute("DELETE FROM tracked_markets")
        return count
    except Exception as exc:
        logger.error("db.clear_tracked_markets failed: %s", exc)
        return -1
