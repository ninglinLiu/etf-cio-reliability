from copy import deepcopy
import unittest

import numpy as np

from etf_lab.artifacts import read_json
from scripts.compare_existing import ROOT, load_existing
from scripts.replicate_paired import replay


class ExternalReplications(unittest.TestCase):
    def test_original_source_matches_independent_two_sided_cost_identity(self):
        original, _ = load_existing()
        opening = np.linspace(100, 160, 80).reshape(-1, 1)
        dates = [str(i) for i in range(80)]
        rows, _ = original["simulate"](["SPY"], dates, opening, opening, "SPY", "market")
        calculated = np.prod([1+r["net_return"] for r in rows])
        independent = float(opening[-1, 0]/opening[21, 0]) * .9985/1.0015
        self.assertAlmostEqual(calculated, independent, places=12)

    def test_paired_future_prices_do_not_change_past_orders(self):
        snapshot = read_json(ROOT/"data/paired-spy-tlt.json")
        config = read_json(ROOT/"config/paired-replication.json")
        original = replay(snapshot, config)
        changed = deepcopy(snapshot)
        for bar in changed["assets"]["SPY"]:
            if bar["date"] >= "2010-01-01": bar["Adj Close"] *= 2
        modified = replay(changed, config)
        for field in ("orders", "daily"):
            self.assertEqual([r for r in original[field] if r["date"] < "2010-01-01"],
                             [r for r in modified[field] if r["date"] < "2010-01-01"])

    def test_paired_orders_rebuild_daily_cash_units_and_equity(self):
        snapshot = read_json(ROOT/"data/paired-spy-tlt.json")
        config = read_json(ROOT/"config/paired-replication.json")
        result = replay(snapshot, config)
        prices = {s: {r["date"]: r["Adj Close"] for r in bars} for s,bars in snapshot["assets"].items()}
        cash, units, held = config["initial_cash"], 0., None
        by_date = {}
        for order in result["orders"]: by_date.setdefault(order["date"], []).append(order)
        for row in result["daily"]:
            for order in by_date.get(row["date"], []):
                amount = order["adjusted_units"] * prices[order["symbol"]][row["date"]]
                if order["side"] == "SELL":
                    self.assertEqual(held, order["symbol"])
                    self.assertAlmostEqual(units, order["adjusted_units"])
                    cash += amount-order["fee"]
                    units, held = 0., None
                else:
                    self.assertIsNone(held)
                    cash -= amount+order["fee"]
                    units, held = order["adjusted_units"], order["symbol"]
            self.assertGreaterEqual(cash, -1e-7)
            self.assertAlmostEqual(cash+units*prices[held][row["date"]], row["equity"], places=7)
        self.assertEqual(result["metrics"]["order_count"], len(result["orders"]))

    def test_paired_missing_asset_session_fails_closed(self):
        snapshot = read_json(ROOT/"data/paired-spy-tlt.json")
        snapshot["assets"]["TLT"].pop(100)
        with self.assertRaisesRegex(ValueError,"Missing or mismatched"):
            replay(snapshot, read_json(ROOT/"config/paired-replication.json"))
