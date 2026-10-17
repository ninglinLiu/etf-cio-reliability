"""Causal execution tests; compare time prefixes and independent money arithmetic."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from etf_lab.backtrader_engine import run_backtest
from etf_lab.engine import DataError
from etf_lab.execution_contract import validate_execution
from fixtures import sample, change_price


def intent(order):
    return {k: v for k, v in order.items() if k != "events"}


class BacktraderAcceptance(unittest.TestCase):
    def setUp(self):
        self.snapshot, original = sample()
        self.cfg = replace(original, allocation=.5)

    def test_framework_and_hand_calculated_round_trip(self):
        r = run_backtest(self.snapshot, self.cfg)
        self.assertEqual(r["engine"]["name"], "backtrader")
        self.assertEqual(r["engine"]["version"], "1.9.78.123")
        self.assertFalse(any(r["engine"][x] for x in ("preload", "runonce", "cheat_on_open", "cheat_on_close")))
        # $500 budget / Jan 6 close $12 => 41 shares, fixed before Jan 7 $14 open.
        self.assertEqual([(t["date"], t["quantity"], t["price"]) for t in r["trades"]],
                         [("2020-01-07", 41, 14), ("2020-01-10", 41, 8)])
        self.assertEqual(r["trades"][0]["cash_after"], 426)
        self.assertEqual(r["metrics"]["ending_equity"], 754)
        self.assertEqual(r["trades"][1]["realized_pnl"], -246)

    def test_next_open_cannot_change_prior_submitted_quantity(self):
        baseline = run_backtest(self.snapshot, self.cfg)
        change_price(self.snapshot["bars"][6], open=20)
        changed = run_backtest(self.snapshot, self.cfg)
        self.assertEqual(intent(baseline["orders"][0]), intent(changed["orders"][0]))
        self.assertEqual(changed["trades"][0]["quantity"], 41)
        self.assertEqual(changed["trades"][0]["price"], 20)

    def test_gap_insufficient_cash_is_broker_margin_not_resizing(self):
        change_price(self.snapshot["bars"][6], open=30)
        r = run_backtest(self.snapshot, self.cfg)
        self.assertEqual(r["orders"][0]["quantity"], 41)
        self.assertEqual(r["trades"], [])
        self.assertEqual(r["rejected_orders"][0]["reason"], "BROKER_MARGIN")
        self.assertEqual(r["rejected_orders"][0]["quantity"], 41)
        self.assertEqual(r["metrics"]["ending_equity"], 1000)

    def test_execution_day_close_high_low_volume_do_not_affect_open_fill(self):
        cfg = replace(self.cfg, slippage_bps=5, impact_threshold=0, impact_bps=10)
        before = run_backtest(self.snapshot, cfg)
        bar = self.snapshot["bars"][6]
        change_price(bar, close=999)
        bar.update(high=9999, low=1, volume=10**12)
        after = run_backtest(self.snapshot, cfg)
        self.assertEqual(intent(before["orders"][0]), intent(after["orders"][0]))
        self.assertEqual(before["trades"][0], after["trades"][0])

    def test_future_tail_changes_leave_past_daily_signals_and_orders_identical(self):
        before = run_backtest(self.snapshot, self.cfg)
        for bar in self.snapshot["bars"][8:]:
            change_price(bar, open=bar["open"]*100, close=bar["close"]*200)
            bar.update(volume=1, dividend=100, adj_close=-999)
        after = run_backtest(self.snapshot, self.cfg)
        cutoff = "2020-01-08"
        for key in ("daily", "trades"):
            self.assertEqual([x for x in before[key] if x["date"] <= cutoff],
                             [x for x in after[key] if x["date"] <= cutoff])
        self.assertEqual([intent(x) for x in before["orders"] if x["date"] <= cutoff],
                         [intent(x) for x in after["orders"] if x["date"] <= cutoff])

    def test_truncated_replay_matches_full_history_at_each_cutoff(self):
        full = run_backtest(self.snapshot, self.cfg)
        for cutoff in ("2020-01-06", "2020-01-07", "2020-01-08", "2020-01-09", "2020-01-10"):
            with self.subTest(cutoff=cutoff):
                truncated = deepcopy(self.snapshot)
                truncated["bars"] = [x for x in truncated["bars"] if x["date"] <= cutoff]
                truncated["expected_sessions"] = [x["date"] for x in truncated["bars"]]
                truncated["data_cutoff"] = cutoff
                truncated["coverage"]["end_inclusive"] = cutoff
                short = run_backtest(truncated, replace(self.cfg, end=cutoff))
                for key in ("daily", "trades"):
                    self.assertEqual(short[key], [x for x in full[key] if x["date"] <= cutoff])
                self.assertEqual([intent(x) for x in short["orders"]],
                                 [intent(x) for x in full["orders"] if x["date"] <= cutoff])

    def test_adj_close_is_not_a_signal_or_cash_input(self):
        before = run_backtest(self.snapshot, self.cfg)
        for n, bar in enumerate(self.snapshot["bars"]): bar["adj_close"] = n * 1000000
        self.assertEqual(before, run_backtest(self.snapshot, self.cfg))

    def test_unsupported_split_is_rejected_explicitly(self):
        self.snapshot["bars"][7]["split"] = 2
        with self.assertRaisesRegex(DataError, "SPLIT_UNSUPPORTED"):
            run_backtest(self.snapshot, self.cfg)

    def test_dividend_credits_prior_holders_once(self):
        self.snapshot["bars"][7]["dividend"] = 1
        for bar in self.snapshot["bars"][7:]:
            change_price(bar, open=bar["open"]-1, close=bar["close"]-1)
        r = run_backtest(self.snapshot, self.cfg)
        self.assertEqual(r["dividend_income"], 41)
        self.assertEqual(r["metrics"]["ending_equity"], 754)
        self.assertEqual(r["trades"][1]["realized_pnl"], -246)

    def test_ex_date_buyer_does_not_receive_dividend(self):
        self.snapshot["bars"][6]["dividend"] = 1
        self.assertEqual(run_backtest(self.snapshot, self.cfg)["dividend_income"], 0)

    def test_ex_date_seller_receives_dividend_in_closed_pnl(self):
        self.snapshot["bars"][9]["dividend"] = 1
        r = run_backtest(self.snapshot, self.cfg)
        self.assertEqual(r["dividend_income"], 41)
        self.assertEqual(r["trades"][1]["realized_pnl"], -205)
        self.assertEqual(r["metrics"]["ending_equity"], 795)

    def test_fee_slippage_impact_are_applied_once_by_framework(self):
        cfg = replace(self.cfg, fee_fixed=1, fee_bps=10, slippage_bps=5, impact_bps=10, impact_threshold=0)
        r = run_backtest(self.snapshot, cfg)
        buy, sell = r["trades"]
        self.assertEqual(buy["quantity"], 41)
        self.assertAlmostEqual(buy["price"], 14*1.0015)
        self.assertAlmostEqual(sell["price"], 8*.9985)
        cash = 1000 - 41*14*1.0015 - (1+41*14*1.0015*.001) + 41*8*.9985 - (1+41*8*.9985*.001)
        self.assertAlmostEqual(r["metrics"]["ending_equity"], cash)
        self.assertAlmostEqual(r["cost_totals"]["impact_cost"], 41*22*.001)

    def test_missing_session_stops_before_execution(self):
        del self.snapshot["bars"][6]
        with self.assertRaisesRegex(DataError, "Missing or extra"):
            run_backtest(self.snapshot, self.cfg)

    def test_insufficient_cash_at_submission_produces_no_order(self):
        r = run_backtest(self.snapshot, replace(self.cfg, initial_cash=10, fee_fixed=1))
        self.assertEqual(r["orders"], [])
        self.assertEqual(r["trades"], [])
        self.assertEqual(r["rejected_orders"][0]["reason"], "INSUFFICIENT_CASH_AT_SUBMISSION")

    def test_repeated_runs_are_identical_despite_framework_global_order_refs(self):
        self.assertEqual(run_backtest(self.snapshot, self.cfg), run_backtest(self.snapshot, self.cfg))

    def test_no_fictional_terminal_sale(self):
        r = run_backtest(self.snapshot, replace(self.cfg, end="2020-01-08"))
        self.assertEqual(len(r["trades"]), 1)
        self.assertEqual(r["metrics"]["round_trip_count"], 0)
        self.assertEqual(r["open_shares"], 41)

    def test_future_line_is_not_preloaded(self):
        from etf_lab.backtrader_engine import SmaStrategy
        normal = SmaStrategy.next
        observed = []
        def checked(strategy):
            with self.assertRaises(IndexError):
                _ = strategy.data.close[1]
            observed.append(strategy.data.datetime.date(0))
            normal(strategy)
        with patch.object(SmaStrategy, "next", checked):
            run_backtest(self.snapshot, self.cfg)
        self.assertGreater(len(observed), 0)

    def test_result_contract_rejects_same_day_fill_and_retroactive_resize(self):
        result = run_backtest(self.snapshot, self.cfg)
        self.assertEqual(validate_execution(result, self.snapshot, self.cfg)["status"], "passed")
        for mutation, code in ((lambda r: r["trades"][0].update(date=r["orders"][0]["date"]), "FILL_NOT_NEXT_SESSION"),
                               (lambda r: r["trades"][0].update(quantity=40), "FILL_CHANGED_SUBMITTED_ORDER"),
                               (lambda r: r["orders"][0].update(quantity=71), "QUANTITY_NOT_FIXED"),
                               (lambda r: r["engine"].update(cheat_on_close=True), "UNAPPROVED_EXECUTION")):
            changed = deepcopy(result)
            mutation(changed)
            with self.assertRaisesRegex(ValueError, code):
                validate_execution(changed, self.snapshot, self.cfg)


if __name__ == "__main__": unittest.main()
