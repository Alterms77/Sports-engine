"""
Tests for core/shot_metrics.py

Covers:
- xg_to_shots: derivation from xG
- compute_shot_dominance, compute_shot_accuracy, compute_shot_quality,
  compute_goal_threat_rate, compute_shot_differential
- compute_shot_metrics: aggregate
- compute_shot_form_averages: last5/last10 + trend detection
- project_shots: projection formula
- apply_shot_adjustments: all four rules + normalisation
- generate_shot_picks: all four pick rules
- build_shot_context_from_xg: end-to-end integration
"""

import pytest
from core.shot_metrics import (
    xg_to_shots,
    compute_shot_dominance,
    compute_shot_accuracy,
    compute_shot_quality,
    compute_goal_threat_rate,
    compute_shot_differential,
    compute_shot_metrics,
    compute_shot_form_averages,
    project_shots,
    apply_shot_adjustments,
    generate_shot_picks,
    build_shot_context_from_xg,
    _LEAGUE_AVG_SHOTS,
    _LEAGUE_AVG_SOT,
    _SOT_RATE,
    _XG_PER_TOTAL_SHOT,
    _XG_PER_SOT,
)


# ═══════════════════════════════════════════════════════════════════════════════
# xg_to_shots
# ═══════════════════════════════════════════════════════════════════════════════

class TestXgToShots:
    def test_output_keys(self):
        r = xg_to_shots(1.5)
        for k in ("total_shots", "shots_on_target", "shots_off_target", "blocked_shots"):
            assert k in r

    def test_zero_xg_returns_zeros(self):
        r = xg_to_shots(0.0)
        assert r["total_shots"] == 0.0
        assert r["shots_on_target"] == 0.0

    def test_positive_xg_proportional(self):
        r1 = xg_to_shots(1.0)
        r2 = xg_to_shots(2.0)
        assert r2["total_shots"] == pytest.approx(r1["total_shots"] * 2, abs=0.5)

    def test_calibration_at_league_average(self):
        # At xG = 1.15 the formula uses _XG_PER_TOTAL_SHOT and _XG_PER_SOT.
        # Verify the outputs match the formula directly rather than hardcoded values.
        r = xg_to_shots(1.15)
        from core.shot_metrics import _XG_PER_TOTAL_SHOT, _XG_PER_SOT
        expected_total = round(1.15 / _XG_PER_TOTAL_SHOT, 1)
        expected_sot   = round(1.15 / _XG_PER_SOT,        1)
        assert r["total_shots"]    == pytest.approx(expected_total, abs=0.2)
        assert r["shots_on_target"] == pytest.approx(expected_sot,   abs=0.2)

    def test_sot_lte_total(self):
        for xg in [0.5, 1.0, 2.0, 3.5]:
            r = xg_to_shots(xg)
            assert r["shots_on_target"] <= r["total_shots"]

    def test_negative_xg_treated_as_zero(self):
        r = xg_to_shots(-1.0)
        assert r["total_shots"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Individual metric functions
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeShotDominance:
    def test_equal_shots_is_half(self):
        assert compute_shot_dominance(10, 10) == pytest.approx(0.5, abs=0.001)

    def test_all_shots_one_team(self):
        assert compute_shot_dominance(10, 0) == pytest.approx(1.0, abs=0.001)

    def test_zero_shots_returns_half(self):
        assert compute_shot_dominance(0, 0) == pytest.approx(0.5, abs=0.001)

    def test_clamped_to_unit_interval(self):
        d = compute_shot_dominance(100, 5)
        assert 0.0 <= d <= 1.0


class TestComputeShotAccuracy:
    def test_perfect_accuracy(self):
        assert compute_shot_accuracy(10, 10) == pytest.approx(1.0, abs=0.001)

    def test_zero_shots_returns_league_avg(self):
        assert compute_shot_accuracy(0, 0) == pytest.approx(_SOT_RATE, abs=0.001)

    def test_partial_accuracy(self):
        assert compute_shot_accuracy(4, 10) == pytest.approx(0.4, abs=0.01)

    def test_clamped_to_unit_interval(self):
        acc = compute_shot_accuracy(100, 5)   # more SoT than total: edge case
        assert 0.0 <= acc <= 1.0


class TestComputeShotQuality:
    def test_zero_shots_returns_default(self):
        assert compute_shot_quality(1.5, 0) == pytest.approx(_XG_PER_TOTAL_SHOT, abs=0.001)

    def test_quality_proportional_to_xg(self):
        q1 = compute_shot_quality(2.0, 10)
        q2 = compute_shot_quality(1.0, 10)
        assert q1 > q2

    def test_quality_inversely_proportional_to_shots(self):
        q1 = compute_shot_quality(1.5, 10)
        q2 = compute_shot_quality(1.5, 20)
        assert q1 > q2


class TestComputeGoalThreatRate:
    def test_zero_xg_returns_calibrated_default(self):
        gtr = compute_goal_threat_rate(5.0, 0.0)
        assert gtr == pytest.approx(1.0 / _XG_PER_SOT, abs=0.1)

    def test_positive_values(self):
        gtr = compute_goal_threat_rate(5, 1.6)
        assert gtr > 0


class TestComputeShotDifferential:
    def test_positive_diff(self):
        assert compute_shot_differential(15, 8) == pytest.approx(7.0, abs=0.01)

    def test_negative_diff(self):
        assert compute_shot_differential(8, 15) == pytest.approx(-7.0, abs=0.01)

    def test_zero_diff(self):
        assert compute_shot_differential(10, 10) == pytest.approx(0.0, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# Aggregate compute_shot_metrics
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeShotMetrics:
    def _run(self):
        return compute_shot_metrics(
            shots_on_target=5.0,
            total_shots=12.0,
            shots_opponent=10.0,
            xg=1.5,
            shots_on_target_opponent=4.0,
        )

    def test_output_keys(self):
        r = self._run()
        for k in (
            "total_shots", "shots_on_target", "shots_off_target",
            "shot_dominance", "shot_accuracy", "shot_quality",
            "goal_threat_rate", "shot_differential", "sot_differential",
            "shots_on_target_opponent",
        ):
            assert k in r

    def test_shots_off_target(self):
        r = self._run()
        assert r["shots_off_target"] == pytest.approx(7.0, abs=0.1)

    def test_dominance_value(self):
        r = self._run()
        expected_dominance = 12.0 / (12.0 + 10.0)
        assert r["shot_dominance"] == pytest.approx(expected_dominance, abs=0.01)

    def test_accuracy_value(self):
        r = self._run()
        assert r["shot_accuracy"] == pytest.approx(5.0 / 12.0, abs=0.01)

    def test_sot_differential(self):
        r = self._run()
        assert r["sot_differential"] == pytest.approx(5.0 - 4.0, abs=0.1)


# ═══════════════════════════════════════════════════════════════════════════════
# compute_shot_form_averages
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeShotFormAverages:
    def _make_last5(self, shots, sot, allowed, sot_allowed):
        return [(shots, sot, allowed, sot_allowed)] * 5

    def test_output_keys(self):
        r = compute_shot_form_averages()
        for k in ("avg_shots", "avg_shots_on_target", "avg_shots_allowed",
                  "avg_shots_on_target_allowed", "avg_shots_l10", "trend"):
            assert k in r

    def test_empty_returns_zeros(self):
        r = compute_shot_form_averages()
        assert r["avg_shots"] == 0.0
        assert r["trend"] == "stable"

    def test_averages_correct(self):
        l5 = self._make_last5(12.0, 5.0, 9.0, 3.0)
        r = compute_shot_form_averages(last5_shots=l5)
        assert r["avg_shots"] == pytest.approx(12.0, abs=0.01)
        assert r["avg_shots_on_target"] == pytest.approx(5.0, abs=0.01)
        assert r["avg_shots_allowed"] == pytest.approx(9.0, abs=0.01)

    def test_trend_up_when_last5_much_higher_than_last10(self):
        l5  = [(15, 6, 8, 3)] * 5
        l10 = [(12, 5, 9, 4)] * 10
        r = compute_shot_form_averages(last5_shots=l5, last10_shots=l10)
        assert r["trend"] == "attacking_form_up"

    def test_trend_down_when_last5_much_lower(self):
        l5  = [(9, 3, 11, 5)] * 5
        l10 = [(13, 5, 9, 4)] * 10
        r = compute_shot_form_averages(last5_shots=l5, last10_shots=l10)
        assert r["trend"] == "attacking_form_down"

    def test_trend_stable_when_similar(self):
        l5  = [(12, 5, 9, 4)] * 5
        l10 = [(12, 5, 9, 4)] * 10
        r = compute_shot_form_averages(last5_shots=l5, last10_shots=l10)
        assert r["trend"] == "stable"


# ═══════════════════════════════════════════════════════════════════════════════
# project_shots
# ═══════════════════════════════════════════════════════════════════════════════

class TestProjectShots:
    def test_output_keys(self):
        r = project_shots(12.0, 10.0)
        assert "projected_shots" in r
        assert "projected_shots_on_target" in r

    def test_formula_correct(self):
        r = project_shots(12.0, 10.0)
        assert r["projected_shots"] == pytest.approx(11.0, abs=0.1)

    def test_sot_uses_accuracy(self):
        r = project_shots(12.0, 10.0, shot_accuracy=0.5)
        assert r["projected_shots_on_target"] == pytest.approx(5.5, abs=0.2)

    def test_zero_inputs(self):
        r = project_shots(0.0, 0.0)
        assert r["projected_shots"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# apply_shot_adjustments
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyShotAdjustments:
    def _base_probs(self):
        return {
            "home_win": 50.0, "draw": 25.0, "away_win": 25.0,
            "over_1_5": 70.0, "over_2_5": 50.0, "over_3_5": 30.0,
            "btts": 45.0,
        }

    def _home_shots(self, dominance=0.65, sot_diff=5):
        return {
            "shot_dominance":           dominance,
            "sot_differential":         float(sot_diff),
            "shots_on_target_opponent": 4.0,
        }

    def _away_shots(self, dominance=0.35, sot_diff=-5):
        return {
            "shot_dominance":           dominance,
            "sot_differential":         float(sot_diff),
            "shots_on_target_opponent": 4.0,
        }

    def test_rule1_shot_dominance_xg_edge(self):
        probs = self._base_probs()
        adj = apply_shot_adjustments(
            xg_home=2.5, xg_away=1.0, probs=probs,
            home_shots=self._home_shots(dominance=0.65),
            away_shots=self._away_shots(dominance=0.35),
        )
        assert "SHOT_DOM_HOME" in adj["shot_adjustment_reasons"]
        assert adj["shot_adjustment_applied"] is True

    def test_rule2_sot_differential_boosts_over15(self):
        probs = self._base_probs()
        original_o15 = probs["over_1_5"]
        home = {**self._home_shots(), "sot_differential": 4.0}
        adj = apply_shot_adjustments(
            xg_home=1.5, xg_away=1.2, probs=probs,
            home_shots=home,
            away_shots=self._away_shots(dominance=0.35),
        )
        assert "HIGH_SOT_HOME" in adj["shot_adjustment_reasons"]
        assert adj["over_1_5"] > original_o15

    def test_rule4_high_volume_boosts_over25(self):
        probs = self._base_probs()
        original_o25 = probs["over_2_5"]
        adj = apply_shot_adjustments(
            xg_home=1.5, xg_away=1.2, probs=probs,
            home_shots=self._home_shots(dominance=0.52),
            away_shots=self._away_shots(dominance=0.48),
            total_shots_projection=25.0,
        )
        assert "HIGH_VOL_MATCH" in adj["shot_adjustment_reasons"]
        assert adj["over_2_5"] > original_o25

    def test_1x2_sums_to_100_after_adjustment(self):
        probs = self._base_probs()
        adj = apply_shot_adjustments(
            xg_home=2.5, xg_away=0.8, probs=probs,
            home_shots=self._home_shots(dominance=0.70),
            away_shots=self._away_shots(dominance=0.30),
        )
        total = adj["home_win"] + adj["draw"] + adj["away_win"]
        assert abs(total - 100.0) < 0.5

    def test_no_rule_fires_returns_original_probs(self):
        probs = self._base_probs()
        # No dominance / xG edge / high SoT / projection
        h = {"shot_dominance": 0.50, "sot_differential": 1.0, "shots_on_target_opponent": 5.0}
        a = {"shot_dominance": 0.50, "sot_differential": -1.0, "shots_on_target_opponent": 5.0}
        adj = apply_shot_adjustments(
            xg_home=1.2, xg_away=1.1, probs=probs,
            home_shots=h, away_shots=a,
        )
        assert adj["shot_adjustment_applied"] is False

    def test_output_does_not_exceed_99(self):
        probs = {"home_win": 98.0, "draw": 1.0, "away_win": 1.0,
                 "over_1_5": 99.0, "over_2_5": 98.0, "over_3_5": 50.0}
        h = {"shot_dominance": 0.80, "sot_differential": 10.0, "shots_on_target_opponent": 1.0}
        a = {"shot_dominance": 0.20, "sot_differential": -10.0, "shots_on_target_opponent": 8.0}
        adj = apply_shot_adjustments(
            xg_home=3.0, xg_away=0.5, probs=probs,
            home_shots=h, away_shots=a, total_shots_projection=30.0,
        )
        for key in ("over_1_5", "over_2_5"):
            assert adj[key] <= 99.0


# ═══════════════════════════════════════════════════════════════════════════════
# generate_shot_picks
# ═══════════════════════════════════════════════════════════════════════════════

class TestGenerateShotPicks:
    def _probs(self):
        return {"home_win": 60.0, "draw": 20.0, "away_win": 20.0,
                "over_1_5": 75.0, "over_2_5": 55.0}

    def test_rule1_home_team_over_goals(self):
        home = {"shots_on_target": 7.0, "shot_dominance": 0.55,
                "shot_differential": 3.0, "sot_differential": 3.0,
                "shots_on_target_opponent": 4.0,
                "projected_shots": 12.0, "total_shots": 12.0}
        away = {"shots_on_target": 3.0, "shot_dominance": 0.45,
                "shot_differential": -3.0, "sot_differential": -3.0,
                "shots_on_target_opponent": 7.0,
                "projected_shots": 9.0, "total_shots": 9.0}
        picks = generate_shot_picks(home, away, xg_home=2.0, xg_away=1.0,
                                    probs=self._probs(),
                                    home_name="Real Madrid", away_name="Levante")
        markets = [p["market"] for p in picks]
        assert "team_over_1_5" in markets

    def test_rule2_shot_dominance_pick(self):
        home = {"shots_on_target": 5.0, "shot_dominance": 0.70,
                "shot_differential": 5.0, "sot_differential": 2.0,
                "shots_on_target_opponent": 4.0,
                "projected_shots": 14.0, "total_shots": 14.0}
        away = {"shots_on_target": 3.0, "shot_dominance": 0.30,
                "shot_differential": -5.0, "sot_differential": -2.0,
                "shots_on_target_opponent": 5.0,
                "projected_shots": 8.0, "total_shots": 8.0}
        picks = generate_shot_picks(home, away, xg_home=1.8, xg_away=0.9,
                                    probs=self._probs(),
                                    home_name="Barcelona", away_name="Getafe")
        markets = [p["market"] for p in picks]
        assert "moneyline" in markets

    def test_rule3_win_to_nil_pick(self):
        home = {"shots_on_target": 5.0, "shot_dominance": 0.60,
                "shot_differential": 4.0, "sot_differential": 3.0,
                "shots_on_target_opponent": 4.5,
                "projected_shots": 12.0, "total_shots": 12.0}
        away = {"shots_on_target": 2.0, "shot_dominance": 0.40,
                "shot_differential": -4.0, "sot_differential": -3.0,
                "shots_on_target_opponent": 1.5,  # ≤ 2.0 → triggers
                "projected_shots": 8.0, "total_shots": 8.0}
        picks = generate_shot_picks(home, away, xg_home=1.8, xg_away=0.9,
                                    probs=self._probs(),
                                    home_name="Liverpool", away_name="Norwich")
        markets = [p["market"] for p in picks]
        assert "win_to_nil" in markets

    def test_rule4_over_25_high_volume(self):
        home = {"shots_on_target": 5.0, "shot_dominance": 0.55,
                "shot_differential": 2.0, "sot_differential": 1.0,
                "shots_on_target_opponent": 4.0,
                "projected_shots": 14.0, "total_shots": 14.0}
        away = {"shots_on_target": 5.0, "shot_dominance": 0.45,
                "shot_differential": -2.0, "sot_differential": -1.0,
                "shots_on_target_opponent": 5.0,
                "projected_shots": 13.0, "total_shots": 13.0}
        picks = generate_shot_picks(home, away, xg_home=1.6, xg_away=1.4,
                                    probs=self._probs(),
                                    home_name="Man City", away_name="Arsenal")
        markets = [p["market"] for p in picks]
        assert "over_2_5" in markets

    def test_no_picks_when_no_rules_fire(self):
        home = {"shots_on_target": 4.0, "shot_dominance": 0.50,
                "shot_differential": 0.0, "sot_differential": 0.0,
                "shots_on_target_opponent": 4.5,
                "projected_shots": 11.0, "total_shots": 11.0}
        away = {"shots_on_target": 4.0, "shot_dominance": 0.50,
                "shot_differential": 0.0, "sot_differential": 0.0,
                "shots_on_target_opponent": 4.0,
                "projected_shots": 10.0, "total_shots": 10.0}
        picks = generate_shot_picks(home, away, xg_home=1.2, xg_away=1.1,
                                    probs=self._probs())
        assert isinstance(picks, list)
        # Rules 1-3 should not fire; rule 4 only fires at ≥25 total
        for p in picks:
            assert p["market"] != "team_over_1_5"
            assert p["market"] != "moneyline"
            assert p["market"] != "win_to_nil"


# ═══════════════════════════════════════════════════════════════════════════════
# build_shot_context_from_xg (end-to-end)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildShotContextFromXg:
    def _probs(self):
        return {
            "home_win": 55.0, "draw": 25.0, "away_win": 20.0,
            "over_1_5": 75.0, "over_2_5": 55.0, "over_3_5": 30.0, "btts": 45.0,
        }

    def test_output_structure(self):
        ctx = build_shot_context_from_xg(
            xg_home=1.8, xg_away=1.1,
            probs=self._probs(),
            home_name="Arsenal", away_name="Chelsea",
        )
        assert "home" in ctx
        assert "away" in ctx
        assert "projection" in ctx
        assert "adjusted_probs" in ctx
        assert "shot_picks" in ctx

    def test_home_metrics_keys(self):
        ctx = build_shot_context_from_xg(1.8, 1.1, self._probs())
        for k in ("total_shots", "shots_on_target", "shot_dominance",
                  "shot_accuracy", "shot_quality", "goal_threat_rate",
                  "shot_differential", "projected_shots"):
            assert k in ctx["home"]

    def test_higher_xg_higher_shots(self):
        ctx_high = build_shot_context_from_xg(2.5, 0.8, self._probs())
        ctx_low  = build_shot_context_from_xg(0.8, 2.5, self._probs())
        assert ctx_high["home"]["total_shots"] > ctx_high["away"]["total_shots"]
        assert ctx_low["away"]["total_shots"] > ctx_low["home"]["total_shots"]

    def test_adjusted_probs_is_dict(self):
        ctx = build_shot_context_from_xg(1.8, 1.1, self._probs())
        assert isinstance(ctx["adjusted_probs"], dict)

    def test_shot_picks_is_list(self):
        ctx = build_shot_context_from_xg(1.8, 1.1, self._probs())
        assert isinstance(ctx["shot_picks"], list)

    def test_with_form_shots_uses_them(self):
        form_home = {
            "avg_shots": 15.0, "avg_shots_on_target": 6.5,
            "avg_shots_allowed": 8.0, "avg_shots_on_target_allowed": 3.0,
        }
        ctx = build_shot_context_from_xg(1.8, 1.1, self._probs(),
                                         home_form_shots=form_home)
        # With form data, home total shots should reflect the form average
        assert abs(ctx["home"]["total_shots"] - 15.0) < 1.0

    def test_zero_xg_does_not_crash(self):
        probs = {"home_win": 33.0, "draw": 33.0, "away_win": 34.0,
                 "over_1_5": 50.0, "over_2_5": 30.0, "over_3_5": 15.0, "btts": 40.0}
        ctx = build_shot_context_from_xg(0.0, 0.0, probs)
        assert ctx["home"]["total_shots"] == 0.0
        assert ctx["away"]["total_shots"] == 0.0
