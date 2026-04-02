"""
Tennis prediction engine (ATP / WTA).

Model: Elo-based win probability with surface adjustment.

  elo_win_prob(A, B) = 1 / (1 + 10^((elo_B − elo_A) / 400))

  surface_adj modifies each player's effective Elo based on their known
  surface preference (clay / grass / hard specialist ratings).  The per-player
  surface tag lives in _PLAYER_SURFACE_TYPE; the Elo delta for each
  (playing_surface, specialist_type) combo is in SURFACE_ELO_ADJ.

  match_win_prob is derived from set_win_prob via a binomial model:
    best-of-3:  P(win) = p² + 2p²(1−p)     (win 2-0 or 2-1)
    best-of-5:  P(win) = p³(1 + 3q + 6q²)  (win 3-0, 3-1, or 3-2)

ATP ranking → Elo approximation (inverse log scale):
  rank 1    ≈ 2400 Elo
  rank 10   ≈ 2200
  rank 50   ≈ 2050
  rank 100  ≈ 1950
  rank 200+ ≈ 1850
"""

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ── Surface Elo adjustments ───────────────────────────────────────────────────
# Outer key = surface being played on.
# Inner key = player's specialist type.
# Value     = Elo points added to that player's base rating.
#
# Calibration (empirical, 2019-24 ATP/WTA):
#   Clay specialists gain ~+35 on clay vs hard; lose ~-25 on grass.
#   Grass specialists gain ~+30 on grass; lose ~-20 on clay.
#   Hard-court neutral players get a small +10 bonus on hard courts.
SURFACE_ELO_ADJ = {
    "clay":  {"clay":  +35, "grass": -20, "hard": -10, "neutral":  0},
    "grass": {"clay":  -20, "grass": +30, "hard":  -5, "neutral":  0},
    "hard":  {"clay":  -10, "grass":  -5, "hard": +10, "neutral":  0},
}

# ── Per-player surface preference ────────────────────────────────────────────
# "clay"    = strong clay-court specialist
# "grass"   = strong grass-court specialist
# "hard"    = hard-court specialist / indoor specialist
# "neutral" = no strong surface preference (all-around player)
_PLAYER_SURFACE_TYPE: dict[str, str] = {
    # ATP
    "Novak Djokovic":        "hard",     # dominant on hard; solid everywhere
    "Carlos Alcaraz":        "clay",     # clay specialist, also good on grass
    "Jannik Sinner":         "hard",
    "Daniil Medvedev":       "hard",
    "Alexander Zverev":      "clay",
    "Andrey Rublev":         "clay",
    "Stefanos Tsitsipas":    "clay",
    "Taylor Fritz":          "hard",
    "Hubert Hurkacz":        "hard",
    "Holger Rune":           "clay",
    "Alex de Minaur":        "hard",
    "Felix Auger-Aliassime": "hard",
    "Denis Shapovalov":      "hard",
    "Roger Federer":         "grass",
    "Rafael Nadal":          "clay",
    "Andy Murray":           "hard",
    # WTA
    "Iga Swiatek":           "clay",
    "Aryna Sabalenka":       "hard",
    "Coco Gauff":            "hard",
    "Elena Rybakina":        "grass",
    "Jessica Pegula":        "hard",
    "Madison Keys":          "hard",
    "Ons Jabeur":            "grass",
    "Karolina Pliskova":     "hard",
    "Simona Halep":          "clay",
    "Bianca Andreescu":      "hard",
    "Venus Williams":        "hard",
    "Serena Williams":       "hard",
    "Petra Kvitova":         "grass",
    "Caroline Wozniacki":    "hard",
}

# ── ATP/WTA player aliases ────────────────────────────────────────────────────
_ATP_ALIASES: dict = {
    # Djokovic
    "djokovic": "Novak Djokovic",
    "novak": "Novak Djokovic",
    "nole": "Novak Djokovic",
    # Alcaraz
    "alcaraz": "Carlos Alcaraz",
    "carlos alcaraz": "Carlos Alcaraz",
    # Sinner
    "sinner": "Jannik Sinner",
    "jannik": "Jannik Sinner",
    # Medvedev
    "medvedev": "Daniil Medvedev",
    "daniil": "Daniil Medvedev",
    # Zverev
    "zverev": "Alexander Zverev",
    "sascha": "Alexander Zverev",
    # Rublev
    "rublev": "Andrey Rublev",
    # Tsitsipas
    "tsitsipas": "Stefanos Tsitsipas",
    "stefanos": "Stefanos Tsitsipas",
    # Fritz
    "fritz": "Taylor Fritz",
    "taylor fritz": "Taylor Fritz",
    # De Minaur
    "de minaur": "Alex de Minaur",
    "deminaur": "Alex de Minaur",
    # Hurkacz
    "hurkacz": "Hubert Hurkacz",
    # Rune
    "rune": "Holger Rune",
    # Federer (retired but famous)
    "federer": "Roger Federer",
    "roger": "Roger Federer",
    # Nadal (retired)
    "nadal": "Rafael Nadal",
    "rafa": "Rafael Nadal",
    # Murray (retired/semi-active)
    "murray": "Andy Murray",
    # Shapovalov
    "shapovalov": "Denis Shapovalov",
    # Auger-Aliassime
    "auger": "Felix Auger-Aliassime",
    "faa": "Felix Auger-Aliassime",
    # Women's
    "swiatek": "Iga Swiatek",
    "iga": "Iga Swiatek",
    "sabalenka": "Aryna Sabalenka",
    "aryna": "Aryna Sabalenka",
    "gauff": "Coco Gauff",
    "coco": "Coco Gauff",
    "rybakina": "Elena Rybakina",
    "pegula": "Jessica Pegula",
    "keys": "Madison Keys",
    "jabeur": "Ons Jabeur",
    "kvitova": "Petra Kvitova",
    "pliskova": "Karolina Pliskova",
    "halep": "Simona Halep",
    "andreescu": "Bianca Andreescu",
    "wozniacki": "Caroline Wozniacki",
    "venus": "Venus Williams",
    "serena": "Serena Williams",
}

# Known player Elo ratings (approximated from recent ATP/WTA rankings)
# These serve as fallbacks when ESPN API data is not available.
_KNOWN_ELO: dict = {
    "Novak Djokovic":     2390,
    "Carlos Alcaraz":     2360,
    "Jannik Sinner":      2350,
    "Daniil Medvedev":    2290,
    "Alexander Zverev":   2240,
    "Andrey Rublev":      2180,
    "Stefanos Tsitsipas": 2160,
    "Taylor Fritz":       2150,
    "Hubert Hurkacz":     2130,
    "Holger Rune":        2100,
    "Alex de Minaur":     2090,
    "Felix Auger-Aliassime": 2080,
    "Denis Shapovalov":   2040,
    "Roger Federer":      2380,  # retired peak
    "Rafael Nadal":       2360,  # retired peak
    "Andy Murray":        2100,
    # Women
    "Iga Swiatek":        2340,
    "Aryna Sabalenka":    2280,
    "Coco Gauff":         2180,
    "Elena Rybakina":     2160,
    "Jessica Pegula":     2100,
    "Madison Keys":       2050,
    "Ons Jabeur":         2050,
    "Karolina Pliskova":  2040,
    "Simona Halep":       2100,
    "Bianca Andreescu":   2020,
    "Venus Williams":     2100,
    "Serena Williams":    2350,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def ranking_to_elo(rank: int) -> float:
    """
    Approximate Elo from ATP/WTA ranking (empirical inverse-log scale).
    """
    if rank <= 1:
        return 2400
    elif rank <= 5:
        return 2350 - (rank - 1) * 12
    elif rank <= 20:
        return 2302 - (rank - 5) * 8
    elif rank <= 50:
        return 2182 - (rank - 20) * 4
    elif rank <= 100:
        return 2062 - (rank - 50) * 2
    elif rank <= 200:
        return 1962 - (rank - 100) * 1
    else:
        return max(1600, 1862 - (rank - 200) * 0.5)


def elo_win_prob(elo_a: float, elo_b: float) -> float:
    """Elo win probability for player A against player B."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


def set_to_match_win_prob(p_set: float, best_of: int = 3) -> float:
    """
    Convert per-set win probability to match win probability.
    best_of=3: first to 2 sets; best_of=5: first to 3 sets.
    """
    p = p_set
    q = 1.0 - p
    if best_of == 3:
        # Win 2-0: p² | Win 2-1: 2·p²·q
        return p ** 2 + 2 * p ** 2 * q
    else:
        # Win 3-0: p³ | Win 3-1: 3·p³·q | Win 3-2: 6·p³·q²
        return p ** 3 * (1 + 3 * q + 6 * q ** 2)


def resolve_player(name: str) -> Optional[str]:
    key = name.strip().lower()
    if key in _ATP_ALIASES:
        return _ATP_ALIASES[key]
    for alias, canonical in _ATP_ALIASES.items():
        if key in alias or alias in key:
            return canonical
    return None


def suggest_players(name: str, top_n: int = 3) -> list:
    key = name.strip().lower()
    seen: set = set()
    results = []
    for alias, canonical in _ATP_ALIASES.items():
        if canonical not in seen and (key in alias or alias in key):
            seen.add(canonical)
            results.append(canonical)
            if len(results) >= top_n:
                break
    return results


def _get_player_elo(player_name: str) -> float:
    """Return best-available Elo for a player.

    Priority:
    1. Hardcoded Elo from ``_KNOWN_ELO`` (top players, always available).
    2. ESPN ATP/WTA ranking → convert via ``ranking_to_elo()``.
    3. League-default for an unranked / unknown player (rank 100 equivalent).
    """
    # 1. Hardcoded known Elo
    if player_name in _KNOWN_ELO:
        return _KNOWN_ELO[player_name]

    # 2. Try ESPN ranking (tennis player search)
    try:
        from api.espn_api import get_team_season_stats
        stats = get_team_season_stats("tennis", player_name)
        if stats:
            rank = stats.get("rank") or stats.get("ranking")
            if rank:
                rank_int = int(float(rank))
                logger.debug("Tennis ESPN rank for %s: %d", player_name, rank_int)
                return ranking_to_elo(rank_int)
    except Exception as exc:
        logger.debug("ESPN tennis rank unavailable for '%s': %s", player_name, exc)

    # 3. Conservative default — treat as a respectable but unranked player (rank ~80)
    return ranking_to_elo(80)


def _confidence(win_prob: float) -> str:
    if win_prob >= 70:
        return "ALTA"
    elif win_prob >= 60:
        return "MEDIA"
    else:
        return "BAJA"


# ── Main prediction ───────────────────────────────────────────────────────────

def predict_match(
    player1_name: str,
    player2_name: str,
    surface: str = "hard",
    best_of: int = 3,
) -> dict:
    """
    Predict a tennis match result.

    Parameters
    ----------
    player1_name : str — first player (serves first by convention)
    player2_name : str — opponent
    surface      : str — "clay" | "grass" | "hard" (default "hard")
    best_of      : int — 3 or 5

    Returns a standardised prediction dict.
    """
    surface = surface.lower().strip()
    if surface not in ("clay", "grass", "hard"):
        surface = "hard"

    elo1 = _get_player_elo(player1_name)
    elo2 = _get_player_elo(player2_name)

    # ── Apply surface Elo adjustments ────────────────────────────────────────
    # Look up each player's surface type and add the corresponding Elo delta
    # for the surface being played on.  Unknown players are treated as neutral.
    surf_table = SURFACE_ELO_ADJ[surface]
    type1 = _PLAYER_SURFACE_TYPE.get(player1_name, "neutral")
    type2 = _PLAYER_SURFACE_TYPE.get(player2_name, "neutral")
    elo1_adj = elo1 + surf_table.get(type1, 0)
    elo2_adj = elo2 + surf_table.get(type2, 0)

    # Per-set win probability using surface-adjusted Elo
    p_set = elo_win_prob(elo1_adj, elo2_adj)

    # Match win probability from binomial set model
    p_match = round(set_to_match_win_prob(p_set, best_of) * 100, 1)
    p_match_opp = round(100 - p_match, 1)

    favoured = player1_name if p_match >= p_match_opp else player2_name
    lead_prob = max(p_match, p_match_opp)
    conf = _confidence(lead_prob)

    surfaces_emoji = {"clay": "🏟️", "grass": "🌱", "hard": "🏢"}
    surface_emoji = surfaces_emoji.get(surface, "🎾")

    best_bet = f"Victoria {favoured} ({lead_prob:.1f}%)"

    return {
        "sport": f"Tenis 🎾 ({surface_emoji} {surface.capitalize()})",
        "home": player1_name,
        "away": player2_name,
        "home_win": p_match,
        "away_win": p_match_opp,
        "elo_p1": round(elo1),
        "elo_p2": round(elo2),
        "surface": surface,
        "best_of": best_of,
        "expected_home": None,
        "expected_away": None,
        "spread": round(elo1 - elo2, 0),
        "over_under": None,
        "confidence": conf,
        "best_bet": best_bet,
        "live_data": False,
        "home_record": "",
        "away_record": "",
    }
