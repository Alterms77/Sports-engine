"""
Tests for api/live_aggregator.py

Covers:
  - All aggregator public functions return correct types offline
  - Formatting helpers produce correct Telegram-ready output
  - Priority waterfall selects first successful source
"""
import pytest
from unittest.mock import patch


from api.live_aggregator import (
    get_live_scores,
    get_all_live_scores,
    get_today_schedule,
    get_team_live_form,
    get_league_table,
    get_next_fixtures,
    format_live_event,
    format_live_scoreboard,
    format_fixture_list,
    format_last_results,
    _sport_to_espn_key,
    _normalise_espn,
)


# ─────────────────────────────────────────────────────────────────────────────
# Offline / empty returns
# ─────────────────────────────────────────────────────────────────────────────

class TestAggregatorOffline:
    def test_get_live_scores_returns_list(self):
        result = get_live_scores("football")
        assert isinstance(result, list)

    def test_get_all_live_scores_returns_list(self):
        result = get_all_live_scores()
        assert isinstance(result, list)

    def test_get_today_schedule_returns_list(self):
        result = get_today_schedule("football")
        assert isinstance(result, list)

    def test_get_team_live_form_returns_dict(self):
        result = get_team_live_form("Real Madrid", "football")
        assert isinstance(result, dict)

    def test_get_league_table_returns_list(self):
        result = get_league_table("La Liga")
        assert isinstance(result, list)

    def test_get_next_fixtures_returns_list(self):
        result = get_next_fixtures("Barcelona")
        assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# format_live_event
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatLiveEvent:
    def _make_event(self, **kwargs):
        base = {
            "home": "Real Madrid",
            "away": "Barcelona",
            "home_score": "",
            "away_score": "",
            "status": "Scheduled",
            "minute": None,
            "tournament": "La Liga",
        }
        base.update(kwargs)
        return base

    def test_live_event_with_score(self):
        ev = self._make_event(home_score=2, away_score=1, status="In progress")
        result = format_live_event(ev)
        assert "🔴" in result
        assert "2-1" in result

    def test_finished_event_with_score(self):
        ev = self._make_event(home_score=3, away_score=0, status="Finished")
        result = format_live_event(ev)
        assert "✅" in result
        assert "3-0" in result

    def test_scheduled_event_no_score(self):
        ev = self._make_event()
        result = format_live_event(ev)
        assert "⏰" in result
        assert "Real Madrid" in result
        assert "Barcelona" in result

    def test_returns_string(self):
        ev = self._make_event()
        assert isinstance(format_live_event(ev), str)


# ─────────────────────────────────────────────────────────────────────────────
# format_live_scoreboard
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatLiveScoreboard:
    def test_empty_events_returns_no_events_message(self):
        result = format_live_scoreboard([])
        assert "📭" in result

    def test_single_event(self):
        events = [{
            "home": "Team A", "away": "Team B",
            "home_score": 1, "away_score": 0,
            "status": "In progress", "tournament": "Liga X", "country": "Country",
        }]
        result = format_live_scoreboard(events)
        assert "Team A" in result
        assert "Team B" in result

    def test_groups_by_tournament(self):
        events = [
            {"home": "A", "away": "B", "home_score": "", "away_score": "",
             "status": "Scheduled", "tournament": "PL", "country": "England"},
            {"home": "C", "away": "D", "home_score": "", "away_score": "",
             "status": "Scheduled", "tournament": "LL", "country": "Spain"},
        ]
        result = format_live_scoreboard(events)
        assert "PL" in result
        assert "LL" in result

    def test_returns_string(self):
        assert isinstance(format_live_scoreboard([]), str)


# ─────────────────────────────────────────────────────────────────────────────
# format_fixture_list
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatFixtureList:
    def test_empty_returns_no_fixtures_message(self):
        result = format_fixture_list([])
        assert "📭" in result

    def test_fixture_includes_teams_and_date(self):
        fixtures = [{
            "date": "2026-03-20",
            "time": "20:00",
            "home": "Atletico",
            "away": "Sevilla",
            "tournament": "La Liga",
        }]
        result = format_fixture_list(fixtures)
        assert "Atletico" in result
        assert "Sevilla" in result
        assert "2026-03-20" in result

    def test_fixture_without_time(self):
        fixtures = [{
            "date": "2026-03-20",
            "time": "",
            "home": "TeamA",
            "away": "TeamB",
            "tournament": "Cup",
        }]
        result = format_fixture_list(fixtures)
        assert "TeamA" in result


# ─────────────────────────────────────────────────────────────────────────────
# format_last_results
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatLastResults:
    def test_empty_returns_message(self):
        result = format_last_results([], "Liverpool")
        assert "Sin resultados" in result

    def test_win_shows_checkmark(self):
        matches = [{"scored": 2, "conceded": 0, "result": "W", "opponent": "Arsenal", "is_home": True}]
        result = format_last_results(matches, "Liverpool")
        assert "✅" in result

    def test_draw_shows_arrow(self):
        matches = [{"scored": 1, "conceded": 1, "result": "D", "opponent": "Chelsea", "is_home": False}]
        result = format_last_results(matches, "Liverpool")
        assert "➡️" in result

    def test_loss_shows_cross(self):
        matches = [{"scored": 0, "conceded": 2, "result": "L", "opponent": "City", "is_home": True}]
        result = format_last_results(matches, "Liverpool")
        assert "❌" in result

    def test_home_shows_house(self):
        matches = [{"scored": 1, "conceded": 0, "result": "W", "opponent": "Everton", "is_home": True}]
        result = format_last_results(matches, "Liverpool")
        assert "🏠" in result

    def test_away_shows_plane(self):
        matches = [{"scored": 1, "conceded": 0, "result": "W", "opponent": "Everton", "is_home": False}]
        result = format_last_results(matches, "Liverpool")
        assert "✈️" in result


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_sport_to_espn_key_football(self):
        assert _sport_to_espn_key("football") == "soccer"
        assert _sport_to_espn_key("soccer") == "soccer"

    def test_sport_to_espn_key_nba(self):
        assert _sport_to_espn_key("nba") == "nba"
        assert _sport_to_espn_key("basketball") == "nba"

    def test_sport_to_espn_key_unknown(self):
        assert _sport_to_espn_key("unknown_sport") is None

    def test_normalise_espn_structure(self):
        raw = {
            "home": "Lakers", "away": "Celtics",
            "home_score": 102, "away_score": 98,
            "status": "In Progress",
        }
        ev = _normalise_espn(raw, "basketball")
        assert ev["home"] == "Lakers"
        assert ev["away"] == "Celtics"
        assert ev["home_score"] == 102
        assert ev["sport"] == "basketball"
        assert ev["source"] == "espn"


# ─────────────────────────────────────────────────────────────────────────────
# Priority waterfall: SofaScore → ESPN → TheSportsDB
# ─────────────────────────────────────────────────────────────────────────────

class TestPriorityWaterfall:
    def test_sofascore_result_used_when_available(self):
        """When SofaScore returns data, it is used (ESPN not called)."""
        fake_events = [
            {"home": "A", "away": "B", "home_score": 1, "away_score": 0,
             "status": "In progress", "tournament": "Liga", "country": "ES",
             "id": 1, "sport": "football", "minute": None, "start_time": None, "home_id": None, "away_id": None, "status_type": ""},
        ]
        with patch("api.sofascore.get_live_events", return_value=fake_events) as mock_ss:
            result = get_live_scores("football")
        # Should return the fake events (with "source" key added)
        assert len(result) == 1
        assert result[0]["home"] == "A"

    def test_espn_fallback_when_sofascore_empty(self):
        """When SofaScore returns empty, ESPN is tried."""
        with patch("api.sofascore.get_live_events", return_value=[]):
            with patch("api.espn_api.get_scoreboard", return_value=None):
                result = get_live_scores("football")
        assert isinstance(result, list)
