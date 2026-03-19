"""
Trading Calendar - Determines the most recent trading day for ICE exchanges
and validates whether downloaded reports match the expected trading date.
"""

import logging
import re
from datetime import date, datetime, timedelta

import exchange_calendars as xcals
import pandas as pd

logger = logging.getLogger(__name__)

# Map report IDs to their exchange calendar codes
EXCHANGE_CALENDAR_MAP = {
    # ICE Futures Europe
    7: "ICE",
    83: "ICE",
    159: "ICE",
    # ICE Futures U.S.
    10: "ICEUS",
    # ICE Futures Singapore / Abu Dhabi / Canada — fallback to ICE
    143: "ICE",
    160: "ICE",
    167: "ICE",
    254: "ICE",
    # Volume & OI reports (cross-exchange, use ICE as default)
    8: "ICE",
    9: "ICE",
    26: "ICE",
    27: "ICE",
    97: "ICE",
    176: "ICE",
    # Others
    145: "ICE",
    196: "ICE",
    278: "ICEUS",
}

# Default exchange if report ID not in map
DEFAULT_EXCHANGE = "ICE"


class TradingCalendar:
    """Provides trading day information for ICE exchanges."""

    def __init__(self):
        self._calendars: dict[str, xcals.ExchangeCalendar] = {}

    def _get_calendar(self, exchange: str) -> xcals.ExchangeCalendar:
        """Get or create a cached exchange calendar."""
        if exchange not in self._calendars:
            self._calendars[exchange] = xcals.get_calendar(exchange)
        return self._calendars[exchange]

    def get_last_trading_day(self, reference_date: date = None,
                             exchange: str = DEFAULT_EXCHANGE) -> date:
        """Get the most recent completed trading day.

        If reference_date is a trading day, returns the previous trading day
        (since the current day's reports aren't available until after close).
        If reference_date is not a trading day (weekend/holiday), returns
        the most recent trading day before it.
        """
        if reference_date is None:
            reference_date = date.today()

        cal = self._get_calendar(exchange)
        ts = pd.Timestamp(reference_date)

        if cal.is_session(ts):
            return cal.previous_session(ts).date()
        else:
            # For non-session days, find the last session on or before
            idx = cal.sessions.searchsorted(ts, side="right") - 1
            if idx >= 0:
                return cal.sessions[idx].date()
            return cal.sessions[0].date()

    def is_trading_day(self, check_date: date,
                       exchange: str = DEFAULT_EXCHANGE) -> bool:
        """Check if a given date is a trading day."""
        cal = self._get_calendar(exchange)
        return cal.is_session(pd.Timestamp(check_date))

    def get_expected_report_date(self, report_id: int = None,
                                 reference_date: date = None) -> date:
        """Get the expected report date for a given report.

        Reports published today should contain data from the last trading day.
        """
        exchange = EXCHANGE_CALENDAR_MAP.get(report_id, DEFAULT_EXCHANGE)
        return self.get_last_trading_day(reference_date, exchange)

    def validate_report_date(self, report_date: date, report_id: int = None,
                              reference_date: date = None) -> bool:
        """Check if the report date matches the expected most recent trading day.

        Returns True if the report is for the expected trading day.
        """
        expected = self.get_expected_report_date(report_id, reference_date)
        return report_date == expected


def extract_date_from_text(text: str) -> date | None:
    """Try to extract a report date from PDF text content.

    Looks for common date patterns in ICE reports.
    """
    if not text:
        return None

    # Common date patterns in ICE reports
    patterns = [
        # "18 Mar 2026", "18 March 2026"
        r"(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
        r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
        r"Nov(?:ember)?|Dec(?:ember)?)\s+(\d{4})",
        # "Mar 18, 2026", "March 18, 2026"
        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
        r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
        r"Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2}),?\s+(\d{4})",
        # "2026-03-18"
        r"(\d{4})-(\d{2})-(\d{2})",
        # "03/18/2026" or "18/03/2026"
        r"(\d{2})/(\d{2})/(\d{4})",
        # "Date: 2026-03-18"
        r"[Dd]ate[:\s]+(\d{4})-(\d{2})-(\d{2})",
    ]

    month_map = {
        "jan": 1, "january": 1, "feb": 2, "february": 2,
        "mar": 3, "march": 3, "apr": 4, "april": 4,
        "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }

    # Try "Date:" prefixed patterns first (most reliable)
    date_prefix = re.search(
        r"[Dd]ate[:\s]+(\d{4})-(\d{2})-(\d{2})", text
    )
    if date_prefix:
        try:
            return date(
                int(date_prefix.group(1)),
                int(date_prefix.group(2)),
                int(date_prefix.group(3)),
            )
        except ValueError:
            pass

    # "18 Mar 2026" style
    m = re.search(patterns[0], text)
    if m:
        try:
            day = int(m.group(1))
            month = month_map.get(m.group(2).lower()[:3])
            year = int(m.group(3))
            if month:
                return date(year, month, day)
        except ValueError:
            pass

    # "Mar 18, 2026" style
    m = re.search(patterns[1], text)
    if m:
        try:
            month = month_map.get(m.group(1).lower()[:3])
            day = int(m.group(2))
            year = int(m.group(3))
            if month:
                return date(year, month, day)
        except ValueError:
            pass

    # "2026-03-18" style
    m = re.search(patterns[2], text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    return None


def extract_date_from_filename(filename: str) -> date | None:
    """Try to extract a date from a PDF filename.

    ICE report filenames often contain dates like:
    - FuturesReport_20260318.pdf
    - EOD_2026-03-18.pdf
    - report_03182026.pdf
    """
    if not filename:
        return None

    # YYYYMMDD
    m = re.search(r"(\d{4})(\d{2})(\d{2})", filename)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            # Sanity check: year between 2020 and 2030
            if 2020 <= d.year <= 2030:
                return d
        except ValueError:
            pass

    # YYYY-MM-DD
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", filename)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    return None
