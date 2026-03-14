# ===============================
# VALUE BET DETECTOR
# ===============================

def calculate_value(probability, odds):

    prob = probability / 100

    value = (prob * odds) - 1

    return round(value, 3)


def detect_value_bets(prediction, odds):

    values = {}

    home_prob = prediction["home_win"]
    draw_prob = prediction["draw"]
    away_prob = prediction["away_win"]

    home_odds = odds.get("home")
    draw_odds = odds.get("draw")
    away_odds = odds.get("away")

    if home_odds:
        values["home"] = calculate_value(home_prob, home_odds)

    if draw_odds:
        values["draw"] = calculate_value(draw_prob, draw_odds)

    if away_odds:
        values["away"] = calculate_value(away_prob, away_odds)

    return values