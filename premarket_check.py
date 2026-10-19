"""Minute-bar premarket diagnostics. No orders, no live-quote guarantee."""
import argparse
import json
import math
import os
from collections import Counter
from datetime import timedelta

import pandas as pd

ET = "America/New_York"
TAB = "PreMarket_Check"
MAX_AGE_MINUTES = 10
MIN_MOVE_PCT = 2
MIN_PM_VOLUME = 50_000
COLUMNS = [
    "Session Date", "Ticker", "Watchlist Source", "Watchlist Date", "Status",
    "Review Flag", "Previous Close", "Previous Close Date", "Premarket Bar Price",
    "Gap %", "Premarket Bar Volume", "Last Trade Minute ET", "Data Age Minutes",
    "Captured Minutes", "Volume Data Complete", "Scanned ET", "Scanned HKT",
    "Source", "Note",
]


def clock(value=None):
    stamp = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("An explicit timezone is required.")
    return stamp.tz_convert(ET)


def session_context(now, calendar=None):
    if calendar is None:
        import pandas_market_calendars as mcal
        calendar = mcal.get_calendar("NYSE")
    now = clock(now)
    date = now.date()
    schedule = calendar.schedule(start_date=date - timedelta(days=20), end_date=date)
    today = schedule[schedule.index.strftime("%Y-%m-%d") == str(date)]
    prior = schedule[schedule.index.strftime("%Y-%m-%d") < str(date)]
    if today.empty:
        return {"date": str(date), "state": "NO_TRADING_SESSION"}
    if prior.empty:
        raise ValueError("No previous trading session found.")
    opening = clock(today.iloc[0]["market_open"])
    start = pd.Timestamp(f"{date} 04:00", tz=ET)
    state = "PREMARKET" if start <= now < opening else "OUTSIDE_PREMARKET"
    return {"date": str(date), "previous": prior.index[-1].strftime("%Y-%m-%d"),
            "start": start, "open": opening, "state": state}


def sheet_records(values):
    if not values or not any(str(v or "").strip() for row in values for v in row):
        return []
    header = [str(v or "").strip() for v in values[0]]
    while header and not header[-1]:
        header.pop()
    if not header or len(header) != len(set(header)) or "" in header:
        raise ValueError("Invalid source worksheet header.")
    return [dict(zip(header, row)) for row in values[1:]
            if any(str(v or "").strip() for v in row)]


def date_text(value):
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def watchlist(minervini, eod, expected):
    eligible = [r for r in minervini if date_text(r.get("Date")) == expected]
    if not eligible:
        raise ValueError(f"Minervini has no records for expected trading date {expected}.")
    sources = {}
    for row in eligible:
        if str(row.get("Pass All", "")).strip().upper() == "TRUE":
            sources.setdefault(str(row.get("Ticker", "")).strip(), set()).add("Minervini")
    for row in eod:
        if date_text(row.get("Date")) == expected:
            sources.setdefault(str(row.get("Ticker", "")).strip(), set()).add("EOD")
    sources.pop("", None)
    for ticker in ("SPY", "QQQ"):
        sources.setdefault(ticker, set()).add("Market reference")
    if len(sources) > 250:
        raise ValueError("Watchlist exceeds 250 stocks; not silently truncating it.")
    return {ticker: " + ".join(sorted(tags)) for ticker, tags in sorted(sources.items())}


def ticker_frame(raw, ticker):
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(0):
            return pd.DataFrame()
        return raw[ticker].copy()
    return raw.copy()


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def make_row(ticker, source, context, now):
    now = clock(now)
    row = {name: "" for name in COLUMNS}
    row.update({"Session Date": context["date"], "Ticker": ticker,
                "Watchlist Source": source, "Watchlist Date": context.get("previous", ""),
                "Status": context["state"], "Review Flag": False,
                "Scanned ET": now.isoformat(timespec="seconds"),
                "Scanned HKT": now.tz_convert("Asia/Hong_Kong").isoformat(timespec="seconds"),
                "Source": "Yahoo via yfinance; completed 1m bars"})
    return row


def analyse(ticker, source, daily, intraday, context, now):
    now = clock(now)
    row = make_row(ticker, source, context, now)
    if context["state"] != "PREMARKET":
        row["Note"] = "No premarket price is substituted from another session."
        return row
    if now >= context["open"]:
        row["Status"] = "SESSION_ENDED_DURING_SCAN"
        return row
    baseline = None
    corporate_action = False
    if daily is not None and not daily.empty and "Close" in daily:
        dates = pd.DatetimeIndex(daily.index)
        if dates.tz is not None:
            dates = dates.tz_convert(ET)
        matches = daily.loc[dates.strftime("%Y-%m-%d") == context["previous"]]
        if not matches.empty:
            baseline = finite(matches["Close"].iloc[-1])
        action_rows = daily.loc[dates.strftime("%Y-%m-%d") == context["date"]]
        if "Stock Splits" in action_rows:
            corporate_action = bool(pd.to_numeric(action_rows["Stock Splits"], errors="coerce").fillna(0).ne(0).any())
    if baseline is None or baseline <= 0:
        row["Status"] = "PREVIOUS_CLOSE_MISSING"
        row["Note"] = "Exact previous-session unadjusted close unavailable."
        return row
    row["Previous Close"] = baseline
    row["Previous Close Date"] = context["previous"]
    if intraday is None or intraday.empty or "Close" not in intraday:
        row["Status"] = "NO_TODAY_PREMARKET_BARS"
        return row
    frame = intraday.copy()
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        row["Status"] = "TIMEZONE_UNKNOWN"
        return row
    frame.index = index.tz_convert(ET)
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    # Include only today's completed 04:00-open bars; never prior postmarket.
    frame = frame.loc[(frame.index >= context["start"]) &
                      (frame.index < context["open"]) &
                      (frame.index + pd.Timedelta(minutes=1) <= now)]
    closes = pd.to_numeric(frame["Close"], errors="coerce")
    frame = frame.loc[closes.gt(0) & closes.map(lambda x: finite(x) is not None)]
    if frame.empty:
        row["Status"] = "NO_TODAY_PREMARKET_BARS"
        return row
    row["Captured Minutes"] = len(frame)
    if "Volume" not in frame:
        row["Status"] = "VOLUME_UNAVAILABLE"
        return row
    volume = pd.to_numeric(frame["Volume"], errors="coerce")
    valid_volume = volume.notna() & volume.ge(0) & volume.map(lambda x: finite(x) is not None)
    complete = bool(valid_volume.all())
    row["Volume Data Complete"] = complete
    if complete:
        row["Premarket Bar Volume"] = int(volume.sum())
    traded = frame.loc[valid_volume & volume.gt(0)]
    if traded.empty:
        row["Status"] = "NO_POSITIVE_VOLUME_BARS"
        row["Note"] = "Zero-volume bars cannot establish a recent trade."
        return row
    last_stamp = traded.index[-1]
    age = (now - last_stamp).total_seconds() / 60
    price = finite(traded["Close"].iloc[-1])
    row.update({"Premarket Bar Price": price, "Gap %": round((price / baseline - 1) * 100, 3),
                "Last Trade Minute ET": last_stamp.isoformat(),
                "Data Age Minutes": round(age, 2)})
    if corporate_action:
        row["Status"] = "SPLIT_REQUIRES_REVIEW"
    elif not complete:
        row["Status"] = "VOLUME_INCOMPLETE"
    elif age > MAX_AGE_MINUTES:
        row["Status"] = "STALE"
    else:
        row["Status"] = "RECENT_BAR"
        row["Review Flag"] = bool(abs(row["Gap %"]) >= MIN_MOVE_PCT and
                                   row["Premarket Bar Volume"] >= MIN_PM_VOLUME and
                                   "Market reference" != source)
    row["Note"] = "Research flag only; minute-bar coverage/delay is not certified. Verify broker quote and corporate actions."
    return row


def save_snapshot(book, rows):
    import gspread
    try:
        ws = book.worksheet(TAB)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=TAB, rows=1000, cols=len(COLUMNS))
    old = ws.get_all_values()
    nonblank = any(str(v or "").strip() for r in old for v in r)
    if nonblank:
        header = [str(v or "").strip() for v in old[0]]
        while header and not header[-1]:
            header.pop()
        if header != COLUMNS:
            raise ValueError("PreMarket_Check header differs; refusing to overwrite.")
    values = [COLUMNS] + [[r.get(name, "") for name in COLUMNS] for r in rows]
    # Clear old snapshot's surplus rows in the same update, retaining formatting.
    values += [[""] * len(COLUMNS) for _ in range(max(0, len(old) - len(values)))]
    if len(values) > ws.row_count:
        ws.resize(rows=len(values), cols=max(len(COLUMNS), ws.col_count))
    ws.update(range_name="A1", values=values, value_input_option="RAW")
    ws.freeze(rows=1)
    print(f"Saved {len(rows)} diagnostic rows to {TAB}.")


def run(book, now=None, scope="watchlist", download=None, calendar=None, now_fn=None):
    import gspread
    if download is None:
        import yfinance as yf
        download = yf.download
    now = clock(now)
    context = session_context(now, calendar)
    if context["state"] != "PREMARKET":
        row = make_row("", "", context, now)
        row["Note"] = "Run on a trading day between 04:00 and the regular open, New York time."
        save_snapshot(book, [row])
        print(context["state"])
        return
    if scope == "probe":
        sources = {t: "Data probe" for t in ["AAPL", "MSFT", "SPY"]}
    else:
        try:
            mini = sheet_records(book.worksheet("Minervini").get_all_values())
            try:
                eod = sheet_records(book.worksheet("EOD").get_all_values())
            except gspread.WorksheetNotFound:
                eod = []
            sources = watchlist(mini, eod, context["previous"])
        except (ValueError, gspread.WorksheetNotFound) as exc:
            row = make_row("", "", context, now)
            row.update({"Status": "WATCHLIST_NOT_READY", "Note": str(exc)})
            save_snapshot(book, [row])
            raise RuntimeError("Previous-session watchlist is missing or invalid.") from exc
    print(f"Premarket date={context['date']}; previous session={context['previous']}; symbols={len(sources)}")
    snapshots = {}
    tickers = list(sources)
    for offset in range(0, len(tickers), 20):
        batch = tickers[offset:offset+20]
        kwargs = dict(tickers=batch, group_by="ticker", auto_adjust=False,
                      progress=False, threads=4, timeout=15)
        daily = download(period="1mo", interval="1d", actions=True, **kwargs)
        minutes = download(period="1d", interval="1m", prepost=True, ignore_tz=False, **kwargs)
        for ticker in batch:
            snapshots[ticker] = (ticker_frame(daily, ticker), ticker_frame(minutes, ticker))
        print(f"Fetched {min(offset+len(batch),len(tickers))}/{len(tickers)}")
    finished = clock() if now_fn is None else clock(now_fn())
    rows = [analyse(t, sources[t], *snapshots[t], context, finished) for t in tickers]
    # Probe output never produces a review flag.
    if scope == "probe":
        for row in rows:
            row["Review Flag"] = False
            row["Note"] = "Data probe only. " + row["Note"]
    rows.sort(key=lambda r: (not r["Review Flag"], r["Status"], r["Ticker"]))
    save_snapshot(book, rows)
    print("Statuses:", dict(Counter(r["Status"] for r in rows)))
    print("A successful workflow means a diagnostic snapshot was saved, not that quotes are fresh.")


def main():
    import gspread
    from google.oauth2.service_account import Credentials
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["probe", "watchlist"], default="watchlist")
    args = parser.parse_args()
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    book = gspread.authorize(creds).open_by_key(os.environ["SPREADSHEET_ID"].strip())
    run(book, scope=args.scope)


if __name__ == "__main__":
    main()
