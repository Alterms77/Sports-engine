"""
Tests for the refactored core/parlay.py.

Covers:
- _build_candidates: correct picks generated per sport
- score_risk: risk scoring heuristics
- generate_parlay_legs: confidence/prob filtering, variety constraints,
                        high-risk exclusion, market-type caps
- build_parlays: tier construction and combined-probability calculation
- format_parlay: output format, sport emoji, filtered count note
"""

import pytest

from core.parlay import (
    _build_candidates,
    score_risk,
    generate_parlay_legs,
    build_parlays,
    format_parlay,
    _CONFIDENCE_RANK,
    _MAX_SAME_MARKET,
    _HIGH_RISK_SCORE,
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
        # A match where ALL markets are weak (no market > 65 %) and win probs
        # are very balanced should trigger high risk via two flags:
        #   +0.40 (top side < 55 %) + 0.20 (no market > 65 %) = 0.60 >= 0.5
        pred = _soccer_pred(
            home_win=50.0, away_win=30.0,
            over_1_5=60.0, over_2_5=45.0, over_3_5=30.0, btts=48.0,
            confidence="ALTA",
        )
        score, reasons = score_risk(pred)
        assert score >= _HIGH_RISK_SCORE
        assert any("equilibra" in r.lower() or "baja" in r.lower() for r in reasons)

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

    def test_returns_list(self):
        result = generate_parlay_legs(self._five_safe_soccer_preds())
        assert isinstance(result, list)

    def test_respects_max_legs(self):
        preds = self._five_safe_soccer_preds()
        result = generate_parlay_legs(preds, max_legs=3)
        assert len(result) <= 3

    def test_sorted_by_prob_descending(self):
        preds = self._five_safe_soccer_preds()
        result = generate_parlay_legs(preds)
        probs = [leg["prob"] for leg in result]
        assert probs == sorted(probs, reverse=True)

    def test_filters_low_confidence(self):
        preds = [
            _soccer_pred("H1", "A1", confidence="ALTA", home_win=80.0, over_1_5=85.0),
            _soccer_pred("H2", "A2", confidence="BAJA", home_win=80.0, over_1_5=85.0),
        ]
        result = generate_parlay_legs(preds, min_confidence="ALTA")
        matches = [leg["match"] for leg in result]
        assert "H2 vs A2" not in matches

    def test_filters_below_min_prob(self):
        preds = [_soccer_pred(home_win=60.0, over_1_5=62.0, over_2_5=55.0, btts=50.0)]
        result = generate_parlay_legs(preds, min_prob=75.0)
        # All candidates below 75 % — no legs expected
        assert result == []

    def test_excludes_high_risk_matches(self):
        high_risk = _soccer_pred("Risk_H", "Risk_A",
                                 confidence="BAJA",   # BAJA alone pushes risk >= 0.5
                                 home_win=80.0, over_1_5=85.0)
        safe = _soccer_pred("Safe_H", "Safe_A",
                             confidence="ALTA", home_win=80.0, over_1_5=85.0)
        result = generate_parlay_legs([high_risk, safe])
        matches = [leg["match"] for leg in result]
        assert "Risk_H vs Risk_A" not in matches
        assert "Safe_H vs Safe_A" in matches

    def test_market_variety_constraint(self):
        """No more than _MAX_SAME_MARKET legs of the same market type."""
        # Create many soccer preds that would all produce "Over 1.5" (totals)
        # as best candidate, plus one that produces moneyline
        preds = []
        for i in range(6):
            preds.append(_soccer_pred(
                f"T{i}", f"U{i}",
                home_win=70.0,
                over_1_5=90.0 - i,   # totals dominate
                over_2_5=55.0,
                btts=55.0,
                confidence="ALTA",
            ))
        result = generate_parlay_legs(preds, max_legs=5)
        from collections import Counter
        counts = Counter(leg["market_type"] for leg in result)
        for mtype, cnt in counts.items():
            assert cnt <= _MAX_SAME_MARKET, (
                f"Market type '{mtype}' appears {cnt} times, max is {_MAX_SAME_MARKET}"
            )

    def test_one_leg_per_match(self):
        """Each match may contribute at most one leg."""
        pred = _soccer_pred("H1", "A1", home_win=80.0, over_1_5=85.0)
        result = generate_parlay_legs([pred] * 5)
        match_names = [leg["match"] for leg in result]
        assert len(match_names) == len(set(match_names))

    def test_leg_has_required_keys(self):
        preds = [_soccer_pred(home_win=80.0, over_1_5=85.0)]
        result = generate_parlay_legs(preds)
        if result:
            leg = result[0]
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
        result = generate_parlay_legs(preds, min_prob=75.0)
        sports = {leg["sport_emoji"] for leg in result}
        # At least two different sports should appear
        assert len(sports) >= 2

    def test_empty_predictions_returns_empty(self):
        assert generate_parlay_legs([]) == []

    def test_media_confidence_excluded_when_min_is_alta(self):
        preds = [_soccer_pred(confidence="MEDIA", home_win=82.0, over_1_5=88.0)]
        result = generate_parlay_legs(preds, min_confidence="ALTA")
        assert result == []

    def test_media_confidence_included_when_min_is_media(self):
        preds = [_soccer_pred(confidence="MEDIA", home_win=82.0, over_1_5=88.0)]
        result = generate_parlay_legs(preds, min_confidence="MEDIA", min_prob=75.0)
        assert len(result) == 1


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
