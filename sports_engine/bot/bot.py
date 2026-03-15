import os
import sys
import csv
import logging
from datetime import datetime
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

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
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

# ===============================
# 🧠 FUNCIONES AUXILIARES
# ===============================


def load_today_matches():
    matches = []

    if not os.path.exists(DATA_PATH):
        logger.warning("Archivo no encontrado: %s", DATA_PATH)
        return matches

    try:
        with open(DATA_PATH, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                matches.append({
                    "home": row["home"].strip(),
                    "away": row["away"].strip(),
                    "league": row.get("league", "").strip(),
                })
    except Exception as e:
        logger.error("Error al cargar partidos: %s", e)

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
        "🎰 Parlays: `/parlay` — parlays confiables del día\n\n"
        "🔬 *Analytics avanzados*\n"
        "  `/form EQUIPO` — form engine profundo\n"
        "  `/intel LOCAL vs VISITANTE` — game intelligence\n"
        "  `/markets LOCAL vs VISITANTE` — modelos de mercado\n\n"
        "🔍 *Market Error Scanner (universal)*\n"
        "  `/scanodds EVENTO | MERCADO | cuota@casa...`\n"
        "  `/scanner` — escanear mercados en seguimiento\n"
        "  `/addmarket DEPORTE | EVENTO | MERCADO | cuota@casa...`\n\n"
        "📡 *Datos en vivo (SofaScore / TheSportsDB / ESPN)*\n"
        "  `/live [deporte]` — marcadores en vivo\n"
        "  `/scores [deporte]` — partidos de hoy\n"
        "  `/liveteam EQUIPO` — forma + próximos partidos\n"
        "  `/tabla LIGA` — clasificación\n\n"
        "📅 `/today` — todos los partidos de hoy\n"
        "🏟️ `/sports` — ver todos los comandos\n"
        "❓ `/help` — ayuda detallada",
        parse_mode="Markdown",
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
        "🎰 `/parlay` — parlays confiables del día\n\n"
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
        "📡 *DATOS EN VIVO* _(SofaScore / TheSportsDB / ESPN)_\n"
        "🔹 `/live [deporte]` | `/scores [deporte]`\n"
        "🔹 `/liveteam EQUIPO` | `/tabla LIGA`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🏀 `/nba H vs V` | ⚾ `/mlb H vs V` | 🏈 `/nfl H vs V`\n"
        "🎾 `/tennis J1 vs J2 [clay/grass/hard]`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*Confianza:* 🟢 ALTA ≥55% | 🟡 MEDIA ≥42% | 🔴 BAJA\n"
        "_Motor: Home/Away Split + Dixon-Coles + MC + Decay Form + H2H + Elo_\n"
        "_Métricas: xThreat · PPDA · Field Tilt · Sharp · AH · HT/FT_\n"
        "_Fuentes: SofaScore · TheSportsDB · ESPN_",
        parse_mode="Markdown",
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show today's football matches (local CSV) + live ESPN schedule for all sports."""
    # ── Local football matches ──
    football_matches = load_today_matches()

    text = "📅 *Partidos de hoy*\n\n"

    if football_matches:
        text += "⚽ *Fútbol*\n"
        for m in football_matches:
            league = f" _{m['league']}_" if m["league"] else ""
            text += f"  • {m['home']} vs {m['away']}{league}\n"
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


async def parlay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate parlay recommendations from today's matches."""
    await update.message.reply_text(
        "🎰 Generando parlays del día…",
        parse_mode="Markdown",
    )

    matches = load_today_matches()
    if not matches:
        await update.message.reply_text(
            "❌ No hay partidos cargados para hoy.\nUsa `/today` para verificar.",
            parse_mode="Markdown",
        )
        return

    predictions = []
    for m in matches:
        try:
            pred = get_full_prediction(
                m["home"], m["away"],
                league=m.get("league", "default"),
                fetch_live=True,
            )
            predictions.append(pred)
        except Exception as e:
            logger.warning("Parlay: skip %s vs %s: %s", m["home"], m["away"], e)

    if not predictions:
        await update.message.reply_text(
            "❌ No se pudieron generar predicciones para los partidos de hoy."
        )
        return

    from core.parlay import generate_parlay_legs, build_parlays, format_parlay
    legs = generate_parlay_legs(predictions)
    if len(legs) < 2:
        await update.message.reply_text(
            "⚠️ No hay suficientes picks confiables para armar un parlay hoy."
        )
        return

    parlays = build_parlays(legs)
    text = format_parlay(parlays)
    await update.message.reply_text(text, parse_mode="Markdown")



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


async def scanner_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Scheduled job (every 15 min): scan all tracked markets and send
    HIGH_VALUE / MARKET_ERROR alerts to the alerts channel.
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


def main():
    logger.info("🚀 Iniciando Sports Engine Bot…")

    validate_config()

    if not TELEGRAM_TOKEN:
        logger.error("TOKEN no está configurado. Saliendo.")
        sys.exit(1)

    # Update today's matches (best-effort)
    try:
        update_matches()
    except Exception as e:
        logger.warning("No se pudieron actualizar los partidos: %s", e)

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # ── Football/Soccer commands ──
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("predict", predict))
    app.add_handler(CommandHandler("value", value))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("parlay", parlay_command))

    # ── Advanced analytics commands ──
    app.add_handler(CommandHandler("form",    form_command))
    app.add_handler(CommandHandler("markets", markets_command))
    app.add_handler(CommandHandler("intel",   intel_command))

    # ── Universal Market Error Scanner commands ──
    app.add_handler(CommandHandler("scanodds",     scanodds_command))
    app.add_handler(CommandHandler("scanner",      scanner_command))
    app.add_handler(CommandHandler("addmarket",    addmarket_command))
    app.add_handler(CommandHandler("clearmarkets", clearmarkets_command))

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

        # ── Market Error Scanner (every 15 minutes) ──
        app.job_queue.run_repeating(scanner_job, interval=900, first=120)
        logger.info("Market Error Scanner scheduled every 15 min")

    logger.info("🤖 Bot corriendo — 5 deportes + datos en vivo (SofaScore/TheSportsDB/ESPN)…")
    app.run_polling()


if __name__ == "__main__":
    main()
