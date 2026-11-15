"""Regression tests for limit-price arithmetic — one per defect.

Run with:  python tests/test_boards.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.limitboard.boards import (  # noqa: E402
    Bar,
    DayRecord,
    LimitRule,
    board_of,
    build_pools,
    classify_day,
    has_price_limit,
    legacy_limit_price,
    limit_price,
    limit_ratio,
)


def bar(close, *, high=None, low=None, open_=None, preclose=None, volume=1e6,
        amount=None, is_st=False, date="2026-11-16"):
    high = close if high is None else high
    low = close if low is None else low
    open_ = close if open_ is None else open_
    preclose = close if preclose is None else preclose
    amount = close * volume if amount is None else amount
    return Bar(date=date, open=open_, high=high, low=low, close=close,
               preclose=preclose, volume=volume, amount=amount, is_st=is_st)


# ----------------------------------------------------------------------- rules
def test_limit_price_uses_exchange_rounding():
    # 5.35 × 1.1 = 5.885: the exchange rounds half-up to 5.89, float round()
    # gives 5.88. A stock closing at 5.88 has *not* sealed its board.
    assert limit_price(5.35, "sh.600000") == 5.89
    assert legacy_limit_price(5.35, "sh.600000") == 5.88
    assert classify_day(bar(5.88, high=5.88, preclose=5.35), "sh.600000") == "none"
    # The notebook's lower limit calls it sealed.
    assert 5.88 >= legacy_limit_price(5.35, "sh.600000")


def test_growth_board_is_twenty_percent():
    assert limit_ratio("sz.300750") == 0.20
    assert limit_ratio("sh.688981") == 0.20
    assert limit_ratio("sh.600000") == 0.10
    assert limit_price(10.0, "sh.688981") == 12.0


def test_st_keeps_its_board_limit():
    # ST on ChiNext is 20%, not 5%. A +10% close is nowhere near the board.
    assert limit_ratio("sz.300001", is_st=True) == 0.20
    assert limit_price(5.0, "sz.300001", is_st=True) == 6.0
    assert classify_day(bar(5.50, high=5.50, preclose=5.0, is_st=True),
                        "sz.300001") == "none"
    # main-board ST really is 5%
    assert limit_ratio("sh.600001", is_st=True) == 0.05


def test_beijing_board_is_thirty_percent():
    assert board_of("bj.830799") == "bj"
    assert limit_ratio("bj.830799") == 0.30
    assert classify_day(bar(11.0, high=11.0, preclose=10.0), "bj.830799") == "none"
    assert classify_day(bar(13.0, high=13.0, preclose=10.0), "bj.830799") == "sealed"


def test_no_limit_window_for_new_listings():
    assert has_price_limit("sh.688981", listed_bars=3) is False
    assert limit_price(10.0, "sh.688981") == 12.0          # the rule still exists
    assert classify_day(bar(14.0, high=14.0, preclose=10.0), "sh.688981",
                        listed_bars=3) == "no_limit"
    assert classify_day(bar(10.5, high=10.5, preclose=10.0), "sh.600000",
                        listed_bars=1) == "no_limit"
    assert classify_day(bar(11.0, high=11.0, preclose=10.0), "sh.600000",
                        listed_bars=2) == "sealed"


def test_daily_high_is_enough_to_see_a_touch():
    # high == limit, close below: 炸板. No 5-minute series required.
    touched = bar(10.62, high=11.0, low=10.10, open_=10.30, preclose=10.0)
    assert classify_day(touched, "sh.600000") == "exploded"
    assert classify_day(bar(10.62, high=10.99, preclose=10.0), "sh.600000") == "none"


def test_missing_previous_bar_is_not_a_first_board():
    # The holiday case: no previous session in the data must not be read as
    # "was not limit-up yesterday".
    record = DayRecord("sh.600000", "x", "sealed", None)
    pools = build_pools([record])
    assert pools.first_board == []
    assert len(pools.unknown_prev) == 1

    ok = build_pools([DayRecord("sh.600000", "x", "sealed", False)])
    assert len(ok.first_board) == 1

    continued = build_pools([DayRecord("sh.600000", "x", "sealed", True)])
    assert continued.first_board == [] and len(continued.continued) == 1


def test_rule_is_configurable():
    rule = LimitRule(main=0.05)
    assert limit_price(10.0, "sh.600000", rule=rule) == 10.5


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
