from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta

from etf_lab.engine import Config


def sample():
    closes = [10, 10, 10, 9, 8, 12, 14, 14, 10, 8, 8, 8]
    days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(len(closes))]
    bars = [{"date": d, "open": float(c), "high": float(c), "low": float(c), "close": float(c),
             "volume": 1000000.0, "dividend": 0.0, "split": 1.0} for d, c in zip(days, closes)]
    snapshot = {"schema": "etf-bars-v1", "symbol": "SPY", "source": "SIMULATED_FIXTURE",
                "source_url": "fixture://spy-hand-calculation-v1", "observed_at": "2020-02-01T00:00:00+00:00",
                "data_cutoff": days[-1], "coverage": {"start": days[0], "end_inclusive": days[-1]},
                "price_basis": "Synthetic as-traded prices; calendar intentionally artificial",
                "calendar": "Explicit artificial daily sessions for unit tests, NOT NYSE",
                "expected_sessions": days, "bars": bars, "synthetic": True}
    config = Config(fast=2, slow=3, adv_window=2, start=days[4], end=days[-1],
                    initial_cash=1000.0, fee_fixed=0, slippage_bps=0, impact_bps=0)
    return snapshot, config


def change_price(bar, **kwargs):
    bar.update(kwargs)
    bar["high"] = max(bar["open"], bar["close"])
    bar["low"] = min(bar["open"], bar["close"])
