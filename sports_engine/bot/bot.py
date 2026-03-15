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
from core.teams import normalize_team
from core.config import TELEGRAM_TOKEN, validate_config

# ===============================
# 🪵 LOGGING
# ===============================

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
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
    )


# ===============================
# 📌 COMANDOS DEL BOT
# ===============================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Sports Engine — Multi-Sport Bot*\n\n"
        "⚽ Fútbol: `/predict LOCAL vs VISITANTE`\n"
        "🏀 NBA: `/nba LOCAL vs VISITANTE`\n"
        "⚾ MLB: `/mlb LOCAL vs VISITANTE`\n"
        "🏈 NFL: `/nfl LOCAL vs VISITANTE`\n"
        "🎾 Tenis: `/tennis J1 vs J2 [clay/grass/hard]`\n\n"
        "📅 `/today` — todos los partidos de hoy\n"
        "🏟️ `/sports` — ver todos los comandos\n"
        "❓ `/help` — ayuda detallada\n\n"
        "_Ejemplos:_\n"
        "`/predict América vs Chivas`\n"
        "`/nba Lakers vs Celtics`\n"
        "`/tennis Djokovic vs Alcaraz clay`",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Ayuda — Sports Engine*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚽ *FÚTBOL*\n"
        "🔹 `/predict LOCAL vs VISITANTE`\n"
        "  xG, 1X2, Over/BTTS, marcador probable, forma, H2H, córners, tarjetas.\n"
        "  _Ej:_ `/predict Real Madrid vs Barcelona`\n\n"
        "🔹 `/stats EQUIPO`\n"
        "  Ataque/defensa local y visitante, forma y clean sheet rate.\n"
        "  _Ej:_ `/stats Bayern Munich`\n\n"
        "🔹 `/value LOCAL vs VISITANTE C\\_L C\\_E C\\_V`\n"
        "  Value bets con tus cuotas.\n"
        "  _Ej:_ `/value Liverpool vs Chelsea 1.90 3.50 4.20`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🏀 *BASQUETBOL (NBA)*\n"
        "🔹 `/nba LOCAL vs VISITANTE`\n"
        "  Prob. victoria, marcador proyectado, spread, over/under.\n"
        "  _Ej:_ `/nba Lakers vs Celtics`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚾ *BÉISBOL (MLB)*\n"
        "🔹 `/mlb LOCAL vs VISITANTE`\n"
        "  Carreras proyectadas, Pythagorean win%, over/under.\n"
        "  _Ej:_ `/mlb Yankees vs Red Sox`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🏈 *FÚTBOL AMERICANO (NFL)*\n"
        "🔹 `/nfl LOCAL vs VISITANTE`\n"
        "  Prob. victoria, puntos proyectados, spread, over/under.\n"
        "  _Ej:_ `/nfl Chiefs vs Eagles`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎾 *TENIS (ATP/WTA)*\n"
        "🔹 `/tennis J1 vs J2 [clay/grass/hard]`\n"
        "  Elo-based win probability, ajuste por superficie.\n"
        "  _Ej:_ `/tennis Djokovic vs Alcaraz clay`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*Confianza:* 🟢 ALTA ≥55% | 🟡 MEDIA ≥42% | 🔴 BAJA\n"
        "*Forma:* 🔥📈➡️📉❄️\n\n"
        "_Motor: Home/Away Split + Dixon-Coles + Monte Carlo + Decay Form + H2H + ESPN API_",
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

    await update.message.reply_text("⏳ Analizando partido…")

    try:
        prediction = get_full_prediction(home, away)
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
        "🏟️ *Sports Engine — Deportes disponibles*\n\n"
        "⚽ *Fútbol*\n"
        "  `/predict LOCAL vs VISITANTE` — predicción completa\n"
        "  `/value LOCAL vs VISITANTE C\\_L C\\_E C\\_V` — value bets\n"
        "  `/stats EQUIPO` — estadísticas del equipo\n\n"
        "🏀 *Basquetbol (NBA)*\n"
        "  `/nba LOCAL vs VISITANTE`\n"
        "  _Ej:_ `/nba Lakers vs Celtics`\n\n"
        "⚾ *Béisbol (MLB)*\n"
        "  `/mlb LOCAL vs VISITANTE`\n"
        "  _Ej:_ `/mlb Yankees vs Red Sox`\n\n"
        "🏈 *Fútbol Americano (NFL)*\n"
        "  `/nfl LOCAL vs VISITANTE`\n"
        "  _Ej:_ `/nfl Chiefs vs Eagles`\n\n"
        "🎾 *Tenis (ATP/WTA)*\n"
        "  `/tennis J1 vs J2 [clay/grass/hard]`\n"
        "  _Ej:_ `/tennis Djokovic vs Alcaraz clay`\n\n"
        "📅 `/today` — partidos de hoy en todos los deportes\n"
        "❓ `/help` — ayuda detallada",
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

    logger.info("🤖 Bot corriendo correctamente — 5 deportes activos…")
    app.run_polling()


if __name__ == "__main__":
    main()
