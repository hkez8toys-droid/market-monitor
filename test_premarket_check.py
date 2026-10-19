"""Offline regression tests: python -m unittest test_premarket_check -v."""
import sys
import types
import unittest
from unittest.mock import patch

import pandas as pd

import premarket_check as p


class MissingWorksheet(Exception):
    pass


class MemoryWorksheet:
    row_count = 1000
    col_count = len(p.COLUMNS)

    def __init__(self, values):
        self.values = values
        self.updates = 0

    def get_all_values(self):
        return self.values

    def update(self, range_name, values, value_input_option):
        assert range_name == "A1" and value_input_option == "RAW"
        self.values = values
        self.updates += 1

    def freeze(self, rows):
        assert rows == 1


class MemoryBook:
    def __init__(self, values):
        self.ws = MemoryWorksheet(values)

    def worksheet(self, name):
        assert name == "PreMarket_Check"
        return self.ws


class CalendarFixture:
    def __init__(self, dates):
        self.dates = dates

    def schedule(self, start_date, end_date):
        index = pd.DatetimeIndex(self.dates)
        return pd.DataFrame({"market_open": [
            pd.Timestamp(f"{d} 09:30", tz=p.ET).tz_convert("UTC") for d in self.dates
        ]}, index=index)


class PremarketTests(unittest.TestCase):
    def setUp(self):
        self.now = pd.Timestamp("2026-10-19 08:55", tz=p.ET)
        self.context = {"date": "2026-10-19", "previous": "2026-10-16",
                        "start": pd.Timestamp("2026-10-19 04:00", tz=p.ET),
                        "open": pd.Timestamp("2026-10-19 09:30", tz=p.ET),
                        "state": "PREMARKET"}
        self.daily = pd.DataFrame({"Close": [99, 100, 999]},
                                  index=pd.to_datetime(["2026-10-15", "2026-10-16", "2026-10-19"]))

    def bars(self, stamps, prices, volumes):
        return pd.DataFrame({"Close": prices, "Volume": volumes},
                            index=pd.DatetimeIndex(stamps).tz_localize(p.ET))

    def check(self, bars, daily=None, now=None):
        return p.analyse("TEST", "Minervini + EOD", self.daily if daily is None else daily,
                         bars, self.context, self.now if now is None else now)

    def test_today_completed_minutes_only_and_exact_previous_close(self):
        bars = self.bars(["2026-10-16 19:00", "2026-10-19 03:59", "2026-10-19 08:53",
                          "2026-10-19 08:54", "2026-10-19 08:55", "2026-10-19 09:30"],
                         [900, 900, 102, 103, 900, 900], [900000, 900000, 30000, 30000, 900000, 900000])
        row = self.check(bars)
        self.assertEqual(row["Previous Close"], 100)
        self.assertEqual(row["Premarket Bar Price"], 103)
        self.assertEqual(row["Gap %"], 3)
        self.assertEqual(row["Premarket Bar Volume"], 60000)
        self.assertEqual(row["Captured Minutes"], 2)
        self.assertEqual(row["Data Age Minutes"], 1)
        self.assertTrue(row["Review Flag"])

    def test_zero_volume_cannot_refresh_stale_trade(self):
        bars = self.bars(["2026-10-19 08:30", "2026-10-19 08:54"], [103, 104], [70000, 0])
        row = self.check(bars)
        self.assertEqual(row["Status"], "STALE")
        self.assertEqual(row["Premarket Bar Price"], 103)
        self.assertFalse(row["Review Flag"])

    def test_missing_current_session_is_not_previous_afterhours(self):
        bars = self.bars(["2026-10-16 19:59"], [105], [90000])
        row = self.check(bars)
        self.assertEqual(row["Status"], "NO_TODAY_PREMARKET_BARS")
        self.assertEqual(row["Premarket Bar Price"], "")

    def test_missing_volumes_and_unknown_timezone_block_flag(self):
        bars = self.bars(["2026-10-19 08:53", "2026-10-19 08:54"], [102, 103], [None, 60000])
        self.assertEqual(self.check(bars)["Status"], "VOLUME_INCOMPLETE")
        bars.index = bars.index.tz_localize(None)
        self.assertEqual(self.check(bars)["Status"], "TIMEZONE_UNKNOWN")

    def test_split_missing_baseline_and_crossing_open_block_flag(self):
        bars = self.bars(["2026-10-19 08:54"], [103], [60000])
        daily = self.daily.copy()
        daily["Stock Splits"] = [0, 0, 2]
        self.assertEqual(self.check(bars, daily=daily)["Status"], "SPLIT_REQUIRES_REVIEW")
        self.assertEqual(self.check(bars, daily=self.daily.drop(pd.Timestamp("2026-10-16")))["Status"], "PREVIOUS_CLOSE_MISSING")
        row = self.check(bars, now=pd.Timestamp("2026-10-19 09:30", tz=p.ET))
        self.assertEqual(row["Status"], "SESSION_ENDED_DURING_SCAN")
        self.assertFalse(row["Review Flag"])

    def test_watchlist_unions_latest_passes_and_eod_without_duplicates(self):
        mini = [{"Date":"2026-10-16", "Ticker":"AAPL", "Pass All":"TRUE"},
                {"Date":"2026-10-16", "Ticker":"MSFT", "Pass All":"FALSE"},
                {"Date":"2026-10-15", "Ticker":"OLD", "Pass All":"TRUE"}]
        eod = [{"Date":"2026-10-16", "Ticker":"AAPL"},
               {"Date":"2026-10-16", "Ticker":"DOWN"}]
        result = p.watchlist(mini, eod, "2026-10-16")
        self.assertEqual(set(result), {"AAPL", "DOWN", "SPY", "QQQ"})
        self.assertIn("Minervini", result["AAPL"])
        with self.assertRaises(ValueError):
            p.watchlist(mini, eod, "2026-10-19")

    def test_dst_prior_session_holiday_and_offhours(self):
        for stamp, dates, previous in [
            ("2026-10-19T12:55:00Z", ["2026-10-16", "2026-10-19"], "2026-10-16"),
            ("2026-11-02T13:55:00Z", ["2026-10-30", "2026-11-02"], "2026-10-30"),
            ("2026-12-28T13:55:00Z", ["2026-12-24", "2026-12-28"], "2026-12-24"),
        ]:
            context = p.session_context(pd.Timestamp(stamp), CalendarFixture(dates))
            self.assertEqual(context["state"], "PREMARKET")
            self.assertEqual(context["previous"], previous)
        self.assertEqual(p.session_context(pd.Timestamp("2026-12-25T13:55Z"),
                                         CalendarFixture(["2026-12-24"]))["state"], "NO_TRADING_SESSION")
        self.assertEqual(p.session_context(pd.Timestamp("2026-10-19T14:00Z"),
                                         CalendarFixture(["2026-10-16", "2026-10-19"]))["state"], "OUTSIDE_PREMARKET")

    def test_snapshot_blanks_surplus_and_preserves_other_sheets(self):
        stub = types.SimpleNamespace(WorksheetNotFound=MissingWorksheet)
        with patch.dict(sys.modules, {"gspread": stub}):
            for old in [[], [[""]*19], [p.COLUMNS + ["", ""]]]:
                book = MemoryBook(old)
                p.save_snapshot(book, [p.make_row("TEST", "", self.context, self.now)])
                self.assertEqual(book.ws.values[0], p.COLUMNS)
            book = MemoryBook([p.COLUMNS] + [["old"]*len(p.COLUMNS) for _ in range(5)])
            p.save_snapshot(book, [p.make_row("TEST", "", self.context, self.now)])
            self.assertTrue(all(not any(r) for r in book.ws.values[2:]))
            book = MemoryBook([["user notes"]])
            with self.assertRaises(ValueError):
                p.save_snapshot(book, [])
            self.assertEqual(book.ws.updates, 0)

    def test_probe_pipeline_requests_minutes_and_never_flags(self):
        book = MemoryBook([])
        calls = []
        def download(**kwargs):
            calls.append(kwargs)
            self.assertFalse(kwargs["auto_adjust"])
            if kwargs["interval"] == "1m":
                self.assertTrue(kwargs["prepost"])
                self.assertFalse(kwargs["ignore_tz"])
                frame = self.bars(["2026-10-19 08:54"], [103], [60000])
            else:
                frame = self.daily
            return pd.concat({t: frame for t in kwargs["tickers"]}, axis=1)
        stub = types.SimpleNamespace(WorksheetNotFound=MissingWorksheet)
        with patch.dict(sys.modules, {"gspread": stub}):
            p.run(book, now=self.now, scope="probe", download=download,
                  calendar=CalendarFixture(["2026-10-16", "2026-10-19"]), now_fn=lambda: self.now)
        rows = p.sheet_records(book.ws.values)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["Status"] == "RECENT_BAR" for r in rows))
        self.assertTrue(all(r["Review Flag"] is False for r in rows))
        self.assertEqual(len(calls), 2)

    def test_real_calendar_when_dependency_is_installed(self):
        try:
            import pandas_market_calendars  # noqa: F401
        except ImportError:
            self.skipTest("Calendar dependency is installed by the GitHub workflow.")
        for stamp, previous in [("2026-10-19T12:55Z", "2026-10-16"),
                                ("2026-11-02T13:55Z", "2026-10-30"),
                                ("2026-12-28T13:55Z", "2026-12-24")]:
            context = p.session_context(pd.Timestamp(stamp))
            self.assertEqual(context["state"], "PREMARKET")
            self.assertEqual(context["previous"], previous)
        self.assertEqual(p.session_context(pd.Timestamp("2026-12-25T13:55Z"))["state"], "NO_TRADING_SESSION")


if __name__ == "__main__":
    unittest.main()
