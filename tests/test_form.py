"""
Tests for core/form.py

Covers:
  - decay_weighted_stats: basic computation, empty input, single match,
    most-recent-game weighting
  - current_streak: win/draw/loss streaks, multiplier caps, last5 string
  - form_emoji: all emoji cases
  - h2h_adjustment: not enough data, balanced H2H, dominant home/away
  - h2h_summary: empty, typical
  - clean_sheet_prob: Poisson P(0)
"""
import math
import pytest

from core.form import (
    decay_weighted_stats,
    current_streak,
    form_emoji,
    h2h_adjustment,
    h2h_summary,
    clean_sheet_prob,
)


# ─────────────────────────────────────────────────────────────────────────────
# decay_weighted_stats
# ─────────────────────────────────────────────────────────────────────────────

class TestDecayWeightedStats:
    def test_empty_history_returns_none(self):
        assert decay_weighted_stats([]) is None

    def test_single_match(self):
        history = [{"scored": 3, "conceded": 1}]
        result = decay_weighted_stats(history)
        assert result is not None
        assert result["attack"] == 3.0
        assert result["defense"] == 1.0

    def test_recent_match_weighted_more(self):
        """With decay, the most recent match should dominate."""
        # Older: 0 goals; Newer: 5 goals
        history = [
            {"scored": 0, "conceded": 0},
            {"scored": 0, "conceded": 0},
            {"scored": 5, "conceded": 0},  # most recent
        ]
        result = decay_weighted_stats(history, alpha=0.5)
        assert result is not None
        # The most recent game (weight 1.0) has 5 goals; total weight ~1.25
        # Weighted avg > simple average (5/3 ≈ 1.67)
        assert result["attack"] > 5 / 3

    def test_uniform_history(self):
        """Constant history → attack/defense equals that constant."""
        goals = 2
        history = [{"scored": goals, "conceded": 1} for _ in range(8)]
        result = decay_weighted_stats(history)
        assert result is not None
        assert abs(result["attack"] - goals) < 0.01
        assert abs(result["defense"] - 1.0) < 0.01

    def test_last_n_truncation(self):
        """last_n=3 means only last 3 matches are used."""
        history = [{"scored": 99, "conceded": 99}] * 7 + [{"scored": 2, "conceded": 0}] * 3
        result = decay_weighted_stats(history, last_n=3)
        # Only the 3 matches with 2 goals each should be used
        assert result is not None
        assert abs(result["attack"] - 2.0) < 0.01

    def test_output_keys(self):
        history = [{"scored": 1, "conceded": 1}]
        result = decay_weighted_stats(history)
        assert "attack" in result
        assert "defense" in result


# ─────────────────────────────────────────────────────────────────────────────
# current_streak
# ─────────────────────────────────────────────────────────────────────────────

class TestCurrentStreak:
    def test_empty_history(self):
        result = current_streak([])
        assert result["type"] == "none"
        assert result["length"] == 0
        assert result["multiplier"] == 1.0
        assert result["last5"] == "-----"

    def test_win_streak_multiplier_increases(self):
        history = [{"result": "W", "scored": 2, "conceded": 0}] * 4
        result = current_streak(history)
        assert result["type"] == "W"
        assert result["length"] == 4
        assert result["multiplier"] > 1.0

    def test_loss_streak_multiplier_decreases(self):
        history = [{"result": "L", "scored": 0, "conceded": 2}] * 4
        result = current_streak(history)
        assert result["type"] == "L"
        assert result["length"] == 4
        assert result["multiplier"] < 1.0

    def test_draw_streak_neutral_multiplier(self):
        history = [{"result": "D", "scored": 1, "conceded": 1}] * 5
        result = current_streak(history)
        assert result["type"] == "D"
        assert result["multiplier"] == 1.0

    def test_win_multiplier_capped_at_10_percent(self):
        """Very long win streak: max +10% (multiplier ≤ 1.10)."""
        history = [{"result": "W", "scored": 2, "conceded": 0}] * 20
        result = current_streak(history)
        assert result["multiplier"] <= 1.10 + 1e-9

    def test_loss_multiplier_capped_at_minus_8_percent(self):
        """Very long loss streak: max -8% (multiplier ≥ 0.92)."""
        history = [{"result": "L", "scored": 0, "conceded": 2}] * 20
        result = current_streak(history)
        assert result["multiplier"] >= 0.92 - 1e-9

    def test_mixed_streak_detects_most_recent(self):
        """Streak is based on most recent results."""
        history = [
            {"result": "W"}, {"result": "W"}, {"result": "L"},
            {"result": "L"}, {"result": "L"},
        ]
        result = current_streak(history)
        assert result["type"] == "L"
        assert result["length"] == 3

    def test_last5_format(self):
        history = [
            {"result": "W"}, {"result": "D"}, {"result": "L"},
            {"result": "W"}, {"result": "W"},
        ]
        result = current_streak(history)
        # last5 should have exactly 5 characters
        assert len(result["last5"]) == 5

    def test_short_history_padded_with_dashes(self):
        history = [{"result": "W"}]
        result = current_streak(history)
        assert len(result["last5"]) == 5
        assert result["last5"].startswith("W")


# ─────────────────────────────────────────────────────────────────────────────
# form_emoji
# ─────────────────────────────────────────────────────────────────────────────

class TestFormEmoji:
    def test_win_streak_4_fire(self):
        streak = {"type": "W", "length": 4}
        assert form_emoji(streak) == "🔥"

    def test_win_streak_2_up_chart(self):
        streak = {"type": "W", "length": 2}
        assert form_emoji(streak) == "📈"

    def test_loss_streak_4_snow(self):
        streak = {"type": "L", "length": 4}
        assert form_emoji(streak) == "❄️"

    def test_loss_streak_2_down_chart(self):
        streak = {"type": "L", "length": 2}
        assert form_emoji(streak) == "📉"

    def test_draw_or_single_neutral(self):
        streak = {"type": "D", "length": 3}
        assert form_emoji(streak) == "➡️"

    def test_none_type_neutral(self):
        streak = {"type": "none", "length": 0}
        assert form_emoji(streak) == "➡️"


# ─────────────────────────────────────────────────────────────────────────────
# h2h_adjustment
# ─────────────────────────────────────────────────────────────────────────────

class TestH2hAdjustment:
    def test_not_enough_data_returns_one(self):
        assert h2h_adjustment([]) == 1.0
        assert h2h_adjustment([(1, 0), (0, 1)]) == 1.0  # only 2 records

    def test_balanced_returns_near_one(self):
        """45% home win rate (= baseline) → multiplier ≈ 1.0."""
        # 9 home wins out of 20 = 45%
        records = [(1, 0)] * 9 + [(0, 1)] * 11
        assert abs(h2h_adjustment(records) - 1.0) < 0.01

    def test_dominant_home_boosts_multiplier(self):
        """All home wins → multiplier > 1."""
        records = [(2, 0)] * 10
        assert h2h_adjustment(records) > 1.0

    def test_dominant_away_reduces_multiplier(self):
        """All away wins → multiplier < 1."""
        records = [(0, 2)] * 10
        assert h2h_adjustment(records) < 1.0

    def test_multiplier_bounds(self):
        """Multiplier is always in [0.94, 1.06]."""
        extreme_home = [(5, 0)] * 20
        extreme_away = [(0, 5)] * 20
        assert h2h_adjustment(extreme_home) <= 1.06 + 1e-9
        assert h2h_adjustment(extreme_away) >= 0.94 - 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# h2h_summary
# ─────────────────────────────────────────────────────────────────────────────

class TestH2hSummary:
    def test_empty_records(self):
        s = h2h_summary([])
        assert s["total"] == 0

    def test_counts(self):
        records = [(2, 1), (1, 1), (0, 2), (3, 0), (1, 1)]
        s = h2h_summary(records)
        assert s["total"] == 5
        assert s["home_wins"] == 2
        assert s["draws"] == 2
        assert s["away_wins"] == 1

    def test_avg_goals(self):
        records = [(2, 1), (1, 2)]   # avg = (3 + 3) / 2 = 3.0
        s = h2h_summary(records)
        assert s["avg_goals"] == 3.0


# ─────────────────────────────────────────────────────────────────────────────
# clean_sheet_prob
# ─────────────────────────────────────────────────────────────────────────────

class TestCleanSheetProb:
    def test_zero_conceded_returns_near_one(self):
        """exp(-0.01) ≈ 0.99."""
        p = clean_sheet_prob(0.0)
        assert p >= 0.98

    def test_high_conceded_returns_near_zero(self):
        """High xG against → small clean sheet probability."""
        p = clean_sheet_prob(3.0)
        assert p < 0.1

    def test_matches_poisson_formula(self):
        """P(0; λ) = exp(-λ)."""
        for avg in (0.5, 1.0, 1.5, 2.0):
            expected = round(math.exp(-avg), 3)
            assert clean_sheet_prob(avg) == expected

    def test_output_in_valid_range(self):
        for avg in (0.1, 0.5, 1.2, 2.5, 4.0):
            p = clean_sheet_prob(avg)
            assert 0.0 <= p <= 1.0
