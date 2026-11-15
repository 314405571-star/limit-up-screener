"""baostock adapter and CLI.

Everything that can fail over the network lives here; every decision about
what is a board lives in :mod:`limitboard.boards` and
:mod:`limitboard.strategies`.

Two differences from the notebook, on purpose:

* **One history fetch per stock.** The notebook asks for today's bar, then
  yesterday's bar *by date*, then a 5-minute series, then re-asks for the day
  again inside each filter — four to six round-trips per stock, and the
  previous-day lookup breaks on the first session after a holiday. Here one
  daily series answers all of it: ``history[-2]`` is the previous **traded**
  session, whatever the calendar did in between.
* **The 5-minute series is gone.** ``high >= limit price`` is the definition
  of "touched the board", and the daily bar already carries the high.

    python -m src.limitboard.baostock_source build-pool --out stock_list.csv
    python -m src.limitboard.baostock_source scan --pool stock_list.csv --date 2026-11-16
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from .boards import Bar, LimitRule
from .strategies import Cap, Config, Stock, screen

__all__ = ["BaoStockSource", "TradingCalendar", "run_screen", "build_pool_file", "main"]

log = logging.getLogger("limitboard")

FIELDS = "date,open,high,low,close,preclose,volume,amount,isST"
HISTORY_BARS = 150


# --------------------------------------------------------------------------- source
class BaoStockSource:
    def __init__(self, *, request_delay: float = 0.15, max_retries: int = 3) -> None:
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.logged_in = False
        self._bs = None

    @property
    def bs(self):
        if self._bs is None:
            import baostock as bs  # lazy: the core must import offline

            self._bs = bs
        return self._bs

    def login(self) -> bool:
        for attempt in range(self.max_retries):
            try:
                res = self.bs.login()
                if res.error_code == "0":
                    self.logged_in = True
                    today = datetime.now().strftime("%Y-%m-%d")
                    try:
                        self.bs.query_trade_dates(start_date=today, end_date=today)
                    except Exception:  # pragma: no cover - network
                        pass
                    return True
                log.warning("login failed: %s", res.error_msg)
            except Exception as exc:  # pragma: no cover - network
                log.warning("login raised: %s", exc)
            time.sleep(2 ** attempt)
        return False

    def logout(self) -> None:
        if self.logged_in:
            try:
                self.bs.logout()
            except Exception:  # pragma: no cover - network
                pass
            self.logged_in = False

    # -- data -------------------------------------------------------------
    def fetch_history(self, code: str, end_date: str, bars: int = HISTORY_BARS) -> list[Bar] | None:
        start = (datetime.strptime(end_date, "%Y-%m-%d")
                 - timedelta(days=int(bars * 1.6))).strftime("%Y-%m-%d")
        for attempt in range(self.max_retries):
            try:
                time.sleep(self.request_delay)
                rs = self.bs.query_history_k_data_plus(
                    code, FIELDS, start_date=start, end_date=end_date,
                    frequency="d", adjustflag="3",      # unadjusted: limit prices are too
                )
                if rs.error_code != "0":
                    log.debug("[%s] %s", code, rs.error_msg)
                    time.sleep(2 ** attempt)
                    continue
                rows: list[list[str]] = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                if not rows:
                    return None
                out: list[Bar] = []
                for row in rows:
                    try:
                        out.append(Bar(
                            date=row[0], open=float(row[1]), high=float(row[2]),
                            low=float(row[3]), close=float(row[4]),
                            preclose=float(row[5]), volume=float(row[6]),
                            amount=float(row[7]), is_st=(row[8] == "1"),
                        ))
                    except (TypeError, ValueError):
                        continue
                return out[-bars:] or None
            except Exception as exc:  # pragma: no cover - network
                log.warning("[%s] %s (retry %d)", code, type(exc).__name__, attempt + 1)
                time.sleep(2 ** attempt)
        return None

    def ipo_date(self, code: str) -> str | None:
        try:
            rs = self.bs.query_stock_basic(code=code)
            if rs.error_code == "0" and rs.next():
                return rs.get_row_data()[2] or None
        except Exception:  # pragma: no cover - network
            pass
        return None

    def shares(self, code: str, day: str) -> tuple[float, float] | None:
        """Total and float shares in 股, from the latest quarterly report."""
        today = datetime.strptime(day, "%Y-%m-%d")
        year, quarter = today.year, (today.month - 1) // 3 + 1
        for _ in range(8):
            try:
                rs = self.bs.query_profit_data(code=code, year=year, quarter=quarter)
                if rs.error_code == "0":
                    rows = []
                    while rs.next():
                        rows.append(rs.get_row_data())
                    if rows:
                        fields = list(rs.fields)
                        total = float(rows[-1][fields.index("totalShare")])
                        liqa = float(rows[-1][fields.index("liqaShare")])
                        return total * 1e4, liqa * 1e4
            except Exception:  # pragma: no cover - network
                return None
            quarter -= 1
            if quarter == 0:
                quarter, year = 4, year - 1
        return None

    def all_codes(self, day: str | None = None) -> list[tuple[str, str]]:
        day = day or datetime.now().strftime("%Y-%m-%d")
        rs = self.bs.query_all_stock(day=day)
        found: dict[str, str] = {}
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            code = row[0]
            if code.startswith(("sh.6", "sz.0", "sz.3", "bj.4", "bj.8")):
                found[code] = row[2] if len(row) > 2 else ""
        return sorted(found.items())


class TradingCalendar:
    """Open sessions from baostock, so "yesterday" means the last session."""

    def __init__(self, days: Iterable[str]) -> None:
        self.days = sorted(set(days))

    @classmethod
    def load(cls, source: BaoStockSource, start: str, end: str) -> "TradingCalendar":
        rs = source.bs.query_trade_dates(start_date=start, end_date=end)
        days = []
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if row[1] == "1":
                days.append(row[0])
        return cls(days)

    def previous(self, day: str) -> str | None:
        earlier = [d for d in self.days if d < day]
        return earlier[-1] if earlier else None

    def days_since(self, start: str, end: str) -> int:
        return sum(1 for d in self.days if start <= d <= end)


# --------------------------------------------------------------------------- cache
def _cache_path(cache_dir: Path, day: str) -> Path:
    return cache_dir / f"bars_{day}.csv"


def _load_cache(path: Path) -> dict[str, list[Bar]]:
    if not path.exists():
        return {}
    data: dict[str, list[Bar]] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            data.setdefault(row["code"], []).append(Bar(
                date=row["date"], open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                preclose=float(row["preclose"]), volume=float(row["volume"]),
                amount=float(row["amount"]), is_st=row["is_st"] == "1",
            ))
    return data


def _append_cache(path: Path, code: str, bars: Sequence[Bar]) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if new:
            writer.writerow(["code", "date", "open", "high", "low", "close",
                             "preclose", "volume", "amount", "is_st"])
        for bar in bars:
            writer.writerow([code, bar.date, bar.open, bar.high, bar.low, bar.close,
                             bar.preclose, bar.volume, bar.amount,
                             "1" if bar.is_st else "0"])


# --------------------------------------------------------------------------- run
def _read_pool(path: Path) -> list[tuple[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        code_col = "code" if "code" in cols else cols[0]
        name_col = "name" if "name" in cols else None
        out = []
        for row in reader:
            code = str(row.get(code_col, "")).strip()
            if code:
                out.append((code, str(row.get(name_col, "")).strip() if name_col else ""))
        return out


def _write(path: Path, rows: Sequence[dict]) -> None:
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _row(code: str, name: str, row, pool_label: str) -> dict:
    checks = row.checks
    passed = [k for k, c in checks.items() if c.passed]
    detail = " | ".join(
        f"{name}:{','.join(check.reasons) if check.reasons else 'pass'}"
        for name, check in checks.items()
    )
    metrics = {}
    for check in checks.values():
        metrics.update(check.metrics)
    return {
        "代码": code,
        "名称": name,
        "阶段": row.pool,
        "池": pool_label,
        "通过": ",".join(passed) or "-",
        "检查明细": detail or "-",
        "收盘价": row.metrics.get("close"),
        "成交额亿": round(row.metrics.get("amount", 0.0) / 1e8, 2),
        "相对位置": _round(metrics.get("relative_position")),
        "VWAP溢价%": _round(metrics.get("vwap_premium")),
        "三日涨幅%": _round(metrics.get("three_day_return")),
        "压力比": _round(metrics.get("pressure_ratio")),
        "压力判定": metrics.get("pressure_note", ""),
    }


def _round(value):
    return None if value is None else round(float(value), 3)


def run_screen(
    *,
    date_str: str,
    pool: str | Path,
    out_dir: str | Path = "output",
    cache_dir: str | Path = "cache",
    config: Config = Config(),
    limit: int | None = None,
    source: BaoStockSource | None = None,
) -> list[dict]:
    cache_dir, out_dir = Path(cache_dir), Path(out_dir)
    cache_file = _cache_path(cache_dir, date_str)

    universe = _read_pool(Path(pool))
    if limit:
        universe = universe[:limit]
    if not universe:
        log.warning("empty pool: %s", pool)
        return []

    source = source or BaoStockSource()
    if not source.login():
        log.error("login failed")
        return []
    try:
        cached = _load_cache(cache_file)
        todo = [(c, n) for c, n in universe if c not in cached]
        log.info("universe %d, cached %d, to fetch %d", len(universe), len(cached), len(todo))
        for i, (code, _name) in enumerate(todo, 1):
            bars = source.fetch_history(code, date_str)
            if bars:
                _append_cache(cache_file, code, bars)
                cached[code] = bars
            if i % 50 == 0:
                log.info("fetched %d/%d", i, len(todo))

        calendar = TradingCalendar.load(
            source, (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=60)).strftime("%Y-%m-%d"),
            date_str,
        )

        stocks: list[Stock] = []
        names: dict[str, str] = {}
        for code, name in universe:
            bars = cached.get(code)
            if not bars:
                continue
            names[code] = name
            listed = None
            ipo = source.ipo_date(code)
            if ipo:
                cutoff = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
                # only the first sessions matter, and the calendar covers them
                listed = calendar.days_since(ipo, date_str) if ipo >= cutoff else 999
            cap = None
            shares = source.shares(code, date_str)
            if shares:
                total, liqa = shares
                close = bars[-1].close
                cap = Cap(total=total * close / 1e8, float_=liqa * close / 1e8)
            stocks.append(Stock(code=code, name=name, history=tuple(bars),
                                listed_bars=listed, cap=cap))
    finally:
        source.logout()

    report = screen(stocks, config, LimitRule())
    log.info("funnel: %s", report.funnel)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    by_code = {row.code: row for row in report.rows}
    pools = report.pools
    _write(out_dir / f"首板池_{date_str}_{stamp}.csv",
           [_row(r.code, r.name, by_code[r.code], "首板") for r in pools.first_board])
    _write(out_dir / f"炸板池_{date_str}_{stamp}.csv",
           [_row(r.code, r.name, by_code[r.code], "炸板") for r in pools.exploded])
    _write(out_dir / f"全部候选_{date_str}_{stamp}.csv",
           [_row(row.code, row.name, row, "") for row in report.rows])

    final = [_row(by_code[c].code, by_code[c].name, by_code[c], "最终") for c in report.final]
    _write(out_dir / f"最终选股_{date_str}_{stamp}.csv", final)
    return final


def build_pool_file(path: str | Path, day: str | None = None) -> None:
    source = BaoStockSource()
    if not source.login():
        raise SystemExit("login failed")
    try:
        pairs = source.all_codes(day)
    finally:
        source.logout()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["code", "name"])
        writer.writerows(pairs)
    log.info("wrote %d codes to %s", len(pairs), path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="First-board (首板) screener")
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build-pool", help="write every listed A-share to CSV")
    build.add_argument("--out", default="stock_list.csv")
    build.add_argument("--day", default=None)

    scan = sub.add_parser("scan", help="screen one trading day")
    scan.add_argument("--pool", required=True)
    scan.add_argument("--date", default=None, help="YYYY-MM-DD (default: last session)")
    scan.add_argument("--out", default="output")
    scan.add_argument("--cache", default="cache")
    scan.add_argument("--limit", type=int, default=None)
    scan.add_argument("--rp-max", type=float, default=Config().rp_max)
    scan.add_argument("--pressure", type=float, default=Config().pressure_multiple)
    scan.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.cmd == "build-pool":
        build_pool_file(args.out, args.day)
        return 0

    day = args.date or datetime.now().strftime("%Y-%m-%d")
    config = Config(rp_max=args.rp_max, pressure_multiple=args.pressure)
    rows = run_screen(date_str=day, pool=args.pool, out_dir=args.out,
                      cache_dir=args.cache, config=config, limit=args.limit)
    for row in rows:
        print(f"{row['代码']} {row['名称']} {row['通过']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
