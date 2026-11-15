"""First-board (首板) screener: limit-up arithmetic, pools, entry filters.

The board rules (:mod:`limitboard.boards`) and the filters
(:mod:`limitboard.strategies`) are pure standard library.
:mod:`limitboard.baostock_source` adds the network adapter and the CLI.
"""

from .boards import (
    Bar,
    DayRecord,
    LimitRule,
    Pools,
    board_of,
    build_pools,
    classify_day,
    has_price_limit,
    legacy_limit_price,
    limit_price,
    limit_ratio,
)
from .strategies import (
    Cap,
    Check,
    Config,
    Report,
    Row,
    Stock,
    pressure_test,
    relative_position,
    return_over,
    screen,
    strategy_high_open,
    strategy_low_open,
    strategy_weak_to_strong,
    vwap_premium,
)

__all__ = [
    "Bar",
    "Cap",
    "Check",
    "Config",
    "DayRecord",
    "LimitRule",
    "Pools",
    "Report",
    "Row",
    "Stock",
    "board_of",
    "build_pools",
    "classify_day",
    "has_price_limit",
    "legacy_limit_price",
    "limit_price",
    "limit_ratio",
    "pressure_test",
    "relative_position",
    "return_over",
    "screen",
    "strategy_high_open",
    "strategy_low_open",
    "strategy_weak_to_strong",
    "vwap_premium",
]
