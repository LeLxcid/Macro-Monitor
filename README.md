# Macro Monitor

A small Python project that pulls the "What to watch in markets" series from FRED,
turns them into stress gauges and yes/no warning flags, and shows them in an
interactive dashboard. It's built from the Money & Credit study guide: every series
is tagged with the core idea (and reading) it helps you watch.

## What's in the folder

| File | What it does |
| --- | --- |
| `config.py` | The list of FRED series, grouped by the study guide's five core ideas. Add series here. |
| `fetch.py` | Downloads the series from FRED into `data/raw.parquet`. Re-running only pulls what's new. |
| `signals.py` | Turns raw data into stress gauges (z-scores), a stress index and regime flags. |
| `app.py` | The Streamlit dashboard. |
| `extras/daily-fetch.yml` | Optional: a GitHub job that runs `fetch.py` every weekday morning (step 6). To use it, move it to `.github/workflows/daily-fetch.yml` before pushing to GitHub. |

## What the dashboard shows

| Tab | What's on it |
| --- | --- |
| Overview | Every warning flag (economy, Fed & banks, markets) plus your leading index and stress index |
| Economy | GDP, CPI, unemployment, payrolls; leading, coincident and lagging indicators; the Sahm rule |
| Fed & banks | The Fed's policy tools (target range, interest on reserves, discount rate, open market operations, reserve and margin requirements), its balance sheet, discount window borrowing, reserves, deposits, bank lending |
| Markets & stress | Stress gauges, Treasury yields, the yield curve, credit spreads, the repo squeeze, VIX |
| Explore | Any series, shown as a level, % change vs. a year ago, or change from the last reading |

Gray bands on every chart are recessions. The sidebar slider sets how many years the charts show.

## First run (about 10 minutes)

Open Terminal, then:

```bash
cd ~/Documents/macro-monitor
python3 -m venv .venv            # make a private Python environment for this project
source .venv/bin/activate        # turn it on (do this each time you open a new Terminal)
pip install -r requirements.txt  # install the libraries
```

**Try it right away with sample data** (no key needed; the app shows a DEMO banner):

```bash
python fetch.py --demo
streamlit run app.py
```

Your browser opens the dashboard. Press `Ctrl+C` in Terminal to stop it.

**Switch to real data:**

1. Get a free API key: fred.stlouisfed.org → sign in → My Account → API Keys.
2. In the project folder, copy `.env.example` to a new file named `.env` and paste your key in.
   (In Terminal: `cp .env.example .env`, then `open -e .env` to edit it.)
3. Run `python fetch.py`. It prints each series as it downloads.
4. Run `streamlit run app.py`.

From then on, the app refreshes from FRED by itself the first time you open it each day.
You can also click **Update data now** in the sidebar.

Run `python signals.py` to print today's readings in Terminal without opening the app.

## Things to know

- **Some indicators use stand-ins.** The Conference Board's Consumer Confidence Index and Leading Economic
  Index, and the ISM manufacturing survey, are paid data and not on FRED. The app uses the University of
  Michigan sentiment index, Census factory orders, and builds its own leading index from the same kinds of inputs.
- **The S&P 500 on FRED only goes back 10 years** (a licensing limit). For longer history, use QuantConnect.
- **Discount rate, reserve requirements and margin requirements aren't downloaded.** The discount rate has
  been set at the top of the fed funds range since March 2020, so it's read from that. Reserve requirements
  (0% since 2020) and the Reg T margin requirement (50% since 1974) are fixed rules, kept in `config.py`
  under `STATIC_POLICY`.

- **A series can fail to download.** FRED occasionally renames or retires a series. `fetch.py` skips it
  and tells you which one, and the rest still update. Search for a replacement on FRED and update `config.py`.
- **Short histories.** SOFR starts in 2018 and IORB in 2021, so the funding-squeeze gauge has less
  history than the others. If the high-yield spread series comes back with only a few years of data,
  that's a licensing limit on FRED's side, not a bug.
- **Look-ahead bias.** The stress gauges compare each day only to the history before it, so they're safe
  to backtest. Weekly and quarterly data are treated as known on their observation date, though they're
  published later. Add a lag before trusting a backtest, and use ALFRED (FRED's archive of
  first-release values) for series that get revised.
- **Your key stays private.** `.env` is listed in `.gitignore`, so it never goes to GitHub.

## Ideas for what to build next

1. Add a gauge: `signals.py` → `gauges()`. Each gauge is one line, oriented so higher = more stress.
2. Check your stress index against NFCI on the dashboard. If they disagree, find out why.
3. Put it online (step 6): push to GitHub, deploy on share.streamlit.io, and add `FRED_API_KEY`
   under the app's Settings → Secrets.
4. Take a flag to QuantConnect (step 7): "When the stress index crosses +1, what do SPY and KBE do over
   the next 3 and 6 months?"
