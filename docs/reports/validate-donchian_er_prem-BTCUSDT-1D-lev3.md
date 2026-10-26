# Validation — donchian_er_prem BTCUSDT 1D

Executed 2026-10-25 19:54 UTC · OOS 2024-01-01..now

## Per-asset

| symbol   |   oos_ret% |   oos_maxdd% |   oos_trades |   oos_win% |   oos_exp% |   is_ret% |   is_trades |
|:---------|-----------:|-------------:|-------------:|-----------:|-----------:|----------:|------------:|
| BTCUSDT  |       21.6 |        -13.3 |           12 |         50 |      14.17 |      67.6 |          17 |

## Pooled OOS trades

| n | win% | expectancy% | avg_hold_d |
|---|---|---|---|
| 12 | 50.0 | 14.17 | 17.2 |

Equal-weight avg OOS return: +21.6%

## Acceptance

- [PASS] OOS Sharpe > 1.0 (single-asset only) (24.32)
- [PASS] OOS max drawdown < 20% (-13.3% (worst asset))
- [FAIL] OOS trades >= 50 (pooled) (12)
- [PASS] OOS expectancy > 2x per-trade cost bar (+14.17% vs bar 3.00%)
- [PASS] OOS return positive (avg or single) (+21.6%)
- [PASS] Stress fees x2 run available via --fee_stress double (info)

**VERDICT: FAIL**

## Yearly stability

|   year |   return_pct |   max_dd_pct |   sharpe |   bars |
|-------:|-------------:|-------------:|---------:|-------:|
|   2019 |          0   |          0   |     0    |     73 |
|   2020 |         49.8 |         -7.7 |    65.34 |    365 |
|   2021 |          4.5 |        -19.7 |    10.68 |    351 |
|   2022 |        -11.7 |        -12.2 |   -55.68 |    362 |
|   2023 |         20.4 |         -4.8 |    56.72 |    365 |
|   2024 |         15.9 |        -13.3 |    34.84 |    366 |
|   2025 |          4.7 |         -3.9 |    30.33 |    365 |
|   2026 |         -0.2 |         -3.3 |     0.01 |    298 |
