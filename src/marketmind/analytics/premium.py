"""E009: USDT/TMN premium study — the Wallex-native signal.

The Tether/Toman rate is a live gauge of Iranian crypto-market risk appetite.
This module measures whether premium extremes carry information about forward
BTC returns on Wallex. Pure feature study: buckets, forward returns, t-stats.
No strategy is built until the conditional edge is demonstrated.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def premium_z(premium_df: pd.DataFrame, win: int = 60) -> pd.Series:
    """Decision-time-safe premium z (shift 1), indexed by ts — reusable gate input."""
    ret = premium_df["close"].sort_index().pct_change()
    z = (ret - ret.rolling(win).mean()) / ret.rolling(win).std()
    z = z.shift(1)
    return pd.Series(z.to_numpy(), index=premium_df["ts"].to_numpy(), name="prem_z")


def load_pair(premium_df: pd.DataFrame, btc_df: pd.DataFrame) -> pd.DataFrame:
    p = premium_df[["ts", "close"]].rename(columns={"close": "usdttmn"})
    b = btc_df[["ts", "close"]].rename(columns={"close": "btc"})
    df = p.merge(b, on="ts", how="inner").sort_values("ts").reset_index(drop=True)
    df["prem_ret"] = df["usdttmn"].pct_change()
    # z-score of premium return over trailing window (isolates cyclical stress
    # from structural rial depreciation)
    win = 60
    df["prem_z"] = (
        (df["prem_ret"] - df["prem_ret"].rolling(win).mean())
        / df["prem_ret"].rolling(win).std()
    ).shift(1)  # known at decision time (yesterday's z)
    for k in (1, 3, 7):
        df[f"btc_fwd_{k}d"] = df["btc"].shift(-k) / df["btc"] - 1
    return df.dropna(subset=["prem_z"])


BUCKETS = [(-np.inf, -2, "z<-2"), (-2, -1, "-2..-1"), (-1, 1, "-1..1"),
           (1, 2, "1..2"), (2, np.inf, "z>2")]


def bucket_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    z = df["prem_z"]
    for lo, hi, label in BUCKETS:
        m = (z >= lo) & (z < hi)
        n = int(m.sum())
        row = {"bucket": label, "n_days": n}
        for k in (1, 3, 7):
            fwd = df.loc[m, f"btc_fwd_{k}d"]
            if n >= 5 and fwd.std() > 0:
                t = float(stats.ttest_1samp(fwd, 0).statistic)
                row[f"fwd{k}d_mean_pct"] = round(fwd.mean() * 100, 3)
                row[f"fwd{k}d_t"] = round(t, 2)
                row[f"fwd{k}d_pos%"] = round((fwd > 0).mean() * 100, 1)
            else:
                row[f"fwd{k}d_mean_pct"] = None
                row[f"fwd{k}d_t"] = None
                row[f"fwd{k}d_pos%"] = None
        rows.append(row)
    return pd.DataFrame(rows)


def render_report(tbl_is: pd.DataFrame, tbl_oos: pd.DataFrame, meta: dict) -> str:
    def fmt(t: pd.DataFrame) -> str:
        return t.to_markdown(index=False)

    return "\n".join([
        "# E009: USDT/TMN premium z-score → forward BTC returns",
        "",
        f"Generated {pd.Timestamp.now('UTC').strftime('%Y-%m-%d %H:%M UTC')} · "
        f"{meta['n_days']} joint days ({meta['start']} .. {meta['end']})",
        "",
        "z = yesterday's standardized 60d change of USDTTMN (shift(1): decision-time safe).",
        "",
        f"## IS ({meta['is_range']})",
        "",
        fmt(tbl_is),
        "",
        f"## OOS ({meta['oos_range']})",
        "",
        fmt(tbl_oos),
        "",
        "## Reading it",
        "",
        "- Interesting columns: `fwd*d_mean_pct` (conditional forward BTC return) and `fwd*d_t`",
        "  (t-stat vs 0; |t|>2 is strong for a single bucket, but note multiple comparisons).",
        "- A tradable gate needs: consistent sign IS AND OOS in the extreme buckets,",
        "  with enough days (n) to matter.",
        "- The premium is also the market's stress gauge: z>2 days are candidates for",
        "  risk-off gates on ANY strategy (that test is the follow-up).",
    ])
