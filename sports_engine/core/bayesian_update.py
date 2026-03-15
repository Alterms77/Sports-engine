"""
Bayesian Update Engine — Sports Engine.

Implements Bayesian inference to update match-outcome probabilities
as new evidence arrives:

  - Pre-match: update base model with team news, referee, weather
  - In-play: update given current score, time elapsed, xG so far
  - Multi-source: combine model probability with market consensus

The engine uses a Beta-Binomial model for win/loss probabilities and
a Dirichlet model for 3-way (1X2) outcomes.

Usage
─────
  from core.bayesian_update import BayesianUpdater

  bu = BayesianUpdater(prior_home=0.55, prior_draw=0.27, prior_away=0.18)
  bu.update_evidence({"referee_strict": True, "weather_xg_mult": 0.90})
  bu.update_score(home_goals=1, away_goals=0, minute=60, xg_so_far_h=1.2, xg_so_far_a=0.6)
  probs = bu.posterior()
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────
# DIRICHLET UPDATER (3-way market)
# ─────────────────────────────────────────────────────────────────

@dataclass
class BayesianUpdater:
    """
    Bayesian probability updater for a 3-way (1X2) market.

    Uses a Dirichlet prior parameterised by the initial model probabilities
    scaled by a confidence factor (higher = stronger prior).

    Parameters
    ----------
    prior_home     : initial home-win probability (0-1)
    prior_draw     : initial draw probability (0-1)
    prior_away     : initial away-win probability (0-1)
    confidence     : prior strength (default 10 = moderate confidence)
    """
    prior_home:  float
    prior_draw:  float
    prior_away:  float
    confidence:  float = 10.0

    def __post_init__(self):
        # Normalise priors
        total = self.prior_home + self.prior_draw + self.prior_away
        if total <= 0:
            self.prior_home = self.prior_draw = self.prior_away = 1.0 / 3
            total = 1.0
        self.alpha_home = self.prior_home / total * self.confidence
        self.alpha_draw = self.prior_draw / total * self.confidence
        self.alpha_away = self.prior_away / total * self.confidence
        self._evidence_log: List[Dict] = []

    def _normalise(self) -> Tuple[float, float, float]:
        total = self.alpha_home + self.alpha_draw + self.alpha_away
        return (
            round(self.alpha_home / total, 4),
            round(self.alpha_draw / total, 4),
            round(self.alpha_away / total, 4),
        )

    def update_evidence(self, evidence: Dict) -> "BayesianUpdater":
        """
        Update the posterior with a dictionary of evidence items.

        Supported keys
        ──────────────
        referee_strict     : bool  → boost draw/away slightly
        referee_permissive : bool  → slight home advantage boost
        weather_xg_mult    : float → < 1.0 boosts draw, > 1.0 boosts favourite
        home_form_boost    : float → positive = home form improving
        away_form_boost    : float → positive = away form improving
        home_injury_pct    : float → % xG reduction for home (0-30)
        away_injury_pct    : float → % xG reduction for away (0-30)
        elo_diff           : float → home_elo - away_elo
        """
        log_entry = {"evidence": evidence.copy(), "before": self._normalise()}

        if evidence.get("referee_strict"):
            self.alpha_draw += 0.4
            self.alpha_away += 0.2

        if evidence.get("referee_permissive"):
            self.alpha_home += 0.3

        xg_mult = evidence.get("weather_xg_mult", 1.0)
        if xg_mult < 0.90:
            # Bad weather → more draws
            self.alpha_draw += 0.6 * (1 - xg_mult) * 10
            self.alpha_home -= 0.3 * (1 - xg_mult) * 10
            self.alpha_away -= 0.3 * (1 - xg_mult) * 10
        elif xg_mult > 1.05:
            # Good weather → favourite more likely
            self.alpha_home += 0.3 * (xg_mult - 1) * 10
            self.alpha_away -= 0.2 * (xg_mult - 1) * 10

        home_form = evidence.get("home_form_boost", 0.0)
        if home_form > 0:
            self.alpha_home += home_form * 0.5
        elif home_form < 0:
            self.alpha_home += home_form * 0.3

        away_form = evidence.get("away_form_boost", 0.0)
        if away_form > 0:
            self.alpha_away += away_form * 0.5
        elif away_form < 0:
            self.alpha_away += away_form * 0.3

        home_inj = evidence.get("home_injury_pct", 0.0)
        if home_inj > 0:
            self.alpha_home -= home_inj * 0.03
            self.alpha_draw += home_inj * 0.015

        away_inj = evidence.get("away_injury_pct", 0.0)
        if away_inj > 0:
            self.alpha_away -= away_inj * 0.03
            self.alpha_draw += away_inj * 0.015

        elo_diff = evidence.get("elo_diff", 0.0)
        if abs(elo_diff) > 50:
            boost = min(abs(elo_diff) / 1000, 0.5)
            if elo_diff > 0:
                self.alpha_home += boost
            else:
                self.alpha_away += boost

        # Clamp to positive
        self.alpha_home = max(self.alpha_home, 0.05)
        self.alpha_draw = max(self.alpha_draw, 0.05)
        self.alpha_away = max(self.alpha_away, 0.05)

        log_entry["after"] = self._normalise()
        self._evidence_log.append(log_entry)
        return self

    def update_score(
        self,
        home_goals:     int,
        away_goals:     int,
        minute:         int,
        xg_so_far_h:    float = 0.0,
        xg_so_far_a:    float = 0.0,
    ) -> "BayesianUpdater":
        """
        In-play Bayesian update given current score and time elapsed.

        Uses a time-decay factor: later in the game → stronger evidence.
        """
        remaining = max(90 - minute, 1)
        time_weight = 1.0 - remaining / 90.0   # 0 at kick-off, 1 at 90'
        evidence_strength = time_weight * 3.0

        goal_diff = home_goals - away_goals
        if goal_diff > 0:
            self.alpha_home += evidence_strength * goal_diff
            self.alpha_draw -= evidence_strength * 0.3
            self.alpha_away -= evidence_strength * 0.3
        elif goal_diff < 0:
            self.alpha_away += evidence_strength * abs(goal_diff)
            self.alpha_draw -= evidence_strength * 0.3
            self.alpha_home -= evidence_strength * 0.3
        else:
            # Draw → draw alpha gets a boost
            self.alpha_draw += evidence_strength * 0.5

        # xG residual (are they creating chances?)
        xg_diff = xg_so_far_h - xg_so_far_a
        self.alpha_home += xg_diff * 0.4 * time_weight
        self.alpha_away -= xg_diff * 0.2 * time_weight

        self.alpha_home = max(self.alpha_home, 0.05)
        self.alpha_draw = max(self.alpha_draw, 0.05)
        self.alpha_away = max(self.alpha_away, 0.05)

        return self

    def posterior(self) -> Dict:
        """
        Return the current posterior probabilities.

        Returns
        -------
        {
            "home_win": float %,
            "draw":     float %,
            "away_win": float %,
            "shift_home": float  (absolute shift from prior)
            "shift_draw": float
            "shift_away": float
        }
        """
        h, d, a = self._normalise()
        ph = self.prior_home / (self.prior_home + self.prior_draw + self.prior_away)
        pd = self.prior_draw / (self.prior_home + self.prior_draw + self.prior_away)
        pa = self.prior_away / (self.prior_home + self.prior_draw + self.prior_away)

        return {
            "home_win":   round(h * 100, 2),
            "draw":       round(d * 100, 2),
            "away_win":   round(a * 100, 2),
            "shift_home": round((h - ph) * 100, 2),
            "shift_draw": round((d - pd) * 100, 2),
            "shift_away": round((a - pa) * 100, 2),
            "evidence_steps": len(self._evidence_log),
        }

    def probability_change_summary(self) -> List[Dict]:
        """Return each evidence update and how it shifted probabilities."""
        return self._evidence_log


# ─────────────────────────────────────────────────────────────────
# CONVENIENCE FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def bayesian_update_pregame(
    prior_home: float,
    prior_draw: float,
    prior_away: float,
    referee_strict:  bool  = False,
    referee_permissive: bool = False,
    weather_xg_mult: float = 1.0,
    home_form_boost: float = 0.0,
    away_form_boost: float = 0.0,
    home_injury_pct: float = 0.0,
    away_injury_pct: float = 0.0,
    elo_diff:        float = 0.0,
) -> Dict:
    """One-call Bayesian pre-game update."""
    bu = BayesianUpdater(prior_home, prior_draw, prior_away)
    bu.update_evidence({
        "referee_strict":     referee_strict,
        "referee_permissive": referee_permissive,
        "weather_xg_mult":    weather_xg_mult,
        "home_form_boost":    home_form_boost,
        "away_form_boost":    away_form_boost,
        "home_injury_pct":    home_injury_pct,
        "away_injury_pct":    away_injury_pct,
        "elo_diff":           elo_diff,
    })
    return bu.posterior()


# ─────────────────────────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────────────────────────

def format_bayesian_update(
    prior: Tuple[float, float, float],
    posterior: Dict,
    event: str = "",
    evidence_labels: Optional[List[str]] = None,
) -> str:
    """Format Bayesian update output for Telegram."""
    ph, pd, pa = prior
    sh = posterior["shift_home"]
    sd = posterior["shift_draw"]
    sa = posterior["shift_away"]

    def _shift_str(s: float) -> str:
        return f"({s:+.1f}pp)" if abs(s) >= 0.2 else ""

    lines = [
        "╔══════════════════════════════════╗",
        "  🎲 BAYESIAN UPDATE ENGINE",
        f"  {event}" if event else "",
        "╚══════════════════════════════════╝",
        "",
        "  *Prior → Posterior (1X2)*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"  🏠 Local:    `{ph:.1f}%` → `{posterior['home_win']:.1f}%` {_shift_str(sh)}",
        f"  ➖ Empate:   `{pd:.1f}%` → `{posterior['draw']:.1f}%`     {_shift_str(sd)}",
        f"  ✈️ Visitante:`{pa:.1f}%` → `{posterior['away_win']:.1f}%` {_shift_str(sa)}",
        "",
        f"  Evidencias aplicadas: `{posterior['evidence_steps']}`",
    ]

    if evidence_labels:
        lines += ["", "*Evidencias:*"]
        for label in evidence_labels[:5]:
            lines.append(f"  • {label}")

    lines += [
        "",
        "_Actualización bayesiana: prior × likelihood → posterior._",
    ]
    return "\n".join(l for l in lines)
