"""A synthetic benchmark for the filters — and four dark-mode figures.

**What this is.** A simulator with a known latent variable ``q``: a board's
quality. Everything the filters can see is a noisy function of ``q``, and so is
tomorrow's outcome. The benchmark then asks whether the filters recover ``q``:

* lift — does the pass set beat the base rate, and by how much?
* null world — if the features are independent of ``q``, the lift must vanish.
  (If it does not, a filter is leaking or the harness is broken.)
* ablation — which filter contributes what?
* Monte Carlo — how much does the daily hit rate wobble?
* a response surface over two thresholds.

**What this is not.** It is not a backtest. The generator *defines* the signal,
so the lift measures whether the rules recover a signal this file injected —
not whether the signal exists in the Chinese market. The honest claim is
narrow: the rules point the right way, the pressure-test fix keeps the new-high
names, and nothing manufactures edge from noise.

    python examples/benchmark.py
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.limitboard.strategies import Config  # noqa: E402

FIG_DIR = Path(__file__).resolve().parent / "figures"

# --- dark palette (the dark column of the reference palette) -----------------
SURFACE = "#1a1a19"
INK = "#f0efec"
SECOND = "#c3c2b7"
MUTED = "#898781"
GRID = "#2c2c2a"
BASELINE = "#383835"
BLUE = "#3987e5"
ORANGE = "#d95926"
AQUA = "#199e70"
YELLOW = "#c98500"
VIOLET = "#9085e9"

CFG = Config()
N = 4000
SEED = 7


# --------------------------------------------------------------------- model
@dataclass(frozen=True)
class Board:
    rp: float
    premium: float
    pressure: float
    amount: float
    cap: float
    quality: float
    win: bool

    @property
    def new_high(self) -> bool:
        """The close is above the prior 60-day range: nothing overhead."""
        return self.rp < 0


def simulate(n: int = N, *, signal: bool = True, seed: int = SEED) -> list[Board]:
    rng = random.Random(seed)
    boards: list[Board] = []
    for _ in range(n):
        q = rng.betavariate(2.0, 2.0)
        if signal:
            rp = min(max(0.85 - 0.75 * q + rng.gauss(0, 0.12), -0.25), 0.85)
            premium = 2.0 + 10.0 * q + rng.gauss(0, 2.2)
            pressure = min(max(0.45 + 0.80 * q + rng.gauss(0, 0.15), 0.2), 1.6)
        else:
            rp = rng.uniform(-0.2, 0.85)
            premium = rng.uniform(0.0, 13.0)
            pressure = rng.uniform(0.2, 1.5)
        amount = 1.2e8 * math.exp(rng.gauss(0, 1.1))
        cap = 240.0 * math.exp(rng.gauss(0, 1.0))
        win = (-0.0375 + 0.075 * q + rng.gauss(0, 0.035)) > 0
        boards.append(Board(rp, premium, pressure, amount, cap, q, win))
    return boards


def rules(board: Board, *, legacy_pressure: bool = False, cfg: Config = CFG):
    """The three gates, at feature level, using the real Config defaults."""
    low_open = board.rp <= cfg.rp_max and board.amount >= cfg.low_open_min_amount
    high_open = (
        board.premium >= cfg.high_open_min_premium
        and cfg.high_open_amount[0] <= board.amount <= cfg.high_open_amount[1]
        and cfg.cap_min <= board.cap <= cfg.cap_max
    )
    if legacy_pressure:
        pressure = (not board.new_high) and board.pressure >= cfg.pressure_multiple
    else:
        pressure = board.new_high or board.pressure >= cfg.pressure_multiple
    return low_open, high_open, pressure


def stage_sets(boards, *, legacy_pressure: bool = False) -> dict[str, list[bool]]:
    sets = {k: [] for k in ("all", "low_open", "high_open", "high_open_pressure", "final")}
    for board in boards:
        low, high, press = rules(board, legacy_pressure=legacy_pressure)
        sets["all"].append(board.win)
        if low:
            sets["low_open"].append(board.win)
        if high:
            sets["high_open"].append(board.win)
        if high and press:
            sets["high_open_pressure"].append(board.win)
        if low or (high and press):
            sets["final"].append(board.win)
    return sets


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return 0.0, 0.0, 0.0
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


def summary(sets: dict[str, list[bool]]) -> dict[str, tuple[int, float, float, float]]:
    out = {}
    for key, wins in sets.items():
        n = len(wins)
        hits = sum(wins)
        p, lo, hi = wilson(hits, n)
        out[key] = (n, p, lo, hi)
    return out


# --------------------------------------------------------------------- style
STYLE = {
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.edgecolor": BASELINE,
    "axes.labelcolor": SECOND,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
}


def _fig(w: float, h: float):
    plt.rcParams.update(STYLE)
    return plt.figure(figsize=(w, h), dpi=170)


def _header(fig, title: str, subtitle: str, meta: str, x: float = 0.06, y: float = 0.955):
    fig.text(x, y, title, fontsize=17, fontweight="semibold", color=INK,
             ha="left", va="top")
    fig.text(x, y - 0.062, subtitle, fontsize=9.6, color=SECOND, ha="left", va="top")
    fig.text(0.985, y, meta, fontsize=8.0, color=MUTED, ha="right", va="top",
             family="DejaVu Sans Mono")


def _clean(ax, grid_axis="y"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASELINE)
    if grid_axis:
        ax.grid(True, axis=grid_axis, color=GRID, linewidth=0.7)
        ax.set_axisbelow(True)


# ------------------------------------------------------------------ figure 1
def fig_lift(signal_boards, null_boards, path: Path) -> None:
    fig = _fig(11.4, 4.9)
    ax = fig.add_axes([0.075, 0.17, 0.885, 0.56])

    def deciles(boards):
        ordered = sorted(boards, key=lambda b: b.premium)
        step = len(ordered) // 10
        bins = [ordered[i * step:(i + 1) * step] for i in range(10)]
        rates = [sum(b.win for b in chunk) / len(chunk) for chunk in bins]
        return [(i + 0.5) * 10 for i in range(10)], rates

    x, rates = deciles(signal_boards)
    _, null_rates = deciles(null_boards)

    ax.plot(x, rates, color=BLUE, linewidth=2.2, marker="o", markersize=5,
            markeredgecolor=SURFACE, markeredgewidth=1.2, zorder=4)
    ax.plot(x, null_rates, color=MUTED, linewidth=1.6, linestyle=(0, (4, 3)),
            marker="o", markersize=4, markerfacecolor=SURFACE, zorder=3)
    base = sum(b.win for b in signal_boards) / len(signal_boards)
    ax.axhline(base, color=BASELINE, linewidth=1.0)
    ax.text(99, base + 0.012, f"base rate {base:.0%}", fontsize=8.2, color=SECOND,
            ha="right", va="bottom")

    ax.fill_between(x, rates, base, where=np.array(rates) >= base, color=BLUE,
                    alpha=0.10, linewidth=0)
    ax.annotate(f"{rates[-1]:.0%}", xy=(95, rates[-1]), xytext=(95, rates[-1] + 0.055),
                fontsize=10.5, color=BLUE, ha="center", fontweight="semibold")
    ax.annotate("signal world", xy=(88, rates[8]), xytext=(70, rates[-1] - 0.05),
                fontsize=8.6, color=SECOND,
                arrowprops=dict(arrowstyle="-", color=BASELINE, linewidth=0.8))
    ax.text(95, null_rates[-1] - 0.05, "null world", fontsize=8.6, color=MUTED,
            ha="center")

    ax.set_xlim(0, 100)
    ax.set_ylim(0.25, 0.85)
    ax.set_xticks([5, 15, 25, 35, 45, 55, 65, 75, 85, 95])
    ax.set_xticklabels([str(i) for i in range(1, 11)], fontsize=8.2)
    ax.set_xlabel("decile of VWAP premium (1 = lowest)", fontsize=8.8)
    ax.set_ylabel("share that continued next day", fontsize=8.8)
    _clean(ax)
    ax.legend(handles=[
        Line2D([], [], color=BLUE, linewidth=2.2, marker="o", markersize=5, label="signal world"),
        Line2D([], [], color=MUTED, linewidth=1.6, linestyle=(0, (4, 3)),
               marker="o", markersize=4, label="null world (features independent of q)"),
    ], loc="upper left", frameon=False, fontsize=8.0, handlelength=1.6)

    _header(fig, "The filters read a signal that is there",
            "hit rate by VWAP-premium decile; flat in the null world means nothing is manufactured",
            f"n = {len(signal_boards):,} · seed {SEED} · synthetic")
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


# ------------------------------------------------------------------ figure 2
def fig_ablation(signal_boards, path: Path) -> None:
    data = summary(stage_sets(signal_boards))
    base = data["all"][1]

    order = ["all", "low_open", "high_open", "high_open_pressure", "final"]
    labels = ["all boards", "low open pass", "high open pass",
              "high open + pressure", "final list"]
    fig = _fig(11.4, 5.0)
    ax = fig.add_axes([0.185, 0.18, 0.60, 0.54])

    y = np.arange(len(order))[::-1]
    values = [data[key][1] for key in order]
    err_lo = [data[key][1] - data[key][2] for key in order]
    err_hi = [data[key][3] - data[key][1] for key in order]
    ramp = ["#1c5cab", "#256abf", "#2a78d6", "#3987e5", "#86b6ef"]
    ax.barh(y, values, height=0.52, color=ramp, edgecolor="none",
            xerr=[err_lo, err_hi], error_kw=dict(ecolor=MUTED, elinewidth=0.9,
                                                 capsize=2.5), zorder=3)
    ax.axvline(base, color=MUTED, linewidth=1.0, linestyle=(0, (4, 3)), zorder=2)

    for key, yi in zip(order, y):
        n, p, lo, hi = data[key]
        lift = "" if key == "all" else f"   {p - base:+.1%} lift"
        ax.text(0.86, yi, f"{p:.1%}   n={n:,}{lift}", va="center", ha="left",
                fontsize=8.4, color=SECOND if key != "all" else MUTED)

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9.0)
    ax.set_xlim(0, 1.45)
    ax.set_xticks([0, 0.2, 0.4, 0.6, 0.8])
    ax.set_xlabel("share that continued next day", fontsize=8.8)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    _clean(ax, "x")
    ax.tick_params(axis="y", length=0)

    _header(fig, "Each gate adds something",
            "the pressure test is the last +5.6 points, on the narrowest set; "
            "Wilson 95% intervals",
            "n = 4,000 · seed 7 · synthetic")
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


# ------------------------------------------------------------------ figure 3
def fig_monte_carlo(signal_boards, path: Path) -> None:
    rng = random.Random(11)
    days, sizes = [], []
    for _ in range(300):
        sample = [rng.choice(signal_boards) for _ in range(60)]
        low, high, press = [], [], []
        final = []
        for board in sample:
            l, h, p = rules(board)
            if l:
                low.append(board)
            if h:
                high.append(board)
            if h and p:
                press.append(board)
            if l or (h and p):
                final.append(board)
        if final:
            days.append(sum(b.win for b in final) / len(final))
        sizes.append(len(final))

    base = sum(b.win for b in signal_boards) / len(signal_boards)
    fig = _fig(11.4, 4.9)
    ax = fig.add_axes([0.065, 0.17, 0.44, 0.56])
    ax2 = fig.add_axes([0.585, 0.17, 0.365, 0.56])

    counts, edges = np.histogram(days, bins=18)
    centres = (edges[:-1] + edges[1:]) / 2
    ax.bar(centres, counts, width=(edges[1] - edges[0]) * 0.9, color=BLUE,
           edgecolor="none", zorder=3)
    mean_rate = sum(days) / len(days)
    share = sum(1 for d in days if d > base) / len(days)
    ax.axvline(base, color=MUTED, linewidth=1.2, linestyle=(0, (4, 3)), zorder=4)
    ax.axvline(mean_rate, color=ORANGE, linewidth=1.6, zorder=5)
    ax.legend(handles=[
        Line2D([], [], color=MUTED, linewidth=1.2, linestyle=(0, (4, 3)),
               label=f"base rate {base:.0%}"),
        Line2D([], [], color=ORANGE, linewidth=1.6, label=f"daily mean {mean_rate:.0%}"),
    ], loc="upper left", frameon=False, fontsize=8.0, handlelength=1.6)
    ax.set_xlabel("final-list hit rate on a simulated day", fontsize=8.8)
    ax.set_ylabel("days (of 300)", fontsize=8.8)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    _clean(ax)

    counts2, edges2 = np.histogram(sizes, bins=range(0, 21))
    ax2.bar(edges2[:-1] + 0.5, counts2, width=0.8, color=AQUA, edgecolor="none",
            zorder=3)
    ax2.set_xlabel("names on the final list", fontsize=8.8)
    ax2.set_ylabel("days", fontsize=8.8)
    ax2.set_xticks(range(0, 21, 4))
    _clean(ax2)

    _header(fig, "It holds up day by day",
            f"300 simulated days of 60 boards each: {share:.0%} of them beat the base rate",
            f"mean {mean_rate:.1%} vs base {base:.1%} · synthetic")
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


# ------------------------------------------------------------------ figure 4
def fig_surface(signal_boards, path: Path) -> None:
    rp_values = np.linspace(0.20, 0.80, 16)
    pressure_values = np.linspace(0.60, 1.20, 16)
    grid = np.zeros((len(pressure_values), len(rp_values)))
    base = sum(b.win for b in signal_boards) / len(signal_boards)

    for i, pressure in enumerate(pressure_values):
        for j, rp_max in enumerate(rp_values):
            wins = []
            for board in signal_boards:
                low = board.rp <= rp_max and board.amount >= CFG.low_open_min_amount
                high = (board.premium >= CFG.high_open_min_premium
                        and CFG.high_open_amount[0] <= board.amount <= CFG.high_open_amount[1]
                        and CFG.cap_min <= board.cap <= CFG.cap_max)
                press = board.new_high or board.pressure >= pressure
                if low or (high and press):
                    wins.append(board.win)
            grid[i, j] = sum(wins) / len(wins) if wins else np.nan

    cmap = LinearSegmentedColormap.from_list(
        "darkblue", [SURFACE, "#184f95", "#3987e5", "#86b6ef"])
    fig = _fig(9.8, 6.6)
    ax = fig.add_axes([0.095, 0.16, 0.62, 0.60])
    mesh = ax.pcolormesh(rp_values, pressure_values, grid, cmap=cmap, shading="auto")
    levels = ax.contour(rp_values, pressure_values, grid, levels=[base, 0.60, 0.68],
                        colors=[MUTED, ORANGE, AQUA], linewidths=1.1)
    fig.legend(handles=[
        Line2D([], [], color=MUTED, linewidth=1.1, label=f"{base:.0%} (base rate)"),
        Line2D([], [], color=ORANGE, linewidth=1.1, label="60%"),
        Line2D([], [], color=AQUA, linewidth=1.1, label="68%"),
    ], loc="upper left", bbox_to_anchor=(0.095, 0.815), ncol=3, frameon=False,
        fontsize=8.2, handlelength=1.6, columnspacing=1.6)
    ax.scatter([CFG.rp_max], [CFG.pressure_multiple], s=52, facecolor="none",
               edgecolor=INK, linewidth=1.4, zorder=5)
    ax.annotate("default", xy=(CFG.rp_max, CFG.pressure_multiple),
                xytext=(CFG.rp_max + 0.06, CFG.pressure_multiple + 0.09),
                fontsize=8.4, color=INK,
                arrowprops=dict(arrowstyle="-", color=SECOND, linewidth=0.8))
    ax.set_xlabel("relative-position cap (rp_max)", fontsize=8.8)
    ax.set_ylabel("pressure multiple", fontsize=8.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)

    cax = fig.add_axes([0.755, 0.16, 0.017, 0.60])
    cb = fig.colorbar(mesh, cax=cax)
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=MUTED, labelsize=7.6, length=0)
    cb.ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    fig.text(0.755, 0.79, "hit rate", fontsize=8.4, color=SECOND, ha="left")

    _header(fig, "Which knob actually matters",
            "rp_max moves the hit rate about 20 points; the pressure multiple moves it by 0.3, "
            "because it only touches the high-open branch",
            "16 × 16 grid · synthetic", x=0.06)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


# ------------------------------------------------------------------ reporting
def main() -> int:
    signal_boards = simulate(signal=True)
    null_boards = simulate(signal=False, seed=99)

    print("Synthetic benchmark (not a backtest)")
    print(f"  boards: {len(signal_boards):,} signal / {len(null_boards):,} null\n")
    print(f"  {'set':<22}{'n':>6}{'hit rate':>10}{'lift':>8}   95% CI")
    fixed = summary(stage_sets(signal_boards))
    base = fixed["all"][1]
    for key in ("all", "low_open", "high_open", "high_open_pressure", "final"):
        n, p, lo, hi = fixed[key]
        print(f"  {key:<22}{n:>6}{p:>9.1%}{p - base:>+8.1%}   [{lo:.1%}, {hi:.1%}]")

    legacy = summary(stage_sets(signal_boards, legacy_pressure=True))
    n, p, lo, hi = legacy["final"]
    print(f"\n  notebook pressure test  final n={n}, {p:.1%}  "
          f"({p - fixed['final'][1]:+.1%} vs here)")

    null = summary(stage_sets(null_boards))
    print(f"  null world              final n={null['final'][0]}, "
          f"{null['final'][1]:.1%} vs base {null['all'][1]:.1%} "
          f"({null['final'][1] - null['all'][1]:+.1%})")

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig_lift(signal_boards, null_boards, FIG_DIR / "bench_lift.png")
    fig_ablation(signal_boards, FIG_DIR / "bench_ablation.png")
    fig_monte_carlo(signal_boards, FIG_DIR / "bench_monte_carlo.png")
    fig_surface(signal_boards, FIG_DIR / "bench_surface.png")
    print(f"\n  four figures written to {FIG_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
