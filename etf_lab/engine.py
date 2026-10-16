from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import math
import statistics


class DataError(ValueError):
    """A dataset cannot support the declared experiment."""


@dataclass(frozen=True)
class Config:
    strategy_version: str = "spy-sma-cross-v1"
    fast: int = 20
    slow: int = 50
    start: str = "2015-01-02"
    end: str = "2025-12-31"
    initial_cash: float = 100000.0
    slippage_bps: float = 5.0
    fee_fixed: float = 1.0
    fee_bps: float = 0.0
    impact_threshold: float = 0.01
    impact_bps: float = 10.0
    adv_window: int = 20
    annualization: int = 252
    dividend_policy: str = "cash_on_ex_date"
    terminal_policy: str = "mark_to_market"
    risk_free_annual: float = 0.0

    def __post_init__(self):
        for name in ("fast", "slow", "adv_window", "annualization"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.fast >= self.slow:
            raise ValueError("fast must be smaller than slow")
        for name in ("initial_cash", "slippage_bps", "fee_fixed", "fee_bps", "impact_threshold", "impact_bps", "risk_free_annual"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.initial_cash <= 0 or self.slippage_bps + self.impact_bps >= 10000 or self.fee_bps >= 10000:
            raise ValueError("Invalid capital or cost rates")
        if date.fromisoformat(self.start) > date.fromisoformat(self.end):
            raise ValueError("start must be before end")
        if self.dividend_policy != "cash_on_ex_date" or self.terminal_policy != "mark_to_market":
            raise ValueError("Unsupported dividend or terminal convention")
        if not self.strategy_version:
            raise ValueError("Strategy version is required")

    def to_dict(self):
        return asdict(self)


def validate(snapshot: dict, cfg: Config) -> list[dict]:
    if snapshot.get("schema") != "etf-bars-v1":
        raise DataError("Unsupported snapshot schema")
    if snapshot.get("symbol") != "SPY":
        raise DataError("This sample supports SPY only")
    for key in ("source", "observed_at", "data_cutoff", "price_basis", "calendar"):
        if not snapshot.get(key):
            raise DataError(f"Snapshot metadata missing: {key}")
    rows = snapshot.get("bars", [])
    expected = snapshot.get("expected_sessions", [])
    if not rows or not expected:
        raise DataError("Bars and an explicit trading-session calendar are required")
    dates = [r["date"] for r in rows]
    if dates != sorted(set(dates)) or expected != sorted(set(expected)):
        raise DataError("Duplicate or unordered sessions")
    if dates != expected:
        raise DataError("Missing or extra trading sessions; no forward fill is allowed")
    for r in rows:
        date.fromisoformat(r["date"])
        for key in ("open", "high", "low", "close", "volume", "dividend", "split"):
            v = r.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise DataError(f"Invalid {key} at {r['date']}")
        if min(r[k] for k in ("open", "high", "low", "close", "split")) <= 0 or min(r["volume"], r["dividend"]) < 0:
            raise DataError(f"Nonpositive price/split or negative volume/dividend at {r['date']}")
        if r["low"] > min(r["open"], r["close"]) or r["high"] < max(r["open"], r["close"]):
            raise DataError(f"OHLC bounds inconsistent at {r['date']}")
    indices = [i for i, r in enumerate(rows) if cfg.start <= r["date"] <= cfg.end]
    if not indices:
        raise DataError("No sessions in the evaluation interval")
    if indices[0] < max(cfg.slow + 1, cfg.adv_window):
        raise DataError("Insufficient pre-period warm-up")
    # Snapshot must cover the full requested interval, even when its last day is a holiday.
    coverage = snapshot.get("coverage", {})
    if coverage.get("start", "9999") > cfg.start or coverage.get("end_inclusive", "0000") < cfg.end:
        raise DataError("Snapshot does not cover the full requested interval")
    if snapshot["data_cutoff"] < dates[-1]:
        raise DataError("Data cutoff precedes the last bar")
    return rows


def signal_series(rows: list[dict], cfg: Config) -> list[dict]:
    """Only events known by each close are applied; never uses future adjusted close."""
    closes, signals = [], []
    previous_spread = None
    for r in rows:
        if r["split"] != 1:
            closes = [c / r["split"] for c in closes]
        closes.append(r["close"])
        fast = sum(closes[-cfg.fast:]) / cfg.fast if len(closes) >= cfg.fast else None
        slow = sum(closes[-cfg.slow:]) / cfg.slow if len(closes) >= cfg.slow else None
        action = None
        if slow is not None:
            spread = fast - slow
            if previous_spread is not None:
                if previous_spread <= 0 < spread:
                    action = "BUY"
                elif previous_spread >= 0 > spread:
                    action = "SELL"
            previous_spread = spread
        signals.append({"date": r["date"], "fast": fast, "slow": slow, "action": action})
    return signals


def quote(side: str, quantity: int, base: float, adv: float, cfg: Config) -> dict:
    if quantity <= 0:
        raise ValueError("Order quantity must be positive")
    if adv <= 0:
        raise DataError("Prior average daily traded value is zero; impact cannot be estimated")
    reference_notional = quantity * base
    participation = reference_notional / adv
    impact_bps = cfg.impact_bps if participation > cfg.impact_threshold else 0.0
    sign = 1 if side == "BUY" else -1
    price = base * (1 + sign * (cfg.slippage_bps + impact_bps) / 10000)
    gross = quantity * price
    fee = cfg.fee_fixed + gross * cfg.fee_bps / 10000
    return {"price": price, "notional": gross, "fee": fee,
            "slippage_cost": reference_notional * cfg.slippage_bps / 10000,
            "impact_cost": reference_notional * impact_bps / 10000,
            "impact_bps": impact_bps, "participation": participation,
            "cash_change": -(gross + fee) if side == "BUY" else gross - fee}


def affordable(cash: float, base: float, adv: float, cfg: Config) -> int:
    lo, hi = 0, int(cash // base)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if -quote("BUY", mid, base, adv, cfg)["cash_change"] <= cash:
            lo = mid
        else:
            hi = mid - 1
    return lo


def metrics(equity: list[dict], initial: float, cfg: Config, closed_pnls: list[float], fills: int) -> dict:
    previous, peak, drawdown = initial, initial, 0.0
    returns = []
    for row in equity:
        value = row["equity"]
        returns.append(value / previous - 1)
        previous = value
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    total = previous / initial - 1
    rf_daily = (1 + cfg.risk_free_annual) ** (1 / cfg.annualization) - 1
    excess = [r - rf_daily for r in returns]
    vol = statistics.stdev(excess) if len(excess) > 1 else 0
    return {"total_return": total, "annualized_return": (1 + total) ** (cfg.annualization / len(returns)) - 1,
            "max_drawdown": drawdown,
            "sharpe": statistics.mean(excess) / vol * math.sqrt(cfg.annualization) if vol > 1e-14 else None,
            "win_rate": sum(p > 0 for p in closed_pnls) / len(closed_pnls) if closed_pnls else None,
            "fill_count": fills, "round_trip_count": len(closed_pnls), "observations": len(returns),
            "ending_equity": previous}


def run_backtest(snapshot: dict, cfg: Config) -> dict:
    rows = validate(snapshot, cfg)
    signals = signal_series(rows, cfg)
    cash, shares = cfg.initial_cash, 0
    trades, daily, events, rejections, pnl = [], [], [], [], []
    entry_cost, cycle_dividends, entry_date = 0.0, 0.0, None
    cumulative_dividends = 0.0
    for i, bar in enumerate(rows):
        if not cfg.start <= bar["date"] <= cfg.end:
            continue
        opening_cash, opening_shares = cash, shares
        if bar["split"] != 1 and shares:
            new_quantity = shares * bar["split"]
            if not math.isclose(new_quantity, round(new_quantity), abs_tol=1e-10):
                raise DataError("Fractional split entitlement requires a cash-in-lieu policy")
            shares = round(new_quantity)
            events.append({"date": bar["date"], "kind": "split", "ratio": bar["split"],
                           "shares_before": opening_shares, "shares_after": shares, "cash_change": 0})
        distribution = shares * bar["dividend"]
        if distribution:
            cash += distribution
            cycle_dividends += distribution
            cumulative_dividends += distribution
            events.append({"date": bar["date"], "kind": "dividend", "eligible_shares": shares,
                           "per_share": bar["dividend"], "cash_change": distribution,
                           "assumption": "Cash available on ex-date, not actual payment date"})
        prior = signals[i - 1]
        # Dollar ADV uses only completed sessions strictly before execution.
        adv = sum(r["close"] * r["volume"] for r in rows[i-cfg.adv_window:i]) / cfg.adv_window
        side = prior["action"]
        trade_cash = 0.0
        if side == "BUY" and shares == 0:
            quantity = affordable(cash, bar["open"], adv, cfg)
            if quantity == 0:
                rejections.append({"date": bar["date"], "signal_date": prior["date"],
                                   "reason": "INSUFFICIENT_CASH", "cash": cash})
            else:
                fill = quote(side, quantity, bar["open"], adv, cfg)
                trade_cash = fill["cash_change"]
                cash += trade_cash
                shares = quantity
                entry_cost, cycle_dividends, entry_date = -trade_cash, 0.0, bar["date"]
                trades.append({"date": bar["date"], "signal_date": prior["date"], "side": side,
                               "reference_open": bar["open"], "quantity": quantity, **fill,
                               "realized_pnl": None, "cash_after": cash, "shares_after": shares})
        elif side == "SELL" and shares:
            quantity = shares
            fill = quote(side, quantity, bar["open"], adv, cfg)
            trade_cash = fill["cash_change"]
            cash += trade_cash
            realized = trade_cash - entry_cost + cycle_dividends
            pnl.append(realized)
            shares = 0
            trades.append({"date": bar["date"], "signal_date": prior["date"], "side": side,
                           "reference_open": bar["open"], "quantity": quantity, **fill,
                           "realized_pnl": realized, "entry_date": entry_date,
                           "entry_cost_including_fee": entry_cost, "cycle_dividends": cycle_dividends,
                           "cash_after": cash, "shares_after": shares})
            entry_cost, cycle_dividends, entry_date = 0.0, 0.0, None
        if cash < -1e-8 or shares < 0:
            raise ArithmeticError("Cash or holding invariant violated")
        equity = cash + shares * bar["close"]
        cash_residual = cash - opening_cash - distribution - trade_cash
        if abs(cash_residual) > 1e-7:
            raise ArithmeticError("Cash ledger failed to reconcile")
        daily.append({"date": bar["date"], "open": bar["open"], "close": bar["close"],
                      "cash_before": opening_cash, "shares_before": opening_shares,
                      "dividend_cash": distribution, "trade_cash": trade_cash,
                      "cash": cash, "shares": shares, "market_value": shares * bar["close"],
                      "equity": equity, "cash_reconciliation_residual": cash_residual,
                      "prior_adv_dollars": adv, "fast": signals[i]["fast"], "slow": signals[i]["slow"],
                      "signal_after_close": signals[i]["action"]})
    open_pnl = shares * daily[-1]["close"] - entry_cost + cycle_dividends if shares else 0.0
    pnl_residual = daily[-1]["equity"] - cfg.initial_cash - sum(pnl) - open_pnl
    if abs(pnl_residual) > 1e-6:
        raise ArithmeticError("Total PnL failed to reconcile")
    return {"metrics": metrics(daily, cfg.initial_cash, cfg, pnl, len(trades)),
            "trades": trades, "daily": daily, "corporate_actions": events, "rejected_orders": rejections,
            "cost_totals": {k: sum(t[k] for t in trades) for k in ("fee", "slippage_cost", "impact_cost")},
            "dividend_income": cumulative_dividends, "open_position_pnl": open_pnl,
            "realized_pnl": sum(pnl), "pnl_reconciliation_residual": pnl_residual,
            "open_shares": shares, "last_signal_not_executed": signals[[r["date"] for r in rows].index(daily[-1]["date"])]["action"],
            "conventions": {"signal": "Strict crossing after close; equality holds prior position; initially cash; pre-period crossings ignored except previous-close order",
                            "execution": "Next session open; integer shares; no leverage; no partial fills or order carry",
                            "prices": "As-traded OHLC; signal history adjusted for splits known by that close; no dividend adjustment",
                            "dividends": "Prior holders receive cash on ex-date (explicit simplifying assumption); no DRIP",
                            "terminal": "Mark open positions at last close; no fictional forced liquidation",
                            "win_rate": "Positive net PnL / closed round trips, including fees and received dividends; open positions excluded",
                            "impact": "Order reference value / previous N sessions average daily traded value, strict threshold; not portfolio allocation fraction"}}


def simplified_reference(snapshot: dict, cfg: Config) -> dict:
    """New local diagnostic comparator, NOT the customer's existing algorithm.

    Binary fractional exposure, close-to-close total return, no costs. Although
    the signal is shifted, it still captures the overnight return before a
    realizable next-open fill. No cash/share accounting is performed.
    """
    rows = validate(snapshot, cfg)
    signals = signal_series(rows, cfg)
    nav, exposure = cfg.initial_cash, 0
    daily = []
    for i, r in enumerate(rows):
        if not cfg.start <= r["date"] <= cfg.end:
            continue
        prior = signals[i-1]["action"]
        if prior == "BUY": exposure = 1
        elif prior == "SELL": exposure = 0
        total_return = r["split"] * (r["close"] + r["dividend"]) / rows[i-1]["close"] - 1
        nav *= 1 + exposure * total_return
        daily.append({"date": r["date"], "equity": nav})
    result = metrics(daily, cfg.initial_cash, cfg, [], 0)
    result.update({"fill_count": None, "round_trip_count": None})
    return {"label": "新建简化参考（不是原项目现有算法）", "metrics": result, "daily": daily,
            "differences": ["同标的、同样本、同交叉信号", "前一收盘信号直接作用于下一段收盘到收盘收益，含不可实现的建仓前隔夜收益", "零费用、零冲击、分数份额、股息即时再投资", "无成交及现金账本，交易次数与胜率不冒充已核对指标"]}
