import csv
import os

folder = "data/historical"

for file in os.listdir(folder):

    path = os.path.join(folder, file)

    with open(path, encoding="latin1") as f:
        rows = list(csv.DictReader(f))

    print(file, "->", len(rows), "partidos")