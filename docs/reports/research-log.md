# Research log — experiment ledger

Every experiment gets an ID, a pre-stated hypothesis, and an honest verdict.
Rejected hypotheses stay here forever so no agent rediscovers them.

---

## E001 — Donchian(20/10) spot baseline, BTCUSDT 1D
- **Date:** 2026-10-25 · **Code:** engine v0 · **Data:** BTCUSDT 1D 2019-10→2026-10 (DQ 0.96)
- **Hypothesis:** trend following is profitable before margin costs.
- **Result (long-only):** +1212% vs buy&hold +868%, Sharpe 1.16, maxDD −46%, 36 trades.
- **Verdict:** PASS as pre-cost baseline. In-sample, single asset, no validation split yet.

## E002 — Donchian(20/10) under margin economics, BTCUSDT 1D
- **Date:** 2026-10-25 · **Code:** engine v1 (margin layer)
- **Hypothesis:** S01 survives activation + 4h renewal fees + 21d cap + liquidation at ≤10X.
- **Result:** total return **−95% to −100% in ALL 24 scenarios** (renewal × leverage × variant).
  avg hold 16.7 days → ~100 renewals/trade; **interest = 85–97% of total costs**.
- **Verdict:** **REJECTED.** Multi-week holds are structurally incompatible with 4h renewal fees
  (within U1 scenario range). The 21-day cap is irrelevant — renewals kill long holds first.

## E003 — Donchian(20/10) under margin economics, BTCUSDT 4h
- **Date:** 2026-10-25 · **Code:** engine v1
- **Hypothesis:** shorter holds (~1 day) dodge the renewal burden.
- **Result:** total return **−100% in ALL scenarios.** 977 trades/7y (~4/week), win rate 22%,
  per-trade fee load 2.2–3.9% of collateral at 2X — churn death.
- **Verdict:** **REJECTED.** Naive channel breakout over-trades; fixed per-trade costs dominate.

---

## E004 — MEASUREMENT: real margin fee structure via public dry-run (2026-10-25)

The user pointed back to the developer docs; systematic probing then discovered
**public (no-auth) margin endpoints** on `api.wallex.ir/margin-trade/v1/...`:

| Endpoint | Finding |
|---|---|
| `GET /public/markets` | 35 margin markets (18 USDT-, 17 TMN-quoted); risk_coef 1–10 step 0.5; liquidity_margin 0.15; max_age 21d — uniform |
| `POST /public/loan` | collateral limits: **notional fixed 100–1700 USDT** (BTC/ETH), independent of leverage |
| `POST /public/dry-run` | full position preview incl. `trade_fee`, `open_fee`, `extend_fee`, `liquidity_price`, `call_price` |

**Measured fee structure (identical across BTCUSDT / ETHUSDT / BTCTMN, long & short):**
- `trade_fee = 0.25% × loan` per leg (default tier; account tier TBD with key)
- `open_fee = extend_fee = loan / 6000` (activation once; renewal each 4h)
- liquidation adverse move = `0.8546/L − 0.0045` (exact fit at L = 2.5, 3, 5, 10; symmetric for shorts)
- warning adverse move ≈ `0.7037/L − 0.0037`
- short positions: loan in base asset, mirror economics

**Impact:** 1-day hold at 3X breaks even at **0.46%** price move (was assumed 1.4–2%);
7-day at 3X: **0.91%** (was 5–9%). Engine recalibrated (v2); U1/U5 → SOLVED (per-tier
trade fee still to confirm with an API key). New constraint discovered: **per-position
notional cap 1700 USDT** — portfolio scale requires multiple positions/markets.

## E005 — Donchian(20/10) re-run under MEASURED economics (2026-10-25, engine v2)

- **BTCUSDT 1D:** leverage 2X long: **+292% total** (Sharpe 0.66, maxDD −91%) — survives
  but drawdown is ruinous. 3X+: −100% — killed by **liquidation + all-in collateral
  sizing** (BTC's 28%+ crashes), not by fees; per-trade expectancy is positive (+8.5% at 3X).
- **BTCUSDT 4h:** −100% everywhere; 977 trades × ~1.2–6% fee load — churn still fatal.
- **Verdict: REVISED.** E002's fee-death verdict is lifted; the binding constraint moved
  from fees to **position sizing / liquidation risk management**. All-in collateral per
  trade is unacceptable; fractional + volatility-targeted collateral is the next engine
  iteration (plan §14 step 8). E003's churn verdict STANDS.

## Next queue
- E007: fractional / vol-targeted collateral sizing in engine (unlocks 3X without ruin).
- E008: selective low-frequency entries (vol-squeeze breakout, 1–3 day time-boxed).
- E009: maker-tier trade fee variant once API key exists (trade_fee may drop to 0.25%→?).
- E010: USDT/TMN premium features (data already stored 2019→).

---

## E006 — Fractional & vol-targeted sizing (2026-10-25, engine v2.1)

- **Change:** `SizingConfig` — collateral per trade = fraction of cash, further capped by
  vol-target (1-ATR adverse move ≤ risk of equity) and exchange notional limits (100–1700 USDT);
  idle cash tracked; below-minimum entries skipped.
- **Setup:** Donchian(20/10) BTCUSDT 1D, start 2000 USDT, measured fees.
- **Fractional 15%:** all leverages survive and profit (2X +106%, 3X +110%, 5X +117% over ~7y);
  **maxDD −28…−36%** (was −91…−100% all-in); still positive at 2× fees (+29…+40%);
  win rate 44–46%, expectancy +10%/trade at 3X.
- **Vol-target 2%/ATR (cap 20%):** 2X +135% with **maxDD −22.6%**, Sharpe 0.93 — best risk profile.
- **Verdict: CONFIRMED (engineering result).** Position sizing was the binding constraint
  after fee calibration; fractional/vol-target sizing resolves the liquidation-ruin failure mode.
- **Honest caveats:** still in-sample, single asset, single parameter set; Sharpe ~0.8–0.9 is
  below the plan's research-acceptance bar (>1.0 OOS); no train/test split yet — E007 does
  walk-forward/OOS before any claim of a real edge. Notional cap (1700) dominates at 2000 USDT
  equity, so leverage choice is nearly cosmetic at this capital level.

## Next queue (updated)
- E007: walk-forward + OOS split of Donchian+sizing (the first honest edge test).
- E008: selective low-frequency entries (vol-squeeze breakout).
- E009: USDT/TMN premium features.

---

## E007 — IS/OOS validation of Donchian(20/10)+vol-target sizing (2026-10-25)

**Protocol:** IS = 2019-10..2023-12 (research), OOS = 2024-01..now touched ONCE.
Config: BTCUSDT 1D, 3X, vol-target 2%/ATR (cap 20%), measured fees, start 2000 USDT.

- **IS:** +120.1%, maxDD −31.4%, 31 trades, win 54.8%, expectancy +19.0%/trade.
- **OOS:** **−2.0%**, maxDD −18.9%, Sharpe −0.54, 25 trades, win 32.0%, **expectancy −0.85%**,
  while unlevered buy & hold did **+83%**.
- **Yearly truth:** 2020 +84%, 2021 +10%, **2022 −20%**, 2023 +35%, 2024 +6%, 2025 −4%, 2026 −4%.
- Parameter sensitivity on IS: all 12 combos positive (uninformative — 2020–21 dominates IS;
  the bull market pays every parameterization).
- Acceptance: 4 of 6 criteria FAIL (Sharpe>1 ✗, expectancy>bar ✗, n≥50 ✗, stress-positive ✗).

**VERDICT: REJECTED for any live path.** The apparent edge was the 2020–2021 bull market.
Post-2024 BTC is range-bound and the channel breakout bleeds via costs in chop.
The methodology worked as designed: the holdout killed the candidate before money could.

**Caveats/notes:**
- Sharpe from a cash-heavy equity curve (many idle bars) inflates (81 in 2020); engine's own
  bar-based Sharpe is the conservative one (1.14 IS). Definitions differ across tables — noted.
- Next hypotheses target the actual failure mode: non-trending regimes and entry selectivity.

## Next queue (updated again)
- E008: regime filter (only take breakouts when trend/vol regime supports) + vol-squeeze
  selective entries with time-boxed exits.
- E009: USDT/TMN premium z-score as gate or standalone signal (Wallex-native data).
- E010: long/short symmetry re-test under regime filters (shorts earned little so far).

---

## E008 — Regime-gated entries (2026-10-25)

OOS ACCESS ACCOUNTING: this window (2024-01..now) has now been evaluated 3×
(E007 donchian, E008a, E008b). Every extra look weakens claims; treat PASSes as
provisional until trade counts grow via multi-asset pooling.

**E008a — Donchian(20/10) + Efficiency-Ratio gate (ER≥0.30), 3X vol-target:**
- IS +74.0% (17 trades, exp +21.1%); **OOS +20.8%, maxDD −12.9%, 12 trades, win 50%, exp +14.2%**
  — positive OOS after costs (vs naive −2.0%). Fee-stress ×2 still +20.8%.
- Sensitivity (er_min 0.15/0.30/0.45): all positive; higher gate → fewer, better trades.
- Checklist: 5/6 PASS; FAIL only "OOS trades ≥ 50" (12). **VERDICT: promising, insufficient evidence.**

**E008b — Vol-squeeze breakout, time-boxed 5 bars, 3X vol-target:**
- IS +20.6% (13 trades); OOS +5.9%, maxDD −10.0%, 14 trades, exp +3.0% (cost bar 3.0 — borderline).
- Checklist: 5/6 PASS, same trade-count FAIL. **VERDICT: marginal, insufficient evidence.**

Interpretation: E007's failure mode (chop) is mitigated by selectivity; direction confirmed.
The bottleneck is now sample size, which is honest progress.

## E009 — USDT/TMN premium z-score → forward BTC returns (2026-10-25)

z = decision-time-safe (shift-1) standardized 60d change of USDTTMN; buckets vs forward BTC.

- **z < −2 (premium dip = local risk-on): forward 7d BTC +2.4% (IS, hit 59%) and
  +2.8% (OOS, hit 74%, t=2.85, n=31)** — consistent, significant, BOTH windows.
- z > +2 (premium spike = local stress): forward returns negative in both windows
  (IS 7d −1.9%, OOS 3d −0.6%) — bearish tilt, not individually significant.
- Middle buckets ≈ drift. **VERDICT: SUPPORTED as a feature.** The premium is a real,
  Wallex-native signal — plan §7 S04 hypothesis survives first contact with data.

## Next queue (updated x3)
- E010: combine E008a + premium gate: z<−2 boosts entry confidence, z>2 vetoes new longs.
- E011: multi-asset pooling (ETHUSDT, SOLUSDT, … margin markets, SAME fixed params)
  to push OOS trade count toward the ≥50 evidence bar.
- E012: short-side re-test with premium z>2 as the short gate.

---

## E010 — ER-gate × premium veto (2026-10-25)

Veto (z>2 blocks entries) barely binds on BTC: OOS +21.6% vs +20.8% without it;
entry days and premium-stress days barely overlap. Grid z∈{1,2,3}: z=3 identical to
no-veto. **Verdict: veto is neutral here.** The premium's demonstrated value is the
z<−2 bullish conditional (E009), which as a *requirement* would starve the sample —
keep it as a monitoring/risk overlay input, not an entry gate.

## E011 — Pooled 9-asset validation of donchian_er (2026-10-25) — FIRST PASS

Same FIXED params everywhere (20/10, ER≥0.30, 3X, vol-target 2%/ATR, cap 20%,
9 USDT margin assets: BTC,ETH,SOL,XRP,BNB,ADA,LINK,DOT,DOGE; each 2000 USDT account):

| Metric | OOS (2024-01..2026-10) |
|---|---|
| Combined portfolio return | **+14.6%** |
| Portfolio max drawdown | **−12.9%** |
| Pooled trades | **84** (≥50 bar: PASS) |
| Pooled expectancy | **+24.6%**/trade (bar 3.0%: PASS) |
| Win rate | 40.5% |
| Fee stress ×2 | +6.9%, maxDD −14.7%, exp +18.1% — still PASS |

Per-asset spread: 6 of 9 positive OOS; SOL −16.7%, ETH −2.4%, LINK −3.4% negative;
XRP (+65%) and DOGE (+53%) dominate the pooled tail.

**VERDICT: PASS — first research-accepted candidate.** With mandatory honesty:
1. **This OOS window has now been accessed 5×** (E007, E008a, E008b, E010, E011).
   Multiple-testing discount applies; treat confidence as provisional, not 95%.
2. Portfolio "Sharpe 18" is the idle-bar-inflated curve metric — ignore it; the
   load-bearing evidence is n=84 trades, expectancy, and maxDD.
3. Returns are tail-driven (typical trend-following): win rate 40.5%, few huge winners.
4. Params were fixed a priori across all assets (no per-asset tuning) — the strongest
   part of this result.
5. Research acceptance ≠ live approval. Next per the ladder: **Red-Team review (H)**,
   then paper-trading design (Stage 4) — nothing touches real money before both,
   plus explicit owner sign-off.

## Next queue (updated x4)
- E012 (H/Red-Team): attack E011 — sub-period stability, remove XRP/DOGE (tail dependence),
  entry-day overlap across assets (correlated trades?), 15m-execution sensitivity,
  min-notional effects at 2000 USDT, liquidation-path audit.
- E013: portfolio-level concurrent-position risk (the engine runs each asset solo;
  live would need a portfolio margin view + per-asset caps).
- E014: paper-trading harness design (dry-run cross-checks, WS live data).

---

## E012 — RED-TEAM on E011 (2026-10-25) — VERDICT: DAMAGE (4/5 attacks survived)

| Attack | Result | Outcome |
|---|---|---|
| A. Drop top-2 winners (XRP, DOGE) | n=69, expectancy **+8.9%** (was +24.6%) | SURVIVES (weaker but positive) |
| B. OOS halves | half1: n=50, **+45.7%** · half2: n=34, **−6.5%**, win 35% | **BREAKS — edge decayed since mid-2025** |
| C. Clustering | 84 trades on 62 distinct days (max 5/day) | SURVIVES |
| D. Leverage 2/3/5× | exp 19.3 / 24.6 / 33.5% (monotone, mechanical) | SURVIVES |
| E. Liquidations | 1 of 84 OOS (4 of 78 IS) — sizing works | SURVIVES |

**Interpretation:** E011's pooled PASS was front-loaded into 2024–early-2025
(late-2024 rally incl. XRP's Nov-2024 +400% run, which sits in half 1). The last
~16 months show no edge (−6.5%). A naive backtest would have sold this as profit;
the split-halves attack killed it. **E011 downgraded: no paper-trading path yet.**

Additional structural finding: **>55% of all closes are forced_age (21-day cap)** —
the strategy usually rides trends until the exchange cap, not its own exit. Exit
design (trailing/time-box) is a genuine repair axis.

**Repair protocol (strict):** repairs are designed on IS ONLY; the 2024+ window is
now burnt (6th access) and must NOT be re-optimized against. The clean test of any
repaired strategy is FORWARD paper trading (Stage 4) on live data.

## Next queue (updated x5)
- E013: exit redesign on IS only (trailing ATR exit / shorter opposite channel /
  profit-taking) targeting the forced_age dominance; then walk-forward WITHIN IS.
- E014: risk-on/risk-off overlay from E009 premium + regime features — when to sit out.
- E015: paper-trading harness (this becomes the only clean judge going forward).

---

## E013/E014 — Exit redesign + regime gate (IS ONLY, 2026-10-25)

Pre-stated selection rule: folds_pos ≥ 2 (of 3 IS folds) ∧ expectancy > 0 ∧
forced_age share ≤ 35%. Rule was registered BEFORE seeing results and is binding.

| variant | n | IS exp% | fold1/2/3 | forced_age% |
|---|---|---|---|---|
| base chan10 (E008a) | 78 | +19.0 | +45.2 / −16.3 / +28.2 | 57.7 |
| trail3 | 77 | +19.7 | +45.1 / −18.7 / +31.2 | 64.9 |
| tb20+trail3 | 77 | +20.2 | +44.7 / −16.7 / +31.1 | 55.8 |
| chan10+SMA200 | 52 | **+29.1** | +46.4 / **−3.0** / +42.9 | 65.4 |
| trail3+SMA200 | 52 | +27.2 | +45.3 / −8.2 / +43.6 | 69.2 |

**Findings:**
1. NO variant qualifies under the pre-stated rule (all exceed the 35% forced-age bar).
2. Diagnosis REVISED: forced-age dominance is a symptom of riding strong trends, not the
   disease; trailing exits did not reduce it or improve expectancy. The real defect is
   regime exposure — fold2 (2021-07..2022-09 bear) is negative for every no-SMA variant.
3. SMA200 regime gate (E014, added between iterations — selection within IS is allowed)
   cuts bear losses ~5× (fold2 −16.3 → −3.0), lifts expectancy +53%, halves trade count.
4. Rule change (dropping the forced-age criterion) is NOT applied retroactively — that
   would be goalpost-moving. It is registered as a hypothesis for the NEXT evaluation.

**Outcome:** candidate `chan10+SMA200` (IS exp +29.1%, folds +46/−3/+43, n=52) is parked
for **forward paper trading only**. The burnt 2024+ window adjudicates nothing further.

## Next queue (updated x6)
- E015: paper-trading harness — live Wallex data, dry-run cross-checks, runs the parked
  candidate (chan10+SMA200) and logs every decision. Fresh data becomes the judge.
- E016 (owner inputs needed for Stage 4): capital ceiling, kill limits, notification channel.

---

## E015 — Paper-trading harness LIVE (2026-10-25, Stage 4 started)

Owner decisions: 200 USDT test capital · Telegram for alerts, UI for main reports ·
weak VPS acceptable (1 vCPU/1GB) · API key protocol agreed (trade-only, no withdrawal,
IP-allowlisted, file-based, no real orders until the full ladder completes).

Built and running:
- `paper/state.py` — SQLite ledger (account/positions/trades/decisions/eity) with the
  measured fee model; accounting unit-tested (no double-count of collateral).
- `paper/runner.py` — daily cycle: refresh live 1D candles for 9 assets → chan10+SMA200
  signals → liquidation watch → exits → vol-target entries (20% cap, 2%/ATR, notional
  bounds, max 3 positions) → every action (open/close/hold/skip) recorded.
- `webapp.py` — Persian RTL dashboard (no CDN): equity canvas, open positions,
  trade log, decision ledger, 60s refresh; token auth for remote access.
- `notify.py` — Telegram sender, file-based secrets, proxy-friendly, never breaks trading.
- Daily automation at 04:30 local; first cycle executed clean (9 holds, equity 200.00).

**Judgment protocol:** ≥8 weeks of forward paper results vs the parked candidate's IS
profile; degradation/fee surprises → review. The 2024+ backtest window remains untouched.

---

## E016 — Hybrid system: LLM analyst joined the loop (2026-10-25)

Owner directive: a real LLM must sit behind the system (chart analysis, macro context,
own judgement beyond formulas) + richer dashboard + one-click operation.

Implemented (paper stage, hard-risk envelope unchanged):
- `llm.py`: OpenAI-compatible client (owner-provided endpoint, file-secured, JSON-disciplined,
  length-aware retry). Failure ALWAYS degrades to neutral opinion, never blocks trading.
- `paper/analyst.py`: per-asset analysis of live Wallex tables (40 OHLCV bars, SMA200/ER/ATR/
  vol, USDTTMN premium z, open position) → strict-JSON opinion {bias, confidence, entry_ok,
  size_factor, key_levels, summary}. Every opinion stored for scoring.
- Hybrid decision policy: deterministic signal entries may be VETOED or size-SCALED-DOWN by
  the LLM; LLM-conviction entries allowed only at confidence ≥ 0.7 + entry_ok, half sizing,
  ONE slot, tagged `llm_conviction` so rule-engine vs LLM judgement are scored separately.
  Exits and liquidation are ALWAYS deterministic — the LLM can never block an exit.
- Dashboard v2: live prices ticker, equity curve, open positions with live PnL + liquidation
  distance + strategy tag, AI analyst panel (bias/confidence/levels/summary), trade log,
  decision feed, run-now and halt/resume buttons, per-path stats (rules vs AI).
- First live cycle: 9/9 coherent Persian analyses (market correctly read as bearish/range:
  premium z −0.89, most assets below SMA200 with negative 20d returns → no entries, all
  entry_ok=0). Degradation path also verified (initial empty-content failures → neutral
  fallback, trading loop unaffected; root cause = token cap, fixed with 1500 tokens).

Live-trading note (unchanged contract): Wallex key, when provided (file-based, trade-only,
no-withdrawal, IP-allowlisted), activates ONLY after ≥8 weeks paper evidence + dossier +
explicit owner approval.

---

## E017 — Correction + hardening round (2026-10-25)

**METRIC CORRECTION (important):** `segment_metrics` annualized Sharpe using
`DatetimeIndex.asi8`, whose unit changed in pandas 3 (not nanoseconds) — every
"portfolio Sharpe" printed by E008–E011 validations was inflated ~31×.
True values: E008a OOS Sharpe ≈ 0.77 (not 24.3); E011 portfolio OOS ≈ 0.58 (not 18.2).
Verdict changes: E008a's "Sharpe>1 PASS" line was bogus (now would FAIL); E011's PASS
included that bogus line — E011 was independently downgraded by Red-Team (E012) anyway.
Fix: unit-safe annualization via total_seconds(); regression test pins Sharpe<3 for a
10%-vol series. Trade counts / expectancy / maxDD criteria were unaffected.

**Runner hardening (paper heart):**
- per-symbol error isolation (one bad symbol can no longer kill the cycle)
- idempotency: cycle skips if already ran in the last 20h (cron) — dashboard run uses
  force=True (manual = intent)
- realistic fills: entries at ask+0.05% slippage, exits at bid−0.05%; liquidation watch
  uses the bid
- **auto-halt risk engine**: if equity falls >10% below peak → halted=1 + Telegram alert
  (plan §13 daily-loss halt, paper edition)

**Agentic upgrade — AI memory:** the analyst now receives its own last 3 opinions per
symbol WITH realized price outcomes ("گفتی صعودی ۶۰٪ → قیمت از آن روز −۴/۲٪") and an
instruction to learn from misses; per-day opinion caching saves duplicate LLM calls.

**Dashboard:** AI opinion history per coin, run-button polls until the cycle actually
finishes, sortable market table, peak/drawdown fields exposed.
Tests: 36 green (Sharpe sanity, bid/ask fills, peak/analyst helpers, idempotency data).
