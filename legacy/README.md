# Legacy: the original notebook, verbatim

These three files are the code cells of `notebooks/打板策略.ipynb`, extracted
without a single edit. They are here so every "the notebook did X" claim in
[`../docs/算法总结.md`](../docs/算法总结.md) can be checked against the source.

| File | Cell | What it is |
|---|---|---|
| `v1_pools.py` | 1 | **V1** — read codes from a CSV, query today and yesterday per stock, classify 首板池 / 炸板池, write two CSVs. |
| `v2_analyzer.py` | 3 | **V2** — the same pipeline in `FirstBoardAnalyzer`, plus the three entry filters, the pressure test, market caps from `query_profit_data`, and an interactive menu. |
| `v3_named_report.py` | 4 | **V3** — V2 plus stock names in the printed report and per-strategy output files. This is the newest version and the one this repo rewrites. |

Cells 2, 5, 6 and 7 are `pip install baostock pandas`, sample output, and notes;
they carry no logic. Cell 5 is a real run's output, cell 6 a note about a
different project.

What they share, and what this repo changes:

- `round(preclose * (1 + ratio), 2)` — banker's rounding on a binary float.
- `if is_st: ratio = 0.05` — every board, including ChiNext and STAR where ST
  keeps the 20% limit.
- `get_previous_trading_day()` skips weekends only. On the first session after a
  holiday the previous-day query is empty and `prev_limit` is `False`, so every
  continuation becomes a "first board".
- `pressure_test`: `if idx is None: continue` rejects every stock at a new high;
  an empty observation window is rejected too.
- `total_cap < 70 or float_cap > 520` — the lower bound watches total market cap,
  the upper bound watches free float.
- A 5-minute series per stock, used only for `max(high)`, plus a re-query of the
  same day inside each filter: 4–6 round-trips per stock.

None of these raise an exception. That is the point.
