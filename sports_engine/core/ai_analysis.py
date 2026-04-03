"""
core/ai_analysis.py — AI-powered sports betting analysis via OpenAI.

Bridges the gap between the purely statistical models (Poisson, Elo, xG, etc.)
and human-readable, narrative-driven betting intelligence.

All functions are completely optional: when ``OPENAI_API_KEY`` is not set,
every function returns a polite fallback string so the bot degrades gracefully.

Model strategy (cost & quality)
--------------------------------
* ``gpt-4o-mini`` for analysis/Q&A  — fast, cheap, excellent for structured prompts.
* ``gpt-4o``      for Vision OCR    — required for image quality (set elsewhere).
The model can be overridden via the ``OPENAI_MODEL`` environment variable.

Public API
----------
``analyze_prediction(pred, sport)``
    Takes any sport predictor's result dict and returns a GPT-powered
    narrative analysis with risk assessment and betting recommendation.

``generate_parlay_narrative(legs, combined_prob, tiers)``
    Generates a concise AI story for a full parlay card.

``answer_betting_question(question, context_text)``
    Free-form sports betting Q&A. The bot passes the question + optional
    statistical context; GPT answers as an expert sports analyst.

``is_available()``
    Returns True when the API key is configured and the module is usable.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

def _api_key() -> str:
    try:
        from core.config import OPENAI_API_KEY  # type: ignore
        return OPENAI_API_KEY
    except Exception:
        return os.getenv("OPENAI_API_KEY", "")


def _model() -> str:
    try:
        from core.config import OPENAI_MODEL, OPENAI_MODEL_DEFAULT  # type: ignore
        return OPENAI_MODEL or OPENAI_MODEL_DEFAULT
    except Exception:
        return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def is_available() -> bool:
    """Return True when an OpenAI API key is configured."""
    return bool(_api_key())


# ── HTTP helper ───────────────────────────────────────────────────────────────

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

_SYSTEM_PROMPT = (
    "Eres un analista deportivo experto en apuestas estadísticas. "
    "Usas datos estadísticos reales (xG, Elo, ERA, eficiencia ofensiva/defensiva, "
    "forma reciente, etc.) para dar análisis objetivos, concisos y fundamentados. "
    "Siempre incluyes: probabilidad evaluada, nivel de riesgo (BAJO/MEDIO/ALTO), "
    "valor esperado (si tienes cuota), y una recomendación clara (APOSTAR / EVITAR / WATCH). "
    "Respondes en español. Eres honesto sobre la incertidumbre y evitas el sesgo "
    "hacia equipos populares o favoritos del público. "
    "Formato: párrafos cortos, sin listas largas innecesarias, máximo 300 palabras."
)

_RATE_LIMIT_DELAY = 1.0  # seconds between calls to avoid hitting rate limits

# Retry settings for 429 / 5xx transient errors
_MAX_RETRIES  = 3
_RETRY_BASE   = 2.0   # exponential-backoff base (seconds): 2, 4, 8 …

# Simple in-process response cache: (messages_hash, max_tokens) → (text, ts)
_RESPONSE_CACHE: dict = {}
_CACHE_TTL = 300  # 5 minutes — avoids duplicate API calls for identical prompts


def _call_openai(
    messages: list[dict],
    max_tokens: int = 500,
    temperature: float = 0.4,
) -> str:
    """
    Call the OpenAI Chat Completions endpoint with retry on 429/5xx.

    Retry strategy
    --------------
    * Up to ``_MAX_RETRIES`` attempts.
    * On HTTP 429 (rate-limit) or 5xx (server error) waits
      ``_RETRY_BASE ** attempt`` seconds before retrying.
    * Other HTTP errors are re-raised immediately (no retry).
    * Identical prompt+token combos are served from an in-process cache
      for up to 5 minutes to reduce unnecessary API calls.

    Returns the model's text response, or raises RuntimeError on failure.
    """
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY no configurado")

    # ── In-process cache lookup ───────────────────────────────────────────
    cache_key = (str(messages), max_tokens)
    now = time.time()
    if cache_key in _RESPONSE_CACHE:
        cached_text, cached_ts = _RESPONSE_CACHE[cache_key]
        if now - cached_ts < _CACHE_TTL:
            logger.debug("_call_openai: serving from cache")
            return cached_text

    payload = {
        "model": _model(),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_exc: Exception = RuntimeError("No se pudo contactar la IA")
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.post(
                _OPENAI_CHAT_URL,
                headers=headers,
                json=payload,
                timeout=30,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                # Transient error — back off and retry
                wait = _RETRY_BASE ** attempt
                logger.warning(
                    "_call_openai: HTTP %s on attempt %d/%d, retrying in %.1fs",
                    resp.status_code, attempt + 1, _MAX_RETRIES, wait,
                )
                time.sleep(wait)
                last_exc = requests.exceptions.HTTPError(
                    f"HTTP {resp.status_code}", response=resp
                )
                continue
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            _RESPONSE_CACHE[cache_key] = (text, time.time())
            return text
        except requests.exceptions.Timeout as exc:
            wait = _RETRY_BASE ** attempt
            logger.warning("_call_openai: timeout attempt %d, retrying in %.1fs", attempt + 1, wait)
            time.sleep(wait)
            last_exc = exc
        except requests.exceptions.HTTPError as exc:
            # Non-retryable HTTP error — raise immediately
            raise
        except Exception as exc:
            last_exc = exc
            break

    raise last_exc


# ── Internal prompt builders ──────────────────────────────────────────────────

def _format_soccer_stats(pred: dict) -> str:
    lines = [
        f"Partido: {pred.get('home', '?')} vs {pred.get('away', '?')}",
        f"Liga: {pred.get('league', 'Desconocida')}",
        f"Probabilidades (1X2): "
        f"Local {pred.get('home_win', '?')}% / "
        f"Empate {pred.get('draw', '?')}% / "
        f"Visitante {pred.get('away_win', '?')}%",
    ]
    if pred.get("xg_home") is not None:
        xg_away = pred.get("xg_away")
        xg_away_str = f"{xg_away:.2f}" if xg_away is not None else "?"
        lines.append(
            f"xG esperado: Local {pred['xg_home']:.2f} — Visitante {xg_away_str}"
        )
    if pred.get("over_2_5") is not None:
        lines.append(f"Over 2.5: {pred['over_2_5']}% | BTTS: {pred.get('btts', '?')}%")
    if pred.get("form_home"):
        lines.append(f"Forma local (5 jornadas): {pred['form_home']}")
    if pred.get("form_away"):
        lines.append(f"Forma visitante (5 jornadas): {pred['form_away']}")
    if pred.get("h2h_summary"):
        lines.append(f"H2H resumen: {pred['h2h_summary']}")
    if pred.get("confidence"):
        lines.append(f"Confianza del modelo: {pred['confidence']}")
    return "\n".join(lines)


def _format_nba_stats(pred: dict) -> str:
    lines = [
        f"Partido: {pred.get('home', '?')} vs {pred.get('away', '?')} (NBA 🏀)",
        f"Probabilidades: Local {pred.get('home_win', '?')}% / Visitante {pred.get('away_win', '?')}%",
        f"Proyección: {pred.get('home', '?')} {pred.get('expected_home', '?')} — "
        f"{pred.get('away', '?')} {pred.get('expected_away', '?')}",
        f"Spread esperado: {pred.get('spread', '?')} pts",
        f"Total proyectado (O/U): {pred.get('over_under', '?')} pts",
    ]
    if pred.get("home_off_rtg"):
        lines.append(
            f"OffRtg: {pred.get('home', '?')} {pred['home_off_rtg']} / "
            f"{pred.get('away', '?')} {pred.get('away_off_rtg', '?')}"
        )
    if pred.get("home_def_rtg"):
        lines.append(
            f"DefRtg: {pred.get('home', '?')} {pred['home_def_rtg']} / "
            f"{pred.get('away', '?')} {pred.get('away_def_rtg', '?')}"
        )
    if pred.get("confidence"):
        lines.append(f"Confianza del modelo: {pred['confidence']}")
    return "\n".join(lines)


def _format_mlb_stats(pred: dict) -> str:
    lines = [
        f"Partido: {pred.get('home', '?')} vs {pred.get('away', '?')} (MLB ⚾)",
        f"Probabilidades: Local {pred.get('home_win', '?')}% / Visitante {pred.get('away_win', '?')}%",
        f"Carreras proyectadas: {pred.get('expected_home', '?')} — {pred.get('expected_away', '?')}",
        f"Total O/U: {pred.get('over_under', '?')} carreras",
    ]

    def _pitcher_line(name_key: str, era_key: str, whip_key: str, k9_key: str,
                      hand_key: str, starts_key: str, label: str) -> None:
        name = pred.get(name_key)
        if not name:
            return
        era   = pred.get(era_key,   "?")
        whip  = pred.get(whip_key,  "?")
        k9    = pred.get(k9_key,    "?")
        hand  = pred.get(hand_key,  "")
        hand_s = f" [{hand}]" if hand else ""
        lines.append(f"Pitcher {label}: {name}{hand_s} (ERA {era} | WHIP {whip} | K/9 {k9})")
        starts = pred.get(starts_key) or []
        if starts:
            starts_str = "  ".join(
                f"{s.get('date','?')} {s.get('ip','?')}IP {s.get('er','?')}CE {s.get('k','?')}K ({s.get('result','?')})"
                for s in starts
            )
            lines.append(f"  Últimas salidas: {starts_str}")

    _pitcher_line("home_pitcher", "home_pitcher_era", "home_pitcher_whip",
                  "home_pitcher_k9", "home_pitcher_hand", "home_pitcher_recent_starts", "local")
    _pitcher_line("away_pitcher", "away_pitcher_era", "away_pitcher_whip",
                  "away_pitcher_k9", "away_pitcher_hand", "away_pitcher_recent_starts", "visitante")

    if pred.get("confidence"):
        lines.append(f"Confianza del modelo: {pred['confidence']}")
    return "\n".join(lines)


def _format_nfl_stats(pred: dict) -> str:
    lines = [
        f"Partido: {pred.get('home', '?')} vs {pred.get('away', '?')} (NFL 🏈)",
        f"Probabilidades: Local {pred.get('home_win', '?')}% / Visitante {pred.get('away_win', '?')}%",
        f"Spread esperado: {pred.get('spread', '?')} pts",
        f"Total proyectado: {pred.get('over_under', '?')} pts",
    ]
    if pred.get("confidence"):
        lines.append(f"Confianza del modelo: {pred['confidence']}")
    return "\n".join(lines)


def _format_tennis_stats(pred: dict) -> str:
    lines = [
        f"Partido: {pred.get('home', '?')} vs {pred.get('away', '?')} (Tenis 🎾)",
        f"Superficie: {pred.get('surface', '?').capitalize()}",
        f"Formato: Best of {pred.get('best_of', 3)}",
        f"Probabilidades: {pred.get('home', '?')} {pred.get('home_win', '?')}% / "
        f"{pred.get('away', '?')} {pred.get('away_win', '?')}%",
        f"Elo: {pred.get('home', '?')} {pred.get('elo_p1', '?')} — "
        f"{pred.get('away', '?')} {pred.get('elo_p2', '?')}",
    ]
    if pred.get("confidence"):
        lines.append(f"Confianza del modelo: {pred['confidence']}")
    return "\n".join(lines)


def _format_prediction(pred: dict, sport: str) -> str:
    """Convert a prediction dict into a human-readable stats block."""
    sport_l = (sport or "").lower()
    if "soccer" in sport_l or "football" in sport_l or "fútbol" in sport_l:
        return _format_soccer_stats(pred)
    if "nba" in sport_l or "basketball" in sport_l:
        return _format_nba_stats(pred)
    if "mlb" in sport_l or "baseball" in sport_l or "béisbol" in sport_l:
        return _format_mlb_stats(pred)
    if "nfl" in sport_l or "american" in sport_l:
        return _format_nfl_stats(pred)
    if "tennis" in sport_l or "tenis" in sport_l:
        return _format_tennis_stats(pred)
    # Generic fallback
    return json.dumps({k: v for k, v in pred.items() if isinstance(v, (str, int, float, bool))}, ensure_ascii=False)


# ── Public API ────────────────────────────────────────────────────────────────

_NO_KEY_MSG = (
    "🤖 _Análisis IA no disponible_ — configura `OPENAI_API_KEY` "
    "en las variables de entorno para activar el análisis GPT."
)

# Human-friendly messages for common API error scenarios.
# These replace the raw exception string (which can contain underscores, URLs,
# and other characters that break Telegram MarkdownV1 inside _…_ italic blocks).
def _friendly_ai_error(exc: Exception) -> str:
    """Return a short, Markdown-safe description of an OpenAI API failure.

    Never exposes raw HTTP error details (URLs, status lines) that could
    contain underscores or other Telegram MarkdownV1 special characters.
    """
    msg = str(exc)
    if "429" in msg or "rate" in msg.lower() or "Too Many" in msg:
        return "límite de solicitudes alcanzado — intenta en unos segundos"
    if "401" in msg or "Unauthorized" in msg or "authentication" in msg.lower():
        return "API key inválida o expirada"
    if "403" in msg or "Forbidden" in msg:
        return "acceso denegado por OpenAI"
    if "500" in msg or "502" in msg or "503" in msg or "server" in msg.lower():
        return "error temporal del servidor de IA"
    if "timeout" in msg.lower() or "Timeout" in msg:
        return "tiempo de espera agotado — intenta de nuevo"
    if "OPENAI_API_KEY" in msg:
        return "API key de OpenAI no configurada"
    # Generic fallback — still Markdown-safe (no underscores / asterisks)
    return "no se pudo contactar el servicio de IA"


def analyze_prediction(pred: dict, sport: str = "soccer") -> str:
    """
    Generate a GPT-powered narrative analysis for a match prediction.

    Parameters
    ----------
    pred : dict
        Result dict from any sport predictor (football.get_full_prediction,
        basketball.predict_game, baseball.predict_game, tennis.predict_match, etc.)
    sport : str
        Sport name for context ("soccer", "nba", "mlb", "nfl", "tennis").

    Returns
    -------
    str
        Markdown-safe analysis text, or a polite fallback if no API key.
    """
    if not is_available():
        return _NO_KEY_MSG

    stats_block = _format_prediction(pred, sport)

    user_msg = (
        f"Analiza este partido con los siguientes datos estadísticos:\n\n"
        f"{stats_block}\n\n"
        f"Proporciona:\n"
        f"1. Evaluación de las probabilidades del modelo (¿son razonables?)\n"
        f"2. Factores clave que inclinan la balanza\n"
        f"3. Mercados con mayor valor (1X2, Over/Under, BTTS, spread, etc.)\n"
        f"4. Nivel de riesgo (BAJO / MEDIO / ALTO) con justificación\n"
        f"5. Recomendación final: APOSTAR / EVITAR / WATCH\n"
    )

    try:
        time.sleep(_RATE_LIMIT_DELAY)
        return _call_openai(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=600,
        )
    except Exception as exc:
        logger.warning("AI analyze_prediction failed: %s", exc)
        return f"⚠️ _Error al consultar IA: {_friendly_ai_error(exc)}_"


def generate_parlay_narrative(
    legs: list[dict],
    combined_prob: float,
    tier: str = "balanced",
) -> str:
    """
    Generate a short AI narrative for a parlay card.

    Parameters
    ----------
    legs : list[dict]
        List of parlay leg dicts (match, pick, prob, sport_emoji, etc.).
    combined_prob : float
        Combined probability of the parlay (%).
    tier : str
        "safe" | "balanced" | "risky"

    Returns
    -------
    str
        A 2–4 sentence GPT narrative, or fallback string.
    """
    if not is_available():
        return _NO_KEY_MSG

    tier_labels = {"safe": "SEGURA", "balanced": "BALANCEADA", "risky": "ARRIESGADA"}
    tier_label = tier_labels.get(tier, tier.upper())

    picks_text = "\n".join(
        f"  • {leg.get('sport_emoji', '')} {leg['match']} → {leg['pick']} ({leg['prob']}%)"
        for leg in legs
    )

    user_msg = (
        f"Tengo un parlay {tier_label} con {len(legs)} patas y probabilidad combinada "
        f"de {combined_prob:.1f}%:\n\n{picks_text}\n\n"
        f"En 2-4 oraciones: ¿por qué este parlay tiene o no sentido estadístico? "
        f"¿Hay picks correlacionados o en conflicto? "
        f"¿Cuál es el pick más débil? Sé conciso."
    )

    try:
        time.sleep(_RATE_LIMIT_DELAY)
        return _call_openai(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=300,
            temperature=0.5,
        )
    except Exception as exc:
        logger.warning("AI generate_parlay_narrative failed: %s", exc)
        return f"⚠️ _Error al generar narrativa IA: {_friendly_ai_error(exc)}_"


def answer_betting_question(question: str, context_text: str = "") -> str:
    """
    Answer a free-form sports betting question using GPT.

    Parameters
    ----------
    question : str
        The user's question (e.g. "¿Tiene valor el Over 2.5 en el Clásico?")
    context_text : str, optional
        Additional statistical context to include in the prompt (e.g. stats
        from a previous /predict run, team form, odds).

    Returns
    -------
    str
        GPT answer string (Markdown safe), or fallback.
    """
    if not is_available():
        return _NO_KEY_MSG

    user_msg = question
    if context_text:
        user_msg = (
            f"Contexto estadístico disponible:\n{context_text}\n\n"
            f"Pregunta: {question}"
        )

    try:
        time.sleep(_RATE_LIMIT_DELAY)
        return _call_openai(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=500,
            temperature=0.5,
        )
    except Exception as exc:
        logger.warning("AI answer_betting_question failed: %s", exc)
        return f"⚠️ _Error al consultar IA: {_friendly_ai_error(exc)}_"


def ai_picks_summary(predictions: list[dict]) -> str:
    """
    Given a list of today's match predictions, ask GPT to highlight the
    top 3 value picks with brief reasoning.

    Parameters
    ----------
    predictions : list[dict]
        Each dict must have at least: home, away, home_win, away_win,
        confidence, sport (or sport_emoji).

    Returns
    -------
    str
        GPT-generated top picks summary, or fallback.
    """
    if not is_available():
        return _NO_KEY_MSG
    if not predictions:
        return "⚠️ _No hay predicciones disponibles para analizar._"

    lines = []
    for p in predictions[:12]:  # cap at 12 to stay within token limit
        sport = p.get("sport") or p.get("sport_emoji", "")
        home_win = p.get("home_win", "?")
        away_win = p.get("away_win", "?")
        conf = p.get("confidence", "?")
        lines.append(
            f"  {sport} {p.get('home', '?')} vs {p.get('away', '?')}: "
            f"Local {home_win}% / Visitante {away_win}% (conf: {conf})"
        )

    preds_text = "\n".join(lines)

    user_msg = (
        f"Estos son los partidos del día con sus probabilidades del modelo:\n\n"
        f"{preds_text}\n\n"
        f"¿Cuáles son los 3 picks con mayor valor estadístico real hoy? "
        f"Justifica brevemente cada uno (máximo 2 líneas por pick). "
        f"Ignora los partidos demasiado equilibrados o con baja confianza."
    )

    try:
        time.sleep(_RATE_LIMIT_DELAY)
        return _call_openai(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=500,
        )
    except Exception as exc:
        logger.warning("AI ai_picks_summary failed: %s", exc)
        return f"⚠️ _Error al generar resumen IA: {_friendly_ai_error(exc)}_"
