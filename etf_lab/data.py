from __future__ import annotations

import contextlib
from datetime import date, datetime, timedelta, timezone
import io
import json
import logging
from pathlib import Path

from .engine import DataError


def download_spy(start: str, end: str, cache: Path) -> dict:
    """end is exclusive. Keep the vendor dataframe before normalization."""
    import exchange_calendars as xcals
    import yfinance as yf
    if date.fromisoformat(start) >= date.fromisoformat(end):
        raise DataError("Download start must precede exclusive end")
    cache.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache))
    # Provider exception/request text is never emitted to durable logs.
    previous_logging = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            frame = yf.Ticker("SPY").history(start=start, end=end, interval="1d", auto_adjust=False,
                                             back_adjust=False, actions=True, repair=False, keepna=True,
                                             raise_errors=True, timeout=30)
    except Exception:
        raise DataError("YFINANCE_DOWNLOAD_FAILED: no usable provider response; request details omitted") from None
    finally:
        logging.disable(previous_logging)
    if frame.empty:
        raise DataError("YFINANCE_EMPTY_RESPONSE")
    required = {"Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits"}
    if not required.issubset(frame.columns):
        raise DataError("YFINANCE_REQUIRED_COLUMNS_MISSING")
    # Yahoo OHLC is split adjusted even with auto_adjust=False. This adapter is
    # deliberately limited to a no-split SPY sample; it must not double-adjust.
    if (frame["Stock Splits"] != 0).any():
        raise DataError("SPY_SPLIT_REQUIRES_AS_TRADED_NORMALIZATION: unsupported vendor split sample")
    if "Capital Gains" in frame and (frame["Capital Gains"] != 0).any():
        raise DataError("CAPITAL_GAIN_DISTRIBUTION_REQUIRES_EXPLICIT_ACCOUNTING")
    provider_rows, bars = [], []
    for timestamp, record in frame.iterrows():
        day = timestamp.tz_convert("America/New_York").date().isoformat() if timestamp.tzinfo else timestamp.date().isoformat()
        provider_rows.append({"date": day, **{k: float(record[k]) for k in required}})
        bars.append({"date": day, "open": float(record["Open"]), "high": float(record["High"]),
                     "low": float(record["Low"]), "close": float(record["Close"]),
                     "volume": float(record["Volume"]), "dividend": float(record["Dividends"]), "split": 1.0})
    last = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
    # Calendar bounds are actual sessions. Pad so holiday endpoints (e.g. Jan 1)
    # remain in bounds when asking for the inclusive requested date interval.
    calendar = xcals.get_calendar("XNYS", start=(date.fromisoformat(start)-timedelta(days=31)).isoformat(),
                                  end=(date.fromisoformat(end)+timedelta(days=31)).isoformat())
    sessions = [s.date().isoformat() for s in calendar.sessions_in_range(start, last)]
    if [r["date"] for r in bars] != sessions:
        raise DataError("TRADING_SESSION_COVERAGE_MISMATCH: missing/extra daily bars")
    result = {"schema": "etf-bars-v1", "symbol": "SPY", "source": "yfinance / Yahoo Finance",
              "source_url": "https://finance.yahoo.com/quote/SPY/history/", "interval": "1d",
              "observed_at": datetime.now(timezone.utc).isoformat(), "data_cutoff": bars[-1]["date"],
              "coverage": {"start": start, "end_inclusive": last},
              "calendar": f"exchange_calendars {xcals.__version__} XNYS daily session calendar",
              "price_basis": "auto_adjust=False; no SPY splits in downloaded window; unadjusted-for-dividend OHLC used for execution",
              "dividend_basis": "Yahoo cash distributions on ex-date; actual payment dates unavailable",
              "adjustment_note": "Vendor Adj Close retained only as evidence, NEVER used for signals, execution or an additional return credit",
              "snapshot_note": "Frozen yfinance returned dataframe, not raw HTTP bytes or a historical point-in-time vendor vintage",
              "provider_version": yf.__version__, "request": {"start": start, "end_exclusive": end, "auto_adjust": False, "repair": False, "actions": True},
              "expected_sessions": sessions, "provider_rows": provider_rows, "bars": bars, "synthetic": False}
    # Invalid prices/NaNs cannot be silently represented as valid JSON.
    json.dumps(result, allow_nan=False)
    return result
