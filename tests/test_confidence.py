"""
Tests for core/confidence.py

Covers all three confidence tiers (ALTA / MEDIA / BAJA) and edge cases.
"""
import pytest
from core.confidence import confidence_level


class TestConfidenceLevel:
    # ── ALTA ──────────────────────────────────────────────────────────────────

    def test_alta_home_win_dominant(self):
        assert confidence_level({"home_win": 60, "draw": 25, "away_win": 15}) == "ALTA"

    def test_alta_draw_dominant(self):
        assert confidence_level({"home_win": 25, "draw": 55, "away_win": 20}) == "ALTA"

    def test_alta_away_win_dominant(self):
        assert confidence_level({"home_win": 15, "draw": 25, "away_win": 60}) == "ALTA"

    def test_alta_exactly_at_threshold(self):
        """55 is the minimum for ALTA."""
        assert confidence_level({"home_win": 55, "draw": 25, "away_win": 20}) == "ALTA"

    # ── MEDIA ─────────────────────────────────────────────────────────────────

    def test_media_at_42(self):
        assert confidence_level({"home_win": 42, "draw": 30, "away_win": 28}) == "MEDIA"

    def test_media_at_54(self):
        """54 is just below ALTA threshold."""
        assert confidence_level({"home_win": 54, "draw": 25, "away_win": 21}) == "MEDIA"

    def test_media_typical_close_match(self):
        assert confidence_level({"home_win": 48, "draw": 28, "away_win": 24}) == "MEDIA"

    # ── BAJA ──────────────────────────────────────────────────────────────────

    def test_baja_very_even(self):
        """Near-equal 1X2: all below 42%."""
        assert confidence_level({"home_win": 36, "draw": 35, "away_win": 29}) == "BAJA"

    def test_baja_just_below_media(self):
        assert confidence_level({"home_win": 41, "draw": 30, "away_win": 29}) == "BAJA"

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_all_zeros(self):
        """Degenerate input: all zeros → BAJA."""
        assert confidence_level({"home_win": 0, "draw": 0, "away_win": 0}) == "BAJA"

    def test_missing_keys_default_to_zero(self):
        """Missing keys are treated as 0."""
        assert confidence_level({"home_win": 60}) == "ALTA"

    def test_returns_string(self):
        result = confidence_level({"home_win": 50, "draw": 30, "away_win": 20})
        assert isinstance(result, str)

    def test_valid_values(self):
        """Output is always one of the three valid strings."""
        valid = {"ALTA", "MEDIA", "BAJA"}
        for hw in (30, 42, 55, 70):
            result = confidence_level(
                {"home_win": hw, "draw": max(0, 50 - hw), "away_win": 50 - max(0, 50 - hw)}
            )
            assert result in valid
