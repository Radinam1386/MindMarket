"""MarketMind CLI.

    uv run python -m marketmind.cli <command>

Commands: markets, backfill, audit, costreport, baseline
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

import pandas as pd

from . import __version__
from .analytics import costmodel
from .backtest import baselines
from .backtest.engine import run
from .config import (
    CANDLES_DIR,
    MARKETS_DIR,
    REPORTS_DIR,
    TAKER_FEE,
    ensure_dirs,
)
from .data import ingest, quality
from .wallex.client import WallexPublicClient

log = logging.getLogger("marketmind")

DEFAULT_BACKFILL = [
    ("BTCUSDT", "15"), ("BTCUSDT", "60"), ("BTCUSDT", "240"), ("BTCUSDT", "1D"),
    ("ETHUSDT", "60"), ("ETHUSDT", "240"), ("ETHUSDT", "1D"),
    ("USDTTMN", "60"), ("USDTTMN", "1D"),
]


def cmd_markets(args) -> None:
    ensure_dirs()
    client = WallexPublicClient()
    symbols = client.markets()
    out = MARKETS_DIR / "latest.json"
    out.write_text(json.dumps(symbols, ensure_ascii=False, indent=1))
    rows = []
    for s, m in symbols.items():
        qv = m.get("stats", {}).get("24h_quoteVolume")
        try:
            qv = float(qv)
        except (TypeError, ValueError):
            qv = 0.0
        rows.append({"symbol": s, "base": m.get("baseAsset"), "quote": m.get("quoteAsset"),
                     "last": m.get("stats", {}).get("lastPrice"), "quote_vol_24h": qv})
    df = pd.DataFrame(rows).sort_values("quote_vol_24h", ascending=False)
    print(f"{len(df)} markets saved to {out}")
    print("\nTop 15 by 24h quote volume:")
    print(df.head(15).to_string(index=False))


def cmd_backfill(args) -> None:
    ensure_dirs()
    pairs = DEFAULT_BACKFILL if args.all else [(args.symbol, args.resolution)]
    if args.symbol and args.resolution and not args.all:
        pairs = [(args.symbol, args.resolution)]
    client = WallexPublicClient()
    for symbol, resolution in pairs:
        print(f"backfilling {symbol} {resolution} ...", flush=True)
        df = ingest.backfill(client, symbol, resolution)
        path, merged = ingest.save_candles(df, symbol, resolution)
        a = quality.audit(merged, resolution)
        print(f"  -> {path.name}: {len(merged)} bars, DQ={a['dq']}, "
              f"missing={a['missing']}, {a['start']}..{a['end']}")


def cmd_audit(args) -> None:
    ensure_dirs()
    df = quality.audit_all()
    if df.empty:
        print("No stored series. Run `backfill` first.")
        return
    view = df.copy()
    for col in ("start", "end"):
        view[col] = view[col].map(lambda v: pd.to_datetime(v, unit="s", utc=True).strftime("%Y-%m-%d"))
    print(view.to_string(index=False))
    report = quality.render_report(df)
    out = REPORTS_DIR / "data-quality.md"
    out.write_text(report)
    bad = df[df["dq"] < quality.MIN_DQ]
    print(f"\nDQ report -> {out}")
    if len(bad):
        print(f"WARNING: {len(bad)} series below MIN_DQ={quality.MIN_DQ}:")
        print(bad[["symbol", "resolution", "dq"]].to_string(index=False))


def cmd_costreport(args) -> None:
    ensure_dirs()
    headline = costmodel.headline_table()
    full = costmodel.breakeven_table()
    full_path = REPORTS_DIR / "cost-breakeven-full.csv"
    full.to_csv(full_path, index=False)
    report = costmodel.render_report(headline, full, full_path)
    out = REPORTS_DIR / "cost-breakeven.md"
    out.write_text(report)
    print(report)
    print(f"\nfull grid -> {full_path} · report -> {out}")


def _plot_equity(df, variants):
    """Equity curves (log scale) vs buy & hold, saved next to the reports."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    idx = pd.to_datetime(df["ts"], unit="s", utc=True)
    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=140)
    for name, r in variants.items():
        (r.equity / r.equity.iloc[0]).plot(ax=ax, label=name, lw=1.4)
    bh = df["close"] / df["close"].iloc[0]
    ax.plot(idx, bh, label="buy & hold", lw=1.0, alpha=0.6, ls="--")
    ax.set_yscale("log")
    ax.set_ylabel("growth of 1x (log)")
    ax.set_title(f"{df.attrs.get('symbol', 'equity')} — Donchian baseline vs buy & hold")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out = REPORTS_DIR / "baseline-donchian-equity.png"
    fig.savefig(out)
    plt.close(fig)
    return out


def cmd_baseline(args) -> None:
    df = ingest.load_candles(args.symbol, args.resolution)
    if df is None or df.empty:
        sys.exit(f"no stored candles for {args.symbol} {args.resolution}; run backfill first")
    a = quality.audit(df, args.resolution)
    if a["dq"] < quality.MIN_DQ:
        print(f"WARNING: DQ={a['dq']} < {quality.MIN_DQ}; results are indicative only")
    sig = baselines.donchian(df, entry_n=args.entry_n, exit_n=args.exit_n)

    variants = {}
    res = run(df, sig["long_entry"], sig["long_exit"], fee_open=TAKER_FEE, fee_close=TAKER_FEE)
    variants["donchian_long_only(spot-realistic)"] = res
    res_ls = run(df, sig["long_entry"], sig["long_exit"], sig["short_entry"], sig["short_exit"],
                 fee_open=TAKER_FEE, fee_close=TAKER_FEE)
    variants["donchian_long_short(margin-required)"] = res_ls

    rows = []
    for name, r in variants.items():
        m = dict(r.metrics)
        m["variant"] = name
        rows.append(m)
    table = pd.DataFrame(rows).set_index("variant")
    cols = ["total_return_pct", "cagr_pct", "max_drawdown_pct", "sharpe", "n_trades",
            "win_rate_pct", "expectancy_pct", "profit_factor", "exposure_pct", "buy_hold_pct"]
    print(f"\nBaseline: Donchian({args.entry_n}/{args.exit_n}) on {args.symbol} {args.resolution} "
          f"(taker fees {TAKER_FEE*100:.2f}% per side, next-open execution)\n")
    print(table[cols].to_string())
    out = REPORTS_DIR / f"baseline-donchian-{args.symbol}-{args.resolution}.md"
    body = f"# Baseline: Donchian({args.entry_n}/{args.exit_n}) {args.symbol} {args.resolution}\n\n" \
           f"Executed {pd.Timestamp.now("UTC").strftime('%Y-%m-%d %H:%M UTC')} · engine v0 (next-open fills, taker fees)\n\n" \
           + table[cols].to_markdown() + "\n\n## Trades\n\n" \
           + pd.DataFrame([t.__dict__ for t in res.trades]).to_markdown(index=False) + "\n"
    out.write_text(body)
    df.attrs.setdefault("symbol", f"{args.symbol} {args.resolution}")
    chart = _plot_equity(df, variants)
    print(f"\nreport -> {out}\nchart  -> {chart}")


def cmd_marginbaseline(args) -> None:
    from .backtest.margin import MarginConfig, SizingConfig, run_margin
    from .config import (
        MARGIN_LIQ_FRAC,
        MARGIN_LIQ_OFFSET,
        MARGIN_LOAN_FEE_RATE,
        MARGIN_MAX_MIN_NOTIONAL,
        MARGIN_TRADE_FEE_RATE,
    )

    df = ingest.load_candles(args.symbol, args.resolution)
    if df is None or df.empty:
        sys.exit(f"no stored candles for {args.symbol} {args.resolution}; run backfill first")
    a = quality.audit(df, args.resolution)
    if a["dq"] < quality.MIN_DQ:
        print(f"WARNING: DQ={a['dq']} < {quality.MIN_DQ}; results are indicative only")
    sig = baselines.donchian(df, entry_n=args.entry_n, exit_n=args.exit_n)

    if args.sizing == "allin":
        sz = SizingConfig()
        sz_label = "all-in"
    elif args.sizing == "frac":
        sz = SizingConfig(fraction_of_cash=args.fraction,
                          max_notional=MARGIN_MAX_MIN_NOTIONAL[0],
                          min_notional=MARGIN_MAX_MIN_NOTIONAL[1])
        sz_label = f"fractional {args.fraction:.0%}"
    else:
        sz = SizingConfig(fraction_of_cash=args.fraction, vol_target_risk=args.risk,
                          max_notional=MARGIN_MAX_MIN_NOTIONAL[0],
                          min_notional=MARGIN_MAX_MIN_NOTIONAL[1])
        sz_label = f"vol-target {args.risk:.1%}/ATR, cap {args.fraction:.0%}"

    rows = []
    for sname in ("real", "double"):
        stress = 1.0 if sname == "real" else 2.0
        for lev in (2.0, 3.0, 5.0):
            cfg = MarginConfig(
                leverage=lev,
                trade_fee_rate=MARGIN_TRADE_FEE_RATE,
                loan_fee_rate=MARGIN_LOAN_FEE_RATE,
                liq_frac=MARGIN_LIQ_FRAC,
                liq_offset=MARGIN_LIQ_OFFSET,
                fee_stress=stress,
            )
            r = run_margin(df, sig["long_entry"], sig["long_exit"], None, None,
                           cfg=cfg, initial_cash=args.initial_cash, sizing=sz)
            m = dict(r.metrics)
            m.update({"fee_stress": sname, "variant": "long"})
            rows.append(m)
            r_ls = run_margin(df, sig["long_entry"], sig["long_exit"],
                              sig["short_entry"], sig["short_exit"],
                              cfg=cfg, initial_cash=args.initial_cash, sizing=sz)
            m2 = dict(r_ls.metrics)
            m2.update({"fee_stress": sname, "variant": "long_short"})
            rows.append(m2)
    table = pd.DataFrame(rows)
    show = ["fee_stress", "variant", "leverage", "total_return_pct", "cagr_pct",
            "max_drawdown_pct", "sharpe", "n_trades", "win_rate_pct", "expectancy_pct",
            "avg_hold_days", "avg_fees_pct_of_collateral"]
    print(f"\nDonchian({args.entry_n}/{args.exit_n}) {args.symbol} {args.resolution} | "
          f"measured fees | sizing: {sz_label} | start {args.initial_cash:.0f} USDT\n")
    print(table[show].to_string(index=False))

    out = REPORTS_DIR / f"margin-baseline-{args.symbol}-{args.resolution}.md"
    closes = table[["renewal", "variant", "leverage", "close_reason"]].copy() if "close_reason" in table else None
    body = (
        f"# Margin baseline: Donchian({args.entry_n}/{args.exit_n}) {args.symbol} {args.resolution}\n\n"
        f"Executed {pd.Timestamp.now('UTC').strftime('%Y-%m-%d %H:%M UTC')} · engine v2 (calibrated margin layer)\n\n"
        "Fees MEASURED via public dry-run 2026-10-25: trade fee 0.25% of loan per leg; "
        "activation + renewal = loan/6000 each (renewal every 4h); liquidation at adverse "
        "move 0.8546/L - 0.0045; 21-day forced close. fee_stress multiplies all fee rates.\n\n"
        + table[show].to_markdown(index=False) + "\n\n"
        "## Exit reasons (per scenario)\n\n"
        + pd.DataFrame([
            {"fee_stress": r["fee_stress"], "variant": r["variant"], "leverage": r["leverage"], **r["closes"]}
            for r in rows
        ]).to_markdown(index=False) + "\n"
    )
    out.write_text(body)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), dpi=140, sharey=False)
    for ax, sname in zip(axes, ("real", "double")):
        for (variant, lev), grp in table.groupby(["variant", "leverage"]):
            sel = grp[(grp.fee_stress == sname)]
            if not sel.empty:
                ax.bar(f"{variant[0]}{int(lev) if lev==int(lev) else lev}x",
                       sel["total_return_pct"].iloc[0])
        ax.set_title(f"fee stress: {sname}")
        ax.set_ylabel("total return %")
        ax.grid(alpha=0.3, axis="y")
    fig.suptitle(f"Donchian margin | {args.symbol} {args.resolution} | sizing: {sz_label}")
    fig.tight_layout()
    chart = REPORTS_DIR / f"margin-baseline-{args.symbol}-{args.resolution}.png"
    fig.savefig(chart)
    plt.close(fig)
    print(f"\nreport -> {out}\nchart  -> {chart}")


def cmd_validate(args) -> None:
    from .backtest.validation import (
        IS_END,
        attach_premium,
        parameter_sensitivity,
        run_validated,
        segment_metrics,
        trade_stats,
    )
    from .config import (
        MARGIN_LIQ_FRAC,
        MARGIN_LIQ_OFFSET,
        MARGIN_LOAN_FEE_RATE,
        MARGIN_MAX_MIN_NOTIONAL,
        MARGIN_TRADE_FEE_RATE,
    )
    from .backtest.margin import MarginConfig, SizingConfig

    symbols = [x.strip() for x in args.symbols.split(",") if x.strip()]
    prem = None
    if args.strategy == "donchian_er_prem":
        prem = ingest.load_candles("USDTTMN", "1D")
        if prem is None:
            sys.exit("donchian_er_prem needs stored USDTTMN 1D; run backfill first")

    cfg = MarginConfig(
        leverage=args.leverage,
        trade_fee_rate=MARGIN_TRADE_FEE_RATE,
        loan_fee_rate=MARGIN_LOAN_FEE_RATE,
        liq_frac=MARGIN_LIQ_FRAC,
        liq_offset=MARGIN_LIQ_OFFSET,
        fee_stress=2.0 if args.fee_stress == "double" else 1.0,
    )
    sz = SizingConfig(fraction_of_cash=args.fraction, vol_target_risk=args.risk,
                      max_notional=MARGIN_MAX_MIN_NOTIONAL[0], min_notional=MARGIN_MAX_MIN_NOTIONAL[1])

    is_end_ts = int(pd.Timestamp(IS_END, tz="UTC").timestamp())
    per_rows, pooled, equities = [], [], []
    single_out = None
    for sym in symbols:
        df = ingest.load_candles(sym, args.resolution)
        if df is None or df.empty:
            sys.exit(f"no stored candles for {sym} {args.resolution}; run backfill first")
        if prem is not None:
            df = attach_premium(df, prem)
        out = run_validated(df, entry_n=args.entry_n, exit_n=args.exit_n,
                            cfg=cfg, sizing=sz, initial_cash=args.initial_cash,
                            strategy=args.strategy)
        oos_trades = [t for t in out["_res"].trades if t.entry_ts >= is_end_ts]
        pooled += oos_trades
        equities.append(out["_res"].equity)
        per_rows.append({"symbol": sym,
                         "oos_ret%": out["oos"]["equity"]["return_pct"],
                         "oos_maxdd%": out["oos"]["equity"]["max_dd_pct"],
                         "oos_trades": out["oos"]["n_trades"],
                         "oos_win%": out["oos"]["win_rate_pct"],
                         "oos_exp%": out["oos"]["expectancy_pct"],
                         "is_ret%": out["is"]["equity"]["return_pct"],
                         "is_trades": out["is"]["n_trades"]})
        if len(symbols) == 1:
            single_out = out

    pooled_stats = trade_stats(pooled)
    avg_ret = sum(r["oos_ret%"] for r in per_rows) / len(per_rows)
    worst_dd = min(r["oos_maxdd%"] for r in per_rows)
    port_seg = None
    if single_out is not None:
        avg_sharpe = single_out["oos"]["equity"]["sharpe"]
    else:
        combo = pd.concat(equities, axis=1).ffill()
        port = combo.sum(axis=1)
        oos_port = port[port.index >= pd.Timestamp(IS_END, tz="UTC")]
        port_seg = segment_metrics(oos_port)
        avg_ret = port_seg["return_pct"]
        worst_dd = port_seg["max_dd_pct"]
        avg_sharpe = port_seg["sharpe"]

    print(f"\nVALIDATION — {args.strategy} {','.join(symbols)} {args.resolution} | "
          f"lev {args.leverage:g}x vol-target {args.risk:.0%}/ATR cap {args.fraction:.0%} | "
          f"fees {'x2 (stress)' if args.fee_stress == 'double' else 'measured'}")
    print(f"OOS window {IS_END}..now | pooled OOS trades: {pooled_stats['n_trades']}\n")

    if single_out is not None:
        out = single_out
        res = out["_res"]
        oos_mask = res.equity.index >= pd.Timestamp(IS_END, tz="UTC")
        oos_close = df.set_index(pd.to_datetime(df["ts"], unit="s", utc=True))["close"][oos_mask]
        bh_oos = (oos_close.iloc[-1] / oos_close.iloc[0] - 1) * 100
        for name in ("is", "oos"):
            seg = out[name]
            print(f"{name.upper():3s} | equity: {seg['equity']['return_pct']:+7.1f}%  "
                  f"maxDD {seg['equity']['max_dd_pct']:6.1f}%  | "
                  f"trades {seg['n_trades']:3d}  win {seg['win_rate_pct']:4.1f}%  "
                  f"expectancy {seg['expectancy_pct']:+6.2f}%")
        print(f"OOS buy & hold (unlevered): {bh_oos:+.1f}%\n")
        print("Yearly stability (one continuous equity curve):")
        print(out["yearly"].to_string(index=False))
        print("\nParameter sensitivity (IS ONLY):")
        sens = parameter_sensitivity(df, cfg, sz, initial_cash=args.initial_cash,
                                     strategy=args.strategy, prem_df=prem)
        print(sens.to_string(index=False))
    else:
        print("Per-asset OOS:")
        print(pd.DataFrame(per_rows).to_string(index=False))
        print(f"\nCombined PORTFOLIO OOS: return {port_seg['return_pct']:+.1f}%  "
              f"maxDD {port_seg['max_dd_pct']:.1f}%  sharpe {port_seg['sharpe']:.2f}")
        print(f"Pooled OOS trades: n={pooled_stats['n_trades']}  "
              f"win {pooled_stats['win_rate_pct']}%  expectancy {pooled_stats['expectancy_pct']:+.2f}%  "
              f"avg hold {pooled_stats['avg_hold_days']}d | equal-weight avg OOS return {avg_ret:+.1f}%")

    exp = pooled_stats["expectancy_pct"]
    n_oos = pooled_stats["n_trades"]
    cost_notional = 2 * MARGIN_TRADE_FEE_RATE * cfg.leverage / (1 + cfg.leverage)
    exp_bar = 2 * cost_notional * (1 + cfg.leverage) * 100
    sharpe_for_check = avg_sharpe if avg_sharpe is not None else 0.0
    sens_pos = "n/a (multi-asset)"
    checks = [
        ("OOS Sharpe > 1.0", sharpe_for_check > 1.0,
         f"{sharpe_for_check:.2f}" + (" (portfolio)" if port_seg else "")),
        ("OOS max drawdown < 20%", abs(worst_dd) < 20,
         f"{worst_dd:.1f}%" + (" (portfolio)" if port_seg else " (worst asset)")),
        ("OOS trades >= 50 (pooled)", n_oos >= 50, f"{n_oos}"),
        ("OOS expectancy > 2x per-trade cost bar", exp > exp_bar, f"{exp:+.2f}% vs bar {exp_bar:.2f}%"),
        ("OOS return positive (avg or single)", avg_ret > 0, f"{avg_ret:+.1f}%"),
        ("Stress fees x2 run available via --fee_stress double", True, "info"),
    ]
    print("\nAcceptance checklist (research bar, plan §12):")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({detail})")
    verdict = all(ok for _, ok, _ in checks if "info" not in name)
    print(f"\nVERDICT: {'PASS — candidate for paper trading (subject to human review)' if verdict else 'FAIL — back to research; no live path'}")

    tag = "-".join(symbols[:3]) + (f"-+{len(symbols)-3}" if len(symbols) > 3 else "")
    out_path = REPORTS_DIR / f"validate-{args.strategy}-{tag}-{args.resolution}-lev{args.leverage:g}.md"
    per_tbl = pd.DataFrame(per_rows).to_markdown(index=False)
    body = (
        f"# Validation — {args.strategy} {','.join(symbols)} {args.resolution}\n\n"
        f"Executed {pd.Timestamp.now('UTC').strftime('%Y-%m-%d %H:%M UTC')} · OOS {IS_END}..now\n\n"
        f"## Per-asset\n\n{per_tbl}\n\n"
        f"## Pooled OOS trades\n\n| n | win% | expectancy% | avg_hold_d |\n|---|---|---|---|\n"
        f"| {pooled_stats['n_trades']} | {pooled_stats['win_rate_pct']} | {pooled_stats['expectancy_pct']} | {pooled_stats['avg_hold_days']} |\n\n"
        f"Equal-weight avg OOS return: {avg_ret:+.1f}%\n\n## Acceptance\n\n"
        + "\n".join(f"- [{'PASS' if ok else 'FAIL'}] {name} ({detail})" for name, ok, detail in checks)
        + f"\n\n**VERDICT: {'PASS' if verdict else 'FAIL'}**\n"
    )
    if single_out is not None:
        body += f"\n## Yearly stability\n\n{single_out['yearly'].to_markdown(index=False)}\n"
    out_path.write_text(body)
    print(f"report -> {out_path}")


def cmd_paper(args) -> None:
    from . import notify
    from .paper import runner, state

    if args.action == "run":
        summary = runner.run_cycle(notify=notify.send)
        for line in summary["lines"]:
            print(line)
        print(f"equity snapshot: {summary['equity']} USDT | open positions: {summary['positions']}")
    elif args.action == "status":
        conn = state.connect()
        cash = state.account_cash(conn)
        positions = [dict(r) for r in conn.execute("SELECT symbol, side, entry_px, liq_px,"
                                                   " collateral, leverage FROM positions")]
        trades = [dict(r) for r in conn.execute("SELECT symbol, return_pct, close_reason,"
                                                " fees FROM trades ORDER BY exit_ts DESC LIMIT 20")]
        eq = [dict(r) for r in conn.execute("SELECT * FROM equity ORDER BY ts DESC LIMIT 30")]
        conn.close()
        print(f"cash {cash:.2f} | positions: {positions or '-'}")
        print(f"last trades: {trades or '-'}")
        print(f"equity snapshots: {eq[:3]}")
    elif args.action == "serve":
        import uvicorn
        from .webapp import app
        print("dashboard: http://127.0.0.1:8787/ (set data/secrets/dashboard_login.json"
              " {username, password} for remote access)")
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    elif args.action == "notify-test":
        ok = notify.send("✅ MarketMind: اتصال تلگرام برقرار است.")
        print("telegram:", "sent" if ok else
              "FAILED (check data/secrets/telegram.json or proxy base_url)")


def cmd_exitstudy(args) -> None:
    """E013: exit redesign, IS ONLY (the 2024+ window is burnt and stays untouched)."""
    import numpy as np
    from .backtest.margin import MarginConfig, SizingConfig, run_margin
    from .backtest.validation import IS_END, build_signals
    from .config import (MARGIN_LIQ_FRAC, MARGIN_LIQ_OFFSET, MARGIN_LOAN_FEE_RATE,
                         MARGIN_MAX_MIN_NOTIONAL, MARGIN_TRADE_FEE_RATE)

    assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
              "ADAUSDT", "LINKUSDT", "DOTUSDT", "DOGEUSDT"]
    is_frames = {}
    for sym in assets:
        df = ingest.load_candles(sym, "1D")
        if df is None or df.empty:
            sys.exit(f"missing {sym} 1D candles")
        is_frames[sym] = df[df["ts"] < int(pd.Timestamp(IS_END, tz="UTC").timestamp())].reset_index(drop=True)

    variants = {
        "base_chan10 (E008a)": {"strategy": "donchian_er"},
        "trail3": {"strategy": "er_trail", "trail_k": 3.0},
        "trail3+chan10": {"strategy": "er_trail", "trail_k": 3.0, "use_channel_exit": True},
        "tb20+trail3": {"strategy": "er_trail", "trail_k": 3.0, "time_stop_bars": 20},
        "chan10+sma200": {"strategy": "donchian_er", "sma_n": 200},
        "trail3+sma200": {"strategy": "er_trail", "trail_k": 3.0, "sma_n": 200},
        "tb20+trail3+sma200": {"strategy": "er_trail", "trail_k": 3.0, "time_stop_bars": 20, "sma_n": 200},
    }
    cfg = MarginConfig(leverage=3.0, trade_fee_rate=MARGIN_TRADE_FEE_RATE,
                       loan_fee_rate=MARGIN_LOAN_FEE_RATE, liq_frac=MARGIN_LIQ_FRAC,
                       liq_offset=MARGIN_LIQ_OFFSET)
    sz = SizingConfig(fraction_of_cash=0.20, vol_target_risk=0.02,
                      max_notional=MARGIN_MAX_MIN_NOTIONAL[0], min_notional=MARGIN_MAX_MIN_NOTIONAL[1])

    rows = []
    for vname, spec in variants.items():
        trades_all = []
        reasons = {}
        for sym, is_df in is_frames.items():
            sig = build_signals(spec["strategy"], is_df, spec)
            r = run_margin(is_df, sig["long_entry"], sig["long_exit"], None, None,
                           cfg=cfg, initial_cash=2000.0, sizing=sz)
            for t in r.trades:
                trades_all.append({"exp": t.return_pct, "ts": t.entry_ts, "reason": t.close_reason})
        tr = pd.DataFrame(trades_all)
        # 3 sequential folds within IS
        if len(tr):
            q1, q2 = tr.ts.quantile([1/3, 2/3])
            folds = [tr[tr.ts < q1], tr[(tr.ts >= q1) & (tr.ts < q2)], tr[tr.ts >= q2]]
            fold_exp = [round(float(f.exp.mean()), 2) if len(f) else 0.0 for f in folds]
            folds_pos = sum(1 for e in fold_exp if e > 0)
            forced_share = round(float((tr.reason == "forced_age").mean() * 100), 1)
        rows.append({"variant": vname, "n": len(tr),
                     "is_exp%": round(float(tr.exp.mean()), 2) if len(tr) else 0.0,
                     "win%": round(float((tr.exp > 0).mean() * 100), 1) if len(tr) else 0.0,
                     "forced_age%": forced_share,
                     "fold1": fold_exp[0], "fold2": fold_exp[1], "fold3": fold_exp[2],
                     "folds_pos": folds_pos})

    tbl = pd.DataFrame(rows)
    print("\nE013 EXIT STUDY — IS ONLY (2019-10..2023-12), 9 assets pooled, 3x vol-target\n")
    print(tbl.to_string(index=False))
    eligible = tbl[(tbl.folds_pos >= 2) & (tbl["is_exp%"] > 0) & (tbl["forced_age%"] <= 35)]
    if len(eligible):
        best = eligible.sort_values("is_exp%", ascending=False).iloc[0]
        print(f"\nSELECTED (rule: folds_pos>=2, exp>0, forced_age<=35%, max exp): "
              f"**{best.variant}** (exp {best['is_exp%']:+.2f}%, forced_age {best['forced_age%']}%, "
              f"folds {best.fold1}/{best.fold2}/{best.fold3})")
        print("Next judge for this variant: FORWARD paper trading only — the 2024+ window stays untouched.")
    else:
        print("\nNO VARIANT QUALIFIES — all exit designs fail IS stability; strategy family closed for now.")
    out = REPORTS_DIR / "exitstudy-E013.md"
    out.write_text("# E013 exit study (IS only)\n\n" + tbl.to_markdown(index=False) + "\n")
    print(f"report -> {out}")


def cmd_redteam(args) -> None:
    from .analytics.redteam import ASSETS, attack_report, leverage_sweep
    from .data import ingest as ing

    frames = {}
    for sym in ASSETS:
        df = ing.load_candles(sym, "1D")
        if df is None or df.empty:
            sys.exit(f"missing {sym} 1D candles; run backfill first")
        frames[sym] = df

    rep = attack_report(frames)
    print(f"\nE012 RED-TEAM — attacks on E011 (donchian_er, 9 assets, OOS 2024-01..now)\n")
    print(f"Baseline: {rep['oos_trades']} OOS trades, expectancy {rep['oos_exp']:+.2f}%, "
          f"win {rep['oos_win']:.1f}%")
    print("\nOOS expectancy by asset:"); print(rep["by_symbol"].round(2).to_string())

    print(f"\n[A] TAIL DEPENDENCE — drop top-2 winners {rep['A_tail_syms']}:")
    ok_a = rep["A_kept_exp"] > 3.0
    print(f"    kept n={rep['A_kept_n']}, expectancy {rep['A_kept_exp']:+.2f}%, "
          f"win {rep['A_kept_win']:.1f}%  ->  {'SURVIVES' if ok_a else 'BREAKS'}")

    print(f"\n[B] SUB-PERIODS (OOS halves):")
    h1, h2 = rep["B_half1"], rep["B_half2"]
    ok_b = h1["n"] >= 15 and h2["n"] >= 15 and h1["exp"] > 0 and h2["exp"] > 0
    print(f"    half1: n={h1['n']}, exp {h1['exp']:+.2f}%, win {h1['win']}%")
    print(f"    half2: n={h2['n']}, exp {h2['exp']:+.2f}%, win {h2['win']}%  ->  "
          f"{'SURVIVES' if ok_b else 'BREAKS'}")

    print(f"\n[C] TRADE CLUSTERING: {rep['oos_trades']} trades on {rep['C_distinct_days']} distinct days "
          f"(max {rep['C_max_trades_one_day']} on one day)")
    top = {str(k): int(v) for k, v in rep["C_top_days"].items()}
    ok_c = rep["C_distinct_days"] >= 40
    print(f"    busiest days: {top}  ->  {'SURVIVES' if ok_c else 'BREAKS'} (effective sample check)")

    print("\n[D] LEVERAGE SWEEP (OOS pooled):")
    sweep = leverage_sweep(frames)
    print(sweep.to_string(index=False))
    ok_d = bool((sweep["oos_exp_pct"] > 3.0).all())

    print("\n[E] CLOSE REASONS (IS vs OOS):")
    print(rep["E_reasons"].to_string())
    liq_oos = int(rep["E_reasons"].loc[True, "liquidated"]) if ("liquidated" in rep["E_reasons"].columns and True in rep["E_reasons"].index) else 0
    ok_e = liq_oos <= rep["oos_trades"] * 0.10

    attacks = [("A tail-dependence", ok_a), ("B sub-periods", ok_b),
               ("C clustering", ok_c), ("D leverage sweep", ok_d),
               ("E liquidation share", ok_e)]
    survived = sum(1 for _, ok in attacks if ok)
    print(f"\nATTACKS SURVIVED: {survived}/5")
    print("VERDICT: " + ("E011 HOLDS — proceed to paper-trading design (owner review first)"
                         if survived == 5 else
                         "DAMAGE — E011 needs repair before any paper path; see failures above"))

    out = REPORTS_DIR / "redteam-E011.md"
    body = (f"# E012 Red-Team report on E011\n\n"
            f"Generated {pd.Timestamp.now('UTC').strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"- Baseline: {rep['oos_trades']} OOS trades, exp {rep['oos_exp']:+.2f}%, win {rep['oos_win']}%\n"
            f"- A dropped {rep['A_tail_syms']}: n={rep['A_kept_n']}, exp {rep['A_kept_exp']:+.2f}% — {'OK' if ok_a else 'BREAK'}\n"
            f"- B halves: {h1} | {h2} — {'OK' if ok_b else 'BREAK'}\n"
            f"- C distinct days {rep['C_distinct_days']}/{rep['oos_trades']} trades — {'OK' if ok_c else 'BREAK'}\n"
            f"- D sweep: \n\n{sweep.to_markdown(index=False)}\n\n"
            f"- E reasons:\n\n{rep['E_reasons'].to_markdown()}\n\n"
            f"**Survived {survived}/5.**\n")
    out.write_text(body)
    print(f"report -> {out}")


def cmd_premiumstudy(args) -> None:
    from .analytics.premium import bucket_table, load_pair, render_report

    prem = ingest.load_candles("USDTTMN", "1D")
    btc = ingest.load_candles(args.symbol, "1D")
    if prem is None or btc is None:
        sys.exit("need USDTTMN 1D and BTCUSDT 1D stored; run backfill first")
    df = load_pair(prem, btc)

    from .backtest.validation import IS_END
    is_end = pd.Timestamp(IS_END, tz="UTC")
    dt = pd.to_datetime(df["ts"], unit="s", utc=True)
    df_is, df_oos = df[dt < is_end], df[dt >= is_end]

    tbl_is, tbl_oos = bucket_table(df_is), bucket_table(df_oos)
    meta = {
        "n_days": len(df),
        "start": dt.iloc[0].date(), "end": dt.iloc[-1].date(),
        "is_range": f"{dt[dt < is_end].iloc[0].date()}..{dt[dt < is_end].iloc[-1].date()}",
        "oos_range": f"{dt[dt >= is_end].iloc[0].date()}..{dt[dt >= is_end].iloc[-1].date()}",
    }
    print(f"\nE009 USDT/TMN premium study — {meta['n_days']} joint days "
          f"({meta['start']}..{meta['end']})\n")
    print("IS buckets:");  print(tbl_is.to_string(index=False))
    print("\nOOS buckets:"); print(tbl_oos.to_string(index=False))

    out = REPORTS_DIR / f"premium-study-{args.symbol}-1D.md"
    out.write_text(render_report(tbl_is, tbl_oos, meta))
    print(f"\nreport -> {out}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="marketmind", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("markets", help="fetch & store market snapshot; list top by volume")

    p_bf = sub.add_parser("backfill", help="download historical candles into parquet")
    p_bf.add_argument("--symbol", default=None)
    p_bf.add_argument("--resolution", default=None, choices=["15", "60", "240", "720", "1D"])
    p_bf.add_argument("--all", action="store_true", help=f"default research set: {DEFAULT_BACKFILL}")
    p_bf.set_defaults(func=cmd_backfill)

    p_au = sub.add_parser("audit", help="data-quality audit of all stored series")
    p_au.set_defaults(func=cmd_audit)

    p_cr = sub.add_parser("costreport", help="margin fee break-even tables")
    p_cr.set_defaults(func=cmd_costreport)

    p_bl = sub.add_parser("baseline", help="run Donchian baseline backtest")
    p_bl.add_argument("--symbol", default="BTCUSDT")
    p_bl.add_argument("--resolution", default="1D", choices=["15", "60", "240", "720", "1D"])
    p_bl.add_argument("--entry_n", type=int, default=20)
    p_bl.add_argument("--exit_n", type=int, default=10)
    p_bl.set_defaults(func=cmd_baseline)

    p_mb = sub.add_parser("marginbaseline", help="Donchian baseline under margin economics (scenario grid)")
    p_mb.add_argument("--symbol", default="BTCUSDT")
    p_mb.add_argument("--resolution", default="1D", choices=["15", "60", "240", "720", "1D"])
    p_mb.add_argument("--entry_n", type=int, default=20)
    p_mb.add_argument("--exit_n", type=int, default=10)
    p_mb.add_argument("--sizing", choices=["allin", "frac", "vol"], default="frac",
                      help="all-in | fractional | vol-targeted collateral")
    p_mb.add_argument("--fraction", type=float, default=0.15, help="collateral fraction cap of cash")
    p_mb.add_argument("--risk", type=float, default=0.02, help="vol-target risk per ATR move (fraction)")
    p_mb.add_argument("--initial_cash", type=float, default=2000.0, help="starting equity in USDT")
    p_mb.set_defaults(func=cmd_marginbaseline)

    p_v = sub.add_parser("validate", help="E007+: IS/OOS split + yearly stability + parameter sensitivity")
    p_v.add_argument("--symbols", default="BTCUSDT", help="comma-separated for pooled validation")
    p_v.add_argument("--resolution", default="1D", choices=["15", "60", "240", "720", "1D"])
    p_v.add_argument("--strategy", default="donchian", choices=["donchian", "donchian_er", "squeeze", "donchian_er_prem"])
    p_v.add_argument("--entry_n", type=int, default=20)
    p_v.add_argument("--exit_n", type=int, default=10)
    p_v.add_argument("--leverage", type=float, default=3.0)
    p_v.add_argument("--fraction", type=float, default=0.20)
    p_v.add_argument("--risk", type=float, default=0.02)
    p_v.add_argument("--initial_cash", type=float, default=2000.0)
    p_v.add_argument("--fee_stress", choices=["real", "double"], default="real")
    p_v.set_defaults(func=cmd_validate)

    p_pp = sub.add_parser("paper", help="Stage 4 paper trading")
    p_pp.add_argument("action", choices=["run", "status", "serve", "notify-test"])
    p_pp.add_argument("--host", default="127.0.0.1")
    p_pp.add_argument("--port", type=int, default=8787)
    p_pp.set_defaults(func=cmd_paper)

    p_es = sub.add_parser("exitstudy", help="E013: exit redesign comparison, IS ONLY")
    p_es.set_defaults(func=cmd_exitstudy)

    p_rt = sub.add_parser("redteam", help="E012: Red-Team attacks on the pooled E011 result")
    p_rt.set_defaults(func=cmd_redteam)

    p_ps = sub.add_parser("premiumstudy", help="E009: USDT/TMN premium z-score vs forward BTC returns")
    p_ps.add_argument("--symbol", default="BTCUSDT")
    p_ps.set_defaults(func=cmd_premiumstudy)

    sub.choices["markets"].set_defaults(func=cmd_markets)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ensure_dirs()
    args.func(args)


if __name__ == "__main__":
    main()
