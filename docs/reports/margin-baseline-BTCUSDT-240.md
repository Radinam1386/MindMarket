# Margin baseline: Donchian(20/10) BTCUSDT 240

Executed 2026-10-25 18:19 UTC · engine v2 (calibrated margin layer)

Fees MEASURED via public dry-run 2026-10-25: trade fee 0.25% of loan per leg; activation + renewal = loan/6000 each (renewal every 4h); liquidation at adverse move 0.8546/L - 0.0045; 21-day forced close. fee_stress multiplies all fee rates.

| fee_stress   | variant    |   leverage |   total_return_pct |   cagr_pct |   max_drawdown_pct |   sharpe |   n_trades |   win_rate_pct |   expectancy_pct |   avg_hold_days |   avg_fees_pct_of_collateral |   interest_share_of_fees_pct |
|:-------------|:-----------|-----------:|-------------------:|-----------:|-------------------:|---------:|-----------:|---------------:|-----------------:|----------------:|-----------------------------:|-----------------------------:|
| half         | long       |          2 |             -98.82 |     -46.84 |             -99.96 |     0.14 |        977 |           33.1 |            -0.07 |               1 |                         0.61 |                         16.4 |
| half         | long_short |          2 |            -100    |     -87.78 |            -100    |    -0.09 |       1883 |           32.3 |            -0.4  |               1 |                         0.61 |                         17.5 |
| half         | long       |          3 |            -100    |    -100    |            -100    |     0.62 |        978 |            8.6 |             0.33 |               1 |                         0.94 |                         17.2 |
| half         | long_short |          3 |            -100    |    -100    |            -100    |     0.56 |       1884 |            7.9 |             0.03 |               1 |                         0.94 |                         17.9 |
| half         | long       |          5 |            -100    |    -100    |            -100    |     0.8  |        978 |            8.6 |             0.51 |               1 |                         1.57 |                         17.7 |
| half         | long_short |          5 |            -100    |    -100    |            -100    |     0.32 |       1885 |            1.1 |             0.01 |               1 |                         1.79 |                         27.2 |
| half         | long       |         10 |            -100    |    -100    |            -100    |     0.54 |        979 |           31.9 |            -1.26 |               1 |                         3.08 |                         19.2 |
| half         | long_short |         10 |            -100    |    -100    |            -100    |     0.73 |       1886 |           30.8 |            -2.51 |               1 |                         3.15 |                         28.8 |
| real         | long       |          2 |            -100    |     -77.34 |            -100    |    -0.53 |        977 |           30.4 |            -0.68 |               1 |                         1.22 |                         17.1 |
| real         | long_short |          2 |            -100    |     -97.64 |            -100    |    -0.92 |       1883 |           29.3 |            -1.01 |               1 |                         1.22 |                         18.9 |
| real         | long       |          3 |            -100    |    -100    |            -100    |     0.38 |        978 |            8.2 |             0.12 |               1 |                         1.88 |                         17.7 |
| real         | long_short |          3 |            -100    |    -100    |            -100    |     0.28 |       1884 |            7.3 |            -0.17 |               1 |                         1.89 |                         20.3 |
| real         | long       |          5 |            -100    |    -100    |            -100    |     0.52 |        978 |            8.2 |             0.16 |               1 |                         3.14 |                         18.2 |
| real         | long_short |          5 |            -100    |    -100    |            -100    |     0.17 |       1885 |            1   |            -0.04 |               1 |                         3.58 |                         29.1 |
| real         | long       |         10 |            -100    |    -100    |            -100    |    -0.31 |        979 |           28.5 |            -4.31 |               1 |                         6.19 |                         19.8 |
| real         | long_short |         10 |            -100    |    -100    |            -100    |    -0.37 |       1886 |           27.4 |            -5.56 |               1 |                         6.34 |                         30.4 |
| double       | long       |          2 |            -100    |     -95.95 |            -100    |    -1.85 |        977 |           24.7 |            -1.9  |               1 |                         2.44 |                         18.2 |
| double       | long_short |          2 |            -100    |    -100    |            -100    |    -2.56 |       1883 |           23.7 |            -2.23 |               1 |                         2.44 |                         23.1 |
| double       | long       |          3 |            -100    |    -100    |            -100    |    -0.09 |        978 |            6.7 |            -0.3  |               1 |                         3.77 |                         19   |
| double       | long_short |          3 |            -100    |    -100    |            -100    |    -0.28 |       1884 |            6.2 |            -0.58 |               1 |                         3.78 |                         26.2 |
| double       | long       |          5 |            -100    |    -100    |            -100    |    -0.02 |        978 |            6.4 |            -0.53 |               1 |                         6.28 |                         20.6 |
| double       | long_short |          5 |            -100    |    -100    |            -100    |    -0.12 |       1885 |            0.8 |            -0.14 |               1 |                         7.15 |                         31.7 |
| double       | long       |         10 |            -100    |    -100    |            -100    |    -1.88 |        979 |           21.9 |           -10.41 |               1 |                        12.55 |                         22   |
| double       | long_short |         10 |            -100    |    -100    |            -100    |    -0.1  |       1886 |            0.8 |            -0.25 |               1 |                        14.31 |                         29.7 |

## Exit reasons (per scenario)

| fee_stress   | variant    |   leverage |   signal |   end_of_data |   liquidated |
|:-------------|:-----------|-----------:|---------:|--------------:|-------------:|
| half         | long       |          2 |      976 |             1 |          nan |
| half         | long_short |          2 |     1882 |             1 |          nan |
| half         | long       |          3 |      976 |             1 |            1 |
| half         | long_short |          3 |     1882 |             1 |            1 |
| half         | long       |          5 |      976 |             1 |            1 |
| half         | long_short |          5 |     1881 |             1 |            3 |
| half         | long       |         10 |      973 |             1 |            5 |
| half         | long_short |         10 |     1873 |             1 |           12 |
| real         | long       |          2 |      976 |             1 |          nan |
| real         | long_short |          2 |     1882 |             1 |          nan |
| real         | long       |          3 |      976 |             1 |            1 |
| real         | long_short |          3 |     1882 |             1 |            1 |
| real         | long       |          5 |      976 |             1 |            1 |
| real         | long_short |          5 |     1881 |             1 |            3 |
| real         | long       |         10 |      973 |             1 |            5 |
| real         | long_short |         10 |     1873 |             1 |           12 |
| double       | long       |          2 |      976 |             1 |          nan |
| double       | long_short |          2 |     1882 |             1 |          nan |
| double       | long       |          3 |      976 |             1 |            1 |
| double       | long_short |          3 |     1882 |             1 |            1 |
| double       | long       |          5 |      976 |             1 |            1 |
| double       | long_short |          5 |     1881 |             1 |            3 |
| double       | long       |         10 |      973 |             1 |            5 |
| double       | long_short |         10 |     1873 |             1 |           12 |
