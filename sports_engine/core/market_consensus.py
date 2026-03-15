"""
Market Consensus Model — Sports Engine.

Aggregates odds from multiple bookmakers into a single consensus probability,
stripping out each bookmaker's individual margin to produce a "wisdom of the
crowd" fair price.

Methods implemented
───────────────────
  1. Simple average of no-vig implied probabilities (unweighted)
  2. Precision-weighted average (bookmakers with lower margin get more weight)
  3. Power method (geometric mean of implied probs, re-normalised)

Also produces:
  - Consensus fair odds
  - Disagreement score: how much bookmakers diverge (0 = full agreement)
  - Confidence label based on disagreement
  - Best-available odds vs consensus
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class BookOdds:
    """One bookmaker's full 1X2 (or 2-way) market."""
    bookmaker: str
    odds:      List[float]   # e.g. [home_odds, draw_odds, away_odds]


@dataclass
class ConsensusResult:
    """Aggregated consensus for an N-way market."""
    outcomes:        List[str]         # e.g. ["Home", "Draw", "Away"]
    consensus_probs: List[float]       # % per outcome (sum ≈ 100)
    consensus_odds:  List[float]       # 1 / prob
    disagreement:    float             # 0-100 scale; 0 = perfect agreement
    confidence:      str               # HIGH / MEDIUM / LOW
    best_odds:       List[float]       # best available across all books
    best_bookies:    List[str]
    avg_margin:      float             # average bookmaker margin %
    source_count:    int


# ─────────────────────────────────────────────────────────────────
# NO-VIG PROBABILITY
# ─────────────────────────────────────────────────────────────────

def _no_vig_probs(odds: List[float]) -> List[float]:
    """Remove vig from a set of decimal odds, returning fair probs summing to 1."""
    raw = [1.0 / o for o in odds if o > 1.0]
    total = sum(raw)
    if total <= 0:
        return [1.0 / len(odds)] * len(odds)
    return [p / total for p in raw]


def _margin(odds: List[float]) -> float:
    """Bookmaker margin as % overround."""
    valid = [o for o in odds if o > 1.0]
    if not valid:
        return 0.0
    overround = sum(1.0 / o for o in valid)
    return round((overround - 1.0) * 100, 2)


# ─────────────────────────────────────────────────────────────────
# CONSENSUS ENGINE
# ─────────────────────────────────────────────────────────────────

def build_consensus(
    books: List[BookOdds],
    outcome_labels: List[str],
    method: str = "weighted",
) -> ConsensusResult:
    """
    Build a market consensus from multiple bookmaker odds.

    Parameters
    ----------
    books          : list of BookOdds (one per bookmaker)
    outcome_labels : e.g. ["Local", "Empate", "Visitante"]
    method         : "simple" | "weighted" | "power"

    Returns
    -------
    ConsensusResult
    """
    if not books:
        n = len(outcome_labels)
        return ConsensusResult(
            outcomes        = outcome_labels,
            consensus_probs = [round(100.0 / n, 2)] * n,
            consensus_odds  = [round(n / 1.0, 2)] * n,
            disagreement    = 100.0,
            confidence      = "LOW",
            best_odds       = [0.0] * n,
            best_bookies    = ["—"] * n,
            avg_margin      = 0.0,
            source_count    = 0,
        )

    n         = len(outcome_labels)
    margins   = [_margin(b.odds) for b in books]
    avg_margin = round(sum(margins) / len(margins), 2)

    # Matrix of no-vig probs: shape (n_books × n_outcomes)
    prob_matrix = [_no_vig_probs(b.odds) for b in books]

    if method == "simple":
        weights = [1.0] * len(books)
    elif method == "weighted":
        # Lower margin = higher weight (more efficient book)
        w_raw = [1.0 / max(m, 0.1) for m in margins]
        total_w = sum(w_raw)
        weights = [w / total_w for w in w_raw]
    else:  # power / geometric
        weights = [1.0] * len(books)

    # Weighted average probabilities
    if method == "power":
        # Geometric mean, re-normalised
        geo_probs = []
        for j in range(n):
            log_sum = sum(math.log(max(row[j], 1e-9)) for row in prob_matrix)
            geo_probs.append(math.exp(log_sum / len(prob_matrix)))
        total = sum(geo_probs)
        consensus_raw = [p / total for p in geo_probs]
    else:
        consensus_raw = []
        for j in range(n):
            consensus_raw.append(sum(prob_matrix[i][j] * weights[i] for i in range(len(books))))
        total = sum(consensus_raw)
        consensus_raw = [p / total for p in consensus_raw]

    consensus_probs = [round(p * 100, 2) for p in consensus_raw]
    consensus_odds  = [round(1.0 / p, 3) if p > 0 else 0.0 for p in consensus_raw]

    # Disagreement: average standard deviation across outcomes
    std_list = []
    for j in range(n):
        col = [row[j] * 100 for row in prob_matrix]
        mean = sum(col) / len(col)
        std  = math.sqrt(sum((x - mean) ** 2 for x in col) / len(col))
        std_list.append(std)
    disagreement = round(sum(std_list) / len(std_list) * 10, 1)  # scaled 0-100

    if disagreement < 2.0:
        confidence = "HIGH"
    elif disagreement < 5.0:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Best available odds across books per outcome
    best_odds    = []
    best_bookies = []
    for j in range(n):
        best_i = max(range(len(books)), key=lambda i: books[i].odds[j] if j < len(books[i].odds) else 0)
        best_odds.append(books[best_i].odds[j] if j < len(books[best_i].odds) else 0.0)
        best_bookies.append(books[best_i].bookmaker)

    return ConsensusResult(
        outcomes        = outcome_labels,
        consensus_probs = consensus_probs,
        consensus_odds  = consensus_odds,
        disagreement    = disagreement,
        confidence      = confidence,
        best_odds       = best_odds,
        best_bookies    = best_bookies,
        avg_margin      = avg_margin,
        source_count    = len(books),
    )


# ─────────────────────────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────────────────────────

_CONF_EMOJI = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}


def format_consensus(result: ConsensusResult, event: str = "") -> str:
    """Format a ConsensusResult for Telegram."""
    conf_e = _CONF_EMOJI.get(result.confidence, "⚪")

    lines = [
        "╔══════════════════════════════════╗",
        "  🤝 MARKET CONSENSUS MODEL",
        f"  {event}" if event else "",
        "╚══════════════════════════════════╝",
        "",
        f"🏦 Casas analizadas: `{result.source_count}`   "
        f"Margen prom: `{result.avg_margin:.1f}%`",
        f"{conf_e} Acuerdo del mercado: `{result.confidence}`  "
        f"(divergencia `{result.disagreement:.1f}`)",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for i, outcome in enumerate(result.outcomes):
        cp   = result.consensus_probs[i]
        co   = result.consensus_odds[i]
        bo   = result.best_odds[i]
        bb   = result.best_bookies[i]
        gain = round((bo / co - 1.0) * 100, 1) if co > 0 else 0.0
        gain_str = f"+{gain:.1f}%" if gain >= 0 else f"{gain:.1f}%"
        lines += [
            f"*{outcome}*",
            f"  Consenso: `{cp:.1f}%`  (odds justas `{co:.2f}`)",
            f"  Best available: `{bo:.2f}` @ {bb}  ({gain_str} vs consenso)",
            "",
        ]

    lines.append("_Consenso sin margen = precio justo del mercado._")
    return "\n".join(lines)
