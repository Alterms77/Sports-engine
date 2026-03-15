"""
Reinforcement Learning Strategy — Sports Engine.

Implements a Q-learning agent that learns optimal stake fractions
over time based on historical bet outcomes.

The state space encodes key features of each betting situation:
  - Confidence bucket (HIGH / MEDIUM / LOW)
  - EV bucket (HIGH / MEDIUM / LOW)
  - Current drawdown (NONE / SMALL / LARGE)
  - Recent form (WINNING / NEUTRAL / LOSING)

The action space is a discrete set of stake fractions (0% → 5% bankroll).

Q-table is persisted to data/rl_qtable.json so the agent learns
across bot sessions.

Usage
─────
  from core.rl_strategy import RLBettingAgent

  agent = RLBettingAgent()
  action = agent.choose_action(confidence="ALTA", ev_pct=5.2, drawdown_pct=3.0)
  # ... place bet, observe result ...
  agent.learn(state, action, reward, next_state)
  agent.save()
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
RL_QTABLE_FILE = os.path.join(_DATA_DIR, "rl_qtable.json")

# ─────────────────────────────────────────────────────────────────
# HYPERPARAMETERS
# ─────────────────────────────────────────────────────────────────

ALPHA       = 0.10   # learning rate
GAMMA       = 0.90   # discount factor
EPSILON_START = 0.30 # initial exploration rate
EPSILON_MIN   = 0.05
EPSILON_DECAY = 0.995

# Discrete stake actions as % of bankroll
ACTIONS = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]


# ─────────────────────────────────────────────────────────────────
# STATE ENCODER
# ─────────────────────────────────────────────────────────────────

def encode_state(
    confidence:   str,
    ev_pct:       float,
    drawdown_pct: float,
    recent_wins:  int   = 0,
    recent_bets:  int   = 0,
) -> str:
    """
    Encode betting situation into a discrete state string.

    Returns a string key like "HIGH|HIGH|NONE|WINNING"
    """
    # Confidence bucket
    conf_bucket = confidence.upper() if confidence.upper() in ("ALTA", "MEDIA", "BAJA") else "MEDIA"
    conf_map    = {"ALTA": "H", "MEDIA": "M", "BAJA": "L"}
    c = conf_map.get(conf_bucket, "M")

    # EV bucket
    if ev_pct >= 5.0:
        e = "H"
    elif ev_pct >= 2.0:
        e = "M"
    else:
        e = "L"

    # Drawdown bucket
    if drawdown_pct >= 10.0:
        d = "L"   # large drawdown
    elif drawdown_pct >= 4.0:
        d = "S"   # small drawdown
    else:
        d = "N"   # none

    # Recent form
    if recent_bets >= 3:
        win_rate = recent_wins / recent_bets
        if win_rate >= 0.65:
            f = "W"
        elif win_rate <= 0.35:
            f = "L"
        else:
            f = "N"
    else:
        f = "N"

    return f"{c}|{e}|{d}|{f}"


# ─────────────────────────────────────────────────────────────────
# RL AGENT
# ─────────────────────────────────────────────────────────────────

class RLBettingAgent:
    """
    Q-learning agent for bet sizing.

    State:  encoded string (confidence × EV × drawdown × form)
    Action: stake % from ACTIONS list
    Reward: profit / loss in units, normalised
    """

    def __init__(self, epsilon: float = EPSILON_START):
        self.q_table:  Dict[str, List[float]] = {}
        self.epsilon   = epsilon
        self.steps     = 0
        self.load()

    # ── Q-table I/O ───────────────────────────────────────────────

    def load(self) -> None:
        if not os.path.exists(RL_QTABLE_FILE):
            return
        try:
            with open(RL_QTABLE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            self.q_table = data.get("q_table", {})
            self.epsilon  = data.get("epsilon", EPSILON_START)
            self.steps    = data.get("steps", 0)
            logger.info("RL Q-table loaded (%d states)", len(self.q_table))
        except Exception as exc:
            logger.warning("RL load error: %s", exc)

    def save(self) -> None:
        os.makedirs(_DATA_DIR, exist_ok=True)
        try:
            with open(RL_QTABLE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "q_table": self.q_table,
                    "epsilon": self.epsilon,
                    "steps":   self.steps,
                }, f, indent=2)
        except Exception as exc:
            logger.warning("RL save error: %s", exc)

    # ── Action selection ──────────────────────────────────────────

    def _q_values(self, state: str) -> List[float]:
        if state not in self.q_table:
            # Initialise with small positive values (optimistic start)
            self.q_table[state] = [0.1 * (i + 1) for i in range(len(ACTIONS))]
        return self.q_table[state]

    def choose_action(
        self,
        confidence:   str   = "MEDIA",
        ev_pct:       float = 2.0,
        drawdown_pct: float = 0.0,
        recent_wins:  int   = 0,
        recent_bets:  int   = 0,
    ) -> float:
        """
        Choose a stake % action using ε-greedy policy.

        Returns the stake fraction (% of bankroll).
        """
        state = encode_state(confidence, ev_pct, drawdown_pct, recent_wins, recent_bets)
        q     = self._q_values(state)

        if random.random() < self.epsilon:
            action_idx = random.randint(0, len(ACTIONS) - 1)
        else:
            action_idx = q.index(max(q))

        return ACTIONS[action_idx]

    def learn(
        self,
        state:      str,
        action_pct: float,
        reward:     float,
        next_state: str,
    ) -> None:
        """
        Q-learning update:
            Q(s,a) ← Q(s,a) + α [r + γ·max Q(s',a') − Q(s,a)]
        """
        action_idx = ACTIONS.index(min(ACTIONS, key=lambda a: abs(a - action_pct)))

        q_current  = self._q_values(state)[action_idx]
        q_next_max = max(self._q_values(next_state))

        q_new = q_current + ALPHA * (reward + GAMMA * q_next_max - q_current)
        self.q_table[state][action_idx] = round(q_new, 4)

        # Decay epsilon
        self.epsilon = max(EPSILON_MIN, self.epsilon * EPSILON_DECAY)
        self.steps  += 1

    def record_bet(
        self,
        confidence:   str,
        ev_pct:       float,
        drawdown_pct: float,
        stake_pct:    float,
        result:       str,      # "WIN" | "LOSS"
        odds:         float = 2.0,
    ) -> None:
        """Convenience wrapper: encode state, compute reward, call learn()."""
        state      = encode_state(confidence, ev_pct, drawdown_pct)
        next_state = encode_state(confidence, ev_pct, drawdown_pct)

        if result == "WIN":
            reward = stake_pct * (odds - 1.0) / 5.0    # normalised
        else:
            reward = -stake_pct / 5.0

        self.learn(state, stake_pct, reward, next_state)

    # ── Stats ─────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        return {
            "states_explored": len(self.q_table),
            "epsilon":         round(self.epsilon, 3),
            "total_steps":     self.steps,
        }


# ─────────────────────────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────────────────────────

def format_rl_advice(
    agent: RLBettingAgent,
    confidence: str,
    ev_pct:     float,
    drawdown_pct: float,
    bankroll:   float,
    market:     str = "",
) -> str:
    """Format RL stake advice for Telegram."""
    stake_pct = agent.choose_action(confidence, ev_pct, drawdown_pct)
    stake_abs = round(bankroll * stake_pct / 100, 2)
    stats     = agent.get_stats()

    state = encode_state(confidence, ev_pct, drawdown_pct)
    q     = agent._q_values(state)
    best_action = ACTIONS[q.index(max(q))]

    lines = [
        "╔══════════════════════════════════╗",
        "  🤖 RL BETTING STRATEGY (Q-Learning)",
        f"  {market}" if market else "",
        "╚══════════════════════════════════╝",
        "",
        f"  Estado: `{state}`",
        f"  Confianza: `{confidence}`   EV: `+{ev_pct:.1f}%`   DD: `{drawdown_pct:.1f}%`",
        "",
        f"  🎯 Stake recomendado: `{stake_pct:.1f}%` de bankroll",
        f"     = `{stake_abs:.2f}` u. (de {bankroll:.0f} u.)",
        "",
        f"  🧠 Mejor acción aprendida: `{best_action:.1f}%`",
        f"  📊 Estados explorados: `{stats['states_explored']}`",
        f"  🎲 Epsilon (exploración): `{stats['epsilon']}`",
        "",
        "_RL aprende con cada apuesta registrada. Mejora con el tiempo._",
    ]
    return "\n".join(l for l in lines)
