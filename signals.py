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


def history_years(s: pd.Series) -> float:
    """How many years of actual observations a series has."""
    s = s.dropna()
    return 0.0 if s.empty else (s.index[-1] - s.index[0]).days / 365.25


def long_enough(p: pd.DataFrame, sid: str) -> bool:
    return sid in p and history_years(p[sid]) >= config.MIN_HISTORY_YEARS


def gauges(p: pd.DataFrame) -> pd.DataFrame:
    """Financial stress gauges. Each is oriented so HIGHER = MORE STRESS.
    Spread gauges are only built from series with enough history (config.MIN_HISTORY_YEARS):
    FRED cut the ICE high-yield and investment-grade spreads to 3 years, so for now the
    credit gauge is Moody's Baa spread. If FRED restores the ICE history, those come back on."""
    g = pd.DataFrame(index=p.index)
    if long_enough(p, "BAA10Y"):
        g["Corporate spread (Baa)"] = expanding_z(p["BAA10Y"])
    if long_enough(p, "BAMLH0A0HYM2"):
        g["High-yield spread"] = expanding_z(p["BAMLH0A0HYM2"])
    if long_enough(p, "BAMLC0A0CM"):
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
    """73.8 -> '73rd'. Rounds DOWN, so a reading shown as '90th' really is at or above 90
    (rounding up made an 89.6 show as '90th' next to a 'No' on a 90th-percentile flag)."""
    n = int(np.floor(n))
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


# ---------- forex (ECON 4810: Feenstra/Taylor ch. 2-4) ----------


def usd_per_ccy(nat: dict, pair: str) -> pd.Series:
    """Price of one unit of the foreign currency in dollars (E$/foreign, 'American terms' in ch. 2),
    whichever way the pair is quoted. Up = foreign currency stronger."""
    m = config.FX_PAIRS[pair]
    s = nat[m["sid"]].dropna()
    return 1 / s if m["usd_base"] else s


def pct_change_over(s: pd.Series, days: int) -> float:
    """% change over the last `days` calendar days (uses the last reading on or before that date)."""
    s = s.dropna()
    if len(s) < 2:
        return np.nan
    prior = s.asof(s.index[-1] - pd.Timedelta(days=days))
    return np.nan if pd.isna(prior) else (s.iloc[-1] / prior - 1) * 100


def fx_monthly(nat: dict, stress: pd.Series) -> dict:
    """{pair: monthly DataFrame} with the foreign currency's dollar price, the 10-year yield gap
    (US minus foreign, % pts) and the stress index, all at month end."""
    us10 = nat["DGS10"].resample("ME").mean() if "DGS10" in nat else None
    st = stress.resample("ME").last()
    out = {}
    for pair, m in config.FX_PAIRS.items():
        if m["sid"] not in nat or us10 is None:
            continue
        df = pd.DataFrame({"fx": usd_per_ccy(nat, pair).resample("ME").last(), "stress": st})
        if m["yld"] in nat:
            df["gap"] = us10 - nat[m["yld"]].resample("ME").mean()
        out[pair] = df.dropna(subset=["fx"])
    return out


def carry_returns(df: pd.DataFrame) -> pd.Series:
    """12-month return of a carry trade: long the higher-yielding currency, funded in the lower one.
    Uncovered interest parity (ch. 2 and 4) says this should average ZERO, because the high-yield
    currency should depreciate by the rate gap. Return = |gap| earned + the move in the currency held.
    Uses 10-year yields as the rate (a proxy) and ignores bond price changes and trading costs."""
    if "gap" not in df:
        return pd.Series(dtype=float)
    gap0 = df["gap"].shift(12)                     # US minus foreign, set when the trade starts
    ccy_move = (df["fx"] / df["fx"].shift(12) - 1) * 100   # + = foreign currency appreciated vs USD
    long_usd = gap0 > 0
    ret = np.where(long_usd, gap0 - ccy_move, -gap0 + ccy_move)
    return pd.Series(ret, index=df.index).where(gap0.notna() & ccy_move.notna()).dropna()


def fx_table(nat: dict, monthly: dict) -> pd.DataFrame:
    """One row per major pair: recent moves, the rate gap, how the pair has moved with the gap and
    with financial stress, and the carry (UIP) check."""
    rows = []
    five_years = pd.DateOffset(years=5)
    for pair, m in config.FX_PAIRS.items():
        if pair not in monthly:
            continue
        s = nat[m["sid"]].dropna()
        df = monthly[pair]
        recent = df[df.index >= df.index.max() - five_years]
        d = recent.diff()
        ccy_ret = recent["fx"].pct_change() * 100
        row = {"Pair": pair, "Latest": s.iloc[-1], "As of": s.index[-1],
               "1-month %": pct_change_over(s, 30), "1-year %": pct_change_over(s, 365)}
        if "gap" in df and df["gap"].notna().any():
            g = df["gap"].dropna()
            row["US minus foreign 10y"] = g.iloc[-1]
            row["Gap change, 6 mo"] = g.iloc[-1] - g.asof(g.index[-1] - pd.DateOffset(months=6))
            row["Corr with gap"] = ccy_ret.corr(-d["gap"])      # + = currency rises when ITS yields rise vs the US
        row["Corr with stress"] = ccy_ret.corr(d["stress"])     # - = currency falls when US stress rises
        cr = carry_returns(df)
        if len(cr):
            row["Carry: long"] = "USD" if df["gap"].dropna().iloc[-1] > 0 else m["ccy"]
            row["Carry, last 12 mo %"] = cr.iloc[-1]
            row["Carry, avg 12 mo %"] = cr.mean()
            row["Carry, % of years won"] = (cr > 0).mean() * 100
        rows.append(row)
    return pd.DataFrame(rows).set_index("Pair") if rows else pd.DataFrame()


def fx_crosses(nat: dict) -> pd.DataFrame:
    """Cross rates implied through the dollar (the vehicle-currency logic and triangular
    no-arbitrage condition from ch. 2): E(B per A) = E($ per A) / E($ per B)."""
    usd = {m["ccy"]: usd_per_ccy(nat, p) for p, m in config.FX_PAIRS.items() if m["sid"] in nat}
    usd["USD"] = None
    rows = []
    for name in config.FX_CROSSES:
        a, b = name.split("/")
        if a not in usd or b not in usd or usd[a] is None or usd[b] is None:
            continue
        x = (usd[a] / usd[b]).dropna()
        if x.empty:
            continue
        rows.append({"Cross": name, "Implied rate": x.iloc[-1], "As of": x.index[-1],
                     "1-month %": pct_change_over(x, 30), "1-year %": pct_change_over(x, 365)})
    return pd.DataFrame(rows).set_index("Cross") if rows else pd.DataFrame()


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
        # Week-to-week noise moves it by a few hundredths of a percent, so "shrinking" means
        # down more than 0.5% over 13 weeks (QT ran at roughly 1-2% a quarter).
        add("Fed balance sheet shrinking", "fed", bool(chg < -0.005), f"{chg*100:+.2f}% over 13 weeks (signal below -0.50%)")
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
    # Judged on whichever spread has enough history to rank fairly (see gauges()).
    credit = next((sid for sid in ("BAMLH0A0HYM2", "BAA10Y")
                   if sid in nat and history_years(nat[sid]) >= config.MIN_HISTORY_YEARS), None)
    if credit:
        s = nat[credit].dropna()
        pct = percentile_rank(s)
        label = "HY spread" if credit == "BAMLH0A0HYM2" else "Baa spread"
        add("Credit spreads elevated", "markets", bool(pct >= 80),
            f"{label} {s.iloc[-1]:.2f} pts, {ordinal(pct)} percentile since {s.index[0]:%Y}")
    if idx.notna().any():
        v = idx.dropna().iloc[-1]
        add("Financial stress high", "markets", bool(v > 1), f"Stress index {v:+.2f} (above +1 = stressed)")

    # --- Forex ---
    if "DTWEXBGS" in nat:
        chg = pct_change_over(nat["DTWEXBGS"], 91)
        if not np.isnan(chg):
            add("Dollar surging", "fx", bool(chg > 3), f"Broad dollar {chg:+.1f}% over 3 months (signal above +3%)")
    if "DEXJPUS" in nat and "DEXSZUS" in nat:
        jpy, chf = pct_change_over(nat["DEXJPUS"], 30), pct_change_over(nat["DEXSZUS"], 30)
        if not (np.isnan(jpy) or np.isnan(chf)):
            add("Safe-haven bid (yen & franc rallying)", "fx", bool(jpy < -2 and chf < -2),
                f"USD/JPY {jpy:+.1f}%, USD/CHF {chf:+.1f}% over 1 month (signal: both below -2%)")
    if "RTWEXBGS" in nat and history_years(nat["RTWEXBGS"]) >= config.MIN_HISTORY_YEARS:
        r = nat["RTWEXBGS"].dropna()
        gap = (r.iloc[-1] / r.mean() - 1) * 100
        add("Dollar far from PPP (real terms)", "fx", bool(abs(gap) > 10),
            f"Real dollar {gap:+.1f}% vs. its {r.index[0]:%Y}-{r.index[-1]:%Y} average (signal beyond ±10%)")
    return flags


def build(raw: pd.DataFrame) -> dict:
    """Everything the app needs, in one call."""
    nat = natives(raw)
    p = panel(nat)
    g = gauges(p)
    idx = stress_index(g)
    lc = leading_components(p)
    lead = leading_index(lc)
    fxm = fx_monthly(nat, idx)
    return dict(nat=nat, panel=p, gauges=g, stress=idx, lead_parts=lc, lead=lead,
                recessions=recessions(nat), flags=regime_flags(p, nat, idx, lead),
                fx_monthly=fxm, fx_table=fx_table(nat, fxm), fx_crosses=fx_crosses(nat))


if __name__ == "__main__":
    b = build(pd.read_parquet(config.RAW_FILE))
    print(f"Data through {b['panel'].index.max().date()}\n")
    for sec, title in config.SECTIONS.items():
        print(title)
        for f in [f for f in b["flags"] if f["section"] == sec]:
            mark = "YES" if f["on"] else ("no " if f["on"] is False else " ? ")
            print(f"  [{mark}] {f['name']:<34} {f['detail']}")
    print(f"\nStress index: {b['stress'].dropna().iloc[-1]:+.2f}   Leading index: {b['lead'].dropna().iloc[-1]:+.2f}")
