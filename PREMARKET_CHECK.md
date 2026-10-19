# Premarket minute-data validation

This replaces the old **workflow** that ran a daily-bar premarket scan.
It does not enable a recurring schedule before the data source is tested.
The old `scan.py --mode premarket` implementation remains legacy code;
the new workflow runs `premarket_check.py` instead. Do not run the legacy
command as a premarket signal. Evening daily-bar scanning remains unvalidated.

## First run

Actions -> **PreMarket - Minute Data Check (manual test)** -> Run workflow
-> main -> scope=watchlist. Existing Google Secrets are reused.
For a small connectivity check choose scope=probe (AAPL, MSFT, SPY).
Probe mode does not generate research flags.

Run during New York 04:00-09:30 on an exchange trading day. Prefer 08:55 ET,
which is 20:55 Hong Kong in US summer time and 21:55 in winter time.
No trading-session assumptions are based on Hong Kong's day of week.
NYSE calendar supplies holidays, prior sessions, and the regular opening
instant; America/New_York handles DST. No schedule has been activated yet.

## Sources and settings

- The watchlist is the UNION of previous-session Minervini Pass All=TRUE
  stocks and previous-session EOD movers (up and down), plus SPY/QQQ controls.
  It is a recheck of saved names, not a new broad-market discovery scan.
- A previous-session Minervini dataset is required. EOD can have zero
  matching names. Historical, stale watchlists are not silently substituted.
- Download: Yahoo through yfinance, period=1d, interval=1m, prepost=True,
  ignore_tz=False. Baseline: exact previous trading day's unadjusted daily
  Close, not an adjusted EOD/Minervini price and not a prior after-hours price.
- Only today's 04:00-regular-open minutes are used. Future and unfinished
  bars are excluded. Previous days, prior postmarket, and regular-session
  bars are never relabelled as today's premarket data.
- The last positive-volume minute provides the displayed bar price.
  This is NOT a live bid, ask, last-trade feed, or certified real-time quote.
- Age uses the minute bar START timestamp. A completed minute is at least
  one minute old. A bar age over 10 minutes is STALE; zero-volume bars
  cannot refresh its age.
- Premarket Bar Volume sums received completed 1m volumes. It is provider
  bar volume, not guaranteed consolidated market volume. Missing or invalid
  volumes block flags; zero-volume-only data is not considered a fresh trade.
- Review Flag=TRUE only for a recent bar, complete returned volume values,
  |gap| >= 2%, and received premarket volume >= 50,000. These provisional
  research thresholds have not been backtested and are not buy/sell signals.
- If the provider returns a same-day split action, flags are blocked.
  Missing action fields do not prove no split. Always verify corporate
  actions and broker quotes independently. No automatic trading occurs.

## Reading the sheet

`PreMarket_Check` is a CURRENT snapshot, overwritten only in that dedicated
tab on each successful check, including when outside premarket. Old surplus
rows are blanked in the same update. EOD and Minervini history is not edited.
Verify Session Date, Scanned HKT, Last Trade Minute ET, Data Age Minutes,
and Status every time you view it: it does not refresh after the job ends.

- RECENT_BAR: a returned positive-volume completed minute within 10 minutes.
  Does not certify exchange freshness or completeness.
- STALE: last eligible minute older than 10 minutes; flag is FALSE.
- NO_TODAY_PREMARKET_BARS: no qualifying current-session minutes.
- NO_POSITIVE_VOLUME_BARS / VOLUME_UNAVAILABLE / VOLUME_INCOMPLETE: no usable
  complete volume evidence; flag is FALSE.
- PREVIOUS_CLOSE_MISSING / TIMEZONE_UNKNOWN / SPLIT_REQUIRES_REVIEW: invalid
  comparison basis or corporate-action uncertainty.
- OUTSIDE_PREMARKET / NO_TRADING_SESSION / SESSION_ENDED_DURING_SCAN:
  no actionable premarket snapshot for that run.
- WATCHLIST_NOT_READY: source watchlist needs the previous-session scan.

The job may be green even if all rows are stale/missing/outside-session:
green confirms diagnostic snapshot writing only. Log status counts and
broker comparison are required before claiming data validation succeeded.
Download/Sheets errors fail the workflow; in that event an older snapshot
can remain, so always check the Scanned HKT timestamp.

Do not treat Review Flag=FALSE as a bearish signal or proof of no movement.
No news retrieval, spread/liquidity check, VCP detection, entry, stop loss,
short-sale availability check, or order placement is included.

## API references

- https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html
- https://pandas-market-calendars.readthedocs.io/en/latest/usage.html
