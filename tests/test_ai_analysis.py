"""
Tests for core/ai_analysis.py.

All tests run without a real OpenAI API key — the module must degrade
gracefully in that case and every path that would call the network is
either patched or exercised via the is_available() guard.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core.ai_analysis import (
    is_available,
    analyze_prediction,
    generate_parlay_narrative,
    answer_betting_question,
    ai_picks_summary,
    _format_prediction,
    _format_soccer_stats,
    _format_nba_stats,
    _format_mlb_stats,
    _format_nfl_stats,
    _format_tennis_stats,
    _NO_KEY_MSG,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

def _soccer_pred():
    return {
        "home": "Real Madrid",
        "away": "Barcelona",
        "home_win": 48.2,
        "draw": 26.4,
        "away_win": 25.4,
        "xg_home": 1.52,
        "xg_away": 1.18,
        "over_2_5": 61.3,
        "btts": 54.7,
        "form_home": "WWDWL",
        "form_away": "WDWWW",
        "h2h_summary": "5W-3D-2L últimas 10",
        "confidence": "MEDIA",
        "league": "La Liga",
    }


def _nba_pred():
    return {
        "sport": "NBA 🏀",
        "home": "Lakers",
        "away": "Celtics",
        "home_win": 44.2,
        "away_win": 55.8,
        "expected_home": 108.5,
        "expected_away": 114.1,
        "spread": -5.6,
        "over_under": 222.6,
        "home_off_rtg": 112.3,
        "home_def_rtg": 108.9,
        "away_off_rtg": 118.1,
        "away_def_rtg": 107.2,
        "confidence": "ALTA",
    }


def _mlb_pred():
    return {
        "sport": "MLB ⚾",
        "home": "Yankees",
        "away": "Red Sox",
        "home_win": 58.4,
        "away_win": 41.6,
        "expected_home": 4.8,
        "expected_away": 3.9,
        "over_under": 8.7,
        "home_pitcher": "Gerrit Cole",
        "home_pitcher_era": 2.63,
        "home_pitcher_whip": 0.98,
        "home_pitcher_k9": 11.2,
        "away_pitcher": "Brayan Bello",
        "away_pitcher_era": 3.91,
        "away_pitcher_whip": 1.22,
        "away_pitcher_k9": 8.8,
        "confidence": "ALTA",
    }


def _nfl_pred():
    return {
        "sport": "NFL 🏈",
        "home": "Chiefs",
        "away": "Eagles",
        "home_win": 62.1,
        "away_win": 37.9,
        "spread": 5.2,
        "over_under": 48.5,
        "confidence": "MEDIA",
    }


def _tennis_pred():
    return {
        "sport": "Tenis 🎾",
        "home": "Jannik Sinner",
        "away": "Carlos Alcaraz",
        "home_win": 44.5,
        "away_win": 55.5,
        "elo_p1": 2350,
        "elo_p2": 2360,
        "surface": "clay",
        "best_of": 3,
        "confidence": "BAJA",
    }


def _parlay_legs():
    return [
        {"match": "Real Madrid vs Barcelona", "pick": "Local +0.5", "prob": 72.0, "sport_emoji": "⚽"},
        {"match": "Lakers vs Celtics", "pick": "Visitante ML", "prob": 55.8, "sport_emoji": "🏀"},
        {"match": "Yankees vs Red Sox", "pick": "Over 8.5", "prob": 61.3, "sport_emoji": "⚾"},
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# is_available
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsAvailable:
    def test_false_when_no_key(self):
        with patch("core.ai_analysis._api_key", return_value=""):
            assert is_available() is False

    def test_true_when_key_set(self):
        with patch("core.ai_analysis._api_key", return_value="sk-test-key"):
            assert is_available() is True


# ═══════════════════════════════════════════════════════════════════════════════
# Graceful fallback when no API key
# ═══════════════════════════════════════════════════════════════════════════════

class TestGracefulFallback:
    """All public functions must return the fallback message, not raise."""

    def _no_key(self):
        return patch("core.ai_analysis._api_key", return_value="")

    def test_analyze_prediction_fallback(self):
        with self._no_key():
            result = analyze_prediction(_soccer_pred(), "soccer")
        assert result == _NO_KEY_MSG

    def test_generate_parlay_narrative_fallback(self):
        with self._no_key():
            result = generate_parlay_narrative(_parlay_legs(), 28.5)
        assert result == _NO_KEY_MSG

    def test_answer_betting_question_fallback(self):
        with self._no_key():
            result = answer_betting_question("¿Vale el Over 2.5?")
        assert result == _NO_KEY_MSG

    def test_ai_picks_summary_fallback(self):
        with self._no_key():
            result = ai_picks_summary([_soccer_pred()])
        assert result == _NO_KEY_MSG

    def test_ai_picks_summary_empty_list(self):
        with self._no_key():
            result = ai_picks_summary([])
        # Empty list returns a specific message, not a crash
        assert isinstance(result, str)
        assert len(result) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Stat formatters
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatters:
    def test_soccer_has_match_line(self):
        text = _format_soccer_stats(_soccer_pred())
        assert "Real Madrid" in text
        assert "Barcelona" in text
        assert "48.2%" in text or "48.2" in text

    def test_soccer_has_xg(self):
        text = _format_soccer_stats(_soccer_pred())
        assert "1.52" in text

    def test_soccer_has_over_btts(self):
        text = _format_soccer_stats(_soccer_pred())
        assert "Over 2.5" in text or "2.5" in text
        assert "BTTS" in text

    def test_nba_has_match_line(self):
        text = _format_nba_stats(_nba_pred())
        assert "Lakers" in text
        assert "Celtics" in text
        assert "55.8" in text

    def test_nba_has_off_rtg(self):
        text = _format_nba_stats(_nba_pred())
        assert "112.3" in text

    def test_mlb_has_pitcher_info(self):
        text = _format_mlb_stats(_mlb_pred())
        assert "Gerrit Cole" in text
        assert "2.63" in text
        assert "11.2" in text

    def test_nfl_has_spread(self):
        text = _format_nfl_stats(_nfl_pred())
        assert "5.2" in text
        assert "Chiefs" in text

    def test_tennis_has_elo(self):
        text = _format_tennis_stats(_tennis_pred())
        assert "2350" in text
        assert "Sinner" in text
        assert "clay" in text.lower()

    def test_format_prediction_routes_soccer(self):
        text = _format_prediction(_soccer_pred(), "soccer")
        assert "Real Madrid" in text

    def test_format_prediction_routes_nba(self):
        text = _format_prediction(_nba_pred(), "nba")
        assert "Lakers" in text

    def test_format_prediction_routes_mlb(self):
        text = _format_prediction(_mlb_pred(), "mlb")
        assert "Yankees" in text

    def test_format_prediction_routes_nfl(self):
        text = _format_prediction(_nfl_pred(), "nfl")
        assert "Chiefs" in text

    def test_format_prediction_routes_tennis(self):
        text = _format_prediction(_tennis_pred(), "tennis")
        assert "Sinner" in text

    def test_format_prediction_generic_fallback(self):
        pred = {"home": "TeamX", "away": "TeamY", "home_win": 60.0}
        text = _format_prediction(pred, "unknown_sport")
        # Should not raise and should return some non-empty string
        assert isinstance(text, str)
        assert len(text) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# API call path (mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestWithMockedOpenAI:
    """Verify the call path when the API key is set and OpenAI responds."""

    def _mock_response(self, text: str):
        """Build a mock requests.Response that looks like an OpenAI response."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": text}}]
        }
        return mock_resp

    def test_analyze_prediction_returns_gpt_text(self):
        expected = "Este es un partido equilibrado con valor en el Over 2.5."
        with patch("core.ai_analysis._api_key", return_value="sk-fake"):
            with patch("requests.post", return_value=self._mock_response(expected)):
                result = analyze_prediction(_soccer_pred(), "soccer")
        assert result == expected

    def test_generate_parlay_narrative_returns_gpt_text(self):
        expected = "Parlay coherente con patas independientes."
        with patch("core.ai_analysis._api_key", return_value="sk-fake"):
            with patch("requests.post", return_value=self._mock_response(expected)):
                result = generate_parlay_narrative(_parlay_legs(), 28.5, "balanced")
        assert result == expected

    def test_answer_betting_question_returns_gpt_text(self):
        expected = "El Over 2.5 tiene valor estadístico en este partido."
        with patch("core.ai_analysis._api_key", return_value="sk-fake"):
            with patch("requests.post", return_value=self._mock_response(expected)):
                result = answer_betting_question("¿Vale el Over 2.5?")
        assert result == expected

    def test_answer_with_context_appended(self):
        expected = "Con esos datos, la respuesta es APOSTAR."
        with patch("core.ai_analysis._api_key", return_value="sk-fake"):
            with patch("requests.post", return_value=self._mock_response(expected)) as mock_post:
                result = answer_betting_question(
                    "¿Apostar?", context_text="xG: 2.1 vs 0.8"
                )
        # Verify context was included in the call
        call_args = mock_post.call_args
        user_content = call_args[1]["json"]["messages"][1]["content"]
        assert "xG: 2.1" in user_content
        assert result == expected

    def test_ai_picks_summary_returns_gpt_text(self):
        expected = "1. Real Madrid (48%) — valor en local. 2. Lakers — Over atractivo."
        with patch("core.ai_analysis._api_key", return_value="sk-fake"):
            with patch("requests.post", return_value=self._mock_response(expected)):
                result = ai_picks_summary([_soccer_pred(), _nba_pred()])
        assert result == expected

    def test_model_used_from_config(self):
        """Verify that the model from config is passed to OpenAI."""
        with patch("core.ai_analysis._api_key", return_value="sk-fake"):
            with patch("core.ai_analysis._model", return_value="gpt-4o"):
                with patch("requests.post", return_value=self._mock_response("ok")) as mock_post:
                    analyze_prediction(_soccer_pred(), "soccer")
        payload = mock_post.call_args[1]["json"]
        assert payload["model"] == "gpt-4o"

    def test_network_error_returns_warning_string(self):
        """A network error must not raise — it should return a warning string."""
        with patch("core.ai_analysis._api_key", return_value="sk-fake"):
            with patch("requests.post", side_effect=Exception("timeout")):
                result = analyze_prediction(_soccer_pred(), "soccer")
        assert "Error" in result or "error" in result

    def test_ai_picks_summary_caps_at_12_predictions(self):
        """ai_picks_summary should only pass the first 12 predictions to GPT."""
        preds = [_soccer_pred() for _ in range(20)]
        with patch("core.ai_analysis._api_key", return_value="sk-fake"):
            with patch("requests.post", return_value=self._mock_response("ok")) as mock_post:
                ai_picks_summary(preds)
        user_msg = mock_post.call_args[1]["json"]["messages"][1]["content"]
        # Count how many "Real Madrid vs Barcelona" lines appear
        line_count = user_msg.count("Real Madrid")
        assert line_count <= 12
