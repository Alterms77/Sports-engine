"""
Liquidity Detector — Sports Engine.

Assesses betting market liquidity from the spread of available odds across
bookmakers. Thin markets (few sources, high spread) signal low liquidity and
carry higher risk of non-acceptance or line changes after placement.

Liquidity Score: 0–100
  80-100 → 🟢 ALTA    — deep market, stable lines, easy placement
  50-79  → 🟡 MEDIA   — moderate liquidity, some variance expected
  0-49   → 🔴 BAJA    — thin market, volatile lines, harder fills

Also provides:
  - Best Available Odds (BBO) per market
  - Line Shopping value (% gain vs worst available)
  - Market depth index (number of active sources)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class BookmakerLine:
    """A single bookmaker's line for one market outcome."""
    bookmaker: str
    odds: float

    @property
    def implied_prob(self) -> float:
        return round(1.0 / self.odds, 4) if self.odds > 1.0 else 0.0


@dataclass
class LiquidityReport:
    """Full liquidity assessment for a market."""
    market:          str
    sources:         int          # number of bookmakers quoting this market
    best_odds:       float        # best available odds (BBO)
    best_bookie:     str
    worst_odds:      float
    avg_odds:        float
    spread_pct:      float        # (best - worst) / worst * 100
    line_shop_gain:  float        # % gain vs average by taking best line
    liquidity_score: int          # 0-100
    label:           str          # ALTA / MEDIA / BAJA
    lines:           List[BookmakerLine] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────
# CORE ENGINE
# ─────────────────────────────────────────────────────────────────

def _liquidity_score(sources: int, spread_pct: float) -> int:
    """
    Compute a 0-100 liquidity score.

    More sources  → higher score.
    Wider spread  → lower score (volatile / thin market).
    """
    source_score  = min(sources * 12, 60)          # up to 60 pts from depth
    spread_penalty = min(spread_pct * 2.5, 40)     # up to -40 pts from spread
    score = int(source_score - spread_penalty + 40) # base of 40
    return max(0, min(100, score))


def assess_market_liquidity(
    market: str,
    lines: List[BookmakerLine],
) -> LiquidityReport:
    """
    Assess liquidity for a single market outcome.

    Parameters
    ----------
    market : label for this market (e.g. "Victoria Real Madrid", "Over 2.5")
    lines  : list of BookmakerLine entries (one per bookmaker)

    Returns
    -------
    LiquidityReport
    """
    valid = [l for l in lines if l.odds > 1.0]
    if not valid:
        return LiquidityReport(
            market=market, sources=0,
            best_odds=0.0, best_bookie="—", worst_odds=0.0,
            avg_odds=0.0, spread_pct=0.0, line_shop_gain=0.0,
            liquidity_score=0, label="BAJA", lines=lines,
        )

    odds_vals    = [l.odds for l in valid]
    best_line    = max(valid, key=lambda l: l.odds)
    worst_odds   = min(odds_vals)
    avg_odds     = sum(odds_vals) / len(odds_vals)
    spread_pct   = round((best_line.odds - worst_odds) / worst_odds * 100, 2) if worst_odds > 0 else 0.0
    shop_gain    = round((best_line.odds - avg_odds) / avg_odds * 100, 2) if avg_odds > 0 else 0.0
    score        = _liquidity_score(len(valid), spread_pct)

    if score >= 80:
        label = "ALTA"
    elif score >= 50:
        label = "MEDIA"
    else:
        label = "BAJA"

    return LiquidityReport(
        market          = market,
        sources         = len(valid),
        best_odds       = best_line.odds,
        best_bookie     = best_line.bookmaker,
        worst_odds      = worst_odds,
        avg_odds        = round(avg_odds, 3),
        spread_pct      = spread_pct,
        line_shop_gain  = shop_gain,
        liquidity_score = score,
        label           = label,
        lines           = sorted(valid, key=lambda l: l.odds, reverse=True),
    )


def assess_match_liquidity(markets: Dict[str, List[BookmakerLine]]) -> Dict[str, LiquidityReport]:
    """
    Assess liquidity for all markets in a match.

    Parameters
    ----------
    markets : {market_label: [BookmakerLine, ...]}

    Returns
    -------
    {market_label: LiquidityReport}
    """
    return {label: assess_market_liquidity(label, lines) for label, lines in markets.items()}


# ─────────────────────────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────────────────────────

_LABEL_EMOJI = {"ALTA": "🟢", "MEDIA": "🟡", "BAJA": "🔴"}


def format_liquidity_report(reports: Dict[str, LiquidityReport], event: str = "") -> str:
    """Format liquidity reports for Telegram (Markdown)."""
    lines = [
        "╔══════════════════════════════════╗",
        "  💧 LIQUIDITY DETECTOR",
        f"  {event}" if event else "  Análisis de Liquidez",
        "╚══════════════════════════════════╝",
        "",
    ]

    for label, r in reports.items():
        emoji = _LABEL_EMOJI.get(r.label, "⚪")
        lines += [
            f"*{label}*",
            f"  {emoji} Liquidez: `{r.label}` (score `{r.liquidity_score}`)",
            f"  🏦 Mejor cuota: `{r.best_odds}` @ {r.best_bookie}",
            f"  📉 Spread: `{r.spread_pct:.1f}%`  "
            f"📈 Line shop gain: `+{r.line_shop_gain:.1f}%`",
            f"  🔢 Fuentes activas: `{r.sources}`",
            "",
        ]

    lines.append("💡 _Tomar siempre el mejor precio disponible (line shopping)._")
    return "\n".join(lines)
