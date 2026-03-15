"""
Auto-Scanner Engine — Sports Engine.

Orchestrates fully automatic, continuous background scanning across all
configured sports and bookmakers.  The engine is called from the Telegram
bot's ``auto_scan_job`` every ``AUTO_SCAN_INTERVAL`` seconds.

Flow per scan cycle
───────────────────
  1. Fetch live bookmaker odds for all AUTO_SPORTS via The Odds API
     (if ODDS_API_KEY is set).  Falls back to ESPN events with an empty
     odds list when the key is absent.
  2. Group MarketScans by (event, market_group) to enable arbitrage detection.
  3. Run detectors:
       a. Arbitrage / surebet   (guaranteed profit across bookmakers)
       b. Market error / value  (outlier odds vs market average)
       c. Steam move            (rapid coordinated odds movement)
  4. De-duplicate: skip alerts whose key was already sent within DEDUP_TTL.
  5. Apply spam filter: only emit HIGH_VALUE / MARKET_ERROR / ARB / HARD STEAM.
  6. Return sorted list of ``ScanAlert`` ready for Telegram dispatch.

Steam detection
───────────────
  The engine keeps the **previous scan's odds** in ``_prev_odds`` (keyed by
  market scan identity).  On the next cycle it compares new vs old prices
  and feeds them into ``detect_steam()``.

Deduplication
─────────────
  Each alert type has a stable string key derived from (sport, event,
  market, bookmaker/type).  Keys are stored in ``_seen`` with a timestamp;
  if the same key reappears within DEDUP_TTL seconds the alert is dropped.

Thread-safety
─────────────
  The engine is designed to be called from a *single* async job.  All mutable
  state is module-level and protected by ``_scan_lock`` to handle the rare
  case where two scan cycles overlap.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Module-level state
# ─────────────────────────────────────────────────────────────────────────────

_seen: Dict[str, float] = {}      # dedup cache: alert_key → last_seen_epoch
_prev_odds: Dict[str, float] = {} # steam baseline: scan_key → avg_odds last cycle
_scan_lock = asyncio.Lock()

# Hard cap on alerts emitted per scan cycle to prevent message floods.
MAX_ALERTS_PER_CYCLE = 20

# Entries in _seen are kept for this multiple of DEDUP_TTL before being
# purged, so a recently-seen alert is never accidentally re-sent during
# the window when its TTL just expired but the cache hasn't been cleaned yet.
_DEDUP_EXPIRY_FACTOR = 2


# ─────────────────────────────────────────────────────────────────────────────
# Output dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScanAlert:
    """Unified alert produced by the auto-scanner."""
    alert_type: str     # "ARB" | "MARKET_ERROR" | "HIGH_VALUE" | "VALUE" | "STEAM"
    priority:   int     # 1 (highest) → 4 (lowest); used for sorting
    sport:      str
    event:      str
    market:     str
    summary:    str     # one-line summary for logs
    message:    str     # full Telegram message (Markdown)


# ─────────────────────────────────────────────────────────────────────────────
# Priority map
# ─────────────────────────────────────────────────────────────────────────────

_PRIORITY = {
    "ARB":          1,
    "MARKET_ERROR": 2,
    "HIGH_VALUE":   2,
    "STEAM":        3,
    "VALUE":        4,
}


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication helpers
# ─────────────────────────────────────────────────────────────────────────────

def _dedup_key(alert_type: str, sport: str, event: str, market: str,
               detail: str = "") -> str:
    return f"{alert_type}|{sport}|{event}|{market}|{detail}".lower()


def _is_duplicate(key: str, dedup_ttl: int) -> bool:
    """Return True and update timestamp if this key was recently seen."""
    now = time.time()
    last = _seen.get(key, 0.0)
    if now - last < dedup_ttl:
        return True
    _seen[key] = now
    return False


def _expire_seen(dedup_ttl: int) -> None:
    """Remove stale entries from the dedup cache."""
    now = time.time()
    expired = [k for k, ts in _seen.items() if now - ts >= dedup_ttl * _DEDUP_EXPIRY_FACTOR]
    for k in expired:
        del _seen[k]


# ─────────────────────────────────────────────────────────────────────────────
# Steam baseline helpers
# ─────────────────────────────────────────────────────────────────────────────

def _scan_key(sport: str, event: str, market: str) -> str:
    return f"{sport}|{event}|{market}".lower()


def _update_steam_baseline(scans: list) -> None:
    """Save current average odds as the baseline for the next cycle."""
    from core.market_scanner import compute_market_average
    for scan in scans:
        avg = compute_market_average(scan.odds_list)
        if avg > 0:
            _prev_odds[_scan_key(scan.sport, scan.event, scan.market)] = avg


# ─────────────────────────────────────────────────────────────────────────────
# Grouping helper (for arbitrage)
# ─────────────────────────────────────────────────────────────────────────────

def _group_by_event_market(scans: list) -> Dict[tuple, list]:
    """
    Group MarketScans by (sport, event, market_group) so that all outcome legs
    of the same market are together for arbitrage detection.

    ``market_group`` is derived by stripping the outcome label from the full
    market label.  For The Odds API outcomes this means:
      "Victoria Lakers" → "h2h"
      "Over 220.0"      → "totals"
      "Lakers -4.5"     → "spreads"
    """
    groups: Dict[tuple, list] = {}
    for scan in scans:
        mgroup = _infer_market_group(scan.market)
        key = (scan.sport, scan.event, mgroup)
        groups.setdefault(key, []).append(scan)
    return groups


def _infer_market_group(market_label: str) -> str:
    """Infer the market group from a market label string."""
    ml = market_label.lower()
    if ml.startswith("victoria") or ml.startswith("empate"):
        return "h2h"
    if ml.startswith("over") or ml.startswith("under"):
        return "totals"
    return "spreads"


# ─────────────────────────────────────────────────────────────────────────────
# ESPN fallback: events without odds
# ─────────────────────────────────────────────────────────────────────────────

def _get_espn_events() -> List[str]:
    """Return a list of today's ESPN event strings (no odds)."""
    try:
        from api.espn_api import get_all_scoreboards
        games = get_all_scoreboards()
        return [
            f"{g['home']} vs {g['away']}"
            for g in games
            if g.get("home") and g.get("away")
        ]
    except Exception as exc:
        logger.debug("ESPN fallback: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Main scan function
# ─────────────────────────────────────────────────────────────────────────────

async def scan_once() -> List[ScanAlert]:
    """
    Perform one full scan cycle.  Returns a list of ``ScanAlert`` objects
    ready to be sent as Telegram messages.

    This coroutine is designed to be called from the bot's ``auto_scan_job``.
    It is safe to call concurrently — a non-blocking lock prevents double-runs.
    """
    # Read config values fresh at call time so that env vars added after
    # startup (e.g. via Railway dashboard + redeploy) are always picked up.
    odds_api_key = os.environ.get("ODDS_API_KEY", "")
    from core.config import AUTO_SCAN_MIN_EV, AUTO_SCAN_DEDUP_TTL
    from core.market_scanner import scan_multiple_markets, format_alert
    from core.arbitrage import find_arbitrage, format_arb_alert
    from core.steam_detector import OddsSnapshot, detect_steam, format_steam_alert

    # Non-blocking: if a scan is already running, skip this cycle
    if _scan_lock.locked():
        logger.debug("auto_scanner: scan already in progress, skipping cycle")
        return []

    async with _scan_lock:
        return await asyncio.get_event_loop().run_in_executor(
            None, _scan_once_sync,
            odds_api_key, AUTO_SCAN_MIN_EV, AUTO_SCAN_DEDUP_TTL,
        )


def _scan_once_sync(
    odds_api_key: str,
    min_ev: float,
    dedup_ttl: int,
) -> List[ScanAlert]:
    """Synchronous implementation called in a thread-pool executor."""
    from core.market_scanner import (
        scan_multiple_markets, format_alert, compute_market_average,
    )
    from core.arbitrage import find_arbitrage, format_arb_alert, ArbLeg
    from core.steam_detector import OddsSnapshot, detect_steam, format_steam_alert

    _expire_seen(dedup_ttl)
    all_alerts: List[ScanAlert] = []

    # ── Step 1: Fetch live odds ────────────────────────────────────────────────
    if odds_api_key:
        try:
            from api.odds_api import get_all_odds
            scans = get_all_odds(odds_api_key)
            logger.info("auto_scanner: fetched %d market scans from Odds API", len(scans))
        except Exception as exc:
            logger.warning("auto_scanner: Odds API fetch failed: %s", exc)
            scans = []
    else:
        # No key: operate with ESPN events but no real odds (no scanner alerts)
        scans = []
        logger.debug("auto_scanner: ODDS_API_KEY not set; skipping odds fetch")

    if not scans:
        # Nothing to scan — log status and return empty
        events = _get_espn_events()
        logger.info(
            "auto_scanner: no odds data available. ESPN shows %d events today.",
            len(events),
        )
        return []

    # ── Step 2: Market-error / value-bet detection ────────────────────────────
    market_error_alerts = scan_multiple_markets(scans)
    for ma in market_error_alerts:
        # Apply min EV filter (diff_pct is the EV%)
        if ma.diff_pct < min_ev:
            continue
        # Only send HIGH_VALUE and MARKET_ERROR to avoid spam
        if ma.classification not in ("HIGH_VALUE", "MARKET_ERROR"):
            continue
        key = _dedup_key(ma.classification, ma.sport, ma.event,
                         ma.market, ma.bookmaker)
        if _is_duplicate(key, dedup_ttl):
            continue
        all_alerts.append(ScanAlert(
            alert_type=ma.classification,
            priority=_PRIORITY.get(ma.classification, 4),
            sport=ma.sport,
            event=ma.event,
            market=ma.market,
            summary=(
                f"{ma.classification} {ma.sport} | {ma.event} | "
                f"{ma.market} {ma.outlier_odds:.2f}@{ma.bookmaker} "
                f"(+{ma.diff_pct:.1f}%)"
            ),
            message=format_alert(ma),
        ))

    # ── Step 3: Arbitrage detection ───────────────────────────────────────────
    groups = _group_by_event_market(scans)
    for (sport, event, mgroup), group_scans in groups.items():
        # Build outcome_odds dict
        from core.market_scanner import BookmakerOdds as _BO
        outcome_odds: Dict[str, list] = {}
        for scan in group_scans:
            outcome_odds[scan.market] = scan.odds_list

        arb = find_arbitrage(outcome_odds, sport=sport, event=event,
                             market_group=mgroup)
        if arb is None:
            continue
        key = _dedup_key("ARB", sport, event, mgroup)
        if _is_duplicate(key, dedup_ttl):
            continue
        all_alerts.append(ScanAlert(
            alert_type="ARB",
            priority=1,
            sport=sport,
            event=event,
            market=mgroup,
            summary=f"ARB {sport} | {event} | {mgroup} margin={arb.margin_pct:.3f}%",
            message=format_arb_alert(arb),
        ))

    # ── Step 4: Steam-move detection ──────────────────────────────────────────
    for scan in scans:
        sk = _scan_key(scan.sport, scan.event, scan.market)
        prev_avg = _prev_odds.get(sk)
        if prev_avg is None:
            continue  # No baseline yet — will have it next cycle

        curr_avg = compute_market_average(scan.odds_list)
        if curr_avg <= 0 or prev_avg <= 0:
            continue

        # Build snapshots (one entry per bookmaker using prev_avg as proxy)
        snaps = [
            OddsSnapshot(
                bookmaker=b.bookmaker,
                odds_open=prev_avg,
                odds_current=b.odds,
            )
            for b in scan.odds_list
            if b.odds > 1.0
        ]
        steam = detect_steam(
            market=scan.market,
            snapshots=snaps,
            event=scan.event,
            sport=scan.sport,
        )
        if steam is None:
            continue
        if steam.steam_type not in ("HARD", "REVERSE"):
            continue  # Only hard steam warrants an alert
        key = _dedup_key("STEAM", scan.sport, scan.event, scan.market, steam.steam_type)
        if _is_duplicate(key, dedup_ttl):
            continue
        all_alerts.append(ScanAlert(
            alert_type="STEAM",
            priority=3,
            sport=scan.sport,
            event=scan.event,
            market=scan.market,
            summary=(
                f"STEAM {steam.steam_type} {scan.sport} | {scan.event} | "
                f"{scan.market} shift={steam.avg_shift_pp:+.2f}pp"
            ),
            message=format_steam_alert(steam),
        ))

    # ── Step 5: Update steam baseline for next cycle ──────────────────────────
    _update_steam_baseline(scans)

    # ── Step 6: Sort and cap ──────────────────────────────────────────────────
    all_alerts.sort(key=lambda a: (a.priority, a.alert_type))
    # Hard cap: never send more than MAX_ALERTS_PER_CYCLE per cycle to prevent flood
    capped = all_alerts[:MAX_ALERTS_PER_CYCLE]
    if len(all_alerts) > MAX_ALERTS_PER_CYCLE:
        logger.info(
            "auto_scanner: capped alerts from %d to %d for this cycle",
            len(all_alerts), MAX_ALERTS_PER_CYCLE,
        )

    logger.info(
        "auto_scanner: cycle complete — %d alert(s) [arb=%d, error=%d, steam=%d]",
        len(capped),
        sum(1 for a in capped if a.alert_type == "ARB"),
        sum(1 for a in capped if a.alert_type in ("MARKET_ERROR", "HIGH_VALUE")),
        sum(1 for a in capped if a.alert_type == "STEAM"),
    )
    return capped


# ─────────────────────────────────────────────────────────────────────────────
# Status summary (for /autoscan command)
# ─────────────────────────────────────────────────────────────────────────────

def status_summary() -> str:
    """Return a Markdown status block for the /autoscan command."""
    # Read ODDS_API_KEY fresh from the environment at call time so that the
    # status always reflects the current value (e.g. after a Railway redeploy).
    odds_api_key = os.environ.get("ODDS_API_KEY", "")
    from core.config import AUTO_SCAN_INTERVAL, AUTO_SCAN_MIN_EV, AUTO_SCAN_DEDUP_TTL
    try:
        from api.odds_api import get_quota_remaining
        quota = get_quota_remaining()
        quota_str = f"`{quota}` requests restantes"
    except Exception:
        quota_str = "_n/a_"

    key_status = "✅ Configurada" if odds_api_key else "⚠️ No configurada (sin cuotas reales)"
    seen_count = len(_seen)

    return (
        "🤖 *Auto-Scanner Status*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🔑 *ODDS_API_KEY:* {key_status}\n"
        f"⏱ *Intervalo de escaneo:* `{AUTO_SCAN_INTERVAL}s`\n"
        f"📈 *EV mínimo para alertar:* `{AUTO_SCAN_MIN_EV}%`\n"
        f"🔕 *Dedup TTL:* `{AUTO_SCAN_DEDUP_TTL}s`\n"
        f"📊 *Cuota Odds API:* {quota_str}\n"
        f"🗂 *Alertas en caché (dedup):* `{seen_count}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "_El scanner detecta automáticamente:_\n"
        "  💚 Arbitraje (surebet)\n"
        "  🚨 Error de cuota (Market Error)\n"
        "  🔥 Value Bet alto (High Value)\n"
        "  💥 Steam move fuerte (Sharp Money)\n"
    )
