"""
signals.py - turn raw FRED series into readings you can act on.

What it builds:
  natives()        each series at its own frequency (for charts)
  transform()      level / % change vs. a year ago / change from last reading
  panel()          every series on one daily calendar (for combining series)
  gauges()         stress z-scores: how unusual is today vs. history?
  stress_index()   the stress gauges averaged into one number
  leading_index()  your own version of a leading economic index
  sahm()           the Sahm-rule recession indicator
  regime_flags()   plain yes/no statements about the latest data

A note on look-ahead bias (important once you start backtesting):
The z-scores use an EXPANDING window: each day is compared only to the history
available up to that day, never to the future, so they're safe to use as signals
in a backtest. One simplification remains: monthly, weekly and quarterly data are
treated as known on their observation date, but they're published days to weeks
later (GDP about a month after the quarter ends). Add a lag before trusting a backtest.

Run `python signals.py` to print the current readings in the terminal.
"""

import numpy as np
import pandas as pd

import config

# ---------- shaping the data ----------


def natives(raw: pd.DataFrame) -> dict:
    """{series_id: Series} at each series' own frequency, no gaps filled."""
    out = {}
    for sid, grp in raw.groupby("series_id"):
        s = grp.set_index(pd.to_datetime(grp["date"]))["value"].sort_index()
        out[sid] = s[~s.index.duplicated(keep="last")].astype(float)
    return out


def yoy(s: pd.Series) -> pd.Series:
    """% change vs. the value one year earlier. Works for any frequency."""
    prior = s.asof(s.index - pd.DateOffset(years=1))
    prior.index = s.index
    return (s / prior - 1) * 100


def transform(s: pd.Series, view: str) -> pd.Series:
    if view == "yoy":
        return yoy(s)
    if view == "diff":
        return s.diff()
    return s


VIEW_LABELS = {"level": "Level", "yoy": "% change vs. a year ago", "diff": "Change from previous reading"}


def panel(nat: dict) -> pd.DataFrame:
    """One column per series on a business-day calendar. Lower-frequency series are
    carried forward until the next observation. Adds the computed yield curve."""
    wide = pd.concat(nat, axis=1, sort=True)
    days = pd.bdate_range(wide.index.min(), wide.index.max())
    p = wide.reindex(wide.index.union(days)).ffill().reindex(days)
    if {"DGS10", "DGS2"} <= set(p.columns):
        p["CURVE"] = p["DGS10"] - p["DGS2"]  # computed here instead of downloading T10Y2Y
    return p


# ---------- gauges and indexes ----------


def expanding_z(s: pd.Series, min_periods: int = 252) -> pd.Series:
    """How many standard deviations today's value is from its own history so far."""
    mean = s.expanding(min_periods=min_periods).mean()
    std = s.expanding(min_periods=min_periods).std()
    return (s - mean) / std


def gauges(p: pd.DataFrame) -> pd.DataFrame:
    """Financial stress gauges. Each is oriented so HIGHER = MORE STRESS."""
    g = pd.DataFrame(index=p.index)
    if "BAMLH0A0HYM2" in p:
        g["High-yield spread"] = expanding_z(p["BAMLH0A0HYM2"])
    if "BAMLC0A0CM" in p:
        g["Investment-grade spread"] = expanding_z(p["BAMLC0A0CM"])
    if "VIXCLS" in p:
        g["Stock volatility (VIX)"] = expanding_z(p["VIXCLS"])
    if {"SOFR", "IORB"} <= set(p.columns):
        g["Funding squeeze (SOFR - IORB)"] = expanding_z((p["SOFR"] - p["IORB"]) * 100, min_periods=120)
    if "DRTSCILM" in p:
        g["Banks tightening credit"] = expanding_z(p["DRTSCILM"], min_periods=20 * 63)
    return g


def stress_index(g: pd.DataFrame) -> pd.Series:
    """Average of whatever gauges are available that day. 0 = normal, above +1 = stressed."""
    return g.mean(axis=1, skipna=True).rename("Stress index")


def leading_components(p: pd.DataFrame) -> pd.DataFrame:
    """Each leading indicator's 6-month change, as a z-score. Oriented so HIGHER = BETTER
    for the economy. Built from the same kinds of inputs as the Conference Board's
    Leading Economic Index (which is proprietary and not on FRED)."""
    c = pd.DataFrame(index=p.index)
    six_months = 126  # business days
    for sid, meta in config.SERIES.items():
        if meta["cycle"] != "leading" or sid not in p:
            continue
        chg = p[sid].pct_change(six_months, fill_method=None) * 100
        if meta["invert"]:
            chg = -chg
        c[meta["name"]] = expanding_z(chg, min_periods=504)
    if "CURVE" in p:
        c["Yield curve (10y - 2y)"] = expanding_z(p["CURVE"], min_periods=504)
    return c


def leading_index(lc: pd.DataFrame) -> pd.Series:
    """Average of the leading components. Below 0 = leading indicators weaker than normal.
    Each component is capped at +/-4 so one extreme reading (jobless claims in April 2020
    jumped about 25x) can't swamp the others."""
    return lc.clip(-4, 4).mean(axis=1, skipna=True).rename("Leading index")


def sahm(unrate: pd.Series) -> pd.Series:
    """Sahm rule: 3-month average unemployment minus its lowest 3-month average over the
    previous 12 months. 0.5 or more has signaled the start of every US recession since 1970."""
    u3 = unrate.rolling(3).mean()
    return (u3 - u3.shift(1).rolling(12).min()).rename("Sahm indicator")


def ordinal(n: float) -> str:
    """73 -> '73rd'."""
    n = int(round(n))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def percentile_rank(s: pd.Series) -> float:
    """Where the latest value sits in the series' full history (0-100)."""
    s = s.dropna()
    if s.empty:
        return np.nan
    return float((s <= s.iloc[-1]).mean() * 100)


def recessions(nat: dict) -> list:
    """[(start, end), ...] of NBER recessions, for shading charts."""
    r = nat.get("USREC")
    if r is None or r.empty:
        return []
    r = r.astype(int)
    starts = r.index[(r == 1) & (r.shift(1, fill_value=0) == 0)]
    ends = r.index[(r == 1) & (r.shift(-1, fill_value=0) == 0)]
    return [(s, e + pd.offsets.MonthEnd(0)) for s, e in zip(starts, ends)]


# ---------- flags ----------


def regime_flags(p, nat, idx, lead) -> list:
    """Plain-English yes/no readings about the latest data.
    Each flag: name, on (True/False/None), detail, section."""
    flags = []

    def add(name, section, on, detail):
        flags.append(dict(name=name, section=section, on=on, detail=detail))

    # --- Economy ---
    if "UNRATE" in nat:
        sv = sahm(nat["UNRATE"]).dropna()
        if len(sv):
            add("Sahm rule recession signal", "economy", bool(sv.iloc[-1] >= 0.5),
                f"{sv.iloc[-1]:+.2f} pts (signal at +0.50)")
    if lead.notna().any():
        v = lead.dropna().iloc[-1]
        add("Leading indicators weakening", "economy", bool(v < -0.5), f"Leading index {v:+.2f} (below -0.5 = weak)")
    if "CURVE" in p and p["CURVE"].notna().any():
        c = p["CURVE"].dropna().iloc[-1]
        add("Yield curve inverted", "economy", bool(c < 0), f"10y - 2y = {c:+.2f} pts")
    if "PAYEMS" in nat and len(nat["PAYEMS"]) > 4:
        jobs = nat["PAYEMS"].diff().tail(3).mean()
        add("Payrolls shrinking", "economy", bool(jobs < 0), f"{jobs:+,.0f}k jobs/month (3-month avg)")
    if "CPIAUCSL" in nat:
        infl = yoy(nat["CPIAUCSL"]).dropna()
        if len(infl):
            add("Inflation above 3%", "economy", bool(infl.iloc[-1] > 3), f"CPI {infl.iloc[-1]:.1f}% vs. a year ago")

    # --- Fed & banks ---
    if "WALCL" in nat and len(nat["WALCL"]) > 14:
        w = nat["WALCL"]
        chg = w.iloc[-1] / w.iloc[-14] - 1
        add("Fed balance sheet shrinking", "fed", bool(chg < 0), f"{chg*100:+.1f}% over 13 weeks")
    if "WLCFLPCL" in nat:
        pct = percentile_rank(nat["WLCFLPCL"])
        add("Banks leaning on discount window", "fed", bool(pct >= 90),
            f"${nat['WLCFLPCL'].iloc[-1]/1000:,.1f}B borrowed ({ordinal(pct)} percentile)")
    if "RRPONTSYD" in nat:
        rrp = nat["RRPONTSYD"].iloc[-1]
        add("Reverse repo cushion drained", "fed", bool(rrp < 100), f"${rrp:,.0f}B parked at the Fed")
    if "DPSACBW027SBOG" in nat:
        dep = yoy(nat["DPSACBW027SBOG"]).dropna()
        if len(dep):
            add("Bank deposits falling", "fed", bool(dep.iloc[-1] < 0), f"{dep.iloc[-1]:+.1f}% vs. a year ago")

    # --- Markets & stress ---
    if {"SOFR", "IORB"} <= set(p.columns) and p[["SOFR", "IORB"]].dropna().shape[0]:
        avg = (p["SOFR"] - p["IORB"]).dropna().tail(5).mean() * 100
        add("Repo funding squeeze", "markets", bool(avg > 5), f"SOFR - IORB = {avg:+.0f} bp (5-day avg; signal above +5)")
    if "BAMLH0A0HYM2" in nat:
        pct = percentile_rank(nat["BAMLH0A0HYM2"])
        add("Credit spreads elevated", "markets", bool(pct >= 80), f"HY spread at {ordinal(pct)} percentile of history")
    if idx.notna().any():
        v = idx.dropna().iloc[-1]
        add("Financial stress high", "markets", bool(v > 1), f"Stress index {v:+.2f} (above +1 = stressed)")
    return flags


def build(raw: pd.DataFrame) -> dict:
    """Everything the app needs, in one call."""
    nat = natives(raw)
    p = panel(nat)
    g = gauges(p)
    idx = stress_index(g)
    lc = leading_components(p)
    lead = leading_index(lc)
    return dict(nat=nat, panel=p, gauges=g, stress=idx, lead_parts=lc, lead=lead,
                recessions=recessions(nat), flags=regime_flags(p, nat, idx, lead))


if __name__ == "__main__":
    b = build(pd.read_parquet(config.RAW_FILE))
    print(f"Data through {b['panel'].index.max().date()}\n")
    for sec, title in config.SECTIONS.items():
        print(title)
        for f in [f for f in b["flags"] if f["section"] == sec]:
            mark = "YES" if f["on"] else ("no " if f["on"] is False else " ? ")
            print(f"  [{mark}] {f['name']:<34} {f['detail']}")
    print(f"\nStress index: {b['stress'].dropna().iloc[-1]:+.2f}   Leading index: {b['lead'].dropna().iloc[-1]:+.2f}")
