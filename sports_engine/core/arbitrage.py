"""
Arbitrage (Surebet) Detector — Sports Engine.

An arbitrage opportunity (surebet) exists when the combined implied
probabilities of all outcomes in a market are below 100 %, meaning a
bettor can back every outcome across different bookmakers and guarantee
a profit regardless of the result.

Formula
───────
  margin = 1 − Σ( 1 / best_odds_i )   for all outcomes i

  margin > 0  → arbitrage exists; profit = margin / Σ(stake_i)

Stake distribution
──────────────────
  To guarantee equal profit on every outcome, each outcome's stake
  is proportional to:

    stake_i = (total_bank / best_odds_i) / Σ(1 / best_odds_j)

  This ensures each leg pays the same net amount on a unit bank.

Telegram formatting
───────────────────
  ``format_arb_alert`` produces a compact, Markdown-safe block ready
  for sending via ``context.bot.send_message``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.market_scanner import BookmakerOdds

logger = logging.getLogger(__name__)

# Minimum arbitrage margin (%) to report. Set to 0.3 % to filter out
# apparent arbs that are eroded by typical bookmaker withdrawal fees, exchange
# commissions, and timing slippage — leaving only cleanly exploitable surebets.
MIN_ARB_MARGIN_PCT = 0.3   # 0.3 % net profit floor


@dataclass
class ArbLeg:
    """One leg of an arbitrage — the best available odds for one outcome."""
    outcome:    str
    bookmaker:  str
    odds:       float
    stake_pct:  float  # fraction of total bank to wager (0–1)

    @property
    def implied_prob(self) -> float:
        return round(1.0 / self.odds * 100, 2) if self.odds > 0 else 0.0


@dataclass
class ArbitrageAlert:
    """A confirmed arbitrage opportunity across a set of outcomes."""
    sport:        str
    event:        str
    market_group: str                  # e.g. "h2h" or "Over/Under"
    margin_pct:   float                # guaranteed profit % of total bank
    legs:         List[ArbLeg] = field(default_factory=list)


def find_arbitrage(
    outcome_odds: Dict[str, List[BookmakerOdds]],
    sport: str = "",
    event: str = "",
    market_group: str = "h2h",
) -> Optional[ArbitrageAlert]:
    """
    Detect an arbitrage opportunity from a mapping of outcome → bookmaker odds.

    Parameters
    ----------
    outcome_odds : {outcome_label: [BookmakerOdds, ...]}
        Must contain at least 2 outcomes (e.g. Home + Away, or Over + Under).
    sport        : sport label (for display)
    event        : event name (for display)
    market_group : market type label (for display)

    Returns
    -------
    ``ArbitrageAlert`` if margin > ``MIN_ARB_MARGIN_PCT``, else ``None``.
    """
    if len(outcome_odds) < 2:
        return None

    # Best odds available per outcome (across all bookmakers)
    best: Dict[str, BookmakerOdds] = {}
    for outcome, books in outcome_odds.items():
        valid = [b for b in books if b.odds > 1.0]
        if not valid:
            continue
        best[outcome] = max(valid, key=lambda b: b.odds)

    if len(best) < len(outcome_odds):
        # At least one outcome has no valid odds
        return None

    total_implied = sum(1.0 / b.odds for b in best.values())
    margin = 1.0 - total_implied

    if margin * 100 < MIN_ARB_MARGIN_PCT:
        return None

    # Compute stake distribution (proportional to 1/odds)
    legs = []
    for outcome, book in best.items():
        stake_pct = (1.0 / book.odds) / total_implied
        legs.append(ArbLeg(
            outcome=outcome,
            bookmaker=book.bookmaker,
            odds=book.odds,
            stake_pct=round(stake_pct, 4),
        ))

    # Sort legs by stake (biggest first) for clean display
    legs.sort(key=lambda l: l.stake_pct, reverse=True)

    return ArbitrageAlert(
        sport=sport,
        event=event,
        market_group=market_group,
        margin_pct=round(margin * 100, 3),
        legs=legs,
    )


def find_arbitrage_from_scans(
    sport: str,
    event: str,
    market_group: str,
    scans_for_event: list,  # List[MarketScan] filtered to one event+market group
) -> Optional[ArbitrageAlert]:
    """
    Convenience wrapper: build the outcome_odds dict from a list of MarketScans
    that all belong to the same event and represent different outcomes of the
    same market (e.g. the three legs of a 1X2 market).
    """
    outcome_odds: Dict[str, List[BookmakerOdds]] = {}
    for scan in scans_for_event:
        outcome_odds[scan.market] = scan.odds_list
    return find_arbitrage(outcome_odds, sport=sport, event=event,
                          market_group=market_group)


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM FORMATTING
# ─────────────────────────────────────────────────────────────────────────────

_SPORT_EMOJI = {
    "soccer": "⚽", "futbol": "⚽", "fútbol": "⚽",
    "nba": "🏀", "basketball": "🏀",
    "nfl": "🏈", "american football": "🏈",
    "mlb": "⚾", "baseball": "⚾",
    "tennis": "🎾", "tenis": "🎾",
    "hockey": "🏒",
}


def _sport_emoji(sport: str) -> str:
    for key, emoji in _SPORT_EMOJI.items():
        if key in sport.lower():
            return emoji
    return "🏟️"


def format_arb_alert(alert: ArbitrageAlert, bank: float = 1000.0) -> str:
    """
    Format an ``ArbitrageAlert`` for Telegram (MarkdownV1 safe).

    Parameters
    ----------
    alert : the ArbitrageAlert to format
    bank  : total stake to distribute across legs (default $1,000)
    """
    sport_e = _sport_emoji(alert.sport)
    leg_lines = []
    for leg in alert.legs:
        stake = round(bank * leg.stake_pct, 2)
        payout = round(stake * leg.odds, 2)
        leg_lines.append(
            f"  📌 *{leg.outcome}*\n"
            f"     Casa: `{leg.bookmaker}`  Cuota: `{leg.odds:.2f}`\n"
            f"     Stake: `${stake:.2f}` → Pago: `${payout:.2f}`"
        )

    profit = round(bank * alert.margin_pct / 100, 2)
    roi = round(alert.margin_pct, 3)

    return (
        f"💚 *SUREBET / ARBITRAJE DETECTADO*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{sport_e} *Deporte:* {alert.sport}\n"
        f"📋 *Evento:* {alert.event}\n"
        f"📊 *Mercado:* {alert.market_group}\n\n"
        f"💰 *Margen garantizado:* `+{roi:.3f}%`\n"
        f"  _(Bank ${bank:.0f} → profit `${profit:.2f}` sin importar resultado)_\n\n"
        f"*Apuestas:*\n" + "\n".join(leg_lines)
    )
