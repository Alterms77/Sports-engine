from sports.football import predict_match
from core.scorelines import top_scorelines
from core.corners import expected_corners, corners_market
from core.cards import expected_cards
from core.confidence import confidence_level


def main():
    print("=== MOTOR DE PREDICCIÓN DE APUESTAS ===\n")

    home = input("Equipo local: ").strip()
    away = input("Equipo visitante: ").strip()

    # ===============================
    # PREDICCIÓN BASE
    # ===============================
    result = predict_match(home, away)

    xg_home = result["xg_home"]
    xg_away = result["xg_away"]
    probs = result

    print(f"\n⚽ {home} vs {away}")
    print(f"xG {home}: {xg_home}")
    print(f"xG {away}: {xg_away}")

    # ===============================
    # 1X2
    # ===============================
    print("\n📊 1X2")
    print(f"Local: {probs['home_win']}%")
    print(f"Empate: {probs['draw']}%")
    print(f"Visitante: {probs['away_win']}%")

    # ===============================
    # GOLES
    # ===============================
    print("\n⚽ GOLES")
    print(f"Over 1.5: {probs['over_1_5']}%")
    print(f"Over 2.5: {probs['over_2_5']}%")
    print(f"Over 3.5: {probs['over_3_5']}%")
    print(f"BTTS: {probs['btts']}%")

    # ===============================
    # MARCADORES PROBABLES
    # ===============================
    print("\n🎯 MARCADORES PROBABLES")
    scorelines = top_scorelines(xg_home, xg_away)
    for score, pct in scorelines:
        print(f"{score} → {pct}%")

    # ===============================
    # CÓRNERS
    # ===============================
    print("\n🚩 CÓRNERS")
    corners_expected = expected_corners(xg_home, xg_away)
    corners_info = corners_market(corners_expected)

    print(f"Esperados: {corners_expected}")
    print(f"Línea: {corners_info['line']}")
    print(f"Mercado recomendado: {corners_info['suggestion']}")

    # ===============================
    # TARJETAS
    # ===============================
    print("\n🟨 TARJETAS")
    cards = expected_cards(xg_home, xg_away)
    print(f"Estimadas: {cards}")

    # ===============================
    # CONFIANZA
    # ===============================
    print("\n📌 CONFIANZA")
    confidence = confidence_level(probs)
    print(confidence)

    # ===============================
    # VALUE BETS (OPCIONAL)
    # ===============================
    print("\n💰 VALUE BETS")
    try:
        cuota_local = float(input("Cuota Local: "))
        cuota_empate = float(input("Cuota Empate: "))
        cuota_visitante = float(input("Cuota Visitante: "))

        value_local = round((probs["home_win"] / 100) * cuota_local, 2)
        value_empate = round((probs["draw"] / 100) * cuota_empate, 2)
        value_visitante = round((probs["away_win"] / 100) * cuota_visitante, 2)

        print("\nValor esperado:")
        print(f"Local: {value_local}")
        print(f"Empate: {value_empate}")
        print(f"Visitante: {value_visitante}")

    except:
        print("Value bets omitido.")

    print("\n✅ Predicción finalizada.\n")


if __name__ == "__main__":
    main()

