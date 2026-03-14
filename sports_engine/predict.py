from sports.football import predict_match
from core.value import is_value, value_rating


def main():
    print("=== MOTOR DE APUESTAS ===")

    home = input("Equipo local: ")
    away = input("Equipo visitante: ")

    result = predict_match(home, away)

    print(f"\n⚽ {result['home']} vs {result['away']}")
    print(f"xG {result['home']}: {result['xg_home']}")
    print(f"xG {result['away']}: {result['xg_away']}")

    print("\n📊 1X2")
    print(f"Local: {result['home_win']}%")
    print(f"Empate: {result['draw']}%")
    print(f"Visitante: {result['away_win']}%")

    print("\n⚽ GOLES")
    print(f"Over 1.5: {result['over_1_5']}%")
    print(f"Over 2.5: {result['over_2_5']}%")
    print(f"Over 3.5: {result['over_3_5']}%")
    print(f"BTTS: {result['btts']}%")

    print("\n🎯 MARCADORES PROBABLES")
    for s, p in result["top_scores"]:
        print(f"{s} → {p}%")

    print("\n🚩 CÓRNERS")
    print(f"Esperados: {result['corners']}")

    print("\n🟨 TARJETAS")
    print(f"Estimadas: {result['cards']}")

    print("\n📌 CONFIANZA")
    print(result["confidence"])

    print("\n💰 VALUE BETS")
    odds_home = float(input("Cuota Local: "))
    odds_draw = float(input("Cuota Empate: "))
    odds_away = float(input("Cuota Visitante: "))

    if is_value(result["home_win"] / 100, odds_home):
        print(f"✅ VALUE Local ({value_rating(result['home_win']/100, odds_home)}%)")

    if is_value(result["draw"] / 100, odds_draw):
        print(f"✅ VALUE Empate ({value_rating(result['draw']/100, odds_draw)}%)")

    if is_value(result["away_win"] / 100, odds_away):
        print(f"✅ VALUE Visitante ({value_rating(result['away_win']/100, odds_away)}%)")


if __name__ == "__main__":
    main()
