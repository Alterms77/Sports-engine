"""
Tests for core/props.py

Covers all six public functions:
  - football_shots_on_target
  - football_cards_detail
  - nba_quarter_projections
  - nba_player_props
  - nba_game_totals
  - mlb_player_props
  - mlb_run_line
  - nfl_quarter_projections
  - nfl_player_props
"""

import math
import pytest

from core.props import (
    football_shots_on_target,
    football_cards_detail,
    nba_quarter_projections,
    nba_player_props,
    nba_game_totals,
    mlb_player_props,
    mlb_run_line,
    nfl_quarter_projections,
    nfl_player_props,
)


# ═══════════════════════════════════════════════════════════════════════════════
# FOOTBALL — shots on target
# ═══════════════════════════════════════════════════════════════════════════════

class TestFootballShotsOnTarget:
    def test_output_keys(self):
        result = football_shots_on_target(1.5, 1.0)
        for key in ("sot_home", "sot_away", "sot_total", "line", "suggestion"):
            assert key in result

    def test_non_negative_values(self):
        result = football_shots_on_target(0.5, 0.8)
        assert result["sot_home"] >= 0
        assert result["sot_away"] >= 0
        assert result["sot_total"] >= 0

    def test_sot_total_equals_sum(self):
        r = football_shots_on_target(1.5, 1.0)
        assert abs(r["sot_total"] - round(r["sot_home"] + r["sot_away"], 1)) < 0.05

    def test_higher_xg_more_sot(self):
        high = football_shots_on_target(2.5, 1.0)
        low  = football_shots_on_target(0.5, 1.0)
        assert high["sot_home"] > low["sot_home"]

    def test_suggestion_is_over_or_under(self):
        r = football_shots_on_target(1.5, 1.0)
        assert r["suggestion"] in ("Over", "Under")

    def test_suggestion_consistent_with_total(self):
        """suggestion == Over iff sot_total >= line"""
        for xg_h, xg_a in [(1.0, 0.5), (2.0, 2.0), (0.3, 0.3)]:
            r = football_shots_on_target(xg_h, xg_a)
            if r["sot_total"] >= r["line"]:
                assert r["suggestion"] == "Over"
            else:
                assert r["suggestion"] == "Under"

    def test_zero_xg_small_sot(self):
        """Even with zero xG, the floor prevents extreme values."""
        r = football_shots_on_target(0.0, 0.0)
        assert r["sot_home"] >= 0
        assert r["sot_away"] >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# FOOTBALL — cards detail
# ═══════════════════════════════════════════════════════════════════════════════

class TestFootballCardsDetail:
    def test_output_keys(self):
        result = football_cards_detail(1.5, 1.0)
        for key in (
            "yellow_home", "yellow_away", "total_yellow",
            "total_red", "total_cards",
            "over_3_5_cards", "over_4_5_cards",
        ):
            assert key in result

    def test_total_yellow_equals_sum(self):
        r = football_cards_detail(1.5, 1.0)
        assert abs(r["total_yellow"] - round(r["yellow_home"] + r["yellow_away"], 1)) < 0.05

    def test_total_cards_includes_red(self):
        r = football_cards_detail(1.5, 1.0)
        assert abs(r["total_cards"] - round(r["total_yellow"] + r["total_red"], 1)) < 0.05

    def test_higher_xg_more_cards(self):
        """High-xG game (aggressive) → more cards."""
        high = football_cards_detail(3.0, 2.0)
        low  = football_cards_detail(0.5, 0.5)
        assert high["total_cards"] > low["total_cards"]

    def test_opponent_xg_increases_defensive_team_yellows(self):
        """Away team xG increase → home team gets more yellows (forced to defend)."""
        low_away  = football_cards_detail(1.0, 0.5)
        high_away = football_cards_detail(1.0, 2.5)
        assert high_away["yellow_home"] > low_away["yellow_home"]

    def test_over_flags_consistent(self):
        r = football_cards_detail(1.5, 1.0)
        assert r["over_3_5_cards"] == (r["total_cards"] > 3.5)
        assert r["over_4_5_cards"] == (r["total_cards"] > 4.5)

    def test_over_4_5_implies_over_3_5(self):
        """If total > 4.5, it must also be > 3.5."""
        for xg_h, xg_a in [(4.0, 3.0), (0.5, 0.5), (2.0, 1.5)]:
            r = football_cards_detail(xg_h, xg_a)
            if r["over_4_5_cards"]:
                assert r["over_3_5_cards"]


# ═══════════════════════════════════════════════════════════════════════════════
# NBA — quarter projections
# ═══════════════════════════════════════════════════════════════════════════════

class TestNbaQuarterProjections:
    def test_returns_four_quarters(self):
        result = nba_quarter_projections(112.0, 108.0)
        assert len(result) == 4

    def test_quarter_numbers_sequential(self):
        result = nba_quarter_projections(110.0, 110.0)
        assert [q["quarter"] for q in result] == [1, 2, 3, 4]

    def test_output_keys_per_quarter(self):
        result = nba_quarter_projections(112.0, 110.0)
        for q in result:
            assert "quarter" in q
            assert "home" in q
            assert "away" in q
            assert "total" in q

    def test_total_home_sums_to_expected(self):
        xh, xa = 112.0, 108.0
        result = nba_quarter_projections(xh, xa)
        home_sum = sum(q["home"] for q in result)
        away_sum = sum(q["away"] for q in result)
        assert abs(home_sum - xh) < 0.5
        assert abs(away_sum - xa) < 0.5

    def test_q_total_equals_home_plus_away(self):
        result = nba_quarter_projections(114.0, 106.0)
        for q in result:
            assert abs(q["total"] - round(q["home"] + q["away"], 1)) < 0.05

    def test_q4_highest_score_quarter(self):
        """Q4 is empirically the highest-scoring quarter in NBA."""
        result = nba_quarter_projections(112.0, 112.0)
        q4_total = result[3]["total"]
        q1_total = result[0]["total"]
        q3_total = result[2]["total"]
        assert q4_total > q1_total
        assert q4_total > q3_total

    def test_all_values_positive(self):
        result = nba_quarter_projections(85.0, 85.0)
        for q in result:
            assert q["home"] > 0
            assert q["away"] > 0
            assert q["total"] > 0


# ═══════════════════════════════════════════════════════════════════════════════
# NBA — player props
# ═══════════════════════════════════════════════════════════════════════════════

class TestNbaPlayerProps:
    def test_output_structure(self):
        result = nba_player_props(112.5, 108.0)
        assert "home" in result
        assert "away" in result

    def test_prop_keys(self):
        result = nba_player_props(112.5, 108.0)
        for side in ("home", "away"):
            for key in (
                "star_points", "2nd_scorer", "role_player",
                "assists", "rebounds_big", "rebounds_wing",
            ):
                assert key in result[side], f"Missing key '{key}' in '{side}'"

    def test_star_points_is_largest(self):
        """Star player points > 2nd scorer > role player."""
        r = nba_player_props(112.5, 112.5)
        for side in ("home", "away"):
            assert r[side]["star_points"] > r[side]["2nd_scorer"] > r[side]["role_player"]

    def test_higher_ppg_more_star_points(self):
        high = nba_player_props(130.0, 110.0)
        low  = nba_player_props(90.0, 110.0)
        assert high["home"]["star_points"] > low["home"]["star_points"]

    def test_rebounds_big_greater_than_wing(self):
        r = nba_player_props(112.5, 112.5)
        for side in ("home", "away"):
            assert r[side]["rebounds_big"] > r[side]["rebounds_wing"]

    def test_all_values_positive(self):
        r = nba_player_props(85.0, 85.0)
        for side in ("home", "away"):
            for val in r[side].values():
                assert val > 0, f"Unexpected non-positive value: {val}"


# ═══════════════════════════════════════════════════════════════════════════════
# NBA — game totals
# ═══════════════════════════════════════════════════════════════════════════════

class TestNbaGameTotals:
    def test_game_total_equals_sum(self):
        r = nba_game_totals(112.0, 108.0)
        assert abs(r["game_total"] - 220.0) < 0.05

    def test_team_totals_preserved(self):
        r = nba_game_totals(115.0, 107.5)
        assert r["team_total_home"] == 115.0
        assert r["team_total_away"] == 107.5

    def test_ou_line_is_rounded_whole_number(self):
        r = nba_game_totals(112.3, 108.7)
        assert r["over_under_line"] == round(r["game_total"])


# ═══════════════════════════════════════════════════════════════════════════════
# MLB — player props
# ═══════════════════════════════════════════════════════════════════════════════

class TestMlbPlayerProps:
    def test_output_structure(self):
        result = mlb_player_props(4.5, 4.5)
        assert "home" in result
        assert "away" in result

    def test_prop_keys(self):
        result = mlb_player_props(4.5, 4.5)
        for side in ("home", "away"):
            for key in ("team_hits", "cleanup_hits", "cleanup_hr", "ace_strikeouts"):
                assert key in result[side], f"Missing key '{key}' in '{side}'"

    def test_team_hits_greater_than_cleanup(self):
        r = mlb_player_props(4.5, 4.5)
        for side in ("home", "away"):
            assert r[side]["team_hits"] > r[side]["cleanup_hits"]

    def test_higher_offense_more_hits(self):
        high = mlb_player_props(6.0, 4.5)
        low  = mlb_player_props(2.0, 4.5)
        assert high["home"]["team_hits"] > low["home"]["team_hits"]

    def test_better_offense_fewer_ace_ks(self):
        """Ace pitching vs strong offense → fewer strikeouts."""
        # home ace pitches against away batters; high away xR = tough lineup
        strong_opp = mlb_player_props(4.5, 6.0)  # away offense is strong
        weak_opp   = mlb_player_props(4.5, 2.0)  # away offense is weak
        assert strong_opp["home"]["ace_strikeouts"] < weak_opp["home"]["ace_strikeouts"]

    def test_all_values_non_negative(self):
        r = mlb_player_props(0.5, 0.5)
        for side in ("home", "away"):
            for val in r[side].values():
                assert val >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# MLB — run line
# ═══════════════════════════════════════════════════════════════════════════════

class TestMlbRunLine:
    def test_output_keys(self):
        r = mlb_run_line(4.5, 4.5)
        for key in ("fav_side", "cover_prob", "over_under"):
            assert key in r

    def test_cover_prob_is_valid_percentage(self):
        r = mlb_run_line(4.5, 3.0)
        assert 0 <= r["cover_prob"] <= 100

    def test_dominant_home_cover_prob_gt_50(self):
        """When home team is heavily favoured, cover prob > 50%."""
        r = mlb_run_line(8.0, 2.0)
        assert r["cover_prob"] > 50

    def test_fav_side_home_when_home_runs_more(self):
        r = mlb_run_line(5.0, 3.0)
        assert r["fav_side"] == "home"

    def test_fav_side_away_when_away_runs_more(self):
        r = mlb_run_line(3.0, 5.0)
        assert r["fav_side"] == "away"

    def test_over_under_correct(self):
        r = mlb_run_line(4.0, 3.5)
        assert abs(r["over_under"] - 7.5) < 0.05


# ═══════════════════════════════════════════════════════════════════════════════
# NFL — quarter projections
# ═══════════════════════════════════════════════════════════════════════════════

class TestNflQuarterProjections:
    def test_returns_four_quarters(self):
        result = nfl_quarter_projections(24.0, 17.0)
        assert len(result) == 4

    def test_quarter_numbers_sequential(self):
        result = nfl_quarter_projections(22.0, 22.0)
        assert [q["quarter"] for q in result] == [1, 2, 3, 4]

    def test_output_keys_per_quarter(self):
        result = nfl_quarter_projections(24.0, 20.0)
        for q in result:
            for key in ("quarter", "home", "away", "total"):
                assert key in q

    def test_total_home_sums_to_expected(self):
        xh, xa = 24.0, 17.0
        result = nfl_quarter_projections(xh, xa)
        home_sum = sum(q["home"] for q in result)
        away_sum = sum(q["away"] for q in result)
        assert abs(home_sum - xh) < 0.5
        assert abs(away_sum - xa) < 0.5

    def test_q4_highest(self):
        """Q4 (garbage time + late TDs) is highest-scoring in NFL."""
        result = nfl_quarter_projections(22.0, 22.0)
        q4 = result[3]["total"]
        q1 = result[0]["total"]
        q3 = result[2]["total"]
        assert q4 > q1
        assert q4 > q3

    def test_q_total_equals_home_plus_away(self):
        result = nfl_quarter_projections(28.0, 21.0)
        for q in result:
            assert abs(q["total"] - round(q["home"] + q["away"], 1)) < 0.05


# ═══════════════════════════════════════════════════════════════════════════════
# NFL — player props
# ═══════════════════════════════════════════════════════════════════════════════

class TestNflPlayerProps:
    def test_output_structure(self):
        result = nfl_player_props(24.0, 17.0)
        assert "home" in result
        assert "away" in result

    def test_prop_keys(self):
        result = nfl_player_props(24.0, 17.0)
        expected_keys = (
            "qb_pass_yards", "qb_pass_tds", "qb_completions",
            "rb_rush_yards", "rb_rush_tds", "rb_receptions",
            "wr1_recv_yards", "wr1_receptions", "wr1_recv_tds",
        )
        for side in ("home", "away"):
            for key in expected_keys:
                assert key in result[side], f"Missing key '{key}' in '{side}'"

    def test_higher_xpts_more_pass_yards(self):
        high = nfl_player_props(35.0, 17.0)
        low  = nfl_player_props(10.0, 17.0)
        assert high["home"]["qb_pass_yards"] > low["home"]["qb_pass_yards"]

    def test_all_values_non_negative(self):
        r = nfl_player_props(10.0, 10.0)
        for side in ("home", "away"):
            for val in r[side].values():
                assert val >= 0

    def test_pass_yards_greater_than_rush_yards(self):
        """Historically, NFL teams pass more than they rush in yards."""
        r = nfl_player_props(22.0, 22.0)
        for side in ("home", "away"):
            assert r[side]["qb_pass_yards"] > r[side]["rb_rush_yards"]

    def test_proportional_scaling(self):
        """Double the expected points → double the prop lines."""
        base = nfl_player_props(22.0, 22.0)
        double = nfl_player_props(44.0, 44.0)
        assert abs(
            double["home"]["qb_pass_yards"] / base["home"]["qb_pass_yards"] - 2.0
        ) < 0.05

    def test_wr1_yards_greater_than_rb_receptions_yards_typically(self):
        """WR1 recv yards typically exceed RB reception yards."""
        r = nfl_player_props(22.0, 22.0)
        for side in ("home", "away"):
            # WR1 avg 78 yd/gm vs RB avg reception-yards much lower
            assert r[side]["wr1_recv_yards"] > r[side]["rb_receptions"]
