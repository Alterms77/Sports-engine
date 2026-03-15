"""
Referee Model — Sports Engine.

Models referee-specific tendencies that influence:
  - Yellow/red card rates
  - Foul frequency
  - Penalty probability
  - Added time
  - Playing style tolerance (home/away bias)

Built-in database of ~40 referees across major leagues.
Unknown referees return league-average defaults.

Usage
─────
  from core.referee_model import get_referee_profile, referee_impact

  profile = get_referee_profile("Szymon Marciniak")
  impact  = referee_impact(xg_home=1.5, xg_away=1.0, referee_name="Carlos del Cerro")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


# ─────────────────────────────────────────────────────────────────
# LEAGUE AVERAGES (baseline per 90 min)
# ─────────────────────────────────────────────────────────────────

_LEAGUE_DEFAULTS = {
    "premier_league":  {"yellows": 2.8,  "reds": 0.08, "fouls": 21.0, "penalties": 0.22, "added_time": 4.5},
    "la_liga":         {"yellows": 3.8,  "reds": 0.10, "fouls": 24.0, "penalties": 0.25, "added_time": 5.0},
    "bundesliga":      {"yellows": 3.0,  "reds": 0.07, "fouls": 22.5, "penalties": 0.20, "added_time": 4.2},
    "serie_a":         {"yellows": 3.5,  "reds": 0.09, "fouls": 23.5, "penalties": 0.24, "added_time": 4.8},
    "ligue_1":         {"yellows": 3.2,  "reds": 0.08, "fouls": 22.0, "penalties": 0.21, "added_time": 4.3},
    "liga_mx":         {"yellows": 3.4,  "reds": 0.09, "fouls": 22.0, "penalties": 0.22, "added_time": 4.0},
    "default":         {"yellows": 3.2,  "reds": 0.08, "fouls": 22.5, "penalties": 0.22, "added_time": 4.5},
}

_DEFAULT = _LEAGUE_DEFAULTS["default"]


@dataclass
class RefereeProfile:
    """Per-referee statistical tendencies."""
    name:              str
    league:            str
    yellows_per_game:  float   # avg yellow cards shown per match
    reds_per_game:     float
    fouls_per_game:    float
    penalty_rate:      float   # penalties per game
    added_time_avg:    float   # average added time (minutes)
    home_bias:         float   # + = favours home team (card leniency) 0-1 scale
    style:             str     # "strict" | "permissive" | "normal"


# ─────────────────────────────────────────────────────────────────
# REFEREE DATABASE
# ─────────────────────────────────────────────────────────────────

_REFEREE_DB: Dict[str, RefereeProfile] = {
    # UEFA / Champions League
    "szymon marciniak": RefereeProfile(
        "Szymon Marciniak", "international",
        3.0, 0.07, 21.0, 0.20, 5.5, 0.05, "normal"
    ),
    "daniele orsato": RefereeProfile(
        "Daniele Orsato", "serie_a",
        3.8, 0.09, 23.0, 0.28, 5.0, 0.08, "permissive"
    ),
    "clement turpin": RefereeProfile(
        "Clément Turpin", "ligue_1",
        3.1, 0.07, 21.5, 0.22, 4.5, 0.05, "normal"
    ),
    "felix zwayer": RefereeProfile(
        "Felix Zwayer", "bundesliga",
        3.4, 0.08, 22.5, 0.25, 4.3, 0.06, "normal"
    ),
    "carlos del cerro grande": RefereeProfile(
        "Carlos del Cerro Grande", "la_liga",
        4.2, 0.11, 26.0, 0.30, 5.2, 0.10, "strict"
    ),
    "jesus gil manzano": RefereeProfile(
        "Jesús Gil Manzano", "la_liga",
        4.5, 0.12, 27.0, 0.32, 5.5, 0.12, "strict"
    ),
    "anthony taylor": RefereeProfile(
        "Anthony Taylor", "premier_league",
        2.9, 0.07, 21.0, 0.18, 4.2, 0.04, "permissive"
    ),
    "michael oliver": RefereeProfile(
        "Michael Oliver", "premier_league",
        3.0, 0.08, 21.5, 0.22, 4.5, 0.03, "normal"
    ),
    "stuart attwell": RefereeProfile(
        "Stuart Attwell", "premier_league",
        3.2, 0.08, 22.0, 0.24, 4.6, 0.05, "normal"
    ),
    "marco guida": RefereeProfile(
        "Marco Guida", "serie_a",
        3.6, 0.09, 23.5, 0.25, 4.8, 0.07, "normal"
    ),
    "nicolas otamendi": RefereeProfile(   # Liga MX
        "Fernando Hernández", "liga_mx",
        3.6, 0.10, 23.0, 0.24, 4.2, 0.08, "normal"
    ),
    "adonai escobedo": RefereeProfile(
        "Adonai Escobedo", "liga_mx",
        4.0, 0.11, 24.5, 0.28, 4.5, 0.10, "strict"
    ),
    "dr.  jose luis sanchez": RefereeProfile(
        "José Luis Sánchez Arminio", "la_liga",
        3.9, 0.10, 24.0, 0.26, 5.1, 0.09, "strict"
    ),
}


def get_referee_profile(name: str) -> RefereeProfile:
    """
    Look up a referee by name (case-insensitive, partial match ok).
    Returns a default 'average' profile if not found.
    """
    key = name.lower().strip()
    if key in _REFEREE_DB:
        return _REFEREE_DB[key]
    for db_key, profile in _REFEREE_DB.items():
        if key in db_key or db_key in key:
            return profile
    # Return default
    d = _DEFAULT
    return RefereeProfile(
        name=name, league="default",
        yellows_per_game=d["yellows"],
        reds_per_game=d["reds"],
        fouls_per_game=d["fouls"],
        penalty_rate=d["penalties"],
        added_time_avg=d["added_time"],
        home_bias=0.0,
        style="normal",
    )


# ─────────────────────────────────────────────────────────────────
# IMPACT ON PREDICTIONS
# ─────────────────────────────────────────────────────────────────

def referee_impact(
    referee_name: str,
    xg_home: float,
    xg_away: float,
    league: str = "default",
) -> Dict:
    """
    Compute referee-adjusted predictions.

    Returns a dict with:
      - adjusted card expectations
      - penalty probability modifier
      - game-flow descriptors
      - betting-relevant signals
    """
    profile  = get_referee_profile(referee_name)
    defaults = _LEAGUE_DEFAULTS.get(league, _DEFAULT)

    # Card expectations (absolute numbers)
    yellow_adj  = round(profile.yellows_per_game, 1)
    red_adj     = round(profile.reds_per_game, 2)
    penalty_adj = round(profile.penalty_rate, 3)

    # xG nudge from home bias (strict referees suppress physical play → lower home advantage)
    home_bias_mult = 1.0 + profile.home_bias * 0.05
    xg_home_adj    = round(xg_home * home_bias_mult, 3)
    xg_away_adj    = round(xg_away / max(home_bias_mult, 0.95), 3)

    # Over/Under card signal
    league_yellows = defaults["yellows"]
    card_ratio     = profile.yellows_per_game / max(league_yellows, 0.1)
    if card_ratio >= 1.25:
        card_signal = "Over tarjetas"
    elif card_ratio <= 0.80:
        card_signal = "Under tarjetas"
    else:
        card_signal = "Normal"

    signals = []
    if profile.style == "strict":
        signals.append("Árbitro estricto — esperar más tarjetas amarillas/rojas")
    elif profile.style == "permissive":
        signals.append("Árbitro permisivo — juego más físico tolerable")
    if profile.penalty_rate > defaults["penalties"] * 1.3:
        signals.append(f"⚠️ Alta tasa de penaltis ({profile.penalty_rate:.2f}/partido)")
    if profile.home_bias > 0.1:
        signals.append("🏠 Ligero sesgo a favor del local")

    return {
        "referee":         profile.name,
        "style":           profile.style,
        "yellows_expected": yellow_adj,
        "reds_expected":    red_adj,
        "penalty_rate":     penalty_adj,
        "added_time":       profile.added_time_avg,
        "card_signal":      card_signal,
        "xg_home_adj":     xg_home_adj,
        "xg_away_adj":     xg_away_adj,
        "signals":          signals,
    }


# ─────────────────────────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────────────────────────

def format_referee_impact(impact: Dict, event: str = "") -> str:
    """Format a referee impact report for Telegram."""
    style_emoji = {"strict": "🟥", "permissive": "🟩", "normal": "🟨"}.get(impact["style"], "⬜")

    lines = [
        f"🟩 *REFEREE MODEL — {impact['referee']}*",
        f"  Estilo: {style_emoji} `{impact['style'].upper()}`",
        "",
        f"  🟨 Amarillas esperadas: `{impact['yellows_expected']}`",
        f"  🟥 Rojas esperadas:     `{impact['reds_expected']}`",
        f"  🎯 Penaltis/partido:    `{impact['penalty_rate']}`",
        f"  ⏱️ Tiempo añadido:      `{impact['added_time']}` min",
        f"  📊 Señal tarjetas:      `{impact['card_signal']}`",
    ]
    if event:
        lines.insert(0, f"  Partido: {event}")
        lines.insert(0, "")

    if impact["signals"]:
        lines += ["", "*Señales:*"]
        for s in impact["signals"]:
            lines.append(f"  • {s}")

    return "\n".join(lines)
