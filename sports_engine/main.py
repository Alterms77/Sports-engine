from core.backtesting import load_matches, backtest

team_stats = {
    "Barcelona": {
        "attack": 2.1,
        "defense": 0.9,
        "recent_goals": [2, 3, 1, 4, 2]
    },
    "Antwerp": {
        "attack": 1.2,
        "defense": 1.6,
        "recent_goals": [1, 0, 2, 1, 1]
    },
    "Napoli": {
        "attack": 1.8,
        "defense": 1.1,
        "recent_goals": [2, 1, 1, 3, 2]
    },
    "Real Madrid": {
        "attack": 2.0,
        "defense": 1.0,
        "recent_goals": [2, 2, 3, 1, 2]
    },
    "Inter": {
        "attack": 1.9,
        "defense": 0.8,
        "recent_goals": [1, 2, 2, 1, 3]
    },
    "Benfica": {
        "attack": 1.6,
        "defense": 1.2,
        "recent_goals": [1, 1, 2, 0, 1]
    },
    "PSG": {
        "attack": 2.2,
        "defense": 1.1,
        "recent_goals": [3, 2, 1, 2, 1]
    },
    "Newcastle": {
        "attack": 1.7,
        "defense": 1.3,
        "recent_goals": [1, 0, 1, 2, 1]
    },
    "Dortmund": {
        "attack": 1.8,
        "defense": 1.2,
        "recent_goals": [2, 1, 2, 1, 0]
    }
}

matches = load_matches("data/matches.csv")
correct, total, accuracy = backtest(matches, team_stats)

print("\n=== BACKTESTING PARTIDOS REALES ===")
print(f"Aciertos: {correct}/{total}")
print(f"Precisión: {accuracy:.2%}")