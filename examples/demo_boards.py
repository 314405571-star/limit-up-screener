"""Offline demo: 18 synthetic A-share days, the pools, the filters, four figures.

No network, no baostock. Every bar is generated from a fixed seed, so the
output is identical on every machine and every number in the README can be
checked by running this file.

    python examples/demo_boards.py
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.limitboard.boards import (  # noqa: E402
    Bar,
    LimitRule,
    classify_day,
    legacy_limit_price,
    limit_price,
)
from src.limitboard.strategies import Cap, Config, Stock, screen  # noqa: E402

FIG_DIR = Path(__file__).resolve().parent / "figures"
TARGET = date(2026, 11, 16)


@dataclass
class Spec:
    code: str
    name: str
    kind: str                          # sealed | exploded | none | holiday | listing | st_drift
    low: float = 8.0
    high: float = 10.0
    prev: float | None = None
    premium: float = 4.0
    amount: float = 2.5e8
    volume: float | None = None
    cap: tuple[float, float] = (180.0, 220.0)   # (float, total) 亿
    listed: int = 300
    is_st: bool = False
    seed: int = 1
    target: date = TARGET
    note: str = ""


UNIVERSE = [
    Spec("sh.600001", "平稳首板", "sealed", 8.5, 10.0, 9.50, 3.0, 2.2e8, seed=1),
    Spec("sh.600002", "深跌反弹首板", "sealed", 5.0, 20.0, 10.0, 3.0, 3.0e8, seed=2),
    Spec("sh.600003", "高开首板", "sealed", 8.0, 10.2, 9.80, 8.0, 8.0e8, seed=3),
    Spec("sh.600004", "放量首板", "sealed", 8.0, 10.0, 9.90, 7.5, 6.5e8, seed=4),
    Spec("sh.600005", "缩量首板", "sealed", 8.0, 10.0, 9.90, 7.5, 6.0e8, seed=5),
    Spec("sh.600006", "新高首板", "sealed", 8.0, 10.0, 10.20, 9.0, 6.5e8, seed=6),
    Spec("sz.300007", "创业板ST", "st_drift", 4.5, 5.2, 5.00, 0.0, 2.0e8,
         cap=(120.0, 150.0), is_st=True, seed=7),
    Spec("sh.688008", "次新科创", "listing", 0.0, 0.0, None, 0.0, 9.0e8,
         cap=(60.0, 80.0), listed=3, seed=8),
    Spec("sz.300009", "炸板", "exploded", 8.0, 11.0, 10.0, 0.0, 5.0e8, seed=9),
    Spec("sh.600010", "节后连板", "holiday", 8.0, 10.0, 9.60, 3.0, 3.5e8,
         seed=10, target=date(2026, 10, 8)),
    Spec("sh.600011", "平盘", "none", 8.0, 10.0, 9.50, 0.0, 8.0e7, seed=11),
    Spec("sh.600012", "一分钱之差", "cent", 4.8, 5.3, 5.35, 3.0, 1.8e8, seed=12),
    Spec("sh.600013", "市值超限", "sealed", 8.0, 10.0, 9.90, 8.0, 9.0e8,
         cap=(600.0, 700.0), seed=13),
    Spec("sh.600014", "成交额不足", "sealed", 8.0, 10.0, 9.50, 3.0, 6.0e7, seed=14),
    Spec("sh.600015", "三连板", "continued", 8.0, 10.0, 9.90, 5.0, 4.0e8, seed=15),
    Spec("sz.300016", "炸板过热", "exploded", 8.0, 13.0, 11.0, 0.0, 5.0e8,
         cap=(150.0, 180.0), seed=16),
    Spec("sh.600017", "一字板", "one_word", 8.0, 10.0, 9.90, 0.0, 3.0e8, seed=17),
    Spec("sh.600018", "无量炸板", "exploded", 8.0, 10.0, 9.90, 0.0, 5.0e6,
         volume=4.5e5, seed=18),
]


# --------------------------------------------------------------------- builders
def prev_trading_day(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _trading_days(n: int, end: date) -> list[date]:
    """``n`` weekday dates ending on ``end`` (inclusive)."""
    days: list[date] = []
    day = end
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return list(reversed(days))


def _base(n: int, low: float, high: float, seed: int, end: date) -> list[Bar]:
    rng = random.Random(seed)
    bars: list[Bar] = []
    for i, day in enumerate(_trading_days(n, end)):
        mid = low + (high - low) * (0.5 + 0.36 * math.sin(i / 5.0 + seed))
        close = round(mid + rng.uniform(-0.03, 0.03) * (high - low), 2)
        prev_close = bars[-1].close if bars else close
        volume = 1.2e6
        bars.append(Bar(
            date=day.isoformat(),
            open=round(prev_close, 2), high=round(min(high, close * 1.012), 2),
            low=round(max(low, close * 0.988), 2), close=close,
            preclose=prev_close, volume=volume, amount=close * volume,
        ))
    return bars


def _day(bars: list[Bar], day: date, *, open_: float, high: float, low: float,
         close: float, volume: float, amount: float, is_st: bool = False) -> Bar:
    return Bar(date=day.isoformat(), open=round(open_, 2), high=round(high, 2),
               low=round(low, 2), close=round(close, 2),
               preclose=bars[-1].close, volume=volume, amount=amount, is_st=is_st)


def build(spec: Spec) -> list[Bar]:
    target = spec.target
    prev = prev_trading_day(target)

    if spec.kind == "listing":
        bars = _base(2, 8.0, 8.4, spec.seed, prev)
        close = round(bars[-1].close * 1.40, 2)
        bars.append(_day(bars, target, open_=bars[-1].close, high=close,
                         low=bars[-1].close, close=close, volume=4.8e7,
                         amount=spec.amount))
        return bars

    if spec.kind == "holiday":
        bars = _base(129, spec.low, spec.high, spec.seed, date(2026, 9, 30))
        sealed = limit_price(bars[-2].close, spec.code)
        bars[-1] = replace(bars[-1], close=sealed, high=sealed,
                           preclose=bars[-2].close)
        limit = limit_price(sealed, spec.code)
        vwap = round(limit / (1 + spec.premium / 100), 2)
        volume = spec.amount / vwap
        bars.append(_day(bars, target, open_=vwap, high=limit,
                         low=round(vwap * 0.998, 2), close=limit,
                         volume=volume, amount=spec.amount))
        return bars

    if spec.kind == "continued":
        bars = _base(129, spec.low, spec.high, spec.seed, prev_trading_day(prev))
        first = limit_price(bars[-1].close, spec.code)
        bars.append(_day(bars, prev, open_=bars[-1].close, high=first,
                         low=bars[-1].close, close=first, volume=3.0e6,
                         amount=first * 3.0e6))
        limit = limit_price(first, spec.code)
        vwap = round(limit / (1 + spec.premium / 100), 2)
        volume = spec.amount / vwap
        bars.append(_day(bars, target, open_=vwap, high=limit,
                         low=round(vwap * 0.998, 2), close=limit,
                         volume=volume, amount=spec.amount))
        return bars

    bars = _base(129, spec.low, spec.high, spec.seed, prev)

    if spec.prev is not None:
        preclose = round(spec.prev / 1.005, 2)
        bars[-2] = replace(bars[-2], close=preclose, high=max(bars[-2].high, preclose))
        bars[-1] = replace(bars[-1], close=spec.prev, preclose=preclose,
                           high=max(bars[-1].high, spec.prev),
                           low=min(bars[-1].low, spec.prev))

    limit = limit_price(bars[-1].close, spec.code, spec.is_st)

    if spec.kind == "none":
        close = round(bars[-1].close * 1.02, 2)
        bars.append(_day(bars, target, open_=bars[-1].close, high=close,
                         low=round(bars[-1].close * 0.995, 2), close=close,
                         volume=1.1e6, amount=close * 1.1e6))
        return bars

    if spec.kind == "st_drift":
        close = round(bars[-1].close * 1.10, 2)      # +10%: nowhere near 20%
        bars.append(_day(bars, target, open_=bars[-1].close, high=close,
                         low=bars[-1].close, close=close, volume=2.4e7,
                         amount=spec.amount, is_st=True))
        return bars

    if spec.kind == "cent":
        # yesterday 5.35 -> the board is 5.89; this closes at 5.88, one cent low
        close = legacy_limit_price(bars[-1].close, spec.code)
        bars.append(_day(bars, target, open_=round(bars[-1].close * 1.02, 2),
                         high=close, low=bars[-1].close, close=close,
                         volume=spec.amount / close, amount=spec.amount))
        return bars

    if spec.kind == "one_word":
        volume = spec.amount / limit
        bars.append(_day(bars, target, open_=limit, high=limit, low=limit,
                         close=limit, volume=volume, amount=spec.amount))
        return bars

    if spec.kind == "exploded":
        close = round(limit * 0.965, 2)
        vwap = round((limit + close + bars[-1].close) / 3, 2)
        volume = spec.volume or (spec.amount / vwap)
        bars.append(_day(bars, target, open_=round(bars[-1].close * 1.02, 2),
                         high=limit, low=round(bars[-1].close * 0.99, 2),
                         close=close, volume=volume, amount=spec.amount))
        return bars

    # sealed: today closes on the board
    vwap = limit if spec.premium <= 0 else round(limit / (1 + spec.premium / 100), 2)
    volume = spec.volume or (spec.amount / vwap)
    bars.append(_day(bars, target, open_=vwap, high=limit, low=round(vwap * 0.998, 2),
                     close=limit, volume=volume, amount=spec.amount))
    return bars


def pressure_bars(pass_: bool) -> list[Bar]:
    """One breakout, five heavy sessions, a quiet pullback, then today."""
    closes = [10.0] * 90 + [11.0] + [10.6] * 5 + [10.7, 11.77]
    highs = [10.0] * 90 + [11.1] + [10.65] * 5 + [10.75, 11.77]
    lows = [9.9] * 90 + [10.9] + [10.5] * 5 + [10.6, 10.95]
    volumes = [1.0e7] * 90 + [8.0e7] + [8.0e7] * 5 + [1.0e7, (7.6e7 if pass_ else 5.5e7)]
    days = _trading_days(len(closes), TARGET)
    bars: list[Bar] = []
    for i, close in enumerate(closes):
        preclose = closes[i - 1] if i else close
        bars.append(Bar(date=days[i].isoformat(), open=preclose, high=highs[i],
                        low=lows[i], close=close, preclose=preclose,
                        volume=volumes[i], amount=close * volumes[i]))
    vwap = round(11.77 / 1.075, 2)
    volume = volumes[-1]
    bars[-1] = Bar(date=TARGET.isoformat(), open=vwap, high=11.77,
                   low=round(vwap * 0.998, 2), close=11.77, preclose=10.7,
                   volume=volume, amount=volume * vwap)
    return bars


def legacy_pool(bars: list[Bar], code: str, is_st: bool = False) -> str:
    """The notebook's pool assignment, reproduced exactly (no previous bar ⇒ 首板)."""
    today = bars[-1]
    prev_date = (date.fromisoformat(today.date) - timedelta(days=1)).isoformat()
    while date.fromisoformat(prev_date).weekday() >= 5:
        prev_date = (date.fromisoformat(prev_date) - timedelta(days=1)).isoformat()
    by_date = {b.date: b for b in bars}
    prev_bar = by_date.get(prev_date)

    def sealed(b: Bar) -> bool:
        return b is not None and b.close >= legacy_limit_price(b.preclose, code, is_st)

    prev = sealed(prev_bar)
    today_sealed = sealed(today)
    if prev:
        return "continued"        # the notebook has no bucket; treated as out of scope
    if today_sealed:
        return "first_board"
    touched = today.high >= legacy_limit_price(today.preclose, code, is_st)
    return "exploded" if touched else "none"


# --------------------------------------------------------------------- reporting
def run():
    stocks: list[Stock] = []
    histories: dict[str, list[Bar]] = {}
    for spec in UNIVERSE:
        bars = build(spec)
        # attach the pressure profile to the two 首板 that exercise it
        if spec.code in ("sh.600004", "sh.600005"):
            pressure = pressure_bars(spec.code == "sh.600004")
            limit = limit_price(pressure[-2].close, spec.code)
            vwap = round(limit / 1.075, 2)
            volume = pressure[-1].volume
            pressure[-1] = Bar(date=TARGET.isoformat(), open=vwap, high=limit,
                               low=round(vwap * 0.998, 2), close=limit,
                               preclose=pressure[-2].close, volume=volume,
                               amount=volume * vwap)
            bars = pressure
        histories[spec.code] = bars
        stocks.append(Stock(code=spec.code, name=spec.name, history=tuple(bars),
                            listed_bars=spec.listed, cap=Cap(spec.cap[0], spec.cap[1])))

    report = screen(stocks, Config(), LimitRule())
    return report, histories


def print_funnel(funnel: dict) -> None:
    print("\nFUNNEL")
    print(f"  {'stage':<26}{'count':>7}")
    for key in ("universe", "no_data", "no_limit", "none", "continued", "unknown_prev",
                "first_board", "exploded", "low_open", "high_open", "high_open_pressure",
                "weak_to_strong", "weak_pressure", "final"):
        if key in funnel:
            print(f"  {key:<26}{funnel[key]:>7}")


def print_rows(report) -> None:
    print("\nPOOLS AND FILTERS")
    header = (f"  {'code':<11}{'name':<14}{'state':<9}{'pool':<12}"
              f"{'low_open':<26}{'high_open+pressure':<26}{'weak+pressure':<20}")
    print(header)
    for row in report.rows:
        def cell(key: str) -> str:
            check = row.checks.get(key)
            if check is None:
                return "-"
            return "pass" if check.passed else ",".join(check.reasons)

        def combined(base: str) -> str:
            text = cell(base)
            if base + "_pressure" in row.checks:
                text += " / press=" + ("pass" if row.checks[base + "_pressure"].passed else "fail")
            return text
        print(f"  {row.code:<11}{row.name:<14}{row.state:<9}{row.pool:<12}"
              f"{cell('low_open'):<26}{combined('high_open'):<28}{combined('weak_to_strong'):<24}")


def print_legacy_compare(histories) -> None:
    print("\nNOTEBOOK vs THIS REPO (pool assignment)")
    print(f"  {'code':<11}{'name':<16}{'notebook':<14}{'here':<14}why")
    report_ = screen([Stock(code=s.code, name=s.name, history=tuple(histories[s.code]),
                            listed_bars=s.listed, cap=Cap(s.cap[0], s.cap[1]))
                      for s in UNIVERSE], Config(), LimitRule())
    here = {row.code: row for row in report_.rows}
    diff = 0
    for spec in UNIVERSE:
        bars = histories[spec.code]
        old = legacy_pool(bars, spec.code, spec.is_st)
        new = here[spec.code].pool
        if old != new:
            diff += 1
            why = {
                "sz.300007": "ST on ChiNext is 20%, not 5%",
                "sh.688008": "first five sessions have no limit",
                "sh.600010": "previous session is before the holiday",
                "sh.600012": "limit is 5.89 (half-up), not 5.88",
                "sh.600015": "yesterday sealed -> continuation",
            }.get(spec.code, "")
            print(f"  {spec.code:<11}{spec.name:<16}{old:<14}{new:<14}{why}")
    print(f"\n  {diff} of {len(UNIVERSE)} funds are classified differently")


def main() -> int:
    report, histories = run()
    from examples import figures
    print("First-board screener — offline demo")
    print(f"  universe        {len(UNIVERSE)} synthetic stocks")
    print(f"  analysis day    {TARGET.isoformat()}")
    print_funnel(report.funnel)
    print_rows(report)
    print_legacy_compare(histories)
    print(f"\n  final list: {', '.join(report.final) or '(empty)'}")

    row = next(r for r in report.rows if r.code == "sh.600006")
    check = row.checks.get("high_open_pressure")
    print("\n  pressure test on sh.600006 (a new high, no overhead supply):")
    print("    notebook: idx is None -> rejected")
    print(f"    here:     {check.metrics.get('pressure_note')} -> "
          f"{'pass' if check.passed else 'fail'}")

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    figures.intraday(FIG_DIR / "intraday_pools.png")
    figures.funnel(report.funnel, FIG_DIR / "funnel.png")
    figures.pressure(histories["sh.600004"], FIG_DIR / "pressure_test.png")
    figures.rounding(FIG_DIR / "rounding.png")
    print(f"\n  four figures written to {FIG_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
