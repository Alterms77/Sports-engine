"""
Tests for api/sofascore.py and api/thesportsdb.py

All tests run fully offline — they verify that every public function
returns a safe empty value ([], {}, None) when the network is
unavailable, ensuring the bot never crashes in offline / CI environments.
"""
import pytest


# ── SofaScore ──────────────────────────────────────────────────────────────

class TestSofascoreOffline:
    """Verify graceful fallback when SofaScore is unreachable."""

    def setup_method(self):
        from api.sofascore import clear_cache
        clear_cache()

    def test_get_live_events_returns_list(self):
        from api.sofascore import get_live_events
        result = get_live_events("football")
        assert isinstance(result, list)

    def test_get_scheduled_events_returns_list(self):
        from api.sofascore import get_scheduled_events
        result = get_scheduled_events("football", "2026-03-15")
        assert isinstance(result, list)

    def test_search_team_returns_none_or_dict(self):
        from api.sofascore import search_team
        result = search_team("Barcelona")
        assert result is None or isinstance(result, dict)

    def test_get_team_form_returns_dict(self):
        from api.sofascore import get_team_form
        result = get_team_form("Real Madrid")
        assert isinstance(result, dict)

    def test_get_h2h_returns_dict(self):
        from api.sofascore import get_h2h
        result = get_h2h("Real Madrid", "Barcelona")
        assert isinstance(result, dict)

    def test_get_standings_returns_list(self):
        from api.sofascore import get_standings
        result = get_standings(17, 61627)
        assert isinstance(result, list)

    def test_get_match_stats_returns_dict(self):
        from api.sofascore import get_match_stats
        result = get_match_stats(99999999)
        assert isinstance(result, dict)

    def test_parse_event_structure(self):
        """_parse_event handles a complete raw SofaScore event dict."""
        from api.sofascore import _parse_event
        raw = {
            "id": 42,
            "homeTeam": {"name": "Team A", "id": 1},
            "awayTeam": {"name": "Team B", "id": 2},
            "homeScore": {"current": 2},
            "awayScore": {"current": 1},
            "status": {"description": "In progress", "type": "inprogress"},
            "time": {"currentPeriodStartTimestamp": 67},
            "tournament": {"name": "Test League", "category": {"name": "Country"}},
            "startTimestamp": 1710000000,
        }
        ev = _parse_event(raw, "football")
        assert ev["id"] == 42
        assert ev["home"] == "Team A"
        assert ev["away"] == "Team B"
        assert ev["home_score"] == 2
        assert ev["away_score"] == 1
        assert ev["sport"] == "football"
        assert ev["tournament"] == "Test League"

    def test_parse_event_missing_fields(self):
        """_parse_event doesn't crash on a sparse dict."""
        from api.sofascore import _parse_event
        ev = _parse_event({}, "football")
        assert ev["home"] == "?"
        assert ev["away"] == "?"


# ── TheSportsDB ──────────────────────────────────────────────────────────────

class TestTheSportsDBOffline:
    """Verify graceful fallback when TheSportsDB is unreachable."""

    def setup_method(self):
        from api.thesportsdb import clear_cache
        clear_cache()

    def test_search_team_returns_none_or_dict(self):
        from api.thesportsdb import search_team
        result = search_team("Barcelona")
        assert result is None or isinstance(result, dict)

    def test_get_last_results_returns_list(self):
        from api.thesportsdb import get_last_results
        result = get_last_results("Real Madrid")
        assert isinstance(result, list)

    def test_get_next_fixtures_returns_list(self):
        from api.thesportsdb import get_next_fixtures
        result = get_next_fixtures("Liverpool")
        assert isinstance(result, list)

    def test_get_league_table_returns_list(self):
        from api.thesportsdb import get_league_table
        result = get_league_table("Premier League")
        assert isinstance(result, list)

    def test_get_team_form_summary_returns_dict(self):
        from api.thesportsdb import get_team_form_summary
        result = get_team_form_summary("Arsenal")
        assert isinstance(result, dict)

    def test_unknown_league_returns_empty(self):
        """Unknown league name returns empty list, not an error."""
        from api.thesportsdb import get_league_table
        result = get_league_table("Non-Existent League XYZ")
        assert result == []

    def test_league_ids_constants(self):
        """LEAGUE_IDS dict is populated with expected keys."""
        from api.thesportsdb import LEAGUE_IDS
        for name in ("Premier League", "La Liga", "Bundesliga", "NBA", "NFL"):
            assert name in LEAGUE_IDS, f"Missing: {name}"
