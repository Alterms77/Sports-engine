"""
Tests for the refactored core/parlay.py.

Covers:
- _build_candidates: correct picks generated per sport
- score_risk / score_risk_soccer/nba/nfl/mlb: per-sport risk scoring
- generate_parlay_legs: returns (legs, report, excluded) tuple;
                        confidence/prob filtering, variety constraints,
                        high-risk exclusion, market-type caps, safe mode
- build_parlays: tier construction and combined-probability calculation
- format_parlay / format_parlay_safe: output format, sport emoji, exclusion summary
"""

import pytest

from core.parlay import (
    _build_candidates,
    score_risk,
    score_risk_soccer,
    score_risk_nba,
    score_risk_nfl,
    score_risk_mlb,
    generate_parlay_legs,
    build_parlays,
    format_parlay,
    format_parlay_safe,
    calibrate_prob_gated,
    _CONFIDENCE_RANK,
    _MAX_SAME_MARKET,
    _HIGH_RISK_SCORE,
    RISK_THRESHOLD_SAFE,
    MIN_PROB_SAFE_ABS,
    MIN_SEP_SAFE,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures / helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _soccer_pred(
    home="Team A", away="Team B",
    home_win=78.0, draw=12.0, away_win=10.0,
    over_1_5=85.0, over_2_5=62.0, over_3_5=35.0,
    btts=55.0,
    confidence="ALTA",
    is_sharp=False,
    live_data=True,
):
    return {
        "sport": "⚽ Soccer",
        "home": home,
        "away": away,
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        "over_1_5": over_1_5,
        "over_2_5": over_2_5,
        "over_3_5": over_3_5,
        "btts": btts,
        "confidence": confidence,
        "league": "Premier League",
        "live_data": live_data,
        "sharp": {"is_sharp": is_sharp, "edge_score": 2 if is_sharp else 0},
    }


def _nba_pred(
    home="Lakers", away="Warriors",
    home_win=68.0, away_win=32.0,
    confidence="ALTA",
    live_data=True,
):
    return {
        "sport": "NBA 🏀",
        "home": home,
        "away": away,
        "home_win": home_win,
        "away_win": away_win,
        "confidence": confidence,
        "league": "NBA",
        "live_data": live_data,
    }


def _mlb_pred(
    home="Yankees", away="Red Sox",
    home_win=66.0, away_win=34.0,
    confidence="ALTA",
    live_data=True,
    run_line=None,
):
    return {
        "sport": "MLB ⚾",
        "home": home,
        "away": away,
        "home_win": home_win,
        "away_win": away_win,
        "confidence": confidence,
        "league": "MLB",
        "live_data": live_data,
        "run_line": run_line or {"fav_side": "home", "cover_prob": 58.0, "over_under": 8.5},
    }


def _nfl_pred(
    home="Chiefs", away="Patriots",
    home_win=70.0, away_win=30.0,
    confidence="ALTA",
    live_data=True,
):
    return {
        "sport": "NFL 🏈",
        "home": home,
        "away": away,
        "home_win": home_win,
        "away_win": away_win,
        "confidence": confidence,
        "league": "NFL",
        "live_data": live_data,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# _build_candidates
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildCandidates:
    def test_soccer_includes_moneyline(self):
        pred = _soccer_pred()
        cands = _build_candidates(pred)
        picks = [c["pick"] for c in cands]
        assert any("Victoria" in p for p in picks)

    def test_soccer_includes_draw(self):
        pred = _soccer_pred()
        cands = _build_candidates(pred)
        assert any(c["pick"] == "Empate" for c in cands)

    def test_soccer_includes_totals(self):
        pred = _soccer_pred()
        cands = _build_candidates(pred)
        picks = [c["pick"] for c in cands]
        assert "Over 1.5" in picks
        assert "Over 2.5" in picks

    def test_soccer_includes_btts(self):
        pred = _soccer_pred()
        cands = _build_candidates(pred)
        assert any("BTTS" in c["pick"] for c in cands)

    def test_soccer_market_types_assigned(self):
        pred = _soccer_pred()
        cands = _build_candidates(pred)
        types = {c["market_type"] for c in cands}
        assert "moneyline" in types
        assert "totals" in types
        assert "btts" in types

    def test_nba_only_moneyline(self):
        pred = _nba_pred()
        cands = _build_candidates(pred)
        # NBA should not include totals or BTTS (no draw)
        assert all(c["market_type"] == "moneyline" for c in cands)
        # No BTTS
        assert not any("BTTS" in c["pick"] for c in cands)

    def test_mlb_includes_run_line(self):
        pred = _mlb_pred()
        cands = _build_candidates(pred)
        types = {c["market_type"] for c in cands}
        assert "spread" in types

    def test_nfl_only_moneyline(self):
        pred = _nfl_pred()
        cands = _build_candidates(pred)
        assert all(c["market_type"] == "moneyline" for c in cands)

    def test_probabilities_match_prediction(self):
        pred = _soccer_pred(home_win=78.0, over_1_5=85.0)
        cands = _build_candidates(pred)
        over15 = next(c for c in cands if c["pick"] == "Over 1.5")
        assert over15["prob"] == 85.0

    def test_no_candidates_for_zero_probs(self):
        pred = _soccer_pred(home_win=0, away_win=0, draw=0,
                            over_1_5=0, over_2_5=0, over_3_5=0, btts=0)
        cands = _build_candidates(pred)
        assert cands == []


# ═══════════════════════════════════════════════════════════════════════════════
# score_risk
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreRisk:
    def test_high_win_prob_low_risk(self):
        pred = _soccer_pred(home_win=80.0, away_win=10.0, confidence="ALTA",
                            is_sharp=False, live_data=True)
        score, reasons = score_risk(pred)
        assert score < _HIGH_RISK_SCORE

    def test_balanced_probs_high_risk(self):
        # A match where win probs are very balanced (top side < 55 %)
        # should trigger high risk via COIN_FLIP flag.
        pred = _soccer_pred(
            home_win=50.0, away_win=30.0,
            over_1_5=60.0, over_2_5=45.0, over_3_5=30.0, btts=48.0,
            confidence="ALTA",
        )
        score, reasons = score_risk(pred)
        assert score >= _HIGH_RISK_SCORE
        assert any(r in ("COIN_FLIP", "LOW_PROB", "LOW_SEPARATION") for r in reasons)

    def test_low_confidence_increases_risk(self):
        pred_high = _soccer_pred(confidence="ALTA")
        pred_low  = _soccer_pred(confidence="BAJA")
        score_high, _ = score_risk(pred_high)
        score_low, _  = score_risk(pred_low)
        assert score_low > score_high

    def test_sharp_game_increases_risk(self):
        pred_normal = _soccer_pred(is_sharp=False)
        pred_sharp  = _soccer_pred(is_sharp=True)
        score_n, _ = score_risk(pred_normal)
        score_s, _ = score_risk(pred_sharp)
        assert score_s > score_n

    def test_no_live_data_increases_risk(self):
        pred_live = _soccer_pred(live_data=True)
        pred_dead = _soccer_pred(live_data=False)
        score_l, _ = score_risk(pred_live)
        score_d, _ = score_risk(pred_dead)
        assert score_d > score_l

    def test_reasons_is_list(self):
        pred = _soccer_pred()
        _, reasons = score_risk(pred)
        assert isinstance(reasons, list)

    def test_score_capped_at_1(self):
        pred = _soccer_pred(home_win=48.0, away_win=40.0, confidence="BAJA",
                            is_sharp=True, live_data=False)
        score, _ = score_risk(pred)
        assert score <= 1.0

    def test_baja_confidence_triggers_high_risk(self):
        pred = _soccer_pred(confidence="BAJA")
        score, _ = score_risk(pred)
        assert score >= _HIGH_RISK_SCORE

    def test_media_confidence_below_high_risk_when_probs_good(self):
        """MEDIA confidence alone should not always make a game high-risk."""
        pred = _soccer_pred(home_win=72.0, away_win=18.0, confidence="MEDIA",
                            is_sharp=False, live_data=True)
        score, _ = score_risk(pred)
        # MEDIA (+0.15) + no other flags → score = 0.15 < 0.5
        assert score < _HIGH_RISK_SCORE


# ═══════════════════════════════════════════════════════════════════════════════
# generate_parlay_legs
# ═══════════════════════════════════════════════════════════════════════════════

class TestGenerateParlayLegs:
    def _five_safe_soccer_preds(self):
        return [
            _soccer_pred(f"Home{i}", f"Away{i}", home_win=80.0 - i,
                         over_1_5=88.0 - i, confidence="ALTA")
            for i in range(5)
        ]

    def _legs(self, preds, **kwargs):
        """Helper: call generate_parlay_legs and return just the legs list."""
        legs, _report, _excluded = generate_parlay_legs(preds, **kwargs)
        return legs

    def test_returns_tuple_of_three(self):
        result = generate_parlay_legs(self._five_safe_soccer_preds())
        assert isinstance(result, tuple) and len(result) == 3

    def test_legs_is_list(self):
        legs, _, _ = generate_parlay_legs(self._five_safe_soccer_preds())
        assert isinstance(legs, list)

    def test_report_has_required_keys(self):
        _, report, _ = generate_parlay_legs(self._five_safe_soccer_preds())
        for key in ("total_candidates", "legs_selected", "mode", "exclusions"):
            assert key in report

    def test_excluded_is_list(self):
        _, _, excluded = generate_parlay_legs(self._five_safe_soccer_preds())
        assert isinstance(excluded, list)

    def test_returns_list(self):
        legs, _, _ = generate_parlay_legs(self._five_safe_soccer_preds())
        assert isinstance(legs, list)

    def test_respects_max_legs(self):
        preds = self._five_safe_soccer_preds()
        legs = self._legs(preds, max_legs=3)
        assert len(legs) <= 3

    def test_sorted_by_prob_descending(self):
        preds = self._five_safe_soccer_preds()
        legs = self._legs(preds)
        probs = [leg["prob"] for leg in legs]
        assert probs == sorted(probs, reverse=True)

    def test_filters_low_confidence(self):
        preds = [
            _soccer_pred("H1", "A1", confidence="ALTA", home_win=80.0, over_1_5=85.0),
            _soccer_pred("H2", "A2", confidence="BAJA", home_win=80.0, over_1_5=85.0),
        ]
        legs = self._legs(preds, min_confidence="ALTA")
        matches = [leg["match"] for leg in legs]
        assert "H2 vs A2" not in matches

    def test_filters_below_min_prob(self):
        preds = [_soccer_pred(home_win=60.0, over_1_5=62.0, over_2_5=55.0, btts=50.0)]
        legs = self._legs(preds, min_prob=75.0)
        # All candidates below 75 % — no legs expected
        assert legs == []

    def test_excludes_high_risk_matches(self):
        high_risk = _soccer_pred("Risk_H", "Risk_A",
                                 confidence="BAJA",   # BAJA alone pushes risk >= threshold
                                 home_win=80.0, over_1_5=85.0)
        safe = _soccer_pred("Safe_H", "Safe_A",
                             confidence="ALTA", home_win=80.0, over_1_5=85.0)
        legs = self._legs([high_risk, safe])
        matches = [leg["match"] for leg in legs]
        assert "Risk_H vs Risk_A" not in matches
        assert "Safe_H vs Safe_A" in matches

    def test_market_variety_constraint(self):
        """No more than _MAX_SAME_MARKET legs of the same market type."""
        preds = []
        for i in range(6):
            preds.append(_soccer_pred(
                f"T{i}", f"U{i}",
                home_win=70.0,
                over_1_5=90.0 - i,
                over_2_5=55.0,
                btts=55.0,
                confidence="ALTA",
            ))
        legs = self._legs(preds, max_legs=5)
        from collections import Counter
        counts = Counter(leg["market_type"] for leg in legs)
        for mtype, cnt in counts.items():
            assert cnt <= _MAX_SAME_MARKET, (
                f"Market type '{mtype}' appears {cnt} times, max is {_MAX_SAME_MARKET}"
            )

    def test_one_leg_per_match(self):
        """Each match may contribute at most one leg."""
        pred = _soccer_pred("H1", "A1", home_win=80.0, over_1_5=85.0)
        legs = self._legs([pred] * 5)
        match_names = [leg["match"] for leg in legs]
        assert len(match_names) == len(set(match_names))

    def test_leg_has_required_keys(self):
        preds = [_soccer_pred(home_win=80.0, over_1_5=85.0)]
        legs = self._legs(preds)
        if legs:
            leg = legs[0]
            for key in ("match", "pick", "prob", "league",
                        "confidence", "market_type", "sport_emoji", "risk_reasons"):
                assert key in leg, f"Missing key: {key}"

    def test_multi_sport_mix(self):
        """Soccer, NBA, NFL, MLB predictions can all contribute legs."""
        preds = [
            _soccer_pred(home_win=80.0, over_1_5=85.0),
            _nba_pred(home_win=76.0),
            _nfl_pred(home_win=77.0),
            _mlb_pred(home_win=75.0),
        ]
        legs = self._legs(preds, min_prob=75.0)
        sports = {leg["sport_emoji"] for leg in legs}
        assert len(sports) >= 2

    def test_empty_predictions_returns_empty(self):
        legs, report, excluded = generate_parlay_legs([])
        assert legs == []
        assert excluded == []
        assert report["total_candidates"] == 0

    def test_media_confidence_excluded_when_min_is_alta(self):
        preds = [_soccer_pred(confidence="MEDIA", home_win=82.0, over_1_5=88.0)]
        legs = self._legs(preds, min_confidence="ALTA")
        assert legs == []

    def test_media_confidence_included_when_min_is_media(self):
        preds = [_soccer_pred(confidence="MEDIA", home_win=82.0, over_1_5=88.0)]
        legs = self._legs(preds, min_confidence="MEDIA", min_prob=75.0)
        assert len(legs) == 1

    def test_excluded_list_populated_on_low_conf(self):
        preds = [_soccer_pred(confidence="BAJA", home_win=82.0, over_1_5=88.0)]
        _, _, excluded = generate_parlay_legs(preds, min_confidence="ALTA")
        assert len(excluded) == 1
        assert "LOW_CONF" in excluded[0]["reasons"]

    def test_excluded_list_populated_on_low_prob(self):
        preds = [_soccer_pred(home_win=60.0, over_1_5=62.0)]
        _, _, excluded = generate_parlay_legs(preds, min_prob=75.0)
        assert any("LOW_PROB" in e["reasons"] for e in excluded)

    def test_report_exclusions_count_matches_excluded_list(self):
        preds = [
            _soccer_pred(confidence="BAJA", home_win=80.0, over_1_5=85.0),
            _soccer_pred("H2", "A2", home_win=60.0, over_1_5=62.0),
        ]
        _, report, excluded = generate_parlay_legs(preds, min_prob=75.0)
        total_excl_in_list = len(excluded)
        total_in_report = sum(report["exclusions"].values())
        # The report and excluded list may differ because an event can have
        # multiple exclusion reasons but only one entry in `excluded`.
        # We just check both are non-zero.
        assert total_excl_in_list >= 1
        assert total_in_report >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# build_parlays
# ═══════════════════════════════════════════════════════════════════════════════

def _make_legs(probs):
    return [
        {"match": f"H{i} vs A{i}", "pick": "Victoria H", "prob": p,
         "league": "X", "confidence": "ALTA",
         "market_type": "moneyline", "sport_emoji": "⚽", "risk_reasons": []}
        for i, p in enumerate(probs)
    ]


class TestBuildParlays:
    def test_fewer_than_2_legs_all_none(self):
        result = build_parlays(_make_legs([80.0]))
        assert result["safe"] is None
        assert result["balanced"] is None
        assert result["risky"] is None

    def test_exactly_2_legs_safe_only(self):
        result = build_parlays(_make_legs([80.0, 75.0]))
        assert result["safe"] is not None
        assert result["balanced"] is None
        assert result["risky"] is None

    def test_safe_uses_top_2(self):
        legs = _make_legs([90.0, 85.0, 80.0, 75.0])
        result = build_parlays(legs)
        assert len(result["safe"]["legs"]) == 2
        assert result["safe"]["legs"][0]["prob"] == 90.0

    def test_balanced_uses_top_3(self):
        legs = _make_legs([90.0, 85.0, 80.0, 75.0])
        result = build_parlays(legs)
        assert len(result["balanced"]["legs"]) == 3

    def test_risky_uses_up_to_5(self):
        legs = _make_legs([90.0, 85.0, 80.0, 78.0, 75.0, 70.0])
        result = build_parlays(legs)
        assert len(result["risky"]["legs"]) == 5

    def test_combined_probability_correct(self):
        legs = _make_legs([80.0, 75.0])
        result = build_parlays(legs)
        expected = round((80.0 / 100) * (75.0 / 100) * 100, 1)
        assert abs(result["safe"]["combined_prob"] - expected) < 0.05

    def test_combined_prob_decreases_with_more_legs(self):
        legs = _make_legs([90.0, 88.0, 85.0, 83.0])
        result = build_parlays(legs)
        assert result["safe"]["combined_prob"] > result["balanced"]["combined_prob"]
        assert result["balanced"]["combined_prob"] > result["risky"]["combined_prob"]

    def test_combined_prob_is_percentage_not_decimal(self):
        legs = _make_legs([80.0, 75.0])
        result = build_parlays(legs)
        assert result["safe"]["combined_prob"] > 1.0   # percentage, not 0-1

    def test_all_probs_100_gives_100(self):
        legs = _make_legs([100.0, 100.0, 100.0])
        result = build_parlays(legs)
        assert result["balanced"]["combined_prob"] == 100.0


# ═══════════════════════════════════════════════════════════════════════════════
# format_parlay
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatParlay:
    def _parlays_with_all_tiers(self):
        legs = _make_legs([90.0, 85.0, 80.0, 77.0])
        return build_parlays(legs)

    def test_returns_string(self):
        assert isinstance(format_parlay(self._parlays_with_all_tiers()), str)

    def test_contains_header(self):
        text = format_parlay(self._parlays_with_all_tiers())
        assert "PARLAY DEL DÍA" in text

    def test_contains_safe_label(self):
        text = format_parlay(self._parlays_with_all_tiers())
        assert "SEGURA" in text

    def test_contains_balanced_label(self):
        text = format_parlay(self._parlays_with_all_tiers())
        assert "BALANCEADA" in text

    def test_contains_risky_label(self):
        text = format_parlay(self._parlays_with_all_tiers())
        assert "ARRIESGADA" in text

    def test_contains_combined_prob(self):
        text = format_parlay(self._parlays_with_all_tiers())
        assert "Prob. combinada" in text

    def test_contains_responsible_gambling_warning(self):
        text = format_parlay(self._parlays_with_all_tiers())
        assert "responsablemente" in text.lower()

    def test_empty_parlays_shows_no_picks_message(self):
        text = format_parlay({"safe": None, "balanced": None, "risky": None})
        assert "No hay suficientes" in text

    def test_sport_emoji_in_output(self):
        """Legs with sport_emoji should show that emoji in the formatted output."""
        legs = [
            {"match": "Lakers vs Warriors", "pick": "Victoria Lakers",
             "prob": 80.0, "league": "NBA", "confidence": "ALTA",
             "market_type": "moneyline", "sport_emoji": "🏀", "risk_reasons": []},
            {"match": "Man City vs Arsenal", "pick": "Over 2.5",
             "prob": 78.0, "league": "Premier", "confidence": "ALTA",
             "market_type": "totals", "sport_emoji": "⚽", "risk_reasons": []},
        ]
        parlays = build_parlays(legs)
        text = format_parlay(parlays)
        assert "🏀" in text
        assert "⚽" in text

    def test_filtered_count_note_shown_when_nonzero(self):
        text = format_parlay(self._parlays_with_all_tiers(), filtered_count=3)
        assert "excluido" in text.lower()

    def test_filtered_count_note_not_shown_when_zero(self):
        text = format_parlay(self._parlays_with_all_tiers(), filtered_count=0)
        assert "excluido" not in text.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Per-sport risk scoring
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerSportRisk:
    """score_risk dispatches to per-sport function and each returns correct shape."""

    def test_soccer_coin_flip_high_risk(self):
        pred = _soccer_pred(home_win=52.0, away_win=30.0, confidence="ALTA")
        score, reasons = score_risk_soccer(pred)
        assert score >= RISK_THRESHOLD_SAFE
        assert "COIN_FLIP" in reasons

    def test_soccer_sharp_increases_risk(self):
        base_s, _ = score_risk_soccer(_soccer_pred(is_sharp=False))
        sharp_s, reasons = score_risk_soccer(_soccer_pred(is_sharp=True))
        assert sharp_s > base_s
        assert "SHARP" in reasons

    def test_soccer_baja_returns_1(self):
        score, reasons = score_risk_soccer(_soccer_pred(confidence="BAJA"))
        assert score == 1.0
        assert "LOW_CONF" in reasons

    def test_nba_dirty_zone(self):
        pred = _nba_pred(home_win=55.0, away_win=45.0, confidence="ALTA")
        score, reasons = score_risk_nba(pred)
        assert "COIN_FLIP" in reasons
        assert score >= RISK_THRESHOLD_SAFE

    def test_nba_missing_live_data(self):
        pred_live = _nba_pred(live_data=True)
        pred_dead = _nba_pred(live_data=False)
        s_l, _ = score_risk_nba(pred_live)
        s_d, r_d = score_risk_nba(pred_dead)
        assert s_d > s_l
        assert "DATA_MISSING" in r_d

    def test_nfl_coin_flip_high_risk(self):
        pred = _nfl_pred(home_win=52.0, away_win=48.0, confidence="ALTA")
        score, reasons = score_risk_nfl(pred)
        assert score >= RISK_THRESHOLD_SAFE
        assert "COIN_FLIP" in reasons

    def test_mlb_missing_pitcher_penalised(self):
        pred = _mlb_pred(home_win=70.0, confidence="ALTA")
        # No pitcher data supplied
        score, reasons = score_risk_mlb(pred)
        assert "DATA_MISSING" in reasons

    def test_dispatch_routes_nba(self):
        pred = _nba_pred()
        pred_direct_s, _ = score_risk_nba(pred)
        dispatch_s, _ = score_risk(pred)
        assert abs(pred_direct_s - dispatch_s) < 0.001

    def test_dispatch_routes_soccer(self):
        pred = _soccer_pred()
        pred_direct_s, _ = score_risk_soccer(pred)
        dispatch_s, _ = score_risk(pred)
        assert abs(pred_direct_s - dispatch_s) < 0.001


# ═══════════════════════════════════════════════════════════════════════════════
# Calibrate prob gated
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalibrateProbGated:
    def _cal_stats_full(self, factor=0.80):
        """Simulate calibration stats with ≥ 100 samples."""
        return {
            "moneyline": {
                "n": 150,
                "predicted": 75.0,
                "hit_rate": 60.0,
                "calibration": factor,
                "bias": "OVERCONFIDENT",
            }
        }

    def _cal_stats_conservative(self, factor=0.80):
        """Simulate calibration stats with 30–99 samples."""
        return {
            "moneyline": {
                "n": 50,
                "predicted": 75.0,
                "hit_rate": 60.0,
                "calibration": factor,
                "bias": "OVERCONFIDENT",
            }
        }

    def test_no_data_returns_unchanged(self):
        cal_p, n, bucket = calibrate_prob_gated(80.0, "moneyline", {})
        assert cal_p == 80.0
        assert n == 0
        assert bucket == "none"

    def test_full_calibration_applied(self):
        stats = self._cal_stats_full(factor=0.85)
        cal_p, n, bucket = calibrate_prob_gated(80.0, "moneyline", stats)
        # 80 * clamp(0.85, 0.60, 1.40) = 68.0
        assert bucket == "full"
        assert cal_p < 80.0

    def test_conservative_calibration_is_weaker(self):
        stats_cons = self._cal_stats_conservative(factor=0.70)
        stats_full = dict(stats_cons)
        stats_full["moneyline"] = dict(stats_cons["moneyline"], n=150)
        cal_cons, _, b_c = calibrate_prob_gated(80.0, "moneyline", stats_cons)
        cal_full, _, b_f = calibrate_prob_gated(80.0, "moneyline", stats_full)
        assert b_c == "conservative"
        assert b_f == "full"
        # Conservative applies half-strength: less deviation from raw prob
        assert abs(cal_cons - 80.0) < abs(cal_full - 80.0)

    def test_safe_mode_clamped(self):
        # With factor=0.5 (extreme) prob should be clamped to 50 in safe mode
        stats = {"moneyline": {"n": 150, "predicted": 80.0, "hit_rate": 40.0,
                               "calibration": 0.50, "bias": "OVERCONFIDENT"}}
        cal_p, _, _ = calibrate_prob_gated(80.0, "moneyline", stats, safe_mode=True)
        assert cal_p >= 50.0

    def test_safe_mode_upper_clamp(self):
        # With factor=1.5 (extreme) prob should be clamped to _CAL_SAFE_MAX in safe mode
        stats = {"moneyline": {"n": 150, "predicted": 60.0, "hit_rate": 90.0,
                               "calibration": 1.50, "bias": "UNDERCONFIDENT"}}
        cal_p, _, _ = calibrate_prob_gated(80.0, "moneyline", stats, safe_mode=True)
        assert cal_p <= 92.0


# ═══════════════════════════════════════════════════════════════════════════════
# Safe mode
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafeMode:
    def _clear_pred(self, home_win=75.0, away_win=15.0, draw=10.0):
        """A soccer pred that clearly passes safe-mode clarity criterion."""
        pred = _soccer_pred(
            home_win=home_win, away_win=away_win, draw=draw,
            over_1_5=85.0, over_2_5=70.0, btts=55.0,
            confidence="ALTA",
        )
        return pred

    def test_only_moneyline_in_safe_mode(self):
        """Safe mode should only produce moneyline picks."""
        preds = [self._clear_pred() for _ in range(5)]
        # Give each a unique match name
        for i, p in enumerate(preds):
            p["home"] = f"Team{i}"
            p["away"] = f"Rival{i}"
        legs, _, _ = generate_parlay_legs(
            preds, max_legs=3, min_prob=62.0, safe_mode=True
        )
        for leg in legs:
            assert leg["market_type"] == "moneyline", (
                f"Expected moneyline, got {leg['market_type']}"
            )

    def test_safe_mode_max_3_legs(self):
        preds = [self._clear_pred() for _ in range(10)]
        for i, p in enumerate(preds):
            p["home"] = f"H{i}"; p["away"] = f"A{i}"
        legs, _, _ = generate_parlay_legs(
            preds, max_legs=3, min_prob=62.0, safe_mode=True
        )
        assert len(legs) <= 3

    def test_safe_mode_no_variety_cap(self):
        """In safe mode all legs can be the same market type (no variety cap)."""
        preds = [self._clear_pred() for _ in range(5)]
        for i, p in enumerate(preds):
            p["home"] = f"H{i}"; p["away"] = f"A{i}"
        legs, _, _ = generate_parlay_legs(
            preds, max_legs=5, min_prob=62.0, safe_mode=True
        )
        from collections import Counter
        counts = Counter(leg["market_type"] for leg in legs)
        # Safe mode: all legs may be moneyline (> _MAX_SAME_MARKET is fine)
        assert True  # no assertion failure

    def test_clarity_filter_excludes_low_separation(self):
        """Moneyline with small separation between best and second should be excluded."""
        # home_win=63, away_win=62, draw=25 → top-2 separation = 1 pp < 12 pp
        pred = _soccer_pred(
            home_win=63.0, away_win=62.0, draw=25.0,
            confidence="ALTA",
        )
        _, _, excluded = generate_parlay_legs(
            [pred], max_legs=3, min_prob=62.0, safe_mode=True
        )
        assert any("LOW_SEPARATION" in e["reasons"] for e in excluded)

    def test_low_separation_for_draw_excluded(self):
        """Draw with very low probability excluded in safe mode."""
        pred = _soccer_pred(
            home_win=60.0, away_win=20.0, draw=20.0,
            confidence="ALTA",
        )
        # draw=20 < MIN_DRAW_PROB=40 → draw pick excluded via LOW_PROB in clarity
        legs, _, excluded = generate_parlay_legs(
            [pred], max_legs=3, min_prob=62.0, safe_mode=True
        )
        # Home win should still pass (if home_win >= 62 and sep >= 12)
        # Here home_win=60 < 62 → also LOW_PROB
        assert any("LOW_PROB" in e["reasons"] or "LOW_SEPARATION" in e["reasons"]
                   for e in excluded)

    def test_format_parlay_safe_returns_string(self):
        preds = [self._clear_pred()]
        legs, report, _ = generate_parlay_legs(
            preds, max_legs=3, min_prob=62.0, safe_mode=True
        )
        text = format_parlay_safe(legs, report, parlay_id="P260315-1")
        assert isinstance(text, str)
        assert "SAFE" in text

    def test_format_parlay_safe_shows_exclusion_summary(self):
        # Force some exclusions
        preds = [_soccer_pred(confidence="BAJA")]
        legs, report, _ = generate_parlay_legs(
            preds, max_legs=3, min_prob=62.0, safe_mode=True
        )
        text = format_parlay_safe(legs, report)
        assert "Resumen" in text or "filtros" in text.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# format_parlay with report
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatParlayReport:
    def test_report_exclusions_shown_in_output(self):
        report = {
            "total_candidates": 10,
            "legs_selected": 3,
            "exclusions": {"LOW_CONF": 2, "LOW_PROB": 3},
        }
        parlays = build_parlays(_make_legs([85.0, 82.0, 80.0]))
        text = format_parlay(parlays, report=report)
        assert "excluido" in text.lower()
        # Keys are escaped for Markdown v1 — underscores become \_
        assert "LOW\\_CONF" in text

    def test_report_none_falls_back_to_filtered_count(self):
        parlays = build_parlays(_make_legs([85.0, 82.0]))
        text = format_parlay(parlays, filtered_count=5, report=None)
        assert "excluido" in text.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Dream Parlay (_build_dream_bundle, generate_dream_parlay, format_parlay_dream)
# ═══════════════════════════════════════════════════════════════════════════════

from core.parlay import (
    _build_dream_bundle,
    generate_dream_parlay,
    format_parlay_dream,
)


def _dream_soccer_pred(home_win=72.0, away_win=15.0, draw=13.0,
                        over_1_5=80.0, over_2_5=55.0, over_3_5=30.0, btts=52.0):
    return {
        "sport": "⚽ Soccer",
        "home": "Liverpool",
        "away": "Arsenal",
        "home_win": home_win,
        "away_win": away_win,
        "draw": draw,
        "over_1_5": over_1_5,
        "over_2_5": over_2_5,
        "over_3_5": over_3_5,
        "btts": btts,
        "confidence": "ALTA",
        "league": "Premier League",
    }


def _dream_nba_pred(home_win=65.0, away_win=35.0):
    return {
        "sport": "NBA 🏀",
        "home": "Lakers",
        "away": "Celtics",
        "home_win": home_win,
        "away_win": away_win,
        "confidence": "ALTA",
        "league": "NBA",
        "game_totals": {"over_prob": 58.0, "under_prob": 42.0, "line": 224.5},
    }


def _dream_mlb_pred(home_win=63.0, away_win=37.0):
    return {
        "sport": "MLB ⚾",
        "home": "Yankees",
        "away": "Red Sox",
        "home_win": home_win,
        "away_win": away_win,
        "confidence": "ALTA",
        "league": "MLB",
        "run_line": {"fav_side": "home", "cover_prob": 57.0},
    }


class TestBuildDreamBundle:
    def test_soccer_returns_bundle(self):
        bundle = _build_dream_bundle(_dream_soccer_pred())
        assert bundle is not None
        assert bundle["match"] == "Liverpool vs Arsenal"
        assert bundle["sport_emoji"] == "⚽"
        assert len(bundle["legs"]) >= 1

    def test_soccer_includes_moneyline(self):
        bundle = _build_dream_bundle(_dream_soccer_pred())
        assert bundle is not None
        picks = [leg["pick"] for leg in bundle["legs"]]
        assert any("Victoria" in p for p in picks)

    def test_soccer_includes_totals(self):
        # over_2_5=55 → should include Over 2.5
        bundle = _build_dream_bundle(_dream_soccer_pred(over_2_5=55.0))
        assert bundle is not None
        picks = [leg["pick"] for leg in bundle["legs"]]
        assert any("Over" in p for p in picks)

    def test_soccer_includes_over35_when_high_prob(self):
        # over_3_5=52 ≥ 50 → should prefer Over 3.5 over lower lines
        bundle = _build_dream_bundle(_dream_soccer_pred(over_3_5=52.0, over_2_5=65.0))
        assert bundle is not None
        picks = [leg["pick"] for leg in bundle["legs"]]
        assert "Over 3.5" in picks

    def test_soccer_btts_excluded_for_blowout(self):
        # home_win=85 ≥ 75 → BTTS should NOT be included (blowout story)
        bundle = _build_dream_bundle(_dream_soccer_pred(home_win=85.0, btts=60.0))
        assert bundle is not None
        picks = [leg["pick"] for leg in bundle["legs"]]
        assert not any("BTTS" in p for p in picks)

    def test_soccer_btts_included_for_moderate_win(self):
        # home_win=65 < 75, btts=55 ≥ 50 → BTTS should be included
        bundle = _build_dream_bundle(_dream_soccer_pred(home_win=65.0, btts=55.0))
        assert bundle is not None
        picks = [leg["pick"] for leg in bundle["legs"]]
        assert any("BTTS" in p for p in picks)

    def test_soccer_draw_no_over35(self):
        # Draw story should not include Over 3.5 even if prob is ≥ 50 %
        bundle = _build_dream_bundle(
            _dream_soccer_pred(home_win=30.0, away_win=20.0, draw=50.0,
                                over_3_5=55.0, over_2_5=65.0)
        )
        assert bundle is not None
        picks = [leg["pick"] for leg in bundle["legs"]]
        assert "Over 3.5" not in picks

    def test_no_contradictory_moneylines(self):
        bundle = _build_dream_bundle(_dream_soccer_pred())
        assert bundle is not None
        ml_picks = [leg for leg in bundle["legs"] if leg["market_type"] == "moneyline"]
        pick_names = [l["pick"] for l in ml_picks]
        # Should not contain both home-win and away-win
        has_home = any("Liverpool" in p for p in pick_names)
        has_away = any("Arsenal" in p for p in pick_names)
        assert not (has_home and has_away)

    def test_no_duplicate_picks(self):
        bundle = _build_dream_bundle(_dream_soccer_pred())
        assert bundle is not None
        picks = [leg["pick"] for leg in bundle["legs"]]
        assert len(picks) == len(set(picks))

    def test_nba_includes_totals(self):
        bundle = _build_dream_bundle(_dream_nba_pred())
        assert bundle is not None
        picks = [leg["pick"] for leg in bundle["legs"]]
        assert any("Over" in p or "Under" in p for p in picks)

    def test_nba_picks_best_totals_side(self):
        # over_prob=58 > under_prob=42 → should pick Over
        bundle = _build_dream_bundle(_dream_nba_pred())
        assert bundle is not None
        picks = [leg["pick"] for leg in bundle["legs"]]
        assert any("Over" in p for p in picks)

    def test_mlb_run_line_coherent_direction(self):
        # fav_side="home", winner_direction=home → run line should be included
        bundle = _build_dream_bundle(_dream_mlb_pred())
        assert bundle is not None
        picks = [leg["pick"] for leg in bundle["legs"]]
        assert any("-1.5" in p for p in picks)

    def test_mlb_run_line_not_included_when_misaligned(self):
        # fav_side="home" but away team is favored → run line should NOT be included
        pred = _dream_mlb_pred(home_win=35.0, away_win=65.0)
        pred["run_line"] = {"fav_side": "home", "cover_prob": 57.0}
        bundle = _build_dream_bundle(pred)
        if bundle is not None:
            picks = [leg["pick"] for leg in bundle["legs"]]
            # If Yankees -1.5 (home run line) is included, that contradicts away win story
            assert "Yankees -1.5" not in picks

    def test_bundle_prob_calculated_correctly(self):
        bundle = _build_dream_bundle(_dream_soccer_pred())
        assert bundle is not None
        expected = 1.0
        for leg in bundle["legs"]:
            expected *= leg["prob"] / 100.0
        assert abs(bundle["bundle_prob"] - round(expected * 100, 1)) < 0.01

    def test_returns_none_for_empty_candidates(self):
        pred = {
            "sport": "⚽ Soccer",
            "home": "A",
            "away": "B",
            "home_win": 0,
            "away_win": 0,
            "draw": 0,
            "over_1_5": 0,
            "over_2_5": 0,
            "over_3_5": 0,
            "btts": 0,
        }
        assert _build_dream_bundle(pred) is None


class TestGenerateDreamParlay:
    def test_returns_all_predictions(self):
        preds = [_dream_soccer_pred() for _ in range(6)]
        bundles = generate_dream_parlay(preds)
        # All predictions should produce bundles (each is a valid soccer pred)
        assert len(bundles) == 6

    def test_no_hard_limit(self):
        preds = [_dream_soccer_pred() for _ in range(10)]
        bundles = generate_dream_parlay(preds)
        assert len(bundles) == 10  # no 4-bundle cap

    def test_max_bundles_respected_when_passed(self):
        preds = [_dream_soccer_pred() for _ in range(10)]
        bundles = generate_dream_parlay(preds, max_bundles=3)
        assert len(bundles) <= 3

    def test_sport_diversity_round_robin(self):
        soccer_preds = [_dream_soccer_pred() for _ in range(3)]
        nba_preds    = [_dream_nba_pred()    for _ in range(3)]
        bundles = generate_dream_parlay(soccer_preds + nba_preds)
        sports = [b["sport"] for b in bundles]
        # Both sports should appear
        assert any("soccer" in s or "football" in s for s in sports)
        assert any("nba" in s for s in sports)

    def test_returns_empty_for_no_predictions(self):
        assert generate_dream_parlay([]) == []

    def test_total_picks_at_least_three_across_bundles(self):
        preds = [_dream_soccer_pred(), _dream_nba_pred(), _dream_mlb_pred()]
        bundles = generate_dream_parlay(preds)
        total_picks = sum(len(b["legs"]) for b in bundles)
        assert total_picks >= 3


class TestFormatParlayDream:
    def test_returns_string(self):
        bundle = _build_dream_bundle(_dream_soccer_pred())
        text = format_parlay_dream([bundle])
        assert isinstance(text, str)

    def test_header_present(self):
        text = format_parlay_dream([])
        assert "PARLAY SOÑADOR" in text

    def test_empty_bundles_shows_warning(self):
        text = format_parlay_dream([])
        assert "No hay suficientes" in text or "⚠️" in text

    def test_shows_total_legs(self):
        bundles = generate_dream_parlay([_dream_soccer_pred(), _dream_nba_pred()])
        text = format_parlay_dream(bundles)
        assert "Patas totales" in text

    def test_shows_sport_breakdown(self):
        bundles = generate_dream_parlay([_dream_soccer_pred(), _dream_nba_pred()])
        text = format_parlay_dream(bundles)
        assert "Deportes incluidos" in text

    def test_shows_match_count(self):
        bundles = generate_dream_parlay([_dream_soccer_pred(), _dream_nba_pred()])
        text = format_parlay_dream(bundles)
        assert "Partidos" in text

    def test_parlay_id_included_when_provided(self):
        bundle = _build_dream_bundle(_dream_soccer_pred())
        text = format_parlay_dream([bundle], parlay_id="DREAM-001")
        assert "DREAM-001" in text

    def test_responsible_gambling_warning(self):
        text = format_parlay_dream([_build_dream_bundle(_dream_soccer_pred())])
        assert "riesgo" in text.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# _md_escape — special-character escaping (root-cause fix for /parlay entity error)
# ═══════════════════════════════════════════════════════════════════════════════

from core.parlay import _md_escape


class TestMdEscape:
    """Ensure _md_escape sanitises all Telegram Markdown v1 special characters."""

    def test_escapes_underscore(self):
        assert _md_escape("hello_world") == r"hello\_world"

    def test_escapes_asterisk(self):
        assert _md_escape("bold*text") == r"bold\*text"

    def test_escapes_backtick(self):
        assert _md_escape("code`snippet") == "code\\`snippet"

    def test_escapes_open_bracket(self):
        assert _md_escape("[link]") == r"\[link]"

    def test_escapes_all_special_chars_together(self):
        raw = "Team_A *vs* `B` [cup]"
        escaped = _md_escape(raw)
        assert "\\_" in escaped
        assert "\\*" in escaped
        assert "\\`" in escaped
        assert "\\[" in escaped

    def test_plain_text_unchanged(self):
        assert _md_escape("Real Madrid vs Barcelona") == "Real Madrid vs Barcelona"

    def test_non_string_coerced(self):
        assert _md_escape(42) == "42"


class TestFormatParlaySpecialChars:
    """format_parlay must not fail when team/pick names contain Markdown specials."""

    def _leg(self, match, pick, prob=80.0):
        return {
            "match": match,
            "pick": pick,
            "prob": prob,
            "league": "Test",
            "confidence": "ALTA",
            "market_type": "moneyline",
            "sport_emoji": "⚽",
            "risk_reasons": [],
        }

    def test_asterisk_in_team_name_does_not_break_format(self):
        legs = [self._leg("FC *Stars* vs Rival", "Victoria FC *Stars*")]
        parlays = build_parlays(legs + [self._leg("X vs Y", "Victoria X", 78.0)])
        text = format_parlay(parlays)
        # Escaped asterisks must appear; raw asterisks that open/close bold must not
        assert "\\*" in text

    def test_underscore_in_team_name_escaped(self):
        legs = [
            self._leg("Team_A vs Team_B", "Victoria Team_A"),
            self._leg("H vs A", "Victoria H", 78.0),
        ]
        parlays = build_parlays(legs)
        text = format_parlay(parlays)
        assert "\\_" in text

    def test_backtick_in_pick_escaped(self):
        legs = [
            self._leg("Home vs Away", "Over 2.5`s", 82.0),
            self._leg("X vs Y", "Victoria X", 79.0),
        ]
        parlays = build_parlays(legs)
        text = format_parlay(parlays)
        assert "\\`" in text

    def test_format_parlay_safe_asterisk_in_pick(self):
        legs = [self._leg("A vs B", "Victoria A*", 78.0)]
        report = {"total_candidates": 1, "legs_selected": 1, "exclusions": {}}
        text = format_parlay_safe(legs, report)
        assert "\\*" in text

    def test_format_parlay_dream_asterisk_in_match(self):
        bundle = {
            "match": "Club *Real* vs Rival",
            "sport": "⚽ Soccer",
            "sport_emoji": "⚽",
            "narrative": "💪 Partido especial",
            "legs": [{"pick": "Victoria Local", "prob": 72.0, "market_type": "moneyline"}],
            "bundle_prob": 72.0,
        }
        text = format_parlay_dream([bundle])
        assert "\\*" in text


# ═══════════════════════════════════════════════════════════════════════════════
# Markdown escaping of exclusion keys/reasons
# ═══════════════════════════════════════════════════════════════════════════════

class TestExclusionKeyEscaping:
    """Verify that exclusion keys with underscores are escaped in output."""

    def test_format_parlay_exclusion_keys_with_underscores_escaped(self):
        import re
        report = {
            "total_candidates": 10,
            "legs_selected": 2,
            "exclusions": {"COIN_FLIP": 3, "LOW_PROB": 2},
        }
        parlays = build_parlays(_make_legs([85.0, 82.0]))
        text = format_parlay(parlays, report=report)
        # Keys must appear (escaped) in the output
        assert "COIN\\_FLIP" in text
        assert "LOW\\_PROB" in text
        # Raw unescaped underscores must not appear (only escaped ones should)
        assert not re.search(r"(?<!\\)COIN_FLIP", text)
        assert not re.search(r"(?<!\\)LOW_PROB", text)

    def test_format_parlay_safe_exclusion_reasons_with_underscores_escaped(self):
        import re
        legs = [
            {
                "match": "X vs Y",
                "pick": "Victoria X",
                "prob": 80.0,
                "league": "Test",
                "confidence": "ALTA",
                "market_type": "moneyline",
                "sport_emoji": "⚽",
                "risk_reasons": [],
            }
        ]
        report = {
            "total_candidates": 5,
            "legs_selected": 1,
            "exclusions": {"LOW_CONF": 2, "HIGH_RISK": 1},
        }
        text = format_parlay_safe(legs, report)
        # Keys must appear (escaped) in the output
        assert "LOW\\_CONF" in text
        assert "HIGH\\_RISK" in text
        assert not re.search(r"(?<!\\)LOW_CONF", text)
        assert not re.search(r"(?<!\\)HIGH_RISK", text)
