"""
Player Impact Model — Sports Engine.

Quantifies the effect of individual player absences or presences on
expected goals (xG) and match probabilities.

The model uses a tiered rating system (1–10) for each player and maps
that rating to an xG multiplier based on:
  - Position (striker > midfielder > defender > goalkeeper)
  - Role type: star scorer, playmaker, set-piece taker, defensive anchor
  - Team dependency: how central the player is to the team's build-up

Built-in player database covers major leagues. Unknown players can be
entered manually with a custom rating.

Impact formula
──────────────
  xG_adjusted = xG_base × (1 + Σ impact_i × direction_i)

  direction = +1 (player available) | -1 (player absent)
  impact_i  = position_weight × (rating / 10) × dependency_weight
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────
# POSITION WEIGHTS (attack impact)
# ─────────────────────────────────────────────────────────────────

_POSITION_ATTACK_WEIGHT = {
    "striker":    0.18,
    "winger":     0.12,
    "cam":        0.10,    # attacking midfielder
    "cm":         0.06,    # central midfielder
    "dm":         0.03,    # defensive midfielder
    "fullback":   0.04,
    "cb":         0.02,    # centre-back
    "gk":         0.01,
}

_POSITION_DEFENSE_WEIGHT = {
    "striker":    0.01,
    "winger":     0.02,
    "cam":        0.02,
    "cm":         0.05,
    "dm":         0.10,
    "fullback":   0.08,
    "cb":         0.12,
    "gk":         0.15,
}


# ─────────────────────────────────────────────────────────────────
# MINI PLAYER DATABASE (quality rating 1-10)
# ─────────────────────────────────────────────────────────────────

_PLAYER_DB: Dict[str, Dict] = {
    # La Liga
    "vinicius":        {"position": "winger",  "rating": 9.5, "team": "Real Madrid"},
    "benzema":         {"position": "striker",  "rating": 9.2, "team": "Al-Ittihad"},
    "bellingham":      {"position": "cam",      "rating": 9.3, "team": "Real Madrid"},
    "yamal":           {"position": "winger",   "rating": 9.0, "team": "Barcelona"},
    "lewandowski":     {"position": "striker",  "rating": 9.0, "team": "Barcelona"},
    "pedri":           {"position": "cm",       "rating": 8.8, "team": "Barcelona"},
    # Premier League
    "haaland":         {"position": "striker",  "rating": 9.8, "team": "Man City"},
    "salah":           {"position": "winger",   "rating": 9.4, "team": "Liverpool"},
    "kane":            {"position": "striker",  "rating": 9.3, "team": "Bayern Munich"},
    "saka":            {"position": "winger",   "rating": 8.8, "team": "Arsenal"},
    "son":             {"position": "winger",   "rating": 8.7, "team": "Tottenham"},
    "de bruyne":       {"position": "cam",      "rating": 9.3, "team": "Man City"},
    "van dijk":        {"position": "cb",       "rating": 9.0, "team": "Liverpool"},
    # Bundesliga
    "musiala":         {"position": "cam",      "rating": 9.0, "team": "Bayern Munich"},
    "gnabry":          {"position": "winger",   "rating": 8.3, "team": "Bayern Munich"},
    # Serie A
    "osimhen":         {"position": "striker",  "rating": 9.0, "team": "Napoli"},
    "lautaro":         {"position": "striker",  "rating": 9.0, "team": "Inter Milan"},
    # Liga MX
    "henry martin":    {"position": "striker",  "rating": 8.0, "team": "América"},
    "chicote calvo":   {"position": "striker",  "rating": 7.5, "team": "América"},
    "uriel antuna":    {"position": "winger",   "rating": 7.8, "team": "Cruz Azul"},
    "rogelio funes mori": {"position": "striker", "rating": 7.8, "team": "Monterrey"},
    "germán berterame":  {"position": "striker",  "rating": 8.2, "team": "Monterrey"},
    # MLS/other
    "messi":           {"position": "cam",      "rating": 9.7, "team": "Inter Miami"},
    "ronaldo":         {"position": "striker",  "rating": 9.5, "team": "Al-Nassr"},
    "mbappé":          {"position": "striker",  "rating": 9.6, "team": "Real Madrid"},
    "neymar":          {"position": "winger",   "rating": 9.0, "team": "Al-Hilal"},
}


def _lookup_player(name: str) -> Optional[Dict]:
    key = name.lower().strip()
    if key in _PLAYER_DB:
        return _PLAYER_DB[key]
    # Partial match
    for db_key, data in _PLAYER_DB.items():
        if key in db_key or db_key in key:
            return data
    return None


# ─────────────────────────────────────────────────────────────────
# IMPACT CALCULATION
# ─────────────────────────────────────────────────────────────────

@dataclass
class PlayerStatus:
    """A player's status for a specific match."""
    name:      str
    status:    str          # "available" | "absent" | "doubt" | "returning"
    position:  str = "cm"
    rating:    float = 7.0
    team:      str = ""


def _direction(status: str) -> float:
    """Convert status to +1 / -1 / 0.5 direction multiplier."""
    return {"available": 1.0, "absent": -1.0, "doubt": -0.5, "returning": 0.7}.get(status, 0.0)


def player_xg_impact(
    xg_base: float,
    players: List[PlayerStatus],
    is_attack: bool = True,
) -> Tuple[float, List[Dict]]:
    """
    Compute xG after applying player status impacts.

    Parameters
    ----------
    xg_base   : base expected goals (before player adjustments)
    players   : list of PlayerStatus objects
    is_attack : True = compute attack impact on xG scored;
                False = compute defense impact on xG conceded

    Returns
    -------
    (adjusted_xg, breakdown)
    breakdown = [{"player": str, "impact_pct": float, "direction": str}]
    """
    weights = _POSITION_ATTACK_WEIGHT if is_attack else _POSITION_DEFENSE_WEIGHT

    total_delta = 0.0
    breakdown   = []

    for p in players:
        w  = weights.get(p.position.lower(), 0.04)
        di = _direction(p.status)
        r  = min(max(p.rating, 1.0), 10.0)

        # Impact as fraction of xG
        impact = w * (r / 10.0) * di
        total_delta += impact

        breakdown.append({
            "player":     p.name,
            "position":   p.position,
            "rating":     r,
            "status":     p.status,
            "impact_pct": round(impact * 100, 2),
        })

    adjusted = round(max(xg_base * (1.0 + total_delta), 0.10), 3)

    return adjusted, sorted(breakdown, key=lambda x: abs(x["impact_pct"]), reverse=True)


def compute_team_player_impact(
    xg_attack_base:  float,
    xg_defense_base: float,   # xG conceded base
    team_players:    List[PlayerStatus],
) -> Dict:
    """
    Full team player-impact assessment.

    Returns
    -------
    {
        "xg_attack_adj":   float,
        "xg_defense_adj":  float,
        "attack_delta_pct":  float,
        "defense_delta_pct": float,
        "attack_breakdown":  list,
        "defense_breakdown": list,
    }
    """
    xg_att_adj, att_bd = player_xg_impact(xg_attack_base, team_players, is_attack=True)
    xg_def_adj, def_bd = player_xg_impact(xg_defense_base, team_players, is_attack=False)

    return {
        "xg_attack_adj":    xg_att_adj,
        "xg_defense_adj":   xg_def_adj,
        "attack_delta_pct": round((xg_att_adj / xg_attack_base - 1.0) * 100 if xg_attack_base > 0 else 0.0, 2),
        "defense_delta_pct": round((xg_def_adj / xg_defense_base - 1.0) * 100 if xg_defense_base > 0 else 0.0, 2),
        "attack_breakdown":  att_bd[:5],
        "defense_breakdown": def_bd[:5],
    }


def parse_player_statuses(text: str) -> List[PlayerStatus]:
    """
    Parse a free-text string of player statuses.

    Format: "Haaland:absent Saka:doubt De Bruyne:available"

    Unknown players are looked up in the database; unknown positions
    default to "cm" with rating 7.5.
    """
    players = []
    for token in text.strip().split():
        if ":" not in token:
            continue
        name_raw, status_raw = token.split(":", 1)
        name   = name_raw.replace("_", " ")
        status = status_raw.lower()
        db     = _lookup_player(name)
        if db:
            players.append(PlayerStatus(
                name=name, status=status,
                position=db["position"], rating=db["rating"], team=db.get("team", ""),
            ))
        else:
            players.append(PlayerStatus(
                name=name, status=status, position="cm", rating=7.5,
            ))
    return players


# ─────────────────────────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────────────────────────

def format_player_impact(
    team_name:  str,
    xg_base:    float,
    impact_data: Dict,
) -> str:
    """Format player impact report for Telegram."""
    att = impact_data["attack_delta_pct"]
    dfe = impact_data["defense_delta_pct"]
    att_str = f"+{att:.1f}%" if att >= 0 else f"{att:.1f}%"
    dfe_str = f"+{dfe:.1f}%" if dfe >= 0 else f"{dfe:.1f}%"

    lines = [
        f"👤 *Player Impact — {team_name}*",
        f"  xG base: `{xg_base:.2f}`",
        f"  xG ataque ajustado: `{impact_data['xg_attack_adj']:.2f}` ({att_str})",
        f"  xG defensa ajustada: `{impact_data['xg_defense_adj']:.2f}` ({dfe_str})",
        "",
        "  *Impactos:*",
    ]
    for b in impact_data["attack_breakdown"][:4]:
        status_e = {"available": "✅", "absent": "❌", "doubt": "⚠️", "returning": "🔄"}.get(b["status"], "❓")
        lines.append(
            f"  {status_e} {b['player']} ({b['position']}) "
            f"r={b['rating']:.0f} → `{b['impact_pct']:+.1f}%` xG"
        )
    return "\n".join(lines)
