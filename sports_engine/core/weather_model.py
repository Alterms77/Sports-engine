"""
Weather Model — Sports Engine.

Models the impact of weather conditions on football match outcomes and
betting markets. Weather affects:

  - Overall goals (rain, wind → lower xG)
  - Corners (wind → more corners, fewer long shots)
  - Cards (cold/slippery pitch → more defensive play, more fouls)
  - Playing style (heavy pitch → physical, short-pass teams suffer more)
  - Home advantage (extreme conditions favour physical/lower-quality home team)

Input format (flexible)
───────────────────────
  Condition string: "rain", "wind", "snow", "fog", "heat", "normal"
  Temperature:      degrees Celsius
  Wind speed:       km/h
  Precipitation:    mm/h

Impact scale: multiplier on xG (0.7 = -30% on xG, 1.1 = +10%)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class WeatherConditions:
    """Weather conditions for a match."""
    condition:     str      # "normal" | "rain" | "heavy_rain" | "wind" | "snow" | "fog" | "heat"
    temp_c:        float    # Celsius
    wind_kph:      float    # km/h
    precipitation: float    # mm/h
    pitch:         str      # "good" | "wet" | "heavy" | "frozen" | "artificial"


# ─────────────────────────────────────────────────────────────────
# IMPACT TABLES
# ─────────────────────────────────────────────────────────────────

_CONDITION_XG_MULT: Dict[str, float] = {
    "normal":     1.00,
    "light_rain": 0.97,
    "rain":       0.92,
    "heavy_rain": 0.84,
    "wind":       0.91,
    "heavy_wind": 0.83,
    "snow":       0.78,
    "fog":        0.90,
    "heat":       0.95,   # fatigue, less pressing
    "cold":       0.96,
}

_PITCH_XG_MULT: Dict[str, float] = {
    "good":       1.00,
    "wet":        0.95,
    "heavy":      0.88,
    "frozen":     0.82,
    "artificial": 1.02,
}

_CONDITION_CORNERS_MULT: Dict[str, float] = {
    "normal":     1.00,
    "light_rain": 0.98,
    "rain":       0.95,
    "heavy_rain": 0.90,
    "wind":       1.08,   # more misplaced crosses → corners
    "heavy_wind": 1.15,
    "snow":       0.92,
    "fog":        0.97,
    "heat":       0.98,
    "cold":       0.99,
}

_CONDITION_CARDS_MULT: Dict[str, float] = {
    "normal":     1.00,
    "light_rain": 1.02,
    "rain":       1.05,   # more fouls on slippery pitch
    "heavy_rain": 1.10,
    "wind":       1.03,
    "heavy_wind": 1.06,
    "snow":       1.08,
    "fog":        1.04,
    "heat":       1.05,   # tempers
    "cold":       1.01,
}

_WIND_XG_PENALTY: float = 0.002     # per km/h above 30 km/h
_RAIN_XG_PENALTY: float = 0.02      # per mm/h above 1 mm/h
_TEMP_HOT_CUTOFF: float = 30.0      # heat penalty starts here
_TEMP_COLD_CUTOFF: float = 5.0      # cold penalty starts here


def _normalise_condition(cond: str) -> str:
    """Map raw condition string to a canonical key."""
    c = cond.lower().strip()
    mapping = {
        "lluvia fuerte": "heavy_rain",
        "lluvia": "rain",
        "llovizna": "light_rain",
        "viento fuerte": "heavy_wind",
        "viento": "wind",
        "nieve": "snow",
        "niebla": "fog",
        "calor": "heat",
        "frio": "cold",
        "frío": "cold",
        "normal": "normal",
        "soleado": "normal",
        "despejado": "normal",
    }
    return mapping.get(c, c if c in _CONDITION_XG_MULT else "normal")


def compute_weather_impact(cond: WeatherConditions) -> Dict:
    """
    Compute match-level weather impact multipliers.

    Returns
    -------
    {
        "xg_mult":        float,  # multiply base xG by this
        "corners_mult":   float,
        "cards_mult":     float,
        "description":    str,
        "signals":        list[str],
        "severity":       str,    # "LOW" | "MEDIUM" | "HIGH"
    }
    """
    norm = _normalise_condition(cond.condition)

    xg_mult      = _CONDITION_XG_MULT.get(norm, 1.0)
    corners_mult = _CONDITION_CORNERS_MULT.get(norm, 1.0)
    cards_mult   = _CONDITION_CARDS_MULT.get(norm, 1.0)
    pitch_mult   = _PITCH_XG_MULT.get(cond.pitch, 1.0)

    # Continuous adjustments
    if cond.wind_kph > 30:
        wind_penalty = min((cond.wind_kph - 30) * _WIND_XG_PENALTY, 0.20)
        xg_mult      = max(xg_mult - wind_penalty, 0.60)

    if cond.precipitation > 1.0:
        rain_penalty = min((cond.precipitation - 1.0) * _RAIN_XG_PENALTY, 0.15)
        xg_mult      = max(xg_mult - rain_penalty, 0.60)

    if cond.temp_c > _TEMP_HOT_CUTOFF:
        heat_pen = min((cond.temp_c - _TEMP_HOT_CUTOFF) * 0.005, 0.08)
        xg_mult  = max(xg_mult - heat_pen, 0.70)

    if cond.temp_c < _TEMP_COLD_CUTOFF:
        cold_pen = min((_TEMP_COLD_CUTOFF - cond.temp_c) * 0.003, 0.06)
        xg_mult  = max(xg_mult - cold_pen, 0.70)

    xg_mult      = round(xg_mult * pitch_mult, 4)
    corners_mult = round(corners_mult, 4)
    cards_mult   = round(cards_mult, 4)

    # Severity
    impact = abs(1.0 - xg_mult)
    if impact >= 0.15:
        severity = "HIGH"
    elif impact >= 0.07:
        severity = "MEDIUM"
    else:
        severity = "LOW"

    signals = []
    if xg_mult < 0.90:
        signals.append(f"⬇️ xG reducido ~{(1-xg_mult)*100:.0f}% por condiciones")
    if corners_mult > 1.05:
        signals.append(f"🚩 Más córners esperados (+{(corners_mult-1)*100:.0f}% por viento)")
    if cards_mult > 1.05:
        signals.append(f"🟨 Más tarjetas esperadas (+{(cards_mult-1)*100:.0f}% por superficie)")
    if cond.pitch in ("heavy", "frozen"):
        signals.append(f"⚠️ Césped {cond.pitch} — equipos técnicos perjudicados")
    if severity == "HIGH":
        signals.append("🌧️ Condiciones extremas — Over 2.5 desfavorecido")

    desc_parts = [f"{cond.condition.capitalize()}", f"{cond.temp_c:.0f}°C"]
    if cond.wind_kph > 15:
        desc_parts.append(f"Viento {cond.wind_kph:.0f} km/h")
    if cond.precipitation > 0:
        desc_parts.append(f"Lluvia {cond.precipitation:.1f} mm/h")

    return {
        "xg_mult":      xg_mult,
        "corners_mult": corners_mult,
        "cards_mult":   cards_mult,
        "description":  " · ".join(desc_parts),
        "signals":      signals,
        "severity":     severity,
        "condition":    norm,
    }


def apply_weather_to_prediction(
    xg_home: float,
    xg_away: float,
    corners_total: float,
    weather: WeatherConditions,
) -> Dict:
    """
    Apply weather impact to a match prediction.

    Returns adjusted xG and corners values plus the impact report.
    """
    impact = compute_weather_impact(weather)

    return {
        "xg_home_adj":    round(xg_home * impact["xg_mult"], 3),
        "xg_away_adj":    round(xg_away * impact["xg_mult"], 3),
        "corners_adj":    round(corners_total * impact["corners_mult"], 1),
        "weather_impact": impact,
    }


def parse_weather_input(text: str) -> Optional[WeatherConditions]:
    """
    Parse a free-text weather string.

    Format: "lluvia 15 25 2 wet"
            condition temp_c wind_kph precip_mm pitch
    """
    parts = text.strip().split()
    if not parts:
        return None
    try:
        condition     = parts[0] if parts else "normal"
        temp_c        = float(parts[1]) if len(parts) > 1 else 15.0
        wind_kph      = float(parts[2]) if len(parts) > 2 else 10.0
        precipitation = float(parts[3]) if len(parts) > 3 else 0.0
        pitch         = parts[4] if len(parts) > 4 else "good"
        return WeatherConditions(
            condition=condition, temp_c=temp_c,
            wind_kph=wind_kph, precipitation=precipitation, pitch=pitch
        )
    except (ValueError, IndexError):
        return None


# ─────────────────────────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────────────────────────

_SEVERITY_EMOJI = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}
_COND_EMOJI = {
    "normal":     "☀️",
    "light_rain": "🌦️",
    "rain":       "🌧️",
    "heavy_rain": "⛈️",
    "wind":       "💨",
    "heavy_wind": "🌪️",
    "snow":       "❄️",
    "fog":        "🌫️",
    "heat":       "🥵",
    "cold":       "🥶",
}


def format_weather_impact(impact: Dict, event: str = "") -> str:
    """Format weather impact for Telegram."""
    sev_e  = _SEVERITY_EMOJI.get(impact["severity"], "⚪")
    cond_e = _COND_EMOJI.get(impact["condition"], "🌤️")

    lines = [
        f"{cond_e} *WEATHER MODEL*",
    ]
    if event:
        lines.append(f"  {event}")
    lines += [
        f"  {impact['description']}",
        "",
        f"  Impacto: {sev_e} `{impact['severity']}`",
        f"  xG mult:      `{impact['xg_mult']:.3f}` "
        f"({round((impact['xg_mult']-1)*100, 1):+.1f}%)",
        f"  Córners mult: `{impact['corners_mult']:.3f}` "
        f"({round((impact['corners_mult']-1)*100, 1):+.1f}%)",
        f"  Tarjetas mult:`{impact['cards_mult']:.3f}` "
        f"({round((impact['cards_mult']-1)*100, 1):+.1f}%)",
    ]

    if impact["signals"]:
        lines += ["", "*Señales:*"]
        for s in impact["signals"]:
            lines.append(f"  • {s}")

    return "\n".join(lines)
