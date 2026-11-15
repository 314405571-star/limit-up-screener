"""End-to-end offline test of the adapter: cache, pools, date-stamped output.

A fake source stands in for baostock, so this runs with no network and no
baostock installed.

    python tests/test_pipeline.py
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.limitboard.boards import Bar, limit_price  # noqa: E402
from src.limitboard.baostock_source import run_screen  # noqa: E402

DAY = "2026-11-16"


def _series(closes, highs, lows, volumes):
    start = date(2026, 6, 1)
    out, prev = [], closes[0]
    for i, close in enumerate(closes):
        out.append(Bar(date=(start + timedelta(days=i)).isoformat(),
                       open=prev, high=highs[i], low=lows[i], close=close,
                       preclose=prev, volume=volumes[i], amount=close * volumes[i]))
        prev = close
    return out


class _FakeResult:
    error_code = "0"

    def __init__(self, rows):
        self._rows = list(rows)
        self._i = -1

    def next(self):
        self._i += 1
        return self._i < len(self._rows)

    def get_row_data(self):
        return self._rows[self._i]


class _FakeBS:
    def query_trade_dates(self, start_date=None, end_date=None):
        rows, day = [], date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        while day <= end:
            if day.weekday() < 5:
                rows.append([day.isoformat(), "1"])
            day += timedelta(days=1)
        return _FakeResult(rows)


class FakeSource:
    def __init__(self):
        self.fetches = 0
        self.bs = _FakeBS()
        base = [9.6, 9.8, 10.0, 10.2, 10.4] * 14          # 70 bars with a range
        closes = base + [limit_price(base[-1], "sh.600001")]
        first = _series(closes, [b + 0.1 for b in base] + [limit_price(base[-1], "sh.600001")],
                        [b - 0.1 for b in base] + [10.4], [1e7] * 70 + [4e7])
        exploded = _series(base + [10.4],
                           [b + 0.1 for b in base] + [limit_price(base[-1], "sh.600002")],
                           [b - 0.1 for b in base] + [10.1], [1e7] * 70 + [3e7])
        none = _series(base + [10.1], [b + 0.1 for b in base] + [10.15],
                       [b - 0.1 for b in base] + [9.95], [1e7] * 70 + [1e7])
        self.data = {"sh.600001": first, "sh.600002": exploded, "sh.600003": none}

    def login(self):
        return True

    def logout(self):
        return None

    def fetch_history(self, code, end_date, bars=150):
        self.fetches += 1
        return self.data[code]

    def ipo_date(self, code):
        return None

    def shares(self, code, day):
        return None


def test_pipeline_end_to_end():
    source = FakeSource()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pool = root / "pool.csv"
        with pool.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["code", "name"])
            writer.writerows([("sh.600001", "首板"), ("sh.600002", "炸板"),
                              ("sh.600003", "平盘")])

        rows = run_screen(date_str=DAY, pool=pool, out_dir=root / "out",
                          cache_dir=root / "cache", source=source)
        assert {r["代码"] for r in rows} == {"sh.600001", "sh.600002"}

        pool_file = next((root / "out").glob(f"首板池_{DAY}_*.csv"))
        with pool_file.open(encoding="utf-8-sig", newline="") as fh:
            first = list(csv.DictReader(fh))
        assert [r["代码"] for r in first] == ["sh.600001"]
        assert first[0]["池"] == "首板"

        exploded_file = next((root / "out").glob(f"炸板池_{DAY}_*.csv"))
        with exploded_file.open(encoding="utf-8-sig", newline="") as fh:
            exploded = list(csv.DictReader(fh))
        assert [r["代码"] for r in exploded] == ["sh.600002"]
        assert (root / "cache" / f"bars_{DAY}.csv").exists()

        before = source.fetches
        run_screen(date_str=DAY, pool=pool, out_dir=root / "out",
                   cache_dir=root / "cache", source=source)
        assert source.fetches == before, "cached bars must not be re-fetched"


def test_output_carries_the_trading_date():
    source = FakeSource()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pool = root / "pool.csv"
        pool.write_text("code,name\nsh.600001,首板\n", encoding="utf-8")
        run_screen(date_str=DAY, pool=pool, out_dir=root / "out",
                   cache_dir=root / "cache", source=source)
        names = [p.name for p in (root / "out").iterdir()]
        assert any(DAY in name for name in names), names


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
