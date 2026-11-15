"""The four demo figures. Matplotlib only, fixed data, no network."""

from __future__ import annotations

import math
import random
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from src.limitboard.boards import legacy_limit_price, limit_price  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
SECOND = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
AQUA = "#1baf7a"
YELLOW = "#eda100"

STYLE = {
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.edgecolor": BASELINE,
    "axes.labelcolor": SECOND,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": SECOND,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
}


def _figure(w, h):
    plt.rcParams.update(STYLE)
    return plt.figure(figsize=(w, h), dpi=170)


def _title(fig, title, subtitle, x=0.065, y=0.955):
    fig.text(x, y, title, fontsize=17, fontweight="semibold", color=INK,
             ha="left", va="top")
    fig.text(x, y - 0.062, subtitle, fontsize=9.8, color=SECOND, ha="left", va="top")


def _clean(ax, grid_axis="y"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    if grid_axis:
        ax.grid(True, axis=grid_axis, color=GRID, linewidth=0.7)
        ax.set_axisbelow(True)


# --------------------------------------------------------------------- figure 1
def intraday(path: Path) -> None:
    """Four intraday shapes; the daily bar already separates them."""
    limit, preclose = 11.00, 10.00
    n = 48
    rng = random.Random(7)

    def noisy(values, amp=0.012):
        return [min(v + rng.uniform(-amp, amp), limit) for v in values]

    early = [preclose + (limit - preclose) * min(i / 12, 1) for i in range(n)]
    late = [preclose + (limit - preclose) * min(max(i - 34, 0) / 8, 1) for i in range(n)]
    exploded = []
    for i in range(n):
        if i <= 18:
            exploded.append(preclose + (limit - preclose) * i / 18)
        else:
            drop = min((i - 18) / 12, 1)
            exploded.append(limit - (limit - 10.28) * drop)
    none = [preclose + 0.18 * i / (n - 1) for i in range(n)]

    fig = _figure(11.6, 4.9)
    ax = fig.add_axes([0.065, 0.24, 0.915, 0.55])
    series = [
        ("sealed · 10:30", noisy(early), BLUE),
        ("sealed · 14:20", noisy(late), ORANGE),
        ("exploded · 11:00", noisy(exploded), AQUA),
        ("never touched", noisy(none), MUTED),
    ]
    for label, values, color in series:
        ax.plot(range(n), values, color=color, linewidth=1.8, solid_capstyle="round")
    ax.axhline(limit, color=INK, linewidth=1.0, linestyle=(0, (4, 3)))
    ax.text(0.4, limit + 0.018, "limit 11.00", fontsize=8.2, color=SECOND, va="bottom")

    # direct labels at the right edge
    ax.text(n + 0.6, 11.06, "sealed (both)", fontsize=8.4, color=SECOND, va="center")
    ax.text(n + 0.6, 10.28, "exploded", fontsize=8.4, color=SECOND, va="center")
    ax.text(n + 0.6, 10.15, "never touched", fontsize=8.4, color=SECOND, va="center")

    ax.set_xlim(0, n + 7)
    ax.set_ylim(9.95, 11.15)
    ax.set_xticks([0, 12, 24, 36, 47])
    ax.set_xticklabels(["09:35", "10:35", "11:35", "14:05", "15:00"], fontsize=8.2)
    ax.set_ylabel("price", fontsize=8.6)
    _clean(ax)
    handles = [Line2D([], [], color=c, linewidth=2.0, label=l) for l, _, c in series]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.16),
              frameon=False, fontsize=8.0, handlelength=1.4, ncol=4)

    _title(fig, "Four intraday paths, two pools",
           "the daily high is enough: high = 11.00 means the board was touched")
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------- figure 2
def funnel(counts: dict, path: Path) -> None:
    rows = [
        ("all funds", counts["universe"], "start"),
        ("first board", counts["first_board"], "pool"),
        ("exploded", counts["exploded"], "pool"),
        ("low open pass", counts["low_open"], "filter"),
        ("high open + pressure", counts["high_open_pressure"], "filter"),
        ("weak-to-strong + pressure", counts["weak_pressure"], "filter"),
        ("final list", counts["final"], "final"),
    ]
    colors = {"start": MUTED, "pool": BLUE, "filter": AQUA, "final": "#104281"}
    fig = _figure(8.6, 4.6)
    ax = fig.add_axes([0.28, 0.14, 0.66, 0.60])
    y = list(range(len(rows)))[::-1]
    ax.barh(y, [r[1] for r in rows], height=0.62, edgecolor="none",
            color=[colors[r[2]] for r in rows])
    for yi, (_, value, _) in zip(y, rows):
        ax.text(value + 0.25, yi, str(value), va="center", ha="left",
                fontsize=9.2, color=SECOND)
    ax.set_yticks(y)
    ax.set_yticklabels([r[0] for r in rows], fontsize=8.8)
    ax.set_xlim(0, 20)
    ax.set_xticks([0, 5, 10, 15, 20])
    ax.set_xlabel("funds", fontsize=8.6)
    _clean(ax, "x")
    for side in ("left",):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="y", length=0)
    handles = [Patch(facecolor=colors[k], edgecolor="none", label=l) for k, l in
               (("pool", "in a pool"), ("filter", "passes a filter"), ("final", "final list"))]
    ax.legend(handles=handles, loc="lower right", frameon=False, fontsize=7.8,
              handlelength=1.1, handleheight=0.9)
    in_pool = counts["first_board"] + counts["exploded"]
    _title(fig, f"One day, {counts['universe']} funds",
           f"{in_pool} enter a pool, {counts['final']} survive the filters; "
           f"a fund may pass more than one",
           x=0.065)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------- figure 3
def pressure(bars, path: Path) -> None:
    tail = bars[-30:]
    n = len(tail)
    closes = [b.close for b in tail]
    volumes = [b.volume / 1e6 for b in tail[:-1]]
    window = range(n - 7, n - 2)                   # the five heavy sessions
    max_volume = max(volumes[n - 7:n - 2])
    threshold = max_volume * 0.9

    fig = _figure(10.6, 5.8)
    ax_p = fig.add_axes([0.085, 0.52, 0.885, 0.26])
    ax_v = fig.add_axes([0.085, 0.14, 0.885, 0.26], sharex=ax_p)

    ax_p.plot(range(n), closes, color=INK, linewidth=1.6)
    ax_p.axhline(tail[-2].high, color=ORANGE, linewidth=1.1, linestyle=(0, (4, 3)))
    ax_p.text(0.6, tail[-2].high + 0.05, "resistance = yesterday's high",
              fontsize=8.0, color=SECOND, va="bottom")
    ax_p.scatter([n - 8], [11.0], s=28, color=BLUE, zorder=5)
    ax_p.annotate("breakout", xy=(n - 8, 11.0), xytext=(1.2, 11.16),
                  fontsize=8.2, color=SECOND,
                  arrowprops=dict(arrowstyle="-", color=BASELINE, linewidth=0.8))
    ax_p.axvspan(n - 8.5, n - 2.5, color="#86b6ef", alpha=0.18, linewidth=0)
    ax_p.text(n - 5, 9.62, "5 sessions of supply", fontsize=8.0, color=SECOND,
              ha="center", va="bottom")
    ax_p.set_ylim(9.5, 11.32)
    ax_p.set_ylabel("price", fontsize=8.6)
    _clean(ax_p)
    ax_p.tick_params(labelbottom=False)

    ax_v.bar(range(n - 1), volumes, width=0.8, color=BASELINE, edgecolor="none")
    ax_v.bar(list(window), volumes[n - 7:n - 2], width=0.8, color=BLUE, edgecolor="none")
    ax_v.axhline(threshold, color=ORANGE, linewidth=1.1, linestyle=(0, (4, 3)))
    ax_v.text(0.6, threshold + 3, "0.9 × the window's max", fontsize=8.0, color=SECOND)
    ax_v.set_xlim(0, n + 0.2)
    ax_v.bar([n - 1.2], [76], width=0.42, color=AQUA, edgecolor="none")
    ax_v.scatter([n - 0.6], [55], s=34, facecolor=SURFACE, edgecolor=ORANGE,
                 linewidth=1.4, zorder=5)
    ax_v.legend(handles=[
        Patch(facecolor=AQUA, edgecolor="none", label="today 76 · pass"),
        Line2D([], [], marker="o", markersize=6, linestyle="none",
               markerfacecolor=SURFACE, markeredgecolor=ORANGE,
               label="today 55 · fail"),
    ], loc="upper right", frameon=False, fontsize=7.8, borderaxespad=0.2)
    ax_v.set_ylim(0, 112)
    ax_v.set_ylabel("volume, millions", fontsize=8.6)
    ax_v.set_xlabel("sessions (the last 30 of 98)", fontsize=8.6)
    ax_v.set_xticks([0, 10, 20, n - 1])
    ax_v.set_xticklabels(["D-29", "D-19", "D-9", "today"], fontsize=8.0)
    _clean(ax_v)

    _title(fig, "The pressure test",
           "today's volume must reach 90% of what changed hands after the last breakout",
           y=0.965)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------- figure 4
def rounding(path: Path) -> None:
    fig = _figure(11.2, 4.4)

    ax = fig.add_axes([0.075, 0.20, 0.36, 0.52])
    close = 5.88
    ax.axhline(legacy_limit_price(5.35, "sh.600000"), color=ORANGE, linewidth=1.1,
               linestyle=(0, (4, 3)))
    ax.scatter([0.16], [close], s=52, color=INK, zorder=5)
    ax.text(0.22, 5.8778, "close 5.88 = notebook limit → sealed", fontsize=8.4,
            color=SECOND, va="top")
    ax.axhline(limit_price(5.35, "sh.600000"), color=BLUE, linewidth=1.1,
               linestyle=(0, (4, 3)))
    ax.text(0.22, 5.8915, "exchange limit 5.89 → not sealed", fontsize=8.4,
            color=SECOND, va="bottom")
    ax.set_xlim(0, 1)
    ax.set_ylim(5.866, 5.896)
    ax.set_xticks([])
    ax.set_ylabel("price", fontsize=8.6)
    ax.set_title("pre-close 5.35, one cent apart", loc="left", fontsize=10.4,
                 fontweight="semibold", color=INK, pad=8)
    _clean(ax, None)
    ax.spines["bottom"].set_visible(False)

    ax2 = fig.add_axes([0.53, 0.20, 0.42, 0.52])
    buckets = list(range(5, 50, 5))
    counts = []
    for low in buckets:
        bad = 0
        for cents in range(low * 100, (low + 5) * 100):
            price = cents / 100
            if abs(round(price * 1.1, 2) - limit_price(price, "sh.600000")) > 1e-9:
                bad += 1
        counts.append(bad)
    ax2.bar(range(len(counts)), counts, width=0.62, color=BLUE, edgecolor="none")
    for i, value in enumerate(counts):
        ax2.text(i, value + 0.15, str(value), ha="center", fontsize=8.2, color=SECOND)
    ax2.set_xticks(range(len(counts)))
    ax2.set_xticklabels([f"{b}–{b + 5}" for b in buckets], fontsize=7.6)
    ax2.set_xlabel("pre-close price, ¥", fontsize=8.6)
    ax2.set_ylabel("prices rounding one cent low", fontsize=8.6)
    ax2.set_ylim(0, max(counts) + 2)
    ax2.set_title("every 5-yuan band has some", loc="left", fontsize=10.4,
                  fontweight="semibold", color=INK, pad=8)
    _clean(ax2)

    _title(fig, "Half-up, or half-to-even?",
           "1.2% of pre-close prices produce a board one cent below the exchange's",
           y=0.955)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
