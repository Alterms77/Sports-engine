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

Dynamic Elo
-----------
  Results can be recorded via ``record_tennis_result()`` which persists the
  updated ratings to ``data/tennis_elo.json``.  The system uses:
    * K-factor 32 (base) · recency_weight (more recent → higher K)
    * Surface-specific sub-ratings (separate Elo per surface per player)
    * Elo damping: after each update, ratings regress 3 % toward base Elo
      to prevent extreme drift from a small number of matches.

ATP ranking → Elo approximation (inverse log scale):
  rank 1    ≈ 2400 Elo
  rank 10   ≈ 2200
  rank 50   ≈ 2050
  rank 100  ≈ 1950
  rank 200+ ≈ 1850
"""

import json
import math
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Dynamic Elo persistence ───────────────────────────────────────────────────

_TENNIS_ELO_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "tennis_elo.json",
)

# K-factor: base value.  Multiplied by recency_weight (1.0 for today,
# decays toward 0.5 over ~30 matches to give more weight to recent form).
_K_BASE = 32.0
# Damping: after each update, each rating regresses this fraction toward
# the player's "anchor" (hardcoded _KNOWN_ELO or ranking-derived base).
_ELO_DAMPING = 0.03


def _tennis_elo_data() -> dict:
    """
    Load dynamic tennis Elo store from JSON.

    Structure::

        {
          "ratings": {"Player Name": float, ...},
          "surface_ratings": {
              "Player Name": {"hard": float, "clay": float, "grass": float}
          },
          "match_counts": {"Player Name": int},
          "last_updated": "ISO8601"
        }
    """
    if not os.path.exists(_TENNIS_ELO_FILE):
        return {"ratings": {}, "surface_ratings": {}, "match_counts": {}, "last_updated": ""}
    try:
        with open(_TENNIS_ELO_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Cannot load tennis_elo.json: %s", exc)
        return {"ratings": {}, "surface_ratings": {}, "match_counts": {}, "last_updated": ""}


def _save_tennis_elo(data: dict) -> None:
    """Persist dynamic Elo store to JSON."""
    os.makedirs(os.path.dirname(_TENNIS_ELO_FILE), exist_ok=True)
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(_TENNIS_ELO_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning("Cannot save tennis_elo.json: %s", exc)


def record_tennis_result(
    winner: str,
    loser: str,
    surface: str = "hard",
    is_slam: bool = False,
) -> dict:
    """
    Record a tennis match result and update dynamic Elo ratings.

    Parameters
    ----------
    winner  : str  — full player name (e.g. "Carlos Alcaraz")
    loser   : str  — full player name
    surface : str  — "hard" | "clay" | "grass"
    is_slam : bool — True for Grand Slam matches (higher K-factor)

    Returns
    -------
    dict with keys ``winner_elo``, ``loser_elo``,
    ``winner_surface_elo``, ``loser_surface_elo``, ``delta``.
    """
    surface = surface.lower().strip()
    if surface not in ("clay", "grass", "hard"):
        surface = "hard"

    data     = _tennis_elo_data()
    ratings  = data.setdefault("ratings", {})
    surf_rtg = data.setdefault("surface_ratings", {})
    counts   = data.setdefault("match_counts", {})

    # Base Elo (overall) — seed from hardcoded table when first seen
    def _seed(name: str) -> float:
        if name not in ratings:
            ratings[name] = float(_KNOWN_ELO.get(name, ranking_to_elo(80)))
        return ratings[name]

    w_elo = _seed(winner)
    l_elo = _seed(loser)

    # Surface sub-ratings — seeded from base Elo when first seen
    surf_rtg.setdefault(winner, {})
    surf_rtg.setdefault(loser,  {})
    w_surf = surf_rtg[winner].setdefault(surface, w_elo)
    l_surf = surf_rtg[loser].setdefault(surface,  l_elo)

    # Recency weighting: K shrinks as the average match count grows (stability).
    # avg_n = average of both players' recorded match counts.
    # recency_weight = 1.0 when avg_n = 0, decays to 0.5 when avg_n = 100
    # (i.e., ~100 matches per player on average → divisor 200 → weight = 0.5).
    w_n = counts.get(winner, 0)
    l_n = counts.get(loser,  0)
    avg_n = (w_n + l_n) / 2.0
    recency_weight = max(0.5, 1.0 - avg_n / 200.0)

    k = _K_BASE * recency_weight * (1.5 if is_slam else 1.0)

    # Elo update (winner gets 1.0, loser gets 0.0)
    exp_w = 1.0 / (1.0 + 10.0 ** ((l_elo - w_elo) / 400.0))
    exp_l = 1.0 - exp_w
    delta_overall = k * (1.0 - exp_w)

    new_w_elo = w_elo + delta_overall
    new_l_elo = l_elo - delta_overall

    # Surface sub-rating update
    exp_w_surf = 1.0 / (1.0 + 10.0 ** ((l_surf - w_surf) / 400.0))
    delta_surf = k * (1.0 - exp_w_surf)
    new_w_surf = w_surf + delta_surf
    new_l_surf = l_surf - delta_surf

    # Elo damping — regress toward anchor (prevents extreme drift)
    anchor_w = float(_KNOWN_ELO.get(winner, ranking_to_elo(80)))
    anchor_l = float(_KNOWN_ELO.get(loser,  ranking_to_elo(80)))
    new_w_elo  = new_w_elo  * (1 - _ELO_DAMPING) + anchor_w * _ELO_DAMPING
    new_l_elo  = new_l_elo  * (1 - _ELO_DAMPING) + anchor_l * _ELO_DAMPING
    new_w_surf = new_w_surf * (1 - _ELO_DAMPING) + anchor_w * _ELO_DAMPING
    new_l_surf = new_l_surf * (1 - _ELO_DAMPING) + anchor_l * _ELO_DAMPING

    # Persist
    ratings[winner] = round(new_w_elo, 1)
    ratings[loser]  = round(new_l_elo, 1)
    surf_rtg[winner][surface] = round(new_w_surf, 1)
    surf_rtg[loser][surface]  = round(new_l_surf, 1)
    counts[winner] = w_n + 1
    counts[loser]  = l_n + 1

    _save_tennis_elo(data)

    return {
        "winner_elo":         round(new_w_elo, 1),
        "loser_elo":          round(new_l_elo, 1),
        "winner_surface_elo": round(new_w_surf, 1),
        "loser_surface_elo":  round(new_l_surf, 1),
        "delta":              round(delta_overall, 1),
        "surface":            surface,
    }


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


def _get_player_elo(player_name: str, surface: str = "hard") -> float:
    """Return best-available Elo for a player.

    Priority:
    1. Dynamic Elo from ``data/tennis_elo.json`` (surface-specific when available).
    2. Hardcoded Elo from ``_KNOWN_ELO`` (top players, always available).
    3. ESPN ATP/WTA ranking → convert via ``ranking_to_elo()``.
    4. League-default for an unranked / unknown player (rank 100 equivalent).
    """
    # 1. Dynamic persisted Elo (surface-specific if recorded, else overall)
    try:
        data = _tennis_elo_data()
        surf_rtg = data.get("surface_ratings", {}).get(player_name, {})
        if surface in surf_rtg:
            return float(surf_rtg[surface])
        overall = data.get("ratings", {}).get(player_name)
        if overall is not None:
            return float(overall)
    except Exception as exc:
        logger.debug("Dynamic tennis Elo unavailable for '%s': %s", player_name, exc)

    # 2. Hardcoded known Elo
    if player_name in _KNOWN_ELO:
        return _KNOWN_ELO[player_name]

    # 3. Try ESPN ranking (tennis player search)
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

    # 4. Conservative default — treat as a respectable but unranked player (rank ~80)
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

    # Dynamic Elo (surface-specific when available) takes priority over static
    elo1 = _get_player_elo(player1_name, surface)
    elo2 = _get_player_elo(player2_name, surface)

    # ── Apply surface Elo adjustments ────────────────────────────────────────
    # Only apply the static surface-type adjustment when the dynamic store does
    # NOT already have a surface-specific rating for this player (to avoid
    # double-counting the surface bonus).
    data = _tennis_elo_data()
    surf1_dynamic = surface in data.get("surface_ratings", {}).get(player1_name, {})
    surf2_dynamic = surface in data.get("surface_ratings", {}).get(player2_name, {})

    surf_table = SURFACE_ELO_ADJ[surface]
    type1 = _PLAYER_SURFACE_TYPE.get(player1_name, "neutral")
    type2 = _PLAYER_SURFACE_TYPE.get(player2_name, "neutral")
    elo1_adj = elo1 + (0 if surf1_dynamic else surf_table.get(type1, 0))
    elo2_adj = elo2 + (0 if surf2_dynamic else surf_table.get(type2, 0))

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

    counts = data.get("match_counts", {})

    return {
        "sport": f"Tenis 🎾 ({surface_emoji} {surface.capitalize()})",
        "home": player1_name,
        "away": player2_name,
        "home_win": p_match,
        "away_win": p_match_opp,
        "elo_p1": round(elo1_adj),
        "elo_p2": round(elo2_adj),
        "elo_p1_base": round(elo1),
        "elo_p2_base": round(elo2),
        "elo_dynamic_p1": surf1_dynamic or (player1_name in data.get("ratings", {})),
        "elo_dynamic_p2": surf2_dynamic or (player2_name in data.get("ratings", {})),
        "matches_recorded_p1": counts.get(player1_name, 0),
        "matches_recorded_p2": counts.get(player2_name, 0),
        "surface": surface,
        "best_of": best_of,
        "expected_home": None,
        "expected_away": None,
        "spread": round(elo1_adj - elo2_adj, 0),
        "over_under": None,
        "confidence": conf,
        "best_bet": best_bet,
        "live_data": False,
        "home_record": "",
        "away_record": "",
    }
