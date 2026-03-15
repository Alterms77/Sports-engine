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


def format_prediction(pred: dict) -> str:
    conf = pred["confidence"]
    emoji = _confidence_emoji(conf)
    league = pred.get("league", "")
    league_str = f" _({league})_" if league and league != "default" else ""

    # Top scoreline
    top_score = pred["top_scores"][0] if pred.get("top_scores") else ("?", 0)
    score_str = f"{top_score[0]} ({top_score[1]:.1f}%)"

    # Form section
    fh = pred.get("form_home", {})
    fa = pred.get("form_away", {})
    form_home_str = f"{fh.get('emoji','➡️')} {fh.get('last5','-----')}"
    form_away_str = f"{fa.get('emoji','➡️')} {fa.get('last5','-----')}"

    # H2H section
    h2h_section = _h2h_line(pred)

    # Value bets (only shown if user supplied odds)
    value_lines = []
    for market, val in (pred.get("value_bets") or {}).items():
        if val and val > 0:
            value_lines.append(f"  ✅ {market.capitalize()}: +{val:.3f}")
    value_section = "\n💰 *Value Bets*\n" + "\n".join(value_lines) if value_lines else ""

    # Clean sheet probabilities
    cs_home = pred.get("clean_sheet_home")
    cs_away = pred.get("clean_sheet_away")
    cs_str = ""
    if cs_home is not None and cs_away is not None:
        cs_str = (
            f"\n🔒 *Clean Sheet*\n"
            f"  {pred['home'].split()[0]}: {cs_home*100:.0f}% | "
            f"{pred['away'].split()[0]}: {cs_away*100:.0f}%\n"
        )

    return (
        f"⚽ *{pred['home']} vs {pred['away']}*{league_str}\n\n"
        f"📊 *xG Esperado*\n"
        f"  Local: `{pred['xg_home']}`  Visitante: `{pred['xg_away']}`\n\n"
        f"🏆 *Probabilidades 1X2*\n"
        f"  Local: `{pred['home_win']:.1f}%`\n"
        f"  Empate: `{pred['draw']:.1f}%`\n"
        f"  Visitante: `{pred['away_win']:.1f}%`\n\n"
        f"🎯 *Marcador más probable:* `{score_str}`\n\n"
        f"🔥 *Mercados*\n"
        f"  Over 1.5: `{pred['over_1_5']}%`\n"
        f"  Over 2.5: `{pred['over_2_5']}%`\n"
        f"  Over 3.5: `{pred['over_3_5']}%`\n"
        f"  BTTS: `{pred['btts']}%`\n\n"
        f"📈 *Forma reciente*\n"
        f"  {pred['home'].split()[0]}: {form_home_str}\n"
        f"  {pred['away'].split()[0]}: {form_away_str}\n"
        f"{h2h_section}"
        f"{cs_str}"
        f"🚩 Córners: `{pred['corners']}`  🟨 Tarjetas: `{pred['cards']}`\n"
        f"{value_section}\n\n"
        f"💡 *Mejor Pick:* {_best_pick(pred)}\n"
        f"{emoji} *Confianza:* {conf}"
        + _live_source_badge(pred)
    )


def _live_source_badge(pred: dict) -> str:
    """Return a small live-data source note when live data was used."""
    source = pred.get("live_source")
    if not source:
        return ""
    names = {"sofascore": "SofaScore", "thesportsdb": "TheSportsDB", "espn": "ESPN"}
    pretty = names.get(source, source.capitalize())
    return f"\n\n📡 _Forma en vivo: {pretty}_"


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
        "📡 *Datos en vivo (SofaScore / TheSportsDB / ESPN)*\n"
        "  `/live [deporte]` — marcadores en vivo\n"
        "  `/scores [deporte]` — partidos de hoy\n"
        "  `/liveteam EQUIPO` — forma + próximos partidos\n"
        "  `/tabla LIGA` — clasificación\n\n"
        "📅 `/today` — todos los partidos de hoy\n"
        "🏟️ `/sports` — ver todos los comandos\n"
        "❓ `/help` — ayuda detallada\n\n"
        "_Ejemplos:_\n"
        "`/predict América vs Chivas`\n"
        "`/live futbol`\n"
        "`/liveteam Barcelona`\n"
        "`/tabla Premier League`",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Ayuda — Sports Engine*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚽ *FÚTBOL*\n"
        "🔹 `/predict LOCAL vs VISITANTE`\n"
        "  xG, 1X2, Over/BTTS, marcador probable, forma en vivo,\n"
        "  H2H, córners, tarjetas. Usa SofaScore/TheSportsDB.\n"
        "  _Ej:_ `/predict Real Madrid vs Barcelona`\n\n"
        "🔹 `/stats EQUIPO` — stats históricas del equipo\n"
        "🔹 `/value L vs V C\\_L C\\_E C\\_V` — value bets\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📡 *DATOS EN VIVO* _(SofaScore / TheSportsDB / ESPN)_\n"
        "🔹 `/live [deporte]`\n"
        "  Marcadores en vivo ahora mismo.\n"
        "  Deportes: `futbol` `nba` `nfl` `mlb` `tenis`\n"
        "  _Ej:_ `/live futbol`\n\n"
        "🔹 `/scores [deporte] [YYYY-MM-DD]`\n"
        "  Todos los partidos del día (o fecha específica).\n"
        "  _Ej:_ `/scores nba`  `/scores futbol 2026-03-15`\n\n"
        "🔹 `/liveteam EQUIPO`\n"
        "  Últimos 5 resultados + próximos partidos en vivo.\n"
        "  _Ej:_ `/liveteam Barcelona`\n\n"
        "🔹 `/tabla LIGA`\n"
        "  Clasificación de la liga.\n"
        "  Ligas: Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Liga MX\n"
        "  _Ej:_ `/tabla Bundesliga`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🏀 `/nba H vs V` | ⚾ `/mlb H vs V` | 🏈 `/nfl H vs V`\n"
        "🎾 `/tennis J1 vs J2 [clay/grass/hard]`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*Confianza:* 🟢 ALTA ≥55% | 🟡 MEDIA ≥42% | 🔴 BAJA\n"
        "*Forma:* 🔥📈➡️📉❄️\n\n"
        "_Motor: Home/Away Split + Dixon-Coles + MC + Decay Form + H2H_\n"
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
    Example: /value América vs Chivas 1.80 3.40 4.50
    """
    if not context.args or len(context.args) < 5:
        await update.message.reply_text(
            "❌ Formato:\n`/value LOCAL vs VISITANTE C\\_LOCAL C\\_EMPATE C\\_VISIT`\n\n"
            "Ejemplo:\n`/value América vs Chivas 1.80 3.40 4.50`",
            parse_mode="Markdown",
        )
        return

    # Last 3 args are the odds
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

    # Everything before the odds is the match string
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

    await update.message.reply_text("⏳ Calculando value bets…")

    try:
        user_odds = {"home": odds_home, "draw": odds_draw, "away": odds_away}
        prediction = get_full_prediction(home, away, odds=user_odds)

        vb = prediction.get("value_bets", {})

        lines = [f"💰 *Value Bets — {prediction['home']} vs {prediction['away']}*\n"]
        labels = {
            "home": f"Victoria {prediction['home']}",
            "draw": "Empate",
            "away": f"Victoria {prediction['away']}",
        }
        has_value = False
        for market in ("home", "draw", "away"):
            val = vb.get(market, 0) or 0
            prob = {"home": prediction["home_win"], "draw": prediction["draw"], "away": prediction["away_win"]}[market]
            o = {"home": odds_home, "draw": odds_draw, "away": odds_away}[market]
            indicator = "✅ VALUE" if val > 0 else "❌ Sin value"
            lines.append(f"  {labels[market]} @ {o:.2f}\n    Prob: {prob:.1f}% | Value: {val:+.3f} {indicator}\n")
            if val > 0:
                has_value = True

        if not has_value:
            lines.append("\n⚠️ No hay value en ninguno de los mercados con estas cuotas.")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

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
    /stats EQUIPO
    Shows home/away attack+defense, recent form, streak, and clean sheet prob.
    """
    if not context.args:
        await update.message.reply_text(
            "❌ Uso:\n`/stats EQUIPO`\n\nEjemplo:\n`/stats Bayern Munich`",
            parse_mode="Markdown",
        )
        return

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
        elif sport_key == "MLB":
            lines.append("🎯 *Carreras proyectadas*")
            lines.append(f"  {home}: `{pred['expected_home']}` runs")
            lines.append(f"  {away}: `{pred['expected_away']}` runs")
            lines.append(f"  Over/Under: `{pred['over_under']}` runs")
            if pred.get("home_era"):
                lines.append(f"  ERA pitcheo — {home}: `{pred['home_era']}` / {away}: `{pred.get('away_era', '?')}`")
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
        "⚽ *Fútbol*\n"
        "  `/predict LOCAL vs VISITANTE`\n"
        "  `/value LOCAL vs VISITANTE C\\_L C\\_E C\\_V`\n"
        "  `/stats EQUIPO`\n\n"
        "🏀 *NBA* — `/nba LOCAL vs VISITANTE`\n"
        "⚾ *MLB* — `/mlb LOCAL vs VISITANTE`\n"
        "🏈 *NFL* — `/nfl LOCAL vs VISITANTE`\n"
        "🎾 *Tenis* — `/tennis J1 vs J2 [clay/grass/hard]`\n\n"
        "📡 *Datos en vivo*\n"
        "  `/live [deporte]` — marcadores en vivo ahora\n"
        "  `/scores [deporte]` — partidos de hoy\n"
        "  `/liveteam EQUIPO` — forma + próximos partidos\n"
        "  `/tabla LIGA` — clasificación\n\n"
        "📅 `/today` — agenda multideporte\n"
        "❓ `/help` — ayuda detallada\n\n"
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

    logger.info("🤖 Bot corriendo — 5 deportes + datos en vivo (SofaScore/TheSportsDB/ESPN)…")
    app.run_polling()


if __name__ == "__main__":
    main()
