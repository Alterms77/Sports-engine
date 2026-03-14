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
        "🤖 *Sports Engine*\n\n"
        "Comandos disponibles:\n"
        "  /today — partidos del día\n"
        "  /predict LOCAL vs VISITANTE — predicción completa\n"
        "  /value LOCAL vs VISITANTE C\\_LOCAL C\\_EMPATE C\\_VISIT — value bets\n"
        "  /stats EQUIPO — estadísticas de un equipo\n"
        "  /help — ayuda detallada\n\n"
        "Ejemplos:\n"
        "`/predict América vs Chivas`\n"
        "`/stats Barcelona`",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Ayuda — Sports Engine*\n\n"
        "*Comandos:*\n\n"
        "🔹 `/start` — Mensaje de bienvenida\n\n"
        "🔹 `/today` — Lista los partidos cargados para hoy\n\n"
        "🔹 `/predict LOCAL vs VISITANTE`\n"
        "  Predicción completa: xG, 1X2, Over/BTTS, marcador probable,\n"
        "  forma reciente (últimos 5), H2H, córners, tarjetas y confianza.\n"
        "  Auto-detecta la liga para ajustar la ventaja local.\n"
        "  _Ejemplo:_ `/predict Real Madrid vs Barcelona`\n\n"
        "🔹 `/stats EQUIPO`\n"
        "  Muestra estadísticas de un equipo: ataque/defensa local y visitante,\n"
        "  forma reciente y probabilidad de clean sheet.\n"
        "  _Ejemplo:_ `/stats Bayern Munich`\n\n"
        "🔹 `/value LOCAL vs VISITANTE C\\_LOCAL C\\_EMPATE C\\_VISIT`\n"
        "  Calcula el valor esperado de cada resultado con las cuotas dadas.\n"
        "  _Ejemplo:_ `/value Liverpool vs Chelsea 1.90 3.50 4.20`\n\n"
        "*Indicadores de confianza:*\n"
        "  🟢 ALTA — probabilidad ≥ 55%\n"
        "  🟡 MEDIA — probabilidad ≥ 42%\n"
        "  🔴 BAJA — probabilidad < 42%\n\n"
        "*Indicadores de forma:*\n"
        "  🔥 Racha ganadora larga  📈 Buen momento\n"
        "  ➡️ Sin racha clara  📉 Mal momento  ❄️ Racha perdedora larga\n\n"
        "_Motor: Home/Away Split + Dixon-Coles + Monte Carlo 50k + Decay Form + H2H_",
        parse_mode="Markdown",
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    matches = load_today_matches()

    if not matches:
        await update.message.reply_text("📭 No hay partidos cargados para hoy.")
        return

    text = "📅 *Partidos disponibles*\n\n"

    for m in matches:
        league = f" _{m['league']}_" if m["league"] else ""
        text += f"• {m['home']} vs {m['away']}{league}\n"

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
# 🚀 MAIN
# ===============================


def main():
    logger.info("🚀 Iniciando Sports Engine Bot…")

    validate_config()


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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("predict", predict))
    app.add_handler(CommandHandler("value", value))
    app.add_handler(CommandHandler("stats", stats))

    logger.info("🤖 Bot corriendo correctamente…")
    app.run_polling()


if __name__ == "__main__":
    main()
