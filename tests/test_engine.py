from copy import deepcopy
from dataclasses import replace
import math
import unittest

from etf_lab.engine import Config, DataError, affordable, metrics, quote, run_backtest, signal_series
from fixtures import sample, change_price


class FinancialAcceptance(unittest.TestCase):
    def test_hand_calculated_round_trip_and_cash(self):
        snapshot, cfg = sample()
        result = run_backtest(snapshot, cfg)
        # Day 6 close crosses up; 71 shares at day 7 open $14; $6 cash.
        # Day 9 close crosses down; sell at day 10 open $8; loss $426.
        self.assertEqual([(t["date"], t["side"], t["quantity"], t["price"]) for t in result["trades"]],
                         [("2020-01-07", "BUY", 71, 14), ("2020-01-10", "SELL", 71, 8)])
        self.assertEqual(result["trades"][0]["cash_after"], 6)
        self.assertEqual(result["trades"][1]["realized_pnl"], -426)
        self.assertAlmostEqual(result["metrics"]["total_return"], -.426)
        self.assertEqual(result["metrics"]["round_trip_count"], 1)
        self.assertEqual(result["metrics"]["win_rate"], 0)
        self.assertAlmostEqual(result["pnl_reconciliation_residual"], 0)

    def test_gap_fills_next_open_not_signal_close(self):
        snapshot, cfg = sample()
        change_price(snapshot["bars"][6], open=20.0)
        result = run_backtest(snapshot, cfg)
        buy = result["trades"][0]
        self.assertEqual(buy["signal_date"], "2020-01-06")
        self.assertEqual(buy["date"], "2020-01-07")
        self.assertEqual(buy["price"], 20)
        self.assertEqual(buy["quantity"], 50)
        self.assertEqual(result["daily"][2]["equity"], 700)
        self.assertAlmostEqual(result["metrics"]["total_return"], -.60)

    def test_split_preserves_wealth_and_signal_continuity(self):
        snapshot, cfg = sample()
        original = run_backtest(snapshot, cfg)
        # 2-for-1 on day 8, while holding. Equivalent path must have same PnL.
        snapshot["bars"][7]["split"] = 2.0
        for bar in snapshot["bars"][7:]:
            for key in ("open", "high", "low", "close"): bar[key] /= 2
            bar["volume"] *= 2
        result = run_backtest(snapshot, cfg)
        self.assertEqual(result["trades"][1]["quantity"], 142)
        self.assertEqual(result["daily"][3]["equity"], original["daily"][3]["equity"])
        self.assertAlmostEqual(result["metrics"]["total_return"], original["metrics"]["total_return"])
        self.assertEqual([s["action"] for s in signal_series(sample()[0]["bars"], cfg)],
                         [s["action"] for s in signal_series(snapshot["bars"], cfg)])

    def test_dividend_credited_once_to_prior_holders(self):
        snapshot, cfg = sample()
        snapshot["bars"][7]["dividend"] = 1.0
        for bar in snapshot["bars"][7:]:
            change_price(bar, open=bar["open"]-1, close=bar["close"]-1)
        # Adj Close is intentionally nonsense: engine cannot use it as money.
        for bar in snapshot["bars"]: bar["adj_close"] = 99999
        result = run_backtest(snapshot, cfg)
        self.assertEqual(result["dividend_income"], 71)
        self.assertEqual(result["daily"][3]["equity"], 1000)
        self.assertEqual(result["trades"][1]["realized_pnl"], -426)

    def test_ex_date_buyer_has_no_distribution_entitlement(self):
        snapshot, cfg = sample()
        snapshot["bars"][6]["dividend"] = 1.0
        result = run_backtest(snapshot, cfg)
        self.assertEqual(result["dividend_income"], 0)

    def test_fees_slippage_and_impact_are_not_double_counted(self):
        snapshot, cfg = sample()
        cfg = replace(cfg, fee_fixed=1, fee_bps=10, slippage_bps=5, impact_bps=10, impact_threshold=0)
        result = run_backtest(snapshot, cfg)
        buy, sell = result["trades"]
        buy_price, sell_price = 14 * 1.0015, 8 * .9985
        self.assertEqual(buy["quantity"], 71)
        expected = 1000 - 71*buy_price - (1 + 71*buy_price*.001) + 71*sell_price - (1 + 71*sell_price*.001)
        self.assertAlmostEqual(result["metrics"]["ending_equity"], expected)
        self.assertAlmostEqual(result["cost_totals"]["impact_cost"], 71*(14+8)*.001)
        self.assertAlmostEqual(result["cost_totals"]["slippage_cost"], 71*(14+8)*.0005)

    def test_missing_session_rejected_instead_of_forward_filled(self):
        snapshot, cfg = sample()
        del snapshot["bars"][7]
        with self.assertRaisesRegex(DataError, "Missing or extra"):
            run_backtest(snapshot, cfg)

    def test_nan_duplicate_and_short_coverage_rejected(self):
        snapshot, cfg = sample()
        snapshot["bars"][5]["close"] = float("nan")
        with self.assertRaises(DataError): run_backtest(snapshot, cfg)
        snapshot, cfg = sample()
        snapshot["bars"][5]["date"] = snapshot["bars"][4]["date"]
        with self.assertRaises(DataError): run_backtest(snapshot, cfg)
        snapshot, cfg = sample()
        with self.assertRaisesRegex(DataError,"full requested"):
            run_backtest(snapshot, replace(cfg, end="2020-01-20"))

    def test_insufficient_cash_rejects_order_without_negative_cash(self):
        snapshot, cfg = sample()
        result = run_backtest(snapshot, replace(cfg, initial_cash=14, fee_fixed=1))
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["rejected_orders"][0]["reason"], "INSUFFICIENT_CASH")
        self.assertTrue(all(r["cash"] == 14 and r["shares"] == 0 for r in result["daily"]))
        self.assertIsNone(result["metrics"]["win_rate"])
        self.assertIsNone(result["metrics"]["sharpe"])

    def test_future_prices_and_volume_do_not_change_prior_orders(self):
        snapshot, cfg = sample()
        before = run_backtest(snapshot, cfg)
        for bar in snapshot["bars"][7:]:
            change_price(bar, open=bar["open"]*100, close=bar["close"]*100)
            bar["volume"] = 1
        after = run_backtest(snapshot, cfg)
        self.assertEqual(before["trades"][0], after["trades"][0])
        self.assertEqual(before["daily"][:3], after["daily"][:3])

    def test_execution_day_volume_does_not_leak_into_impact(self):
        snapshot, cfg = sample()
        cfg = replace(cfg, impact_threshold=.00001, impact_bps=20)
        original = run_backtest(snapshot, cfg)["trades"][0]
        snapshot["bars"][6]["volume"] = 10**15
        self.assertEqual(original, run_backtest(snapshot, cfg)["trades"][0])

    def test_affordable_matches_independent_enumeration_across_impact_threshold(self):
        _, cfg = sample()
        cfg = replace(cfg, fee_fixed=1, fee_bps=3, impact_threshold=.2, impact_bps=100, slippage_bps=10)
        for cash in (10, 21, 100, 101, 105, 500):
            feasible = [q for q in range(int(cash/10)+1) if q == 0 or -quote("BUY",q,10,500,cfg)["cash_change"] <= cash]
            self.assertEqual(affordable(cash,10,500,cfg), max(feasible))

    def test_terminal_position_is_marked_not_artificially_sold(self):
        snapshot, cfg = sample()
        result = run_backtest(snapshot, replace(cfg, end="2020-01-08"))
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["open_shares"], 71)
        self.assertEqual(result["metrics"]["round_trip_count"], 0)
        self.assertAlmostEqual(result["pnl_reconciliation_residual"], 0)

    def test_drawdown_includes_initial_capital(self):
        _, cfg = sample()
        result = metrics([{"equity":80},{"equity":90}], 100, cfg, [], 0)
        self.assertAlmostEqual(result["max_drawdown"], -.20)

    def test_config_rejects_nonsensical_inputs(self):
        for change in ({"fast":50}, {"fee_fixed":-1}, {"slow":True}, {"initial_cash":0}, {"impact_bps":10000}):
            with self.subTest(change=change), self.assertRaises(ValueError): Config(**change)


if __name__ == "__main__": unittest.main()
