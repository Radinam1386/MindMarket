# E007 validation — Donchian(20/10) BTCUSDT 1D

Executed 2026-10-25 19:43 UTC · OOS touched once

## IS / OOS

| window | return% | maxDD% | sharpe | trades | win% | expectancy% |
|---|---|---|---|---|---|---|
| IS | 20.6 | -21.3 | 18.21 | 13 | 46.2 | 6.41 |
| OOS | 5.9 | -10.0 | 9.39 | 14 | 42.9 | 3.01 |

OOS buy & hold (unlevered): +83.0%

## Yearly stability

|   year |   return_pct |   max_dd_pct |   sharpe |   bars |
|-------:|-------------:|-------------:|---------:|-------:|
|   2019 |          0   |          0   |     0    |     73 |
|   2020 |         13.9 |         -4.5 |    46.04 |    365 |
|   2021 |        -10.3 |        -11.4 |   -39.73 |    351 |
|   2022 |         -9.1 |         -9.3 |   -58.81 |    362 |
|   2023 |         29.9 |         -2.3 |    80.63 |    365 |
|   2024 |          4.3 |         -4.9 |    15.66 |    366 |
|   2025 |         -2.2 |         -5.9 |   -14.12 |    365 |
|   2026 |          3.8 |         -4.9 |    17.77 |    298 |

## Parameter sensitivity (IS only)

| variant          |   is_return_pct |   is_max_dd_pct |   is_sharpe |   n_trades |   win_rate_pct |   expectancy_pct |   avg_hold_days |
|:-----------------|----------------:|----------------:|------------:|-----------:|---------------:|-----------------:|----------------:|
| time_stop_bars=3 |           16.41 |          -17.55 |        0.53 |         13 |           38.5 |             4.54 |             3.5 |
| time_stop_bars=5 |           20.57 |          -21.35 |        0.57 |         13 |           46.2 |             6.41 |             5.2 |
| time_stop_bars=8 |            9.41 |          -28.98 |        0.26 |         13 |           23.1 |             1.3  |             7.5 |

## Acceptance checklist

- [PASS] OOS Sharpe > 1.0 (9.39)
- [PASS] OOS max drawdown < 20% (10.0%)
- [FAIL] OOS trades >= 50 (14)
- [PASS] OOS expectancy > 2x per-trade cost bar (+3.01% vs bar 3.00%)
- [PASS] Expectancy sign stable across params (IS) (3/3 positive)
- [PASS] Stress fees x2 still positive (OOS return) (+5.9%)

**VERDICT: FAIL**
