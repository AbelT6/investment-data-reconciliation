"""
make_chart.py
Draws output/breaks_by_type.png from output/score.csv: planted vs. caught for
each break type. Reads existing results only; it does not re-run any checks.
Run it after score.py (or after run_all.py).
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"

score = pd.read_csv(OUTPUT / "score.csv").sort_values("planted")
labels = score.break_type.str.replace("_", " ").str.capitalize().str.replace("Id ", "ID ")
planted, caught = score.planted.sum(), score.caught.sum()
false_pos = score.false_positives.sum()

INK, MUTED, GRID = "#1f1f1e", "#6b6a66", "#e6e5e1"
PLANTED, CAUGHT = "#c9c8c3", "#2a78d6"

fig, ax = plt.subplots(figsize=(8, 4.6), facecolor="white")
y = range(len(score))
h = 0.36
ax.barh([i + h / 2 + 0.02 for i in y], score.planted, height=h, color=PLANTED, label="Planted")
ax.barh([i - h / 2 - 0.02 for i in y], score.caught, height=h, color=CAUGHT, label="Caught")
for i, (p, c) in enumerate(zip(score.planted, score.caught)):
    ax.text(c + 0.1, i - h / 2 - 0.02, f"{c}", va="center", fontsize=9, color=INK)

ax.set_yticks(list(y), labels, fontsize=10, color=INK)
ax.set_xlabel("Breaks", color=MUTED)
ax.xaxis.grid(True, color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
for side in ("top", "right", "left"):
    ax.spines[side].set_visible(False)
ax.spines["bottom"].set_color(GRID)
ax.tick_params(colors=MUTED, length=0)
ax.legend(loc="lower right", frameon=False, fontsize=9)
ax.set_title(f"{caught} of {planted} planted breaks caught, {false_pos} false positives",
             loc="left", fontsize=12, color=INK, pad=12)

fig.tight_layout()
out = OUTPUT / "breaks_by_type.png"
fig.savefig(out, dpi=150)
print(f"Wrote {out}")
