# Data quality report

Generated: 2026-10-25 17:24 UTC · threshold MIN_DQ=0.9

| symbol   | resolution   |    bars | start      | end        |     dq |   missing |   duplicates |   ohlc_violations |   zero_volume_pct |   max_gap_bars |
|:---------|:-------------|--------:|:-----------|:-----------|-------:|----------:|-------------:|------------------:|------------------:|---------------:|
| BTCUSDT  | 15           | 2513150 | 2022-01-09 | 2026-10-25 | 0.9178 |       463 |            0 |                 0 |             80.51 |             30 |
| BTCUSDT  | 1D           |    2545 | 2019-10-15 | 2026-10-25 | 0.9624 |        27 |            0 |                 0 |             31.28 |              3 |
| BTCUSDT  | 240          |   56541 | 2019-10-15 | 2026-10-25 | 0.9191 |      1299 |            0 |                 0 |             30.29 |             21 |
| BTCUSDT  | 60           |   56616 | 2019-10-15 | 2026-10-25 | 0.921  |      4998 |            0 |                 0 |             30.29 |             87 |
| ETHUSDT  | 1D           |    2512 | 2019-10-16 | 2026-10-25 | 0.9559 |        59 |            0 |                 0 |             30.33 |             10 |
| ETHUSDT  | 240          |   54927 | 2019-10-16 | 2026-10-25 | 0.9037 |      1678 |            0 |                 0 |             30.92 |             68 |
| ETHUSDT  | 60           |   55000 | 2019-10-16 | 2026-10-25 | 0.9051 |      6570 |            0 |                 0 |             30.92 |            275 |
| USDTTMN  | 1D           |    2798 | 2019-01-01 | 2026-10-25 | 0.9213 |       196 |            0 |                 0 |             37.49 |              8 |
| USDTTMN  | 60           |   60609 | 2019-01-01 | 2026-10-24 | 0.8991 |      7871 |            0 |                 2 |             31.82 |            204 |

Rules: signals/backtests may only consume series with DQ >= MIN_DQ. Missing bars are gap-filled only inside the backtest engine (never in storage).