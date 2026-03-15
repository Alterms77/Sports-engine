"""
Market Models — Complete betting market computation for Sports Engine.

All markets derived from xG model + Dixon-Coles correction:
  - Goals Over/Under: 0.5, 1.5, 2.5, 3.5, 4.5
  - Asian Handicap: -2.5, -2.0, -1.5, -1.0, -0.5, 0, +0.5, +1.0, +1.5, +2.0, +2.5
  - Team goal totals: home and away Over/Under 0.5, 1.5, 2.5
  - Correct Score market: top 9 most likely scorelines
  - HT/FT combinations: top 6
  - DNB and Double Chance
"""

from typing import List, Dict

from core.advanced_predictions import (
    _build_score_matrix,
    compute_asian_handicap,
    compute_dnb,
    compute_double_chance,
    compute_ht_ft,
    compute_team_totals,
)


def compute_goals_lines(xg_home: float, xg_away: float) -> Dict:
    """
    Over/Under probabilities for lines 0.5 through 4.5.

    Returns
    -------
    {
        "over_0_5": float, "under_0_5": float,
        "over_1_5": float, "under_1_5": float,
        ...
        "over_4_5": float, "under_4_5": float,
    }
    """
    matrix = _build_score_matrix(xg_home, xg_away)
    result = {}
    for n in range(5):
        line     = float(n) + 0.5
        over     = sum(p for (hg, ag), p in matrix.items() if hg + ag > line)
        over_key = f"over_{n}_5"
        result[over_key]                       = round(over * 100, 1)
        result[over_key.replace("over", "under")] = round((1 - over) * 100, 1)
    return result


def compute_correct_score_market(
    xg_home: float,
    xg_away: float,
    top_n: int = 9,
) -> List[Dict]:
    """
    Correct Score market: top_n most likely scorelines.

    Returns
    -------
    [{"score": "1-0", "prob": 14.2}, ...]
    """
    matrix = _build_score_matrix(xg_home, xg_away)
    sorted_scores = sorted(matrix.items(), key=lambda x: x[1], reverse=True)
    return [
        {"score": f"{hg}-{ag}", "prob": round(p * 100, 1)}
        for (hg, ag), p in sorted_scores[:top_n]
    ]


def full_market_model(
    xg_home: float,
    xg_away: float,
    prediction: Dict,
) -> Dict:
    """
    Generate a complete market model for a match.

    Parameters
    ----------
    xg_home, xg_away : expected goals
    prediction        : full prediction dict (home_win, draw, away_win)

    Returns
    -------
    {
        "goals_lines":    dict,   # O/U 0.5-4.5
        "asian_handicap": list,   # AH -2.5 to +2.5
        "team_totals":    dict,   # home/away O/U 0.5-2.5
        "correct_score":  list,   # top 9 scorelines
        "ht_ft":          list,   # top 6 HT/FT combos
        "dnb":            dict,   # Draw No Bet
        "double_chance":  dict,   # 1X, X2, 12
    }
    """
    hw = prediction.get("home_win", 0)
    dr = prediction.get("draw",     0)
    aw = prediction.get("away_win", 0)

    ah_lines = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5]

    return {
        "goals_lines":    compute_goals_lines(xg_home, xg_away),
        "asian_handicap": [
            compute_asian_handicap(xg_home, xg_away, l) for l in ah_lines
        ],
        "team_totals":    compute_team_totals(xg_home, xg_away),
        "correct_score":  compute_correct_score_market(xg_home, xg_away),
        "ht_ft":          compute_ht_ft(xg_home, xg_away),
        "dnb":            compute_dnb(hw, dr, aw),
        "double_chance":  compute_double_chance(hw, dr, aw),
    }


def format_market_model(
    prediction: Dict,
    market: Dict,
) -> str:
    """
    Format the full market model output for Telegram.

    Parameters
    ----------
    prediction : full prediction dict
    market     : output of full_market_model()
    """
    home = prediction.get("home", "Local")
    away = prediction.get("away", "Visitante")
    home1 = home.split()[0]
    away1 = away.split()[0]

    lines = [
        "╔══════════════════════════════════╗",
        f"  📊 MODELOS DE MERCADO",
        f"  {home} vs {away}",
        "╚══════════════════════════════════╝",
        "",
    ]

    # ── Goals Over/Under lines ──
    gl = market.get("goals_lines", {})
    if gl:
        lines += [
            "⚽ *GOLES — OVER/UNDER*",
            "━━━━━━━━━━━━━━━━━━━━",
            f"  Over 0.5: `{gl.get('over_0_5', 0)}%`   Under 0.5: `{gl.get('under_0_5', 0)}%`",
        ]
        for n, label in [(1, "1.5"), (2, "2.5"), (3, "3.5"), (4, "4.5")]:
            over_key  = f"over_{n}_5"
            under_key = f"under_{n}_5"
            lines.append(
                f"  Over {label}: `{gl.get(over_key, 0)}%`   Under {label}: `{gl.get(under_key, 0)}%`"
            )
        lines.append("")

    # ── Double Chance & DNB ──
    dc  = market.get("double_chance", {})
    dnb = market.get("dnb", {})
    if dc or dnb:
        lines += [
            "🎯 *DOBLE OPORTUNIDAD / DNB*",
            "━━━━━━━━━━━━━━━━━━━━",
        ]
        if dc:
            lines.append(
                f"  1X: `{dc.get('dc_1x', 0)}%`   X2: `{dc.get('dc_x2', 0)}%`   12: `{dc.get('dc_12', 0)}%`"
            )
        if dnb:
            lines.append(
                f"  DNB {home1}: `{dnb.get('dnb_home', 0)}%`   DNB {away1}: `{dnb.get('dnb_away', 0)}%`"
            )
        lines.append("")

    # ── Asian Handicap ──
    ah_list = market.get("asian_handicap", [])
    if ah_list:
        lines += [
            "🏹 *ASIAN HANDICAP*",
            "━━━━━━━━━━━━━━━━━━━━",
            f"  `{'Línea':>6}` `{home1:>8}` `{away1:>8}` `{'Push':>6}`",
        ]
        for ah in ah_list:
            push_str = f"{ah['push_prob']:.1f}%" if ah["push_prob"] > 0 else "  —  "
            sign     = "+" if ah["line"] > 0 else ""
            lines.append(
                f"  `{sign}{ah['line']:>5.1f}` `{ah['home_prob']:>7.1f}%` `{ah['away_prob']:>7.1f}%` `{push_str:>6}`"
            )
        lines.append("")

    # ── Team Totals ──
    tt = market.get("team_totals", {})
    if tt:
        lines += [
            "📐 *TOTALES POR EQUIPO*",
            "━━━━━━━━━━━━━━━━━━━━",
            f"  {home1:<12} O0.5: `{tt.get('home_over_0_5', 0)}%`  O1.5: `{tt.get('home_over_1_5', 0)}%`  O2.5: `{tt.get('home_over_2_5', 0)}%`",
            f"  {away1:<12} O0.5: `{tt.get('away_over_0_5', 0)}%`  O1.5: `{tt.get('away_over_1_5', 0)}%`  O2.5: `{tt.get('away_over_2_5', 0)}%`",
            "",
        ]

    # ── HT/FT combinations ──
    ht_ft = market.get("ht_ft", [])
    if ht_ft:
        lines += [
            "⏱️ *HT/FT COMBINACIONES*",
            "━━━━━━━━━━━━━━━━━━━━",
        ]
        for combo in ht_ft[:6]:
            lines.append(f"  {combo['outcome']:>5}: `{combo['prob']:.1f}%`")
        lines.append("")

    # ── Correct Score ──
    cs = market.get("correct_score", [])
    if cs:
        lines += [
            "🎰 *MARCADORES MÁS PROBABLES*",
            "━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, s in enumerate(cs[:9], 1):
            lines.append(f"  {i}. `{s['score']}` → `{s['prob']:.1f}%`")

    return "\n".join(lines)
