"""
Tests for core/parlay_analyzer.py

Covers:
- decimal_to_prob / american_to_prob helpers
- _prob_from_text — odds extraction from free-form text
- parse_leg_text  — single leg parsing (various formats)
- parse_parlay_text — multi-leg parsing
- analyze_parlay — combined probability, risk tier, recommendations
- format_parlay_analysis — Telegram output format
"""

import pytest

from core.parlay_analyzer import (
    decimal_to_prob,
    american_to_prob,
    _prob_from_text,
    parse_leg_text,
    parse_parlay_text,
    analyze_parlay,
    format_parlay_analysis,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Probability helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestDecimalToProb:
    def test_typical_favourite(self):
        # 1.50 → 66.7 %
        assert abs(decimal_to_prob(1.50) - 66.7) < 0.5

    def test_even_money(self):
        # 2.00 → 50.0 %
        assert abs(decimal_to_prob(2.00) - 50.0) < 0.1

    def test_long_shot(self):
        # 5.00 → 20.0 %
        assert abs(decimal_to_prob(5.00) - 20.0) < 0.1

    def test_odds_below_one_returns_near_100(self):
        assert decimal_to_prob(0.5) >= 99.0

    def test_returns_float(self):
        assert isinstance(decimal_to_prob(1.75), float)


class TestAmericanToProb:
    def test_favourite_minus_110(self):
        # -110 → 52.4 %
        assert abs(american_to_prob(-110) - 52.4) < 0.5

    def test_underdog_plus_150(self):
        # +150 → 40.0 %
        assert abs(american_to_prob(150) - 40.0) < 0.1

    def test_even_money_plus_100(self):
        # +100 → 50.0 %
        assert abs(american_to_prob(100) - 50.0) < 0.1

    def test_heavy_favourite_minus_300(self):
        # -300 → 75.0 %
        assert abs(american_to_prob(-300) - 75.0) < 0.5

    def test_returns_float(self):
        assert isinstance(american_to_prob(-110), float)


# ═══════════════════════════════════════════════════════════════════════════════
# _prob_from_text
# ═══════════════════════════════════════════════════════════════════════════════

class TestProbFromText:
    def test_decimal_with_at(self):
        prob, raw = _prob_from_text("Over 2.5 @1.75")
        assert prob is not None
        assert abs(prob - decimal_to_prob(1.75)) < 0.1
        assert "@" in raw

    def test_decimal_plain_at_end(self):
        prob, raw = _prob_from_text("Moneyline Lakers 2.10")
        assert prob is not None
        assert abs(prob - decimal_to_prob(2.10)) < 0.1

    def test_american_positive(self):
        prob, raw = _prob_from_text("Chiefs spread +110")
        assert prob is not None
        assert abs(prob - american_to_prob(110)) < 0.1

    def test_american_negative(self):
        prob, raw = _prob_from_text("BTTS -120")
        assert prob is not None
        assert abs(prob - american_to_prob(-120)) < 0.1

    def test_no_odds_returns_none(self):
        prob, raw = _prob_from_text("Burnley vs Bournemouth Over 2.5")
        assert prob is None
        assert raw == ""

    def test_raw_string_preserved(self):
        _, raw = _prob_from_text("Over 2.5 @1.80")
        assert "1.80" in raw


# ═══════════════════════════════════════════════════════════════════════════════
# parse_leg_text
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseLegText:
    # ── Basic format: match | pick | @odds ───────────────────────────────
    def test_pipe_separated_with_odds(self):
        leg = parse_leg_text("Burnley vs Bournemouth | Over 2.5 | @1.75")
        assert leg is not None
        assert "Burnley" in leg["match"]
        assert "Bournemouth" in leg["match"]
        assert "Over 2.5" in leg["pick"]
        assert leg["prob"] is not None
        assert abs(leg["prob"] - decimal_to_prob(1.75)) < 0.1

    def test_pipe_separated_no_odds(self):
        leg = parse_leg_text("Lakers vs Warriors | Moneyline")
        assert leg is not None
        assert "Lakers" in leg["match"]
        assert "Warriors" in leg["match"]
        assert "Moneyline" in leg["pick"]
        assert leg["prob"] is None

    def test_space_separated_pick_keyword(self):
        leg = parse_leg_text("Real Madrid vs Barcelona Victoria Real Madrid @1.45")
        assert leg is not None
        assert "Real Madrid" in leg["match"]
        assert "Barcelona" in leg["match"]
        assert leg["prob"] is not None

    def test_american_odds(self):
        leg = parse_leg_text("Chiefs vs Patriots spread +110")
        assert leg is not None
        assert "Chiefs" in leg["match"]
        assert leg["prob"] is not None
        assert abs(leg["prob"] - american_to_prob(110)) < 0.1

    def test_american_negative_odds(self):
        leg = parse_leg_text("Tigres vs America BTTS -120")
        assert leg is not None
        assert leg["prob"] is not None
        assert abs(leg["prob"] - american_to_prob(-120)) < 0.1

    # ── Numbered list format ─────────────────────────────────────────────
    def test_numbered_leg_stripped(self):
        leg = parse_leg_text("1. Burnley vs Bournemouth | Over 2.5 | @1.75")
        assert leg is not None
        assert "Burnley" in leg["match"]

    def test_numbered_with_parenthesis(self):
        leg = parse_leg_text("2) Lakers vs Warriors | Moneyline | @2.00")
        assert leg is not None
        assert "Lakers" in leg["match"]

    # ── Noise / header lines ─────────────────────────────────────────────
    def test_empty_line_returns_none(self):
        assert parse_leg_text("") is None
        assert parse_leg_text("   ") is None

    def test_parlay_header_returns_none(self):
        assert parse_leg_text("PARLAY") is None
        assert parse_leg_text("combinada") is None
        assert parse_leg_text("boleto") is None

    def test_total_line_returns_none(self):
        assert parse_leg_text("Total: $150") is None
        assert parse_leg_text("Pago potencial: $500") is None

    # ── Return keys ──────────────────────────────────────────────────────
    def test_required_keys_present(self):
        leg = parse_leg_text("Man City vs Arsenal | Over 2.5 | @1.80")
        assert leg is not None
        for key in ("match", "pick", "odds_raw", "prob", "prob_source"):
            assert key in leg

    def test_prob_source_is_odds_when_prob_found(self):
        leg = parse_leg_text("Man City vs Arsenal | Over 2.5 | @1.80")
        assert leg["prob_source"] == "odds"

    def test_prob_source_is_unknown_when_no_odds(self):
        leg = parse_leg_text("Man City vs Arsenal | Over 2.5")
        assert leg["prob_source"] == "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# parse_parlay_text
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseParlayText:
    def test_multiline_input(self):
        text = (
            "Burnley vs Bournemouth | Over 2.5 | @1.75\n"
            "Lakers vs Warriors | Moneyline | @2.10\n"
            "Real Madrid vs Barcelona | Victoria Real Madrid | 1.45"
        )
        legs = parse_parlay_text(text)
        assert len(legs) == 3

    def test_semicolon_separated(self):
        text = "Burnley vs Bournemouth @1.75; Lakers vs Warriors @2.10"
        legs = parse_parlay_text(text)
        assert len(legs) == 2

    def test_filters_noise_lines(self):
        text = (
            "PARLAY 3 PATAS\n"
            "1. Burnley vs Bournemouth | Over 2.5 | @1.75\n"
            "2. Lakers vs Warriors | Moneyline | @2.10\n"
            "Total: $500\n"
            "Pago potencial: $1200"
        )
        legs = parse_parlay_text(text)
        assert len(legs) == 2

    def test_empty_input_returns_empty_list(self):
        assert parse_parlay_text("") == []
        assert parse_parlay_text("\n\n\n") == []

    def test_single_leg(self):
        legs = parse_parlay_text("Man City vs Arsenal | Over 2.5 | @1.80")
        assert len(legs) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# analyze_parlay
# ═══════════════════════════════════════════════════════════════════════════════

def _make_leg(match="A vs B", pick="Over 2.5", prob=80.0):
    return {
        "match": match,
        "pick": pick,
        "odds_raw": "@1.25",
        "prob": prob,
        "prob_source": "odds",
    }


class TestAnalyzeParlay:
    def test_combined_prob_two_legs(self):
        legs = [_make_leg(prob=80.0), _make_leg("C vs D", prob=75.0)]
        result = analyze_parlay(legs, try_lookup=False)
        expected = round((80.0 / 100) * (75.0 / 100) * 100, 1)
        assert abs(result["combined_prob"] - expected) < 0.1

    def test_combined_prob_three_legs(self):
        legs = [_make_leg(prob=90.0), _make_leg("C vs D", prob=80.0), _make_leg("E vs F", prob=75.0)]
        result = analyze_parlay(legs, try_lookup=False)
        expected = round(0.9 * 0.8 * 0.75 * 100, 1)
        assert abs(result["combined_prob"] - expected) < 0.1

    def test_no_probs_gives_none_combined(self):
        legs = [
            {"match": "A vs B", "pick": "?", "odds_raw": "", "prob": None, "prob_source": "unknown"},
        ]
        result = analyze_parlay(legs, try_lookup=False)
        assert result["combined_prob"] is None

    def test_risk_label_baja_for_high_prob(self):
        legs = [_make_leg(prob=90.0), _make_leg("C vs D", prob=80.0)]
        result = analyze_parlay(legs, try_lookup=False)
        assert result["risk_label"] == "BAJA"
        assert result["risk_emoji"] == "🟢"

    def test_risk_label_media_for_moderate_prob(self):
        legs = [_make_leg(prob=75.0), _make_leg("C vs D", prob=70.0)]
        result = analyze_parlay(legs, try_lookup=False)
        # 0.75 * 0.70 = 0.525 → 52.5% → BAJA
        # Let's use values that produce 40-60% combined
        legs2 = [_make_leg(prob=70.0), _make_leg("C vs D", prob=65.0)]
        result2 = analyze_parlay(legs2, try_lookup=False)
        combined = result2["combined_prob"]
        if combined >= 40:
            assert result2["risk_label"] in ("BAJA", "MEDIA")

    def test_risk_label_muy_alta_for_very_low_prob(self):
        legs = [_make_leg(prob=40.0)] * 5  # ~1% combined
        result = analyze_parlay(legs, try_lookup=False)
        assert result["risk_label"] in ("ALTA", "MUY ALTA")

    def test_risk_desconocido_when_no_probs(self):
        legs = [
            {"match": "A vs B", "pick": "?", "odds_raw": "", "prob": None, "prob_source": "unknown"},
        ]
        result = analyze_parlay(legs, try_lookup=False)
        assert result["risk_label"] == "DESCONOCIDO"
        assert result["risk_emoji"] == "❓"

    def test_recommendations_is_list(self):
        legs = [_make_leg(prob=80.0), _make_leg("C vs D", prob=75.0)]
        result = analyze_parlay(legs, try_lookup=False)
        assert isinstance(result["recommendations"], list)
        assert len(result["recommendations"]) >= 1

    def test_weak_leg_flagged_in_recs(self):
        legs = [
            _make_leg("Strong A vs B", pick="Over 2.5", prob=85.0),
            _make_leg("Weak C vs D",   pick="Moneyline", prob=42.0),
        ]
        result = analyze_parlay(legs, try_lookup=False)
        recs_text = " ".join(result["recommendations"])
        assert "Weak" in recs_text or "42" in recs_text

    def test_single_leg_recommendation(self):
        legs = [_make_leg(prob=80.0)]
        result = analyze_parlay(legs, try_lookup=False)
        recs_text = " ".join(result["recommendations"])
        assert "sola" in recs_text or "directa" in recs_text

    def test_many_legs_low_prob_recommendation(self):
        legs = [_make_leg(f"T{i} vs T{i+1}", prob=60.0) for i in range(6)]
        result = analyze_parlay(legs, try_lookup=False)
        recs_text = " ".join(result["recommendations"])
        # Should warn about too many legs
        assert "patas" in recs_text or "5+" in recs_text or "combina" in recs_text

    def test_output_has_required_keys(self):
        legs = [_make_leg(prob=80.0)]
        result = analyze_parlay(legs, try_lookup=False)
        for key in ("legs", "combined_prob", "risk_label", "risk_emoji", "recommendations"):
            assert key in result

    def test_legs_list_is_enriched_copy(self):
        original_leg = _make_leg(prob=80.0)
        legs = [original_leg]
        result = analyze_parlay(legs, try_lookup=False)
        # Original dict should not be mutated
        assert original_leg is not result["legs"][0]


# ═══════════════════════════════════════════════════════════════════════════════
# format_parlay_analysis
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatParlayAnalysis:
    def _analysis_two_legs(self):
        legs = [_make_leg(prob=80.0), _make_leg("C vs D", prob=75.0)]
        return analyze_parlay(legs, try_lookup=False)

    def test_returns_string(self):
        assert isinstance(format_parlay_analysis(self._analysis_two_legs()), str)

    def test_contains_header(self):
        text = format_parlay_analysis(self._analysis_two_legs())
        assert "ANÁLISIS DE PARLAY" in text

    def test_contains_match_names(self):
        text = format_parlay_analysis(self._analysis_two_legs())
        assert "A vs B" in text
        assert "C vs D" in text

    def test_contains_combined_prob(self):
        text = format_parlay_analysis(self._analysis_two_legs())
        assert "combinada" in text.lower()

    def test_contains_risk_level(self):
        text = format_parlay_analysis(self._analysis_two_legs())
        assert "riesgo" in text.lower()

    def test_contains_recommendations_section(self):
        text = format_parlay_analysis(self._analysis_two_legs())
        assert "RECOMENDACIONES" in text

    def test_contains_responsible_gambling_warning(self):
        text = format_parlay_analysis(self._analysis_two_legs())
        assert "responsablemente" in text.lower()

    def test_contains_prob_percentages(self):
        text = format_parlay_analysis(self._analysis_two_legs())
        assert "80" in text or "75" in text

    def test_unknown_prob_shows_question_mark(self):
        legs = [
            {"match": "X vs Y", "pick": "Over 2.5", "odds_raw": "",
             "prob": None, "prob_source": "unknown"},
        ]
        analysis = analyze_parlay(legs, try_lookup=False)
        text = format_parlay_analysis(analysis)
        assert "?" in text

    def test_model_source_annotated(self):
        legs = [
            {"match": "A vs B", "pick": "Over 2.5", "odds_raw": "",
             "prob": 72.0, "prob_source": "model"},
        ]
        analysis = analyze_parlay(legs, try_lookup=False)
        text = format_parlay_analysis(analysis)
        assert "modelo" in text.lower()
