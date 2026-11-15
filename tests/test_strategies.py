"""Regression tests for the entry filters and the pressure test.

Run with:  python tests/test_strategies.py
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.limitboard.boards import Bar, limit_price  # noqa: E402
from src.limitboard.strategies import (  # noqa: E402
    Cap,
    Config,
    Stock,
    pressure_test,
    relative_position,
    return_over,
    screen,
    strategy_high_open,
    strategy_weak_to_strong,
    vwap_premium,
)


def make_bars(closes, *, volumes=None, amounts=None, highs=None, lows=None,
              start=date(2026, 6, 1)):
    out, day = [], start
    for i, close in enumerate(closes):
        preclose = closes[i - 1] if i else close
        high = highs[i] if highs else max(close, preclose)
        low = lows[i] if lows else min(close, preclose)
        volume = volumes[i] if volumes else 1e6
        amount = amounts[i] if amounts else close * volume
        out.append(Bar(date=day.isoformat(), open=preclose, high=high, low=low,
                       close=close, preclose=preclose, volume=volume, amount=amount))
        day += timedelta(days=1)
    return out


# --------------------------------------------------------------------- measures
def test_relative_position_uses_the_prior_range():
    # 60 flat bars between 10 and 20, then a board at 22 — above the prior high.
    bars = make_bars([15.0] * 60 + [22.0], highs=[20.0] * 60 + [22.0],
                     lows=[10.0] * 60 + [21.0])
    rp = relative_position(bars, 22.0, lookback=60)
    assert rp is not None and rp < 0            # above the prior range
    incl = relative_position(bars, 22.0, lookback=60, exclude_today=False)
    assert incl == 0.0                          # today's own high defines the top


def test_three_day_return_uses_four_closes():
    closes = [9.0, 10.0, 10.0, 11.0]
    bars = make_bars(closes)
    assert abs(return_over(bars, 3) - (11.0 / 9.0 - 1) * 100) < 1e-9
    # three closes would be a two-day return
    assert abs((closes[-1] / closes[-3] - 1) * 100 - 10.0) < 1e-9


def test_vwap_premium_is_close_over_vwap():
    # 1.0m shares for 10.0m yuan → VWAP 10.0, close 10.5 → +5%
    bars = make_bars([10.0, 10.5], volumes=[1e6, 1e6], amounts=[10e6, 10e6])
    assert abs(vwap_premium(bars[-1]) - 5.0) < 1e-9


# ------------------------------------------------------------------- pressure
def test_pressure_passes_a_new_high():
    # Nothing in the prior window ever closed above yesterday's high: there is
    # no overhead supply. The notebook's `if idx is None: continue` rejects it.
    bars = make_bars([10.0] * 20 + [11.0, 10.8],
                     highs=[10.0] * 20 + [11.2, 11.3],
                     lows=[9.8] * 20 + [10.6, 10.7],
                     volumes=[1e6] * 20 + [3e6, 9e5])
    check = pressure_test(bars)
    assert check.passed and check.metrics["pressure_note"] == "new_high"


def test_pressure_requires_ninety_percent_of_the_breakout_volume():
    # Breakout at 11.0, five sessions of 4.0m shares above the old range,
    # then yesterday closes at 10.7 (high 10.75) below the breakout.
    closes = [10.0] * 10 + [9.9] * 5 + [11.0] + [10.6] * 5 + [10.7, 10.8]
    highs = [10.0] * 10 + [9.95] * 5 + [11.1] + [10.65] * 5 + [10.75, 10.85]
    lows = [9.9] * 10 + [9.85] * 5 + [10.9] + [10.5] * 5 + [10.6, 10.7]
    volumes = [1e6] * 10 + [1e6] * 5 + [4e6] + [4e6] * 5 + [1e6, 3.6e6]
    check = pressure_test(make_bars(closes, highs=highs, lows=lows, volumes=volumes))
    assert check.metrics["pressure_note"] == "measured"
    assert abs(check.metrics["pressure_ratio"] - 0.9) < 1e-9
    assert check.passed

    weak = volumes[:-1] + [3.0e6]                            # 0.75 of the max
    assert not pressure_test(make_bars(closes, highs=highs, lows=lows,
                                       volumes=weak)).passed


# --------------------------------------------------------------------- filters
def test_market_cap_band_uses_one_measure():
    # A big total cap with a tiny free float: 600亿 total, 50亿 float.
    closes = [10.0] * 70 + [11.0]
    bars = make_bars(closes)
    bars[-1] = Bar(date="2026-11-16", open=10.2, high=11.0, low=10.1, close=11.0,
                   preclose=10.0, volume=1e6, amount=6e8)
    check = strategy_high_open(bars, Config(), Cap(total=600.0, float_=50.0))
    assert "market_cap" in check.reasons
    # The notebook's rule is `total < 70 or float > 520`, so it lets this through.
    legacy_rejects = 600.0 < 70 or 50.0 > 520
    assert legacy_rejects is False


def test_weak_to_strong_rejects_an_extended_run():
    closes = [10.0] * 70 + [10.2, 10.8, 12.0]
    bars = make_bars(closes, volumes=[1e6] * 72 + [5e6],
                     amounts=[10e6] * 72 + [6e8])
    bars[-1] = Bar(date="2026-11-16", open=12.0, high=12.9, low=12.0, close=12.9,
                   preclose=12.0, volume=4.84e7, amount=6e8)
    check = strategy_weak_to_strong(bars, Config(), Cap(total=200, float_=150))
    assert not check.passed
    assert "three_day_return" in check.reasons     # +29% over three sessions
    assert check.metrics["candle_body"] > 7.0      # the candle itself is fine


def test_high_open_needs_a_seven_percent_premium():
    closes = [10.0] * 70 + [11.0]
    bars = make_bars(closes, volumes=[1e6] * 71,
                     amounts=[10e6] * 70 + [11.0 * 1e6])
    check = strategy_high_open(bars, Config(), Cap(total=200, float_=150))
    assert not check.passed and "vwap_premium" in check.reasons


# ---------------------------------------------------------------------- screen
def test_screen_keeps_a_continuation_out_of_the_first_board_pool():
    closes = [10.0] * 68 + [11.0, 12.1]
    highs = [10.0] * 68 + [11.0, 12.1]
    lows = [9.9] * 68 + [10.5, 11.2]
    bars = make_bars(closes, highs=highs, lows=lows,
                     volumes=[1e6] * 69 + [2e6])
    stock = Stock(code="sh.600000", name="连板", history=tuple(bars),
                  listed_bars=200, cap=Cap(200, 150))
    report = screen([stock])
    row = report.rows[0]
    assert row.state == "sealed" and row.prev_sealed is True
    assert row.pool == "continued"
    assert report.pools.first_board == []


def test_screen_builds_both_pools():
    # A first board and an exploded board on the same day.
    base = [10.0] * 68
    first = make_bars(base + [10.9, 11.99], highs=base + [10.95, 12.0],
                      lows=base + [10.8, 11.5], volumes=[1e6] * 69 + [4e6])
    # 11.99 ≈ 10.9 × 1.1 rounded; use the helper to be exact
    close = limit_price(10.9, "sh.600001")
    first[-1] = Bar(date="2026-11-16", open=11.2, high=close, low=11.1, close=close,
                    preclose=10.9, volume=4e6, amount=close * 4e6 * 0.97)
    exploded = make_bars(base + [10.9, 11.0], highs=base + [10.95, 12.0],
                         lows=base + [10.8, 10.4], volumes=[1e6] * 69 + [4e6])
    exploded[-1] = Bar(date="2026-11-16", open=11.2, high=limit_price(10.9, "sh.600002"),
                       low=10.4, close=10.8, preclose=10.9, volume=4e6,
                       amount=10.8 * 4e6)
    stocks = [
        Stock("sh.600001", "首板", tuple(first), 200, Cap(200, 150)),
        Stock("sh.600002", "炸板", tuple(exploded), 200, Cap(200, 150)),
    ]
    report = screen(stocks)
    assert report.funnel["first_board"] == 1
    assert report.funnel["exploded"] == 1
    assert {r.pool for r in report.rows} == {"first_board", "exploded"}


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
