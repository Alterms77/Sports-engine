import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# ===============================
# 🤖 SPORTS ENGINE TELEGRAM BOT
# ===============================
# Python 3.11
# python-telegram-bot >= 20.x
# Bot público (modo demo)
# ===============================

from update_matches import update_matches
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes
)

import csv
import sys
from datetime import datetime

# ===============================
# 🔧 IMPORT DEL MOTOR DE PREDICCIÓN
# ===============================
try:
    from sports.football import get_full_prediction
    from core.teams import normalize_team
except ImportError:
    print("⚠️ Módulos opcionales no disponibles")

# ===============================
# 🔑 CONFIGURACIÓN
# ===============================

# Usar variable de entorno o token directo
TOKEN = os.getenv("TOKEN", "8183332785:AAGOHTrosx5TwECKVwRq5in0BSiY7uF0Nyg")

# Determinar la ruta correcta
if os.path.exists("sports_engine"):
    BASE_DIR = os.path.abspath("sports_engine")
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_PATH = os.path.join(BASE_DIR, "data", "today_matches.csv")

print(f"📁 BASE_DIR: {BASE_DIR}")
print(f"📄 DATA_PATH: {DATA_PATH}")
print(f"🔑 TOKEN cargado: {'Sí' if TOKEN else 'No'}")

# ===============================
# 🧠 FUNCIONES AUXILIARES
# ===============================

def load_today_matches():
    matches = []

    if not os.path.exists(DATA_PATH):
        print(f"⚠️ Archivo no encontrado: {DATA_PATH}")
        return matches

    try:
        with open(DATA_PATH, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                matches.append({
                    "home": row["home"].strip(),
                    "away": row["away"].strip(),
                    "league": row.get("league", "").strip()
                })
    except Exception as e:
        print(f"❌ Error al cargar partidos: {e}")

    return matches


def format_prediction(pred):
    return (
        f"⚽ {pred['home']} vs {pred['away']}\n\n"
        f"📊 xG\n"
        f"Local: {pred['xg_home']}\n"
        f"Visitante: {pred['xg_away']}\n\n"
        f"🏆 Probabilidades\n"
        f"Local: {pred['home_win']}%\n"
        f"Empate: {pred['draw']}%\n"
        f"Visitante: {pred['away_win']}%\n\n"
        f"🔥 Mercados\n"
        f"Over 1.5: {pred['over_1_5']}%\n"
        f"Over 2.5: {pred['over_2_5']}%\n"
        f"Over 3.5: {pred['over_3_5']}%\n"
        f"BTTS: {pred['btts']}%\n\n"
        f"🚩 Córners esperados: {pred['corners']}\n"
        f"🟨 Tarjetas esperadas: {pred['cards']}\n\n"
        f"🎯 Confianza del pick: {pred['confidence']}"
    )

# ===============================
# 📌 COMANDOS DEL BOT
# ===============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Sports Engine Demo*\n\n"
        "Comandos disponibles:\n"
        "/today → partidos del día\n"
        "/predict LOCAL vs VISITANTE → predicción\n\n"
        "Ejemplo:\n"
        "`/predict América vs Chivas`",
        parse_mode="Markdown"
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    matches = load_today_matches()

    if not matches:
        await update.message.reply_text("📭 No hay partidos cargados para hoy.")
        return

    text = "📅 *Partidos de hoy*\n\n"

    for m in matches:
        league = f" ({m['league']})" if m["league"] else ""
        text += f"• {m['home']} vs {m['away']}{league}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def predict(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        await update.message.reply_text(
            "❌ Uso incorrecto.\n\n"
            "Formato correcto:\n"
            "/predict LOCAL vs VISITANTE"
        )
        return

    raw_text = " ".join(context.args)

    if " vs " not in raw_text.lower():
        await update.message.reply_text(
            "❌ Formato incorrecto.\n\n"
            "Usa exactamente:\n"
            "LOCAL vs VISITANTE"
        )
        return

    home_raw, away_raw = raw_text.split(" vs ", 1)

    # 🔥 dejamos que el motor resuelva los equipos
    home = home_raw.strip()
    away = away_raw.strip()

    await update.message.reply_text("⏳ Analizando partido...")

    try:
        prediction = get_full_prediction(home, away)
        await update.message.reply_text(format_prediction(prediction))

    except Exception as e:
        print("ERROR REAL:", e)
        await update.message.reply_text(
            f"❌ Error al analizar el partido: {e}"
        )

# ===============================
# 🚀 MAIN
# ===============================

def main():
    print("🚀 Iniciando Sports Engine Bot...")
    
    # 🔄 actualizar partidos desde API (con manejo de errores)
    try:
        update_matches()
    except Exception as e:
        print(f"⚠️ Error actualizando partidos: {e}")

    if not TOKEN:
        print("❌ ERROR: TOKEN no está configurado")
        sys.exit(1)

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("predict", predict))

    print("🤖 Bot corriendo correctamente...")
    app.run_polling()


if __name__ == "__main__":
    main()