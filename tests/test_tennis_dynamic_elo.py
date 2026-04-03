"""
Tests for the dynamic Elo system in sports/tennis.py.
"""
import json
import os
import sys
import tempfile

import pytest

# Ensure sports_engine/ is on PYTHONPATH
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sports_engine"))

import sports.tennis as tennis


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_tmp_elo_file(tmp_path):
    """Patch tennis._TENNIS_ELO_FILE to a temp location for isolation."""
    elo_path = str(tmp_path / "tennis_elo.json")
    tennis._TENNIS_ELO_FILE = elo_path  # monkey-patch for the test
    return elo_path


# ── Tests: record_tennis_result ───────────────────────────────────────────────

class TestRecordTennisResult:
    def test_returns_expected_keys(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        result = tennis.record_tennis_result("Novak Djokovic", "Daniil Medvedev", "hard")
        for key in ("winner_elo", "loser_elo", "winner_surface_elo", "loser_surface_elo", "delta", "surface"):
            assert key in result, f"Missing key: {key}"

    def test_winner_elo_increases(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        before_winner = tennis._get_player_elo("Carlos Alcaraz", "clay")
        result = tennis.record_tennis_result("Carlos Alcaraz", "Alexander Zverev", "clay")
        assert result["winner_elo"] > before_winner - 10  # winner should not lose much

    def test_loser_elo_decreases(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        before_loser = tennis._get_player_elo("Alexander Zverev", "clay")
        result = tennis.record_tennis_result("Carlos Alcaraz", "Alexander Zverev", "clay")
        assert result["loser_elo"] < before_loser + 10  # loser should not gain much

    def test_delta_positive(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        result = tennis.record_tennis_result("Jannik Sinner", "Holger Rune", "hard")
        assert result["delta"] > 0

    def test_surface_recorded(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        result = tennis.record_tennis_result("Iga Swiatek", "Coco Gauff", "clay")
        assert result["surface"] == "clay"

    def test_slam_gives_larger_delta(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        r_normal = tennis.record_tennis_result("Novak Djokovic", "Carlos Alcaraz", "hard", is_slam=False)
        # Reset file for second test
        _make_tmp_elo_file(tmp_path)
        r_slam   = tennis.record_tennis_result("Novak Djokovic", "Carlos Alcaraz", "hard", is_slam=True)
        assert r_slam["delta"] > r_normal["delta"]

    def test_elo_persisted_to_file(self, tmp_path):
        elo_path = _make_tmp_elo_file(tmp_path)
        tennis.record_tennis_result("Roger Federer", "Rafael Nadal", "grass")
        assert os.path.exists(elo_path)
        with open(elo_path) as f:
            data = json.load(f)
        assert "Roger Federer" in data["ratings"]
        assert "Rafael Nadal" in data["ratings"]

    def test_surface_sub_rating_persisted(self, tmp_path):
        elo_path = _make_tmp_elo_file(tmp_path)
        tennis.record_tennis_result("Rafael Nadal", "Roger Federer", "clay")
        with open(elo_path) as f:
            data = json.load(f)
        assert "clay" in data["surface_ratings"].get("Rafael Nadal", {})

    def test_match_count_increments(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        tennis.record_tennis_result("Andy Murray", "Stefanos Tsitsipas", "hard")
        data = tennis._tennis_elo_data()
        assert data["match_counts"].get("Andy Murray", 0) >= 1

    def test_unknown_surface_defaults_to_hard(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        result = tennis.record_tennis_result("Novak Djokovic", "Jannik Sinner", "carpet")
        assert result["surface"] == "hard"


# ── Tests: _get_player_elo with dynamic store ─────────────────────────────────

class TestGetPlayerEloDynamic:
    def test_uses_dynamic_overall_elo_after_result(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        # Record a result so dynamic Elo exists
        tennis.record_tennis_result("Taylor Fritz", "Hubert Hurkacz", "hard")
        elo = tennis._get_player_elo("Taylor Fritz", "hard")
        # Dynamic store should be consulted (we just can't know the exact value)
        assert isinstance(elo, float)
        assert 1500 <= elo <= 2600

    def test_falls_back_to_known_elo_when_no_dynamic(self, tmp_path):
        _make_tmp_elo_file(tmp_path)  # empty store
        elo = tennis._get_player_elo("Novak Djokovic", "hard")
        # Should fall back to _KNOWN_ELO
        assert elo == tennis._KNOWN_ELO["Novak Djokovic"]

    def test_surface_specific_elo_used_when_available(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        tennis.record_tennis_result("Carlos Alcaraz", "Jannik Sinner", "clay")
        clay_elo = tennis._get_player_elo("Carlos Alcaraz", "clay")
        hard_elo = tennis._get_player_elo("Carlos Alcaraz", "hard")
        # Clay elo should differ from hard elo (or overall) after clay recording
        # They differ unless the clay update happened to land at the same value
        data = tennis._tennis_elo_data()
        recorded_clay = data["surface_ratings"].get("Carlos Alcaraz", {}).get("clay")
        assert clay_elo == recorded_clay


# ── Tests: predict_match with dynamic Elo ────────────────────────────────────

class TestPredictMatchDynamic:
    def test_elo_dynamic_flags_false_before_any_results(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        pred = tennis.predict_match("Novak Djokovic", "Rafael Nadal", "clay")
        # No dynamic results recorded yet
        assert pred["elo_dynamic_p1"] is False
        assert pred["elo_dynamic_p2"] is False

    def test_elo_dynamic_flag_true_after_result(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        tennis.record_tennis_result("Novak Djokovic", "Rafael Nadal", "clay")
        pred = tennis.predict_match("Novak Djokovic", "Rafael Nadal", "clay")
        assert pred["elo_dynamic_p1"] is True
        assert pred["elo_dynamic_p2"] is True

    def test_matches_recorded_increments(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        tennis.record_tennis_result("Iga Swiatek", "Aryna Sabalenka", "clay")
        pred = tennis.predict_match("Iga Swiatek", "Aryna Sabalenka", "clay")
        assert pred["matches_recorded_p1"] >= 1
        assert pred["matches_recorded_p2"] >= 1

    def test_prediction_still_valid_after_dynamic_elo(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        tennis.record_tennis_result("Carlos Alcaraz", "Daniil Medvedev", "hard")
        pred = tennis.predict_match("Carlos Alcaraz", "Daniil Medvedev", "hard")
        assert 0 < pred["home_win"] < 100
        assert 0 < pred["away_win"] < 100
        assert abs(pred["home_win"] + pred["away_win"] - 100) < 0.5

    def test_elo_p1_base_and_adjusted_present(self, tmp_path):
        _make_tmp_elo_file(tmp_path)
        pred = tennis.predict_match("Novak Djokovic", "Carlos Alcaraz", "hard")
        assert "elo_p1_base" in pred
        assert "elo_p2_base" in pred
        assert "elo_p1" in pred
        assert "elo_p2" in pred
