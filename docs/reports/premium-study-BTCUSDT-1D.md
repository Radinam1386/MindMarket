# E009: USDT/TMN premium z-score → forward BTC returns

Generated 2026-10-25 19:44 UTC · 2483 joint days (2019-12-21 .. 2026-10-25)

z = yesterday's standardized 60d change of USDTTMN (shift(1): decision-time safe).

## IS (2019-12-21..2023-12-31)

| bucket   |   n_days |   fwd1d_mean_pct |   fwd1d_t |   fwd1d_pos% |   fwd3d_mean_pct |   fwd3d_t |   fwd3d_pos% |   fwd7d_mean_pct |   fwd7d_t |   fwd7d_pos% |
|:---------|---------:|-----------------:|----------:|-------------:|-----------------:|----------:|-------------:|-----------------:|----------:|-------------:|
| z<-2     |       44 |            0.367 |      0.54 |         47.7 |            0.307 |      0.32 |         54.5 |            2.383 |      1.43 |         59.1 |
| -2..-1   |      111 |           -0.2   |     -0.63 |         49.5 |            0.269 |      0.48 |         51.4 |            0.215 |      0.26 |         49.5 |
| -1..1    |     1114 |            0.246 |      2.45 |         52.2 |            0.642 |      3.68 |         55.9 |            1.549 |      5.64 |         54.5 |
| 1..2     |      137 |            0.049 |      0.16 |         53.3 |            0.462 |      0.84 |         54.7 |            0.975 |      1.18 |         56.2 |
| z>2      |       48 |           -0.13  |     -0.25 |         39.6 |           -0.586 |     -0.68 |         37.5 |           -1.922 |     -1.36 |         50   |

## OOS (2024-01-01..2026-10-25)

| bucket   |   n_days |   fwd1d_mean_pct |   fwd1d_t |   fwd1d_pos% |   fwd3d_mean_pct |   fwd3d_t |   fwd3d_pos% |   fwd7d_mean_pct |   fwd7d_t |   fwd7d_pos% |
|:---------|---------:|-----------------:|----------:|-------------:|-----------------:|----------:|-------------:|-----------------:|----------:|-------------:|
| z<-2     |       31 |            0.494 |      0.99 |         58.1 |            1.036 |      1.27 |         58.1 |            2.758 |      2.85 |         74.2 |
| -2..-1   |      106 |            0.357 |      1.29 |         49.1 |            0.649 |    nan    |         51.9 |            1.027 |    nan    |         50   |
| -1..1    |      748 |            0.078 |    nan    |         50.7 |            0.297 |    nan    |         52.3 |            0.541 |    nan    |         50.1 |
| 1..2     |      108 |           -0.157 |     -0.62 |         45.4 |           -0.297 |     -0.81 |         50   |            0.603 |    nan    |         55.6 |
| z>2      |       36 |           -0.119 |     -0.4  |         50   |           -0.595 |     -0.88 |         47.2 |           -0.662 |     -0.59 |         52.8 |

## Reading it

- Interesting columns: `fwd*d_mean_pct` (conditional forward BTC return) and `fwd*d_t`
  (t-stat vs 0; |t|>2 is strong for a single bucket, but note multiple comparisons).
- A tradable gate needs: consistent sign IS AND OOS in the extreme buckets,
  with enough days (n) to matter.
- The premium is also the market's stress gauge: z>2 days are candidates for
  risk-off gates on ANY strategy (that test is the follow-up).