# Margin baseline: Donchian(20/10) BTCUSDT 1D

Executed 2026-10-25 19:16 UTC · engine v2 (calibrated margin layer)

Fees MEASURED via public dry-run 2026-10-25: trade fee 0.25% of loan per leg; activation + renewal = loan/6000 each (renewal every 4h); liquidation at adverse move 0.8546/L - 0.0045; 21-day forced close. fee_stress multiplies all fee rates.

| fee_stress   | variant    |   leverage |   total_return_pct |   cagr_pct |   max_drawdown_pct |   sharpe |   n_trades |   win_rate_pct |   expectancy_pct |   avg_hold_days |   avg_fees_pct_of_collateral |
|:-------------|:-----------|-----------:|-------------------:|-----------:|-------------------:|---------:|-----------:|---------------:|-----------------:|----------------:|-----------------------------:|
| real         | long       |          2 |             134.6  |      12.9  |             -22.59 |     0.93 |         55 |           45.5 |             9.86 |            16.7 |                         4.36 |
| real         | long_short |          2 |             148.98 |      13.86 |             -31.31 |     0.88 |         92 |           42.4 |             6.68 |            16   |                         4.23 |
| real         | long       |          3 |             115.58 |      11.55 |             -31.42 |     0.79 |         56 |           44.6 |            10.14 |            16.4 |                         6.46 |
| real         | long_short |          3 |             113.26 |      11.38 |             -35.91 |     0.7  |         93 |           41.9 |             6.87 |            15.8 |                         6.29 |
| real         | long       |          5 |             112.43 |      11.32 |             -30.18 |     0.78 |         56 |           44.6 |            14.76 |            16.1 |                        10.63 |
| real         | long_short |          5 |             106.83 |      10.89 |             -37.37 |     0.67 |         93 |           41.9 |             9.72 |            15.6 |                        10.39 |
| double       | long       |          2 |              64.31 |       7.32 |             -29.41 |     0.54 |         55 |           45.5 |             5.49 |            16.7 |                         8.73 |
| double       | long_short |          2 |              34.98 |       4.36 |             -51.52 |     0.32 |         92 |           41.3 |             2.45 |            16   |                         8.45 |
| double       | long       |          3 |              41    |       5.01 |             -38.18 |     0.37 |         56 |           44.6 |             3.71 |            16.4 |                        12.91 |
| double       | long_short |          3 |              -6.59 |      -0.97 |             -65.18 |     0.06 |         93 |           40.9 |             0.6  |            15.8 |                        12.58 |
| double       | long       |          5 |              30.84 |       3.9  |             -39.26 |     0.31 |         56 |           44.6 |             4.27 |            16.1 |                        21.26 |
| double       | long_short |          5 |             -20.71 |      -3.25 |             -69.53 |    -0.04 |         93 |           40.9 |            -0.48 |            15.6 |                        20.79 |

## Exit reasons (per scenario)

| fee_stress   | variant    |   leverage |   forced_age |   signal |   liquidated |
|:-------------|:-----------|-----------:|-------------:|---------:|-------------:|
| real         | long       |          2 |           29 |       26 |          nan |
| real         | long_short |          2 |           46 |       46 |          nan |
| real         | long       |          3 |           29 |       26 |            1 |
| real         | long_short |          3 |           46 |       46 |            1 |
| real         | long       |          5 |           28 |       26 |            2 |
| real         | long_short |          5 |           45 |       45 |            3 |
| double       | long       |          2 |           29 |       26 |          nan |
| double       | long_short |          2 |           46 |       46 |          nan |
| double       | long       |          3 |           29 |       26 |            1 |
| double       | long_short |          3 |           46 |       46 |            1 |
| double       | long       |          5 |           28 |       26 |            2 |
| double       | long_short |          5 |           45 |       45 |            3 |
