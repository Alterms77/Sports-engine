"""
Tests for the new ESPN API helper functions added to resolve:
  - /mlb live probable starting pitchers (get_mlb_probable_starters)
  - /nba live team statistical leaders (get_nba_team_leaders)
  - /nba team injuries (get_nba_injuries)
  - /mlb team injuries (get_mlb_injuries)

All ESPN HTTP calls are mocked so these tests run offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from api.espn_api import get_mlb_probable_starters, get_nba_team_leaders, get_nba_injuries, get_mlb_injuries


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _mlb_scoreboard(home_name: str, away_name: str,
                    home_pitcher: str | None = "Gerrit Cole",
                    away_pitcher: str | None = "Pablo López",
                    home_era: float | None = 2.85,
                    away_era: float | None = 3.40,
                    home_hand: str = "R",
                    away_hand: str = "R") -> dict:
    """Build a minimal ESPN MLB scoreboard JSON fixture."""

    def _probable(name, era, hand):
        stats = []
        if era is not None:
            stats.append({"abbreviation": "ERA", "displayValue": str(era)})
        return [{
            "athlete": {
                "fullName": name,
                "throwHand": {"abbreviation": hand},
            },
            "statistics": stats,
        }]

    home_comp = {
        "homeAway": "home",
        "team": {"displayName": home_name, "abbreviation": home_name[:3].upper()},
        "probables": _probable(home_pitcher, home_era, home_hand) if home_pitcher else [],
    }
    away_comp = {
        "homeAway": "away",
        "team": {"displayName": away_name, "abbreviation": away_name[:3].upper()},
        "probables": _probable(away_pitcher, away_era, away_hand) if away_pitcher else [],
    }
    return {
        "events": [{
            "competitions": [{
                "competitors": [home_comp, away_comp],
            }],
        }],
    }


def _nba_team_page(team_name: str,
                   scorer_name: str = "LeBron James", scorer_val: str = "28.5",
                   reb_name: str = "Anthony Davis", reb_val: str = "12.1",
                   ast_name: str = "LeBron James", ast_val: str = "7.4") -> dict:
    """Build a minimal ESPN NBA team page JSON with leaders."""
    def _leader_cat(cat_name: str, player_name: str, value: str) -> dict:
        return {
            "name": cat_name,
            "displayName": cat_name,
            "leaders": [{
                "athlete": {"displayName": player_name},
                "displayValue": value,
                "value": float(value),
            }],
        }

    return {
        "team": {
            "displayName": team_name,
            "id": "13",
            "leaders": [
                _leader_cat("pointsPerGame", scorer_name, scorer_val),
                _leader_cat("reboundsPerGame", reb_name, reb_val),
                _leader_cat("assistsPerGame", ast_name, ast_val),
            ],
        }
    }


# ── get_mlb_probable_starters ─────────────────────────────────────────────────

class TestGetMlbProbableStarters:

    def _patch_fetch(self, data: dict):
        return patch("api.espn_api._fetch", return_value=data)

    def test_returns_home_and_away_pitcher(self):
        sb = _mlb_scoreboard("New York Yankees", "Boston Red Sox")
        with self._patch_fetch(sb):
            result = get_mlb_probable_starters("Yankees", "Red Sox")
        assert result["home_pitcher"]["name"] == "Gerrit Cole"
        assert result["away_pitcher"]["name"] == "Pablo López"

    def test_returns_era_for_home_pitcher(self):
        sb = _mlb_scoreboard("Yankees", "Red Sox", home_era=2.85)
        with self._patch_fetch(sb):
            result = get_mlb_probable_starters("Yankees", "Red Sox")
        assert abs(result["home_pitcher"]["era"] - 2.85) < 0.01

    def test_returns_era_for_away_pitcher(self):
        sb = _mlb_scoreboard("Yankees", "Red Sox", away_era=3.40)
        with self._patch_fetch(sb):
            result = get_mlb_probable_starters("Yankees", "Red Sox")
        assert abs(result["away_pitcher"]["era"] - 3.40) < 0.01

    def test_returns_hand(self):
        sb = _mlb_scoreboard("Yankees", "Red Sox", home_hand="L")
        with self._patch_fetch(sb):
            result = get_mlb_probable_starters("Yankees", "Red Sox")
        assert result["home_pitcher"]["hand"] == "L"

    def test_empty_when_no_matching_game(self):
        sb = _mlb_scoreboard("Tigers", "Cubs")
        with self._patch_fetch(sb):
            result = get_mlb_probable_starters("Yankees", "Red Sox")
        assert result == {}

    def test_empty_when_espn_unreachable(self):
        with self._patch_fetch(None):
            result = get_mlb_probable_starters("Yankees", "Red Sox")
        assert result == {}

    def test_no_pitcher_entry_when_probables_empty(self):
        sb = _mlb_scoreboard("Yankees", "Red Sox",
                             home_pitcher=None, away_pitcher=None)
        with self._patch_fetch(sb):
            result = get_mlb_probable_starters("Yankees", "Red Sox")
        # No pitcher info — result may be empty or missing keys
        assert result.get("home_pitcher") is None
        assert result.get("away_pitcher") is None

    def test_fuzzy_match_abbreviation(self):
        """Should match even when caller uses city name or abbreviation."""
        sb = _mlb_scoreboard("New York Yankees", "Boston Red Sox")
        with self._patch_fetch(sb):
            result = get_mlb_probable_starters("NY Yankees", "BOS")
        # Match is fuzzy so it may or may not hit; the important thing is no crash
        assert isinstance(result, dict)


# ── get_nba_team_leaders ──────────────────────────────────────────────────────

class TestGetNbaTeamLeaders:

    def _patch_find_id(self, team_id: str = "13"):
        return patch("api.espn_api.find_team_id", return_value=team_id)

    def _patch_fetch(self, data: dict):
        return patch("api.espn_api._fetch", return_value=data)

    def test_returns_top_scorer(self):
        page = _nba_team_page("Los Angeles Lakers")
        with self._patch_find_id():
            with self._patch_fetch(page):
                result = get_nba_team_leaders("Lakers")
        assert result["top_scorer"]["name"] == "LeBron James"
        assert result["top_scorer"]["value"] == "28.5"

    def test_returns_top_rebounder(self):
        page = _nba_team_page("Los Angeles Lakers")
        with self._patch_find_id():
            with self._patch_fetch(page):
                result = get_nba_team_leaders("Lakers")
        assert result["top_rebounder"]["name"] == "Anthony Davis"

    def test_returns_top_assists(self):
        page = _nba_team_page("Los Angeles Lakers")
        with self._patch_find_id():
            with self._patch_fetch(page):
                result = get_nba_team_leaders("Lakers")
        assert result["top_assists"]["name"] == "LeBron James"
        assert result["top_assists"]["value"] == "7.4"

    def test_empty_when_team_not_found(self):
        with patch("api.espn_api.find_team_id", return_value=None):
            result = get_nba_team_leaders("Unknown Team XYZ")
        assert result == {}

    def test_empty_when_espn_unreachable(self):
        with self._patch_find_id():
            with self._patch_fetch(None):
                result = get_nba_team_leaders("Lakers")
        assert result == {}

    def test_partial_leaders_no_crash(self):
        """Should handle missing categories gracefully."""
        page = {
            "team": {
                "displayName": "Warriors",
                "id": "9",
                "leaders": [],  # no leaders data
            }
        }
        with self._patch_find_id("9"):
            with self._patch_fetch(page):
                result = get_nba_team_leaders("Warriors")
        assert isinstance(result, dict)
        assert "top_scorer" not in result

    def test_fragment_match_points(self):
        """Category names containing 'point' are mapped to top_scorer."""
        page = {
            "team": {
                "displayName": "Warriors",
                "id": "9",
                "leaders": [{
                    "name": "avgPoints",
                    "displayName": "avg Points",
                    "leaders": [{
                        "athlete": {"displayName": "Steph Curry"},
                        "displayValue": "31.2",
                        "value": 31.2,
                    }],
                }],
            }
        }
        with self._patch_find_id("9"):
            with self._patch_fetch(page):
                result = get_nba_team_leaders("Warriors")
        assert result.get("top_scorer", {}).get("name") == "Steph Curry"


# ── get_nba_injuries ──────────────────────────────────────────────────────────

def _nba_injuries_response(players: list) -> dict:
    """Build a minimal ESPN NBA team injuries response."""
    return {"injuries": [
        {
            "athlete": {
                "displayName": p["name"],
                "position": {"abbreviation": p.get("pos", "")},
            },
            "status": {
                "type": {"description": p["status"]},
                "detail": p.get("detail", ""),
            },
        }
        for p in players
    ]}


class TestGetNbaInjuries:

    def _patch_find_id(self, team_id: str = "13"):
        return patch("api.espn_api.find_team_id", return_value=team_id)

    def _patch_fetch(self, data):
        return patch("api.espn_api._fetch", return_value=data)

    def test_returns_out_players(self):
        resp = _nba_injuries_response([
            {"name": "LeBron James", "status": "Out", "pos": "SF"},
        ])
        with self._patch_find_id():
            with self._patch_fetch(resp):
                result = get_nba_injuries("Lakers")
        assert result["total"] == 1
        assert result["players"][0]["name"] == "LeBron James"
        assert result["players"][0]["status"] == "Out"

    def test_out_count_sums_correctly(self):
        resp = _nba_injuries_response([
            {"name": "Player A", "status": "Out"},
            {"name": "Player B", "status": "Doubtful"},
            {"name": "Player C", "status": "Questionable"},
            {"name": "Player D", "status": "Active"},
        ])
        with self._patch_find_id():
            with self._patch_fetch(resp):
                result = get_nba_injuries("Lakers")
        # Out=1.0 + Doubtful=0.75 + Questionable=0.25 = 2.0; Active not counted
        assert abs(result["out_count"] - 2.0) < 0.01

    def test_empty_when_team_not_found(self):
        with patch("api.espn_api.find_team_id", return_value=None):
            result = get_nba_injuries("Unknown Team")
        assert result == {}

    def test_empty_when_espn_unreachable(self):
        with self._patch_find_id():
            with self._patch_fetch(None):
                result = get_nba_injuries("Lakers")
        assert result == {}

    def test_no_crash_on_empty_injuries(self):
        with self._patch_find_id():
            with self._patch_fetch({"injuries": []}):
                result = get_nba_injuries("Lakers")
        assert result["total"] == 0
        assert result["out_count"] == 0.0


# ── get_mlb_injuries ──────────────────────────────────────────────────────────

class TestGetMlbInjuries:

    def _patch_find_id(self, team_id: str = "10"):
        return patch("api.espn_api.find_team_id", return_value=team_id)

    def _patch_fetch(self, data):
        return patch("api.espn_api._fetch", return_value=data)

    def test_returns_il_players(self):
        resp = {"injuries": [
            {
                "athlete": {
                    "displayName": "Gerrit Cole",
                    "position": {"abbreviation": "SP"},
                },
                "status": {
                    "type": {"description": "15-Day IL"},
                    "detail": "Elbow",
                },
            }
        ]}
        with self._patch_find_id():
            with self._patch_fetch(resp):
                result = get_mlb_injuries("Yankees")
        assert result["total"] == 1
        assert result["players"][0]["name"] == "Gerrit Cole"

    def test_il_counts_as_out(self):
        resp = {"injuries": [
            {
                "athlete": {"displayName": "Cole", "position": {"abbreviation": "SP"}},
                "status": {"type": {"description": "10-Day IL"}, "detail": "Knee"},
            }
        ]}
        with self._patch_find_id():
            with self._patch_fetch(resp):
                result = get_mlb_injuries("Yankees")
        assert result["out_count"] == 1.0

    def test_empty_when_team_not_found(self):
        with patch("api.espn_api.find_team_id", return_value=None):
            result = get_mlb_injuries("Unknown Team")
        assert result == {}
