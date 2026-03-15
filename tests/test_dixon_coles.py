"""
Tests for Dixon-Coles implementation in sports/football.py

Covers:
  - _dc_correction: all four low-score corrections + passthrough (rho = -0.13)
  - dixon_coles_probabilities: probabilities sum to ~100%, symmetry, BTTS/Over
  - expected_corners and expected_cards via their modules
"""
import sys
import os
import math

import pytest

# We need sys.path set; conftest.py handles this.
# Import the private correction function via the module.
import importlib
import sports.football as _football

from sports.football import dixon_coles_probabilities
from core.corners import expected_corners
from core.cards import expected_cards


_RHO = -0.13   # same constant as in football.py


# ─────────────────────────────────────────────────────────────────────────────
# Dixon-Coles tau correction
# ─────────────────────────────────────────────────────────────────────────────

class TestDcCorrection:
    """Test the tau (ρ) correction function directly."""

    def _correction(self, hg, ag, xg_h=1.5, xg_a=1.0):
        return _football._dc_correction(hg, ag, xg_h, xg_a)

    def test_0_0_correction(self):
        """(0,0): 1 - xg_h * xg_a * rho.  rho<0 → correction > 1."""
        xg_h, xg_a = 1.5, 1.0
        expected = 1.0 - xg_h * xg_a * _RHO
        assert abs(self._correction(0, 0, xg_h, xg_a) - expected) < 1e-9

    def test_1_0_correction(self):
        """(1,0): 1 + xg_a * rho.  rho<0 → correction < 1."""
        xg_h, xg_a = 1.5, 1.0
        expected = 1.0 + xg_a * _RHO
        assert abs(self._correction(1, 0, xg_h, xg_a) - expected) < 1e-9

    def test_0_1_correction(self):
        """(0,1): 1 + xg_h * rho.  rho<0 → correction < 1."""
        xg_h, xg_a = 1.5, 1.0
        expected = 1.0 + xg_h * _RHO
        assert abs(self._correction(0, 1, xg_h, xg_a) - expected) < 1e-9

    def test_1_1_correction(self):
        """(1,1): 1 - rho.  rho<0 → correction > 1."""
        expected = 1.0 - _RHO
        assert abs(self._correction(1, 1) - expected) < 1e-9

    def test_other_scores_return_one(self):
        """All other score combinations: correction = 1.0 (no adjustment)."""
        for hg, ag in [(2, 0), (0, 2), (2, 1), (1, 2), (3, 3), (0, 3)]:
            assert self._correction(hg, ag) == 1.0, f"Failed for ({hg},{ag})"


# ─────────────────────────────────────────────────────────────────────────────
# dixon_coles_probabilities
# ─────────────────────────────────────────────────────────────────────────────

class TestDixonColesProbabilities:
    def test_1x2_sum_to_100(self):
        """Home win + draw + away win must sum to ~100%."""
        for xg_h, xg_a in [(1.5, 1.0), (2.0, 0.8), (1.0, 1.0), (0.5, 2.5)]:
            result = dixon_coles_probabilities(xg_h, xg_a)
            total = result["home_win"] + result["draw"] + result["away_win"]
            assert abs(total - 100.0) < 0.5, (
                f"1X2 sum={total:.2f} for xG({xg_h}, {xg_a})"
            )

    def test_symmetric_xg_near_equal_home_away(self):
        """With equal xG, home_win ≈ away_win (they're not identical because of DC correction)."""
        result = dixon_coles_probabilities(1.5, 1.5)
        assert abs(result["home_win"] - result["away_win"]) < 2.0

    def test_stronger_home_higher_home_win(self):
        """Higher home xG → higher home win probability."""
        strong_home = dixon_coles_probabilities(2.5, 0.5)
        weak_home   = dixon_coles_probabilities(0.5, 2.5)
        assert strong_home["home_win"] > weak_home["home_win"]
        assert strong_home["away_win"] < weak_home["away_win"]

    def test_over_markets_not_exceed_100(self):
        result = dixon_coles_probabilities(1.5, 1.5)
        for key in ("over_1_5", "over_2_5", "over_3_5", "btts"):
            assert 0 <= result[key] <= 100, f"{key}={result[key]}"

    def test_over_2_5_less_than_over_1_5(self):
        """Strictly: over_2_5 <= over_1_5."""
        result = dixon_coles_probabilities(1.5, 1.0)
        assert result["over_2_5"] <= result["over_1_5"]
        assert result["over_3_5"] <= result["over_2_5"]

    def test_btts_increases_with_both_attacking(self):
        """Balanced attack → more BTTS."""
        balanced = dixon_coles_probabilities(1.5, 1.5)
        one_sided = dixon_coles_probabilities(3.0, 0.1)
        assert balanced["btts"] > one_sided["btts"]

    def test_draw_increases_near_equal_xg(self):
        """Draw probability peaks around equal xG."""
        equal = dixon_coles_probabilities(1.2, 1.2)
        dominant = dixon_coles_probabilities(3.0, 0.5)
        assert equal["draw"] > dominant["draw"]

    def test_output_keys_present(self):
        result = dixon_coles_probabilities(1.5, 1.0)
        for key in ("home_win", "draw", "away_win", "over_1_5", "over_2_5", "over_3_5", "btts"):
            assert key in result


# ─────────────────────────────────────────────────────────────────────────────
# expected_corners
# ─────────────────────────────────────────────────────────────────────────────

class TestExpectedCorners:
    def test_basic_formula(self):
        """corners = 8 + total_xg * 2.3"""
        for xg_h, xg_a in [(1.5, 1.0), (2.0, 0.8), (0.5, 0.5)]:
            expected = round(8 + (xg_h + xg_a) * 2.3, 1)
            assert expected_corners(xg_h, xg_a) == expected

    def test_minimum_is_eight(self):
        """With zero xG, still expect base 8 corners."""
        assert expected_corners(0, 0) == 8.0

    def test_higher_xg_more_corners(self):
        low  = expected_corners(0.5, 0.5)
        high = expected_corners(2.5, 2.5)
        assert high > low


# ─────────────────────────────────────────────────────────────────────────────
# expected_cards
# ─────────────────────────────────────────────────────────────────────────────

class TestExpectedCards:
    def test_basic_formula(self):
        """cards = 3.8 + total_xg * 0.35"""
        for xg_h, xg_a in [(1.5, 1.0), (2.0, 1.0)]:
            expected = round(3.8 + (xg_h + xg_a) * 0.35, 1)
            assert expected_cards(xg_h, xg_a) == expected

    def test_base_is_3_8(self):
        assert expected_cards(0, 0) == 3.8

    def test_higher_xg_more_cards(self):
        low  = expected_cards(0.5, 0.5)
        high = expected_cards(3.0, 3.0)
        assert high > low
