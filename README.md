# MindMarket — MarketMind AI

Quantitative research system for Wallex (wallex.ir) margin trading — data pipeline,
cost-aware backtesting, staged validation, and (later) paper & controlled live trading.

**Full project report (فارسی):** [`docs/PROJECT-REPORT.md`](docs/PROJECT-REPORT.md) · Master plan: [`docs/MARKETMIND-PLAN.md`](docs/MARKETMIND-PLAN.md) — capabilities,
verified Wallex findings, strategy catalog, risk engine, deployment ladder.

## Golden rules

1. No live trading without: OOS validation → Red-Team → ≥8 weeks paper → **explicit human approval**.
2. Research always assumes **Tier-1 fees** (taker 0.30%/side) — profits must survive pessimistic costs.
3. No API key with withdrawal permission. Ever. Trade-only, IP-allowlisted.
4. Signals decide on bar close; execution happens at the next bar's open. No exceptions.
5. Every experiment is reproducible: data snapshot + code + params.

## Quickstart

```bash
uv sync                                   # setup (use a reachable PyPI index if needed)
uv run pytest -q                          # tests must be green
uv run python -m marketmind.cli markets   # snapshot of all Wallex markets
uv run python -m marketmind.cli backfill --all   # download history (slow first time)
uv run python -m marketmind.cli audit     # data-quality report
uv run python -m marketmind.cli costreport       # fee break-even tables (the reality check)
uv run python -m marketmind.cli baseline --symbol BTCUSDT --resolution 1D  # first backtest
```

Data lives in `data/` (gitignored). Reports in `docs/reports/`.

## Status (2026-10-25)

- [x] T01 repo + env (Python 3.12, uv)
- [x] T02 Wallex public client (throttled, retrying) + backward-paged UDF backfill
- [x] T03 full history: BTC/ETH (2019-10→) 1h/4h/1D + USDTTMN (2019-01→) + **minute data 2022→**
      (discovery: `resolution=15` returns 1-minute bars; see config.py)
- [x] T06 fee break-even report → `docs/reports/cost-breakeven.md`
- [x] T07 Donchian baseline on BTCUSDT 1D → `docs/reports/baseline-donchian-BTCUSDT-1D.md`
- [x] T08/T09 margin layer + engine v1 + E002/E003 (pessimistic-fee verdicts)
- [x] **U1 SOLVED**: public dry-run endpoints discovered & fee structure MEASURED
      (trade 0.25% of loan/leg; activation+renewal = loan/6000 per 4h; liq = 0.8546/L−0.0045;
      notional cap 100–1700 USDT/position) → engine recalibrated to v2 → E005 re-run
- [x] E006 fractional/vol-target sizing: all leverages survive, maxDD −23…−36%, robust to 2× fees
- [x] E007 IS/OOS validation: **REJECTED** — the edge was the 2020-21 bull (OOS −2% vs B&H +83%);
      methodology verdict working as designed → back to research
- [x] E008 regime-gated entries: ER-gated Donchian **OOS +20.8%** (5/6 PASS; thin sample);
      vol-squeeze OOS +5.9% (borderline)
- [x] E009 USDTTMN premium: **real signal** — z<−2 → forward 7d +2.8% OOS (hit 74%, t=2.85);
      z>2 → bearish tilt both windows
- [x] E010 premium veto: neutral on entries (kept as risk overlay, not gate)
- [x] **E011 pooled 9-asset donchian_er: research PASS** — OOS portfolio +14.6%,
      maxDD −12.9%, 84 trades, expectancy +24.6%, robust to 2× fees
- [x] **E012 Red-Team: DAMAGE (4/5)** — edge is front-loaded: OOS half1 +45.7%,
      half2 **−6.5%** → E011 downgraded, no paper path; >55% of exits are the 21d cap
- [x] E013/E014 exit redesign + SMA200 regime gate (IS only): trail exits don't help;
      **SMA200 fixes the bear fold** (−16.3→−3.0, exp +29.1%) — but pre-stated rule still
      bars qualification → candidate **parked for forward paper trading** (2024+ burnt)
- [ ] Next: E015 paper-trading harness (live data = the judge) · owner inputs for Stage 4
- [x] **E015 LIVE (Stage 4 started)** — 200 USDT paper account, chan10+SMA200 candidate,
      decision ledger, Persian dashboard (`paper serve` → http://127.0.0.1:8787),
      Telegram notifier (awaiting bot credentials), daily 04:30 automation
- [x] E016 hybrid: LLM analyst in the loop (veto/size-down + tagged conviction entries,
      never blocks exits) + dashboard v2 (live prices, AI panel, run-now, halt/resume)
- [ ] Judgment: ≥8 weeks forward paper results → evidence dossier → owner approval gates
      (rules-vs-AI per-path stats tracked on the dashboard)

## Project layout

```
src/marketmind/
  config.py            constants: paths, fees, resolutions, margin scenarios
  wallex/client.py     public REST client (markets/depth/trades/udf_history)
  data/ingest.py       backward-paged backfill -> parquet
  data/quality.py      gap/duplicate/OHLC audit + DQ score (gate: 0.90)
  analytics/costmodel.py  margin round-trip & break-even math
  backtest/baselines.py   Donchian reference strategies
  backtest/engine.py      event-loop engine: next-open fills, fees, long/short
  cli.py               commands: markets | backfill | audit | costreport | baseline
tests/                 unit tests (no network)
docs/MARKETMIND-PLAN.md  the master plan
```
