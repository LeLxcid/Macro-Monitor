"""
app.py - the interactive dashboard.

Run it with:
    streamlit run app.py

Tabs: Overview (all warning flags + the two summary indexes), Economy (leading /
coincident / lagging indicators), Fed & banks (the Fed's policy tools and the banking
system), Markets & stress, and Explore (any series, any view).

If you have a FRED key set up, the app refreshes the data automatically the first time
you open it each day (or click "Update data now").
"""

import json
import os
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import fetch
import signals

st.set_page_config(page_title="Macro Monitor", layout="wide")

# Chart colors: the first three slots of a colorblind-checked palette, always in this order.
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
CRITICAL = "#d03b3b"
STALE_HOURS = 20  # refresh from FRED if data is older than this


# ---------- data loading ----------

def read_meta():
    if os.path.exists(config.META_FILE):
        with open(config.META_FILE) as f:
            return json.load(f)
    return {}


def maybe_refresh():
    """Pull new data from FRED if we have a key and the saved data is stale."""
    if not fetch.get_api_key():
        return None
    meta = read_meta()
    updated = meta.get("updated_utc")
    age_h = 1e9 if not updated else (datetime.now(timezone.utc) - datetime.fromisoformat(updated)).total_seconds() / 3600
    empty = not os.path.exists(config.RAW_FILE) or pd.read_parquet(config.RAW_FILE).empty
    if age_h > STALE_HOURS or meta.get("demo") or empty:
        try:
            fetch.fetch_real()
            return "updated"
        except (Exception, SystemExit) as e:  # SystemExit is what fetch raises when there's no key
            return f"error: {e}"
    return None


@st.cache_data(ttl=6 * 3600, show_spinner="Loading data...")
def load():
    status = maybe_refresh()
    if not os.path.exists(config.RAW_FILE):
        return None, status
    raw = pd.read_parquet(config.RAW_FILE)
    if raw.empty:
        return None, status
    return signals.build(raw), status


B, refresh_status = load()
meta = read_meta()

# ---------- sidebar ----------

with st.sidebar:
    st.header("Data")
    if meta.get("updated_utc"):
        st.caption(f"Last fetched: {meta['updated_utc'].replace('T', ' ')[:16]} UTC")
    if fetch.get_api_key():
        if st.button("Update data now", width="stretch"):
            ok = False
            with st.spinner("Pulling from FRED..."):
                try:
                    fetch.fetch_real()
                    ok = True
                except (Exception, SystemExit) as e:  # SystemExit = no key; NOT BaseException,
                    st.error(f"Update failed: {e}")    # which also swallows Streamlit's rerun signal
            if ok:  # st.rerun() works by raising an exception, so it must stay outside the try
                st.cache_data.clear()
                st.rerun()
    else:
        st.info("No FRED key found. Add one to a `.env` file (see README) to get real, auto-updating data.")
    if refresh_status and refresh_status.startswith("error"):
        st.warning(f"Automatic update failed: {refresh_status[7:]}")
    st.divider()
    years = st.slider("Years of history on charts", 2, 26, 20, key="years")
    st.caption("Gray bands on charts are recessions (NBER).")
    st.divider()
    st.caption("Built from the Money & Credit study guide.")

if B is None:
    st.title("Macro Monitor")
    if refresh_status and refresh_status.startswith("error"):
        st.error(f"Couldn't download data from FRED: {refresh_status[7:]}")
    st.warning("No data yet. Double-click setup.command again, or in Terminal run `python fetch.py` "
               "(real data) or `python fetch.py --demo` (sample data).")
    st.stop()

nat, p = B["nat"], B["panel"]
START = p.index.max() - pd.DateOffset(years=years)
RECS = B["recessions"]


# ---------- helpers ----------

def have(*sids):
    return all(s in nat and not nat[s].empty for s in sids)


def clip(s):
    return s[s.index >= START].dropna()


_chart_ids = iter(range(10_000))  # every chart needs its own key when the same chart appears on two tabs


def chart(traces, units, height=300, hline=None, hline_label=None, stacked=False):
    """traces: list of (series, name, color). One y-axis, recessions shaded."""
    fig = go.Figure()
    for s, name, color in traces:
        s = clip(s)
        fig.add_trace(go.Scatter(
            x=s.index, y=s.values, name=name, mode="lines",
            line=dict(color=color, width=2 if not stacked else 0.5),
            stackgroup="one" if stacked else None,
            hovertemplate="%{x|%b %d, %Y}<br>" + name + ": %{y:,.2f}<extra></extra>"))
    for r0, r1 in RECS:
        if r1 >= START:
            fig.add_vrect(x0=max(r0, START), x1=r1, fillcolor="gray", opacity=0.15, line_width=0, layer="below")
    if hline is not None:
        fig.add_hline(y=hline, line=dict(color="gray", width=1, dash="dot"),
                      annotation_text=hline_label or "", annotation_position="top left")
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                      yaxis_title=units, showlegend=len(traces) > 1,
                      legend=dict(orientation="h", y=1.12, x=0))
    st.plotly_chart(fig, width="stretch", key=f"chart_{next(_chart_ids)}")


def fmt(v):
    """Readable numbers: 233,203 not 233,202.95; 4.52 stays 4.52."""
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.2f}"


def series_view(sid, view=None):
    m = config.SERIES[sid]
    return signals.transform(nat[sid], view or m["view"])


def view_units(sid, view=None):
    view = view or config.SERIES[sid]["view"]
    return "% vs. a year ago" if view == "yoy" else config.SERIES[sid]["units"]


def small_chart(sid):
    """One indicator: title, latest reading, chart in its default view."""
    m = config.SERIES[sid]
    s = series_view(sid).dropna()
    if s.empty:
        return
    unit = view_units(sid)
    st.markdown(f"**{m['name']}**  \n:gray[{signals.VIEW_LABELS[m['view']]} · latest {fmt(s.iloc[-1])} "
                f"({s.index[-1]:%b %Y})]")
    chart([(s, m["name"], C1)], unit, height=220, hline=0 if m["view"] in ("yoy", "diff") else None)
    st.caption(m["why"])


def flag_cards(flags, ncols=4):
    cols = st.columns(ncols)
    for i, f in enumerate(flags):
        with cols[i % ncols]:
            with st.container(border=True):
                badge = "**? No data**" if f["on"] is None else (":red[**⚠ Yes**]" if f["on"] else ":green[**✓ No**]")
                st.markdown(f"{badge}  \n**{f['name']}**  \n{f['detail']}")


def last(sid):
    return nat[sid].dropna().iloc[-1] if have(sid) else None


# ---------- header ----------

st.title("Macro Monitor")
# Newest DAILY observation, capped at today: some weekly series are stamped with a date at
# the end of their week, which made the header show a date in the future.
_today = pd.Timestamp.today().normalize()
_daily = [nat[s].dropna().index.max() for s, m in config.SERIES.items() if m["freq"] == "daily" and have(s)]
_through = min(max(_daily), _today) if _daily else min(p.index.max(), _today)
st.caption(f"Daily data through **{_through:%b %d, %Y}** · {len(nat)} series from FRED")
if meta.get("demo"):
    st.error("DEMO DATA: these numbers are made up so you can try the app. "
             "Run `python fetch.py` with a FRED key for real data.", icon="⚠️")

tab_over, tab_econ, tab_fed, tab_mkt, tab_fx, tab_exp = st.tabs(
    ["Overview", "Economy", "Fed & banks", "Markets & stress", "Forex", "Explore"])

# =====================================================================
with tab_over:
    st.caption("Each flag is a yes/no reading of the latest data. Yes means that warning sign is showing.")
    for sec, title in config.SECTIONS.items():
        fl = [f for f in B["flags"] if f["section"] == sec]
        if fl:
            st.markdown(f"#### {title}")
            flag_cards(fl)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Leading index")
        st.caption("Your version of a leading economic index: the average of the leading indicators' "
                   "6-month changes. Below 0 = weaker than normal. It should fall before recessions.")
        chart([(B["lead"], "Leading index", C1)], "std. deviations from normal", hline=0)
    with c2:
        st.markdown("#### Financial stress index")
        st.caption("The average of the stress gauges. Above +1 = stressed. The Chicago Fed's NFCI is "
                   "shown as an independent check.")
        tr = [(B["stress"], "Your stress index", C1)]
        if have("NFCI"):
            tr.append((nat["NFCI"], "Chicago Fed NFCI", C2))
        chart(tr, "std. deviations from normal", hline=1, hline_label="stressed")

# =====================================================================
with tab_econ:
    k = st.columns(5)
    if have("GDPC1"):
        g = signals.yoy(nat["GDPC1"]).dropna()
        k[0].metric("Real GDP growth", f"{g.iloc[-1]:.1f}%", f"{g.iloc[-1]-g.iloc[-2]:+.1f} pts vs. prior qtr",
                    delta_color="off", help=f"vs. a year ago, {g.index[-1].to_period('Q')}")
    if have("CPIAUCSL"):
        c = signals.yoy(nat["CPIAUCSL"]).dropna()
        k[1].metric("CPI inflation", f"{c.iloc[-1]:.1f}%", f"{c.iloc[-1]-c.iloc[-2]:+.1f} pts vs. prior month",
                    delta_color="off", help=f"vs. a year ago, {c.index[-1]:%b %Y}")
    if have("UNRATE"):
        u = nat["UNRATE"].dropna()
        k[2].metric("Unemployment", f"{u.iloc[-1]:.1f}%", f"{u.iloc[-1]-u.iloc[-2]:+.1f} pts vs. prior month",
                    delta_color="off", help=f"{u.index[-1]:%b %Y}")
    if have("PAYEMS"):
        j = nat["PAYEMS"].diff().dropna()
        k[3].metric("Jobs added", f"{j.iloc[-1]:+,.0f}k", f"3-mo avg {j.tail(3).mean():+,.0f}k",
                    delta_color="off", help=f"{j.index[-1]:%b %Y}")
    if have("DFEDTARU", "DFEDTARL"):
        k[4].metric("Fed funds target", f"{last('DFEDTARL'):.2f}–{last('DFEDTARU'):.2f}%")

    st.markdown("#### Big picture")
    b1, b2 = st.columns(2)
    with b1:
        if have("GDPC1"):
            small_chart("GDPC1")
    with b2:
        if have("UNRATE"):
            st.markdown("**Sahm rule indicator**  \n:gray[recession signal at +0.50]")
            chart([(signals.sahm(nat["UNRATE"]), "Sahm indicator", C1)], "% points", height=220,
                  hline=0.5, hline_label="signal")
            st.caption("3-month average unemployment vs. its low of the past year. Crossing +0.5 has marked "
                       "the start of every US recession since 1970.")

    blurbs = {
        "leading": "Tend to turn **before** the economy. Watch these for what's coming.",
        "coincident": "Move **with** the economy. They tell you where it is now.",
        "lagging": "Turn **after** the economy. They confirm a turn that already happened.",
    }
    for cyc in ("leading", "coincident", "lagging"):
        st.markdown(f"#### {config.CYCLES[cyc]} indicators")
        st.caption(blurbs[cyc])
        sids = [s for s, m in config.SERIES.items() if m["cycle"] == cyc and have(s)]
        cols = st.columns(3)
        for i, sid in enumerate(sids):
            with cols[i % 3]:
                small_chart(sid)
        if cyc == "leading" and len(B["lead_parts"].columns):
            with st.expander("What's driving the leading index today"):
                lp = B["lead_parts"].ffill().iloc[-1].dropna().sort_values()
                for name, v in lp.items():
                    st.markdown(f"{'▼' if v < -0.5 else ('▲' if v > 0.5 else '•')} {name}: **{v:+.2f}**")

# =====================================================================
with tab_fed:
    st.markdown("#### The Fed's policy tools right now")
    rows = []
    if have("DFEDTARU", "DFEDTARL"):
        rows.append(("Fed funds target range", f"{last('DFEDTARL'):.2f}–{last('DFEDTARU'):.2f}%",
                     "The policy rate. Everything else on this page is how the Fed keeps market rates inside it."))
    if have("IORB"):
        rows.append(("Interest on reserve balances", f"{last('IORB'):.2f}%",
                     "Paid to banks on reserves. The main tool for steering rates since 2008."))
    if have("DFEDTARU"):
        rows.append(("Discount rate (primary credit)", f"{last('DFEDTARU'):.2f}%",
                     "What banks pay to borrow at the discount window. Set at the top of the target range since March 2020."))
    if have("TREAST", "WSHOMCB"):
        t, m = nat["TREAST"], nat["WSHOMCB"]
        chg = (t.iloc[-1] + m.iloc[-1]) - (t.iloc[-14] + m.iloc[-14])
        rows.append(("Open market operations: permanent", f"${(t.iloc[-1]+m.iloc[-1])/1e6:,.2f}T held · {chg/1e3:+,.0f}B in 13 wks",
                     "Treasuries and MBS the Fed owns. Rising = QE (adds reserves); falling = QT (drains them)."))
    if have("RPONTSYD", "RRPONTSYD"):
        rows.append(("Open market operations: temporary", f"Repo ${last('RPONTSYD'):,.0f}B · Reverse repo ${last('RRPONTSYD'):,.0f}B",
                     "Overnight operations. Repo lends cash to the market; reverse repo absorbs it."))
    for name, (val, why) in config.STATIC_POLICY.items():
        rows.append((name, val, why))
    table = "| Tool | Setting now | What it does |\n| --- | --- | --- |\n" + "\n".join(
        f"| **{a}** | {b} | {c} |" for a, b, c in rows)
    st.markdown(table)
    st.caption("Reserve and margin requirements are fixed rules, not data feeds; they're set in config.py.")

    f1, f2 = st.columns(2)
    with f1:
        st.markdown("**Policy rates**")
        tr = [(nat[s], n, c) for s, n, c in [("DFEDTARU", "Target, upper", C1), ("IORB", "Interest on reserves", C2),
                                              ("SOFR", "SOFR (market repo rate)", C3)] if have(s)]
        chart(tr, "%")
    with f2:
        if have("WALCL", "TREAST", "WSHOMCB"):
            st.markdown("**Fed balance sheet: what it holds**")
            other = (nat["WALCL"] - nat["TREAST"] - nat["WSHOMCB"]).dropna()
            chart([(nat["TREAST"] / 1e6, "Treasuries", C1), (nat["WSHOMCB"] / 1e6, "MBS", C2),
                   (other / 1e6, "Everything else (loans, etc.)", C3)], "$ trillions", stacked=True)
    f3, f4 = st.columns(2)
    with f3:
        if have("RPONTSYD", "RRPONTSYD"):
            st.markdown("**Temporary operations: repo vs. reverse repo**")
            chart([(nat["RPONTSYD"], "Repo (Fed lends cash)", C1),
                   (nat["RRPONTSYD"], "Reverse repo (Fed absorbs cash)", C2)], "$ billions")
    with f4:
        if have("WLCFLPCL"):
            st.markdown("**Discount window borrowing**")
            chart([(nat["WLCFLPCL"] / 1e3, "Primary credit", C1)], "$ billions")
            st.caption(config.SERIES["WLCFLPCL"]["why"])

    st.markdown("#### The banking system")
    cols = st.columns(3)
    for i, sid in enumerate(["WRESBAL", "DPSACBW027SBOG", "TOTBKCR", "DRTSCILM"]):
        if have(sid):
            with cols[i % 3]:
                small_chart(sid)

# =====================================================================
with tab_mkt:
    m1, m2 = st.columns([2, 1])
    with m1:
        st.markdown("#### Financial stress index")
        tr = [(B["stress"], "Your stress index", C1)]
        if have("NFCI"):
            tr.append((nat["NFCI"], "Chicago Fed NFCI", C2))
        chart(tr, "std. deviations from normal", hline=1, hline_label="stressed")
    with m2:
        st.markdown("#### What's driving it today")
        st.caption("Each gauge: how unusual today is vs. its own history (0 = normal).")
        latest = B["gauges"].ffill().iloc[-1].dropna().sort_values(ascending=False)
        for name, v in latest.items():
            st.markdown(f"{'▲' if v > 0.5 else ('▼' if v < -0.5 else '•')} {name}: **{v:+.2f}**")

    a, b = st.columns(2)
    with a:
        if have("DGS2", "DGS10"):
            st.markdown("**Treasury yields**")
            chart([(nat["DGS2"], "2-year", C1), (nat["DGS10"], "10-year", C2)], "%")
    with b:
        if "CURVE" in p:
            st.markdown("**Yield curve (10y minus 2y)**")
            chart([(p["CURVE"], "10y - 2y", C1)], "% points", hline=0, hline_label="inverted below")
    a, b = st.columns(2)
    with a:
        tr = [(nat[s], n, c) for s, n, c in [("BAMLH0A0HYM2", "High-yield", C1), ("BAMLC0A0CM", "Investment-grade", C2),
                                              ("BAA10Y", "Baa (Moody's)", C3)] if have(s)]
        if tr:
            st.markdown("**Corporate bond spreads**")
            chart(tr, "% points")
            st.caption("FRED only provides 3 years of the high-yield and investment-grade spreads, so the "
                       "stress index and credit flag use the Baa spread, which goes back to 1986.")
    with b:
        if have("SOFR", "IORB"):
            st.markdown("**Repo funding squeeze (SOFR minus IORB)**")
            chart([((p["SOFR"] - p["IORB"]) * 100, "SOFR - IORB", C1)], "basis points", hline=0)
    if have("MORTGAGE30US", "DGS10"):
        a, b = st.columns(2)
        with a:
            st.markdown("**30-year mortgage rate vs. 10-year Treasury**")
            chart([(nat["MORTGAGE30US"], "30-yr mortgage", C1), (nat["DGS10"], "10-yr Treasury", C2)], "%")
        with b:
            st.markdown("**Mortgage spread (mortgage rate minus 10-year)**")
            m = nat["MORTGAGE30US"]
            spread = (m - p["DGS10"].reindex(m.index, method="ffill")).dropna()  # matched to each weekly reading
            chart([(spread, "Mortgage spread", C1)], "% points")
            st.caption("Mortgage rates follow the 10-year yield, plus a spread for prepayment risk and how much "
                       "investors (and the Fed) want mortgage bonds. A widening spread means mortgages are getting "
                       "pricier even if Treasury yields don't move.")
    if have("VIXCLS"):
        st.markdown("**VIX**")
        chart([(nat["VIXCLS"], "VIX", C1)], "index", height=250)

# =====================================================================
with tab_fx:
    st.caption("The major pairs, what's been driving them, and what the ECON 4810 theories (Feenstra/Taylor "
               "ch. 2-4) say to look for. Rates are the Fed's daily noon quotes in New York, published weekly, "
               "so this is a daily-close view, not a live one.")
    pairs = [p for p, m in config.FX_PAIRS.items() if have(m["sid"])]
    if not pairs:
        st.info("No forex data yet. Click **Update data now** in the sidebar to download it.")
    else:
        # ---- latest quotes ----
        cols = st.columns(len(pairs) + (1 if have("DTWEXBGS") else 0))
        for i, pair in enumerate(pairs):
            s = nat[config.FX_PAIRS[pair]["sid"]].dropna()
            dec = 2 if s.iloc[-1] > 20 else 4
            cols[i].metric(pair, f"{s.iloc[-1]:.{dec}f}", f"{signals.pct_change_over(s, 30):+.1f}% 1 mo",
                           delta_color="normal",  # green when the pair rose over the month, red when it fell
                           help=f"As of {s.index[-1]:%b %d, %Y}")
        if have("DTWEXBGS"):
            d = nat["DTWEXBGS"].dropna()
            cols[-1].metric("Broad dollar", f"{d.iloc[-1]:.1f}", f"{signals.pct_change_over(d, 30):+.1f}% 1 mo",
                            delta_color="normal", help=f"Trade-weighted, as of {d.index[-1]:%b %d, %Y}")

        # ---- 1) asset approach ----
        st.markdown("#### What's moving the majors (asset approach, ch. 4)")
        st.caption("Uncovered interest parity says a currency strengthens when its interest rates rise relative to "
                   "the other country's. The table checks that, and how each currency behaves when US financial "
                   "stress rises. Correlations use monthly changes over the last 5 years.")
        T = B["fx_table"]
        if not T.empty:
            show = T.drop(columns=[c for c in T.columns if c.startswith("Carry")]).copy()
            show["As of"] = pd.to_datetime(show["As of"]).dt.strftime("%b %d")
            st.dataframe(show, width="stretch", column_config={
                "Latest": st.column_config.NumberColumn(format="%.4f"),
                "1-month %": st.column_config.NumberColumn(format="%+.1f"),
                "1-year %": st.column_config.NumberColumn(format="%+.1f"),
                "US minus foreign 10y": st.column_config.NumberColumn(
                    format="%+.2f", help="US 10-year yield minus the foreign one, % points. Positive = dollar pays more."),
                "Gap change, 6 mo": st.column_config.NumberColumn(format="%+.2f"),
                "Corr with gap": st.column_config.NumberColumn(
                    format="%+.2f", help="How the foreign currency moved with ITS yields rising vs. US yields. "
                                         "Positive = moves the way UIP predicts."),
                "Corr with stress": st.column_config.NumberColumn(
                    format="%+.2f", help="How the foreign currency moved when your US stress index rose. "
                                         "Positive = safe haven (yen, franc); negative = risk-on (Aussie, kiwi)."),
            })
            havens = [p for p in T.index if T.loc[p].get("Corr with stress", 0) > 0.1]
            risky = [p for p in T.index if T.loc[p].get("Corr with stress", 0) < -0.1]
            if havens or risky:
                st.caption(f"Acting as safe havens lately: **{', '.join(havens) or 'none'}**. "
                           f"Selling off with stress: **{', '.join(risky) or 'none'}**.")

        pick = st.selectbox("Look at one pair", pairs, key="fx_pick")
        pm = config.FX_PAIRS[pick]
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**{pick}**  \n:gray[{config.SERIES[pm['sid']]['units']}]")
            chart([(nat[pm["sid"]], pick, C1)], config.SERIES[pm["sid"]]["units"], height=260)
        with c2:
            df = B["fx_monthly"].get(pick)
            if df is not None and "gap" in df:
                st.markdown(f"**10-year yield gap: US minus {pm['ccy']}**  \n:gray[% points, monthly]")
                chart([(df["gap"], "US minus foreign", C2)], "% points", height=260, hline=0)
        st.caption(("A rising gap favors the dollar, so under UIP " + pick + (" should rise." if pm["usd_base"] else " should fall."))
                   + " Shown as two charts on purpose: a pair and a rate gap on one chart with two axes is easy to misread.")

        # ---- 2) the dollar and risk ----
        st.markdown("#### The dollar and financial stress")
        c1, c2 = st.columns(2)
        with c1:
            tr = [(nat[s], n, c) for s, n, c in [("DTWEXBGS", "Broad dollar", C1), ("DTWEXAFEGS", "vs. advanced economies", C2)] if have(s)]
            if tr:
                st.markdown("**Trade-weighted dollar** (the effective exchange rate, ch. 2)")
                chart(tr, "index", height=260)
        with c2:
            st.markdown("**Your stress index**")
            chart([(B["stress"], "Stress index", C1)], "std. deviations from normal", height=260, hline=1, hline_label="stressed")
        st.caption("The dollar usually rises when global stress spikes (2008, March 2020) because the world borrows "
                   "in dollars and scrambles for them: the hierarchy of money from your Money & Credit readings.")

        # ---- 3) PPP ----
        if have("RTWEXBGS"):
            st.markdown("#### The long run: PPP and the real dollar (ch. 3)")
            r = nat["RTWEXBGS"].dropna()
            gap = (r.iloc[-1] / r.mean() - 1) * 100
            c1, c2 = st.columns([1, 2])
            with c1:
                st.metric("Real dollar vs. its average", f"{gap:+.1f}%", help=f"Average since {r.index[0]:%Y}: {r.mean():.1f}")
                st.caption("The real exchange rate adjusts for inflation in the US and abroad. If relative PPP held, "
                           "it would stay near its average, so a large gap says the dollar is expensive (above) or "
                           "cheap (below) in real terms. PPP deviations are slow to fade: the textbook half-life is "
                           "3-5 years, so this is a long-run lean, not a timing signal.")
            with c2:
                chart([(r, "Real broad dollar", C1)], "index", height=260, hline=r.mean(), hline_label="long-run average")

        # ---- 4) arbitrage ----
        st.markdown("#### Arbitrage opportunities (ch. 2)")
        st.caption("Ch. 2 covers three kinds of currency arbitrage. Here's what each looks like right now, and "
                   "honestly, how much of it a dashboard on daily data can find.")
        a1, a2, a3 = st.tabs(["Triangular (cross rates)", "Covered interest parity", "Uncovered interest parity: carry"])
        with a1:
            X = B["fx_crosses"]
            if not X.empty:
                xs = X.copy()
                xs["As of"] = pd.to_datetime(xs["As of"]).dt.strftime("%b %d")
                st.dataframe(xs, width="stretch", column_config={
                    "Implied rate": st.column_config.NumberColumn(format="%.4f"),
                    "1-month %": st.column_config.NumberColumn(format="%+.1f"),
                    "1-year %": st.column_config.NumberColumn(format="%+.1f")})
            st.markdown(
                "These cross rates are built through the dollar as the **vehicle currency**: "
                "E(¥/€) = E(¥/$) × E($/€). Triangular arbitrage is the bet that a bank's *direct* quote for a "
                "cross differs from this implied rate. In real markets that gap is closed by bank algorithms in "
                "milliseconds and is smaller than the bid-ask spread, so you'd need live quotes from two venues "
                "and very low costs to see one. FRED has no independent cross quotes, so this table can't show "
                "triangular arbitrage. Use it as a fair-value reference for crosses.")
        with a2:
            st.markdown(
                "**Riskless arbitrage.** Covered interest parity says the forward premium equals the interest gap: "
                "F/E = (1 + i$)/(1 + i*). Testing it needs forward rates, which aren't on FRED. The evidence "
                "slide in ch. 2 shows gaps closing once capital controls ended. Since 2008 a small, persistent "
                "deviation (the 'cross-currency basis') has reappeared, but only large banks with cheap balance "
                "sheets can trade it. It isn't an opportunity for an individual account.")
        with a3:
            if not T.empty and "Carry: long" in T:
                cs = T[["US minus foreign 10y", "Carry: long", "Carry, last 12 mo %", "Carry, avg 12 mo %",
                        "Carry, % of years won"]].copy()
                cs["Rate edge (% pts)"] = cs["US minus foreign 10y"].abs()
                cs = cs.drop(columns="US minus foreign 10y").sort_values("Rate edge (% pts)", ascending=False)
                st.dataframe(cs[["Carry: long", "Rate edge (% pts)", "Carry, last 12 mo %", "Carry, avg 12 mo %",
                                 "Carry, % of years won"]], width="stretch", column_config={
                    "Rate edge (% pts)": st.column_config.NumberColumn(format="%.2f"),
                    "Carry, last 12 mo %": st.column_config.NumberColumn(format="%+.1f"),
                    "Carry, avg 12 mo %": st.column_config.NumberColumn(
                        format="%+.1f", help="Average of every rolling 12-month carry return in the data. UIP says ~0."),
                    "Carry, % of years won": st.column_config.NumberColumn(format="%.0f%%")})
            st.markdown(
                "**Risky arbitrage.** UIP says the higher-yielding currency should *depreciate* by the rate gap, "
                "so borrowing in the low-rate currency to hold the high-rate one should earn nothing on average. "
                "In practice it often earns something (the 'forward premium puzzle'), but it loses suddenly when "
                "stress spikes, because funding currencies like the yen and franc are safe havens. Compare the "
                "carry columns with *Corr with stress* above. A positive average here is a risk premium, "
                "not free money.")
            st.caption("Uses 10-year government yields as the interest rate (FRED's short-rate series for these "
                       "countries stopped updating). That ignores bond price changes and trading costs, so treat the "
                       "numbers as direction, not P&L. A real test belongs in QuantConnect with short rates and forwards.")

# =====================================================================
with tab_exp:
    labels = {sid: f"{m['name']} ({sid})" for sid, m in config.SERIES.items() if have(sid) and sid != "USREC"}
    e1, e2, e3 = st.columns([1.2, 2, 1])
    with e1:
        section = st.selectbox("Section", ["All"] + list(config.SECTIONS.values()), key="exp_section")
    pool = [s for s in labels if section == "All" or config.SECTIONS[config.SERIES[s]["section"]] == section]
    with e2:
        default = pool.index("BAMLH0A0HYM2") if "BAMLH0A0HYM2" in pool else 0
        sid = st.selectbox("Series", pool, index=default, format_func=lambda k: labels[k], key="exp_series")
    with e3:
        compare = st.selectbox("Compare with (shown below)", ["None"] + [k for k in labels if k != sid],
                               format_func=lambda k: "None" if k == "None" else labels[k], key="exp_compare")
    v1, v2 = st.columns([1, 2])
    with v1:
        views = list(signals.VIEW_LABELS)
        view = st.radio("Show as", views, index=views.index(config.SERIES[sid]["view"]),
                        format_func=lambda v: signals.VIEW_LABELS[v], key="exp_view", horizontal=False)
    lo, hi = p.index.min().date(), p.index.max().date()
    with v2:
        rng = st.slider("Date range", min_value=lo, max_value=hi, value=(lo, hi), format="MMM YYYY", key="exp_range")

    def explore_chart(sid, view, color):
        m = config.SERIES[sid]
        s = signals.transform(nat[sid], view).dropna()
        s = s[(s.index >= pd.Timestamp(rng[0])) & (s.index <= pd.Timestamp(rng[1]))]
        st.markdown(f"**{m['name']}** · {view_units(sid, view)} · {m['freq']}  \n{m['why']}  \n"
                    f":gray[Core idea: {config.IDEAS[m['idea']]}]")
        if s.empty:
            st.info("No data in this range.")
            return
        q1, q2, q3 = st.columns(3)
        q1.metric("Latest", fmt(s.iloc[-1]), f"{s.iloc[-1] - s.iloc[0]:+,.2f} over range", delta_color="off")
        yrs = signals.history_years(nat[sid])
        if yrs >= config.MIN_HISTORY_YEARS:
            q2.metric("Percentile of full history", signals.ordinal(signals.percentile_rank(signals.transform(nat[sid], view))),
                      help=f"Ranked against {yrs:.0f} years of data")
        else:
            q2.metric("Percentile of full history", "n/a",
                      help=f"Only {yrs:.1f} years of history on FRED, too short to rank fairly")
        q3.metric("Range high / low", f"{fmt(s.max())} / {fmt(s.min())}")
        fig = go.Figure(go.Scatter(x=s.index, y=s.values, line=dict(color=color, width=2), name=m["name"],
                                   hovertemplate="%{x|%b %d, %Y}<br>%{y:,.2f}<extra></extra>"))
        for r0, r1 in RECS:
            if r1 >= pd.Timestamp(rng[0]) and r0 <= pd.Timestamp(rng[1]):
                fig.add_vrect(x0=r0, x1=r1, fillcolor="gray", opacity=0.15, line_width=0, layer="below")
        if view in ("yoy", "diff"):
            fig.add_hline(y=0, line=dict(color="gray", width=1))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title=view_units(sid, view), showlegend=False)
        st.plotly_chart(fig, width="stretch", key=f"explore_{sid}_{color}")
        return s

    s1 = explore_chart(sid, view, C1)
    s2 = None
    if compare != "None":
        st.divider()
        s2 = explore_chart(compare, config.SERIES[compare]["view"], C2)
        st.caption("Shown as two charts on purpose: two different units on one chart with two axes is easy to misread.")

    with st.expander("See the data / download it"):
        parts = {labels[sid]: s1} if s1 is not None else {}
        if s2 is not None:
            parts[labels[compare]] = s2
        if parts:
            tbl = pd.concat(parts, axis=1, sort=True)
            st.dataframe(tbl.iloc[::-1], width="stretch", height=300)
            st.download_button("Download as CSV", tbl.to_csv().encode(), file_name=f"{sid}.csv", mime="text/csv")
