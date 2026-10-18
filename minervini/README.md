# Minervini results in the existing Google spreadsheet

This adapter runs the technical screening logic from
https://github.com/icedevil2001/mark_minervini_stock_screener without its
Streamlit UI. The upstream author is icedevil2001. See UPSTREAM_LICENSE.
`vendor_indicators.py` is copied unchanged from upstream blob
`ea7d9125b7fc4fca87f33efc95b324bd4e64ed11`; ticker retrieval and fallback
lists in `universe.py` are extracted from stock_screener.py blob
`2e8ac5a502becd49ca5b68ac66e278f3402eb985`, with UI decorators removed.

## Run

In Actions choose **Minervini - Daily Screener (05:40 HKT)**, then
**Run workflow**, branch main. The schedule is Tuesday-Saturday 05:40 Hong
Kong time, ten minutes after the current EOD schedule. Scheduled starts may
be delayed. Uses existing GOOGLE_CREDENTIALS and SPREADSHEET_ID Secrets.
No paid market-data API or new hosting service is required. Public-repo
standard GitHub runners are intended; do not switch to paid larger runners.

Results append only to `Minervini`. EOD data and workflows are untouched.
The service account needs Editor access to the existing spreadsheet.
First execution must be verified in Actions and then in the spreadsheet.

## Settings and reading results

- Universe: S&P 500 fetched with upstream fallbacks. Static fallback lists
  are not guaranteed current; failed/delisted symbols are logged/skipped.
- Two years of adjusted daily OHLCV; at least 260 complete bars required.
  The original UI defaults to one year. This adapter deliberately uses two
  years for complete history; upstream custom RS uses the entire downloaded
  history, so its scores can differ from a one-year UI run.
- Minimum price USD 5; 20-day average volume 500,000; custom RS threshold 70.
- All eligible analysed stocks are saved, including failures. Filter Date
  to the latest trading date, then Pass All=TRUE for stocks passing all eight.
- TRUE is a screening result, not a buy signal. No VCP detector, fundamental
  analysis, execution, backtest, or profitability claim is included.
- Date uses the benchmark's latest bar date. Stocks with a different latest
  bar date are skipped. Fewer than 80% current universe bars fails the run
  before writing; passing this check does not establish price accuracy.
- Same Date + Ticker + Config is appended once. A rerun preserves the first
  saved values rather than refreshing them. Existing EOD duplicates are not
  changed. Configuration changes use a new Config value.

## Formula caveats (preserved from upstream)

- Custom RS Score: mean nonnegative daily return / absolute mean negative
  daily return; divide by the benchmark's corresponding ratio and multiply
  by 100, capped at 200. NOT an IBD score or a cross-sectional percentile.
- C1: price > SMA150 and SMA200; C2: SMA150 > SMA200.
- C3: current SMA200 > upstream iloc[-20] SMA200. This is an endpoint
  comparison, not proof of a continuously rising month.
- C4: SMA50 > SMA150 > SMA200; C5: price > SMA50.
- C6: price >= 1.3 x 52w Close Low; C7: price >= .75 x 52w Close High.
  These use the last 260 CLOSES, not intraday high/low extremes.
- C8: custom RS >= 70.
- RSI, ADX and other auxiliary formulas are retained, not independently
  certified. In particular upstream RSI fills zero-loss/undefined cases
  with 50, so do not assume it matches a broker's RSI at edge cases.
- Yahoo/yfinance data may be missing or inaccurate. Compare prices and
  corporate actions with an independent source before relying on results.

This integration writes result tables only. It does not deploy the
upstream charts, web UI, or CSV download interface.
