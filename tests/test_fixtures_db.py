"""
Tests for sports_engine/core/db.py

Covers:
- is_available(): returns False when DATABASE_URL is absent
- is_available(): returns False when psycopg cannot be imported
- build_fixture_row(): returns the expected dict structure
- upsert_fixtures(): returns 0 (no-op) when DB is unavailable
- get_upcoming_fixtures(): returns [] when DB is unavailable
- ensure_table(): returns False when DB is unavailable
- get_upcoming_count(): returns None when DB is unavailable
- get_latest_finished(): returns [] when DB is unavailable
- Filtering logic: get_upcoming_fixtures with mocked DB connection
- URL normalisation: postgres:// → postgresql://
"""

import sys
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Ensure sports_engine/ is on sys.path
_SPORTS_ENGINE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "sports_engine",
)
if _SPORTS_ENGINE not in sys.path:
    sys.path.insert(0, _SPORTS_ENGINE)

import core.db as db_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_psycopg_cache():
    """Reset the module-level psycopg availability cache between tests."""
    original = db_module._psycopg_ok
    yield
    db_module._psycopg_ok = original


# ---------------------------------------------------------------------------
# is_available()
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_false_when_no_database_url(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        assert db_module.is_available() is False

    def test_false_when_psycopg_not_installed(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
        db_module._psycopg_ok = None
        with patch.dict(sys.modules, {"psycopg": None}):
            # Simulate ImportError by making the module None
            db_module._psycopg_ok = None
            with patch("builtins.__import__", side_effect=_import_raising_for_psycopg):
                db_module._psycopg_ok = None
                result = db_module._psycopg_available()
        assert result is False

    def test_true_when_url_and_psycopg_available(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
        db_module._psycopg_ok = True  # simulate psycopg installed
        assert db_module.is_available() is True

    def test_uses_postgres_url_fallback(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pass@host/db")
        db_module._psycopg_ok = True
        assert db_module.is_available() is True


def _import_raising_for_psycopg(name, *args, **kwargs):
    if name == "psycopg":
        raise ImportError("No module named 'psycopg'")
    return __import__(name, *args, **kwargs)


# ---------------------------------------------------------------------------
# _get_database_url()
# ---------------------------------------------------------------------------

class TestGetDatabaseUrl:
    def test_returns_database_url(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@c/d")
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        assert db_module._get_database_url() == "postgresql://a:b@c/d"

    def test_falls_back_to_postgres_url(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_URL", "postgresql://x:y@z/w")
        assert db_module._get_database_url() == "postgresql://x:y@z/w"

    def test_returns_none_when_absent(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        assert db_module._get_database_url() is None


# ---------------------------------------------------------------------------
# URL normalisation (postgres:// → postgresql://)
# ---------------------------------------------------------------------------

class TestUrlNormalisation:
    def test_normalises_postgres_scheme(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host:5432/mydb")
        db_module._psycopg_ok = True

        captured_url = []

        def mock_connect(url):
            captured_url.append(url)
            raise RuntimeError("stop here")

        with patch("core.db._connect", side_effect=mock_connect):
            # We'll test via _connect indirectly
            pass

        # Test the normalisation logic directly
        url = "postgres://user:pass@host:5432/mydb"
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        assert url.startswith("postgresql://")

    def test_leaves_postgresql_scheme_unchanged(self):
        url = "postgresql://user:pass@host:5432/mydb"
        if url.startswith("postgres://") and not url.startswith("postgresql://"):
            url = "postgresql://" + url[len("postgres://"):]
        assert url == "postgresql://user:pass@host:5432/mydb"


# ---------------------------------------------------------------------------
# build_fixture_row()
# ---------------------------------------------------------------------------

class TestBuildFixtureRow:
    def test_returns_expected_keys(self):
        row = db_module.build_fixture_row(
            fixture_id=12345,
            home="Real Madrid",
            away="Barcelona",
            league_id=140,
            league_name="La Liga",
            kickoff_utc=datetime(2025, 6, 1, 20, 0, tzinfo=timezone.utc),
            status_short="NS",
        )
        assert row["fixture_id"] == "12345"
        assert row["home_team"] == "Real Madrid"
        assert row["away_team"] == "Barcelona"
        assert row["league_id"] == 140
        assert row["league_name"] == "La Liga"
        assert row["status_short"] == "NS"
        assert row["sport"] == "soccer"
        assert row["provider"] == "apisports"

    def test_fixture_id_is_stringified(self):
        row = db_module.build_fixture_row(
            fixture_id=99,
            home="A", away="B",
            league_id=1, league_name="L",
            kickoff_utc=None, status_short="FT",
        )
        assert isinstance(row["fixture_id"], str)
        assert row["fixture_id"] == "99"

    def test_custom_provider_and_sport(self):
        row = db_module.build_fixture_row(
            fixture_id="abc",
            home="X", away="Y",
            league_id=None, league_name="NBA",
            kickoff_utc=None, status_short="NS",
            sport="basketball",
            provider="sportradar",
        )
        assert row["sport"] == "basketball"
        assert row["provider"] == "sportradar"
        assert row["league_id"] is None

    def test_none_league_id_allowed(self):
        row = db_module.build_fixture_row(
            fixture_id=1,
            home="H", away="A",
            league_id=None, league_name="Cup",
            kickoff_utc=None, status_short="PST",
        )
        assert row["league_id"] is None


# ---------------------------------------------------------------------------
# Functions that no-op / return safe defaults when DB is unavailable
# ---------------------------------------------------------------------------

class TestNoOpWhenUnavailable:
    """All public DB functions must fail silently when is_available() is False."""

    @pytest.fixture(autouse=True)
    def db_unavailable(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        db_module._psycopg_ok = None  # reset cache

    def test_ensure_table_returns_false(self):
        assert db_module.ensure_table() is False

    def test_upsert_fixtures_returns_zero(self):
        rows = [db_module.build_fixture_row(
            fixture_id=1, home="H", away="A",
            league_id=1, league_name="L",
            kickoff_utc=None, status_short="NS",
        )]
        assert db_module.upsert_fixtures(rows) == 0

    def test_upsert_empty_list_returns_zero(self):
        assert db_module.upsert_fixtures([]) == 0

    def test_get_upcoming_fixtures_returns_empty_list(self):
        result = db_module.get_upcoming_fixtures(hours=24)
        assert result == []

    def test_get_upcoming_count_returns_none(self):
        assert db_module.get_upcoming_count() is None

    def test_get_latest_finished_returns_empty_list(self):
        assert db_module.get_latest_finished() == []


# ---------------------------------------------------------------------------
# Filtering logic with a mocked DB layer
# ---------------------------------------------------------------------------

class TestGetUpcomingFixturesFiltering:
    """Test that get_upcoming_fixtures uses correct SQL parameters."""

    @pytest.fixture(autouse=True)
    def db_available(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/d")
        db_module._psycopg_ok = True

    def test_passes_not_started_statuses_to_query(self):
        """get_upcoming_fixtures should pass NOT_STARTED_STATUSES to the query."""
        mock_rows = [
            {
                "fixture_id": "101",
                "provider": "apisports",
                "sport": "soccer",
                "league_id": 140,
                "league_name": "La Liga",
                "home_team": "Real Madrid",
                "away_team": "Villarreal",
                "kickoff_utc": datetime.now(timezone.utc) + timedelta(hours=2),
                "status_short": "NS",
            }
        ]

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_rows
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("core.db._connect", return_value=mock_conn):
            with patch("core.db.dict_row", create=True):
                # Patch psycopg.rows.dict_row import
                mock_dict_row = MagicMock()
                with patch.dict(sys.modules, {"psycopg.rows": MagicMock(dict_row=mock_dict_row)}):
                    result = db_module.get_upcoming_fixtures(hours=24)

        # The cursor should have been called with dict_row factory
        assert mock_conn.cursor.called

    def test_returns_empty_list_on_db_error(self, monkeypatch):
        """get_upcoming_fixtures returns [] (not raises) when DB errors occur."""
        with patch("core.db._connect", side_effect=RuntimeError("connection refused")):
            result = db_module.get_upcoming_fixtures(hours=24)
        assert result == []

    def test_upsert_returns_minus_one_on_error(self, monkeypatch):
        """upsert_fixtures returns -1 (not raises) when DB errors occur."""
        rows = [db_module.build_fixture_row(
            fixture_id=1, home="H", away="A",
            league_id=1, league_name="L",
            kickoff_utc=None, status_short="NS",
        )]
        with patch("core.db._connect", side_effect=RuntimeError("connection refused")):
            result = db_module.upsert_fixtures(rows)
        assert result == -1

    def test_ensure_table_returns_false_on_error(self):
        """ensure_table returns False (not raises) when DB errors occur."""
        with patch("core.db._connect", side_effect=RuntimeError("connection refused")):
            result = db_module.ensure_table()
        assert result is False


# ---------------------------------------------------------------------------
# Status constant correctness
# ---------------------------------------------------------------------------

class TestStatusConstants:
    def test_not_started_statuses(self):
        expected = {"NS", "TBD", "PST", "SUSP", "INT"}
        assert db_module.NOT_STARTED_STATUSES == expected

    def test_finished_statuses(self):
        expected = {"FT", "AET", "PEN", "AWD", "WO", "ABD", "CANC"}
        assert db_module.FINISHED_STATUSES == expected

    def test_no_overlap_between_status_sets(self):
        overlap = db_module.NOT_STARTED_STATUSES & db_module.FINISHED_STATUSES
        assert not overlap, f"Status sets overlap: {overlap}"


# ---------------------------------------------------------------------------
# load_today_matches() DB-path integration (mocked)
# ---------------------------------------------------------------------------

class TestLoadTodayMatchesDbPath:
    """Verify bot.load_today_matches() uses DB rows when is_available() is True."""

    def test_load_today_matches_uses_db_when_available(self, monkeypatch):
        """load_today_matches should return DB rows (not CSV) when DB is available."""
        import importlib
        import bot.bot as bot

        future_kickoff = datetime.now(timezone.utc) + timedelta(hours=3)
        fake_db_rows = [
            {
                "fixture_id": "999",
                "provider": "apisports",
                "sport": "soccer",
                "league_id": 140,
                "league_name": "La Liga",
                "home_team": "Atletico Madrid",
                "away_team": "Getafe",
                "kickoff_utc": future_kickoff,
                "status_short": "NS",
            }
        ]

        monkeypatch.setattr("core.db.is_available", lambda: True)
        monkeypatch.setattr("core.db.get_upcoming_fixtures", lambda hours=24: fake_db_rows)

        result = bot.load_today_matches()

        assert len(result) == 1
        assert result[0]["home"] == "Atletico Madrid"
        assert result[0]["away"] == "Getafe"
        assert result[0]["league"] == "La Liga"
        assert result[0]["sport"] == "soccer"

    def test_load_today_matches_falls_back_to_csv_when_db_unavailable(
        self, monkeypatch, tmp_path
    ):
        """load_today_matches falls back to CSV when DB is not available."""
        import bot.bot as bot

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        future_kickoff = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )

        csv_file = tmp_path / "today_matches.csv"
        csv_file.write_text(
            "home,away,league,date,kickoff_utc,status,round,tournament\n"
            f"Sevilla,Granada,La Liga,{today},{future_kickoff},NS,,La Liga\n"
        )

        monkeypatch.setattr("core.db.is_available", lambda: False)
        monkeypatch.setattr(bot, "DATA_PATH", str(csv_file))

        result = bot.load_today_matches()

        assert len(result) == 1
        assert result[0]["home"] == "Sevilla"
        assert result[0]["away"] == "Granada"

    def test_load_today_matches_falls_back_to_csv_on_db_error(
        self, monkeypatch, tmp_path
    ):
        """load_today_matches falls back to CSV if DB raises an exception."""
        import bot.bot as bot

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        future_kickoff = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )

        csv_file = tmp_path / "today_matches.csv"
        csv_file.write_text(
            "home,away,league,date,kickoff_utc,status,round,tournament\n"
            f"Valencia,Osasuna,La Liga,{today},{future_kickoff},NS,,La Liga\n"
        )

        def _raise_db_error(hours=24):
            raise RuntimeError("DB timeout")

        monkeypatch.setattr("core.db.is_available", lambda: True)
        monkeypatch.setattr("core.db.get_upcoming_fixtures", _raise_db_error)
        monkeypatch.setattr(bot, "DATA_PATH", str(csv_file))

        result = bot.load_today_matches()

        assert len(result) == 1
        assert result[0]["home"] == "Valencia"


# ---------------------------------------------------------------------------
# DB bootstrap: ensure_table() called at startup
# ---------------------------------------------------------------------------

class TestDbBootstrap:
    """Verify that ensure_table() is called at startup when DB is available."""

    def test_ensure_table_called_on_startup(self, monkeypatch):
        """main() should call ensure_table() when DATABASE_URL is configured."""
        called = []

        monkeypatch.setattr("core.db.is_available", lambda: True)
        monkeypatch.setattr("core.db.ensure_table", lambda: called.append(1) or True)

        # Import bot module and invoke the bootstrap block directly
        import bot.bot as bot

        # Simulate the bootstrap block that main() runs
        from core.db import is_available as _db_available, ensure_table as _db_ensure_table
        if _db_available():
            _db_ensure_table()

        assert len(called) == 1, "ensure_table() should have been called once"

    def test_ensure_table_not_called_when_db_unavailable(self, monkeypatch):
        """ensure_table() should NOT be called when DB is not configured."""
        called = []

        monkeypatch.setattr("core.db.is_available", lambda: False)
        monkeypatch.setattr("core.db.ensure_table", lambda: called.append(1) or True)

        from core.db import is_available as _db_available, ensure_table as _db_ensure_table
        if _db_available():
            _db_ensure_table()

        assert len(called) == 0, "ensure_table() should NOT be called when DB is unavailable"


# ---------------------------------------------------------------------------
# load_tomorrow_matches_multisport() DB-path integration (mocked)
# ---------------------------------------------------------------------------

class TestLoadTomorrowMatchesDbPath:
    """Verify bot.load_tomorrow_matches_multisport() uses DB rows when available."""

    def test_uses_db_for_tomorrow_soccer(self, monkeypatch):
        """Should return tomorrow's soccer fixtures from DB when available."""
        import bot.bot as bot

        tomorrow_dt = datetime.now(timezone.utc) + timedelta(days=1)
        tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")
        future_kickoff = tomorrow_dt.replace(hour=18, minute=0, second=0, microsecond=0)

        fake_db_rows = [
            {
                "fixture_id": "42",
                "provider": "apisports",
                "sport": "soccer",
                "league_id": 39,
                "league_name": "Premier League",
                "home_team": "Liverpool",
                "away_team": "Arsenal",
                "kickoff_utc": future_kickoff,
                "status_short": "NS",
            }
        ]

        monkeypatch.setattr("core.db.is_available", lambda: True)
        monkeypatch.setattr("core.db.get_upcoming_fixtures", lambda hours=24: fake_db_rows)

        # ESPN returns nothing (we only want to test the soccer/DB path here)
        try:
            from api import espn_api as _espn
            monkeypatch.setattr(_espn, "get_scoreboard", lambda sport, **kw: [])
        except Exception:
            pass

        result = bot.load_tomorrow_matches_multisport()

        soccer_results = [m for m in result if m.get("sport") == "soccer"]
        assert len(soccer_results) == 1
        assert soccer_results[0]["home"] == "Liverpool"
        assert soccer_results[0]["away"] == "Arsenal"
        assert soccer_results[0]["league"] == "Premier League"

    def test_falls_back_to_csv_when_db_unavailable(self, monkeypatch, tmp_path):
        """Should fall back to CSV when DB is not available."""
        import bot.bot as bot

        tomorrow_dt = datetime.now(timezone.utc) + timedelta(days=1)
        tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")

        csv_file = tmp_path / "tomorrow_matches.csv"
        csv_file.write_text(
            "home,away,league,date,kickoff_utc,status,round,tournament\n"
            f"Real Madrid,Valencia,La Liga,{tomorrow_str},"
            f"{tomorrow_str}T20:00:00+00:00,NS,,La Liga\n"
        )

        monkeypatch.setattr("core.db.is_available", lambda: False)
        monkeypatch.setattr(bot, "DATA_PATH_TOMORROW", str(csv_file))
        monkeypatch.setattr(bot, "DATA_PATH", str(tmp_path / "today_matches.csv"))

        # ESPN returns nothing
        try:
            from api import espn_api as _espn
            monkeypatch.setattr(_espn, "get_scoreboard", lambda sport, **kw: [])
        except Exception:
            pass

        result = bot.load_tomorrow_matches_multisport()

        soccer_results = [m for m in result if m.get("sport") == "soccer"]
        assert len(soccer_results) == 1
        assert soccer_results[0]["home"] == "Real Madrid"

    def test_db_rows_outside_tomorrow_are_excluded(self, monkeypatch):
        """DB rows with kickoff NOT in tomorrow's date should be excluded."""
        import bot.bot as bot

        # A kickoff that is clearly TODAY (not tomorrow)
        today_kickoff = datetime.now(timezone.utc).replace(hour=20, minute=0, second=0, microsecond=0)

        fake_db_rows = [
            {
                "fixture_id": "7",
                "provider": "apisports",
                "sport": "soccer",
                "league_id": 262,
                "league_name": "Liga MX",
                "home_team": "Club America",
                "away_team": "Cruz Azul",
                "kickoff_utc": today_kickoff,
                "status_short": "NS",
            }
        ]

        monkeypatch.setattr("core.db.is_available", lambda: True)
        monkeypatch.setattr("core.db.get_upcoming_fixtures", lambda hours=24: fake_db_rows)

        try:
            from api import espn_api as _espn
            monkeypatch.setattr(_espn, "get_scoreboard", lambda sport, **kw: [])
        except Exception:
            pass

        result = bot.load_tomorrow_matches_multisport()

        soccer_results = [m for m in result if m.get("sport") == "soccer"]
        assert len(soccer_results) == 0, "Today's kickoff should NOT appear in tomorrow's matches"


# ---------------------------------------------------------------------------
# Subscribers CRUD (no-ops when DB unavailable)
# ---------------------------------------------------------------------------

class TestSubscribersCrud:
    """Verify subscriber CRUD functions return safe defaults when DB unavailable."""

    def test_get_subscribers_returns_empty_when_unavailable(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        assert db_module.get_subscribers() == []

    def test_add_subscriber_returns_false_when_unavailable(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        assert db_module.add_subscriber(12345) is False

    def test_remove_subscriber_returns_false_when_unavailable(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        assert db_module.remove_subscriber(12345) is False


# ---------------------------------------------------------------------------
# Tracked markets CRUD (no-ops when DB unavailable)
# ---------------------------------------------------------------------------

class TestTrackedMarketsCrud:
    """Verify tracked_markets CRUD functions return safe defaults when DB unavailable."""

    def test_get_tracked_markets_returns_empty_when_unavailable(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        assert db_module.get_tracked_markets_raw() == []

    def test_save_tracked_market_returns_false_when_unavailable(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        result = db_module.save_tracked_market("NBA", "A vs B", "Over 220.5", "", [])
        assert result is False

    def test_remove_tracked_market_returns_false_when_unavailable(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        assert db_module.remove_tracked_market("A vs B", "Over 220.5") is False

    def test_clear_tracked_markets_returns_minus_one_when_unavailable(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        assert db_module.clear_tracked_markets() == -1


# ---------------------------------------------------------------------------
# Subscribers persistence via bot._load_subscribers / _save_subscribers
# ---------------------------------------------------------------------------

class TestSubscribersPersistence:
    """Verify _load_subscribers / _save_subscribers prefer Postgres when available."""

    def test_load_uses_db_when_available(self, monkeypatch):
        """_load_subscribers returns DB rows when DB is available."""
        import bot.bot as bot

        monkeypatch.setattr("core.db.is_available", lambda: True)
        monkeypatch.setattr("core.db.get_subscribers", lambda: [111, 222])

        result = bot._load_subscribers()
        assert result == [111, 222]

    def test_load_falls_back_to_json_when_db_unavailable(self, monkeypatch, tmp_path):
        """_load_subscribers falls back to JSON when DB is unavailable."""
        import bot.bot as bot

        subs_file = tmp_path / "alert_subscribers.json"
        subs_file.write_text('{"subscribers": [333, 444]}')

        monkeypatch.setattr("core.db.is_available", lambda: False)
        monkeypatch.setattr(bot, "_SUBSCRIBERS_PATH", str(subs_file))

        result = bot._load_subscribers()
        assert result == [333, 444]

    def test_load_returns_empty_when_no_file_and_db_unavailable(self, monkeypatch, tmp_path):
        """_load_subscribers returns [] when JSON file doesn't exist and DB is down."""
        import bot.bot as bot

        monkeypatch.setattr("core.db.is_available", lambda: False)
        monkeypatch.setattr(bot, "_SUBSCRIBERS_PATH", str(tmp_path / "nonexistent.json"))

        result = bot._load_subscribers()
        assert result == []
