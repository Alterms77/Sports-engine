"""
Universal Market Error Scanner — Sports Engine.

Detects pricing discrepancies and potential errors across ANY sport and
ANY market type by comparing odds from multiple bookmakers.

Supported sports: football, basketball, baseball, tennis, NFL, MMA, esports, etc.
Supported markets: 1X2, Over/Under, AH, player props, corners, cards, shots,
                   points, rebounds, assists, totals, handicaps, and more.

Bookmakers monitored: Bet365, Caliente, Codere, Playdoit, Betway, BetMexico,
                      Betcris, and any others added via the feed manager.

Detection thresholds
────────────────────
  >= 20% above market average → 🟡 Value Opportunity
  >= 30% above market average → 🔥 High Value
  >= 40% above market average → 🚨 Probable Market Error

Alerts are generated and sent immediately — no match analysis required.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Tuple

logger = logging.getLogger(__name__)


def _md(text: str) -> str:
    """Escape Telegram Markdown v1 special characters in dynamic content."""
    return (
        str(text)
        .replace("_", r"\_")
        .replace("*", r"\*")
        .replace("`", r"\`")
        .replace("[", r"\[")
    )

# ──────────────────────────────────────────────────────────────────────────────
# DETECTION THRESHOLDS (as fractional multipliers)
# ──────────────────────────────────────────────────────────────────────────────

THRESHOLD_VALUE  = 0.20   # 20 % above average → Value Opportunity
THRESHOLD_HIGH   = 0.30   # 30 % above average → High Value
THRESHOLD_ERROR  = 0.40   # 40 % above average → Probable Market Error

# Well-known bookmakers (used for display hints; scanner works with ANY name)
BOOKMAKERS_MX = [
    "Bet365", "Caliente", "Codere", "Playdoit",
    "Betway", "BetMexico", "Betcris", "1xBet",
    "Bodog", "Betsson", "WinMaster",
]

# ──────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class BookmakerOdds:
    """Odds offered by a single bookmaker for one outcome."""
    bookmaker: str
    odds: float

    @property
    def implied_prob(self) -> float:
        """Implied probability = 1 / odds (0–1 range)."""
        return round(1.0 / self.odds, 4) if self.odds > 0 else 0.0

    @property
    def implied_prob_pct(self) -> float:
        """Implied probability as a percentage."""
        return round(self.implied_prob * 100, 2)


@dataclass
class MarketScan:
    """
    A single betting market to be scanned across bookmakers.

    Fields
    ------
    sport    : any sport name (e.g. "Fútbol", "NBA", "Tenis", "MMA")
    event    : match / event description (e.g. "Real Madrid vs Barça")
    market   : market description (e.g. "Victoria Madrid", "Over 2.5",
               "Mbappé Tiros a puerta +1.5", "LeBron Puntos +25.5")
    player   : optional player name for props (e.g. "Mbappé")
    odds_list: list of BookmakerOdds, one entry per bookmaker
    """
    sport:      str
    event:      str
    market:     str
    player:     str = ""
    odds_list:  List[BookmakerOdds] = field(default_factory=list)


@dataclass
class MarketAlert:
    """Alert produced when an outlier odds entry is detected."""
    sport:          str
    event:          str
    market:         str
    player:         str
    bookmaker:      str    # the outlier bookmaker
    outlier_odds:   float
    average_odds:   float  # average of ALL entries (including outlier)
    diff_pct:       float  # % above average (e.g. 35.2 for 35.2 %)
    classification: str    # "VALUE" | "HIGH_VALUE" | "MARKET_ERROR"
    all_odds:       List[BookmakerOdds] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# CORE DETECTION ENGINE
# ──────────────────────────────────────────────────────────────────────────────

def compute_market_average(odds_list: List[BookmakerOdds]) -> float:
    """Return the simple average of all valid odds (> 1.0)."""
    valid = [b.odds for b in odds_list if b.odds > 1.0]
    if not valid:
        return 0.0
    return round(sum(valid) / len(valid), 4)


def detect_outliers(
    odds_list: List[BookmakerOdds],
    min_sources: int = 2,
) -> List[Tuple[BookmakerOdds, float]]:
    """
    Find bookmakers whose odds are significantly above the market average.

    Parameters
    ----------
    odds_list   : all odds for this market outcome
    min_sources : minimum number of sources required before scanning

    Returns
    -------
    List of (BookmakerOdds, diff_pct) sorted by diff_pct descending.
    diff_pct is a raw percentage (e.g. 25.0 for 25 % above average).
    """
    if len(odds_list) < min_sources:
        return []

    avg = compute_market_average(odds_list)
    if avg <= 1.0:
        return []

    outliers = []
    for entry in odds_list:
        if entry.odds <= 1.0:
            continue
        diff = (entry.odds - avg) / avg
        if diff >= THRESHOLD_VALUE:
            outliers.append((entry, round(diff * 100, 2)))

    return sorted(outliers, key=lambda x: x[1], reverse=True)


def classify_alert(diff_pct: float) -> str:
    """
    Classify an alert based on how far the odds deviate from average.

    diff_pct is expressed as a percentage (e.g. 35.0 for 35 %).
    """
    if diff_pct >= THRESHOLD_ERROR * 100:
        return "MARKET_ERROR"
    elif diff_pct >= THRESHOLD_HIGH * 100:
        return "HIGH_VALUE"
    else:
        return "VALUE"


def scan_market(scan: MarketScan) -> List[MarketAlert]:
    """
    Scan a single MarketScan for pricing discrepancies.

    Returns a (possibly empty) list of MarketAlert objects.
    """
    outliers = detect_outliers(scan.odds_list)
    if not outliers:
        return []

    avg = compute_market_average(scan.odds_list)
    alerts = []

    for entry, diff_pct in outliers:
        alerts.append(MarketAlert(
            sport          = scan.sport,
            event          = scan.event,
            market         = scan.market,
            player         = scan.player,
            bookmaker      = entry.bookmaker,
            outlier_odds   = entry.odds,
            average_odds   = avg,
            diff_pct       = diff_pct,
            classification = classify_alert(diff_pct),
            all_odds       = scan.odds_list,
        ))

    return alerts


def scan_multiple_markets(scans: List[MarketScan]) -> List[MarketAlert]:
    """
    Scan a list of MarketScan objects and aggregate all alerts.

    Returns alerts sorted by diff_pct descending (most significant first).
    """
    all_alerts: List[MarketAlert] = []
    for scan in scans:
        all_alerts.extend(scan_market(scan))
    return sorted(all_alerts, key=lambda a: a.diff_pct, reverse=True)


# ──────────────────────────────────────────────────────────────────────────────
# INPUT PARSING
# ──────────────────────────────────────────────────────────────────────────────

def parse_odds_input(text: str) -> List[BookmakerOdds]:
    """
    Parse a space-separated string of "cuota@casa" tokens.

    Example
    -------
    "1.90@Bet365 2.50@Caliente 1.85@Codere"
    → [BookmakerOdds("Bet365", 1.90), BookmakerOdds("Caliente", 2.50), ...]

    Tokens that cannot be parsed are silently skipped.
    """
    entries: List[BookmakerOdds] = []
    for token in text.strip().split():
        if "@" not in token:
            continue
        parts = token.split("@", 1)
        try:
            odds = float(parts[0].replace(",", "."))
            casa = parts[1].strip()
            if odds > 1.0 and casa:
                entries.append(BookmakerOdds(bookmaker=casa, odds=odds))
        except (ValueError, IndexError):
            logger.debug("Skipping malformed odds token: %r", token)
    return entries


# ──────────────────────────────────────────────────────────────────────────────
# TELEGRAM FORMATTING
# ──────────────────────────────────────────────────────────────────────────────

_EMOJI = {
    "VALUE":        "🟡",
    "HIGH_VALUE":   "🔥",
    "MARKET_ERROR": "🚨",
}
_LABEL = {
    "VALUE":        "Value Opportunity",
    "HIGH_VALUE":   "High Value",
    "MARKET_ERROR": "Probable Market Error",
}

# Sport emoji map (fallback to 🏟️)
_SPORT_EMOJI = {
    "fútbol": "⚽", "futbol": "⚽", "football": "⚽", "soccer": "⚽",
    "nba": "🏀", "basket": "🏀", "basketball": "🏀",
    "mlb": "⚾", "baseball": "⚾", "béisbol": "⚾",
    "nfl": "🏈", "american football": "🏈",
    "tenis": "🎾", "tennis": "🎾",
    "mma": "🥊", "ufc": "🥊", "boxing": "🥊", "boxeo": "🥊",
    "esports": "🎮", "e-sports": "🎮",
    "hockey": "🏒", "golf": "⛳", "rugby": "🏉",
    "f1": "🏎️", "formula 1": "🏎️",
}


def _sport_emoji(sport: str) -> str:
    return _SPORT_EMOJI.get(sport.lower(), "🏟️")


def format_alert(alert: MarketAlert) -> str:
    """Format a single MarketAlert for Telegram (Markdown)."""
    emoji = _EMOJI.get(alert.classification, "⚠️")
    label = _LABEL.get(alert.classification, "Alerta")
    sport_e = _sport_emoji(alert.sport)

    player_line = f"\n👤 *Jugador:* {_md(alert.player)}" if alert.player else ""

    # Build odds comparison table (highest odds first)
    rows = []
    for b in sorted(alert.all_odds, key=lambda x: x.odds, reverse=True):
        marker = " ◄ *ERROR*" if b.bookmaker == alert.bookmaker else ""
        rows.append(
            f"  `{b.bookmaker:<12}` `{b.odds:.2f}`  ({b.implied_prob_pct:.1f}%){marker}"
        )
    odds_table = "\n".join(rows)

    return (
        f"{emoji} *MARKET ERROR ALERT — {label.upper()}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{sport_e} *Deporte:* {_md(alert.sport)}\n"
        f"📋 *Evento:* {_md(alert.event)}{player_line}\n"
        f"📊 *Mercado:* {_md(alert.market)}\n\n"
        f"🏦 *Casa detectada:* `{alert.bookmaker}`\n"
        f"💰 *Cuota detectada:* `{alert.outlier_odds:.2f}` "
        f"(impl. {round(100/alert.outlier_odds, 1)}%)\n"
        f"📉 *Cuota promedio:* `{alert.average_odds:.2f}` "
        f"(impl. {round(100/alert.average_odds, 1) if alert.average_odds > 0 else '?'}%)\n"
        f"📈 *Diferencia:* `+{alert.diff_pct:.1f}%`\n\n"
        f"*Cuotas del mercado:*\n"
        f"{odds_table}"
    )


def format_scan_summary(
    alerts: List[MarketAlert],
    scanned: int = 0,
) -> str:
    """
    Format a scan summary for Telegram.

    Parameters
    ----------
    alerts  : list of MarketAlert from scan_multiple_markets()
    scanned : number of markets that were scanned
    """
    if not alerts:
        scanned_str = f" ({scanned} mercados)" if scanned else ""
        return (
            f"✅ *Sin errores detectados{scanned_str}*\n"
            f"_Mercado eficiente — todas las cuotas dentro del rango normal._"
        )

    errors = [a for a in alerts if a.classification == "MARKET_ERROR"]
    highs  = [a for a in alerts if a.classification == "HIGH_VALUE"]
    values = [a for a in alerts if a.classification == "VALUE"]

    lines = [
        "╔══════════════════════════════════╗",
        "  🔍 UNIVERSAL MARKET ERROR SCANNER",
        "╚══════════════════════════════════╝",
        "",
        f"  🚨 Errores probables: `{len(errors)}`",
        f"  🔥 High Value:        `{len(highs)}`",
        f"  🟡 Value Opportunity: `{len(values)}`",
        f"  Mercados escaneados:  `{scanned}`",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for alert in alerts[:8]:
        emoji = _EMOJI.get(alert.classification, "⚠️")
        sport_e = _sport_emoji(alert.sport)
        lines.append(
            f"{emoji} {sport_e} *{_md(alert.bookmaker)}* — {_md(alert.event)}\n"
            f"   {_md(alert.market)}: `{alert.outlier_odds:.2f}` "
            f"vs avg `{alert.average_odds:.2f}` (+{alert.diff_pct:.1f}%)"
        )

    if len(alerts) > 8:
        lines.append(f"\n_... y {len(alerts) - 8} alertas más_")

    return "\n".join(lines)
