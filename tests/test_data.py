from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from etf_lab.data import download_spy
from etf_lab.engine import DataError


class ProviderNormalization(unittest.TestCase):
    def frame(self):
        import pandas as pd
        return pd.DataFrame({"Open":[10.,11.],"High":[10.,11.],"Low":[10.,11.],"Close":[10.,11.],
                             "Adj Close":[9.,10.],"Volume":[100,200],"Dividends":[0.,0.],"Stock Splits":[0.,0.]},
                            index=pd.DatetimeIndex(["2020-01-02","2020-01-03"],tz="America/New_York"))

    def test_holiday_date_range_uses_correct_calendar_bounds(self):
        with tempfile.TemporaryDirectory() as root, patch("yfinance.Ticker.history", return_value=self.frame()):
            result = download_spy("2020-01-01","2020-01-04",Path(root))
        self.assertEqual(result["expected_sessions"],["2020-01-02","2020-01-03"])
        self.assertEqual(result["bars"][0]["close"],10)
        self.assertEqual(result["provider_rows"][0]["Adj Close"],9)

    def test_provider_missing_session_is_not_silently_removed(self):
        frame = self.frame().iloc[:1]
        with tempfile.TemporaryDirectory() as root, patch("yfinance.Ticker.history", return_value=frame):
            with self.assertRaisesRegex(DataError,"COVERAGE_MISMATCH"):
                download_spy("2020-01-01","2020-01-04",Path(root))

    def test_split_adjusted_vendor_data_requires_explicit_conversion(self):
        frame = self.frame()
        frame.loc[frame.index[1],"Stock Splits"] = 2
        with tempfile.TemporaryDirectory() as root, patch("yfinance.Ticker.history", return_value=frame):
            with self.assertRaisesRegex(DataError,"AS_TRADED_NORMALIZATION"):
                download_spy("2020-01-01","2020-01-04",Path(root))

    def test_provider_exception_is_redacted(self):
        with tempfile.TemporaryDirectory() as root, patch("yfinance.Ticker.history", side_effect=RuntimeError("api_key=SECRET_VALUE")):
            with self.assertRaises(DataError) as ctx:
                download_spy("2020-01-01","2020-01-04",Path(root))
        self.assertNotIn("SECRET_VALUE",str(ctx.exception))


if __name__ == "__main__": unittest.main()
