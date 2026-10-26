# Fee break-even analysis — measured fee structure (2026-10-25)

Source: public dry-run endpoint, live. trade 0.25% of loan per leg;
activation + renewal = loan/6000 each (renewal every 4h).

## Headline (real fees, no stress)

| horizon   |   leverage |   renewals_n |   price_move_needed_pct |   collateral_return_needed_pct |
|:----------|-----------:|-------------:|------------------------:|-------------------------------:|
| 4h        |          2 |            1 |                   0.356 |                           1.07 |
| 4h        |          3 |            1 |                   0.4   |                           1.6  |
| 4h        |          5 |            1 |                   0.444 |                           2.67 |
| 12h       |          2 |            3 |                   0.378 |                           1.13 |
| 12h       |          3 |            3 |                   0.425 |                           1.7  |
| 12h       |          5 |            3 |                   0.472 |                           2.83 |
| 1d        |          2 |            6 |                   0.411 |                           1.23 |
| 1d        |          3 |            6 |                   0.462 |                           1.85 |
| 1d        |          5 |            6 |                   0.514 |                           3.08 |
| 3d        |          2 |           18 |                   0.544 |                           1.63 |
| 3d        |          3 |           18 |                   0.613 |                           2.45 |
| 3d        |          5 |           18 |                   0.681 |                           4.08 |
| 7d        |          2 |           42 |                   0.811 |                           2.43 |
| 7d        |          3 |           42 |                   0.913 |                           3.65 |
| 7d        |          5 |           42 |                   1.014 |                           6.08 |
| 14d       |          2 |           84 |                   1.278 |                           3.83 |
| 14d       |          3 |           84 |                   1.438 |                           5.75 |
| 14d       |          5 |           84 |                   1.597 |                           9.58 |
| 21d       |          2 |          126 |                   1.744 |                           5.23 |
| 21d       |          3 |          126 |                   1.962 |                           7.85 |
| 21d       |          5 |          126 |                   2.181 |                          13.08 |

## Reading it

- `price_move_needed_pct`: gross move needed on notional before the first rial of profit.
- Real structure is far cheaper than the pre-measurement pessimistic scenarios:
  a 1-day hold at 3x now needs ~0.3% instead of the earlier assumed 1.4%.
- The binding constraints remain: per-leg trade fee at entry+exit and 4h clock;
  fee_stress=double in the full grid covers regime/parameter uncertainty.

Full scenario grid (105 rows) saved to `cost-breakeven-full.csv`.