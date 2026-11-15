"""Limit-up arithmetic — pure, standard library only.

Everything here is a decision about *one day*: how much the board is allowed
to move, what the limit price is, and whether the stock closed on it, touched
it, or never got there. The notebook spreads this over nine functions and five
price rules; the rules themselves are ten lines.

The two things that go wrong in every copy of it:

* the limit price is rounded with Python's ``round`` (binary float, half-to-even)
  instead of the exchange's half-up to the cent, so a little over 1% of
  pre-close prices produce a limit one cent too low;
* ``ST`` is mapped to 5% for every board. A ChiNext or STAR ST stock has a 20%
  limit; a Beijing Stock Exchange name has 30%. The notebook calls any of them
  "sealed" once they close 5% up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Sequence

__all__ = [
    "Bar",
    "LimitRule",
    "DayRecord",
    "Pools",
    "board_of",
    "limit_ratio",
    "limit_price",
    "legacy_limit_price",
    "has_price_limit",
    "classify_day",
    "build_pools",
    "EPS",
]

#: Prices from the feed carry two decimals; comparisons get a tiny tolerance.
EPS = 1e-9


@dataclass(frozen=True)
class Bar:
    """One daily bar, unadjusted — the only correct basis for limit prices."""

    date: str
    open: float
    high: float
    low: float
    close: float
    preclose: float
    volume: float = 0.0
    amount: float = 0.0
    is_st: bool = False


@dataclass(frozen=True)
class LimitRule:
    """Daily price limits by board, effective since the 2020 reforms."""

    main: float = 0.10          # 60x / 00x
    growth: float = 0.20        # 300x (ChiNext), 688x (STAR)
    bj: float = 0.30            # 8xxxxx / 4xxxxx
    st_main: float = 0.05
    st_growth: float = 0.20     # ST on ChiNext/STAR keeps the 20% limit
    st_bj: float = 0.30
    #: Trading days from listing during which the board has no ±10% limit.
    no_limit_main: int = 2      # day 1 has the 44% IPO rule, not the 10% board
    no_limit_growth: int = 6    # first five sessions are uncapped
    no_limit_bj: int = 2


@dataclass(frozen=True)
class DayRecord:
    code: str
    name: str
    state: str                  # sealed | exploded | none | no_limit | no_data
    prev_sealed: bool | None    # None: the previous bar is not in the data


@dataclass
class Pools:
    first_board: list[DayRecord] = field(default_factory=list)
    exploded: list[DayRecord] = field(default_factory=list)
    continued: list[DayRecord] = field(default_factory=list)
    unknown_prev: list[DayRecord] = field(default_factory=list)
    no_limit: list[DayRecord] = field(default_factory=list)
    none: list[DayRecord] = field(default_factory=list)
    no_data: list[DayRecord] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "first_board": len(self.first_board),
            "exploded": len(self.exploded),
            "continued": len(self.continued),
            "unknown_prev": len(self.unknown_prev),
            "no_limit": len(self.no_limit),
            "none": len(self.none),
            "no_data": len(self.no_data),
        }


# --------------------------------------------------------------------------- rules
def board_of(code: str) -> str:
    """``main`` | ``growth`` | ``bj`` from a baostock or bare stock code."""
    digits = code.split(".")[-1]
    if code.startswith("bj.") or digits.startswith(("4", "8", "9")):
        return "bj"
    if digits.startswith(("30", "68")):
        return "growth"
    return "main"


def limit_ratio(code: str, is_st: bool = False, rule: LimitRule = LimitRule()) -> float:
    board = board_of(code)
    if is_st:
        return {"main": rule.st_main, "growth": rule.st_growth, "bj": rule.st_bj}[board]
    return {"main": rule.main, "growth": rule.growth, "bj": rule.bj}[board]


def limit_price(
    preclose: float, code: str, is_st: bool = False, rule: LimitRule = LimitRule()
) -> float:
    """The board price, rounded the way the exchange rounds: half-up to 0.01."""
    if preclose <= 0:
        return 0.0
    ratio = Decimal(str(limit_ratio(code, is_st, rule)))
    raw = Decimal(str(preclose)) * (Decimal(1) + ratio)
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def legacy_limit_price(preclose: float, code: str, is_st: bool = False) -> float:
    """The notebook's version, kept so the difference can be measured."""
    if is_st:
        ratio = 0.05
    else:
        ratio = 0.20 if code.split(".")[-1].startswith(("30", "68")) else 0.10
    return round(preclose * (1 + ratio), 2)


def has_price_limit(
    code: str, listed_bars: int | None, rule: LimitRule = LimitRule()
) -> bool:
    """False during the no-limit window after listing."""
    if listed_bars is None:
        return True
    board = board_of(code)
    floor = {
        "main": rule.no_limit_main,
        "growth": rule.no_limit_growth,
        "bj": rule.no_limit_bj,
    }[board]
    return listed_bars >= floor


def classify_day(
    bar: Bar,
    code: str,
    *,
    listed_bars: int | None = None,
    rule: LimitRule = LimitRule(),
) -> str:
    """``sealed`` / ``exploded`` / ``none`` / ``no_limit`` / ``no_data``.

    ``exploded`` uses the daily high: a bar that printed the limit price but
    closed below it. No intraday data is needed — ``high >= limit`` *is* the
    definition of "touched the board", which is why the notebook's extra
    5-minute query for 5,000 stocks buys nothing.
    """
    if bar.preclose <= 0 or bar.close <= 0:
        return "no_data"
    if not has_price_limit(code, listed_bars, rule):
        return "no_limit"
    limit = limit_price(bar.preclose, code, bar.is_st, rule)
    if bar.close + EPS >= limit:
        return "sealed"
    if bar.high + EPS >= limit:
        return "exploded"
    return "none"


def build_pools(records: Iterable[DayRecord]) -> Pools:
    """Sort each day's records into the 首板池 and 炸板池.

    A stock with no previous bar is **not** silently treated as "was not
    limit-up". That is how a continuation board turns into a "first board" the
    morning after a holiday: the previous-day query returns nothing, the
    notebook's ``prev_limit`` is ``False``, and the whole pool is wrong.
    """
    pools = Pools()
    for record in records:
        if record.state == "no_data":
            pools.no_data.append(record)
        elif record.state == "no_limit":
            pools.no_limit.append(record)
        elif record.state not in ("sealed", "exploded"):
            pools.none.append(record)
        elif record.prev_sealed is None:
            pools.unknown_prev.append(record)
        elif record.prev_sealed:
            pools.continued.append(record)      # 连板, out of scope by design
        elif record.state == "sealed":
            pools.first_board.append(record)
        else:
            pools.exploded.append(record)
    return pools
