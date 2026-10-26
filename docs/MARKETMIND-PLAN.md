# MarketMind AI — Autonomous Quantitative Research & Leveraged Trading Plan

**Wallex (wallex.ir) Margin Trading Program — v1.0**
**Date: 2026-10-25 · Status: PLAN — awaiting owner review before implementation**

Evidence conventions used throughout: **[CONFIRMED]** = verified from official docs or live API probe on 2026-10-25; **[ACCOUNT-VERIFIED-NEEDED]** = documented but requires the owner's account to verify; **[UNVERIFIED]** = could not be established; **[PROPOSAL]** = design recommendation.

---

## 1. Executive Summary

Wallex is an Iranian centralized exchange. Its leveraged product is **معامله تعهدی (margin/commitment trading)**: a collateral-and-loan margin system with **up to 10X leverage, long and short positions**, an activation fee at open, a renewal fee every 4 hours, a hard warning price, a liquidation price, a maximum position age of 21 days, and full API control including a pre-open `dry-run` simulation endpoint and server-side SL/TP. It is **not** a perpetual-futures market: there is no funding rate, no open interest, no derivatives order book — costs are borrowing-based and clock-based, which changes strategy economics fundamentally.

The single most important quantitative fact discovered: **fees dominate everything**. At Tier-1 fees (maker 0.25% / taker 0.30%), a taker-in/taker-out round trip costs ≈0.60% of notional **before** margin activation and renewal fees. At 10X leverage that is ≈6% of collateral per round trip. High-frequency or scalping strategies are arithmetically dead on arrival; only multi-hour to multi-day swing positions with maker-style entries, or event/macro-driven trades, can plausibly clear costs. The plan therefore treats **fee-aware cost modeling as the first-class research constraint**, not an afterthought.

The infrastructure finding: Wallex exposes **free public REST market data** (373 spot markets; 186 USDT-quote pairs; order book; trades; OHLCV candles at 15m/1h/4h/12h/1D from ≈Jan 2020) plus **WebSocket streams**, all verified live during this research. Historical depth (~6.8 years of daily BTCUSDT data) is sufficient for baseline statistical work on liquid pairs, thin for anything exotic. A complete data foundation can be built at **zero data cost**.

The strategic finding: Wallex-specific edges worth researching are (a) **local market structure** — the USDT/TMN rate ("Tether premium") is a live, Wallex-native macro/risk-appetite signal unavailable anywhere else; (b) **dislocation between Wallex prices and global reference prices** under capital controls; (c) classical trend/momentum/mean-reversion on BTC/ETH **only at holding periods long enough to amortize fees**. Global derivatives analytics (funding, OI, liquidations) can be used only as contextual features from third-party data, never as Wallex-execution assumptions.

The verdict on "can this be profitable": **unknown and unproven.** The honest prior, given fee tiers and 10X cap, is that most classical strategies will fail cost screening. The plan is built to discover this cheaply — a staged research ladder where the first live-money decision is months away, gated by baselines, walk-forward validation, adversarial review, paper trading, and explicit human approval.

Recommended immediate path: **Week 1** builds the data pipeline and backtest harness and runs the first fee-break-even study (no strategy yet). **Weeks 2–6** test baselines and the first 6–10 strategy hypotheses against cost-aware acceptance criteria. A go/no-go decision point on paper trading arrives only after a strategy survives the Red Team.

---

## 2. Environment and Capability Inventory

Inspected 2026-10-25 on the local machine (`/Users/radin/Desktop/MindMarket`).

| # | Capability / resource | Availability | How it will be used | Limitations | Setup / credentials needed | Verification |
|---|---|---|---|---|---|---|
| 1 | MindMarket git repo | **[CONFIRMED]** — exists, clean, only `README.md` (initial commit) | Home of the entire system; this document committed as first artifact | Greenfield; no reusable code | None | `git log` executed |
| 2 | Python 3.14 + numpy 2.4 / pandas 3.0 / scipy / scikit-learn / torch / matplotlib / jupyterlab / requests / websockets | **[CONFIRMED]** installed | Research, feature computation, ML, backtesting, tooling | No `ccxt`, `statsmodels`, `arch`, `vectorbt`; pandas 3.0 API changes; Python 3.14 may lack wheels for some packages | `uv venv` + `uv pip install` (uv present) | `pip3 list` executed |
| 3 | Node.js 24 | **[CONFIRMED]** | Only if needed for Wallex SDKs / dashboards | Not required for core | None | `node --version` executed |
| 4 | ZCode agent harness (subagents, workflows, cron/scheduled automations, bash, web fetch/search) | **[CONFIRMED]** | The multi-agent research system is implemented as ZCode agents + scheduled runs + dynamic workflows | WebSearch timed out repeatedly this session (flaky); WebFetch/webReader worked | None | Used during this research |
| 5 | Wallex public REST data (`/v1/markets`, `/v1/udf/history`, `/v1/depth`, `/v1/trades`) | **[CONFIRMED live** 2026-10-25**]** | Historical ingestion + live snapshots | 15m min granularity; history starts ≈Jan 2020 (BTCUSDT); resolutions limited to 15/60/240/720/1D; unknown rate limits | None | Live probes executed (§3) |
| 6 | Wallex WebSocket (candle, trade, buy/sell depth, fair price, all-prices, private channel) | **[CONFIRMED docs]** | Live monitoring, paper trading, spread/depth capture | Message schema not yet captured; stability unknown | None for public channels | Docs + first connection test |
| 7 | Wallex private REST (orders, balances, fees, margin endpoints incl. `dry-run`) | **[ACCOUNT-VERIFIED-NEEDED]** | Paper trading via dry-run; later live trading | Requires API key; **withdrawal permission must never be enabled** | Owner creates API key (max 3 keys), trade-only permission, IP allowlist | Key creation + `dry-run` call |
| 8 | Global market context data (Binance/Bybit public APIs, CoinGecko, etc.) | **[UNVERIFIED from this host]** — network reachability varies by region | Reference prices, global funding/OI/liquidations as features only | Reachability from Iran + sanctions blocking on some CDNs must be tested; never assume execution parity | None for public endpoints | Connectivity test in Week 1 |
| 9 | News / macro sources (Twitter/X, Telegram channels, CoinDesk, official Wallex announcements, CBI rates) | **[PARTIAL]** web fetch works; Telegram needs userbot or manual export | News/event features, regime context | Timestamp quality varies; manipulation risk high | None initially | Per-source test |
| 10 | On-chain data (Glassnode, CryptoQuant, Dune) | **[LIMITED]** free tiers only, delayed | Optional later-phase features | Free tiers delayed/no history; low priority | None | Deferred |
| 11 | Compute (local Mac, ARM) | **[CONFIRMED]** | All local execution: backtests, SQLite/Parquet, ML | No GPU-cluster scale; deep learning unrealistic and unwarranted | None | — |
| 12 | Capital | **[UNKNOWN]** | Final live allocation | Cannot size risk engine without it | Owner decision | §23 Q1 |

**What cannot be done autonomously:** creating the Wallex API key, reading account-level fee tiers, funding the account, enabling any live trading. These are owner actions; the system design assumes they arrive late and deliberately.

---

## 3. Verified Wallex Product and API Findings

Sources (all retrieved 2026-10-25): `developers.wallex.ir` (official API documentation), `wallex.ir` (homepage, `/fee`, `/api-document`), live probes of `api.wallex.ir`.

### 3.1 The margin product (معامله تعهدی) — mechanics

| Item | Finding | Status |
|---|---|---|
| Product type | Collateral-and-loan margin on spot markets (not perpetual futures; no funding rate) | **[CONFIRMED]** docs |
| Direction | Long (buy) and short (sell) positions both supported | **[CONFIRMED]** docs + homepage ("معامله تعهدی… تا ۱۰X") |
| Leverage | "نسبت اعتبار" (credit ratio) up to **10X**; per-position field `risk_coef` | **[CONFIRMED]** homepage + API schema |
| Collateral | Posted at open; editable later (`edit-collateral` endpoint) | **[CONFIRMED]** API docs |
| Loan | Exchange-provided loan per position; `calculate-loan` endpoint previews loan terms | **[CONFIRMED]** API docs |
| Activation fee | Charged at position open (exact % — see unknowns) | **[CONFIRMED]** docs; value **[UNVERIFIED]** |
| Renewal fee | Charged **every 4 hours**; accrues as `interest` on the position; deducted from collateral at close; renewal extends `expires_at` | **[CONFIRMED]** docs |
| Interest accounting | `interest` field on position; "نزد اعتبار نوشته می‌شود و هنگام بستن کسر می‌شود" | **[CONFIRMED]** docs |
| Warning (margin call) | `call_price` per position; `is_called` flag flips when reached | **[CONFIRMED]** API schema |
| Liquidation | `liquidity_price` per position; forced close with `close_reason` | **[CONFIRMED]** API schema |
| Max position age | `max_age` = **21 days**; extendable (`extend_time`) | **[CONFIRMED]** docs |
| Position close paths | Expiry, liquidation, manual close endpoint, SL/TP hit | **[CONFIRMED]** docs |
| SL/TP | Server-side; editable via `edit-sltp` | **[CONFIRMED]** API docs |
| Pre-open simulation | `dry-run` endpoint returns full position preview (prices, fees, interest) before committing | **[CONFIRMED]** API docs |
| Margin-eligible markets | Margin-active markets list exists via API | **[ACCOUNT-VERIFIED-NEEDED]** (endpoint requires key; public path 404) |
| Position PnL | `get-pnl` / position `profit` (unrealized) fields | **[CONFIRMED]** API docs |

**Position response schema (documented fields):** `call_price, close_reason, collateral, created_at, expires_at, extend_time, first_order_filled, id, interest, is_called, liquidity_price, loan, market, max_age, open_filled_price, open_price, opened_at, profit, risk_coef, side, state, success, updated_at`.

### 3.2 Fee schedule (spot; retrieved from wallex.ir/fee, 2026-10-25)

Toman-base-market tiers by 30-day volume:

| Tier | 30d volume (Toman) | Maker | Taker | Daily TMN withdrawal cap |
|---|---|---|---|---|
| 1 | 0 – 100M | 0.25% | 0.30% | 100M |
| 2 | 100M – 1B | 0.20% | 0.25% | 300M |
| 3 | 1 – 3B | 0.17% | 0.20% | 500M |
| 4 | 3 – 10B | 0.14% | 0.17% | 800M |
| 5 | 10 – 20B | 0.12% | 0.15% | 1B |
| 6 | > 20B | 0.10% | 0.13% | 2B |

Also confirmed: OTC instant trade fee **0%**; Toman deposit 0.01%, withdrawal 1% per 50M capped at 10,000 Toman; VIP tier exists above 500K USDT 30-day volume. **USDT-base-market fee table exists but its numbers were not machine-extractable — [ACCOUNT-VERIFIED-NEEDED].**

**Fee economics that drive this entire plan:** round-trip taker cost at Tier 1 ≈ **0.60% of notional** before margin activation + renewals. Renewal every 4h means holding cost accrues roughly ~×n over n periods (exact % unverified). At 10X, a Tier-1 taker round trip burns ≈6% of collateral. Consequences:

1. Strategies must target expected gross moves of **multiple percent** or use maker entries.
2. Holding period is a cost knob: every 4h of holding must be paid for by drift/volatility.
3. Fee tier matters enormously — a real edge at Tier 1 can be a false edge at Tier 6; backtests must run at the owner's *actual* tier.
4. High win-rate scalping is mathematically excluded. This is a feature of the research space, not a failure of imagination.

### 3.3 Trading API surface

**Spot orders** — `POST /v1/account/orders` (documented) with order types **Limit, Market, Stop Limit, Stop Market** **[CONFIRMED]**; client-side order id (`clientOrderId`) supported on create/query/cancel **[CONFIRMED]** — critical for idempotent submission and reconciliation. Spot endpoints for open orders, order list, single-order detail, cancel.

**Margin endpoints** — `activeMarkets`, `calculate-loan`, `dry-run`, open/close position, positions, `edit-collateral`, `edit-sltp` **[CONFIRMED docs]** (paths under `/v1/margin/...`; exact route names verified from official sitemap; behavior [ACCOUNT-VERIFIED-NEEDED]).

**Market data** — all confirmed live:

| Endpoint | Result of live probe (2026-10-25) |
|---|---|
| `GET /v1/markets` | 373 symbols (186 USDT-quote, 187 TMN-quote) with precision, stepSize, tickSize, minQty, minNotional, bid/ask, 24h/7d stats. Includes tokenized stocks (AAPLXUSDT, AMZNXUSDT…) |
| `GET /v1/udf/history` | TradingView-UDF OHLCV. Valid resolutions: **15, 60, 240, 720, 1D** (1, 5, 30, 1W → `Invalid resolution` / `no_data`). BTCUSDT daily exists from **2020-01-01**; 2017–2019 ranges → `no_data` |
| `GET /v1/depth?symbol=…` | Order book with per-level cumulative notional |
| `GET /v1/trades?symbol=…` | Latest trades with timestamp/side — (recent window only; historical trade archive [UNVERIFIED]) |

**WebSocket** — documented channels: connection/token, `candle`, `trade`, `buy-depth`, `sell-depth`, `fair-price`, `all-price`, private channel; raw-protocol and migration docs exist **[CONFIRMED docs; behavior UNVERIFIED]**.

**API keys** — created in account settings; **max 3 keys**; per-key permissions for trading and **withdrawal** (withdrawal capability must never be granted to the trading system); optional IP allowlist **[CONFIRMED docs]**. Official SDKs exist incl. Python (community/official wrappers; we will use plain `httpx`/`requests` first). API support: Telegram `@wallexapi`, `api@wallex.ir`. Docs support answers show **504 timeouts happen in practice** — retries/backoff are mandatory, and 504 on order placement must be treated as "unknown state" and reconciled, never blind-retried.

### 3.4 Capability classification

**Confirmed & usable now:** public market data (REST+WS), historical candles ≥15m from ≈2020 on majors, order book/trade snapshots, full product documentation, fee tiers (Toman table), margin product structure.

**Requires account-level verification:** margin-active market list, margin activation/renewal fee percentages, per-market `risk_coef` schedule, USDT-market fee tiers, account tier placement, private endpoint behaviors, rate limits (undocumented publicly), WebSocket private channel, `dry-run` output fidelity.

**Unsupported / unavailable:** perpetual futures or funding rates; options; native open-interest/liquidation history; sub-1-minute candles; historical tick data; cross-exchange execution; (no documented) reduce-only/post-only flags — **[UNVERIFIED whether limit orders can be marked post-only; assume not, and verify]**.

**Unknowns blocking implementation:** none fatal — all blockers have safe research-path workarounds (paper trading via `dry-run` + public data) until the owner supplies API-key access.

---

## 4. Critical Unknowns and Blockers

| # | Unknown | Why it matters | Safe interim assumption | Verification step |
|---|---|---|---|---|
| U1 | Margin activation & renewal fee % | Dominates holding-period economics | Model as parameter ∈ {0.1%, 0.2%, 0.4%}/4h; reject strategies not robust across the range | Owner: `dry-run` + `calculate-loan` probes with API key |
| U2 | Margin-eligible markets | Defines tradable universe | Restrict research to BTC/ETH USDT pairs until verified | `GET margin/activeMarkets` with key |
| U3 | Rate limits | Ingestion & live loop design | ≤1 req/s public, exponential backoff, jitter | Escalating-load probe from 1→10 req/s, back off on first 429 |
| U4 | Whether margin open/close legs pay spot fees (and at what side) | Cost model correctness | Assume worst case: taker both legs at owner's tier | `dry-run` output + first tiny live-equivalent test (paper stage) |
| U5 | Liquidation trigger price (last vs mark/fair) and liquidation penalty | Risk engine distance math | Assume last-price trigger, add 2× safety buffer to min distance | Docs query to @wallexapi + stress observation in paper stage |
| U6 | SL/TP execution semantics (stop-market at what price, slippage behavior) | Stop modeling in backtests | Model stop fills with 0.3% adverse slippage minimum | Paper-stage measured fills |
| U7 | WebSocket stability/message schema | Live system design | Poll REST as fallback; treat WS as enhancement | 72h soak test capturing schema |
| U8 | Owner capital, fee tier, risk tolerance | Sizing, thresholds, kill limits | Placeholder: conservative Tier-1 fees, small capital | §23 questions |
| U9 | Reachability of global data sources from operational network | Feature availability | Feature-flag all third-party sources; degrade gracefully | Week-1 connectivity matrix |
| U10 | Wallex historical data quality (gaps, outages, anomalies) | Backtest integrity | Data-quality scoring gate before any experiment | Week-1 gap audit of BTCUSDT/ETHUSDT 1h since 2020 |

**No blocker prevents starting.** The entire Stage-1 research ladder runs on public data + local compute.

---

## 5. Market-Data and News-Source Catalog

| Source | Type | Cost | History | Freshness | Primary use | Reliability | Licensing/notes |
|---|---|---|---|---|---|---|---|
| Wallex `/v1/udf/history` | OHLCV 15m–1D | Free | ≈2020→now (majors) | 15m | Core backtest data | High (official) | Rate limits unknown (U3) |
| Wallex `/v1/depth`, `/v1/trades` | Book/trades snapshot | Free | Snapshot only | Real-time | Spread/liquidity modeling, slippage calib | High | Start recording own archive immediately |
| Wallex `/v1/markets` | Metadata + TMN/USDT stats | Free | Current | Real-time | Universe, minNotional, **USDTTMN ticker** | High | — |
| Wallex WebSocket | Live candles/trades/book | Free | Live | Real-time | Paper trading, monitoring | Med (U7) | — |
| Wallex announcements (blog/Telegram) | Exchange events | Free | Site archive | Hours | Delistings, maintenance, margin param changes | High | Must be ingested — exchange-param changes are strategy risk |
| Binance/Bybit public REST/WS | Global OHLCV, funding, OI, liquidations | Free (public) | Years | Real-time | Reference price, dislocation features, global regime | High | **[U9 reachability]**; features only, never execution assumptions |
| CoinGecko public API | Cross-exchange aggregates | Free tier | Years | Minutes | Sanity checks, global cap | Med-High | Rate-limited |
| TradingView (Wallex UDF compatible) | Charting | Free | Same as UDF | 15m | Manual review only — never a data source for automation | n/a | — |
| CBI / TGju / bonbast-style TMN rates | FX macro | Free | Years | Daily | Tether-premium normalization | Med | Corroborate 2+ sources |
| CoinDesk/The Block/official project blogs | News | Free | Archive | Minutes | Event features | Med-High | Timestamp discipline required |
| Telegram crypto channels (IR) | Local sentiment | Free | Scrollback | Real-time | Sentiment features (late phase) | **Low** | Manipulation-prone; hard controls |
| Glassnode/CryptoQuant free tiers | On-chain | Free/delayed | Limited | Days | Optional features (deferred) | High-but-delayed | Low priority given delay |

**Decision:** Phase 1 uses Wallex-native data only. Global data (if reachable) enters Phase 2 as context features. News/Telegram enters last, only if price/volume features prove insufficient.

---

## 6. Technical-Analysis Research Matrix

Every family below is a **hypothesis to test against a cost-aware baseline**, not a default inclusion. "Min horizon" = shortest holding period at which fee load (0.6%+ round trip, Tier-1 assumption) can plausibly be cleared by that signal's typical move.

| Family | Candidates | Measures | Data need | Min horizon (fee logic) | Known weaknesses | Test design |
|---|---|---|---|---|---|---|
| Trend | EMA/SMA crossovers, Donchian breakouts, ADX-filtered trend | Persistence of directional drift | 1h/4h/1D | ≥1 day | Whipsaw in ranges; late entries | Baseline: donchian(20) long/short vs buy&hold vs random-entry risk-matched |
| Momentum | ROC(1d..4w), RSI regime filter, cross-sectional RS (BTC vs ETH vs alts) | Autocorrelation of returns | 1h/1D | ≥1 day | Crash reversals; crowding | Lagged-return predictive power (IC) before any signal construction |
| Mean reversion | Z-score of price vs 20d MA, Bollinger excursions, RSI-2 style | Negative autocorrelation at specific scales | 15m/1h | **Multi-day or nothing** — MR targets are small vs fees | Fee death; catching knives | Test only with maker entries; expected move must exceed 2× round-trip cost |
| Volatility | ATR percentile, realized-vol ratio, squeeze (BB inside KC) | Vol clustering, expansion timing | 1h/1D | n/a (regime filter first) | Not directional alone | Use as filter/regime input; measure conditional strategy performance |
| Volume/flow | OBV slope, volume z-score, MFI, price-volume divergence | Participation confirmation | 1h/1D | ≥1 day | Wallex volume includes bot/wash patterns (verify) | Conditional-lift test: does feature improve baseline after costs? |
| Market structure | Swing HH/HL/LH/LL (pivot rules), range compression, failed-breakout rules | Structure breaks | 15m/1h | ≥1 day | Subjective unless rule-encoded | Encode as deterministic pivot/ATR rules; ablation vs baseline |
| Statistical | Rolling Sharpe of drift, change-point (CUSUM/Bayesian), regime HMM-lite, autocorr structure | Regime boundaries | 1h/1D | n/a (meta) | Overfit via regime hindsight | Regime must be detectable in real time with ≤1-bar lag; walk-forward |
| Wallex-specific | **USDT/TMN premium z-score**, Wallex-vs-global price dislocation, TMN-pair vs USDT-pair spread, local book imbalance | Local capital-flow stress | 15m/1h | Hours–days (dislocations can be large) | Confounders (rial drift, holidays) | Unique differentiator — highest-priority novel family |
| Derivatives-context | Global funding rate, OI change, liquidation clusters (Binance et al.) | Global positioning | 1h | ≥1 day | Not Wallex-native; reachability (U9) | Context feature only; must improve Wallex-executed backtests to earn inclusion |

**Inclusion rule:** a feature/indicator family enters the production feature store only if it (a) adds out-of-sample lift to a cost-aware baseline, (b) survives ±100% parameter perturbation, (c) has a stated economic rationale, and (d) is computable from timestamped data available at decision time.

---

## 7. Candidate Strategy Catalog (initial hypotheses)

All specifications are skeletons to be formalized in the strategy registry (§10) before any backtest runs. Leverage assumption: 2–3X default (never 10X in research), Tier-1 fee pessimism.

| ID | Hypothesis (falsifiable) | Rationale | Type | Horizon | Entry (draft) | Exit/Stop (draft) | Cost hurdle | Rejection criteria (draft) |
|---|---|---|---|---|---|---|---|---|
| S01 | BTC trend-following (Donchian/EMA) on 1D clears costs after fees | Drift persistence in BTC | Trend | Days–weeks (bounded by 21d max age) | Close > 20d Donchian + ADX>20 | Opposite channel or 2×ATR trail | Needs ~1.5%+ net expected move | Sharpe<0.5 OOS or maxDD>25% on 3X |
| S02 | ETH/BTC cross-sectional momentum rotation | Relative strength persists | Momentum | Weeks | Hold stronger of BTC/ETH, rebalance weekly | Rank reversal | Turnover-heavy; weekly only | Net < buy-hold-both after costs |
| S03 | Volatility-squeeze breakout (1D) with vol filter | Compression precedes expansion | Breakout | Days | BB-width percentile <20 then break with volume | Failed-break rule (reentry into range) | ~1 breakout/2 weeks per asset | Win-rate signal but negative expectancy after fees |
| S04 | **USDT/TMN premium extreme → risk-off BTC stance** | Local capital flight signal: premium spikes track IR market stress | Macro/event (Wallex-native) | Days | Premium z>2.5 → reduce/close longs; z<-2.5 → risk-on re-entry | z normalization | Low turnover (event-driven) | No conditional lift on BTC 1D returns |
| S05 | Wallex-vs-global dislocation fade on BTC/ETH | Capital controls create slow reversion of local premium/discount | Stat-arb-lite (single venue) | Hours–days | Wallex USDT-pair deviates >x%σ from global reference (maker entry) | Deviation collapse or widen stop | Maker entry mandatory | Dislocation mean-reverts slower than renewal fees accumulate |
| S06 | Multi-day mean reversion after capitulation (RSI-2 style, 1D) | Overreaction rebounds | Mean reversion | 2–5 days | RSI2<10 with price>200d MA, maker limit at support | Time stop 5d (max-age aware) | Maker entry + ≥3% target | Median trade PnL < 0 after pessimistic fees |
| S07 | Range-trading between regime boundaries (ETH 1D, range regime only) | Ranges persist until they don't | Range | Days | Buy range-low maker / sell range-high maker (long-only) | Range break = stop | Maker×2 keeps hurdle ~0.5% | Range classification lag destroys edge |
| S08 | Global-funding-context filter on S01 (funding extreme blocks trend entries) | Crowded positioning degrades breakouts | Hybrid | (filter on S01) | S01 entry suppressed when global funding z>2 | — | n/a | No improvement to S01 OOS Sharpe |
| S09 | News/event blackout windows (CBI announcements, known macro calendar) | Forced-liquidation risk around events | Risk overlay | n/a | No new margin positions within T hours of scheduled events | — | n/a | No reduction in tail losses |
| S10 | ML ensemble (§8) vs S01 baseline | Nonlinear interactions add lift | ML | As learned | Model probability threshold + regime gate | Model-driven + hard ATR stop | Same as S01 | No OOS lift after costs → ML track closed |

Strategies deliberately **not** pursued: order-book microstructure/HFT (fee + latency impossibility), triangular arb (OTC 0% fee hints Wallex itself occupies that niche), funding-rate carry (product doesn't exist on Wallex), grid bots (Wallex ships a margin-grid bot — treat as benchmark to beat, not a research line).

---

## 8. Machine-Learning and Pattern-Discovery Plan

**Philosophy:** simple → complex; every model must beat §7 baselines *after costs*; interpretability first.

1. **Targets:** forward k-bar return sign and volatility-normalized return (k ∈ {1d, 3d, 7d} aligned to fee-viable horizons); secondary: drawdown-risk classifier for position management.
2. **Features:** TA families (§6) at decision-time-computable lags; regime flags; Wallex-native (premium z, dislocation, book imbalance); global-context if reachable. Every feature carries `available_at_ts` metadata; pipeline enforces as-of joins — leakage test is a CI gate.
3. **Models in order:** penalized logistic → gradient boosting (LightGBM/XGBoost) → (only if justified) small sequence models. Calibrated probabilities (isotonic/Platt), `class_probability` thresholds mapped to *expected value after fees* decisions, not accuracy.
4. **Validation:** expanding-window walk-forward, purged+embargoed folds; final holdout = last 12 months, touched once per strategy generation. Deflated Sharpe / White's reality check style multiple-testing correction given the experiment registry count.
5. **Drift:** PSI on features, rolling calibration curves; retrain only on schedule + human sign-off; auto-rollback to previous frozen model if paper metrics breach bounds.
6. **RL track:** explicitly deferred; only reconsidered after supervised signals prove out, with realistic cost/liquidation environment. Default assumption: RL not needed.

---

## 9. Multi-Agent Architecture (ZCode-native)

Implemented as ZCode agents/subagents + scheduled automations + workflows — no new infrastructure. Twelve roles, staged activation (not all at once):

| Agent | Responsibility | I/O contract (JSON) | Tools/permissions | Activates | Reviews / reviewed by |
|---|---|---|---|---|---|
| A. Research-Coordinator | Backlog, experiment registry, de-duplication, evidence summaries; **cannot** declare profitability | `ExperimentSpec`, `ExperimentResult` | Read registry; write `research/`; no API keys | Phase 1 | Reviews all; audited by H |
| B. Regime-Analyst | Trend/range/vol/stress classification with uncertainty; real-time detectability test | `RegimeReport{regime, probs, confidence, ts}` | Feature store read | Phase 2 | Reviewed by H; used by K |
| C. Quant-Analyst | Indicator/feature hypotheses, statistical tests, parameter sensitivity | `FeatureStudy{feature, stats, lift, failures}` | Research data, notebooks | Phase 1 | H adversarial review |
| D. News-Analyst | Timestamped event capture, source scoring, fact-vs-spec labels | `EventFeed{ts, source_tier, class, assets}` | Web fetch; no trading | Phase 3 (optional) | K consumes; H audits timestamps |
| E. Liquidity-Analyst | Spread/depth/slippage estimation, cost model calibration from own recordings | `CostModel{per-market params, CI}` | Wallex public data | Phase 1 | Feeds I and J |
| F. ML-Researcher | Feature/model experiments, failure log | `ModelCard{target, features, val results, caveats}` | Research data, sklearn/lightgbm | Phase 2 | H red-teams for leakage |
| G. Strategy-Engineer | Approved hypothesis → versioned strategy spec (entry/exit/sizing/costs) | `StrategySpec.yaml` (schema §10) | Repo write `strategies/` | Phase 2 | I backtests; H reviews |
| H. Red-Team / Critic | Attacks every result: leakage, overfit, fills, regime dependence; can veto | `CritiqueReport{verdict, attacks, required-fixes}` | Read everything; write critiques | Phase 1 | Escalates to owner on disputes |
| I. Backtest-Validator | Runs registry experiments, maintains holdout discipline, reproducible metrics | `BacktestReport{metrics, costs, sensitivity, n_experiments}` | Backtest engine; holdout key-holder | Phase 1 | Independent of G |
| J. Risk-Analyst (deterministic) | Enforces hard limits; veto power; **not an LLM** — plain Python service | `RiskDecision{allow, reason, limits_version}` | Account state + limits.yaml | Phase 4 (live prep) | Owner-owned config; LLMs cannot modify |
| K. Decision-Aggregator | Merges B,C,D,E,F outputs → trade proposal with confidence + contradictions | `TradeProposal` (§14 schema) | Read agent outputs | Phase 3–4 | J gates; H audits |
| L. Monitoring-Analyst | Paper/live drift, data health, cost drift, degradation alarms | `HealthReport` | Telemetry DB | Phase 4 | Pages owner via logs/reports |

**Anti-pathology rules:** every experiment must cite a registry ID (no orphan runs); duplicate detection via hypothesis hash; hard budgets (max experiments/week, max LLM calls/experiment); holdout access only through I; no agent other than the live executor touches authenticated endpoints, and only within J-approved envelopes.

---

## 10. Data Pipeline and Storage Design

```
wallex REST/WS ──► ingest (polite client, backoff, jitter)
                    │ raw/ parquet (append-only, immutable)
                    ▼
              validator (gaps, outliers, monotonic ts, duplicate check, DQ score)
                    │ clean/ parquet, versioned daily snapshots
                    ▼
              features (as-of joins, available_at_ts enforced) ──► feature_store (parquet + DuckDB)
                    ▼
              experiments (BacktestReport, ExperimentSpec, ModelCard, CritiqueReport)
                    ▼
              paper/live (orders, fills, positions, risk_events, agent_decisions, approvals, audit)
```

Storage: **SQLite** (catalog, registry, paper trading, audit) + **Parquet/DuckDB** (candles, features, book recordings). One repo; raw immutable; every experiment = hash of (data snapshot id + code SHA + config). Rejected-hypothesis ledger prevents rediscovering dead patterns.

Core tables: `symbols, candles(symbol,ts,o,h,l,c,v,dq_score), book_snapshots, trades_raw, usdt_tmn_premium, features(feature,ts,value,available_at), experiments, strategies(id,version,spec_hash), backtest_results, models, paper_orders, paper_fills, paper_positions, cost_model_params, risk_events, agent_decisions, human_approvals, audit_log, system_errors`.

Retention/backup: raw never edited; nightly local backup + weekly off-machine copy; integrity via per-file checksums; recovery drill documented.

---

## 11. Backtesting Methodology

Custom event-driven engine (Wallex margin mechanics don't exist in off-the-shelf backtesters; vectorbt/backtrader rejected as primary — no 4h-renewal/21d-age/liquidation semantics, though backtrader may be borrowed for ideas).

**Must model:** maker/taker fees at owner's tier; spread from recorded book snapshots (spread archive starts Week 1); slippage = f(order size vs recorded depth); margin activation + 4h renewal accrual; `risk_coef` leverage, `call_price`/`liquidity_price` from documented formulas (verified vs `dry-run` output); 21-day max age; SL/TP with ≥0.3% adverse gap assumption; intrabar ambiguity resolved conservatively (if OHLC ordering matters — e.g., stop and TP in same bar — assume worst ordering, or drop to 15m data); concurrent-position portfolio accounting; API-failure days (halt trading when DQ low).

**Must prevent:** lookahead (as-of feature joins, CI test); warm-up leakage; fill fantasy (maker fills require price crossing the limit with book-present check); same-data optimize/evaluate; selection bias (report experiment count; deflated metrics).

**Standard outputs per run:** net/gross return, fee share, expectancy, profit factor, win rate, payoff ratio, Sharpe/Sortino, max DD + duration, exposure, turnover, trades, tail (CVaR5%), consecutive losses, per-regime/per-year breakdown, and sensitivity grid: fees ×{0.5,1,1.5,2}, slippage ×{0.5,1,2}, params ±20%, latency {0,1,3} bars.

---

## 12. Statistical Validation and Acceptance Criteria

Protocol per strategy: **IS (2020–2023) research → validation (2024–2025) → locked OOS holdout (last 12m, one shot) → walk-forward rolling → paper (≥8 weeks live-data) → human review.** Monte-Carlo/bootstrap CIs on expectancy and maxDD; stationary block bootstrap for trade resampling; deflated Sharpe given registry experiment count; parameter-plateau requirement (no cliff edges); multi-regime table mandatory.

**Proposed starting thresholds** (calibrate to owner's capital/risk in §23 — these are proposals, not gospel, and Tier-1 fees are baked in):

| Metric | Research-accept (paper gate) | Live-gate (Stage 6) |
|---|---|---|
| OOS net expectancy/trade | > 2× round-trip pessimistic cost | Maintained in paper ≥8w |
| OOS Sharpe (annualized, net) | > 1.0 | Paper Sharpe > 0.5 and >0 with 95% CI lower bound |
| Max drawdown (3X lev) | < 20% | < 15% in paper |
| Trades (OOS) | ≥ 50 (else "insufficient evidence") | — |
| Cost stress ×1.5 | Still profitable | — |
| Parameter ±20% | Sign of expectancy unchanged | — |
| Profit factor | > 1.25 after costs | > 1.15 in paper |

Research acceptance ≠ live approval. Fail → reject/revise/research; never re-test until it passes (that's the multiple-testing trap). The holdout is single-shot per strategy version.

---

## 13. Leveraged-Trading Risk-Engine Specification

A **deterministic Python service** (no LLM in the loop), config in owner-signed `limits.yaml`:

| Control | Default (proposal) |
|---|---|
| Max risk per position (collateral at loss-stop) | 1% of allocated capital |
| Max concurrent positions | 2 |
| Max gross exposure | 3× allocated capital |
| Max leverage (`risk_coef`) | 3X (10X allowed by exchange; system cap lower) |
| Min liquidation distance | max(3×ATR(1d), 12%) at entry, checked continuously |
| Min warning-price distance (`call_price`) | 8% |
| Daily loss halt | −2% of allocated capital → no new risk-increasing orders until human reset |
| Weekly drawdown halt | −5% → flatten-or-hold decision document, default reduce |
| Portfolio max DD kill | −15% → full stop, flatten all (respecting slippage), lock |
| Single-asset concentration | ≤ 60% gross |
| Min data quality / staleness | DQ ≥ 0.9 and book age < 30s else **fail-closed**: no new orders; existing SL/TP left armed |
| Max spread/slippage gate | spread > 0.15% or est. slippage > 0.2% → block entry |
| Max renewal-fee drag | projected holding cost > 50% of expected net edge → block |
| Event blackout | no entries within config window of scheduled macro/exchange events |
| Duplicate/idempotency | `clientOrderId` required on every order; 504 → reconcile-then-decide, never blind retry |
| Kill switch | One command flattens all (market close via `close-position`), cancels all, disables system key |

Stress scenarios modeled before any live gate: overnight gap −15%, book thinning ×5 spread, renewal-fee parameter doubling, exchange outage mid-position (SL/TP server-side mitigates — verify U6), stale feed trading, partial fill on close, TMN devaluation shock affecting USDT/TMN accounting.

**LLM agents can propose; only J (deterministic) permits. Limits change = owner-signed config commit + audit entry. No withdrawal capability anywhere in the system. Ever.**

---

## 14. Per-Trade Decision Workflow

```
1  Data validity (DQ, staleness, spread) ──fail──► ABSTAIN (fail-closed)
2  Regime + uncertainty (B)
3  Candidate signals from approved strategy versions (G)
4  Evidence for/against aggregation (K) — incl. contradicting evidence mandatory
5  Event-risk check (D/S09)
6  Cost projection (E): fees + activation + renewals(horizon) + slippage
7  Expected net payoff distribution + uncertainty (K)
8  Sizing (vol-targeted, Kelly-capped at 0.25×)
9  Leverage & liquidation-distance computation (Wallex formulas, dry-run cross-check)
10 Hard risk gates (J) — all must pass
11 Strategy approved-for-environment check (version whitelist, regime map)
12 Decision: TRADE / REJECT / ABSTAIN (weak/contradictory/stale evidence ⇒ ABSTAIN)
13 Persist full TradeProposal JSON (schema below) + every input snapshot hash
14 Monitor: SL/TP server-side + local watchdog; drift & rule-based exits
```

`TradeProposal` JSON: `{ts, market, product:"margin", strategy_id, strategy_version, regime{label,probs}, side, entry_plan{type,price,qty,maker?}, invalidations[], sl, tp, horizon_days, collateral, risk_coef, est_costs{open_fee, renewals_n×r, close_fee, slippage_p50, slippage_p95}, expected{net_edge_p50, ci80, payoff_dist_ref}, liquidation{liquidity_price, distance_pct, call_price}, evidence[{agent,finding,weight}], contradicting_evidence[], confidence{calibrated_score, basis}, data_quality, risk_checks[{check,pass,limit_version}], approval{status:"PENDING_HUMAN"|"AUTO_WITHIN_LIMITS", approver_id}, final_action}`

LLM verbal confidence is never treated as a probability; calibrated scores come from hit-rate tracking per agent per condition (Brier-scored, monitored by L).

---

## 15. Wallex Integration and Security Plan

- **Client:** small `wallex-client` Python package (httpx, http2, retries with jitter, circuit breaker); per-endpoint timeout budget; every request/response logged (redacted).
- **Auth:** API key header (exact scheme verified at key creation); key stored in macOS Keychain → env-injected at runtime; **trade-only permission, IP-allowlisted, no withdrawal scope**; max-3-keys budget: one for system, one for human ops, one spare.
- **Reliability:** `clientOrderId` idempotency; order-state reconciliation loop (local state vs `get-orders`/positions) every cycle; 5xx/timeout ⇒ quarantine that order id until reconciled; clock-skew guard (NTP check before signing/ordering).
- **WS:** public channels first (candle/trade/depth/fair-price); REST fallback polling; private channel only for live stage; schema captured and versioned.
- **Never:** keys in git/logs/prompts/model context; withdrawal-capable keys; hard-coded assumptions where docs are silent — every unknown routes to the U1–U10 verification list.
- **Auditing:** append-only audit log of every authenticated call (method, path, params-hash, result, latency); weekly export reviewed by owner.

---

## 16. Paper-Trading Design (Stage 4)

Paper engine consumes **live** Wallex data and executes the real decision pipeline (§14) with simulated fills: maker fills require traded-through price + book presence; taker fills at book-aware prices; renewal fees accrue per real clock; SL/TP simulated with configured slippage. Additionally, cross-validate simulator honesty against the exchange's own **`dry-run` endpoint** (real pre-trade preview without commitment) — discrepancies become cost-model calibration items. Duration ≥ 8 weeks, must include at least one high-vol episode; weekly HealthReport; degradation triggers (L) defined up-front: e.g., realized slippage > 2× model, expectancy CI crossing zero, DQ failures > 1%/day.

---

## 17. Human-Approval and Controlled Live-Deployment Process

Gate sequence: research-acceptance (I) → Red-Team pass (H) → paper gate (L metrics) → **owner reads the evidence dossier** (hypothesis, methodology, OOS+paper results, costs, risks, failure modes, unknowns, proposed limits) → owner signs approval record (hash of strategy version + limits version) → Stage 6 with: minimal allocation (proposal: ≤ owner-defined pilot capital, e.g. 5–10% of intended), 2X max leverage initially, 1 concurrent position, daily loss halt −1%, mandatory weekly review, full audit. Live strategy version frozen; any change (params, model, limits) = new validation cycle + new approval. Emergency: kill switch (§13) + post-mortem template.

---

## 18. Monitoring and Ongoing Research

Live: position/watchdog loop (every cycle: prices fresh? SL/TP armed? distance to `call_price`? renewal-fee drag vs remaining edge?), reconciliation vs exchange state, fee-drift tracker, DQ dashboards, alert log. Research isolation: live strategy pinned in `production/`; all experiments in `research/` namespaces; holdout untouched; LLM token/API budgets tracked. Weekly: regime review, paper-vs-backtest tracking error, new-hypothesis triage. Monthly: cost-model recalibration, drift report, risk-limit review with owner.

---

## 19. Technology Stack (with alternatives)

| Layer | Choice | Alternatives | Rationale |
|---|---|---|---|
| Language | Python 3.12 in `uv` venv (3.14 if wheels allow) | Node (Wallex SDKs exist) | Quant ecosystem; pinned venv avoids 3.14 wheel gaps |
| Data | Parquet + DuckDB + SQLite | TimescaleDB, ClickHouse | Zero-ops, local, fast enough for years of 15m data |
| HTTP/WS | httpx + websockets | aiohttp | Reliability patterns, timeouts |
| Analysis | pandas/polars, numpy, scipy, statsmodels | — | statsmodels added (not installed) |
| Backtest | Custom event-driven engine | vectorbt (vectorized cross-check), backtrader | Wallex margin semantics are custom |
| ML | scikit-learn, LightGBM | XGBoost, torch (installed, deferred) | Interpretable-first |
| Experiments | Registry in SQLite + MLflow-file mode | DVC, W&B | Local, reproducible, no external service |
| Agents | ZCode agents + workflows + cron | LangGraph/CrewAI (rejected) | Already present; no new infra, no token sprawl |
| Scheduling | ZCode automations + launchd | cron, APScheduler | Survives restarts |
| Monitoring | Structured logs + SQL reports + Grafana-lite (optional) | Prometheus | Keep minimal |
| Secrets | macOS Keychain + env | Vault (overkill) | Local single-user |

Costs: **$0 recurring** for data and infra (all local + free endpoints). Optional paid later: better news feeds, VPS (~$5–10/mo) if 24/7 uptime demanded at live stage — decision deferred to owner.

---

## 20. Implementation Roadmap

Legend: D=depends on. Effort in focused hours (solo owner + AI assistance).

**M0 — Foundations (Week 1)**
- **T01** Repo scaffold, uv venv, pin deps (pandas, numpy, scipy, statsmodels, lightgbm, httpx, websockets, pytest). D:—. 2h. Done: `uv run pytest` green, CI-style checks script.
- **T02** `wallex-client` public module: markets, depth, trades, udf/history with backoff+cache. D:T01. 6h. Done: fetch all 373 markets, BTCUSDT 2020→now 1D+1h+15m to parquet.
- **T03** Historical backfill (majors first: BTCUSDT, ETHUSDT, USDTTMN; then top-20 USDT pairs by volume). D:T02. 4h. Done: gap audit report; DQ scores per symbol.
- **T04** Book/trade recorder (WS + REST poll fallback) → book_snapshots. D:T02. 6h. Done: 72h continuous recording, schema documented.
- **T05** U-verification battery #1 (no key): rate-limit probe, WS soak, resolution matrix, earliest-history per symbol. D:T02. 3h. Done: unknowns table updated with measured values.
- **T06** Fee & cost model v0 (Tier-1 pessimistic; U1 parameters). D:T03. 3h. Done: break-even move table per horizon×leverage — **first go/no-go evidence**.
- **T07** Baseline backtest engine v0 (spot, long/short, fees, no margin yet) + S01 baseline run. D:T03,T06. 8h. Done: reproducible BacktestReport for Donchian-20 on BTCUSDT 1D 2020–2025.

**M1 — Margin Realism (Weeks 2–3)**
- **T08** Margin cost layer: activation+4h renewal accrual, 21d age, call/liquidation formulas (from docs; parameterized pending U1). D:T06. 6h.
- **T09** Backtest engine v1: margin positions, SL/TP conservative fills, portfolio accounting. D:T07,T08. 10h.
- **T10** Strategy registry + experiment ledger schemas; H (Red-Team) checklist automation. D:T07. 4h.
- **T11** Walk-forward + sensitivity harness + deflated-metric reporting. D:T09. 8h.
- **T12** Run S01–S07 first pass with honest costs; log verdicts (expect rejections — that is success of method). D:T09,T10,T11. 8h.

**M2 — Wallex-Native Edge (Weeks 3–5)**
- **T13** USDTTMN premium + dislocation feature pipeline. D:T03. 6h.
- **T14** S04/S05 formal tests. D:T13,T11. 8h.
- **T15** Regime agent (B) v1: detectable-in-real-time regime classifier + uncertainty. D:T03. 8h.
- **T16** Global-data connectivity matrix (U9); optional funding/OI features. D:T02. 4h.
- **T17** ML track v1 (features → LightGBM → calibrated EV decisions) vs baselines. D:T12,T13,T15. 12h.
- **T18** Mid-point review: kill or continue each line; update registry. D:all above. 2h. **Go/no-go gate toward paper trading.**

**M3 — Paper Stage Prep (Weeks 5–7)**
- **T19** Owner creates trade-only API key; U1–U6 verified via `dry-run`/`calculate-loan`; cost model recalibrated to reality. D:owner. 2h+owner.
- **T20** Paper engine (live data + simulated fills + dry-run cross-checks). D:T09,T19. 10h.
- **T21** Decision pipeline (§14) + TradeProposal persistence. D:T20. 8h.
- **T22** Risk engine J v1 (deterministic, fail-closed, kill switch) in simulation mode. D:T21. 8h.
- **T23** ≥8-week paper run with weekly HealthReports (L). D:T22. calendar time.

**M4 — Live Readiness (after paper gate)**
- **T24** Evidence dossier generator; owner approval workflow. **T25** Live executor with idempotency+reconciliation. **T26** Pilot deployment per §17. **T27** Continuous monitoring + monthly review cadence.

Critical path: T01→T02→T03→T06→T07→T09→T12→T18→T20→T23. Parallelizable: T04/T05/T10/T13/T15/T16.

---

## 21. Cost and Effort Estimates

Owner cost: data $0; software $0; compute local. Optional VPS at live stage ≈ $5–10/mo (**estimate**). Effort to paper-ready (M0–M3): ≈ 115–130 focused hours over 6–8 calendar weeks with AI assistance — **estimate**, assuming part-time. To live pilot: +25–40h plus 8+ weeks paper calendar time and owner approvals. Token/LLM budget: bounded by agent budgets (§9); estimate ≤ $20/mo equivalent if using metered models; $0 if local plan.

## 22. Failure Scenarios and Mitigations

| Scenario | Mitigation |
|---|---|
| All strategies fail cost screen | Expected outcome by prior; deliverable is the honest negative result + narrowed conditions; system pivots to monitoring/context value |
| Overfit strategy reaches paper | Red-Team gate, holdout single-shot, deflated metrics, paper gate |
| Exchange param change (fees, risk_coef schedule) | Announcement ingestion; cost model parameterized; versioned re-run auto-flag |
| Liquidation despite stops (gap/illiquidity) | 3X cap, min-distance rules, server-side SL/TP, event blackouts, stress tests |
| API 504 → duplicate order | clientOrderId idempotency + reconcile-then-act quarantine |
| Withdrawal-key compromise | Withdrawal scope never enabled; IP allowlist; key rotation runbook |
| Data outage silently poisons signals | DQ gate + staleness fail-closed |
| TMN/USDT accounting shock | Premium feature monitoring; USDT-quote markets primary; capital caps |
| Owner over-trust in automation | Hard human-approval gates; frozen versions; audit trail |
| Scope creep / infinite research loops | Experiment budgets, registry de-dup, staged gates |

## 23. Unresolved Questions for the Owner

1. Capital allocation ceiling and acceptable max drawdown (sizes every threshold in §12–13)?
2. Current Wallex fee tier / 30d volume (USDT-base-market table)?
3. API key creation now (trade-only, IP-allowlisted) or after M2?
4. Preferred leverage ceiling (proposal: system-capped 3X)?
5. Trading universe preference — BTC/ETH only, or include liquid alts?
6. Is a VPS/24-7 uptime desired at live stage, or local-hours operation?
7. Risk appetite for the unique Wallex-native strategies (S04/S05) vs classical only?
8. Any existing Wallex margin experience (observed activation/renewal fees, liquidation events) to seed U1/U5?
9. Notification channel for kill-switch/alerts?
10. Confirm: withdrawal permission on any key is permanently out of scope?

## 24. First-Week Action Plan (prioritized)

1. **T01–T02** — scaffold repo + public client (day 1).
2. **T03** — backfill BTCUSDT/ETHUSDT/USDTTMN all resolutions; publish gap/DQ audit (day 2).
3. **T04** — start book/trade recorder; let it run all week (day 2).
4. **T06** — fee-economics v0: break-even table per horizon×leverage×renewal-fee scenario (day 3). **This is the week's headline deliverable.**
5. **T05** — rate-limit + WS soak tests; update U-table (day 4).
6. **T07** — baseline engine + Donchian-20 BTCUSDT first honest backtest (days 4–5).
7. **T10** — registry + Red-Team checklist (day 5).
8. Owner: answer §23 Q1–Q3; optionally schedule API-key creation for Week 5.

## 25. Final Readiness Checklist (before any live deployment)

- [ ] U1–U6 verified against the owner's real account (`dry-run`, `calculate-loan`, fee endpoints)
- [ ] ≥1 strategy passed: OOS single-shot + walk-forward + cost-stress ×1.5 + parameter plateau + ≥50 OOS trades
- [ ] Red-Team critique resolved without material findings
- [ ] ≥8 weeks paper trading meeting §12 live-gate; includes one high-vol episode
- [ ] Paper-vs-dry-run discrepancy < agreed tolerance
- [ ] Risk engine J passes scenario suite (gap, outage, duplicate-order, stale-data, kill switch) in simulation
- [ ] Live API key: trade-only, IP-allowlisted, withdrawal scope never enabled, stored in Keychain
- [ ] Idempotency + reconciliation proven under injected 5xx
- [ ] Owner-signed approval record (strategy version + limits version hash)
- [ ] Pilot allocation ≤ agreed ceiling; kill switch drilled; audit export scheduled
- [ ] Explicit acknowledgement: past performance ≠ future; capital at risk of total loss

---

### Appendix A — Evidence Index (retrieved 2026-10-25)

| Claim | Source |
|---|---|
| Margin product ≤10X, long/short | wallex.ir homepage ("نسبت اعتبار در بازار تعهدی والکس تا ۱۰X") |
| Activation fee / 4h renewal / 21d max age / interest-to-collateral | developers.wallex.ir/docs/margin-intro |
| Position schema (call_price, liquidity_price, risk_coef, interest, max_age…) | developers.wallex.ir/docs/margin-types |
| Margin endpoints incl. dry-run | developers.wallex.ir sitemap + /docs/margin-* |
| Candle endpoint `/v1/udf/history` | developers.wallex.ir/docs/generated/candle-history |
| Order types & clientOrderId & API-key rules | developers.wallex.ir/docs + wallex.ir/api-document |
| Fee tiers (Toman table) | wallex.ir/fee |
| 373 markets; live depth/trades/candles; resolutions 15/60/240/720/1D; history from 2020-01-01 | live probes of api.wallex.ir (this session) |
| 504 timeouts occur | support answer on developers.wallex.ir FAQ |
| Margin-grid bot exists | wallex.ir/bots/create/margin-grid (sitemap) |

*Negative results recorded honestly: wallex.it unreachable (not the target exchange); 1/5/30m/1W candles unsupported; pre-2019–2020 history absent; margin-markets public route 404 (auth required); WebSearch tool flaky this session.*
