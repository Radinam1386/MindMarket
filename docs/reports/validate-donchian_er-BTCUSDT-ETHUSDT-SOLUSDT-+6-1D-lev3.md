# Validation — donchian_er BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT,ADAUSDT,LINKUSDT,DOTUSDT,DOGEUSDT 1D

Executed 2026-10-25 19:57 UTC · OOS 2024-01-01..now

## Per-asset

| symbol   |   oos_ret% |   oos_maxdd% |   oos_trades |   oos_win% |   oos_exp% |   is_ret% |   is_trades |
|:---------|-----------:|-------------:|-------------:|-----------:|-----------:|----------:|------------:|
| BTCUSDT  |       12.5 |        -15.5 |           12 |       50   |       7.47 |      52.8 |          17 |
| ETHUSDT  |      -11.8 |        -28.3 |           13 |       38.5 |      -8.18 |      72.5 |          19 |
| SOLUSDT  |      -23.2 |        -27.5 |           11 |       36.4 |     -13.37 |      45.2 |           6 |
| XRPUSDT  |       55.8 |        -25.3 |            8 |       25   |      93.82 |      -2.3 |           5 |
| BNBUSDT  |        9.2 |        -30.8 |           10 |       50   |       8.98 |      -5.1 |           7 |
| ADAUSDT  |        4.4 |        -28.2 |           10 |       20   |       9.87 |       0.9 |           6 |
| LINKUSDT |       -8   |        -16.3 |            7 |       28.6 |       2.54 |      -1.6 |           7 |
| DOTUSDT  |        9.4 |        -18.7 |            6 |       50   |      21.56 |       5.9 |           6 |
| DOGEUSDT |       45.1 |        -24.3 |            7 |       57.1 |      85.06 |     -14.2 |           5 |

## Pooled OOS trades

| n | win% | expectancy% | avg_hold_d |
|---|---|---|---|
| 84 | 39.3 | 18.07 | 16.7 |

Equal-weight avg OOS return: +6.9%

## Acceptance

- [PASS] OOS Sharpe > 1.0 (9.29 (portfolio))
- [PASS] OOS max drawdown < 20% (-14.7% (portfolio))
- [PASS] OOS trades >= 50 (pooled) (84)
- [PASS] OOS expectancy > 2x per-trade cost bar (+18.07% vs bar 3.00%)
- [PASS] OOS return positive (avg or single) (+6.9%)
- [PASS] Stress fees x2 run available via --fee_stress double (info)

**VERDICT: PASS**
