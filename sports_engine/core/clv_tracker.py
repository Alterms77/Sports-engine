"""
Closing Line Value (CLV) Tracker — Sports Engine.

CLV measures how the odds you bet compare to the final odds just before the
market closes. Beating the closing line is the single best predictor of
long-term profitability.

  CLV % = (closing_odds / bet_odds - 1) × 100
  Positive CLV → you beat the market.
  Negative CLV → market moved against you.

The tracker stores each pick with opening, bet, and closing odds in
data/clv_log.json and computes average CLV over time.

Thresholds
──────────
  CLV >= +3%  → 🟢 Excellent — strong long-term edge signal
  CLV >= 0%   → 🟡 Positive  — beating the line
  CLV < 0%    → 🔴 Negative  — losing to the closing line
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
CLV_LOG_FILE = os.path.join(_DATA_DIR, "clv_log.json")


# ─────────────────────────────────────────────────────────────────
# CALCULATION
# ─────────────────────────────────────────────────────────────────

def compute_clv(bet_odds: float, closing_odds: float) -> float:
    """
    Compute Closing Line Value as a percentage.

    Positive = you got a better price than the closing line.
    """
    if bet_odds <= 0 or closing_odds <= 0:
        return 0.0
    return round((closing_odds / bet_odds - 1.0) * 100.0, 2)


def clv_label(clv_pct: float) -> str:
    """Return a label and emoji for a CLV percentage."""
    if clv_pct >= 3.0:
        return "🟢 Excellent"
    elif clv_pct >= 0.0:
        return "🟡 Positive"
    else:
        return "🔴 Negative"


# ─────────────────────────────────────────────────────────────────
# PERSISTENCE
# ─────────────────────────────────────────────────────────────────

def _load_log() -> List[Dict]:
    os.makedirs(_DATA_DIR, exist_ok=True)
    if not os.path.exists(CLV_LOG_FILE):
        return []
    try:
        with open(CLV_LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("CLV log read error: %s", exc)
        return []


def _save_log(data: List[Dict]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    try:
        with open(CLV_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning("CLV log write error: %s", exc)


def log_pick(
    event: str,
    market: str,
    bet_odds: float,
    closing_odds: Optional[float] = None,
    sport: str = "Fútbol",
    stake_units: float = 1.0,
    result: Optional[str] = None,      # "WIN" | "LOSS" | None
) -> Dict:
    """
    Add a pick to the CLV log.

    If closing_odds is provided CLV is computed immediately.
    Otherwise a placeholder entry is stored and can be updated later.
    """
    entry: Dict = {
        "event":         event,
        "sport":         sport,
        "market":        market,
        "bet_odds":      bet_odds,
        "closing_odds":  closing_odds,
        "clv_pct":       compute_clv(bet_odds, closing_odds) if closing_odds else None,
        "stake_units":   stake_units,
        "result":        result,
        "timestamp":     datetime.utcnow().isoformat(timespec="seconds"),
    }
    log = _load_log()
    log.append(entry)
    _save_log(log)
    return entry


def update_closing_odds(event: str, market: str, closing_odds: float) -> bool:
    """
    Update the closing odds for a previously logged pick.
    Returns True if the entry was found and updated.
    """
    log  = _load_log()
    found = False
    for entry in reversed(log):
        if entry["event"] == event and entry["market"] == market and entry["closing_odds"] is None:
            entry["closing_odds"] = closing_odds
            entry["clv_pct"]      = compute_clv(entry["bet_odds"], closing_odds)
            found = True
            break
    if found:
        _save_log(log)
    return found


def get_clv_stats(last_n: int = 50) -> Dict:
    """
    Summarise CLV performance over the last N picks.

    Returns
    -------
    {
        "total":       int,
        "with_clv":    int,  # picks where closing odds were recorded
        "avg_clv":     float,
        "positive":    int,
        "negative":    int,
        "excellent":   int,  # CLV >= +3%
        "roi":         float | None,
        "picks":       [...]  # last_n entries
    }
    """
    log  = _load_log()
    recent = log[-last_n:] if len(log) > last_n else log
    with_clv = [e for e in recent if e.get("clv_pct") is not None]

    avg_clv   = round(sum(e["clv_pct"] for e in with_clv) / len(with_clv), 2) if with_clv else 0.0
    positive  = sum(1 for e in with_clv if e["clv_pct"] >= 0)
    negative  = len(with_clv) - positive
    excellent = sum(1 for e in with_clv if e["clv_pct"] >= 3.0)

    # Simple ROI from resolved picks
    resolved  = [e for e in recent if e.get("result") in ("WIN", "LOSS")]
    roi = None
    if resolved:
        total_staked = sum(e.get("stake_units", 1.0) for e in resolved)
        total_return = sum(
            e.get("stake_units", 1.0) * (e.get("bet_odds", 1.0) - 1.0)
            if e["result"] == "WIN" else -e.get("stake_units", 1.0)
            for e in resolved
        )
        roi = round(total_return / total_staked * 100, 2) if total_staked > 0 else 0.0

    return {
        "total":     len(recent),
        "with_clv":  len(with_clv),
        "avg_clv":   avg_clv,
        "positive":  positive,
        "negative":  negative,
        "excellent": excellent,
        "roi":       roi,
        "picks":     recent[-10:],
    }


# ─────────────────────────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────────────────────────

def format_clv_stats(stats: Dict) -> str:
    """Format CLV stats for Telegram."""
    avg_label = clv_label(stats["avg_clv"])
    roi_str   = f"`{stats['roi']:+.1f}%`" if stats["roi"] is not None else "_sin resolver_"

    lines = [
        "╔══════════════════════════════════╗",
        "  📊 CLV TRACKER — Closing Line Value",
        "╚══════════════════════════════════╝",
        "",
        f"📋 Picks registrados: `{stats['total']}`  (con CLV: `{stats['with_clv']}`)",
        f"📈 CLV promedio: `{stats['avg_clv']:+.2f}%` {avg_label}",
        f"  🟢 Positivo: `{stats['positive']}`  "
        f"🔴 Negativo: `{stats['negative']}`  "
        f"⭐ Excellent: `{stats['excellent']}`",
        f"💰 ROI estimado: {roi_str}",
        "",
        "📝 *Últimos picks:*",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for p in reversed(stats["picks"][-5:]):
        clv_str = f"{p['clv_pct']:+.1f}%" if p.get("clv_pct") is not None else "pendiente"
        res_str = f" [{p['result']}]" if p.get("result") else ""
        lines.append(
            f"  {p.get('sport','?')} | {p['event'][:22]}\n"
            f"    {p['market'][:20]} @ `{p['bet_odds']}` → CLV `{clv_str}`{res_str}"
        )

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "_Beating the closing line = edge a largo plazo._",
    ]
    return "\n".join(lines)


def format_clv_single(entry: Dict) -> str:
    """Format a single CLV entry for Telegram."""
    clv_str   = f"{entry['clv_pct']:+.1f}%" if entry.get("clv_pct") is not None else "pendiente"
    label_str = clv_label(entry["clv_pct"]) if entry.get("clv_pct") is not None else ""
    return (
        f"📊 *CLV Registrado*\n"
        f"  {entry.get('sport','Fútbol')} — {entry['event']}\n"
        f"  Mercado: {entry['market']}\n"
        f"  Cuota apostada: `{entry['bet_odds']}`\n"
        f"  Cuota de cierre: `{entry.get('closing_odds', 'pendiente')}`\n"
        f"  CLV: *{clv_str}* {label_str}"
    )
