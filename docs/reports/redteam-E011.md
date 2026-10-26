# E012 Red-Team report on E011

Generated 2026-10-25 20:05 UTC

- Baseline: 84 OOS trades, exp +24.60%, win 40.476190476190474%
- A dropped ['XRPUSDT', 'DOGEUSDT']: n=69, exp +8.90% — OK
- B halves: {'n': 50, 'exp': 45.72, 'win': 44.0} | {'n': 34, 'exp': -6.47, 'win': 35.3} — BREAK
- C distinct days 62/84 trades — OK
- D sweep: 

|   leverage |   oos_n |   oos_exp_pct |   oos_win_pct |
|-----------:|--------:|--------------:|--------------:|
|          2 |      84 |         19.27 |          40.5 |
|          3 |      84 |         24.6  |          40.5 |
|          5 |      85 |         33.52 |          38.8 |

- E reasons:

| oos   |   forced_age |   liquidated |   signal |
|:------|-------------:|-------------:|---------:|
| False |           46 |            4 |       28 |
| True  |           43 |            1 |       40 |

**Survived 4/5.**
