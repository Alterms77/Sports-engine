"""
Steam Move Detector — Sports Engine.

A "steam move" occurs when a large, coordinated sharp money move hits the
market simultaneously across multiple bookmakers, causing rapid odds movement.
Retail books typically follow the sharp price within minutes.

Detection logic
───────────────
  1. Compare current odds to a reference (opening / previous snapshot).
  2. Compute the implied probability shift for each bookmaker.
  3. If ≥ 2 bookmakers moved in the same direction AND the aggregate shift
     exceeds the threshold → steam move detected.

Thresholds (implied probability shift)
  Soft steam   ≥ 2 pp  (2 percentage-point shift)  → 🌊 Soft Steam
  Hard steam   ≥ 4 pp                               → 💥 Hard Steam
  Reverse line ≥ 2 pp  opposite to public consensus → 🔄 Reverse Line

Typical steam signals
  - Sharp side: the direction odds moved TOWARD (implying money)
  - "Following the steam" = betting same direction as sharps
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

THRESHOLD_SOFT  = 2.0   # pp shift → Soft Steam
THRESHOLD_HARD  = 4.0   # pp shift → Hard Steam
MIN_MOVERS      = 2     # minimum bookmakers that must move in same direction


@dataclass
class OddsSnapshot:
    """Odds for a market at a specific point in time."""
    bookmaker: str
    odds_open:    float
    odds_current: float

    @property
    def prob_open(self) -> float:
        return round(1.0 / self.odds_open * 100, 2) if self.odds_open > 0 else 0.0

    @property
    def prob_current(self) -> float:
        return round(1.0 / self.odds_current * 100, 2) if self.odds_current > 0 else 0.0

    @property
    def prob_shift(self) -> float:
        """Positive = odds shortened (money came in), Negative = odds drifted."""
        return round(self.prob_current - self.prob_open, 2)


@dataclass
class SteamAlert:
    """A detected steam move for one market outcome."""
    market:        str
    sport:         str
    event:         str
    steam_type:    str          # "SOFT" | "HARD" | "REVERSE"
    sharp_side:    str          # label of the outcome that steam hit
    avg_shift_pp:  float        # average probability shift in pp
    movers:        int          # count of books that moved same direction
    avg_open:      float        # average opening odds
    avg_current:   float        # average current odds
    snapshots:     List[OddsSnapshot] = field(default_factory=list)


def detect_steam(
    market: str,
    snapshots: List[OddsSnapshot],
    event: str = "",
    sport: str = "Fútbol",
    public_lean: Optional[str] = None,     # label of public-money side
) -> Optional[SteamAlert]:
    """
    Detect a steam move from a set of opening→current odds snapshots.

    Parameters
    ----------
    market      : label (e.g. "Victoria Real Madrid")
    snapshots   : opening + current odds per bookmaker
    event       : match label (for display)
    sport       : sport label
    public_lean : if provided, moves AGAINST this side = reverse line

    Returns
    -------
    SteamAlert if steam detected, None otherwise.
    """
    if len(snapshots) < MIN_MOVERS:
        return None

    up   = [s for s in snapshots if s.prob_shift >  0.5]   # odds shortened
    down = [s for s in snapshots if s.prob_shift < -0.5]   # odds drifted

    # Determine dominant direction
    dominant, direction = (up, "shortened") if len(up) >= len(down) else (down, "drifted")
    if len(dominant) < MIN_MOVERS:
        return None

    avg_shift = abs(sum(s.prob_shift for s in dominant) / len(dominant))

    if avg_shift < THRESHOLD_SOFT:
        return None

    if avg_shift >= THRESHOLD_HARD:
        steam_type = "HARD"
    else:
        steam_type = "SOFT"

    # Reverse line: public leans one way, but steam goes opposite
    if public_lean and direction == "drifted" and market == public_lean:
        steam_type = "REVERSE"

    avg_open    = sum(s.odds_open    for s in dominant) / len(dominant)
    avg_current = sum(s.odds_current for s in dominant) / len(dominant)

    return SteamAlert(
        market       = market,
        sport        = sport,
        event        = event,
        steam_type   = steam_type,
        sharp_side   = market if direction == "shortened" else f"NOT {market}",
        avg_shift_pp = round(avg_shift, 2),
        movers       = len(dominant),
        avg_open     = round(avg_open, 3),
        avg_current  = round(avg_current, 3),
        snapshots    = snapshots,
    )


def detect_multiple_steam(
    market_snapshots: Dict[str, List[OddsSnapshot]],
    event: str = "",
    sport: str = "Fútbol",
) -> List[SteamAlert]:
    """
    Detect steam moves across multiple markets.

    Parameters
    ----------
    market_snapshots : {market_label: [OddsSnapshot, ...]}

    Returns
    -------
    List of SteamAlert (sorted by avg_shift_pp descending).
    """
    alerts = []
    for market, snaps in market_snapshots.items():
        alert = detect_steam(market, snaps, event=event, sport=sport)
        if alert:
            alerts.append(alert)
    return sorted(alerts, key=lambda a: a.avg_shift_pp, reverse=True)


# ─────────────────────────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────────────────────────

_STEAM_EMOJI = {
    "SOFT":    "🌊",
    "HARD":    "💥",
    "REVERSE": "🔄",
}
_STEAM_LABEL = {
    "SOFT":    "Soft Steam",
    "HARD":    "Hard Steam — SHARP MONEY",
    "REVERSE": "Reverse Line Movement",
}


def format_steam_alert(alert: SteamAlert) -> str:
    """Format a single SteamAlert for Telegram."""
    emoji = _STEAM_EMOJI.get(alert.steam_type, "⚠️")
    label = _STEAM_LABEL.get(alert.steam_type, "Steam")

    rows = []
    for s in sorted(alert.snapshots, key=lambda x: abs(x.prob_shift), reverse=True)[:5]:
        arrow = "▲" if s.prob_shift > 0 else "▼"
        rows.append(
            f"  `{s.bookmaker:<12}` {s.odds_open:.2f}→`{s.odds_current:.2f}`  "
            f"{arrow}{abs(s.prob_shift):.1f}pp"
        )

    return (
        f"{emoji} *{label}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏟️ {alert.event}  _{alert.sport}_\n"
        f"📊 Mercado: *{alert.market}*\n\n"
        f"💰 Sharp side: *{alert.sharp_side}*\n"
        f"📈 Movimiento promedio: `{alert.avg_shift_pp:+.2f} pp`\n"
        f"🏦 Casas que movieron: `{alert.movers}`\n"
        f"  Cuota abierta: `{alert.avg_open:.2f}` → actual: `{alert.avg_current:.2f}`\n\n"
        f"*Movimientos por casa:*\n"
        + "\n".join(rows)
    )


def format_steam_summary(alerts: List[SteamAlert]) -> str:
    """Format a list of steam alerts into a summary."""
    if not alerts:
        return "✅ *Sin steam moves detectados.*\n_Mercado estable._"

    hard   = [a for a in alerts if a.steam_type == "HARD"]
    soft   = [a for a in alerts if a.steam_type == "SOFT"]
    rev    = [a for a in alerts if a.steam_type == "REVERSE"]

    lines = [
        "╔══════════════════════════════════╗",
        "  💥 STEAM MOVE DETECTOR",
        "╚══════════════════════════════════╝",
        "",
        f"  💥 Hard Steam:    `{len(hard)}`",
        f"  🌊 Soft Steam:    `{len(soft)}`",
        f"  🔄 Reverse Line:  `{len(rev)}`",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for a in alerts[:6]:
        emoji = _STEAM_EMOJI.get(a.steam_type, "⚠️")
        lines.append(
            f"{emoji} *{a.event}* — {a.market}\n"
            f"   Sharp: *{a.sharp_side}*  Shift: `{a.avg_shift_pp:+.2f}pp`  "
            f"(`{a.movers}` casas)"
        )

    return "\n".join(lines)
