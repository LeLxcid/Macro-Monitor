"""
fetch.py - download (or update) every series in config.py from FRED and save
them to data/raw.parquet.

Usage:
    python fetch.py          # pull real data from FRED (needs FRED_API_KEY)
    python fetch.py --demo   # make fake sample data so you can try the app
                             # before you have a key (the app labels it DEMO)

Running it again later only downloads what's new (plus the last 60 days, since
FRED sometimes revises recent values), so it's fast to run every day.
"""

import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config

# Python from python.org on a Mac doesn't trust any HTTPS certificates until you run its
# "Install Certificates" step, so every request to FRED fails. Pointing Python at the
# certifi bundle (installed with the other libraries) fixes that without any extra setup.
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass


def get_api_key():
    """Find the FRED key: a .env file locally, or Streamlit secrets when hosted."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    key = os.environ.get("FRED_API_KEY")
    if not key:
        try:
            import streamlit as st
            key = st.secrets.get("FRED_API_KEY")
        except Exception:
            key = None
    return key


def load_raw():
    """Load the saved data, or an empty table if there isn't any yet."""
    if os.path.exists(config.RAW_FILE):
        return pd.read_parquet(config.RAW_FILE)
    return pd.DataFrame(columns=["date", "series_id", "value"])


def save(raw, demo):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    raw = raw.sort_values(["series_id", "date"]).reset_index(drop=True)
    raw.to_parquet(config.RAW_FILE, index=False)
    meta = {"updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "demo": demo}
    with open(config.META_FILE, "w") as f:
        json.dump(meta, f)


def fetch_real():
    key = get_api_key()
    if not key:
        sys.exit("No FRED_API_KEY found. Put it in a .env file (see .env.example), "
                 "or run `python fetch.py --demo` to try the app with sample data.")

    from fredapi import Fred
    fred = Fred(api_key=key)

    raw = load_raw()
    # If the saved file is demo data, start fresh with real data.
    if os.path.exists(config.META_FILE):
        with open(config.META_FILE) as f:
            if json.load(f).get("demo"):
                raw = raw.iloc[0:0]

    frames, failed = [], []
    for sid in config.SERIES:
        have = raw[raw.series_id == sid]
        if len(have):
            # Re-pull the last 60 days to pick up revisions.
            start = (pd.to_datetime(have.date.max()) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        else:
            start = config.START_DATE
        try:
            s = fred.get_series(sid, observation_start=start).dropna()
        except Exception as e:  # a bad ID or a FRED hiccup shouldn't stop the rest
            failed.append(f"{sid} ({e})")
            continue
        new = pd.DataFrame({"date": pd.to_datetime(s.index), "series_id": sid, "value": s.values.astype(float)})
        old = have[pd.to_datetime(have.date) < pd.to_datetime(start)]
        frames.append(pd.concat([old, new]))
        print(f"  {sid:<16} {len(new):>5} rows pulled, latest {new.date.max().date() if len(new) else '-'}")

    if not frames:
        # Nothing downloaded at all: don't overwrite good data with an empty file.
        first = failed[0] if failed else "unknown error"
        raise RuntimeError(
            "Every FRED download failed, so nothing was saved. First error: " + first +
            "\nCommon causes: no internet connection, a mistyped key in .env, or FRED being down.")

    # Keep series we failed to refresh, as they were.
    kept = raw[raw.series_id.isin([f.split(' ')[0] for f in failed])]
    out = pd.concat(frames + [kept], ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    save(out, demo=False)
    print(f"Saved {len(out):,} rows for {out.series_id.nunique()} series to {config.RAW_FILE}")
    if failed:
        print("Could not update:", "; ".join(failed))


def fetch_demo():
    """Fake but realistic-looking data, with stress episodes in late 2008 and March 2020,
    so you can see how the dashboard behaves. None of these numbers are real."""
    rng = np.random.default_rng(7)
    today = pd.Timestamp.today().normalize()
    days = pd.bdate_range("2005-01-01", today)
    n = len(days)
    t = np.arange(n)

    def bump(center, width, height):
        c = days.get_indexer([pd.Timestamp(center)], method="nearest")[0]
        return height * np.exp(-0.5 * ((t - c) / width) ** 2)

    stress = bump("2008-11-20", 60, 1.0) + bump("2020-03-20", 15, 0.7) + bump("2022-10-01", 60, 0.25) + bump("2011-10-01", 40, 0.25)
    rec = bump("2009-03-01", 120, 1.0) + bump("2020-04-15", 20, 1.2)       # recession dips
    lead = bump("2008-06-01", 150, 1.0) + bump("2020-03-15", 15, 1.0)      # leading indicators turn earlier
    yrs = t / 252
    walk = lambda scale: np.cumsum(rng.normal(0, scale, n))
    noise = lambda scale: rng.normal(0, scale, n)
    path = lambda *pts: np.interp(t, np.linspace(0, n, len(pts)), pts)

    policy = path(2.25, 5.25, 0.25, 0.25, 2.4, 0.25, 5.5, 4.0, 4.0)
    in_rec = ((days >= "2008-01-01") & (days < "2009-07-01")) | ((days >= "2020-02-01") & (days < "2020-05-01"))
    d = {
        # Economy
        "GDPC1": 14000 * np.exp(0.02 * yrs) * (1 - 0.04 * rec),
        "SP500": 1200 * np.exp(0.07 * yrs) * (1 - 0.45 * lead) * np.exp(walk(0.006) * 0.3),
        "ICSA": 230000 * (1 + 1.5 * lead) + 5e6 * bump("2020-03-30", 4, 1.0) + noise(8000),
        "PERMIT": 1900 - 1100 * bump("2010-01-01", 500, 1.0) + 250 * bump("2021-12-01", 150, 1.0) + noise(30),
        "UMCSENT": 88 - 25 * lead - 30 * bump("2022-06-15", 60, 1.0) + noise(2),
        "NEWORDER": 55000 * np.exp(0.02 * yrs) * (1 - 0.25 * lead) * (1 + noise(0.01)),
        "ACOGNO": 140000 * np.exp(0.025 * yrs) * (1 - 0.15 * lead) * (1 + noise(0.008)),
        "PAYEMS": 133000 * np.exp(0.006 * yrs) * (1 - 0.045 * bump("2009-12-01", 250, 1.0) - 0.13 * bump("2020-04-15", 12, 1.0)),
        "UNRATE": np.clip(4.6 + 5 * bump("2009-10-01", 180, 1.0) + 10 * bump("2020-04-15", 25, 1.0) - 0.8 * bump("2019-06-01", 400, 1.0) + noise(0.05), 3.3, None),
        "CPIAUCSL": 192 * np.exp(0.022 * yrs + 0.12 / (1 + np.exp(-(days.year + days.dayofyear / 365 - 2022) * 3))),
        "CP": 900 * np.exp(0.05 * yrs) * (1 - 0.3 * rec) * (1 + noise(0.01)),
        "TCMDO": 40e6 * np.exp(0.045 * yrs),
        "USREC": in_rec.astype(float),
        # Fed & banks
        "DFEDTARU": policy,
        "DFEDTARL": policy - 0.25,
        "IORB": policy - 0.1,
        "TREAST": path(0.48e6, 0.48e6, 1.7e6, 2.4e6, 5.6e6, 4.3e6, 4.2e6),
        "WSHOMCB": path(0, 0, 1.0e6, 1.7e6, 2.6e6, 2.3e6, 2.1e6),
        "WALCL": path(0.85e6, 2.2e6, 4.5e6, 3.8e6, 8.9e6, 7.0e6, 6.6e6),
        "RPONTSYD": np.clip(1 + 75 * bump("2019-09-20", 15, 1.0) + 200 * bump("2020-03-20", 10, 1.0) + noise(1), 0, None),
        "RRPONTSYD": np.clip(path(5, 10, 50, 2300, 400, 30) + noise(20), 0, None),
        "WLCFLPCL": np.clip(100 + 100000 * bump("2008-11-01", 40, 1.0) + 50000 * bump("2020-04-01", 15, 1.0) + 150000 * bump("2023-03-20", 5, 1.0) + noise(30), 0, None),
        "WRESBAL": path(0.01e6, 1.0e6, 2.7e6, 1.5e6, 4.2e6, 3.2e6, 3.0e6),
        "DPSACBW027SBOG": path(6000, 8000, 13000, 18000, 17800),
        "TOTBKCR": path(7500, 9500, 12000, 17000, 17800),
        "DRTSCILM": 60 * stress - 5 + noise(4),
        # Markets
        "DGS2": policy + 0.2 + walk(0.01) * 0.3,
        "DGS10": 2.8 + policy * 0.35 + walk(0.012) * 0.3,
        "BAMLH0A0HYM2": 3.8 + 14 * stress + np.abs(walk(0.02)) * 0.3,
        "BAMLC0A0CM": 1.2 + 4.5 * stress + np.abs(walk(0.006)) * 0.2,
        "VIXCLS": 15 + 55 * stress + np.abs(noise(2)),
        "SOFR": policy - 0.05 + 0.6 * bump("2019-09-17", 2, 1.0) + noise(0.01),
        "NFCI": -0.5 + 3.2 * stress + walk(0.004) * 0.2,
    }

    frames = []
    for sid, vals in d.items():
        s = pd.Series(vals, index=days)
        freq = config.SERIES[sid]["freq"]
        if freq == "weekly":
            s = s.resample("W-WED").last()
        elif freq == "monthly":
            s = s.resample("MS").first()
        elif freq == "quarterly":
            s = s.resample("QS").first()
        if sid in ("SOFR", "IORB", "RRPONTSYD"):
            s = s[s.index >= "2018-04-01"]  # these series only exist from ~2018/2021
        if sid == "SP500":
            s = s[s.index >= today - pd.DateOffset(years=10)]  # FRED only provides 10 years
        s = s[s.index <= today]
        frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
    save(pd.concat(frames, ignore_index=True), demo=True)
    print(f"Saved DEMO data to {config.RAW_FILE}. These numbers are fake. Run `python fetch.py` with a key for real data.")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        fetch_demo()
    else:
        fetch_real()
