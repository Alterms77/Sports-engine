"""
Tests for the new lineup / injury fetching functions added to:
  - api.sportradar  : get_nba_injuries, get_nba_game_lineup, get_mlb_game_lineup
  - api.api_football: find_fixture_id, get_fixture_lineups, get_fixture_injuries

All external HTTP calls are mocked so tests run offline.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ════════════════════════════════════════════════════════════════════════════════
# Sportradar — get_nba_injuries
# ════════════════════════════════════════════════════════════════════════════════

class TestSportradarNbaInjuries:

    def _sr_inj_payload(self, team_name: str, players: list) -> dict:
        """Minimal Sportradar league/injuries response for one team."""
        return {
            "teams": [{
                "id": "sr:team:1",
                "name": team_name.split()[-1],
                "market": " ".join(team_name.split()[:-1]),
                "alias": team_name[:3].upper(),
                "players": [
                    {
                        "id": f"sr:player:{i}",
                        "first_name": p["name"].split()[0],
                        "last_name": " ".join(p["name"].split()[1:]),
                        "status": p["status"],
                        "primary_position": p.get("pos", "G"),
                    }
                    for i, p in enumerate(players)
                ],
            }]
        }

    def test_returns_out_players(self):
        from api.sportradar import get_nba_injuries
        payload = self._sr_inj_payload("Los Angeles Lakers", [
            {"name": "LeBron James", "status": "OUT", "pos": "SF"},
        ])
        with patch("api.sportradar.is_available", return_value=True), \
             patch("api.sportradar._fetch", return_value=payload):
            result = get_nba_injuries("Lakers")
        assert result["total"] == 1
        assert result["players"][0]["name"] == "LeBron James"

    def test_out_count_weights(self):
        from api.sportradar import get_nba_injuries
        payload = self._sr_inj_payload("Boston Celtics", [
            {"name": "Player A", "status": "OUT"},
            {"name": "Player B", "status": "DOUBTFUL"},
            {"name": "Player C", "status": "QUESTIONABLE"},
            {"name": "Player D", "status": "ACTIVE"},
        ])
        with patch("api.sportradar.is_available", return_value=True), \
             patch("api.sportradar._fetch", return_value=payload):
            result = get_nba_injuries("Celtics")
        # OUT=1.0 + DOUBTFUL=0.75 + QUESTIONABLE=0.25 = 2.0; ACTIVE excluded
        assert abs(result["out_count"] - 2.0) < 0.01

    def test_empty_when_not_available(self):
        from api.sportradar import get_nba_injuries
        with patch("api.sportradar.is_available", return_value=False):
            result = get_nba_injuries("Lakers")
        assert result == {}

    def test_empty_when_team_not_found(self):
        from api.sportradar import get_nba_injuries
        payload = self._sr_inj_payload("Golden State Warriors", [
            {"name": "Steph Curry", "status": "OUT"},
        ])
        with patch("api.sportradar.is_available", return_value=True), \
             patch("api.sportradar._fetch", return_value=payload):
            result = get_nba_injuries("Totally Unknown Team XYZ")
        assert result == {}

    def test_empty_on_fetch_failure(self):
        from api.sportradar import get_nba_injuries
        with patch("api.sportradar.is_available", return_value=True), \
             patch("api.sportradar._fetch", return_value=None):
            result = get_nba_injuries("Lakers")
        assert result == {}


# ════════════════════════════════════════════════════════════════════════════════
# Sportradar — get_nba_game_lineup
# ════════════════════════════════════════════════════════════════════════════════

class TestSportradarNbaGameLineup:

    def _boxscore(self) -> dict:
        def _player(name, starter):
            fn, *rest = name.split()
            return {
                "first_name": fn,
                "last_name": " ".join(rest),
                "starter": starter,
                "primary_position": "G",
                "statistics": {"points_game": 22.5},
            }
        return {
            "home": {
                "id": "sr:team:1", "market": "Los Angeles", "name": "Lakers",
                "players": [
                    _player("LeBron James", True),
                    _player("Anthony Davis", True),
                    _player("Bench Player", False),
                ],
            },
            "away": {
                "id": "sr:team:2", "market": "Boston", "name": "Celtics",
                "players": [_player("Jayson Tatum", True)],
            },
        }

    def test_returns_starters(self):
        from api.sportradar import get_nba_game_lineup
        with patch("api.sportradar.is_available", return_value=True), \
             patch("api.sportradar._fetch", return_value=self._boxscore()):
            result = get_nba_game_lineup("abc-game-id")
        assert len(result["home"]["starters"]) == 2
        assert result["home"]["starters"][0]["name"] == "LeBron James"

    def test_excludes_non_starters(self):
        from api.sportradar import get_nba_game_lineup
        with patch("api.sportradar.is_available", return_value=True), \
             patch("api.sportradar._fetch", return_value=self._boxscore()):
            result = get_nba_game_lineup("abc-game-id")
        names = [p["name"] for p in result["home"]["starters"]]
        assert "Bench Player" not in names

    def test_empty_when_not_available(self):
        from api.sportradar import get_nba_game_lineup
        with patch("api.sportradar.is_available", return_value=False):
            assert get_nba_game_lineup("abc") == {}

    def test_empty_on_fetch_failure(self):
        from api.sportradar import get_nba_game_lineup
        with patch("api.sportradar.is_available", return_value=True), \
             patch("api.sportradar._fetch", return_value=None):
            assert get_nba_game_lineup("abc") == {}


# ════════════════════════════════════════════════════════════════════════════════
# Sportradar — get_mlb_game_lineup
# ════════════════════════════════════════════════════════════════════════════════

class TestSportradarMlbGameLineup:

    def _lineup_payload(self) -> dict:
        def _batter(first, last, order, avg):
            return {
                "first_name": first, "last_name": last,
                "batting_order": order, "position": "CF",
                "statistics": {"hitting": {"overall": {"avg": avg}}},
            }
        return {
            "home_team": {
                "id": "sr:team:10", "market": "New York", "name": "Yankees",
                "lineup": [
                    _batter("Aaron", "Judge", 3, 0.311),
                    _batter("Gleyber", "Torres", 2, 0.257),
                ],
            },
            "away_team": {
                "id": "sr:team:11", "market": "Boston", "name": "Red Sox",
                "lineup": [_batter("Rafael", "Devers", 4, 0.302)],
            },
        }

    def test_returns_sorted_lineup(self):
        from api.sportradar import get_mlb_game_lineup
        with patch("api.sportradar.is_available", return_value=True), \
             patch("api.sportradar._fetch", return_value=self._lineup_payload()):
            result = get_mlb_game_lineup("xyz-game-id")
        orders = [p["order"] for p in result["home"]["lineup"]]
        assert orders == sorted(orders)

    def test_lineup_names_present(self):
        from api.sportradar import get_mlb_game_lineup
        with patch("api.sportradar.is_available", return_value=True), \
             patch("api.sportradar._fetch", return_value=self._lineup_payload()):
            result = get_mlb_game_lineup("xyz-game-id")
        names = [p["name"] for p in result["home"]["lineup"]]
        assert "Aaron Judge" in names

    def test_empty_when_not_available(self):
        from api.sportradar import get_mlb_game_lineup
        with patch("api.sportradar.is_available", return_value=False):
            assert get_mlb_game_lineup("xyz") == {}


# ════════════════════════════════════════════════════════════════════════════════
# api_football — find_fixture_id
# ════════════════════════════════════════════════════════════════════════════════

class TestFindFixtureId:

    def _fixtures_response(self, home: str, away: str, fid: int = 12345) -> dict:
        return {
            "response": [{
                "fixture": {"id": fid, "date": "2026-04-07T20:00:00+00:00"},
                "teams": {
                    "home": {"id": 1, "name": home},
                    "away": {"id": 2, "name": away},
                },
            }]
        }

    def test_returns_fixture_id(self):
        from api.api_football import find_fixture_id
        payload = self._fixtures_response("Arsenal", "Chelsea", fid=99001)
        with patch("api.api_football.API_SPORTS_KEY", "fake-key"), \
             patch("api.api_football._fetch", return_value=payload):
            fid = find_fixture_id("Arsenal", "Chelsea")
        assert fid == 99001

    def test_fuzzy_match(self):
        from api.api_football import find_fixture_id
        payload = self._fixtures_response("Manchester City", "Liverpool", fid=55555)
        with patch("api.api_football.API_SPORTS_KEY", "fake-key"), \
             patch("api.api_football._fetch", return_value=payload):
            fid = find_fixture_id("Man City", "Liverpool")
        # "man city" in "manchester city"
        assert fid == 55555

    def test_returns_none_when_no_match(self):
        from api.api_football import find_fixture_id
        payload = self._fixtures_response("Real Madrid", "Barcelona", fid=77777)
        with patch("api.api_football.API_SPORTS_KEY", "fake-key"), \
             patch("api.api_football._fetch", return_value=payload):
            fid = find_fixture_id("Arsenal", "Chelsea")
        assert fid is None

    def test_returns_none_without_api_key(self):
        from api.api_football import find_fixture_id
        with patch("api.api_football.API_SPORTS_KEY", ""):
            fid = find_fixture_id("Arsenal", "Chelsea")
        assert fid is None

    def test_returns_none_on_fetch_failure(self):
        from api.api_football import find_fixture_id
        with patch("api.api_football.API_SPORTS_KEY", "fake-key"), \
             patch("api.api_football._fetch", return_value=None):
            fid = find_fixture_id("Arsenal", "Chelsea")
        assert fid is None


# ════════════════════════════════════════════════════════════════════════════════
# api_football — get_fixture_lineups
# ════════════════════════════════════════════════════════════════════════════════

class TestGetFixtureLineups:

    def _lineups_response(self) -> dict:
        def _starter(name, num, pos):
            return {"player": {"name": name, "number": num, "pos": pos}}
        return {
            "response": [
                {
                    "team": {"id": 1, "name": "Arsenal"},
                    "formation": "4-3-3",
                    "startXI": [
                        _starter("Saka", 7, "F"),
                        _starter("Odegaard", 8, "M"),
                    ],
                },
                {
                    "team": {"id": 2, "name": "Chelsea"},
                    "formation": "4-2-3-1",
                    "startXI": [_starter("Palmer", 20, "M")],
                },
            ]
        }

    def test_returns_home_and_away_lineups(self):
        from api.api_football import get_fixture_lineups
        with patch("api.api_football.API_SPORTS_KEY", "fake-key"), \
             patch("api.api_football._fetch", return_value=self._lineups_response()):
            result = get_fixture_lineups(12345)
        assert result["home"]["team_name"] == "Arsenal"
        assert result["away"]["team_name"] == "Chelsea"

    def test_formation_returned(self):
        from api.api_football import get_fixture_lineups
        with patch("api.api_football.API_SPORTS_KEY", "fake-key"), \
             patch("api.api_football._fetch", return_value=self._lineups_response()):
            result = get_fixture_lineups(12345)
        assert result["home"]["formation"] == "4-3-3"

    def test_starters_listed(self):
        from api.api_football import get_fixture_lineups
        with patch("api.api_football.API_SPORTS_KEY", "fake-key"), \
             patch("api.api_football._fetch", return_value=self._lineups_response()):
            result = get_fixture_lineups(12345)
        names = [p["name"] for p in result["home"]["startXI"]]
        assert "Saka" in names

    def test_empty_without_api_key(self):
        from api.api_football import get_fixture_lineups
        with patch("api.api_football.API_SPORTS_KEY", ""):
            assert get_fixture_lineups(12345) == {}

    def test_empty_on_fetch_failure(self):
        from api.api_football import get_fixture_lineups
        with patch("api.api_football.API_SPORTS_KEY", "fake-key"), \
             patch("api.api_football._fetch", return_value=None):
            assert get_fixture_lineups(12345) == {}


# ════════════════════════════════════════════════════════════════════════════════
# api_football — get_fixture_injuries
# ════════════════════════════════════════════════════════════════════════════════

class TestGetFixtureInjuries:

    def _injuries_response(self) -> dict:
        return {
            "response": [
                {
                    "player": {"id": 1, "name": "Saka", "type": "Missing Fixture", "reason": "Hamstring"},
                    "team":   {"id": 1, "name": "Arsenal"},
                },
                {
                    "player": {"id": 2, "name": "Odegaard", "type": "Questionable", "reason": "Ankle"},
                    "team":   {"id": 1, "name": "Arsenal"},
                },
            ]
        }

    def test_returns_injury_list(self):
        from api.api_football import get_fixture_injuries
        with patch("api.api_football.API_SPORTS_KEY", "fake-key"), \
             patch("api.api_football._fetch", return_value=self._injuries_response()):
            injuries = get_fixture_injuries(12345)
        assert len(injuries) == 2

    def test_injury_fields(self):
        from api.api_football import get_fixture_injuries
        with patch("api.api_football.API_SPORTS_KEY", "fake-key"), \
             patch("api.api_football._fetch", return_value=self._injuries_response()):
            injuries = get_fixture_injuries(12345)
        saka = next(i for i in injuries if i["name"] == "Saka")
        assert saka["type"] == "Missing Fixture"
        assert saka["team"] == "Arsenal"
        assert saka["reason"] == "Hamstring"

    def test_empty_without_api_key(self):
        from api.api_football import get_fixture_injuries
        with patch("api.api_football.API_SPORTS_KEY", ""):
            assert get_fixture_injuries(12345) == []

    def test_empty_list_on_fetch_failure(self):
        from api.api_football import get_fixture_injuries
        with patch("api.api_football.API_SPORTS_KEY", "fake-key"), \
             patch("api.api_football._fetch", return_value=None):
            assert get_fixture_injuries(12345) == []
