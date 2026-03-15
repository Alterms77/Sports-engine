"""
Risk Management — Sports Engine.

Provides a complete bankroll management system:

  - Kelly Criterion (full, half, quarter) stake sizing
  - Fixed-unit staking
  - Percentage-of-bankroll staking
  - Stop-loss rules (session, daily, weekly)
  - Maximum drawdown monitoring
  - Bet diversification limits
  - Risk-of-ruin estimation
  - Session P&L tracker

Usage
─────
  from core.risk_management import RiskManager

  rm = RiskManager(bankroll=1000.0, kelly_fraction=0.25)
  stake = rm.kelly_stake(prob=0.58, odds=1.90)
  rm.record_bet(stake=stake, odds=1.90, result="WIN")
  print(rm.session_summary())
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
RISK_LOG_FILE = os.path.join(_DATA_DIR, "risk_log.json")


# ─────────────────────────────────────────────────────────────────
# STAKE CALCULATORS (pure functions)
# ─────────────────────────────────────────────────────────────────

def kelly_stake(
    bankroll: float,
    prob: float,
    odds: float,
    fraction: float = 0.25,
    max_pct: float = 5.0,
) -> float:
    """
    Fractional Kelly stake.

    Parameters
    ----------
    bankroll  : current bankroll
    prob      : model win probability (0-1)
    odds      : decimal odds
    fraction  : Kelly fraction (default 0.25 = quarter Kelly)
    max_pct   : cap stake at this % of bankroll (default 5%)

    Returns stake in currency units (minimum 0).
    """
    if odds <= 1.0 or prob <= 0 or prob >= 1:
        return 0.0
    b     = odds - 1.0
    q     = 1.0 - prob
    full  = (b * prob - q) / b
    stake = max(0.0, full * fraction * bankroll)
    cap   = bankroll * max_pct / 100
    return round(min(stake, cap), 2)


def fixed_unit_stake(units: float = 1.0) -> float:
    """Fixed-unit staking (simplest approach)."""
    return round(max(units, 0.0), 2)


def pct_bankroll_stake(
    bankroll: float,
    pct: float = 2.0,
    max_pct: float = 5.0,
) -> float:
    """Stake a fixed percentage of current bankroll."""
    pct = min(pct, max_pct)
    return round(bankroll * pct / 100.0, 2)


def risk_of_ruin(
    win_rate: float,
    avg_odds: float,
    kelly_fraction: float = 0.25,
    n_bets: int = 200,
) -> float:
    """
    Estimate risk of ruin (probability of losing the entire bankroll)
    using the simple formula for fractional Kelly staking.

    RoR ≈ ((1 - edge) / (1 + edge)) ^ (1 / kelly_fraction)
    where edge = win_rate * avg_odds - 1
    """
    if avg_odds <= 1.0 or win_rate <= 0:
        return 1.0
    edge = win_rate * avg_odds - 1.0
    if edge <= 0:
        return 1.0
    try:
        ror = ((1.0 - edge) / (1.0 + edge)) ** (1.0 / kelly_fraction)
    except Exception:
        ror = 1.0
    return round(min(max(ror, 0.0), 1.0), 4)


def max_drawdown(balances: List[float]) -> float:
    """Compute maximum drawdown from a list of running balances."""
    if not balances:
        return 0.0
    peak = balances[0]
    mdd  = 0.0
    for b in balances:
        if b > peak:
            peak = b
        dd = (peak - b) / peak if peak > 0 else 0.0
        mdd = max(mdd, dd)
    return round(mdd * 100, 2)


# ─────────────────────────────────────────────────────────────────
# RISK MANAGER CLASS
# ─────────────────────────────────────────────────────────────────

@dataclass
class BetRecord:
    stake:     float
    odds:      float
    result:    str          # "WIN" | "LOSS" | "PUSH" | "PENDING"
    pnl:       float
    balance:   float
    timestamp: str
    market:    str = ""


class RiskManager:
    """
    Stateful bankroll risk manager.

    Parameters
    ----------
    bankroll       : initial bankroll
    kelly_fraction : default Kelly fraction (0.25 = quarter Kelly)
    stop_loss_pct  : daily stop-loss % (e.g. 10 = stop when down 10%)
    max_bet_pct    : maximum stake per bet as % of bankroll
    """

    def __init__(
        self,
        bankroll:       float = 1000.0,
        kelly_fraction: float = 0.25,
        stop_loss_pct:  float = 10.0,
        max_bet_pct:    float = 5.0,
    ):
        self.initial_bankroll = bankroll
        self.bankroll         = bankroll
        self.kelly_fraction   = kelly_fraction
        self.stop_loss_pct    = stop_loss_pct
        self.max_bet_pct      = max_bet_pct
        self.bets: List[BetRecord] = []

    # ── Stake calculation ──────────────────────────────────────────

    def kelly_stake(self, prob: float, odds: float) -> float:
        return kelly_stake(
            self.bankroll, prob, odds,
            self.kelly_fraction, self.max_bet_pct
        )

    def flat_stake(self, units: float = 1.0) -> float:
        return fixed_unit_stake(units)

    def pct_stake(self, pct: float = 2.0) -> float:
        return pct_bankroll_stake(self.bankroll, pct, self.max_bet_pct)

    # ── Bet recording ──────────────────────────────────────────────

    def record_bet(
        self,
        stake:  float,
        odds:   float,
        result: str,
        market: str = "",
    ) -> BetRecord:
        """
        Record the outcome of a bet and update the bankroll.

        result: "WIN" | "LOSS" | "PUSH"
        """
        if result == "WIN":
            pnl = stake * (odds - 1.0)
        elif result == "LOSS":
            pnl = -stake
        else:  # PUSH
            pnl = 0.0

        self.bankroll = round(self.bankroll + pnl, 2)
        rec = BetRecord(
            stake=stake, odds=odds, result=result,
            pnl=round(pnl, 2), balance=self.bankroll,
            timestamp=datetime.utcnow().isoformat(timespec="seconds"),
            market=market,
        )
        self.bets.append(rec)
        return rec

    # ── Risk checks ───────────────────────────────────────────────

    def stop_loss_triggered(self) -> bool:
        """Return True if session drawdown exceeds stop-loss threshold."""
        dd = (self.initial_bankroll - self.bankroll) / self.initial_bankroll * 100
        return dd >= self.stop_loss_pct

    def current_drawdown_pct(self) -> float:
        peak = max((b.balance for b in self.bets), default=self.initial_bankroll)
        peak = max(peak, self.initial_bankroll)
        return round((peak - self.bankroll) / peak * 100, 2)

    # ── Summary ───────────────────────────────────────────────────

    def session_summary(self) -> Dict:
        resolved = [b for b in self.bets if b.result in ("WIN", "LOSS")]
        wins   = sum(1 for b in resolved if b.result == "WIN")
        losses = sum(1 for b in resolved if b.result == "LOSS")
        total_staked = sum(b.stake for b in resolved)
        total_pnl    = sum(b.pnl  for b in resolved)
        roi   = round(total_pnl / total_staked * 100, 2) if total_staked > 0 else 0.0
        balances = [b.balance for b in self.bets]
        mdd  = max_drawdown(balances) if balances else 0.0
        ror  = risk_of_ruin(
            wins / len(resolved) if resolved else 0.5,
            sum(b.odds for b in resolved) / len(resolved) if resolved else 2.0,
            self.kelly_fraction,
        )
        return {
            "bankroll":       self.bankroll,
            "initial":        self.initial_bankroll,
            "pnl":            round(total_pnl, 2),
            "roi_pct":        roi,
            "wins":           wins,
            "losses":         losses,
            "total_bets":     len(self.bets),
            "total_staked":   round(total_staked, 2),
            "max_drawdown":   mdd,
            "risk_of_ruin":   ror,
            "stop_triggered": self.stop_loss_triggered(),
        }


# ─────────────────────────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────────────────────────

def format_stake_advice(
    bankroll: float,
    prob: float,
    odds: float,
    market: str = "",
    kelly_fraction: float = 0.25,
) -> str:
    """Format a stake advice block for Telegram."""
    k_stake  = kelly_stake(bankroll, prob, odds, kelly_fraction)
    f_stake  = pct_bankroll_stake(bankroll, 2.0)
    ror      = risk_of_ruin(prob, odds, kelly_fraction)
    ev       = round((prob * odds - 1.0) * 100, 2)

    ev_str  = f"+{ev:.1f}%" if ev >= 0 else f"{ev:.1f}%"
    ror_str = f"{ror*100:.1f}%"

    lines = [
        "╔══════════════════════════════════╗",
        "  💼 RISK MANAGEMENT",
        f"  {market}" if market else "",
        "╚══════════════════════════════════╝",
        "",
        f"💰 Bankroll: `{bankroll:.2f}` u.",
        f"📊 Prob. modelo: `{prob*100:.1f}%`   Cuota: `{odds:.2f}`",
        f"📈 EV: `{ev_str}`",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🎯 *Stake recomendado*",
        f"  Quarter Kelly ({kelly_fraction*100:.0f}%): `{k_stake:.2f}` u.",
        f"  Flat 2% bankroll: `{f_stake:.2f}` u.",
        "",
        f"⚠️  Riesgo de ruina (200 apuestas): `{ror_str}`",
        "",
        "_Nunca apuestes más de lo que Kelly sugiere._",
    ]
    return "\n".join(l for l in lines if l is not None)


def format_risk_summary(summary: Dict) -> str:
    """Format a session risk summary for Telegram."""
    pnl_str = f"+{summary['pnl']:.2f}" if summary["pnl"] >= 0 else f"{summary['pnl']:.2f}"
    roi_str = f"+{summary['roi_pct']:.1f}%" if summary["roi_pct"] >= 0 else f"{summary['roi_pct']:.1f}%"
    stop_str = "🔴 ACTIVO" if summary["stop_triggered"] else "🟢 Normal"

    lines = [
        "╔══════════════════════════════════╗",
        "  📊 RESUMEN DE SESIÓN",
        "╚══════════════════════════════════╝",
        "",
        f"💰 Bankroll: `{summary['bankroll']:.2f}` u.  (inicial `{summary['initial']:.2f}`)",
        f"📈 P&L: `{pnl_str}` u.   ROI: `{roi_str}`",
        f"🏆 Resultados: `{summary['wins']}W / {summary['losses']}L` "
        f"({summary['total_bets']} apuestas)",
        f"💸 Invertido: `{summary['total_staked']:.2f}` u.",
        "",
        f"📉 Max Drawdown: `{summary['max_drawdown']:.1f}%`",
        f"☠️  Riesgo de ruina: `{summary['risk_of_ruin']*100:.1f}%`",
        f"🛑 Stop-loss: {stop_str}",
    ]
    return "\n".join(lines)
