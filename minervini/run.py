"""Headless adapter for the upstream Minervini screener; see README.md."""
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .vendor_indicators import TrendTemplate, relative_strength
from .universe import get_ticker_list

PERIOD = "2y"
MIN_PRICE = 5
MIN_VOLUME = 500_000
MIN_RS = 70
BENCHMARK = "^GSPC"
TAB = "Minervini"
CONFIG = "sp500-2y-price5-vol500k-rs70-upstream-v1"
COLUMNS = [
    "Date", "Ticker", "Pass All", "Conditions Passed", "Price",
    "Custom RS Score", "RSI (14)", "MACD Hist", "ADX (14)", "Stoch %K",
    "BB %B", "ATR (14)", "Vol ROC %", "Avg Vol (20d)",
    "SMA 50", "SMA 150", "SMA 200", "EMA 21",
    "52w Close Low", "52w Close High", "% From 52w Low", "% From 52w High",
    "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8",
    "Benchmark", "Lookback", "Config", "Scanned UTC",
]


def clean_frame(df):
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    needed = ["Open", "High", "Low", "Close", "Volume"]
    df = df[needed].apply(pd.to_numeric, errors="coerce")
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    df = df[(df["Close"] > 0) & (df["Volume"] >= 0)]
    return df.sort_index()


def bar_date(df):
    stamp = pd.Timestamp(df.index[-1])
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("America/New_York")
    return stamp.strftime("%Y-%m-%d")


def screen_frame(ticker, df, benchmark_rs, market_date, scanned):
    df = clean_frame(df)
    # 260 bars to match upstream's 52-week close-based window.
    if len(df) < 260 or bar_date(df) != market_date:
        return None
    if df["Close"].iloc[-1] < MIN_PRICE:
        return None
    if df["Volume"].tail(20).mean() < MIN_VOLUME:
        return None
    template = TrendTemplate(ticker, df, benchmark_rs, MIN_RS)
    if not template._valid:
        return None
    raw = template.summary_dict()
    details = list(template.condition_details().values())
    row = {name: raw.get(name, "") for name in COLUMNS}
    row.update({
        "Date": market_date,
        "Ticker": ticker,
        "Pass All": bool(template.passes_all()),
        "Custom RS Score": raw["RS Rating"],
        "52w Close Low": raw["52w Low"],
        "52w Close High": raw["52w High"],
        "Benchmark": BENCHMARK,
        "Lookback": PERIOD,
        "Config": CONFIG,
        "Scanned UTC": scanned,
    })
    row.update({f"C{i}": bool(value) for i, value in enumerate(details, 1)})
    return row


def normalize_date(value):
    stamp = pd.to_datetime(value, errors="coerce")
    return str(value) if pd.isna(stamp) else stamp.strftime("%Y-%m-%d")


def cell_value(value):
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return ""
    return value


def write_results(client, spreadsheet_id, rows):
    import gspread
    book = client.open_by_key(spreadsheet_id)
    try:
        ws = book.worksheet(TAB)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=TAB, rows=5000, cols=len(COLUMNS))
    existing = ws.get_all_values()
    if existing and existing[0] != COLUMNS:
        raise ValueError("Minervini header differs; refusing to overwrite data.")
    if not existing:
        ws.update(range_name="A1", values=[COLUMNS])
        ws.freeze(rows=1)

    key_columns = [COLUMNS.index(name) for name in ("Date", "Ticker", "Config")]
    seen = set()
    for old in existing[1:]:
        if len(old) > max(key_columns):
            seen.add((normalize_date(old[key_columns[0]]), old[key_columns[1]], old[key_columns[2]]))
    fresh = []
    for row in rows:
        key = (row["Date"], row["Ticker"], row["Config"])
        if key in seen:
            continue
        seen.add(key)
        fresh.append([cell_value(row.get(name, "")) for name in COLUMNS])
    if fresh:
        ws.append_rows(fresh, value_input_option="RAW", table_range="A1")
    print(f"Minervini: appended {len(fresh)}; skipped {len(rows) - len(fresh)} duplicates.")
    return len(fresh)


def main():
    import gspread
    import yfinance as yf
    from google.oauth2.service_account import Credentials

    spreadsheet_id = os.environ.get("SPREADSHEET_ID", "").strip()
    credentials_json = os.environ.get("GOOGLE_CREDENTIALS", "")
    if not spreadsheet_id or not credentials_json:
        raise ValueError("Set GOOGLE_CREDENTIALS and SPREADSHEET_ID in repository Secrets.")

    benchmark = clean_frame(yf.download(
        BENCHMARK, period=PERIOD, auto_adjust=True, progress=False,
    ))
    if len(benchmark) < 260:
        raise RuntimeError("Insufficient benchmark history; no results written.")
    market_date = bar_date(benchmark)
    benchmark_rs = relative_strength(benchmark["Close"])
    if not np.isfinite(benchmark_rs) or benchmark_rs <= 0:
        raise RuntimeError("Invalid benchmark score; no results written.")

    tickers = sorted(set(get_ticker_list("S&P 500")))
    if len(tickers) < 400:
        raise RuntimeError("Ticker universe unexpectedly small; no results written.")
    scanned = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    current_count = 0
    errors = 0
    for offset in range(0, len(tickers), 50):
        batch = tickers[offset:offset + 50]
        raw = yf.download(
            batch, period=PERIOD, group_by="ticker", auto_adjust=True,
            threads=4, progress=False,
        )
        for ticker in batch:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    frame = raw[ticker]
                elif len(batch) == 1:
                    frame = raw
                else:
                    raise ValueError("Unexpected download columns")
                frame = clean_frame(frame)
                if not frame.empty and bar_date(frame) == market_date:
                    current_count += 1
                result = screen_frame(ticker, frame, benchmark_rs, market_date, scanned)
                if result is not None:
                    rows.append(result)
            except Exception as exc:
                errors += 1
                print(f"Skipped {ticker}: {type(exc).__name__}")
        print(f"Processed {min(offset + len(batch), len(tickers))}/{len(tickers)} tickers")

    coverage = current_count / len(tickers)
    print(f"Data date={market_date}; current bars={current_count}/{len(tickers)}; errors={errors}")
    if coverage < 0.8 or not rows:
        raise RuntimeError("Incomplete market data; no results written. Check download logs.")
    rows.sort(key=lambda row: (not row["Pass All"], -row["Custom RS Score"], row["Ticker"]))
    passed = sum(row["Pass All"] for row in rows)
    print(f"Eligible stocks={len(rows)}; passed all 8={passed}; source=Yahoo; custom RS is not IBD RS.")
    creds = Credentials.from_service_account_info(
        json.loads(credentials_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    write_results(gspread.authorize(creds), spreadsheet_id, rows)


if __name__ == "__main__":
    main()
