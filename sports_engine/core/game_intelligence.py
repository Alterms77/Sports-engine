"""
Game Intelligence — Situational analysis for Sports Engine.

Analyses contextual factors that affect match outcomes:
  - H2H scoring patterns (high/low-scoring derbies)
  - Consecutive BTTS streaks for each team
  - Consecutive Over/Under 2.5 streaks
  - Consecutive clean sheet and failed-to-score streaks
  - "Trap game" detection (clear favourite vs in-form underdog)
  - Over / BTTS aggregate signal
"""

from typing import List, Dict


def _btts_streak(history: List[Dict]) -> int:
    """Count consecutive BTTS games in the most recent matches."""
    streak = 0
    for m in reversed(history[-10:]):
        if m.get("scored", 0) > 0 and m.get("conceded", 0) > 0:
            streak += 1
        else:
            break
    return streak


def _over_streak(history: List[Dict], line: float = 2.5) -> int:
    """Count consecutive Over-line games in the most recent matches."""
    streak = 0
    for m in reversed(history[-10:]):
        if m.get("scored", 0) + m.get("conceded", 0) > line:
            streak += 1
        else:
            break
    return streak


def _cs_streak(history: List[Dict]) -> int:
    """Count consecutive clean sheets in the most recent matches."""
    streak = 0
    for m in reversed(history[-10:]):
        if m.get("conceded", 0) == 0:
            streak += 1
        else:
            break
    return streak


def _fts_streak(history: List[Dict]) -> int:
    """Count consecutive failed-to-score games in the most recent matches."""
    streak = 0
    for m in reversed(history[-10:]):
        if m.get("scored", 0) == 0:
            streak += 1
        else:
            break
    return streak


def analyze_game_intelligence(
    prediction: Dict,
    home_history: List[Dict],
    away_history: List[Dict],
    h2h_records: List[tuple],
) -> Dict:
    """
    Generate a comprehensive situational intelligence report.

    Parameters
    ----------
    prediction    : full prediction dict from predict_match()
    home_history  : home team match history (scored, conceded, result, is_home)
    away_history  : away team match history
    h2h_records   : [(home_goals, away_goals), ...] head-to-head records

    Returns
    -------
    dict with situational signals and streaks.
    """
    intel = {}

    # ── H2H scoring patterns ──
    h2h_n = len(h2h_records)
    if h2h_n >= 3:
        avg_total    = sum(hg + ag for hg, ag in h2h_records) / h2h_n
        btts_h2h     = sum(1 for hg, ag in h2h_records if hg > 0 and ag > 0)
        h2h_btts_rate = round(btts_h2h / h2h_n * 100, 1)

        if avg_total > 3.0:
            h2h_pattern = "high-scoring"
        elif avg_total < 2.0:
            h2h_pattern = "low-scoring"
        else:
            h2h_pattern = "normal"
    else:
        avg_total     = 0.0
        h2h_btts_rate = 0.0
        h2h_pattern   = "sin datos"

    intel["h2h_pattern"]   = h2h_pattern
    intel["h2h_btts_rate"] = h2h_btts_rate
    intel["h2h_avg_goals"] = round(avg_total, 1)

    # ── Team streaks ──
    intel["home_btts_streak"] = _btts_streak(home_history)
    intel["away_btts_streak"] = _btts_streak(away_history)
    intel["home_over_streak"] = _over_streak(home_history)
    intel["away_over_streak"] = _over_streak(away_history)
    intel["home_cs_streak"]   = _cs_streak(home_history)
    intel["away_cs_streak"]   = _cs_streak(away_history)
    intel["home_fts_streak"]  = _fts_streak(home_history)
    intel["away_fts_streak"]  = _fts_streak(away_history)

    # ── Trap game detection ──
    trap_game   = False
    trap_reason = ""
    home_win    = prediction.get("home_win", 0)
    away_win    = prediction.get("away_win", 0)
    form_home   = prediction.get("form_home", {})
    form_away   = prediction.get("form_away", {})

    if home_win > 60 and form_away.get("emoji") == "🔥":
        trap_game   = True
        trap_reason = "Favorito claro vs visitante en racha de fuego 🔥"
    elif away_win > 60 and form_home.get("emoji") == "🔥":
        trap_game   = True
        trap_reason = "Visitante favorito vs local en racha de fuego 🔥"

    intel["trap_game"]   = trap_game
    intel["trap_reason"] = trap_reason

    # ── Smart insight signals ──
    home_name = prediction.get("home", "Local")
    away_name = prediction.get("away", "Visitante")
    over_2_5  = prediction.get("over_2_5", 0)

    signals = []

    if h2h_pattern == "high-scoring" and over_2_5 > 55:
        signals.append(
            f"📈 Derby históricamente goleador (prom {avg_total:.1f} goles)"
        )
    if h2h_pattern == "low-scoring" and over_2_5 < 45:
        signals.append(
            f"🔒 Derby históricamente bajo en goles (prom {avg_total:.1f})"
        )
    if intel["home_btts_streak"] >= 3:
        signals.append(
            f"⚽⚽ {home_name} lleva {intel['home_btts_streak']} partidos BTTS"
        )
    if intel["away_btts_streak"] >= 3:
        signals.append(
            f"⚽⚽ {away_name} lleva {intel['away_btts_streak']} partidos BTTS"
        )
    if intel["home_cs_streak"] >= 3:
        signals.append(
            f"🔒 {home_name} lleva {intel['home_cs_streak']} porterías a cero"
        )
    if intel["away_cs_streak"] >= 3:
        signals.append(
            f"🔒 {away_name} lleva {intel['away_cs_streak']} porterías a cero"
        )
    if intel["home_over_streak"] >= 3:
        signals.append(
            f"🔥 {home_name}: Over 2.5 en sus últimos {intel['home_over_streak']} partidos"
        )
    if intel["away_over_streak"] >= 3:
        signals.append(
            f"🔥 {away_name}: Over 2.5 en sus últimos {intel['away_over_streak']} partidos"
        )
    if intel["home_fts_streak"] >= 2:
        signals.append(
            f"🚫 {home_name} lleva {intel['home_fts_streak']} partidos sin anotar"
        )
    if intel["away_fts_streak"] >= 2:
        signals.append(
            f"🚫 {away_name} lleva {intel['away_fts_streak']} partidos sin anotar"
        )
    if h2h_btts_rate >= 70:
        signals.append(f"📊 BTTS en {h2h_btts_rate:.0f}% de los H2H anteriores")
    if trap_game:
        signals.append(f"⚠️ TRAMPA POTENCIAL: {trap_reason}")

    intel["intel_signals"] = signals

    # ── Aggregate Over / BTTS signals ──
    over_pts  = 0
    btts_pts  = 0

    if over_2_5 > 60:                       over_pts += 1
    if h2h_pattern == "high-scoring":       over_pts += 1
    if intel["home_over_streak"] >= 2:      over_pts += 1
    if intel["away_over_streak"] >= 2:      over_pts += 1

    if prediction.get("btts", 0) > 55:     btts_pts += 1
    if h2h_btts_rate >= 60:                 btts_pts += 1
    if intel["home_btts_streak"] >= 2:      btts_pts += 1
    if intel["away_btts_streak"] >= 2:      btts_pts += 1

    if over_pts >= 3:
        intel["over_signal"] = "OVER"
    elif over_2_5 < 40 or (h2h_pattern == "low-scoring" and over_2_5 < 50):
        intel["over_signal"] = "UNDER"
    else:
        intel["over_signal"] = "NEUTRAL"

    if btts_pts >= 3:
        intel["btts_signal"] = "YES"
    elif intel["home_fts_streak"] >= 2 or intel["away_fts_streak"] >= 2:
        intel["btts_signal"] = "NO"
    else:
        intel["btts_signal"] = "NEUTRAL"

    return intel


def format_game_intelligence(
    prediction: Dict,
    intelligence: Dict,
) -> str:
    """
    Format the Game Intelligence report for Telegram.

    Parameters
    ----------
    prediction   : full prediction dict
    intelligence : output of analyze_game_intelligence()
    """
    home = prediction.get("home", "Local")
    away = prediction.get("away", "Visitante")

    over_emoji  = {"OVER": "🔥", "UNDER": "🧊", "NEUTRAL": "➡️"}.get(
        intelligence.get("over_signal", "NEUTRAL"), "➡️"
    )
    btts_emoji  = {"YES": "⚽⚽", "NO": "🔒", "NEUTRAL": "➡️"}.get(
        intelligence.get("btts_signal", "NEUTRAL"), "➡️"
    )

    lines = [
        "╔══════════════════════════════════╗",
        f"  🧠 GAME INTELLIGENCE",
        f"  {home} vs {away}",
        "╚══════════════════════════════════╝",
        "",
        "📊 *H2H PATRÓN*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"  Patrón histórico: `{intelligence['h2h_pattern'].upper()}`"
        f"  Prom: `{intelligence['h2h_avg_goals']} goles`",
        f"  BTTS H2H: `{intelligence['h2h_btts_rate']}%`",
        "",
        "📈 *RACHAS ACTUALES*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"  {home[:10]}  BTTS: `{intelligence['home_btts_streak']}`"
        f"  Over: `{intelligence['home_over_streak']}`"
        f"  CS: `{intelligence['home_cs_streak']}`"
        f"  FTS: `{intelligence['home_fts_streak']}`",
        f"  {away[:10]}  BTTS: `{intelligence['away_btts_streak']}`"
        f"  Over: `{intelligence['away_over_streak']}`"
        f"  CS: `{intelligence['away_cs_streak']}`"
        f"  FTS: `{intelligence['away_fts_streak']}`",
        "",
        "💡 *SEÑALES CLAVE*",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    signals = intelligence.get("intel_signals", [])
    if signals:
        for s in signals[:6]:
            lines.append(f"  • {s}")
    else:
        lines.append("  Sin señales especiales detectadas.")

    lines += [
        "",
        "🎯 *SEÑAL AGREGADA*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"  {over_emoji} Over/Under 2.5: `{intelligence.get('over_signal', 'NEUTRAL')}`",
        f"  {btts_emoji} Ambos Marcan:   `{intelligence.get('btts_signal', 'NEUTRAL')}`",
    ]

    if intelligence.get("trap_game"):
        lines += [
            "",
            f"⚠️ *TRAMPA DETECTADA*",
            f"  {intelligence['trap_reason']}",
        ]

    return "\n".join(lines)
