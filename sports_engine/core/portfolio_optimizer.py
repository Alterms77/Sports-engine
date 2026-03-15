"""
Portfolio Optimizer — Sports Engine.

Constructs an optimal multi-bet portfolio from a list of candidate bets,
balancing:

  - Expected value maximisation
  - Risk diversification (low bet-correlation)
  - Kelly-constrained stake sizing
  - Total risk budget (max % of bankroll per portfolio)

Methods
───────
  1. EV-ranked simple portfolio (fastest, greedy)
  2. Mean-Variance portfolio: maximise EV / standard-deviation ratio
  3. Max Kelly portfolio: maximise total fractional Kelly allocation

Correlation assumption
──────────────────────
  Bets on the same match are treated as correlated (ρ ≈ 0.8).
  Bets on different matches are treated as independent (ρ ≈ 0).
  Bets on different sports are independent (ρ = 0).

Usage
─────
  from core.portfolio_optimizer import Bet, optimize_portfolio

  bets = [
      Bet("Real Madrid Win",   "Fútbol", prob=0.58, odds=1.85),
      Bet("Over 2.5 Goals",    "Fútbol", prob=0.61, odds=1.72),
      Bet("Lakers Win",        "NBA",    prob=0.55, odds=1.90),
  ]
  portfolio = optimize_portfolio(bets, bankroll=1000.0)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.risk_management import kelly_stake as _kelly_stake


# ─────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────

@dataclass
class Bet:
    """A candidate bet for the portfolio."""
    market:    str
    sport:     str
    prob:      float      # model win probability (0-1)
    odds:      float      # decimal odds
    event:     str = ""
    stake:     float = 0.0   # filled by optimizer
    stake_pct: float = 0.0   # % of bankroll

    @property
    def ev(self) -> float:
        """Expected value as % of stake."""
        return round((self.prob * self.odds - 1.0) * 100, 2)

    @property
    def kelly_full(self) -> float:
        """Full Kelly fraction (0-1)."""
        if self.odds <= 1.0:
            return 0.0
        b = self.odds - 1.0
        q = 1.0 - self.prob
        return max(0.0, (b * self.prob - q) / b)


@dataclass
class Portfolio:
    """An optimized bet portfolio."""
    bets:          List[Bet]
    total_stake:   float
    total_stake_pct: float    # % of bankroll
    expected_pnl:  float      # expected profit
    kelly_budget:  float      # total Kelly fractions allocated
    method:        str
    warnings:      List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────
# CORRELATION HELPER
# ─────────────────────────────────────────────────────────────────

def _correlation(b1: Bet, b2: Bet) -> float:
    """Estimate correlation between two bets."""
    if b1.sport != b2.sport:
        return 0.0
    if b1.event == b2.event and b1.event:
        return 0.7   # same match, different markets
    return 0.0


# ─────────────────────────────────────────────────────────────────
# OPTIMIZERS
# ─────────────────────────────────────────────────────────────────

def _filter_positive_ev(bets: List[Bet]) -> List[Bet]:
    """Keep only bets with positive EV."""
    return [b for b in bets if b.ev > 0 and b.kelly_full > 0]


def optimize_ev_ranked(
    bets: List[Bet],
    bankroll: float,
    kelly_fraction: float = 0.25,
    max_legs: int = 5,
    budget_pct: float = 20.0,
) -> Portfolio:
    """
    Greedy EV-ranked portfolio.

    Selects top bets by EV, applies fractional Kelly stakes,
    ensures total stake ≤ budget_pct of bankroll.
    """
    candidates = _filter_positive_ev(bets)
    candidates.sort(key=lambda b: b.ev, reverse=True)
    candidates = candidates[:max_legs]

    total_stake = 0.0
    budget      = bankroll * budget_pct / 100
    selected    = []
    warnings    = []

    for b in candidates:
        stake = _kelly_stake(bankroll, b.prob, b.odds, kelly_fraction, max_pct=5.0)
        if total_stake + stake > budget:
            stake = max(0.0, budget - total_stake)
        if stake < 0.50:
            warnings.append(f"Stake mínimo no alcanzado: {b.market}")
            continue
        b.stake     = round(stake, 2)
        b.stake_pct = round(stake / bankroll * 100, 2)
        total_stake += stake
        selected.append(b)
        if total_stake >= budget:
            break

    expected_pnl = sum(b.stake * b.ev / 100 for b in selected)
    kelly_budget = sum(b.kelly_full * kelly_fraction for b in selected)

    return Portfolio(
        bets          = selected,
        total_stake   = round(total_stake, 2),
        total_stake_pct = round(total_stake / bankroll * 100, 2),
        expected_pnl  = round(expected_pnl, 2),
        kelly_budget  = round(kelly_budget, 4),
        method        = "EV-Ranked",
        warnings      = warnings,
    )


def optimize_kelly_portfolio(
    bets: List[Bet],
    bankroll: float,
    kelly_fraction: float = 0.25,
    max_legs: int = 5,
    budget_pct: float = 20.0,
) -> Portfolio:
    """
    Kelly-maximising portfolio.

    Prioritises bets with the highest fractional Kelly allocation
    (edge / odds) after accounting for correlated legs.
    """
    candidates = _filter_positive_ev(bets)
    candidates.sort(key=lambda b: b.kelly_full, reverse=True)

    total_stake = 0.0
    budget      = bankroll * budget_pct / 100
    selected: List[Bet] = []
    warnings:  List[str] = []

    for b in candidates[:max_legs]:
        # Reduce Kelly for correlated bets already in portfolio
        corr_penalty = sum(_correlation(b, s) * s.kelly_full for s in selected)
        eff_kelly = max(b.kelly_full - corr_penalty * 0.5, 0.0) * kelly_fraction
        stake     = round(bankroll * eff_kelly, 2)
        stake     = max(min(stake, bankroll * 5.0 / 100), 0.0)

        if total_stake + stake > budget:
            stake = max(0.0, budget - total_stake)
        if stake < 0.50:
            continue
        b.stake     = round(stake, 2)
        b.stake_pct = round(stake / bankroll * 100, 2)
        total_stake += stake
        selected.append(b)
        if total_stake >= budget:
            break

    expected_pnl = sum(b.stake * b.ev / 100 for b in selected)
    kelly_budget = sum(b.kelly_full * kelly_fraction for b in selected)

    return Portfolio(
        bets          = selected,
        total_stake   = round(total_stake, 2),
        total_stake_pct = round(total_stake / bankroll * 100, 2),
        expected_pnl  = round(expected_pnl, 2),
        kelly_budget  = round(kelly_budget, 4),
        method        = "Kelly-Portfolio",
        warnings      = warnings,
    )


def optimize_portfolio(
    bets: List[Bet],
    bankroll: float,
    method: str = "kelly",
    kelly_fraction: float = 0.25,
    max_legs: int = 5,
    budget_pct: float = 20.0,
) -> Portfolio:
    """
    Entry point for portfolio optimisation.

    method: "ev" | "kelly"
    """
    if method == "ev":
        return optimize_ev_ranked(bets, bankroll, kelly_fraction, max_legs, budget_pct)
    return optimize_kelly_portfolio(bets, bankroll, kelly_fraction, max_legs, budget_pct)


# ─────────────────────────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────────────────────────

def format_portfolio(portfolio: Portfolio, bankroll: float) -> str:
    """Format portfolio report for Telegram."""
    lines = [
        "╔══════════════════════════════════╗",
        f"  📐 PORTFOLIO OPTIMIZER ({portfolio.method})",
        "╚══════════════════════════════════╝",
        "",
        f"💰 Bankroll: `{bankroll:.2f}` u.",
        f"📊 Apuestas seleccionadas: `{len(portfolio.bets)}`",
        f"💸 Stake total: `{portfolio.total_stake:.2f}` u. "
        f"(`{portfolio.total_stake_pct:.1f}%` bankroll)",
        f"📈 EV esperado: `+{portfolio.expected_pnl:.2f}` u.",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "*Legs del portfolio:*",
    ]

    for i, b in enumerate(portfolio.bets, 1):
        ev_str = f"+{b.ev:.1f}%" if b.ev >= 0 else f"{b.ev:.1f}%"
        lines.append(
            f"  {i}. `{b.sport}` *{b.market}*\n"
            f"     Prob: `{b.prob*100:.1f}%`  Cuota: `{b.odds}`  "
            f"EV: `{ev_str}`  Stake: `{b.stake:.2f}` u."
        )

    if portfolio.warnings:
        lines += ["", "⚠️ Avisos:"]
        for w in portfolio.warnings:
            lines.append(f"  • {w}")

    lines += ["", "_Diversifica siempre. Never chase losses._"]
    return "\n".join(lines)
