"""
Tests for MLB pitcher handedness and recent starts data flow.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sports_engine"))


# ── Tests: prediction dict structure ─────────────────────────────────────────

class TestBaseballPitcherOutputKeys:
    """Verify baseball.predict_game always returns the new pitcher keys."""

    def _make_pred(self, pitcher_info=None):
        """Build a minimal prediction dict as predict_game would produce."""
        from sports.baseball import predict_game
        import unittest.mock as mock

        # Mock the entire external call chain so no network is needed
        fake_stats = {
            "win_pct": 0.500, "runs_pg": 4.5, "era": 4.00,
            "summary": "W50-L50",
        }
        fake_starters = {
            "home_pitcher": pitcher_info or None,
            "away_pitcher": None,
        }

        with mock.patch("sports.baseball._fetch_stats", return_value=fake_stats), \
             mock.patch("sports.baseball.get_mlb_today_starters", return_value=fake_starters, create=True), \
             mock.patch("sports.baseball.is_available", return_value=True, create=True):
            # Patch the import inside predict_game by mocking the sportradar module
            with mock.patch("api.sportradar.get_mlb_today_starters", return_value=fake_starters), \
                 mock.patch("api.sportradar.is_available", return_value=True):
                return predict_game("Red Sox", "Yankees")

    def test_new_keys_always_present(self):
        pred = self._make_pred()
        for key in (
            "home_pitcher_hand", "home_pitcher_recent_starts",
            "away_pitcher_hand", "away_pitcher_recent_starts",
        ):
            assert key in pred, f"Missing key: {key}"

    def test_none_when_pitcher_info_absent(self):
        pred = self._make_pred(pitcher_info=None)
        assert pred["home_pitcher_hand"] is None
        assert pred["home_pitcher_recent_starts"] == []

    def test_hand_and_starts_propagated_from_pitcher_info(self):
        pitcher = {
            "name": "Gerrit Cole",
            "era": 3.20,
            "whip": 1.05,
            "k_per_9": 10.2,
            "hand": "R",
            "recent_starts": [
                {"date": "04/01", "ip": "7.0", "er": 1, "k": 9, "result": "W"},
                {"date": "03/27", "ip": "6.2", "er": 2, "k": 8, "result": "L"},
            ],
        }
        pred = self._make_pred(pitcher_info=pitcher)
        assert pred["home_pitcher"] == "Gerrit Cole"
        assert pred["home_pitcher_hand"] == "R"
        assert len(pred["home_pitcher_recent_starts"]) == 2
        assert pred["home_pitcher_recent_starts"][0]["date"] == "04/01"

    def test_legacy_era_keys_still_present(self):
        """Ensure we didn't break the existing keys used by parlay.py."""
        pred = self._make_pred()
        assert "home_pitcher_era" in pred
        assert "home_pitcher_whip" in pred
        assert "home_pitcher_k9" in pred
        assert "away_pitcher_era" in pred


# ── Tests: Sportradar _mlb_pitcher_recent_starts parsing ─────────────────────

class TestSportradarPitcherGameLog:
    """Unit-test the game-log parser with mock API responses."""

    def _call_recent(self, mock_data):
        import unittest.mock as mock
        from api.sportradar import _mlb_pitcher_recent_starts
        with mock.patch("api.sportradar._fetch", return_value=mock_data):
            return _mlb_pitcher_recent_starts("fake-id-123")

    def test_empty_on_empty_response(self):
        result = self._call_recent({})
        assert result == []

    def test_parses_standard_game_log(self):
        # Games list is in chronological order (oldest first, newest last).
        # The function takes the last n entries and reverses → most-recent first.
        mock_data = {
            "games": [
                {
                    "scheduled": "2024-03-27T18:05:00Z",
                    "statistics": {"pitching": {
                        "ip_pitched": "6.2", "earned_runs": 2,
                        "strikeouts": 7, "win": False, "loss": True,
                    }},
                },
                {
                    "scheduled": "2024-04-01T18:05:00Z",
                    "statistics": {"pitching": {
                        "ip_pitched": "7.0", "earned_runs": 1,
                        "strikeouts": 9, "win": True, "loss": False,
                    }},
                },
            ]
        }
        result = self._call_recent(mock_data)
        assert len(result) == 2
        # Most-recent first (04/01 was added last → last in list → reversed to first)
        assert result[0]["date"] == "04/01"
        assert result[0]["result"] == "W"
        assert result[0]["k"] == 9
        assert result[1]["date"] == "03/27"
        assert result[1]["result"] == "L"

    def test_caps_at_n_starts(self):
        games = []
        for i in range(10):
            games.append({
                "scheduled": f"2024-03-{(i+1):02d}T18:00:00Z",
                "statistics": {"pitching": {
                    "ip_pitched": "6.0", "earned_runs": 2,
                    "strikeouts": 6, "win": False, "loss": False,
                }},
            })
        result = self._call_recent({"games": games})
        assert len(result) == 5  # default n=5

    def test_skips_entries_without_pitching_data(self):
        mock_data = {
            "games": [
                {"scheduled": "2024-04-01", "statistics": {}},  # no pitching
                {
                    "scheduled": "2024-03-28T18:00:00Z",
                    "statistics": {"pitching": {
                        "ip_pitched": "5.0", "earned_runs": 3,
                        "strikeouts": 5, "win": True, "loss": False,
                    }},
                },
            ]
        }
        result = self._call_recent(mock_data)
        assert len(result) == 1
        assert result[0]["result"] == "W"


# ── Tests: Sportradar _mlb_pitcher_hand ───────────────────────────────────────

class TestSportradarPitcherHand:
    def _call_hand(self, mock_data, player_id="fake-id"):
        import unittest.mock as mock
        from api.sportradar import _mlb_pitcher_hand
        with mock.patch("api.sportradar._fetch", return_value=mock_data):
            return _mlb_pitcher_hand(player_id)

    def test_returns_R_for_right(self):
        result = self._call_hand({"player": {"preferred_hand": "R"}})
        assert result == "R"

    def test_returns_L_for_left(self):
        result = self._call_hand({"player": {"throws": "L"}})
        assert result == "L"

    def test_empty_string_on_missing(self):
        result = self._call_hand({})
        assert result == ""

    def test_empty_string_on_invalid_value(self):
        result = self._call_hand({"player": {"preferred_hand": "X"}})
        assert result == ""
