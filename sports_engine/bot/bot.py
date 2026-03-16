import os
import re
import sys
import csv
import asyncio as _asyncio
import logging
import threading
import time as _time
from datetime import datetime, timezone
from dotenv import load_dotenv

# Allow importing from sports_engine/ regardless of working directory
_BOT_DIR = os.path.dirname(os.path.abspath(__file__))
_SPORTS_ENGINE_DIR = os.path.dirname(_BOT_DIR)
_REPO_ROOT = os.path.dirname(_SPORTS_ENGINE_DIR)

for _p in [_SPORTS_ENGINE_DIR, _REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Load .env if present (local development)
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from update_matches import update_matches
from sports.football import (
    get_full_prediction,
    suggest_teams,
    get_team_stats_summary,
)
import sports.basketball as _bball
import sports.baseball as _baseball
import sports.tennis as _tennis
import sports.american_football as _nfl
from api.espn_api import get_all_scoreboards
from api.live_aggregator import (
    get_live_scores,
    get_all_live_scores,
    get_today_schedule,
    get_team_live_form,
    get_league_table,
    get_next_fixtures,
    format_live_scoreboard,
    format_fixture_list,
    format_last_results,
)
from core.teams import normalize_team
from core.config import TELEGRAM_TOKEN, validate_config

# ===============================
# 🪵 LOGGING
# ===============================

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
)
logger = logging.getLogger(__name__)

# ===============================
# 📁 PATHS
# ===============================

DATA_PATH = os.path.join(_SPORTS_ENGINE_DIR, "data", "today_matches.csv")

# TTL (seconds) between automatic CSV refreshes.
# 10 minutes keeps soccer fixtures fresh throughout the day so that games
# which start or finish during the window are cleaned out quickly.
MATCHES_UPDATE_TTL = 600  # 10 minutes

# State for CSV refresh scheduling
_matches_lock = threading.Lock()
_last_csv_update: float = 0.0  # epoch seconds of last successful update


def _refresh_matches_if_stale() -> None:
    """Refresh the football CSV if the TTL has expired. Thread-safe, non-blocking."""
    global _last_csv_update
    now = _time.time()
    if now - _last_csv_update < MATCHES_UPDATE_TTL:
        return
    if not _matches_lock.acquire(blocking=False):
        # Another thread is already refreshing; skip silently
        return
    try:
        update_matches()
        _last_csv_update = _time.time()
    except Exception as exc:
        logger.warning("No se pudieron actualizar los partidos: %s", exc)
    finally:
        _matches_lock.release()



def load_today_matches():
    """Load upcoming soccer matches from the local CSV.

    Filters applied at read time (defensive double-check):
    - ``date`` column must equal today's date.
    - ``status`` column (if present) must indicate a not-started game.
    - ``kickoff_utc`` column (if present) must be in the future.

    These filters mirror what ``update_matches()`` applies when writing, so
    stale rows left over from a previous run of the update job are discarded
    automatically without requiring a CSV rewrite.
    """
    matches = []
    _now_utc = datetime.now(timezone.utc)
    today    = _now_utc.strftime("%Y-%m-%d")
    now_utc  = _now_utc.replace(tzinfo=None)  # naive UTC for comparison

    # API-Sports status codes that mean the game has not yet kicked off
    _pending = {"NS", "TBD", "PST", "SUSP", "INT"}

    if not os.path.exists(DATA_PATH):
        logger.warning("Archivo no encontrado: %s", DATA_PATH)
        return matches

    try:
        with open(DATA_PATH, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                # Date filter
                row_date = row.get("date", "").strip()
                if row_date and row_date != today:
                    continue

                # Status filter: skip live / finished games
                status = row.get("status", "NS").strip()
                if status and status not in _pending:
                    continue

                # Kickoff time filter: skip games already past kick-off
                kickoff_str = row.get("kickoff_utc", "").strip()
                if kickoff_str:
                    try:
                        # normalise to a naive UTC datetime for comparison
                        kickoff_dt = datetime.fromisoformat(
                            kickoff_str.replace("Z", "+00:00")
                        )
                        if kickoff_dt.tzinfo is not None:
                            kickoff_dt = kickoff_dt.astimezone(timezone.utc).replace(tzinfo=None)
                        if kickoff_dt <= now_utc:
                            continue
                    except (ValueError, TypeError):
                        pass  # keep if unparseable

                matches.append({
                    "home": row["home"].strip(),
                    "away": row["away"].strip(),
                    "league": row.get("league", "").strip(),
                    "sport": "soccer",
                })
    except Exception as e:
        logger.error("Error al cargar partidos: %s", e)

    return matches


def load_today_matches_multisport() -> list:
    """Return a unified list of upcoming today's matches across Soccer, NBA, NFL, and MLB.

    Only games that have **not yet started** are included so that `/parlay`
    never produces picks for games already in progress or finished.

    Sources
    -------
    - Soccer : local ``today_matches.csv`` (API-Sports, filtered to upcoming)
    - NBA / NFL / MLB : ESPN public scoreboard API (no key required)

    Each item in the returned list has at minimum:
      ``sport``, ``home``, ``away``, ``league``
    """
    matches: list = []

    # ── Soccer from local CSV (already filtered to upcoming games) ─────────
    matches.extend(load_today_matches())

    # ── NBA / NFL / MLB from ESPN ──────────────────────────────────────────
    # ESPN status strings that indicate a game has not yet started.
    _espn_scheduled = {"Scheduled", "Pregame", "Pre-Game"}

    try:
        from api.espn_api import get_scoreboard
        for sport in ("nba", "nfl", "mlb"):
            try:
                games = get_scoreboard(sport)
                for g in games:
                    status = g.get("status", "Scheduled")
                    if status not in _espn_scheduled:
                        # Game is live or finished — skip for parlay purposes
                        logger.debug(
                            "Multisport loader: skipping %s vs %s (%s) — status: %s",
                            g["home"], g["away"], sport, status,
                        )
                        continue
                    matches.append({
                        "home": g["home"],
                        "away": g["away"],
                        "league": sport.upper(),
                        "sport": sport,
                    })
            except Exception as exc:
                logger.warning("ESPN scoreboard unavailable for %s: %s", sport, exc)
    except Exception as exc:
        logger.warning("ESPN API import failed: %s", exc)

    return matches


def _confidence_emoji(confidence: str) -> str:
    return {"ALTA": "🟢", "MEDIA": "🟡", "BAJA": "🔴"}.get(confidence, "⚪")


def _best_pick(pred: dict) -> str:
    """Return a 'Best Pick' recommendation based on highest 1X2 probability."""
    options = {
        f"Victoria {pred['home']}": pred["home_win"],
        "Empate": pred["draw"],
        f"Victoria {pred['away']}": pred["away_win"],
    }
    best = max(options, key=options.get)
    return f"{best} ({options[best]:.1f}%)"


def _h2h_line(pred: dict) -> str:
    """Format the H2H record line for display."""
    h2h = pred.get("h2h", {})
    n = h2h.get("total", 0)
    if n < 3:
        return ""
    home_name = pred["home"].split()[0] if pred["home"] else "Local"
    hw = h2h["home_wins"]
    d = h2h["draws"]
    aw = h2h["away_wins"]
    avg_g = h2h.get("avg_goals", 0)
    return (
        f"\n🔄 *H2H* (últimos {n} enfrentamientos)\n"
        f"  {home_name} {hw}-{d}-{aw} | Prom. {avg_g} goles\n"
    )


def _bar(pct: float, width: int = 10) -> str:
    """Visual progress bar using block characters."""
    filled = round(pct / 100 * width)
    filled = max(0, min(filled, width))
    return "█" * filled + "░" * (width - filled)


def format_prediction(pred: dict) -> str:
    conf = pred["confidence"]
    conf_emoji = _confidence_emoji(conf)
    league = pred.get("league", "")
    home = pred.get("home", "Local")
    away = pred.get("away", "Visitante")
    home1 = home.split()[0]
    away1 = away.split()[0]

    elo_home = pred.get("home_elo") or pred.get("elo_home", 1500)
    elo_away = pred.get("away_elo") or pred.get("elo_away", 1500)
    league_line = f" {league}" if league and league != "default" else ""

    # ── Header box ──
    title = f"⚽  {home}  vs  {away}"
    lines = [
        "╔══════════════════════════════════╗",
        f"  {title}",
        f"  🏆{league_line}   Elo: {elo_home:.0f} — {elo_away:.0f}",
        "╚══════════════════════════════════╝",
        "",
    ]

    # ── Model section: xG, xT, PPDA, Field Tilt ──
    xg_h = pred.get("xg_home", 0)
    xg_a = pred.get("xg_away", 0)
    xt_h = pred.get("xt_home", 0)
    xt_a = pred.get("xt_away", 0)
    ppda_h = pred.get("ppda_home", 10)
    ppda_a = pred.get("ppda_away", 10)
    tilt_h = pred.get("tilt_home", 50)
    tilt_a = pred.get("tilt_away", 50)

    lines += [
        "📐 *MODELO*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"  xG         `{xg_h:.2f}` ─── `{xg_a:.2f}`",
        f"  xThreat    `{xt_h:.2f}` ─── `{xt_a:.2f}`",
        f"  PPDA       `{ppda_h:.1f}` ─── `{ppda_a:.1f}`",
        f"  Field Tilt `{tilt_h:.1f}%` ─── `{tilt_a:.1f}%`",
        "",
    ]

    # ── Probabilities with visual bars ──
    hw = pred.get("home_win", 0)
    dr = pred.get("draw", 0)
    aw = pred.get("away_win", 0)
    lines += [
        "🏆 *PROBABILIDADES 1X2*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"  {home1:<12} `{hw:5.1f}%` {_bar(hw)}",
        f"  Empate      `{dr:5.1f}%` {_bar(dr)}",
        f"  {away1:<12} `{aw:5.1f}%` {_bar(aw)}",
        "",
    ]

    # Top scoreline
    top_score = pred["top_scores"][0] if pred.get("top_scores") else ("?-?", 0)
    lines.append(f"🎯 *Marcador probable:* `{top_score[0]}` ({top_score[1]:.1f}%)")
    lines.append("")

    # ── Markets with bars ──
    o15 = pred.get("over_1_5", 0)
    o25 = pred.get("over_2_5", 0)
    o35 = pred.get("over_3_5", 0)
    btts = pred.get("btts", 0)
    lines += [
        "📊 *MERCADOS*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"  Over 1.5  `{o15:5.1f}%` {_bar(o15)}",
        f"  Over 2.5  `{o25:5.1f}%` {_bar(o25)}",
        f"  Over 3.5  `{o35:5.1f}%` {_bar(o35)}",
        f"  BTTS      `{btts:5.1f}%` {_bar(btts)}",
        "",
    ]

    # ── Props: corners, cards, clean sheet, SOT ──
    corner_mkt = pred.get("corners_market", {})
    cd = pred.get("cards_detail")
    cs_home = pred.get("clean_sheet_home")
    cs_away = pred.get("clean_sheet_away")
    sot = pred.get("shots_on_target")

    lines.append("🔧 *PROPS*")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    if corner_mkt and corner_mkt.get("total") is not None:
        lines.append(
            f"  🚩 Córners  {home1}: `{corner_mkt['home']}` | {away1}: `{corner_mkt['away']}` | "
            f"Total: `{corner_mkt['total']}` → `{corner_mkt['suggestion']} {corner_mkt['line']}`"
        )
    else:
        lines.append(f"  🚩 Córners total: `{pred.get('corners_total', pred.get('corners', '?'))}`")

    if cd:
        over_label = "Over 3.5" if cd["over_3_5_cards"] else "Under 3.5"
        lines.append(
            f"  🟨 Tarjetas  {home1}: `{cd['yellow_home']}A` {away1}: `{cd['yellow_away']}A`"
            f"  🟥 Rojas: `{cd['total_red']:.1f}` → `{over_label}`"
        )
    else:
        lines.append(f"  🟨 Tarjetas: `{pred.get('cards', '?')}`")

    if cs_home is not None and cs_away is not None:
        lines.append(
            f"  🔒 CS  {home1}: `{cs_home*100:.0f}%` | {away1}: `{cs_away*100:.0f}%`"
        )

    if sot:
        lines.append(
            f"  👁 Tiros  {home1}: `{sot['sot_home']}` | {away1}: `{sot['sot_away']}` "
            f"Total: `{sot['sot_total']}` → `{sot['suggestion']} {sot['line']}`"
        )
    lines.append("")

    # ── Context: form + H2H ──
    fh = pred.get("form_home", {})
    fa = pred.get("form_away", {})
    form_home_str = f"{fh.get('emoji', '➡️')} `{fh.get('last5', '-----')}`"
    form_away_str = f"{fa.get('emoji', '➡️')} `{fa.get('last5', '-----')}`"

    lines += [
        "📈 *CONTEXTO*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"  {home1}: {form_home_str}",
        f"  {away1}: {form_away_str}",
    ]

    h2h = pred.get("h2h", {})
    if h2h.get("total", 0) >= 3:
        lines.append(
            f"  🔄 H2H ({h2h['total']}): {home1} {h2h['home_wins']}-{h2h['draws']}-{h2h['away_wins']}"
            f"  Prom: {h2h.get('avg_goals', 0)} goles"
        )
    lines.append("")

    # ── Win to Nil ──
    wtn = pred.get("win_to_nil")
    if wtn:
        value_tag = " ⭐ *VALOR ALTO*" if wtn.get("high_value") else ""
        lines += [
            "💡 *PICK ADICIONAL*",
            "━━━━━━━━━━━━━━━━━━━━",
            f"  🔒 Victoria a cero: *{wtn['team']}*{value_tag}",
            "",
        ]

    # ── Value bets (only when user supplied odds) ──
    value_lines = []
    for market, val in (pred.get("value_bets") or {}).items():
        if val and val > 0:
            value_lines.append(f"  ✅ {market.capitalize()}: +{val:.3f}")
    if value_lines:
        lines += ["💰 *VALUE BETS*", "━━━━━━━━━━━━━━━━━━━━"] + value_lines + [""]

    # ── Sharp Game section ──
    sharp = pred.get("sharp", {})
    if sharp and sharp.get("is_sharp"):
        lines += [
            "🔱 *SHARP GAME — EDGE DETECTADO*",
            "━━━━━━━━━━━━━━━━━━━━",
            f"  Pick: *{sharp['pick']}* ({sharp['pick_prob']:.1f}%)",
            f"  Edge score: `{sharp['edge_score']}`",
        ]
        for reason in sharp.get("reasons", [])[:4]:
            lines.append(f"  • {reason}")
        lines.append("")

    # ── Best pick + confidence ──
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        f"💡 *Mejor Pick:* {_best_pick(pred)}",
        f"{conf_emoji} *Confianza:* {conf}" + _live_source_badge(pred),
    ]

    return "\n".join(lines)


def _live_source_badge(pred: dict) -> str:
    """Return a small live-data source note when live data was used."""
    source = pred.get("live_source")
    if not source:
        return ""
    names = {"sofascore": "SofaScore", "thesportsdb": "TheSportsDB", "espn": "ESPN"}
    pretty = names.get(source, source.capitalize())
    return f"\n📡 _Forma en vivo: {pretty}_"


# ===============================
# 📌 COMANDOS DEL BOT
# ===============================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Sports Engine — Multi-Sport + Datos en Vivo*\n\n"
        "⚽ Fútbol: `/predict LOCAL vs VISITANTE`\n"
        "🏀 NBA: `/nba LOCAL vs VISITANTE`\n"
        "⚾ MLB: `/mlb LOCAL vs VISITANTE`\n"
        "🏈 NFL: `/nfl LOCAL vs VISITANTE`\n"
        "🎾 Tenis: `/tennis J1 vs J2 [clay/grass/hard]`\n\n"
        "🎰 Parlays: `/parlay` — parlays confiables del día\n"
        "🎯 Parlay Safe: `/parlay_safe` — máximo hit rate (2-3 patas, moneyline)\n"
        "📸 Analizar tu parlay: `/checkparlay` o envía una *foto* con caption\n"
        "📋 Reportar resultado: `/resultado <id> WLW`\n"
        "📊 Historial & calibración: `/historial`\n"
        "🧠 Estadísticas detalladas: `/estadisticas`\n\n"
        "🔬 *Analytics avanzados*\n"
        "  `/form EQUIPO` · `/intel L vs V` · `/markets L vs V`\n"
        "  `/bayes L vs V` · `/referee ÁRBITRO` · `/weather`\n"
        "  `/player EQUIPO STATUS` · `/clv` · `/risk`\n\n"
        "💡 *Mercados & Dinero inteligente*\n"
        "  `/consensus` · `/steam` · `/liquidity`\n"
        "  `/portfolio` · `/rl`\n\n"
        "🤖 *Auto-Scanner (24/7 automático)*\n"
        "  Detecta arbitraje, errores de cuota, value bets y steam moves\n"
        "  sin intervención manual. Alertas automáticas al canal.\n"
        "  `/autoscan` — ver estado del scanner\n\n"
        "🔍 *Scanner manual*\n"
        "  `/scanodds EVENTO | MERCADO | cuota@casa...`\n"
        "  `/scanner` · `/addmarket` · `/clearmarkets`\n\n"
        "📡 *Datos en vivo (SofaScore / TheSportsDB / ESPN)*\n"
        "  `/live [deporte]` · `/scores` · `/liveteam EQUIPO` · `/tabla LIGA`\n\n"
        "📅 `/today` — partidos de hoy\n"
        "❓ `/help` — ayuda detallada\n"
        "🎛 `/menu` — menú con botones",
        parse_mode="Markdown",
    )
    # Show the interactive button menu right after the welcome message
    await update.message.reply_text(
        "🎛 *Menú rápido — toca un botón para comenzar:*",
        parse_mode="Markdown",
        reply_markup=_build_inline_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Ayuda — Sports Engine*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚽ *FÚTBOL*\n"
        "🔹 `/predict LOCAL vs VISITANTE`\n"
        "  xG, xThreat, PPDA, Field Tilt, 1X2, Over/BTTS,\n"
        "  marcador probable, forma en vivo, H2H, córners, tarjetas.\n"
        "  _Ej:_ `/predict Real Madrid vs Barcelona`\n\n"
        "🔹 `/value L vs V C\\_L C\\_E C\\_V` — Betting Intelligence\n"
        "  Kelly, EV, margen de la casa, cuotas justas.\n"
        "🔹 `/stats EQUIPO` — stats históricas del equipo\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔬 *ANALYTICS AVANZADOS*\n"
        "🔹 `/form EQUIPO`\n"
        "  Team Form Engine profundo: PPG, BTTS, Over rates,\n"
        "  CS rate, FTS, consistencia goleadora, tendencia.\n"
        "  _Ej:_ `/form América`\n\n"
        "🔹 `/intel LOCAL vs VISITANTE`\n"
        "  Game Intelligence: rachas BTTS/Over/CS, patrón H2H,\n"
        "  trampa detectada, señal Over/BTTS agregada.\n"
        "  _Ej:_ `/intel Barcelona vs Real Madrid`\n\n"
        "🔹 `/markets LOCAL vs VISITANTE`\n"
        "  Modelos de mercado completos: O/U 0.5-4.5, Asian\n"
        "  Handicap -2.5→+2.5, HT/FT, CS, DNB, Double Chance.\n"
        "  _Ej:_ `/markets Liverpool vs Arsenal`\n\n"
        "🎰 `/parlay` — parlays confiables del día\n"
        "🎯 `/parlay_safe` — parlay de máximo hit rate (2-3 patas, moneyline, filtros estrictos)\n"
        "📸 `/checkparlay <patas>` — analiza tu parlay propio\n"
        "  _Envía también una foto con el caption de las patas_\n"
        "  _Ej:_ `/checkparlay Burnley vs Bournemouth Over 2.5 @1.75; Lakers vs Warriors @2.10`\n"
        "📋 `/resultado [<id>] <WLWWL>` — reporta el resultado de un parlay\n"
        "  _Ej:_ `/resultado P240315-2 WLW` · W=Ganó L=Perdió X=Cancelado\n"
        "📊 `/historial` — historial, tasas de acierto y calibración automática\n"
        "🧠 `/estadisticas` — calibración por deporte, liga, mercado y bucket de prob\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 *UNIVERSAL MARKET ERROR SCANNER*\n"
        "🔹 `/scanodds EVENTO | MERCADO | cuota@casa...`\n"
        "  Detecta errores de cuota al instante. Válido para cualquier\n"
        "  deporte (fútbol, NBA, MMA, esports...) y cualquier mercado\n"
        "  (goles, props jugador, corners, puntos, etc.).\n"
        "  _Ej:_ `/scanodds Madrid vs Barça | Victoria Madrid | 1.90@Bet365 2.50@Caliente 1.85@Codere`\n"
        "  _Ej:_ `/scanodds NBA | Lakers vs Warriors | LeBron Pts +25.5 | 1.85@Betway 2.20@Caliente`\n\n"
        "🔹 `/scanner [DEPORTE]` — escanear todos los mercados guardados\n"
        "🔹 `/addmarket DEPORTE | EVENTO | MERCADO | cuota@casa...`\n"
        "  Agrega mercado a seguimiento continuo (auto-escaneado c/15 min).\n"
        "🔹 `/clearmarkets` — borrar todos los mercados en seguimiento\n\n"
        "  *Alertas:*\n"
        "  🟡 Value Opportunity  ≥20% sobre promedio\n"
        "  🔥 High Value         ≥30% sobre promedio\n"
        "  🚨 Market Error       ≥40% sobre promedio\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🧠 *INTELIGENCIA AVANZADA*\n"
        "🔹 `/liquidity EVENTO | MERCADO | cuota@casa...`\n"
        "  Analiza profundidad de mercado y line shopping.\n\n"
        "🔹 `/steam EVENTO | MERCADO | abierta@casa actual@casa...`\n"
        "  Detecta movimientos de dinero sharp (Steam moves).\n"
        "  _Ej:_ `/steam Madrid vs Barça | Victoria Madrid | 2.10@B365:1.82 2.05@Cal:1.80`\n\n"
        "🔹 `/consensus EVENTO | Casa1:H/D/A Casa2:H/D/A...`\n"
        "  Modelo de consenso de mercado sin margen (precio justo).\n\n"
        "🔹 `/clv [log | update EVENTO MERCADO CIERRE]`\n"
        "  Tracker de Closing Line Value. Registra y analiza tus picks.\n\n"
        "🔹 `/risk PROB CUOTA [BANKROLL]`\n"
        "  Gestión de riesgo: Kelly, EV, riesgo de ruina.\n"
        "  _Ej:_ `/risk 0.58 1.85 1000`\n\n"
        "🔹 `/player EQUIPO | Jugador1:status Jugador2:status`\n"
        "  Impacto de jugadores en xG (bajas, dudas, retornos).\n"
        "  Status: absent · doubt · returning · available\n"
        "  _Ej:_ `/player Man City | Haaland:absent DeBruyne:doubt`\n\n"
        "🔹 `/referee ÁRBITRO vs PARTIDO`\n"
        "  Perfil del árbitro: tarjetas, penaltis, estilo.\n"
        "  _Ej:_ `/referee Jesus Gil Manzano`\n\n"
        "🔹 `/weather CONDICIÓN TEMP VIENTO LLUVIA CESPED`\n"
        "  Impacto del clima en xG, córners y tarjetas.\n"
        "  _Ej:_ `/weather lluvia 8 45 5 wet`\n\n"
        "🔹 `/portfolio PROB1:CUOTA1:DEPORTE:MERCADO PROB2:CUOTA2:...`\n"
        "  Optimizador de portfolio: stakes Kelly óptimos.\n"
        "  _Ej:_ `/portfolio 0.58:1.85:Fútbol:HomeWin 0.61:1.72:NBA:Over`\n\n"
        "🔹 `/bayes LOCAL vs VISITANTE [opciones]`\n"
        "  Actualización bayesiana de probabilidades con evidencia.\n\n"
        "🔹 `/rl CONFIANZA EV DRAWDOWN [BANKROLL]`\n"
        "  Estrategia RL (Q-Learning): stake recomendado por el agente.\n"
        "  _Ej:_ `/rl ALTA 5.2 3.0 1000`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📡 *DATOS EN VIVO* _(SofaScore / TheSportsDB / ESPN)_\n"
        "🔹 `/live [deporte]` | `/scores [deporte]`\n"
        "🔹 `/liveteam EQUIPO` | `/tabla LIGA`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🏀 `/nba H vs V` | ⚾ `/mlb H vs V` | 🏈 `/nfl H vs V`\n"
        "🎾 `/tennis J1 vs J2 [clay/grass/hard]`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*Confianza:* 🟢 ALTA ≥55% | 🟡 MEDIA ≥42% | 🔴 BAJA\n"
        "_Motor: Poisson + Dixon-Coles + MC + Elo + Decay Form + H2H_\n"
        "_Módulos: xThreat · PPDA · FieldTilt · Sharp · CLV · Steam_\n"
        "_Módulos: Liquidity · Consensus · Player · Referee · Weather_\n"
        "_Módulos: Portfolio · Bayes · RL · AH · HT/FT · Parlay_\n"
        "_Fuentes: SofaScore · TheSportsDB · ESPN_",
        parse_mode="Markdown",
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show today's football matches (local CSV) + live ESPN schedule for all sports."""
    # ── Refresh CSV if stale (TTL = 15 min), non-blocking ──
    _refresh_matches_if_stale()

    # ── Local football matches ──
    football_matches = load_today_matches()

    text = "📅 *Partidos de hoy*\n\n"

    if football_matches:
        text += "⚽ *Fútbol*\n"
        for m in football_matches:
            league = f" _{m['league']}_" if m["league"] else ""
            text += f"  • {m['home']} vs {m['away']}{league}\n"
        # Show last update timestamp for the CSV
        if _last_csv_update:
            ts = datetime.fromtimestamp(_last_csv_update).strftime("%H:%M")
            text += f"_Actualizado: {ts}_\n"
        text += "\n"

    # ── ESPN multi-sport schedule ──
    try:
        espn_games = get_all_scoreboards()
        by_sport: dict = {}
        for g in espn_games:
            by_sport.setdefault(g["sport"], []).append(g)

        sport_emojis = {"NBA": "🏀", "NFL": "🏈", "MLB": "⚾", "ATP": "🎾"}
        for sport, games in by_sport.items():
            emoji = sport_emojis.get(sport, "🏟️")
            text += f"{emoji} *{sport}*\n"
            for g in games[:8]:  # cap at 8 per sport
                score = f" `{g['home_score']}-{g['away_score']}`" if g.get("home_score") and g.get("away_score") else ""
                status = f" _{g['status']}_" if g.get("status") and g["status"] != "Scheduled" else ""
                text += f"  • {g['home']} vs {g['away']}{score}{status}\n"
            text += "\n"
    except Exception as exc:
        logger.warning("ESPN scoreboard unavailable: %s", exc)

    if not football_matches and not text.strip().endswith("*\n"):
        text += "📭 No hay partidos disponibles.\n\nUsa `/sports` para ver todos los comandos."

    await update.message.reply_text(text, parse_mode="Markdown")


async def predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Uso incorrecto.\n\nFormato:\n`/predict LOCAL vs VISITANTE`",
            parse_mode="Markdown",
        )
        return

    raw_text = " ".join(context.args)

    if " vs " not in raw_text.lower():
        await update.message.reply_text(
            "❌ Formato incorrecto.\n\nUsa:\n`LOCAL vs VISITANTE`",
            parse_mode="Markdown",
        )
        return

    home_raw, away_raw = raw_text.split(" vs ", 1)
    home = home_raw.strip()
    away = away_raw.strip()

    await update.message.reply_text(
        "⏳ Analizando partido… _(buscando datos en vivo)_",
        parse_mode="Markdown",
    )

    try:
        # fetch_live=True: automatically pull live form from SofaScore / TheSportsDB
        prediction = get_full_prediction(home, away, fetch_live=True)
        try:
            from core.backtesting import log_pick
            log_pick(prediction)
        except Exception as _log_err:
            logger.warning("Could not log pick: %s", _log_err)
        await update.message.reply_text(
            format_prediction(prediction), parse_mode="Markdown"
        )

    except ValueError as e:
        # Team not found — show suggestions
        msg = str(e)
        suggestions_home = suggest_teams(home)
        suggestions_away = suggest_teams(away)
        tip = ""
        if suggestions_home:
            tip += f"\n\n¿Quisiste decir (local)?\n" + "\n".join(
                f"  • {s}" for s in suggestions_home
            )
        if suggestions_away:
            tip += f"\n\n¿Quisiste decir (visitante)?\n" + "\n".join(
                f"  • {s}" for s in suggestions_away
            )
        await update.message.reply_text(f"❌ {msg}{tip}")

    except Exception as e:
        logger.exception("Error al analizar partido %s vs %s", home, away)
        await update.message.reply_text(f"❌ Error al analizar el partido: {e}")


async def value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /value LOCAL vs VISITANTE C_HOME C_DRAW C_AWAY
    Enhanced with full Betting Intelligence: Kelly, EV, margin, fair odds.
    Example: /value América vs Chivas 1.80 3.40 4.50
    """
    if not context.args or len(context.args) < 5:
        await update.message.reply_text(
            "❌ Formato:\n`/value LOCAL vs VISITANTE C\\_LOCAL C\\_EMPATE C\\_VISIT`\n\n"
            "Ejemplo:\n`/value América vs Chivas 1.80 3.40 4.50`",
            parse_mode="Markdown",
        )
        return

    try:
        odds_away = float(context.args[-1])
        odds_draw = float(context.args[-2])
        odds_home = float(context.args[-3])
    except ValueError:
        await update.message.reply_text(
            "❌ Las cuotas deben ser números. Ejemplo: `1.80 3.40 4.50`",
            parse_mode="Markdown",
        )
        return

    match_text = " ".join(context.args[:-3])

    if " vs " not in match_text.lower():
        await update.message.reply_text(
            "❌ Formato incorrecto. Usa:\n`/value LOCAL vs VISITANTE C\\_LOCAL C\\_EMPATE C\\_VISIT`",
            parse_mode="Markdown",
        )
        return

    home_raw, away_raw = match_text.split(" vs ", 1)
    home = home_raw.strip()
    away = away_raw.strip()

    await update.message.reply_text("⏳ Calculando Betting Intelligence…")

    try:
        user_odds = {"home": odds_home, "draw": odds_draw, "away": odds_away}
        prediction = get_full_prediction(home, away, odds=user_odds)

        from core.betting_intelligence import analyze_betting_markets, format_betting_intelligence
        analysis = analyze_betting_markets(prediction, user_odds)
        text = format_betting_intelligence(analysis, prediction)
        await update.message.reply_text(text, parse_mode="Markdown")

    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        logger.exception("Error en /value %s vs %s", home, away)
        await update.message.reply_text(f"❌ Error: {e}")



# ===============================
# 📌 COMMANDS — Football/Soccer
# ===============================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /stats         — Show bot prediction statistics (picks hit rate)
    /stats EQUIPO  — Show team historical stats
    """
    if not context.args:
        # Show bot prediction statistics
        from core.backtesting import get_stats_summary
        stats_data = get_stats_summary(days=30)

        if stats_data["total_picks"] == 0:
            await update.message.reply_text("📊 No hay picks registrados aún.")
            return

        text = (
            "📊 *ESTADÍSTICAS DEL BOT*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📋 Total picks: {stats_data['total_picks']}\n"
            f"✅ Aciertos: {stats_data['correct']}\n"
            f"📈 Hit rate: {stats_data['hit_rate']}%\n\n"
            "📊 *Por nivel de confianza:*\n"
        )

        for level in ["ALTA", "MEDIA", "BAJA"]:
            data = stats_data["by_confidence"].get(level, {})
            if data.get("total", 0) > 0:
                emoji = "🟢" if level == "ALTA" else "🟡" if level == "MEDIA" else "🔴"
                text += f"{emoji} {level}: {data['correct']}/{data['total']} ({data['hit_rate']}%)\n"

        last7 = stats_data.get("last_7_days", {})
        if last7.get("total", 0) > 0:
            text += f"\n📅 Últimos 7 días: {last7['hit_rate']}% ({last7['correct']}/{last7['total']})\n"

        if stats_data.get("streak", 0) > 0:
            text += f"🔥 Racha actual: {stats_data['streak']} aciertos seguidos\n"

        await update.message.reply_text(text, parse_mode="Markdown")
        return

    # Show team stats
    team_name = " ".join(context.args)
    s = get_team_stats_summary(team_name)

    if not s:
        suggestions = suggest_teams(team_name)
        tip = ""
        if suggestions:
            tip = "\n\n¿Quisiste decir?\n" + "\n".join(f"  • {t}" for t in suggestions)
        await update.message.reply_text(
            f"❌ Equipo '{team_name}' no encontrado.{tip}"
        )
        return

    streak = s["streak"]
    streak_str = (
        f"{streak['length']} {'victorias' if streak['type']=='W' else 'derrotas' if streak['type']=='L' else 'empates'} seguidos"
        if streak["type"] != "none"
        else "sin racha definida"
    )

    cs_home_pct = f"{s['cs_home_prob']*100:.0f}%" if s["cs_home_prob"] is not None else "N/A"
    cs_away_pct = f"{s['cs_away_prob']*100:.0f}%" if s["cs_away_prob"] is not None else "N/A"
    league_str = f" _{s['league']}_" if s["league"] != "default" else ""

    text = (
        f"📊 *Stats: {s['name']}*{league_str}\n\n"
        f"🏠 *En casa* ({s.get('home_games', '?')} partidos)\n"
        f"  ⚽ Ataque: `{s['home_attack']}` goles/partido\n"
        f"  🛡️ Defensa: `{s['home_defense']}` concedidos/partido\n"
        f"  🔒 Clean Sheet: `{cs_home_pct}`\n\n"
        f"✈️ *Fuera* ({s.get('away_games', '?')} partidos)\n"
        f"  ⚽ Ataque: `{s['away_attack']}` goles/partido\n"
        f"  🛡️ Defensa: `{s['away_defense']}` concedidos/partido\n"
        f"  🔒 Clean Sheet: `{cs_away_pct}`\n\n"
        f"📈 *Forma reciente* (últimos 5)\n"
        f"  {s['form_emoji']} `{s['last5']}`\n"
        f"  Racha: {streak_str}\n"
    )

    await update.message.reply_text(text, parse_mode="Markdown")


# ===============================
# 📌 COMMANDS — Multi-sport
# ===============================

def _format_sport_prediction(pred: dict) -> str:
    """
    Generic formatter for non-football sport predictions.
    Handles NBA, NFL, MLB, and Tennis.
    """
    sport = pred.get("sport", "")
    home = pred["home"]
    away = pred["away"]
    conf = pred.get("confidence", "BAJA")
    conf_emoji = _confidence_emoji(conf)

    live_note = "" if pred.get("live_data") else "\n⚠️ _Sin datos ESPN — usando promedios de liga_"

    # Records
    home_rec = f" `{pred['home_record']}`" if pred.get("home_record") else ""
    away_rec = f" `{pred['away_record']}`" if pred.get("away_record") else ""

    lines = [f"*{sport}*", f"*{home}{home_rec}* vs *{away}{away_rec}*\n"]

    # Win probabilities
    lines.append("🏆 *Probabilidades*")
    lines.append(f"  {home}: `{pred['home_win']:.1f}%`")
    lines.append(f"  {away}: `{pred['away_win']:.1f}%`\n")

    # Sport-specific score/spread block
    if pred.get("expected_home") is not None:
        sport_key = sport.split()[0]
        if sport_key == "NBA":
            lines.append("🎯 *Marcador proyectado*")
            lines.append(f"  {home}: `{pred['expected_home']:.0f}` pts")
            lines.append(f"  {away}: `{pred['expected_away']:.0f}` pts")
            lines.append(f"  Over/Under: `{pred['over_under']}` pts")
            if pred.get("spread_str"):
                lines.append(f"  Spread: `{pred['spread_str']}`")
            lines.append("")
            lines.append("📊 *Estadísticas de temporada*")
            lines.append(f"  {home}: `{pred.get('home_ppg', '?')}` PPG / `{pred.get('home_oppg', '?')}` OPPG")
            lines.append(f"  {away}: `{pred.get('away_ppg', '?')}` PPG / `{pred.get('away_oppg', '?')}` OPPG\n")

            # Quarter projections
            quarters = pred.get("quarter_projections")
            if quarters:
                lines.append("📋 *Proyección por cuarto*")
                lines.append(f"  {'C':<3} {'Local':>6} {'Visit':>6} {'Total':>6}")
                for q in quarters:
                    lines.append(
                        f"  Q{q['quarter']:<2} `{q['home']:>5.1f}` `{q['away']:>5.1f}` `{q['total']:>5.1f}`"
                    )
                lines.append("")

            # Player props
            pp = pred.get("player_props", {})
            if pp:
                home_pp = pp.get("home", {})
                away_pp = pp.get("away", {})
                lines.append("🎲 *Props de jugador (líneas estimadas)*")
                lines.append(f"  📌 Estrella pts: {home} `{home_pp.get('star_points', '?')}` / {away} `{away_pp.get('star_points', '?')}`")
                lines.append(f"  📌 2° anotador:  {home} `{home_pp.get('2nd_scorer', '?')}` / {away} `{away_pp.get('2nd_scorer', '?')}`")
                lines.append(f"  🎯 Asistencias PG: {home} `{home_pp.get('assists', '?')}` / {away} `{away_pp.get('assists', '?')}`")
                lines.append(f"  💪 Rebotes (C):  {home} `{home_pp.get('rebounds_big', '?')}` / {away} `{away_pp.get('rebounds_big', '?')}`")
                lines.append(f"  🔄 Rebotes (ala): {home} `{home_pp.get('rebounds_wing', '?')}` / {away} `{away_pp.get('rebounds_wing', '?')}`")
                lines.append("")

        elif sport_key == "NFL":
            lines.append("🎯 *Marcador proyectado*")
            lines.append(f"  {home}: `{pred['expected_home']:.0f}` pts")
            lines.append(f"  {away}: `{pred['expected_away']:.0f}` pts")
            lines.append(f"  Over/Under: `{pred['over_under']}` pts")
            if pred.get("spread_str"):
                lines.append(f"  Spread: `{pred['spread_str']}`")
            lines.append("")
            lines.append("📊 *Estadísticas de temporada*")
            lines.append(f"  {home}: `{pred.get('home_ppg', '?')}` PPG / `{pred.get('home_oppg', '?')}` OPPG")
            lines.append(f"  {away}: `{pred.get('away_ppg', '?')}` PPG / `{pred.get('away_oppg', '?')}` OPPG\n")

            # Quarter projections
            quarters = pred.get("quarter_projections")
            if quarters:
                lines.append("📋 *Proyección por cuarto*")
                lines.append(f"  {'Q':<3} {'Local':>6} {'Visit':>6} {'Total':>6}")
                for q in quarters:
                    lines.append(
                        f"  Q{q['quarter']:<2} `{q['home']:>5.1f}` `{q['away']:>5.1f}` `{q['total']:>5.1f}`"
                    )
                lines.append("")

            # Player props
            pp = pred.get("player_props", {})
            if pp:
                home_pp = pp.get("home", {})
                away_pp = pp.get("away", {})
                lines.append("🎲 *Props de jugador QB (líneas estimadas)*")
                lines.append(f"  🏈 Yardas pase:  {home} `{home_pp.get('qb_pass_yards', '?'):.0f}` / {away} `{away_pp.get('qb_pass_yards', '?'):.0f}`")
                lines.append(f"  🎯 TDs pase:      {home} `{home_pp.get('qb_pass_tds', '?')}` / {away} `{away_pp.get('qb_pass_tds', '?')}`")
                lines.append(f"  ✅ Compleciones:  {home} `{home_pp.get('qb_completions', '?'):.0f}` / {away} `{away_pp.get('qb_completions', '?'):.0f}`")
                lines.append(f"  🏃 Yardas tierra: {home} `{home_pp.get('rb_rush_yards', '?'):.0f}` / {away} `{away_pp.get('rb_rush_yards', '?'):.0f}`")
                lines.append(f"  🏃 TDs tierra:    {home} `{home_pp.get('rb_rush_tds', '?')}` / {away} `{away_pp.get('rb_rush_tds', '?')}`")
                lines.append(f"  🙌 Recepciones RB:{home} `{home_pp.get('rb_receptions', '?')}` / {away} `{away_pp.get('rb_receptions', '?')}`")
                lines.append(f"  📡 Yardas WR1:    {home} `{home_pp.get('wr1_recv_yards', '?'):.0f}` / {away} `{away_pp.get('wr1_recv_yards', '?'):.0f}`")
                lines.append(f"  📡 Recepciones WR:{home} `{home_pp.get('wr1_receptions', '?')}` / {away} `{away_pp.get('wr1_receptions', '?')}`")
                lines.append(f"  🎯 TDs WR1:       {home} `{home_pp.get('wr1_recv_tds', '?')}` / {away} `{away_pp.get('wr1_recv_tds', '?')}`")
                lines.append("")

        elif sport_key == "MLB":
            lines.append("🎯 *Carreras proyectadas*")
            lines.append(f"  {home}: `{pred['expected_home']}` runs")
            lines.append(f"  {away}: `{pred['expected_away']}` runs")
            lines.append(f"  Over/Under: `{pred['over_under']}` runs")
            if pred.get("home_era"):
                lines.append(f"  ERA pitcheo — {home}: `{pred['home_era']}` / {away}: `{pred.get('away_era', '?')}`")

            # Run line (MLB spread equivalent)
            rl = pred.get("run_line", {})
            if rl:
                fav_side = rl.get("fav_side", "")
                fav_name = home if fav_side == "home" else away
                lines.append(f"  Run line (-1.5): `{fav_name}` cubre `{rl.get('cover_prob', '?')}%`")
            lines.append("")

            # Player props
            pp = pred.get("player_props", {})
            if pp:
                home_pp = pp.get("home", {})
                away_pp = pp.get("away", {})
                lines.append("🎲 *Props de jugador (líneas estimadas)*")
                lines.append(f"  🎽 Hits equipo:    {home} `{home_pp.get('team_hits', '?')}` / {away} `{away_pp.get('team_hits', '?')}`")
                lines.append(f"  ⚾ Hits cleanup:   {home} `{home_pp.get('cleanup_hits', '?')}` / {away} `{away_pp.get('cleanup_hits', '?')}`")
                lines.append(f"  💣 HR cleanup:     {home} `{home_pp.get('cleanup_hr', '?')}` / {away} `{away_pp.get('cleanup_hr', '?')}`")
                lines.append(f"  🔥 Ks abridor:     {home} `{home_pp.get('ace_strikeouts', '?')}` Ks / {away} `{away_pp.get('ace_strikeouts', '?')}` Ks")
                lines.append("")

    # Tennis-specific
    if "Tenis" in sport:
        lines.append(f"🎾 *Superficie:* {pred.get('surface', 'hard').capitalize()}")
        lines.append(f"📏 *Formato:* Mejor de {pred.get('best_of', 3)}")
        lines.append(f"📊 *Elo aproximado*")
        lines.append(f"  {home}: `{pred.get('elo_p1', '?')}`")
        lines.append(f"  {away}: `{pred.get('elo_p2', '?')}`\n")

    lines.append(f"💡 *Mejor Pick:* {pred['best_bet']}")
    lines.append(f"{conf_emoji} *Confianza:* {conf}")
    lines.append(live_note)

    return "\n".join(lines)


async def sports_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/sports — List all available sport commands."""
    await update.message.reply_text(
        "🏟️ *Sports Engine — Todos los comandos*\n\n"
        "⚽ *Fútbol* — mercados completos incl. tiros a puerta, tarjetas y córners\n"
        "  `/predict LOCAL vs VISITANTE`\n"
        "  `/value LOCAL vs VISITANTE C\\_L C\\_E C\\_V`\n"
        "  `/stats EQUIPO`\n\n"
        "🏀 *NBA* — spread, O/U, cuartos, props (pts/reb/ast)\n"
        "  `/nba LOCAL vs VISITANTE`\n\n"
        "⚾ *MLB* — carreras, run-line, hits equipo, HR y Ks del abridor\n"
        "  `/mlb LOCAL vs VISITANTE`\n\n"
        "🏈 *NFL* — spread, O/U, cuartos, props QB/RB/WR\n"
        "  `/nfl LOCAL vs VISITANTE`\n\n"
        "🎾 *Tenis* — `/tennis J1 vs J2 [clay/grass/hard]`\n\n"
        "📡 *Datos en vivo*\n"
        "  `/live [deporte]` — marcadores en vivo ahora\n"
        "  `/scores [deporte]` — partidos de hoy\n"
        "  `/liveteam EQUIPO` — forma + próximos partidos\n"
        "  `/tabla LIGA` — clasificación\n\n"
        "📅 `/today` — agenda multideporte\n"
        "❓ `/help` — ayuda detallada\n\n"
        "_Props basados en promedios de liga escalados al rendimiento del equipo._\n"
        "_Fuentes: SofaScore · TheSportsDB · ESPN_",
        parse_mode="Markdown",
    )


async def nba(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/nba HOME vs AWAY — NBA game prediction."""
    if not context.args or " vs " not in " ".join(context.args).lower():
        await update.message.reply_text(
            "❌ Formato:\n`/nba LOCAL vs VISITANTE`\n\n_Ej:_ `/nba Lakers vs Celtics`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    home_raw, away_raw = raw.split(" vs ", 1)
    home_in = home_raw.strip()
    away_in = away_raw.strip()

    home = _bball.resolve_team(home_in) or home_in
    away = _bball.resolve_team(away_in) or away_in

    await update.message.reply_text("⏳ Analizando partido NBA…")
    try:
        pred = _bball.predict_game(home, away)
        await update.message.reply_text(
            _format_sport_prediction(pred), parse_mode="Markdown"
        )
    except Exception as exc:
        logger.exception("Error en /nba %s vs %s", home, away)
        # Try suggestions
        sugg_h = _bball.suggest_teams(home_in)
        sugg_a = _bball.suggest_teams(away_in)
        tip = ""
        if sugg_h:
            tip += "\n¿Local? " + ", ".join(sugg_h)
        if sugg_a:
            tip += "\n¿Visitante? " + ", ".join(sugg_a)
        await update.message.reply_text(f"❌ Error: {exc}{tip}")


async def mlb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mlb HOME vs AWAY — MLB game prediction."""
    if not context.args or " vs " not in " ".join(context.args).lower():
        await update.message.reply_text(
            "❌ Formato:\n`/mlb LOCAL vs VISITANTE`\n\n_Ej:_ `/mlb Yankees vs Red Sox`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    home_raw, away_raw = raw.split(" vs ", 1)
    home = _baseball.resolve_team(home_raw.strip()) or home_raw.strip()
    away = _baseball.resolve_team(away_raw.strip()) or away_raw.strip()

    await update.message.reply_text("⏳ Analizando partido MLB…")
    try:
        pred = _baseball.predict_game(home, away)
        await update.message.reply_text(
            _format_sport_prediction(pred), parse_mode="Markdown"
        )
    except Exception as exc:
        logger.exception("Error en /mlb %s vs %s", home, away)
        sugg_h = _baseball.suggest_teams(home_raw.strip())
        sugg_a = _baseball.suggest_teams(away_raw.strip())
        tip = ""
        if sugg_h:
            tip += "\n¿Local? " + ", ".join(sugg_h)
        if sugg_a:
            tip += "\n¿Visitante? " + ", ".join(sugg_a)
        await update.message.reply_text(f"❌ Error: {exc}{tip}")


async def nfl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/nfl HOME vs AWAY — NFL game prediction."""
    if not context.args or " vs " not in " ".join(context.args).lower():
        await update.message.reply_text(
            "❌ Formato:\n`/nfl LOCAL vs VISITANTE`\n\n_Ej:_ `/nfl Chiefs vs Eagles`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    home_raw, away_raw = raw.split(" vs ", 1)
    home = _nfl.resolve_team(home_raw.strip()) or home_raw.strip()
    away = _nfl.resolve_team(away_raw.strip()) or away_raw.strip()

    await update.message.reply_text("⏳ Analizando partido NFL…")
    try:
        pred = _nfl.predict_game(home, away)
        await update.message.reply_text(
            _format_sport_prediction(pred), parse_mode="Markdown"
        )
    except Exception as exc:
        logger.exception("Error en /nfl %s vs %s", home, away)
        sugg_h = _nfl.suggest_teams(home_raw.strip())
        sugg_a = _nfl.suggest_teams(away_raw.strip())
        tip = ""
        if sugg_h:
            tip += "\n¿Local? " + ", ".join(sugg_h)
        if sugg_a:
            tip += "\n¿Visitante? " + ", ".join(sugg_a)
        await update.message.reply_text(f"❌ Error: {exc}{tip}")


async def tennis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /tennis P1 vs P2 [clay/grass/hard]
    Example: /tennis Djokovic vs Alcaraz clay
    """
    if not context.args or " vs " not in " ".join(context.args).lower():
        await update.message.reply_text(
            "❌ Formato:\n`/tennis JUGADOR1 vs JUGADOR2 [clay/grass/hard]`\n\n"
            "_Ej:_ `/tennis Djokovic vs Alcaraz clay`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    # Extract optional surface from the end
    surface = "hard"
    for surf in ("clay", "grass", "hard"):
        if raw.lower().endswith(f" {surf}"):
            surface = surf
            raw = raw[: -(len(surf) + 1)].strip()
            break

    if " vs " not in raw.lower():
        await update.message.reply_text("❌ Usa el formato: `J1 vs J2 [surface]`", parse_mode="Markdown")
        return

    p1_raw, p2_raw = raw.split(" vs ", 1)
    p1 = _tennis.resolve_player(p1_raw.strip()) or p1_raw.strip()
    p2 = _tennis.resolve_player(p2_raw.strip()) or p2_raw.strip()

    await update.message.reply_text("⏳ Analizando partido de tenis…")
    try:
        pred = _tennis.predict_match(p1, p2, surface=surface)
        await update.message.reply_text(
            _format_sport_prediction(pred), parse_mode="Markdown"
        )
    except Exception as exc:
        logger.exception("Error en /tennis %s vs %s", p1, p2)
        sugg_p1 = _tennis.suggest_players(p1_raw.strip())
        sugg_p2 = _tennis.suggest_players(p2_raw.strip())
        tip = ""
        if sugg_p1:
            tip += "\n¿Jugador 1? " + ", ".join(sugg_p1)
        if sugg_p2:
            tip += "\n¿Jugador 2? " + ", ".join(sugg_p2)
        await update.message.reply_text(f"❌ Error: {exc}{tip}")


# ===============================
# 📡 COMMANDS — Live data (SofaScore / TheSportsDB / ESPN)
# ===============================

async def live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /live [sport]
    Show all currently live events.  Optional sport filter:
      football | nba | nfl | mlb | tennis
    Default: football.
    """
    sport = (context.args[0].lower() if context.args else "football")
    sport_map = {
        "futbol": "football", "soccer": "football",
        "basket": "basketball", "basquet": "basketball",
        "beisbol": "baseball", "baseball": "baseball",
        "americano": "american-football", "nfl": "american-football",
        "tenis": "tennis",
    }
    sport = sport_map.get(sport, sport)

    await update.message.reply_text("⏳ Buscando partidos en vivo…")
    try:
        events = get_live_scores(sport)
        sport_emoji = {
            "football": "⚽", "basketball": "🏀", "american-football": "🏈",
            "baseball": "⚾", "tennis": "🎾",
        }.get(sport, "🏟️")

        if not events:
            await update.message.reply_text(
                f"{sport_emoji} *{sport.capitalize()}*\n\n"
                f"📭 No hay partidos en vivo en este momento.\n\n"
                f"Prueba `/scores` para ver resultados del día.",
                parse_mode="Markdown",
            )
            return

        scoreboard = format_live_scoreboard(events)
        await update.message.reply_text(
            f"{sport_emoji} *LIVE — {sport.upper()}*\n\n{scoreboard}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.exception("Error en /live %s", sport)
        await update.message.reply_text(f"❌ Error al obtener datos en vivo: {exc}")


async def scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /scores [sport] [date]
    Show today's schedule and results.
    Optional sport (football/nba/nfl/mlb/tennis) and optional date YYYY-MM-DD.
    """
    args = context.args or []
    sport = "football"
    date = ""

    for arg in args:
        if "-" in arg and len(arg) == 10:  # YYYY-MM-DD
            date = arg
        else:
            sport_map = {
                "futbol": "football", "soccer": "football",
                "basket": "basketball", "basquet": "basketball",
                "nba": "basketball", "beisbol": "baseball",
                "mlb": "baseball", "americano": "american-football",
                "nfl": "american-football", "tenis": "tennis", "atp": "tennis",
            }
            sport = sport_map.get(arg.lower(), arg.lower())

    await update.message.reply_text("⏳ Buscando partidos…")
    try:
        events = get_today_schedule(sport, date)
        sport_emoji = {
            "football": "⚽", "basketball": "🏀", "american-football": "🏈",
            "baseball": "⚾", "tennis": "🎾",
        }.get(sport, "🏟️")

        label = f"{date}" if date else "hoy"

        if not events:
            await update.message.reply_text(
                f"{sport_emoji} Sin partidos disponibles para {label}.",
                parse_mode="Markdown",
            )
            return

        scoreboard = format_live_scoreboard(events, max_items=20)
        await update.message.reply_text(
            f"{sport_emoji} *{sport.upper()} — {label}*\n\n{scoreboard}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.exception("Error en /scores %s", sport)
        await update.message.reply_text(f"❌ Error al obtener partidos: {exc}")


async def liveteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /liveteam EQUIPO
    Show a team's last 5 results and next 5 fixtures from live sources
    (SofaScore / TheSportsDB).
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso:\n`/liveteam EQUIPO`\n\nEjemplo:\n`/liveteam Real Madrid`",
            parse_mode="Markdown",
        )
        return

    team_name = " ".join(context.args)
    await update.message.reply_text(f"⏳ Buscando datos de *{team_name}*…", parse_mode="Markdown")

    try:
        # Fetch live form
        form = get_team_live_form(team_name, "football")
        fixtures = get_next_fixtures(team_name)

        if not form and not fixtures:
            await update.message.reply_text(
                f"📭 No se encontraron datos en vivo para *{team_name}*.\n\n"
                f"Prueba `/stats {team_name}` para estadísticas del historial.",
                parse_mode="Markdown",
            )
            return

        text_parts = [f"📡 *{team_name}* — datos en vivo\n"]

        if form and form.get("matches"):
            source = form.get("source", "?").capitalize()
            avg_scored   = form.get("attack", 0)
            avg_conceded = form.get("defense", 0)
            last5 = form.get("last5", "?????")
            text_parts.append(
                f"📈 *Últimos resultados* _{source}_\n"
                f"  Forma: `{last5}` | Prom. `{avg_scored}` goles / `{avg_conceded}` concedidos"
            )
            results_str = format_last_results(form["matches"], team_name)
            text_parts.append(results_str)
            text_parts.append("")

        if fixtures:
            text_parts.append("📅 *Próximos partidos*")
            text_parts.append(format_fixture_list(fixtures))

        await update.message.reply_text(
            "\n".join(text_parts), parse_mode="Markdown"
        )
    except Exception as exc:
        logger.exception("Error en /liveteam %s", team_name)
        await update.message.reply_text(f"❌ Error: {exc}")


async def tabla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /tabla LIGA
    Show the league standings from TheSportsDB / SofaScore.
    Supported: Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Liga MX
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso:\n`/tabla LIGA`\n\n"
            "Ligas disponibles:\n"
            "  Premier League, La Liga, Bundesliga,\n"
            "  Serie A, Ligue 1, Liga MX, Champions League",
            parse_mode="Markdown",
        )
        return

    league_name = " ".join(context.args).title()
    # Normalise common abbreviations
    aliases = {
        "Epl": "Premier League",
        "Pl": "Premier League",
        "Premier": "Premier League",
        "Liga": "La Liga",
        "Laliga": "La Liga",
        "Buli": "Bundesliga",
        "Seriea": "Serie A",
        "Ligue": "Ligue 1",
        "Ligue1": "Ligue 1",
        "Ligamx": "Liga MX",
        "Mx": "Liga MX",
        "Ucl": "Champions League",
        "Champions": "Champions League",
    }
    league_name = aliases.get(league_name.replace(" ", ""), league_name)

    await update.message.reply_text(f"⏳ Cargando tabla de *{league_name}*…", parse_mode="Markdown")

    try:
        table = get_league_table(league_name)

        if not table:
            await update.message.reply_text(
                f"📭 No se encontró la tabla de *{league_name}*.\n\n"
                f"Usa `/tabla Premier League`, `/tabla La Liga`, etc.",
                parse_mode="Markdown",
            )
            return

        rows = table[:20]  # top 20
        header = f"🏆 *{league_name}*\n\n"
        header += "`Pos  Equipo              PJ  G  E  P  GF GA Pts`\n"
        lines = []
        for r in rows:
            pos  = str(r.get("position", "?")).rjust(2)
            team = r.get("team", "?")[:18].ljust(18)
            pj   = str(r.get("played", 0)).rjust(2)
            w    = str(r.get("wins", 0)).rjust(2)
            d    = str(r.get("draws", 0)).rjust(2)
            l    = str(r.get("losses", 0)).rjust(2)
            gf   = str(r.get("goals_for", 0)).rjust(3)
            ga   = str(r.get("goals_against", 0)).rjust(3)
            pts  = str(r.get("points", 0)).rjust(3)
            lines.append(f"`{pos}. {team} {pj} {w} {d} {l} {gf} {ga} {pts}`")

        await update.message.reply_text(
            header + "\n".join(lines), parse_mode="Markdown"
        )
    except Exception as exc:
        logger.exception("Error en /tabla %s", league_name)
        await update.message.reply_text(f"❌ Error al obtener la tabla: {exc}")


# ===============================
# 🚀 MAIN
# ===============================


async def _predict_one(m: dict, semaphore: "_asyncio.Semaphore") -> dict | None:
    """
    Run a single match prediction in a thread-pool executor with a per-match
    timeout.  Wrapped in a semaphore to cap concurrent ESPN/API calls.
    """
    async with semaphore:
        sport  = m.get("sport", "soccer").lower()
        home   = m["home"]
        away   = m["away"]
        league = m.get("league", "default")

        def _sync_predict():
            if sport == "nba":
                return _bball.predict_game(home, away)
            if sport == "nfl":
                return _nfl.predict_game(home, away)
            if sport == "mlb":
                return _baseball.predict_game(home, away)
            return get_full_prediction(home, away, league=league, fetch_live=True)

        loop = _asyncio.get_event_loop()
        try:
            pred = await _asyncio.wait_for(
                loop.run_in_executor(None, _sync_predict),
                timeout=15.0,
            )
            pred.setdefault("league", league)
            return pred
        except _asyncio.TimeoutError:
            logger.warning("Parlay: timeout %s vs %s (%s)", home, away, sport)
        except Exception as exc:
            logger.warning("Parlay: skip %s vs %s (%s): %s", home, away, sport, exc)
        return None


async def parlay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate parlay recommendations from today's matches (Soccer + NBA/NFL/MLB)."""
    await update.message.reply_text(
        "🎰 Generando parlays del día…",
        parse_mode="Markdown",
    )

    # ── Refresh soccer CSV if stale ────────────────────────────────────────
    _refresh_matches_if_stale()

    # ── Load today's matches across all supported sports ──────────────────
    matches = load_today_matches_multisport()
    if not matches:
        await update.message.reply_text(
            "❌ No hay partidos cargados para hoy.\nUsa `/today` para verificar.",
            parse_mode="Markdown",
        )
        return

    # ── Run predictions concurrently with per-match 15 s timeout ─────────
    semaphore = _asyncio.Semaphore(5)   # max 5 concurrent ESPN/API calls
    preds = await _asyncio.gather(
        *[_predict_one(m, semaphore) for m in matches],
        return_exceptions=False,
    )
    predictions = [p for p in preds if p is not None]

    if not predictions:
        await update.message.reply_text(
            "❌ No se pudieron generar predicciones para los partidos de hoy."
        )
        return

    # ── Build parlays: only ALTA confidence, ≥ 68 % probability ──────────
    from core.parlay import (
        generate_parlay_legs, build_parlays, format_parlay,
        MIN_CONF_DEFAULT, MIN_PROB_DEFAULT, _md_escape,
    )
    from core.parlay_history import save_parlay as _save_parlay, get_calibration_stats

    # Pre-load calibration stats once so generate_parlay_legs doesn't query DB per leg
    try:
        cal_stats = get_calibration_stats()
    except Exception:
        cal_stats = {}

    legs, report, excluded = generate_parlay_legs(
        predictions,
        min_confidence=MIN_CONF_DEFAULT,
        min_prob=MIN_PROB_DEFAULT,
        cal_stats=cal_stats,
    )

    if len(legs) < 2:
        total = report.get("total_candidates", len(predictions))
        excl  = report.get("exclusions", {})
        excl_str = ", ".join(
            f"{k}={v}" for k, v in sorted(excl.items(), key=lambda x: -x[1])
        ) if excl else "ninguno"

        # Show top near-miss picks so the user understands the quality bar
        near_misses = sorted(
            [e for e in excluded if e.get("p_best_raw", 0) >= 60],
            key=lambda x: x.get("p_best_raw", 0),
            reverse=True,
        )[:3]
        near_miss_lines = []
        for nm in near_misses:
            reasons_short = ", ".join(nm.get("reasons", [])[:2])
            near_miss_lines.append(
                f"  • {_md_escape(nm.get('event_name', 'Partido'))} "
                f"({nm.get('p_best_raw', 0):.0f}%) — _{reasons_short}_"
            )
        near_miss_text = (
            "\n\n*Picks casi incluidos:*\n" + "\n".join(near_miss_lines)
            if near_miss_lines else ""
        )

        await update.message.reply_text(
            "⚠️ No hay suficientes picks de alta confianza para armar un parlay hoy.\n"
            f"_(Analizados: {total} | Excluidos por: {excl_str})_"
            f"{near_miss_text}",
            parse_mode="Markdown",
        )
        return

    parlays = build_parlays(legs)

    # ── Save to history (use the most ambitious tier's legs & prob) ───────
    try:
        # Determine the "main" tier to save: prefer balanced, else safe
        for tier_key in ("balanced", "safe", "risky"):
            tier_data = parlays.get(tier_key)
            if tier_data:
                parlay_id = _save_parlay(
                    tier_data["legs"],
                    tier_key,
                    tier_data["combined_prob"],
                )
                break
        else:
            parlay_id = ""
    except Exception as exc:
        logger.warning("parlay_command: could not save to history: %s", exc)
        parlay_id = ""

    text = format_parlay(
        parlays,
        parlay_id=parlay_id,
        report=report,
    )
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as exc:
        logger.warning("parlay_command: Markdown parse failed (%s), retrying plain", exc)
        await update.message.reply_text(text)


async def parlay_safe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /parlay_safe

    Generate a maximum hit-rate parlay (2-3 legs) using conservative filters:
      * Moneyline / 1X2 markets only
      * Clarity criterion: p_best ≥ 62 % and separation ≥ 12 pp
      * Stricter risk threshold (0.25 vs 0.35 for default)
      * No variety cap — best global picks win regardless of sport
      * Calibration clamped to [50 %, 90 %]
    """
    await update.message.reply_text(
        "🎯 Generando Parlay Safe (máximo hit rate)…",
        parse_mode="Markdown",
    )

    # ── Refresh soccer CSV if stale ────────────────────────────────────────
    _refresh_matches_if_stale()

    # ── Load today's matches across all supported sports ──────────────────
    matches = load_today_matches_multisport()
    if not matches:
        await update.message.reply_text(
            "❌ No hay partidos cargados para hoy.\nUsa `/today` para verificar.",
            parse_mode="Markdown",
        )
        return

    # ── Run predictions concurrently with per-match 15 s timeout ─────────
    semaphore = _asyncio.Semaphore(5)
    preds = await _asyncio.gather(
        *[_predict_one(m, semaphore) for m in matches],
        return_exceptions=False,
    )
    predictions = [p for p in preds if p is not None]

    if not predictions:
        await update.message.reply_text(
            "❌ No se pudieron generar predicciones para los partidos de hoy."
        )
        return

    from core.parlay import generate_parlay_legs, format_parlay_safe
    from core.parlay_history import save_parlay as _save_parlay, get_calibration_stats

    # Pre-load calibration stats once
    try:
        cal_stats = get_calibration_stats()
    except Exception:
        cal_stats = {}

    legs, report, _excluded = generate_parlay_legs(
        predictions,
        max_legs=3,
        min_confidence="ALTA",
        min_prob=62.0,
        safe_mode=True,
        cal_stats=cal_stats,
    )

    # ── Save to history ────────────────────────────────────────────────────
    parlay_id = ""
    if legs:
        try:
            combined = 1.0
            for leg in legs:
                combined *= leg["prob"] / 100.0
            parlay_id = _save_parlay(legs, "safe", round(combined * 100, 1))
        except Exception as exc:
            logger.warning("parlay_safe_command: could not save to history: %s", exc)

    text = format_parlay_safe(legs, report, parlay_id=parlay_id)
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as exc:
        logger.warning("parlay_safe_command: Markdown parse failed (%s), retrying plain", exc)
        await update.message.reply_text(text)


async def parlay_dream_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /parlay_dream  (alias: /parlay_sonador)

    Generate a high-risk, high-reward "dream parlay" that covers multiple
    correlated picks per match across all available sports.  Each bundle tells
    a coherent story — lower probability but more exciting.
    """
    await update.message.reply_text(
        "🌙 Generando Parlay Soñador…",
        parse_mode="Markdown",
    )

    # ── Refresh soccer CSV if stale ────────────────────────────────────────
    _refresh_matches_if_stale()

    # ── Load today's matches; ESPN may already include tomorrow's games ──────
    matches = load_today_matches_multisport()
    if not matches:
        await update.message.reply_text(
            "❌ No hay partidos disponibles para hoy.\nUsa `/today` para verificar o intenta más tarde.",
            parse_mode="Markdown",
        )
        return

    # ── Run predictions concurrently with per-match 15 s timeout ─────────
    semaphore = _asyncio.Semaphore(5)
    preds = await _asyncio.gather(
        *[_predict_one(m, semaphore) for m in matches],
        return_exceptions=False,
    )
    predictions = [p for p in preds if p is not None]

    if not predictions:
        await update.message.reply_text(
            "❌ No se pudieron generar predicciones para los partidos de hoy."
        )
        return

    from core.parlay import generate_dream_parlay, format_parlay_dream
    from core.parlay_history import save_parlay as _save_parlay

    bundles = generate_dream_parlay(predictions, max_bundles=4)

    # ── Save to history ────────────────────────────────────────────────────
    parlay_id = ""
    if bundles:
        try:
            all_legs = [leg for b in bundles for leg in b["legs"]]
            # Annotate legs with match info for history storage
            for bundle in bundles:
                for leg in bundle["legs"]:
                    leg.setdefault("match", bundle["match"])
                    leg.setdefault("sport", bundle["sport"])
                    leg.setdefault("sport_emoji", bundle["sport_emoji"])
            combined = 1.0
            for b in bundles:
                combined *= b["bundle_prob"] / 100.0
            parlay_id = _save_parlay(all_legs, "dream", round(combined * 100, 1))
        except Exception as exc:
            logger.warning("parlay_dream_command: could not save to history: %s", exc)

    text = format_parlay_dream(bundles, parlay_id=parlay_id)
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as exc:
        logger.warning("parlay_dream_command: Markdown parse failed (%s), retrying plain", exc)
        await update.message.reply_text(text)


# ── Parlay photo / text analyzer ─────────────────────────────────────────────


async def photo_parlay_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photos sent to the bot as parlay ticket images.

    The bot reads the photo's *caption* and parses it as parlay legs text.
    If no caption is provided, usage instructions are returned.

    Caption format (one leg per line):
        Burnley vs Bournemouth | Over 2.5 | @1.75
        Lakers vs Warriors | Moneyline | @2.10
        Real Madrid vs Barcelona | Victoria Real Madrid | 1.45
    """
    caption = (update.message.caption or "").strip()

    if not caption:
        from core.parlay_analyzer import USAGE_TEXT
        await update.message.reply_text(USAGE_TEXT, parse_mode="MarkdownV2")
        return

    await update.message.reply_text("🔍 Analizando tu parlay…", parse_mode="Markdown")

    try:
        from core.parlay_analyzer import (
            parse_parlay_text,
            analyze_parlay,
            format_parlay_analysis,
            USAGE_TEXT,
        )
        legs = parse_parlay_text(caption)
        if not legs:
            await update.message.reply_text(
                "❌ No pude detectar patas de parlay en el caption.\n\n"
                + USAGE_TEXT,
                parse_mode="Markdown",
            )
            return

        analysis = analyze_parlay(legs)
        await update.message.reply_text(
            format_parlay_analysis(analysis), parse_mode="Markdown"
        )
    except Exception as exc:
        logger.exception("Error en análisis de parlay (foto)")
        await update.message.reply_text(f"❌ Error al analizar el parlay: {exc}")


async def checkparlay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /checkparlay <parlay legs>

    Analyze a parlay described in text. Separate legs with newlines or
    semicolons. Include decimal or American odds for implied-probability
    calculation; if omitted the prediction model is used as a fallback.

    Examples
    --------
    /checkparlay Burnley vs Bournemouth Over 2.5 @1.75; Lakers vs Warriors @2.10
    /checkparlay Real Madrid vs Barcelona | Victoria Real Madrid | 1.45
    """
    if not context.args:
        from core.parlay_analyzer import USAGE_TEXT
        await update.message.reply_text(
            "❌ Uso: `/checkparlay <patas del parlay>`\n\n" + USAGE_TEXT,
            parse_mode="Markdown",
        )
        return

    raw_text = " ".join(context.args)
    await update.message.reply_text("🔍 Analizando tu parlay…", parse_mode="Markdown")

    try:
        from core.parlay_analyzer import (
            parse_parlay_text,
            analyze_parlay,
            format_parlay_analysis,
            USAGE_TEXT,
        )
        legs = parse_parlay_text(raw_text)
        if not legs:
            await update.message.reply_text(
                "❌ No pude parsear las patas del parlay.\n\n" + USAGE_TEXT,
                parse_mode="Markdown",
            )
            return

        analysis = analyze_parlay(legs)
        await update.message.reply_text(
            format_parlay_analysis(analysis), parse_mode="Markdown"
        )
    except Exception as exc:
        logger.exception("Error en /checkparlay")
        await update.message.reply_text(f"❌ Error al analizar el parlay: {exc}")


# ── Parlay result recording and history ──────────────────────────────────────


async def resultado_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /resultado [<id>] <WLWWL>

    Record the outcome of each leg in a previously generated parlay.

    - ``W`` = ganó (won)
    - ``L`` = perdió (lost)
    - ``X`` = cancelado / void

    If ``<id>`` is omitted the most recently generated parlay is used.
    Also accepts external platform IDs (e.g. PlayDoIt, Caliente, Bet365).

    Examples
    --------
    /resultado P240315-2 WLW
    /resultado PLAYDOIT-12345 WLW    ← external platform ID
    /resultado BET365-ABC123 LLW     ← external platform ID
    /resultado WWL                   ← uses the last parlay
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: `/resultado [<id>] <WLWWL>`\n\n"
            "Ejemplo interno: `/resultado P240315-2 WLW`\n"
            "Ejemplo externo: `/resultado PLAYDOIT-12345 WLW`\n"
            "O para el último parlay: `/resultado WWL`\n\n"
            "W = Ganó · L = Perdió · X = Cancelado",
            parse_mode="Markdown",
        )
        return

    from core.parlay_history import (
        record_results,
        get_last_parlay_id,
        get_num_legs_for_parlay,
        format_result_confirmation,
        save_external_parlay,
    )

    args = context.args

    # Determine whether the first arg is a parlay ID or a results string.
    # Rule: if the first arg contains any character outside W/L/X it is an ID
    # (handles both internal "P240315-2" and external "PLAYDOIT-12345").
    _RESULT_CHARS = frozenset("WLX")
    first_is_id = len(args) >= 2 and not all(
        c in _RESULT_CHARS for c in args[0].upper()
    )

    if first_is_id:
        parlay_id   = args[0].upper()
        results_str = "".join(args[1:]).upper()
    else:
        # No ID — use last generated parlay
        parlay_id = get_last_parlay_id() or ""
        results_str = "".join(args).upper()

    if not parlay_id:
        await update.message.reply_text(
            "❌ No hay parlays guardados aún. Genera uno con `/parlay` primero.",
            parse_mode="Markdown",
        )
        return

    # Validate result chars
    invalid = [c for c in results_str if c not in ("W", "L", "X")]
    if invalid or not results_str:
        await update.message.reply_text(
            f"❌ Formato inválido: `{results_str}`\n\n"
            "Solo se aceptan W (ganó), L (perdió) y X (cancelado).\n"
            "Ejemplo: `WLW` para 3 patas (1ª ganó, 2ª perdió, 3ª ganó).",
            parse_mode="Markdown",
        )
        return

    # Validate result count vs number of legs in the parlay
    num_legs = get_num_legs_for_parlay(parlay_id)
    if num_legs > 0 and len(results_str) < num_legs:
        await update.message.reply_text(
            f"⚠️ Recibí `{len(results_str)}/{num_legs}` resultado(s) — "
            f"faltan `{num_legs - len(results_str)}` pata(s).\n\n"
            f"Envía los {num_legs} resultados juntos: "
            f"`/resultado {parlay_id} {'W'*num_legs}`\n"
            "_Una letra por pata: W=Ganó, L=Perdió, X=Cancelado_",
            parse_mode="Markdown",
        )
        return

    results_list = list(results_str)
    outcome = record_results(parlay_id, results_list)

    if not outcome["found"]:
        # For external platform IDs: create a lightweight placeholder and try again
        if first_is_id:
            try:
                outcome = save_external_parlay(parlay_id, results_list)
            except Exception as exc:
                logger.warning(
                    "resultado_command: could not save external parlay %s: %s",
                    parlay_id, exc,
                )
        if not outcome["found"]:
            await update.message.reply_text(
                f"❌ Parlay `{parlay_id}` no encontrado.\n"
                "Usa `/historial` para ver los IDs guardados.",
                parse_mode="Markdown",
            )
            return

    await update.message.reply_text(
        format_result_confirmation(outcome), parse_mode="Markdown"
    )


async def historial_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /historial

    Show recent parlay history, win rate, and calibration statistics.
    The calibration data is used automatically by future /parlay calls to
    adjust predicted probabilities toward observed hit rates.
    """
    try:
        from core.parlay_history import (
            get_history,
            get_calibration_stats,
            format_history_summary,
        )
        records   = get_history(limit=15)
        cal_stats = get_calibration_stats()
        text      = format_history_summary(records, cal_stats)
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as exc:
        logger.exception("Error en /historial")
        await update.message.reply_text(f"❌ Error al obtener historial: {exc}")


async def estadisticas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /estadisticas

    Show deep analytics: model calibration by sport, league, and market type,
    plus sparkline trend and probability-bucket hit rates.  The model
    automatically applies these statistics to improve future /parlay estimates.
    """
    try:
        from core.parlay_history import (
            get_calibration_stats,
            get_sport_stats,
            get_league_stats,
            get_trend,
            get_bucket_stats,
            format_estadisticas,
        )
        cal_stats    = get_calibration_stats()
        sport_stats  = get_sport_stats()
        league_stats = get_league_stats()
        trend        = get_trend(n_last=20)
        bucket_stats = get_bucket_stats()
        text         = format_estadisticas(cal_stats, sport_stats, league_stats,
                                           trend, bucket_stats)
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as exc:
        logger.exception("Error en /estadisticas")
        await update.message.reply_text(f"❌ Error al obtener estadísticas: {exc}")


# ===============================
# 🔬 ANALYTICS COMMANDS
# ===============================


async def form_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /form EQUIPO
    Show deep Team Form Engine analysis for a team.
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: `/form EQUIPO`\nEjemplo: `/form América`",
            parse_mode="Markdown",
        )
        return

    team_name = " ".join(context.args)
    await update.message.reply_text("⏳ Analizando forma del equipo…")

    try:
        from sports.football import resolve_team, MATCH_HISTORY, HOME_STATS, AWAY_STATS
        resolved = resolve_team(team_name)
        if not resolved:
            suggestions = suggest_teams(team_name)
            tip = ""
            if suggestions:
                tip = "\n\n¿Quisiste decir?\n" + "\n".join(f"  • {t}" for t in suggestions)
            await update.message.reply_text(f"❌ Equipo '{team_name}' no encontrado.{tip}")
            return

        from core.form_engine import analyze_team_form, format_form_report
        history  = MATCH_HISTORY.get(resolved, [])
        all_form  = analyze_team_form(history, last_n=10)
        home_form = analyze_team_form(history, home_only=True, last_n=8)
        away_form = analyze_team_form(history, away_only=True, last_n=8)

        home_report = format_form_report(resolved, all_form, home_form, is_home=True)
        away_report = format_form_report(resolved, all_form, away_form, is_home=False)

        text = (
            "╔══════════════════════════════════╗\n"
            f"  📊 FORM ENGINE — {resolved}\n"
            "╚══════════════════════════════════╝\n\n"
            f"{home_report}\n\n"
            f"{away_report}"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as e:
        logger.exception("Error en /form %s", team_name)
        await update.message.reply_text(f"❌ Error: {e}")


async def markets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /markets LOCAL vs VISITANTE
    Show the full market model: AH, O/U, HT/FT, DNB, CS, team totals.
    """
    if not context.args or " vs " not in " ".join(context.args).lower():
        await update.message.reply_text(
            "❌ Uso: `/markets LOCAL vs VISITANTE`\n"
            "Ejemplo: `/markets Real Madrid vs Barcelona`",
            parse_mode="Markdown",
        )
        return

    raw_text = " ".join(context.args)
    home_raw, away_raw = raw_text.split(" vs ", 1)
    home = home_raw.strip()
    away = away_raw.strip()

    await update.message.reply_text("⏳ Calculando modelos de mercado…")

    try:
        prediction = get_full_prediction(home, away)
        from core.market_models import full_market_model, format_market_model
        market = full_market_model(
            prediction["xg_home"], prediction["xg_away"], prediction
        )
        text = format_market_model(prediction, market)
        await update.message.reply_text(text, parse_mode="Markdown")

    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        logger.exception("Error en /markets %s vs %s", home, away)
        await update.message.reply_text(f"❌ Error: {e}")


async def intel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /intel LOCAL vs VISITANTE
    Show Game Intelligence: situational analysis, streaks, trap detection.
    """
    if not context.args or " vs " not in " ".join(context.args).lower():
        await update.message.reply_text(
            "❌ Uso: `/intel LOCAL vs VISITANTE`\n"
            "Ejemplo: `/intel Barcelona vs Real Madrid`",
            parse_mode="Markdown",
        )
        return

    raw_text = " ".join(context.args)
    home_raw, away_raw = raw_text.split(" vs ", 1)
    home = home_raw.strip()
    away = away_raw.strip()

    await update.message.reply_text("⏳ Analizando inteligencia del partido…")

    try:
        prediction = get_full_prediction(home, away)

        from sports.football import MATCH_HISTORY, H2H_DATA, resolve_team
        home_r = resolve_team(home) or home
        away_r = resolve_team(away) or away

        home_history = MATCH_HISTORY.get(home_r, [])
        away_history = MATCH_HISTORY.get(away_r, [])
        h2h_records  = H2H_DATA.get((home_r, away_r), [])

        from core.game_intelligence import analyze_game_intelligence, format_game_intelligence
        intel = analyze_game_intelligence(prediction, home_history, away_history, h2h_records)
        text  = format_game_intelligence(prediction, intel)
        await update.message.reply_text(text, parse_mode="Markdown")

    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        logger.exception("Error en /intel %s vs %s", home, away)
        await update.message.reply_text(f"❌ Error: {e}")


# ===============================
# 🔍 MARKET ERROR SCANNER COMMANDS
# ===============================


def _parse_scanodds_args(raw: str):
    """
    Parse the /scanodds and /addmarket argument string.

    Accepted formats (pipe-separated):
      EVENTO | MERCADO | cuota@casa...
      DEPORTE | EVENTO | MERCADO | cuota@casa...
      DEPORTE | EVENTO | MERCADO | JUGADOR | cuota@casa...

    Returns (sport, event, market, player, odds_str) or raises ValueError.
    """
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        raise ValueError(
            "Formato incorrecto. Usa:\n"
            "`EVENTO | MERCADO | cuota@casa cuota@casa...`\n"
            "o: `DEPORTE | EVENTO | MERCADO | cuota@casa...`"
        )

    # Detect if last part contains "@" (odds tokens)
    # Walk back from end to find where odds_str begins
    odds_str = parts[-1]
    if "@" not in odds_str:
        raise ValueError("No se encontraron cuotas. Formato: `1.90@Bet365 2.50@Caliente`")

    remaining = parts[:-1]

    if len(remaining) == 2:
        sport, event, market, player = "General", remaining[0], remaining[1], ""
    elif len(remaining) == 3:
        sport, event, market, player = remaining[0], remaining[1], remaining[2], ""
    else:  # 4+
        sport, event, market, player = remaining[0], remaining[1], remaining[2], remaining[3]

    return sport, event, market, player, odds_str


async def scanodds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /scanodds EVENTO | MERCADO | cuota@casa cuota@casa...
    /scanodds DEPORTE | EVENTO | MERCADO | cuota@casa...

    Instantly scan for market errors in any sport/market. Also saves the
    market to tracked list for future automatic scanning.

    Examples:
      /scanodds Real Madrid vs Barça | Victoria Madrid | 1.90@Bet365 2.50@Caliente 1.85@Codere
      /scanodds NBA | Lakers vs Warriors | LeBron Pts +25.5 | 1.85@Betway 2.20@Caliente
      /scanodds UFC | Jones vs Miocic | Jones Gana | 1.35@Bet365 1.60@Caliente 1.40@Betcris
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso:\n"
            "`/scanodds EVENTO | MERCADO | cuota@casa cuota@casa...`\n\n"
            "Ejemplos:\n"
            "`/scanodds Madrid vs Barça | Victoria Madrid | 1.90@Bet365 2.50@Caliente 1.85@Codere`\n"
            "`/scanodds NBA | Lakers vs Warriors | LeBron Pts +25.5 | 1.85@Betway 2.20@Caliente`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)

    try:
        sport, event, market, player, odds_str = _parse_scanodds_args(raw)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}", parse_mode="Markdown")
        return

    from core.market_scanner import parse_odds_input, MarketScan, scan_market, format_alert, format_scan_summary
    odds_list = parse_odds_input(odds_str)

    if len(odds_list) < 2:
        await update.message.reply_text(
            "❌ Se necesitan al menos 2 cuotas de casas diferentes.\n"
            "Formato: `1.90@Bet365 2.50@Caliente 1.85@Codere`",
            parse_mode="Markdown",
        )
        return

    scan   = MarketScan(sport=sport, event=event, market=market, player=player, odds_list=odds_list)
    alerts = scan_market(scan)

    # Auto-save to tracked markets
    try:
        from api.odds_feed import add_market
        add_market(sport, event, market, odds_list, player)
    except Exception as _e:
        logger.debug("Could not save to tracked markets: %s", _e)

    if not alerts:
        player_str = f" ({player})" if player else ""
        avg_str = f"{sum(b.odds for b in odds_list)/len(odds_list):.2f}" if odds_list else "?"
        await update.message.reply_text(
            f"✅ *Sin errores detectados*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 {event}{player_str} — {market}\n"
            f"📉 Cuota promedio: `{avg_str}`\n"
            f"_Todas las cuotas dentro del rango normal._\n\n"
            f"💾 _Mercado guardado para seguimiento automático._",
            parse_mode="Markdown",
        )
        return

    # Send individual alert for each outlier found
    for alert in alerts:
        try:
            await update.message.reply_text(format_alert(alert), parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(format_alert(alert))

    await update.message.reply_text(
        f"💾 _Mercado guardado para seguimiento automático (c/15 min)._",
        parse_mode="Markdown",
    )


async def scanner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /scanner [DEPORTE]
    Scan all tracked markets for errors. Optionally filter by sport.
    """
    sport_filter = " ".join(context.args).strip().lower() if context.args else ""

    await update.message.reply_text("🔍 Escaneando mercados…", parse_mode="Markdown")

    try:
        from api.odds_feed import get_tracked_markets, list_markets_text
        from core.market_scanner import scan_multiple_markets, format_scan_summary

        scans = get_tracked_markets()
        if not scans:
            await update.message.reply_text(
                "📭 No hay mercados en seguimiento.\n\n"
                "Usa `/scanodds EVENTO | MERCADO | cuota@casa...` para agregar uno.",
                parse_mode="Markdown",
            )
            return

        if sport_filter:
            scans = [s for s in scans if sport_filter in s.sport.lower()]
            if not scans:
                await update.message.reply_text(
                    f"📭 No hay mercados de *{sport_filter}* en seguimiento.",
                    parse_mode="Markdown",
                )
                return

        alerts = scan_multiple_markets(scans)
        summary = format_scan_summary(alerts, scanned=len(scans))
        await update.message.reply_text(summary, parse_mode="Markdown")

        # Send individual alerts for errors + high value
        critical = [a for a in alerts if a.classification in ("MARKET_ERROR", "HIGH_VALUE")]
        for alert in critical[:5]:
            from core.market_scanner import format_alert
            try:
                await update.message.reply_text(format_alert(alert), parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(format_alert(alert))

    except Exception as e:
        logger.exception("Error en /scanner")
        await update.message.reply_text(f"❌ Error al escanear: {e}")


async def addmarket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /addmarket DEPORTE | EVENTO | MERCADO | cuota@casa...

    Add a market to the tracked list (no immediate scan).
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso:\n"
            "`/addmarket DEPORTE | EVENTO | MERCADO | cuota@casa...`\n\n"
            "Ejemplo:\n"
            "`/addmarket Fútbol | Madrid vs PSG | Mbappé Tiros +1.5 | 1.80@Bet365 2.30@Caliente`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    try:
        sport, event, market, player, odds_str = _parse_scanodds_args(raw)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}", parse_mode="Markdown")
        return

    from core.market_scanner import parse_odds_input
    odds_list = parse_odds_input(odds_str)
    if len(odds_list) < 2:
        await update.message.reply_text(
            "❌ Se necesitan al menos 2 cuotas.", parse_mode="Markdown"
        )
        return

    try:
        from api.odds_feed import add_market, market_count
        add_market(sport, event, market, odds_list, player)
        total = market_count()
        player_str = f" ({player})" if player else ""
        await update.message.reply_text(
            f"✅ *Mercado agregado al seguimiento*\n"
            f"  {sport} — {event}{player_str}\n"
            f"  {market} | {len(odds_list)} casas\n\n"
            f"📋 Total en seguimiento: `{total}`\n"
            f"_Auto-escaneado cada 15 minutos._",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("Error en /addmarket")
        await update.message.reply_text(f"❌ Error: {e}")


async def clearmarkets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /clearmarkets
    Remove all tracked markets.
    """
    try:
        from api.odds_feed import clear_markets
        count = clear_markets()
        await update.message.reply_text(
            f"🗑️ Se eliminaron `{count}` mercados del seguimiento.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")



# ===============================
# 🧠 INTELLIGENCE COMMANDS
# ===============================


async def liquidity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /liquidity EVENTO | MERCADO | cuota@casa cuota@casa...

    Assess betting market liquidity and identify line shopping opportunities.

    Example:
      /liquidity Madrid vs Barça | Victoria Madrid | 1.90@Bet365 2.10@Caliente 1.88@Codere
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso:\n`/liquidity EVENTO | MERCADO | cuota@casa...`\n\n"
            "Ejemplo:\n`/liquidity Madrid vs Barça | Victoria Madrid | 1.90@Bet365 2.10@Caliente 1.88@Codere`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    try:
        from core.market_scanner import parse_odds_input
        # Parse: EVENTO | MERCADO | cuotas
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 3:
            raise ValueError("Necesitas al menos: EVENTO | MERCADO | cuotas")
        event, market, odds_str = parts[0], parts[1], parts[-1]

        odds_raw = parse_odds_input(odds_str)
        if len(odds_raw) < 2:
            raise ValueError("Se necesitan al menos 2 casas de apuestas")

        from core.liquidity_detector import BookmakerLine, assess_market_liquidity, format_liquidity_report
        lines = [BookmakerLine(b.bookmaker, b.odds) for b in odds_raw]
        report = assess_market_liquidity(market, lines)
        text   = format_liquidity_report({market: report}, event=event)
        await update.message.reply_text(text, parse_mode="Markdown")

    except ValueError as e:
        await update.message.reply_text(f"❌ {e}", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error en /liquidity")
        await update.message.reply_text(f"❌ Error: {e}")


async def steam_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /steam EVENTO | MERCADO | ABIERTA@CASA:ACTUAL ABIERTA@CASA:ACTUAL...

    Detect steam moves (sharp money) from opening vs current odds.

    Format: open_odds@bookmaker:current_odds
    Example:
      /steam Madrid vs Barça | Victoria Madrid | 2.10@Bet365:1.82 2.05@Caliente:1.80 2.12@Codere:1.84
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso:\n"
            "`/steam EVENTO | MERCADO | abierta@casa:actual abierta@casa:actual`\n\n"
            "Ejemplo:\n"
            "`/steam Madrid vs Barça | Victoria Madrid | 2.10@Bet365:1.82 2.05@Caliente:1.80`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    try:
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 3:
            raise ValueError("Formato: EVENTO | MERCADO | abierta@casa:actual ...")
        event, market, snap_str = parts[0], parts[1], parts[-1]

        from core.steam_detector import OddsSnapshot, detect_steam, format_steam_alert, format_steam_summary
        snapshots = []
        for token in snap_str.strip().split():
            if "@" not in token or ":" not in token:
                continue
            left, current_str = token.rsplit(":", 1)
            open_str, bookie  = left.split("@", 1)
            try:
                snapshots.append(OddsSnapshot(bookie, float(open_str), float(current_str)))
            except ValueError:
                continue

        if len(snapshots) < 2:
            raise ValueError("Se necesitan al menos 2 casas con movimiento de cuota")

        alert = detect_steam(market, snapshots, event=event, sport="General")
        if alert:
            await update.message.reply_text(format_steam_alert(alert), parse_mode="Markdown")
        else:
            await update.message.reply_text(
                f"✅ *Sin steam detectado*\n"
                f"  {event} — {market}\n"
                f"_El movimiento no supera el umbral mínimo de steam._",
                parse_mode="Markdown",
            )

    except ValueError as e:
        await update.message.reply_text(f"❌ {e}", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error en /steam")
        await update.message.reply_text(f"❌ Error: {e}")


async def consensus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /consensus EVENTO | H:D:A@Casa1 H:D:A@Casa2 ...

    Build market consensus from multiple bookmakers' 1X2 odds.

    Example:
      /consensus Madrid vs Barça | 1.85:3.50:4.20@Bet365 1.90:3.40:4.10@Caliente 1.87:3.45:4.15@Codere
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso:\n`/consensus EVENTO | H:D:A@Casa H:D:A@Casa...`\n\n"
            "Ejemplo:\n`/consensus Madrid vs Barça | 1.85:3.50:4.20@Bet365 1.90:3.40:4.10@Caliente`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    try:
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 2:
            raise ValueError("Formato: EVENTO | H:D:A@Casa1 H:D:A@Casa2...")
        event, odds_str = parts[0], parts[1]

        from core.market_consensus import BookOdds, build_consensus, format_consensus
        books = []
        for token in odds_str.strip().split():
            if "@" not in token or ":" not in token:
                continue
            odds_part, bookie = token.rsplit("@", 1)
            try:
                odds = [float(x) for x in odds_part.split(":")]
                if len(odds) >= 2:
                    books.append(BookOdds(bookie, odds))
            except ValueError:
                continue

        if len(books) < 2:
            raise ValueError("Se necesitan al menos 2 casas de apuestas (H:D:A@Casa)")

        n_outcomes = len(books[0].odds)
        if n_outcomes == 3:
            labels = ["Local", "Empate", "Visitante"]
        elif n_outcomes == 2:
            labels = ["Local", "Visitante"]
        else:
            labels = [f"Resultado {i+1}" for i in range(n_outcomes)]

        result = build_consensus(books, labels)
        text   = format_consensus(result, event=event)
        await update.message.reply_text(text, parse_mode="Markdown")

    except ValueError as e:
        await update.message.reply_text(f"❌ {e}", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error en /consensus")
        await update.message.reply_text(f"❌ Error: {e}")


async def clv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /clv                              — Show CLV statistics
    /clv log EVENTO | MERCADO | CUOTA — Log a new pick (no closing odds yet)
    /clv update EVENTO | MERCADO | CIERRE — Update closing odds for a pick
    /clv log EVENTO | MERCADO | CUOTA | CIERRE — Log pick with immediate closing

    Example:
      /clv log Madrid vs Barça | Victoria Madrid | 1.92
      /clv update Madrid vs Barça | Victoria Madrid | 1.80
      /clv log Lakers vs Warriors | LeBron Pts +25.5 | 1.85 | 1.75
    """
    from core.clv_tracker import log_pick, update_closing_odds, get_clv_stats, format_clv_stats, format_clv_single

    if not context.args:
        stats = get_clv_stats()
        await update.message.reply_text(format_clv_stats(stats), parse_mode="Markdown")
        return

    sub = context.args[0].lower()
    rest = " ".join(context.args[1:])

    if sub in ("log", "registrar"):
        parts = [p.strip() for p in rest.split("|")]
        if len(parts) < 3:
            await update.message.reply_text(
                "❌ Uso: `/clv log EVENTO | MERCADO | CUOTA [| CIERRE]`",
                parse_mode="Markdown",
            )
            return
        try:
            event   = parts[0]
            market  = parts[1]
            bet_o   = float(parts[2])
            close_o = float(parts[3]) if len(parts) >= 4 else None
            entry   = log_pick(event, market, bet_o, close_o)
            await update.message.reply_text(format_clv_single(entry), parse_mode="Markdown")
        except (ValueError, IndexError):
            await update.message.reply_text("❌ Error: cuota inválida.", parse_mode="Markdown")

    elif sub in ("update", "cierre"):
        parts = [p.strip() for p in rest.split("|")]
        if len(parts) < 3:
            await update.message.reply_text(
                "❌ Uso: `/clv update EVENTO | MERCADO | CUOTA_CIERRE`",
                parse_mode="Markdown",
            )
            return
        try:
            found = update_closing_odds(parts[0], parts[1], float(parts[2]))
            if found:
                await update.message.reply_text(
                    f"✅ CLV actualizado: `{parts[0]}` / {parts[1]}\n  Cuota cierre: `{parts[2]}`",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text("⚠️ Pick no encontrado para actualizar.")
        except (ValueError, IndexError):
            await update.message.reply_text("❌ Error: cuota inválida.", parse_mode="Markdown")

    else:
        stats = get_clv_stats()
        await update.message.reply_text(format_clv_stats(stats), parse_mode="Markdown")


async def risk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /risk PROB CUOTA [BANKROLL]

    Calculate optimal stake using Kelly Criterion + Risk of Ruin.

    Example:
      /risk 0.58 1.85 1000
      /risk 58 1.85           (probability as %)
    """
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ Uso: `/risk PROB CUOTA [BANKROLL]`\n\n"
            "Ejemplo: `/risk 0.58 1.85 1000`",
            parse_mode="Markdown",
        )
        return

    try:
        prob_raw  = float(context.args[0])
        prob      = prob_raw / 100 if prob_raw > 1 else prob_raw
        odds      = float(context.args[1])
        bankroll  = float(context.args[2]) if len(context.args) >= 3 else 1000.0
        market    = " ".join(context.args[3:]) if len(context.args) > 3 else ""

        from core.risk_management import format_stake_advice
        text = format_stake_advice(bankroll, prob, odds, market)
        await update.message.reply_text(text, parse_mode="Markdown")

    except (ValueError, IndexError):
        await update.message.reply_text("❌ Valores inválidos. Ej: `/risk 0.58 1.85 1000`", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error en /risk")
        await update.message.reply_text(f"❌ Error: {e}")


async def player_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /player EQUIPO | Jugador1:status Jugador2:status

    Compute how player absences/presences affect the team's xG.

    Status options: absent · doubt · returning · available

    Examples:
      /player Man City | Haaland:absent DeBruyne:doubt
      /player Real Madrid vs Barça | Vinicius:doubt Bellingham:absent
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: `/player EQUIPO | Jugador:status Jugador:status`\n\n"
            "Ejemplo: `/player Man City | Haaland:absent DeBruyne:doubt`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    try:
        if "|" not in raw:
            raise ValueError("Usa el separador |: EQUIPO | Jugador:status ...")
        team_str, players_str = raw.split("|", 1)
        team_name = team_str.strip()

        from core.player_impact import parse_player_statuses, compute_team_player_impact, format_player_impact
        players = parse_player_statuses(players_str)

        if not players:
            raise ValueError("No se encontraron jugadores. Formato: Jugador:absent")

        # Try to get real xG from football engine
        xg_base = 1.4  # default
        try:
            from sports.football import TEAM_STATS, LEAGUE_AVG, resolve_team
            resolved = resolve_team(team_name)
            if resolved and resolved in TEAM_STATS:
                stats    = TEAM_STATS[resolved]
                xg_base  = stats.get("avg_scored", 1.4)
        except Exception:
            pass

        impact = compute_team_player_impact(xg_base, xg_base * 0.7, players)
        text   = format_player_impact(team_name, xg_base, impact)
        await update.message.reply_text(text, parse_mode="Markdown")

    except ValueError as e:
        await update.message.reply_text(f"❌ {e}", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error en /player")
        await update.message.reply_text(f"❌ Error: {e}")


async def referee_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /referee NOMBRE DEL ÁRBITRO [vs PARTIDO]

    Show referee profile: card rates, penalty rate, style, signals.

    Examples:
      /referee Jesus Gil Manzano
      /referee Anthony Taylor vs Arsenal vs Man City
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: `/referee NOMBRE [vs PARTIDO]`\n\n"
            "Ejemplo: `/referee Jesus Gil Manzano`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    # Split on " vs " for optional event context
    if " vs " in raw.lower():
        parts = raw.split(" vs ", 1)
        referee_name = parts[0].strip()
        event        = " vs ".join(parts)  # show full event
    else:
        referee_name = raw.strip()
        event        = ""

    try:
        from core.referee_model import referee_impact, format_referee_impact
        impact = referee_impact(referee_name, 1.5, 1.0)
        text   = format_referee_impact(impact, event=event)
        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as e:
        logger.exception("Error en /referee")
        await update.message.reply_text(f"❌ Error: {e}")


async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /weather CONDICIÓN TEMP VIENTO LLUVIA CESPED

    Compute weather impact on xG, corners, and cards.

    Conditions: normal · lluvia · heavy_rain · viento · heavy_wind · nieve · calor · frio
    Pitch:      good · wet · heavy · frozen · artificial

    Example:
      /weather lluvia 8 45 5 wet
      /weather normal 22 10 0 good
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: `/weather CONDICIÓN TEMP VIENTO LLUVIA CESPED`\n\n"
            "Ejemplo: `/weather lluvia 8 45 5 wet`\n"
            "Condiciones: normal · lluvia · heavy\\_rain · viento · nieve · calor · frio\n"
            "Césped: good · wet · heavy · frozen · artificial",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    try:
        from core.weather_model import parse_weather_input, compute_weather_impact, format_weather_impact
        cond = parse_weather_input(raw)
        if not cond:
            raise ValueError("No se pudo interpretar las condiciones. Ej: `lluvia 8 45 5 wet`")
        impact = compute_weather_impact(cond)
        text   = format_weather_impact(impact)
        await update.message.reply_text(text, parse_mode="Markdown")

    except ValueError as e:
        await update.message.reply_text(f"❌ {e}", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error en /weather")
        await update.message.reply_text(f"❌ Error: {e}")


async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /portfolio PROB:CUOTA:DEPORTE:MERCADO PROB:CUOTA:DEPORTE:MERCADO ... [bankroll=X]

    Optimise a bet portfolio using Kelly criterion across multiple legs.

    Example:
      /portfolio 0.58:1.85:Fútbol:HomeWin 0.61:1.72:NBA:Over25 0.55:1.90:NFL:Spread bankroll=1000
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso:\n`/portfolio PROB:CUOTA:DEPORTE:MERCADO ... [bankroll=X]`\n\n"
            "Ejemplo:\n`/portfolio 0.58:1.85:Fútbol:HomeWin 0.61:1.72:NBA:Over25 bankroll=1000`",
            parse_mode="Markdown",
        )
        return

    raw      = " ".join(context.args)
    bankroll = 1000.0

    # Extract bankroll= parameter
    import re
    br_match = re.search(r"bankroll=(\d+(?:\.\d+)?)", raw)
    if br_match:
        bankroll = float(br_match.group(1))
        raw      = raw.replace(br_match.group(0), "").strip()

    try:
        from core.portfolio_optimizer import Bet, optimize_portfolio, format_portfolio
        bets = []
        for token in raw.split():
            if not token:
                continue
            parts = token.split(":")
            if len(parts) < 2:
                continue
            try:
                prob   = float(parts[0])
                prob   = prob / 100 if prob > 1 else prob
                odds   = float(parts[1])
                sport  = parts[2] if len(parts) > 2 else "General"
                market = parts[3] if len(parts) > 3 else "Apuesta"
                bets.append(Bet(market=market, sport=sport, prob=prob, odds=odds))
            except (ValueError, IndexError):
                continue

        if not bets:
            raise ValueError("No se encontraron apuestas válidas. Formato: 0.58:1.85:Fútbol:HomeWin")

        portfolio = optimize_portfolio(bets, bankroll)
        if not portfolio.bets:
            await update.message.reply_text(
                "⚠️ Ninguna apuesta tiene valor positivo (EV > 0).\n"
                "Revisa tus probabilidades y cuotas.",
                parse_mode="Markdown",
            )
            return

        text = format_portfolio(portfolio, bankroll)
        await update.message.reply_text(text, parse_mode="Markdown")

    except ValueError as e:
        await update.message.reply_text(f"❌ {e}", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error en /portfolio")
        await update.message.reply_text(f"❌ Error: {e}")


async def bayes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /bayes LOCAL vs VISITANTE [opciones]

    Apply Bayesian update to match probabilities given additional evidence.
    Evidence flags (append after team names):
      strict         — árbitro estricto
      permissive     — árbitro permisivo
      rain           — lluvia (weather_xg_mult=0.90)
      heavy_rain     — lluvia fuerte (weather_xg_mult=0.82)
      home_inj=X     — % baja xG local (0-30)
      away_inj=X     — % baja xG visitante
      home_form=X    — boost forma local (-3 a +3)
      away_form=X    — boost forma visitante

    Example:
      /bayes Real Madrid vs Barcelona strict home_inj=15 rain
    """
    if not context.args or " vs " not in " ".join(context.args).lower():
        await update.message.reply_text(
            "❌ Uso: `/bayes LOCAL vs VISITANTE [opciones]`\n\n"
            "Opciones: `strict` · `permissive` · `rain` · `heavy_rain`\n"
            "  `home_inj=15` · `away_inj=10` · `home_form=1` · `away_form=-1`\n\n"
            "Ejemplo: `/bayes Real Madrid vs Barcelona strict home_inj=15`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    try:
        # Split off " vs " to get teams
        vs_idx   = raw.lower().find(" vs ")
        home_str = raw[:vs_idx].strip()
        rest     = raw[vs_idx + 4:]
        # Everything after the away team (first word group before flags)
        tokens   = rest.split()
        # Parse away team (everything until first flag-like token)
        away_tokens = []
        flag_tokens = []
        for t in tokens:
            if ("=" in t or t.lower() in ("strict","permissive","rain","heavy_rain","snow","wind")):
                flag_tokens.append(t)
            else:
                if not flag_tokens:
                    away_tokens.append(t)
                else:
                    flag_tokens.append(t)
        away_str = " ".join(away_tokens).strip()

        # Get base probabilities from football model
        await update.message.reply_text("⏳ Calculando probabilidades base…")
        try:
            base_pred = get_full_prediction(home_str, away_str)
            ph = base_pred["home_win"] / 100
            pd = base_pred["draw"]     / 100
            pa = base_pred["away_win"] / 100
            elo_diff = base_pred.get("home_elo", 1500) - base_pred.get("away_elo", 1500)
        except Exception:
            ph, pd, pa = 0.45, 0.27, 0.28
            elo_diff = 0

        # Parse evidence flags
        import re as _re
        evidence = {"elo_diff": elo_diff}
        evidence_labels = []

        for t in flag_tokens:
            tl = t.lower()
            if tl == "strict":
                evidence["referee_strict"] = True
                evidence_labels.append("Árbitro estricto")
            elif tl == "permissive":
                evidence["referee_permissive"] = True
                evidence_labels.append("Árbitro permisivo")
            elif tl == "rain":
                evidence["weather_xg_mult"] = 0.90
                evidence_labels.append("Lluvia (xG -10%)")
            elif tl == "heavy_rain":
                evidence["weather_xg_mult"] = 0.82
                evidence_labels.append("Lluvia fuerte (xG -18%)")
            elif tl == "snow":
                evidence["weather_xg_mult"] = 0.78
                evidence_labels.append("Nieve (xG -22%)")
            elif tl == "wind":
                evidence["weather_xg_mult"] = 0.91
                evidence_labels.append("Viento (xG -9%)")
            else:
                m = _re.match(r"home_inj=(\d+(?:\.\d+)?)", tl)
                if m:
                    evidence["home_injury_pct"] = float(m.group(1))
                    evidence_labels.append(f"Bajas local {m.group(1)}%")
                m = _re.match(r"away_inj=(\d+(?:\.\d+)?)", tl)
                if m:
                    evidence["away_injury_pct"] = float(m.group(1))
                    evidence_labels.append(f"Bajas visitante {m.group(1)}%")
                m = _re.match(r"home_form=(-?\d+(?:\.\d+)?)", tl)
                if m:
                    evidence["home_form_boost"] = float(m.group(1))
                    evidence_labels.append(f"Forma local {m.group(1)}")
                m = _re.match(r"away_form=(-?\d+(?:\.\d+)?)", tl)
                if m:
                    evidence["away_form_boost"] = float(m.group(1))
                    evidence_labels.append(f"Forma visitante {m.group(1)}")

        from core.bayesian_update import BayesianUpdater, format_bayesian_update
        bu   = BayesianUpdater(ph, pd, pa)
        bu.update_evidence(evidence)
        post = bu.posterior()

        text = format_bayesian_update(
            prior           = (ph * 100, pd * 100, pa * 100),
            posterior       = post,
            event           = f"{home_str} vs {away_str}",
            evidence_labels = evidence_labels,
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as e:
        logger.exception("Error en /bayes")
        await update.message.reply_text(f"❌ Error: {e}")


async def rl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /rl CONFIANZA EV_PCT DRAWDOWN_PCT [BANKROLL]

    Get RL (Q-Learning) agent's recommended stake for a betting situation.

    Arguments:
      CONFIANZA   : ALTA | MEDIA | BAJA
      EV_PCT      : expected value percentage (e.g. 5.2)
      DRAWDOWN_PCT: current drawdown % (e.g. 3.0)
      BANKROLL    : optional bankroll amount (default 1000)

    Example:
      /rl ALTA 5.2 3.0 1000
    """
    if not context.args or len(context.args) < 3:
        await update.message.reply_text(
            "❌ Uso: `/rl CONFIANZA EV DRAWDOWN [BANKROLL]`\n\n"
            "Ejemplo: `/rl ALTA 5.2 3.0 1000`\n"
            "Confianza: `ALTA` · `MEDIA` · `BAJA`",
            parse_mode="Markdown",
        )
        return

    try:
        confidence   = context.args[0].upper()
        ev_pct       = float(context.args[1])
        drawdown_pct = float(context.args[2])
        bankroll     = float(context.args[3]) if len(context.args) >= 4 else 1000.0
        market       = " ".join(context.args[4:]) if len(context.args) > 4 else ""

        from core.rl_strategy import RLBettingAgent, format_rl_advice
        agent = RLBettingAgent()
        text  = format_rl_advice(agent, confidence, ev_pct, drawdown_pct, bankroll, market)
        await update.message.reply_text(text, parse_mode="Markdown")

    except (ValueError, IndexError):
        await update.message.reply_text(
            "❌ Valores inválidos. Ej: `/rl ALTA 5.2 3.0 1000`", parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception("Error en /rl")
        await update.message.reply_text(f"❌ Error: {e}")


async def autoscan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /autoscan — Show the auto-scanner status and trigger an immediate scan cycle.

    Displays configuration (ODDS_API_KEY, interval, EV threshold, quota) and
    then runs ``scan_once()`` to fetch live bookmaker odds right now, detect
    arbitrage / value bets / steam moves, and return any alerts directly to
    this chat.
    """
    try:
        from core.auto_scanner import status_summary, scan_once
        # 1. Show status first
        await update.message.reply_text(status_summary(), parse_mode="Markdown")

        # 2. Trigger an immediate scan cycle
        await update.message.reply_text("🔍 Ejecutando escaneo ahora…")
        alerts = await scan_once()

        if not alerts:
            await update.message.reply_text(
                "✅ Escaneo completado — sin alertas nuevas en este ciclo.\n"
                "_El scanner automático enviará alertas al canal configurado "
                "cada vez que detecte oportunidades._",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"📊 *{len(alerts)} alerta(s) encontrada(s):*",
                parse_mode="Markdown",
            )
            for alert in alerts:
                try:
                    await update.message.reply_text(
                        alert.message, parse_mode="Markdown"
                    )
                except Exception:
                    await update.message.reply_text(f"⚠️ {alert.summary}")
    except Exception as exc:
        logger.exception("Error en /autoscan")
        await update.message.reply_text(f"❌ Error: {exc}")


async def matches_update_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job (every 15 min): refresh today_matches.csv from API-Sports."""
    global _last_csv_update
    if not _matches_lock.acquire(blocking=False):
        logger.debug("matches_update_job: skipped (another refresh in progress)")
        return
    try:
        update_matches()
        _last_csv_update = _time.time()
        logger.info("matches_update_job: CSV actualizado")
    except Exception as exc:
        logger.warning("matches_update_job: error al actualizar partidos: %s", exc)
    finally:
        _matches_lock.release()


async def scanner_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Scheduled job (every 15 min): scan manually-tracked markets and send
    HIGH_VALUE / MARKET_ERROR alerts to the alerts channel.
    This job handles markets added via /addmarket.  Automatic scanning of
    all live events is handled by auto_scan_job.
    """
    from core.config import ALERTS_CHANNEL_ID
    channel_id = ALERTS_CHANNEL_ID
    if not channel_id:
        return

    try:
        from api.odds_feed import get_tracked_markets
        from core.market_scanner import scan_multiple_markets, format_alert

        scans = get_tracked_markets()
        if not scans:
            return

        alerts = scan_multiple_markets(scans)
        critical = [a for a in alerts if a.classification in ("MARKET_ERROR", "HIGH_VALUE")]

        for alert in critical:
            try:
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=format_alert(alert),
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.warning("Scanner job: could not send alert: %s", exc)

        if critical:
            logger.info(
                "Scanner job: sent %d critical alert(s) to channel %s",
                len(critical), channel_id,
            )
    except Exception as exc:
        logger.warning("Scanner job error: %s", exc)


async def auto_scan_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Fully automatic background scanning job.

    Runs every AUTO_SCAN_INTERVAL seconds (default 300 s / 5 min).
    Fetches live bookmaker odds from The Odds API, detects arbitrage,
    market errors, value bets, and steam moves, then sends alerts to
    the ALERTS_CHANNEL_ID without any manual input.
    """
    from core.config import ALERTS_CHANNEL_ID
    channel_id = ALERTS_CHANNEL_ID
    if not channel_id:
        logger.debug("auto_scan_job: ALERTS_CHANNEL_ID not set, skipping")
        return

    try:
        from core.auto_scanner import scan_once
        alerts = await scan_once()
        for alert in alerts:
            try:
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=alert.message,
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.warning("auto_scan_job: could not send alert: %s", exc)

        if alerts:
            logger.info(
                "auto_scan_job: dispatched %d alert(s) to channel %s",
                len(alerts), channel_id,
            )
    except Exception as exc:
        logger.warning("auto_scan_job error: %s", exc)


async def send_daily_alerts(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: send high-confidence picks to alerts channel."""
    from core.config import ALERTS_CHANNEL_ID
    channel_id = ALERTS_CHANNEL_ID
    if not channel_id:
        return

    matches = load_today_matches()
    if not matches:
        return

    alerts = []
    for match in matches:
        try:
            pred = get_full_prediction(match["home"], match["away"], fetch_live=True)
            if pred["confidence"] == "ALTA":
                probs = {
                    "Local": pred["home_win"],
                    "Empate": pred["draw"],
                    "Visitante": pred["away_win"],
                }
                best = max(probs, key=probs.get)
                alerts.append(
                    f"🔥 {pred['home']} vs {pred['away']}\n"
                    f"   Pick: {best} ({probs[best]}%)\n"
                    f"   xG: {pred['xg_home']} - {pred['xg_away']}"
                )
        except Exception:
            continue

    if alerts:
        header = f"🤖 *PICKS DE ALTA CONFIANZA*\n📅 {datetime.utcnow().strftime('%d/%m/%Y')}\n\n"
        text = header + "\n\n".join(alerts)
        try:
            await context.bot.send_message(
                chat_id=channel_id, text=text, parse_mode="Markdown"
            )
        except Exception as exc:
            logger.warning("Could not send daily alerts: %s", exc)



# ══════════════════════════════════════════════════════════════════════════════
# 🎰 INLINE KEYBOARD MENU
# ══════════════════════════════════════════════════════════════════════════════

# Each entry: (button_label, callback_data)
# callback_data must be unique and ≤ 64 bytes (Telegram limit).
_MENU_SECTIONS = [
    # Section header (displayed as a disabled text row, full width)
    ("── 🎯 PREDICCIONES ──", None),
    ("⚽ Predict",   "cmd_predict"),
    ("🏀 NBA",       "cmd_nba"),
    ("⚾ MLB",       "cmd_mlb"),
    ("🏈 NFL",       "cmd_nfl"),
    ("🎾 Tennis",    "cmd_tennis"),

    ("── 🎰 PARLAYS ──", None),
    ("🎰 Parlay",        "cmd_parlay"),
    ("🛡 Parlay Safe",   "cmd_parlay_safe"),
    ("🌙 Parlay Soñador", "cmd_parlay_dream"),
    ("📸 Check Parlay",  "cmd_checkparlay"),

    ("── 📊 ESTADÍSTICAS ──", None),
    ("📊 Estadísticas",  "cmd_estadisticas"),
    ("📈 Historial",     "cmd_historial"),
    ("📅 Today",         "cmd_today"),

    ("── 🛰 SCANNER ──", None),
    ("🛰 Autoscan",  "cmd_autoscan"),
    ("🔍 Scanner",   "cmd_scanner"),

    ("── 📡 DATOS EN VIVO ──", None),
    ("📡 Live",     "cmd_live"),
    ("📺 Scores",   "cmd_scores"),
    ("🏆 Tabla",    "cmd_tabla"),
]

# Map callback_data → the async handler function name
_MENU_DISPATCH: dict[str, str] = {
    "cmd_predict":      "predict",
    "cmd_nba":          "nba",
    "cmd_mlb":          "mlb",
    "cmd_nfl":          "nfl",
    "cmd_tennis":       "tennis",
    "cmd_parlay":       "parlay_command",
    "cmd_parlay_safe":  "parlay_safe_command",
    "cmd_parlay_dream": "parlay_dream_command",
    "cmd_checkparlay":  "checkparlay_command",
    "cmd_estadisticas": "estadisticas_command",
    "cmd_historial":    "historial_command",
    "cmd_today":        "today",
    "cmd_autoscan":     "autoscan_command",
    "cmd_scanner":      "scanner_command",
    "cmd_live":         "live",
    "cmd_scores":       "scores",
    "cmd_tabla":        "tabla",
}


def _build_inline_keyboard() -> InlineKeyboardMarkup:
    """
    Build the full inline keyboard.

    Section headers (entries with ``callback_data=None``) appear as a single
    full-width disabled-looking button labelled with the section title.
    All other entries are laid out two per row.
    """
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []

    for label, cb in _MENU_SECTIONS:
        if cb is None:
            # Flush any pending pair first
            if pair:
                rows.append(pair)
                pair = []
            # Section header — full-width, uses a no-op callback so Telegram
            # doesn't complain about a button with no action.
            rows.append([InlineKeyboardButton(label, callback_data="noop")])
        else:
            pair.append(InlineKeyboardButton(label, callback_data=cb))
            if len(pair) == 2:
                rows.append(pair)
                pair = []

    if pair:          # flush last odd button
        rows.append(pair)

    return InlineKeyboardMarkup(rows)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /menu — Show the interactive inline keyboard.

    Pressing any button triggers the corresponding bot command in-chat so
    the user never has to type a slash command manually.
    """
    await update.message.reply_text(
        "🤖 *Sports Engine — Menú Principal*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Elige una opción:",
        parse_mode="Markdown",
        reply_markup=_build_inline_keyboard(),
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle all inline-keyboard button presses.

    Each button's ``callback_data`` is looked up in ``_MENU_DISPATCH`` to
    find the handler function, which is then called directly — exactly as if
    the user had sent the corresponding slash command.
    """
    query = update.callback_query
    await query.answer()   # dismiss the Telegram "loading" spinner

    cb = query.data
    if cb == "noop":
        # Section-header button — nothing to do
        return

    # Commands that need arguments show a usage hint instead of running blind
    _NEEDS_ARGS = {
        "cmd_predict":     "⚽ Uso: `/predict LOCAL vs VISITANTE`",
        "cmd_nba":         "🏀 Uso: `/nba LOCAL vs VISITANTE`",
        "cmd_mlb":         "⚾ Uso: `/mlb LOCAL vs VISITANTE`",
        "cmd_nfl":         "🏈 Uso: `/nfl LOCAL vs VISITANTE`",
        "cmd_tennis":      "🎾 Uso: `/tennis J1 vs J2 [clay/grass/hard]`",
        "cmd_checkparlay": "📸 Uso: `/checkparlay <patas del parlay>`\n"
                           "O envía una *foto* con caption describiendo las patas.",
        "cmd_tabla":       "🏆 Uso: `/tabla <liga>`\nEj: `/tabla Premier League`",
    }

    if cb in _NEEDS_ARGS:
        await query.message.reply_text(
            _NEEDS_ARGS[cb], parse_mode="Markdown"
        )
        return

    fn_name = _MENU_DISPATCH.get(cb)
    if not fn_name:
        await query.message.reply_text("❌ Opción no reconocida.")
        return

    # Resolve the handler function from the global namespace of this module
    handler_fn = globals().get(fn_name)
    if not callable(handler_fn):
        await query.message.reply_text(f"❌ Comando `{fn_name}` no disponible.", parse_mode="Markdown")
        return

    # Synthesise a fake Update so the handler receives a proper message object
    # (callback queries have a message, not a new message, so we wrap it)
    class _FakeUpdate:
        """Thin shim that adapts a CallbackQuery message to the Update interface
        expected by command handlers (which call ``update.message.reply_text``).

        ``query`` is passed explicitly (not captured from enclosing scope) to
        keep the dependency visible and make the class easier to reason about.
        """
        def __init__(self, msg, q):
            self.message        = msg
            self.effective_user = q.from_user
            self._query         = q

        def __getattr__(self, item):
            return getattr(self._query, item)

    fake = _FakeUpdate(query.message, query)
    try:
        await handler_fn(fake, context)
    except Exception as exc:
        logger.exception("menu_callback: error dispatching %s", fn_name)
        await query.message.reply_text(f"❌ Error: {exc}")


def main():
    global _last_csv_update
    logger.info("🚀 Iniciando Sports Engine Bot…")

    validate_config()

    if not TELEGRAM_TOKEN:
        logger.error("TOKEN no está configurado. Saliendo.")
        sys.exit(1)

    # Update today's matches (best-effort); record timestamp on success
    try:
        update_matches()
        _last_csv_update = _time.time()
    except Exception as e:
        logger.warning("No se pudieron actualizar los partidos: %s", e)

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # ── Football/Soccer commands ──
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu",  menu_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("predict", predict))
    app.add_handler(CommandHandler("value", value))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("parlay",       parlay_command))
    app.add_handler(CommandHandler("parlay_safe",  parlay_safe_command))
    app.add_handler(CommandHandler("parlay_dream",   parlay_dream_command))
    app.add_handler(CommandHandler("parlay_sonador", parlay_dream_command))

    # ── Parlay photo analyzer, text checker, result recorder, history ──
    app.add_handler(CommandHandler("checkparlay",   checkparlay_command))
    app.add_handler(CommandHandler("resultado",     resultado_command))
    app.add_handler(CommandHandler("historial",     historial_command))
    app.add_handler(CommandHandler("estadisticas",  estadisticas_command))
    # PHOTO handler must come after CommandHandlers
    app.add_handler(MessageHandler(filters.PHOTO, photo_parlay_handler))
    # Inline keyboard callback handler (menu buttons)
    app.add_handler(CallbackQueryHandler(menu_callback))

    # ── Advanced analytics commands ──
    app.add_handler(CommandHandler("form",    form_command))
    app.add_handler(CommandHandler("markets", markets_command))
    app.add_handler(CommandHandler("intel",   intel_command))

    # ── Intelligence commands (Liquidity, Steam, Consensus, CLV, Risk, Player, Referee, Weather, Portfolio, Bayes, RL) ──
    app.add_handler(CommandHandler("liquidity",  liquidity_command))
    app.add_handler(CommandHandler("steam",      steam_command))
    app.add_handler(CommandHandler("consensus",  consensus_command))
    app.add_handler(CommandHandler("clv",        clv_command))
    app.add_handler(CommandHandler("risk",       risk_command))
    app.add_handler(CommandHandler("player",     player_command))
    app.add_handler(CommandHandler("referee",    referee_command))
    app.add_handler(CommandHandler("weather",    weather_command))
    app.add_handler(CommandHandler("portfolio",  portfolio_command))
    app.add_handler(CommandHandler("bayes",      bayes_command))
    app.add_handler(CommandHandler("rl",         rl_command))

    # ── Universal Market Error Scanner commands ──
    app.add_handler(CommandHandler("scanodds",     scanodds_command))
    app.add_handler(CommandHandler("scanner",      scanner_command))
    app.add_handler(CommandHandler("addmarket",    addmarket_command))
    app.add_handler(CommandHandler("clearmarkets", clearmarkets_command))
    app.add_handler(CommandHandler("autoscan",     autoscan_command))

    # ── Multi-sport commands ──
    app.add_handler(CommandHandler("sports", sports_command))
    app.add_handler(CommandHandler("nba", nba))
    app.add_handler(CommandHandler("mlb", mlb))
    app.add_handler(CommandHandler("nfl", nfl))
    app.add_handler(CommandHandler("tennis", tennis))

    # ── Live data commands (SofaScore / TheSportsDB / ESPN) ──
    app.add_handler(CommandHandler("live", live))
    app.add_handler(CommandHandler("scores", scores))
    app.add_handler(CommandHandler("liveteam", liveteam))
    app.add_handler(CommandHandler("tabla", tabla))

    # ── Daily alerts scheduler (8 AM) ──
    from core.config import ALERTS_CHANNEL_ID
    if app.job_queue:
        if ALERTS_CHANNEL_ID:
            from datetime import time as dt_time
            alert_time = dt_time(hour=8, minute=0)
            app.job_queue.run_daily(send_daily_alerts, time=alert_time)
            logger.info("Daily alerts scheduled at 08:00 UTC → channel %s", ALERTS_CHANNEL_ID)

        # ── Market Error Scanner (manually-tracked markets, every 15 min) ──
        app.job_queue.run_repeating(scanner_job, interval=900, first=120)
        logger.info("Market Error Scanner scheduled every 15 min")

        # ── Auto-Scanner (fully automatic, every AUTO_SCAN_INTERVAL seconds) ──
        from core.config import AUTO_SCAN_INTERVAL
        app.job_queue.run_repeating(auto_scan_job, interval=AUTO_SCAN_INTERVAL, first=60)
        logger.info("Auto-Scanner scheduled every %d s", AUTO_SCAN_INTERVAL)

        # ── Football CSV refresh (every 15 minutes) ──
        # first=MATCHES_UPDATE_TTL: the initial update already ran at bot startup,
        # so schedule the first background refresh one full TTL later to avoid
        # a redundant API call immediately after boot.
        app.job_queue.run_repeating(matches_update_job, interval=MATCHES_UPDATE_TTL, first=MATCHES_UPDATE_TTL)
        logger.info("Football matches CSV refresh scheduled every %d s", MATCHES_UPDATE_TTL)

    logger.info("🤖 Bot corriendo — 5 deportes + datos en vivo (SofaScore/TheSportsDB/ESPN)…")
    app.run_polling()


if __name__ == "__main__":
    main()
