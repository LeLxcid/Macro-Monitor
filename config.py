"""
config.py - the list of data series the monitor tracks.

Every series comes from FRED (fred.stlouisfed.org). Each one is tagged with:
  section : which dashboard tab it lives on (economy / fed / markets)
  idea    : the core idea from the Money & Credit study guide it helps you watch
  cycle   : for economic data, whether it's a leading, coincident or lagging indicator
  view    : how it's shown by default - "level" (as is), "yoy" (% change vs. a year ago)
            or "diff" (change from the previous reading, e.g. jobs added per month)
  invert  : True if a HIGHER value is WORSE for the economy (e.g. jobless claims)

To add a series: find its ID on FRED (it's in the URL, e.g.
https://fred.stlouisfed.org/series/DGS10 -> "DGS10") and add an entry below.

Left out on purpose because they would be redundant:
  - T10Y2Y (10y-2y spread): computed from DGS10 - DGS2 instead.
  - DPCREDIT (discount rate): since March 2020 the Fed has set it equal to the top of the
    fed funds target range (DFEDTARU), so the dashboard shows it from that. If the Fed ever
    changes that rule, add DPCREDIT back here.
"""

IDEAS = {
    "leverage": "Balance sheets & leverage",
    "runs": "Maturity mismatch & runs",
    "hierarchy": "Hierarchy of money (funding stress)",
    "credit": "Securitization, shadow banking & credit conditions",
    "policy": "Policy & the price of credit",
    "cycle": "The business cycle",
}

SECTIONS = {"economy": "Economy", "fed": "Fed & banks", "markets": "Markets & stress"}
CYCLES = {"big": "Big picture", "leading": "Leading", "coincident": "Coincident", "lagging": "Lagging"}


def S(name, units, freq, section, idea, why, cycle=None, view="level", invert=False):
    return dict(name=name, units=units, freq=freq, section=section, idea=idea, why=why,
                cycle=cycle, view=view, invert=invert)


SERIES = {
    # ===================== ECONOMY =====================
    "GDPC1": S("Real GDP", "$ billions (inflation-adjusted)", "quarterly", "economy", "cycle",
               "Total output of the economy. Shown as % growth vs. a year ago.", cycle="big", view="yoy"),
    # Leading: these tend to turn BEFORE the economy does.
    "SP500": S("S&P 500", "index", "daily", "economy", "cycle",
               "Stock prices discount future profits. FRED only provides the last 10 years.", cycle="leading"),
    "ICSA": S("Initial jobless claims", "people per week", "weekly", "economy", "cycle",
              "New unemployment filings. Rising claims are an early sign of layoffs.", cycle="leading", invert=True),
    "PERMIT": S("Building permits", "thousands of units (annual rate)", "monthly", "economy", "cycle",
                "Housing starts with a permit; housing is very rate-sensitive (The Bubble, BoE).", cycle="leading"),
    "UMCSENT": S("Consumer sentiment (U. of Michigan)", "index", "monthly", "economy", "cycle",
                 "Free stand-in for the Conference Board's consumer confidence index, which isn't on FRED. "
                 "FRED shows it with a 1-month delay.", cycle="leading"),
    "NEWORDER": S("Core capital goods orders", "$ millions", "monthly", "economy", "cycle",
                  "Business equipment orders excluding defense and aircraft: companies' investment plans "
                  "(Chancellor's buybacks vs. investment).", cycle="leading", view="yoy"),
    "ACOGNO": S("Consumer goods orders", "$ millions", "monthly", "economy", "cycle",
                "Factory orders for consumer goods. A component of the Conference Board's leading index.",
                cycle="leading", view="yoy"),
    # Coincident: these move WITH the economy.
    "PAYEMS": S("Nonfarm payrolls", "thousands of jobs", "monthly", "economy", "cycle",
                "Jobs added each month. Shown as the monthly change.", cycle="coincident", view="diff"),
    "UNRATE": S("Unemployment rate", "%", "monthly", "economy", "cycle",
                "Share of the labor force without a job. Used for the Sahm-rule recession flag.",
                cycle="coincident", invert=True),
    # Lagging: these turn AFTER the economy does.
    "CPIAUCSL": S("Consumer prices (CPI)", "index", "monthly", "economy", "policy",
                  "Inflation, shown as % change vs. a year ago. Lagging: it peaks after the economy does.",
                  cycle="lagging", view="yoy"),
    "CP": S("Corporate profits after tax", "$ billions (annual rate)", "quarterly", "economy", "leverage",
            "What buybacks and dividends are paid from (Economist, Chancellor).", cycle="lagging", view="yoy"),
    "TCMDO": S("Total debt, all sectors", "$ millions", "quarterly", "economy", "leverage",
               "From the Fed's Z.1 accounts: how much the whole economy owes (Balance Sheet Primer).", view="yoy"),
    "USREC": S("Recession indicator (NBER)", "1 = recession", "monthly", "economy", "cycle",
               "Used to shade recessions on the charts."),

    # ===================== FED & BANKS =====================
    # Policy rates
    "DFEDTARU": S("Fed funds target, upper bound", "%", "daily", "fed", "policy",
                  "The policy rate (BoE transmission paper). The discount rate is set equal to this."),
    "DFEDTARL": S("Fed funds target, lower bound", "%", "daily", "fed", "policy",
                  "Bottom of the target range."),
    "IORB": S("Interest on reserve balances", "%", "daily", "fed", "hierarchy",
              "What the Fed pays banks on reserves; the main tool that keeps rates in the range."),
    # Open market operations
    "TREAST": S("Fed Treasury holdings", "$ millions", "weekly", "fed", "policy",
                "Permanent open market operations: Treasuries bought (QE) or run off (QT)."),
    "WSHOMCB": S("Fed mortgage-backed securities holdings", "$ millions", "weekly", "fed", "credit",
                 "Permanent open market operations in agency MBS (Securitization readings)."),
    "WALCL": S("Fed total assets", "$ millions", "weekly", "fed", "policy",
               "The whole Fed balance sheet (Kelton's 'computer')."),
    "RPONTSYD": S("Fed repo operations (lending cash)", "$ billions", "daily", "fed", "hierarchy",
                  "Temporary open market operations: the Fed lends cash against Treasuries. "
                  "Usage spikes when private repo markets are short of cash."),
    "RRPONTSYD": S("Fed reverse repo (absorbing cash)", "$ billions", "daily", "fed", "hierarchy",
                   "Cash money funds park at the Fed. Near zero = the liquidity cushion is gone."),
    # Discount window & reserves
    "WLCFLPCL": S("Discount window borrowing", "$ millions", "weekly", "fed", "runs",
                  "Banks borrowing from the Fed as lender of last resort. Spikes in crises (2008, 2020, March 2023)."),
    "WRESBAL": S("Bank reserves at the Fed", "$ millions", "weekly", "fed", "hierarchy",
                 "The top layer of the hierarchy that banks settle in (Mehrling)."),
    # Banks
    "DPSACBW027SBOG": S("Deposits at US commercial banks", "$ billions", "weekly", "fed", "runs",
                        "From the Fed's H.8. Falling deposits = the S&L / 2023 regional-bank pattern.", view="yoy"),
    "TOTBKCR": S("Bank credit at US commercial banks", "$ billions", "weekly", "fed", "runs",
                 "Loans plus securities banks hold: how much credit banks are creating.", view="yoy"),
    "DRTSCILM": S("Banks tightening business-loan standards", "net %", "quarterly", "fed", "credit",
                  "Fed loan officer survey (SLOOS). Positive = banks are pulling back credit."),

    # ===================== MARKETS & STRESS =====================
    "SOFR": S("Secured Overnight Financing Rate", "%", "daily", "markets", "hierarchy",
              "The overnight repo rate. Above IORB = cash is scarce (Shadow Banks)."),
    "DGS2": S("2-year Treasury yield", "%", "daily", "markets", "policy",
              "Markets' expected path of the policy rate over ~2 years."),
    "DGS10": S("10-year Treasury yield", "%", "daily", "markets", "policy",
               "The long rate: expected short rates plus a term premium."),
    "MORTGAGE30US": S("30-year fixed mortgage rate", "%", "weekly", "markets", "credit",
                      "Freddie Mac's weekly survey. Roughly the 10-year Treasury yield plus a spread; "
                      "the spread reflects MBS demand, prepayment risk and the Fed's MBS holdings "
                      "(Securitization readings, The Bubble)."),
    "BAMLH0A0HYM2": S("High-yield corporate bond spread", "% points", "daily", "markets", "credit",
                      "Extra yield on junk bonds. Blows out when credit stops being elastic."),
    "BAMLC0A0CM": S("Investment-grade corporate bond spread", "% points", "daily", "markets", "credit",
                    "Same idea for safer companies, including heavy buyback borrowers."),
    "VIXCLS": S("VIX (stock market volatility)", "index", "daily", "markets", "credit",
                "The market's fear gauge. Rises with fire-sale dynamics."),
    "NFCI": S("Chicago Fed financial conditions index", "index", "weekly", "markets", "credit",
              "An outside benchmark for your stress index. Above 0 = tighter than average."),
}

# Rules the Fed sets that don't change often enough to need a data feed.
# Check federalreserve.gov if you're reading this long after October 2026.
STATIC_POLICY = {
    "Reserve requirements": ("0%", "Cut to zero on March 26, 2020. Banks hold reserves because the Fed pays "
                             "interest on them (IORB), not because they're required to."),
    "Margin requirement (Reg T)": ("50%", "Minimum down payment to buy stocks on credit, unchanged since 1974. "
                                   "What actually moves in a crisis is repo haircuts (the margin spiral in "
                                   "Shadow Banks) and how much margin debt investors carry."),
}

# How far back to pull history. 2000 gives the z-scores plenty of "normal" history
# before the 2008 crisis. (Some series, like SOFR, start much later; that's fine.)
START_DATE = "2000-01-01"

DATA_DIR = "data"
RAW_FILE = "data/raw.parquet"
META_FILE = "data/meta.json"
