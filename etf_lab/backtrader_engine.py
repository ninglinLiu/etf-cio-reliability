"""Backtrader owns matching, order states, cash and positions.

Trusted fixed strategy, NOT a sandbox for arbitrary Python or an LLM.
The feed advances one completed bar at a time. No preloaded future line values.
The only broker extensions are explicit ex-date distributions and a slippage
parameter selected from a fixed order and lagged ADV. No custom matching loop.
"""
from datetime import datetime
import math

import backtrader as bt

from .engine import Config, DataError, metrics, validate


class FrozenFeed(bt.feed.DataBase):
    lines = ("dividend",)
    params = (("rows", None),)

    def start(self):
        super().start()
        self._rows = iter(self.p.rows)

    def _load(self):
        row = next(self._rows, None)
        if row is None:
            return False
        self.lines.datetime[0] = bt.date2num(datetime.fromisoformat(row["date"]))
        for name in ("open", "high", "low", "close", "volume", "dividend"):
            getattr(self.lines, name)[0] = row[name]
        self.lines.openinterest[0] = 0
        return True


class Fees(bt.CommInfoBase):
    params = (("stocklike", True), ("commtype", bt.CommInfoBase.COMM_FIXED),
              ("fixed", 0.0), ("bps", 0.0))

    def _getcommission(self, size, price, pseudoexec):
        return self.p.fixed + abs(size) * price * self.p.bps / 10000 if size else 0.0


class ResearchBroker(bt.brokers.BackBroker):
    params = (("cfg", None), ("feed", None))

    def next(self):
        data = self.p.feed
        self.opening_cash = self.getcash()
        self.opening_shares = self.getposition(data).size
        self.distribution = self.opening_shares * data.dividend[0]
        if self.distribution:
            # Queued cash is included by BackBroker's end-of-bar valuation.
            # Existing holdings earn it even if sold today; today's buyer does not.
            # It is not available to today's pending opening order.
            self.add_cash(self.distribution)
        super().next()

    def _try_exec_market(self, order, popen, phigh, plow):
        cfg = self.p.cfg
        adv = order.info["prior_adv_dollars"]
        participation = abs(order.created.size) * popen / adv
        impact = cfg.impact_bps if participation > cfg.impact_threshold else 0.0
        order.addinfo(impact_bps=impact, participation=participation, reference_open=popen)
        # Explicit fixed bps model: never cap using the execution day's future H/L.
        self.set_slippage_perc((cfg.slippage_bps + impact) / 10000,
                              slip_open=True, slip_match=True, slip_out=True)
        super()._try_exec_market(order, popen, phigh, plow)


class SmaStrategy(bt.Strategy):
    params = (("cfg", None), ("first_signal_date", None))

    def __init__(self):
        self.cfg = self.p.cfg
        self.fast = bt.ind.SMA(self.data.close, period=self.cfg.fast)
        self.slow = bt.ind.SMA(self.data.close, period=self.cfg.slow)
        self.dollar_volume = self.data.close * self.data.volume
        self.adv = bt.ind.SMA(self.dollar_volume, period=self.cfg.adv_window)
        self.pending = None
        self.orders, self.fills, self.days, self.events, self.rejections = [], [], [], [], []
        self.closed_pnls = []
        self.entry_cost = self.cycle_dividends = self.dividend_total = 0.0
        self.entry_date = None
        self.last_action = None

    def notify_order(self, order):
        date = self.data.datetime.date(0).isoformat()
        status = order.getstatusname()
        intent = self.orders[order.info["intent_index"]]
        # Per-run logical sequence, not Backtrader's process-global ref counter.
        intent["events"].append({"date": date, "status": status})
        if status == "Completed":
            qty, price, fee = abs(order.executed.size), order.executed.price, order.executed.comm
            side = "BUY" if order.isbuy() else "SELL"
            gross = qty * price
            flow = -gross - fee if order.isbuy() else gross - fee
            realized = None
            # Current ex-date dividend belongs to the closing position.
            if order.isbuy():
                self.entry_cost, self.cycle_dividends, self.entry_date = -flow, 0.0, date
            else:
                realized = flow - self.entry_cost + self.cycle_dividends + self.broker.distribution
                self.closed_pnls.append(realized)
            reference = order.info["reference_open"]
            self.fills.append({"date": date, "signal_date": intent["date"], "side": side,
                "quantity": qty, "price": price, "notional": gross, "fee": fee,
                "cash_change": flow, "reference_open": reference,
                "slippage_cost": qty * reference * self.cfg.slippage_bps / 10000,
                "impact_cost": qty * reference * order.info["impact_bps"] / 10000,
                "impact_bps": order.info["impact_bps"], "participation": order.info["participation"],
                "realized_pnl": realized, "entry_date": self.entry_date,
                "entry_cost_including_fee": self.entry_cost,
                "cycle_dividends": self.cycle_dividends + (self.broker.distribution if not order.isbuy() else 0),
                "cash_after": self.broker.getcash(), "shares_after": self.position.size,
                "order_sequence": intent["sequence"], "broker_status": status})
            if not order.isbuy():
                self.entry_cost = self.cycle_dividends = 0.0
                self.entry_date = None
        elif status in ("Margin", "Rejected", "Canceled", "Expired"):
            self.rejections.append({"date": date, "signal_date": intent["date"],
                "quantity": intent["quantity"], "reason": "BROKER_" + status.upper()})
        if not order.alive():
            self.pending = None

    def prenext(self):
        pass  # Warm-up only; validated sample starts after all indicators are ready.

    def next(self):
        cfg, date = self.cfg, self.data.datetime.date(0).isoformat()
        action = None
        if len(self.data) > cfg.slow:
            previous = self.fast[-1] - self.slow[-1]
            current = self.fast[0] - self.slow[0]
            if previous <= 0 < current: action = "BUY"
            elif previous >= 0 > current: action = "SELL"
        self.last_action = action
        distribution = self.broker.distribution
        if cfg.start <= date <= cfg.end:
            self.dividend_total += distribution
            if distribution:
                if self.position: self.cycle_dividends += distribution
                self.events.append({"date": date, "kind": "dividend",
                    "eligible_shares": self.broker.opening_shares, "per_share": self.data.dividend[0],
                    "cash_change": distribution, "assumption": "Ex-date end-of-session cash; payment date unavailable"})
            todays = [t for t in self.fills if t["date"] == date]
            flow = sum(t["cash_change"] for t in todays)
            cash, shares = self.broker.getcash(), self.position.size
            residual = cash - self.broker.opening_cash - distribution - flow
            if cash < -1e-8 or shares < 0 or abs(residual) > 1e-7:
                raise ArithmeticError("Backtrader ledger failed independent cash invariant")
            self.days.append({"date": date, "open": self.data.open[0], "close": self.data.close[0],
                "cash_before": self.broker.opening_cash, "shares_before": self.broker.opening_shares,
                "dividend_cash": distribution, "trade_cash": flow, "cash": cash, "shares": shares,
                "market_value": shares * self.data.close[0], "equity": self.broker.getvalue(),
                "cash_reconciliation_residual": residual, "prior_adv_dollars": self.adv[-1],
                "fast": self.fast[0], "slow": self.slow[0], "signal_after_close": action})
        if date < self.p.first_signal_date or date > cfg.end or self.pending or not action:
            return
        if action == "BUY" and not self.position:
            # All terms are observable at this close. Never read next open here.
            conservative_price = self.data.close[0] * (1 + (cfg.slippage_bps + cfg.impact_bps) / 10000)
            budget = self.broker.getcash() * cfg.allocation
            quantity = max(0, math.floor((budget - cfg.fee_fixed) /
                           (conservative_price * (1 + cfg.fee_bps / 10000))))
            if not quantity:
                self.rejections.append({"date": date, "signal_date": date, "quantity": 0,
                                        "reason": "INSUFFICIENT_CASH_AT_SUBMISSION"})
                return
        elif action == "SELL" and self.position:
            quantity = self.position.size
        else:
            return
        adv = self.adv[0]
        if adv <= 0: raise DataError("Zero completed-session ADV")
        intent = {"sequence": len(self.orders) + 1, "date": date, "side": action,
            "quantity": quantity, "sizing_close": self.data.close[0],
            "cash_at_submission": self.broker.getcash(), "prior_adv_dollars": adv,
            "latest_observed_session": date, "events": []}
        self.orders.append(intent)
        submit = self.buy if action == "BUY" else self.sell
        self.pending = submit(size=quantity, exectype=bt.Order.Market,
                              intent_index=len(self.orders)-1, prior_adv_dollars=adv)


def run_backtest(snapshot: dict, cfg: Config) -> dict:
    rows = validate(snapshot, cfg)
    if any(r["split"] != 1 for r in rows):
        raise DataError("SPLIT_UNSUPPORTED_BY_BACKTRADER_ADAPTER: require corporate-action capable engine")
    first = next(i for i, r in enumerate(rows) if cfg.start <= r["date"] <= cfg.end)
    feed = FrozenFeed(rows=(r for r in rows if r["date"] <= cfg.end))
    cerebro = bt.Cerebro(preload=False, runonce=False, stdstats=False, cheat_on_open=False)
    cerebro.adddata(feed)
    broker = ResearchBroker(cfg=cfg, feed=feed)
    broker.setcash(cfg.initial_cash)
    broker.set_coc(False)
    broker.set_coo(False)
    broker.addcommissioninfo(Fees(fixed=cfg.fee_fixed, bps=cfg.fee_bps))
    cerebro.setbroker(broker)
    cerebro.addstrategy(SmaStrategy, cfg=cfg, first_signal_date=rows[first-1]["date"])
    strategy = cerebro.run()[0]
    days, fills = strategy.days, strategy.fills
    open_pnl = (strategy.position.size * days[-1]["close"] - strategy.entry_cost +
                strategy.cycle_dividends) if strategy.position else 0.0
    residual = days[-1]["equity"] - cfg.initial_cash - sum(strategy.closed_pnls) - open_pnl
    if abs(residual) > 1e-6: raise ArithmeticError("Backtrader PnL reconciliation failed")
    return {"engine": {"name": "backtrader", "version": bt.__version__, "preload": False,
                       "runonce": False, "cheat_on_close": False, "cheat_on_open": False},
        "metrics": metrics(days, cfg.initial_cash, cfg, strategy.closed_pnls, len(fills)),
        "orders": strategy.orders, "trades": fills, "daily": days,
        "corporate_actions": strategy.events, "rejected_orders": strategy.rejections,
        "cost_totals": {k: sum(t[k] for t in fills) for k in ("fee", "slippage_cost", "impact_cost")},
        "dividend_income": strategy.dividend_total, "open_position_pnl": open_pnl,
        "realized_pnl": sum(strategy.closed_pnls), "pnl_reconciliation_residual": residual,
        "open_shares": strategy.position.size, "last_signal_not_executed": strategy.last_action,
        "conventions": {"signal": "Completed close SMA strict crossing; streaming Backtrader indicators",
            "execution": "Quantity fixed at signal close; Backtrader next-open Market fill or margin rejection; no resize/retry",
            "sizing": "Use allocation fraction of signal-close cash and close price, reserving maximum stated costs",
            "dividends": "Prior holders credited on ex-date at end of broker cycle, unavailable to same-open orders; not payment date",
            "prices": "Unadjusted OHLC; Adj Close never used; splits explicitly rejected",
            "terminal": "Last close mark; no forced liquidation",
            "scope": "Trusted fixed SPY strategy, no arbitrary-agent sandbox or point-in-time vendor data guarantee"}}
