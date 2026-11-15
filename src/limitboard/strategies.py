"""The three entry filters and the pressure test — pure, standard library only.

    first board  → 低开 (relative position)  and 高开 (VWAP premium)
    exploded     → 弱转强 (3-day return, candle body, premium)
    both         → pressure test (volume vs the last breakout)

Each filter returns a :class:`Check` with the *names of the checks it failed*,
so a run can say "this one died on the market-cap band" instead of dropping it
into the void.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .boards import Bar, DayRecord, LimitRule, Pools, build_pools, classify_day

__all__ = [
    "Config",
    "Cap",
    "Stock",
    "Check",
    "Row",
    "Report",
    "relative_position",
    "vwap_premium",
    "return_over",
    "pressure_test",
    "strategy_low_open",
    "strategy_high_open",
    "strategy_weak_to_strong",
    "screen",
]


@dataclass(frozen=True)
class Config:
    #: 首板低开: where today's close sits inside the *prior* 60-day range.
    rp_lookback: int = 60
    rp_max: float = 0.50
    low_open_min_amount: float = 1e8

    #: 首板高开: close premium over the day's VWAP.
    high_open_min_premium: float = 7.0
    high_open_amount: tuple[float, float] = (5.5e8, 20e8)

    #: 弱转强
    weak_days: int = 3
    weak_max_return: float = 28.0
    weak_min_entity: float = -5.0
    weak_min_premium: float = -4.0
    weak_amount: tuple[float, float] = (3e8, 19e8)

    #: Market-cap band, in 亿元, applied to one basis — not one bound each.
    cap_min: float = 70.0
    cap_max: float = 520.0
    cap_basis: str = "float"       # "float" (流通市值) or "total" (总市值)

    #: Pressure test
    pressure_multiple: float = 0.90
    pressure_window: int = 5

    min_history: int = 61


@dataclass(frozen=True)
class Cap:
    total: float | None = None     # 总市值, 亿元
    float_: float | None = None    # 流通市值, 亿元


@dataclass(frozen=True)
class Stock:
    code: str
    name: str = ""
    history: tuple[Bar, ...] = ()       # ascending, ending on the analysis day
    listed_bars: int | None = None      # bars since listing, None = unknown
    cap: Cap | None = None


@dataclass(frozen=True)
class Check:
    passed: bool
    reasons: tuple[str, ...] = ()
    metrics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Row:
    code: str
    name: str
    state: str
    prev_sealed: bool | None
    pool: str
    checks: dict = field(default_factory=dict)     # strategy name → Check
    metrics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    rows: tuple[Row, ...]
    pools: Pools
    funnel: dict
    final: tuple[str, ...]


# --------------------------------------------------------------------------- metrics
def relative_position(
    bars: Sequence[Bar], close: float, *, lookback: int = 60, exclude_today: bool = True
) -> float | None:
    """Where ``close`` sits in the high-low range, 0 at the high, 1 at the low.

    ``exclude_today=True`` uses the ``lookback`` bars *before* today. The
    notebook includes today, and today's bar is the limit-up bar: for a sealed
    board ``high == close == limit``, so the high of the window *is* today's
    close and the ratio is exactly ``0`` — the filter can never reject anything.
    """
    need = lookback + 1 if exclude_today else lookback
    if len(bars) < need:
        return None
    window = bars[-need:-1] if exclude_today else bars[-lookback:]
    high = max(b.high for b in window)
    low = min(b.low for b in window)
    if high <= low:
        return 1.0
    return (high - close) / (high - low)


def vwap_premium(bar: Bar) -> float | None:
    """Close vs the day's turnover-weighted average price, in percent.

    The notebook calls this "获利比例" (the share of holders in profit). It is
    not; it is the day's closing premium over VWAP, and the two mean very
    different things.
    """
    if bar.volume <= 0 or bar.amount <= 0:
        return None
    vwap = bar.amount / bar.volume
    if vwap <= 0:
        return None
    return (bar.close / vwap - 1.0) * 100.0


def return_over(bars: Sequence[Bar], days: int) -> float | None:
    """Return over exactly ``days`` sessions, in percent."""
    if days <= 0 or len(bars) < days + 1:
        return None
    base = bars[-days - 1].close
    if base <= 0:
        return None
    return (bars[-1].close / base - 1.0) * 100.0


def _cap_ok(cap: Cap | None, cfg: Config) -> bool | None:
    """True / False / None (unknown)."""
    if cap is None:
        return None
    value = cap.float_ if cfg.cap_basis == "float" else cap.total
    if value is None:
        return None
    return cfg.cap_min <= value <= cfg.cap_max


# --------------------------------------------------------------------------- strategies
def strategy_low_open(bars: Sequence[Bar], cfg: Config = Config()) -> Check:
    reasons: list[str] = []
    rp = relative_position(bars, bars[-1].close, lookback=cfg.rp_lookback) if bars else None
    if rp is None:
        reasons.append("insufficient_history")
    elif rp > cfg.rp_max:
        reasons.append("relative_position")
    amount = bars[-1].amount if bars else 0.0
    if amount < cfg.low_open_min_amount:
        reasons.append("amount")
    return Check(not reasons, tuple(reasons), {"relative_position": rp, "amount": amount})


def strategy_high_open(
    bars: Sequence[Bar], cfg: Config = Config(), cap: Cap | None = None
) -> Check:
    reasons: list[str] = []
    premium = vwap_premium(bars[-1]) if bars else None
    if premium is None or premium < cfg.high_open_min_premium:
        reasons.append("vwap_premium")
    amount = bars[-1].amount if bars else 0.0
    low, high = cfg.high_open_amount
    if not (low <= amount <= high):
        reasons.append("amount")
    cap_ok = _cap_ok(cap, cfg)
    if cap_ok is False:
        reasons.append("market_cap")
    return Check(not reasons, tuple(reasons),
                 {"vwap_premium": premium, "amount": amount, "cap_ok": cap_ok})


def strategy_weak_to_strong(
    bars: Sequence[Bar], cfg: Config = Config(), cap: Cap | None = None
) -> Check:
    reasons: list[str] = []
    ret = return_over(bars, cfg.weak_days)
    if ret is None:
        reasons.append("insufficient_history")
    elif ret > cfg.weak_max_return:
        reasons.append("three_day_return")
    bar = bars[-1] if bars else None
    entity = (bar.close / bar.open - 1.0) * 100.0 if bar and bar.open > 0 else None
    if entity is None or entity < cfg.weak_min_entity:
        reasons.append("candle_body")
    premium = vwap_premium(bar) if bar else None
    if premium is None or premium < cfg.weak_min_premium:
        reasons.append("vwap_premium")
    amount = bar.amount if bar else 0.0
    low, high = cfg.weak_amount
    if not (low <= amount <= high):
        reasons.append("amount")
    cap_ok = _cap_ok(cap, cfg)
    if cap_ok is False:
        reasons.append("market_cap")
    return Check(not reasons, tuple(reasons),
                 {"three_day_return": ret, "candle_body": entity,
                  "vwap_premium": premium, "amount": amount, "cap_ok": cap_ok})


def pressure_test(bars: Sequence[Bar], cfg: Config = Config()) -> Check:
    """Today's volume against the supply that changed hands on the last breakout.

    Resistance is yesterday's high. Find the most recent earlier session that
    closed above it, take the largest volume in the following five sessions,
    and demand today's volume be at least ``multiple`` of it.

    Two cases the notebook gets backwards:

    * **No prior close above the resistance** (the stock is at a new high).
      It does ``continue`` — the strongest breakouts, the ones with no overhead
      supply at all, are silently rejected. Here they pass, labelled ``new_high``.
    * **The breakout was yesterday** so the observation window is empty.
      Also rejected there; here it passes as ``fresh_breakout``.
    """
    if len(bars) < 2:
        return Check(False, ("insufficient_history",), {"pressure_ratio": None})
    resistance = bars[-2].high
    prior = bars[:-2]
    index = None
    for i in range(len(prior) - 1, -1, -1):
        if prior[i].close > resistance:
            index = i
            break

    today_volume = bars[-1].volume
    if index is None:
        return Check(True, (), {"pressure_ratio": None, "pressure_note": "new_high"})

    window = prior[index + 1: index + 1 + cfg.pressure_window]
    if not window:
        return Check(True, (), {"pressure_ratio": None, "pressure_note": "fresh_breakout"})

    max_volume = max(b.volume for b in window)
    if max_volume <= 0:
        return Check(True, (), {"pressure_ratio": None, "pressure_note": "no_volume"})

    ratio = today_volume / max_volume
    passed = ratio >= cfg.pressure_multiple
    reasons = () if passed else ("volume_below_pressure",)
    return Check(passed, reasons,
                 {"pressure_ratio": ratio, "pressure_volume": max_volume,
                  "pressure_note": "measured"})


# --------------------------------------------------------------------------- orchestration
def screen(
    stocks: Iterable[Stock],
    cfg: Config = Config(),
    rule: LimitRule = LimitRule(),
) -> Report:
    """Run the pools, the three strategies and the pressure test for one day."""
    records: list[DayRecord] = []
    rows: dict[str, Row] = {}
    funnel = {
        "universe": 0, "no_data": 0, "no_limit": 0, "none": 0,
        "continued": 0, "unknown_prev": 0, "first_board": 0, "exploded": 0,
        "low_open": 0, "high_open": 0, "high_open_pressure": 0,
        "weak_to_strong": 0, "weak_pressure": 0, "final": 0,
    }

    for stock in stocks:
        funnel["universe"] += 1
        history = tuple(sorted(stock.history, key=lambda b: b.date))
        if not history:
            records.append(DayRecord(stock.code, stock.name, "no_data", None))
            funnel["no_data"] += 1
            continue

        today = history[-1]
        state = classify_day(today, stock.code, listed_bars=stock.listed_bars, rule=rule)

        prev_sealed: bool | None = None
        if len(history) >= 2:
            prev_listed = None if stock.listed_bars is None else stock.listed_bars - 1
            if prev_listed is not None and prev_listed < 1:
                prev_sealed = False
            else:
                prev_sealed = classify_day(
                    history[-2], stock.code, listed_bars=prev_listed, rule=rule
                ) == "sealed"

        record = DayRecord(stock.code, stock.name, state, prev_sealed)
        records.append(record)

        pool = "none"
        if state == "no_data":
            pool = "no_data"
        elif state == "no_limit":
            pool = "no_limit"
        elif state not in ("sealed", "exploded"):
            pool = "none"
        elif prev_sealed is None:
            pool = "unknown_prev"
        elif prev_sealed:
            pool = "continued"
        elif state == "sealed":
            pool = "first_board"
        else:
            pool = "exploded"

        if pool in funnel:
            funnel[pool] += 1

        checks: dict[str, Check] = {}
        if pool == "first_board":
            checks["low_open"] = strategy_low_open(history, cfg)
            checks["high_open"] = strategy_high_open(history, cfg, stock.cap)
        elif pool == "exploded":
            checks["weak_to_strong"] = strategy_weak_to_strong(history, cfg, stock.cap)

        for name in ("high_open", "weak_to_strong"):
            check = checks.get(name)
            if check and check.passed:
                checks[name + "_pressure"] = pressure_test(history, cfg)

        rows[stock.code] = Row(
            code=stock.code, name=stock.name, state=state, prev_sealed=prev_sealed,
            pool=pool, checks=checks, metrics={"close": today.close,
                                               "amount": today.amount},
        )

    pools = build_pools(records)

    for row in rows.values():
        if _passes(row, "low_open"):
            funnel["low_open"] += 1
        if _passes(row, "high_open"):
            funnel["high_open"] += 1
        if _passes(row, "high_open_pressure"):
            funnel["high_open_pressure"] += 1
        if _passes(row, "weak_to_strong"):
            funnel["weak_to_strong"] += 1
        if _passes(row, "weak_to_strong_pressure"):
            funnel["weak_pressure"] += 1

    final: list[str] = []
    for row in rows.values():
        if (_passes(row, "low_open") or _passes(row, "high_open_pressure")
                or _passes(row, "weak_to_strong_pressure")):
            final.append(row.code)
    funnel["final"] = len(final)

    return Report(rows=tuple(rows.values()), pools=pools, funnel=funnel, final=tuple(final))


def _passes(row: Row, key: str) -> bool:
    check = row.checks.get(key)
    return bool(check and check.passed)
