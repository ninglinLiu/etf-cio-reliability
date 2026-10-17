from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

from .model import Model, parse_json
from .store import Store, digest, now

PROTOCOL = {
    "version": "1.1", "lookback": 20, "rebalance_every": 5,
    "holdout_fraction": 0.4, "max_asset_weight": 0.6,
    "fees_bps_one_way": 10, "slippage_bps_one_way": 5,
    "timing": "Signal after close[t]; trade open[t+1]; mark open[t+2]. No same-close fills.",
    "constraints": "Long-only, no leverage, residual cash earns 0; terminal liquidation charged.",
    "invalid_decision_policy": "Reject invalid or failed model output, target cash at the next open, charge turnover, and count rejection. Never silently normalize weights.",
    "factors": "Market open-to-open; cross-sectional 20-day momentum top-minus-bottom (gross). Zero risk-free proxy.",
    "interpretation": "Regression intercept is unexplained return conditional on these factors, not proven skill or causal alpha.",
    "historical_llm": "Historical numerical replay may remain contaminated by pretraining knowledge. Blinding is only a sensitivity test.",
}


def panel(payload: dict):
    expected = payload["universe"]["symbols"]
    assets = {a["symbol"]: a for a in payload["assets"]}
    if any(s not in assets or "error" in assets[s] for s in expected):
        raise ValueError("Incomplete fixed universe: benchmark aborted instead of silently removing failed assets")
    maps = {s: {r["date"]: r for r in assets[s]["rows"]} for s in expected}
    common = sorted(set.intersection(*(set(x) for x in maps.values())))
    if len(common) < 100:
        raise ValueError("At least 100 common completed bars are required")
    opening = np.array([[maps[s][d]["open"] for s in expected] for d in common], dtype=float)
    close = np.array([[maps[s][d]["close"] for s in expected] for d in common], dtype=float)
    if not np.isfinite(opening).all() or not np.isfinite(close).all() or (opening <= 0).any() or (close <= 0).any():
        raise ValueError("Invalid prices")
    excluded = {s: len(maps[s]) - len(common) for s in expected}
    return expected, common, opening, close, excluded


def weights(value, symbols: list[str], cap: float = .6) -> np.ndarray:
    if not isinstance(value, dict) or set(value) - set(symbols):
        raise ValueError("Portfolio contains unknown assets")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or v > cap for v in value.values()):
        raise ValueError("Weights must be finite, long-only and below the position cap")
    w = np.array([value.get(s, 0) for s in symbols], dtype=float)
    if w.sum() > 1 + 1e-9:
        raise ValueError("Leverage is prohibited")
    return w


def rebalance_cost(current: np.ndarray, target: np.ndarray, one_way: float) -> tuple[float, float]:
    cost = 0.
    for _ in range(40):
        turnover = float(np.abs(target * (1 - cost) - current).sum())
        updated = one_way * turnover
        if abs(updated - cost) < 1e-13:
            return updated, turnover
        cost = updated
    raise ValueError("Trading-cost solver did not converge")


def attribution(net, factors, annualization: int) -> dict:
    y, f = np.asarray(net, dtype=float), np.asarray(factors, dtype=float)
    x = np.column_stack([np.ones(len(y)), f])
    n, k = x.shape
    if n < 30 or np.linalg.matrix_rank(x) < k:
        return {"status": "insufficient_data_or_rank", "observations": n}
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    residual = y - x @ beta
    lag = min(int(4 * (n / 100) ** (2 / 9)), n - 1)
    xu = x * residual[:, None]
    meat = xu.T @ xu
    for l in range(1, lag + 1):
        gamma = xu[l:].T @ xu[:-l]
        meat += (1 - l / (lag + 1)) * (gamma + gamma.T)
    bread = np.linalg.pinv(x.T @ x)
    covariance = bread @ meat @ bread * n / (n - k)
    se = float(np.sqrt(max(covariance[0, 0], 0)))
    t = float(beta[0] / se) if se > 1e-14 else None
    return {"status": "estimated", "observations": n, "market_beta": float(beta[1]),
            "momentum_beta": float(beta[2]), "intercept_daily": float(beta[0]),
            "intercept_annualized_arithmetic": float(beta[0] * annualization),
            "intercept_daily_ci95": [float(beta[0] - 1.96 * se), float(beta[0] + 1.96 * se)],
            "hac_t_stat": t, "hac_lags": lag,
            "r_squared": float(1 - residual @ residual / ((y - y.mean()) @ (y - y.mean()))) if np.var(y) > 1e-20 else None,
            "daily_sum_decomposition": {"market": float(np.sum(f[:, 0] * beta[1])), "momentum": float(np.sum(f[:, 1] * beta[2])), "intercept": float(n * beta[0]), "residual": float(np.sum(residual))},
            "caveat": "HAC normal-approximation interval; descriptive, not multiple-testing corrected. Contributions sum simple daily returns, not compounded PnL."}


def summarize(rows: list[dict], annualization: int) -> dict:
    net = np.array([r["net_return"] for r in rows])
    curve = np.cumprod(1 + net)
    peak = np.maximum.accumulate(np.r_[1., curve])[1:]
    volatility = float(np.std(net, ddof=1)) if len(net) > 1 else 0.
    return {"observations": len(rows), "net_total_return": float(curve[-1] - 1),
            "annualized_return": float(curve[-1] ** (annualization / len(net)) - 1),
            "max_drawdown": float(np.min(curve / peak - 1)),
            "sharpe_zero_rf": float(np.mean(net) / volatility * math.sqrt(annualization)) if volatility > 1e-12 else None,
            "turnover_sum": sum(r["turnover"] for r in rows), "cost_fraction_sum": sum(r["cost"] for r in rows),
            "equity_curve": [{"date": r["end_date"], "equity": float(v)} for r, v in zip(rows, curve)],
            "attribution": attribution(net, [r["factors"] for r in rows], annualization)}


def simulate(symbols, dates, opening, close, benchmark, strategy, *, one_way=.0015, start_index=None, decision_fn=None):
    lookback, rebalance = PROTOCOL["lookback"], PROTOCOL["rebalance_every"]
    start = max(lookback, start_index or lookback)
    current, rows, decisions = np.zeros(len(symbols)), [], []
    market_index = symbols.index(benchmark)
    for t in range(start, len(dates) - 2):
        momentum = close[t] / close[t - lookback] - 1
        order = np.argsort(momentum, kind="stable")
        target = current.copy()
        is_rebalance = (t - start) % rebalance == 0
        if strategy == "cash": target[:] = 0
        elif strategy == "market" and t == start:
            target[market_index] = 1
        elif strategy == "equal_weight" and is_rebalance:
            target[:] = 1 / len(symbols)
        elif strategy == "momentum" and is_rebalance:
            target[:] = 0
            for index in order[-2:]:
                if momentum[index] > 0: target[index] = .5
        elif strategy == "llm" and is_rebalance:
            if decision_fn is None: raise ValueError("Missing model decision function")
            # Only prices through t are supplied. Future openings/returns never enter the model request.
            visible = {s: {"momentum_20d": float(momentum[i]), "volatility_20d": float(np.std(np.diff(np.log(close[t-lookback:t+1, i])))),
                           "last_close": float(close[t, i])} for i, s in enumerate(symbols)}
            try:
                decision = decision_fn(dates[t], visible)
                target = weights(decision["weights"], symbols)
            except (ValueError, RuntimeError, KeyError, TypeError) as exc:
                decision = {"weights": {}, "rejected": True, "error": str(exc)[:250]}
                target = np.zeros(len(symbols))
            decisions.append({"signal_date": dates[t], "execution_date": dates[t+1], "visible": visible, "decision": decision})
        cost, turnover = rebalance_cost(current, target, one_way)
        asset_returns = opening[t+2] / opening[t+1] - 1
        gross = float(target @ asset_returns)
        net = (1 - cost) * (1 + gross) - 1
        current = target * (1 + asset_returns) / (1 + gross)
        factor_momentum = float(np.mean(asset_returns[order[-2:]]) - np.mean(asset_returns[order[:2]]))
        rows.append({"signal_date": dates[t], "execution_date": dates[t+1], "end_date": dates[t+2],
                     "weights": dict(zip(symbols, target.tolist())), "gross_return": gross, "net_return": net,
                     "cost": cost, "turnover": turnover, "factors": [float(asset_returns[market_index]), factor_momentum]})
    if not rows: raise ValueError("No evaluable intervals")
    liquidation = float(current.sum() * one_way)
    rows[-1]["net_return"] = (1 + rows[-1]["net_return"]) * (1 - liquidation) - 1
    rows[-1]["cost"] += liquidation
    rows[-1]["turnover"] += float(current.sum())
    rows[-1]["terminal_liquidation"] = True
    return rows, decisions


def run_benchmark(store: Store, market_id: str, include_llm=False, second=False, blind=False) -> dict:
    source = store.get(market_id)
    if source["kind"] != "market": raise ValueError("Expected market artifact")
    payload = source["payload"]
    symbols, dates, opening, close, excluded = panel(payload)
    start = max(PROTOCOL["lookback"], int(len(dates) * (1 - PROTOCOL["holdout_fraction"])))
    if include_llm:
        # 50 intervals -> at most 10 calls per provider, fixed across compared strategies.
        start = max(start, len(dates) - 52)
    protocol = {**PROTOCOL, "dataset_id": market_id, "dataset_hash": source["hash"], "symbols": symbols,
                "start_signal_date": dates[start], "end": dates[-1], "blinded": blind,
                "include_llm": include_llm, "model": Model(store, second=second).config["model"] if include_llm else None}
    registration = store.save("protocol", protocol)  # Persist BEFORE any model decisions/outcomes.
    names = ["cash", "market", "equal_weight", "momentum"] + (["llm"] if include_llm else [])
    results = {}
    model = Model(store, "benchmark", second)
    aliases = {s: f"A{i+1}" for i, s in enumerate(symbols)}
    def decide(date, features):
        shown = {aliases[s] if blind else s: ({k: v for k, v in f.items() if k != "last_close"} if blind else f) for s, f in features.items()}
        answer = model.complete([{"role": "system", "content": "Choose a long-only paper portfolio from supplied numerical features only. No outside facts. Each weight <=0.6, sum <=1, rest cash. Return JSON {weights:{symbol:number}, rationale:string}. Never output a future price. Zero weights allowed."},
                                 {"role": "user", "content": __import__("json").dumps({"date": "hidden" if blind else date, "features": shown})}])
        parsed = parse_json(answer["content"])
        if blind:
            reverse = {v: k for k, v in aliases.items()}
            if set(parsed.get("weights", {})) - set(reverse): raise ValueError("Unknown blinded asset")
            parsed["weights"] = {reverse[k]: v for k, v in parsed["weights"].items()}
        weights(parsed.get("weights"), symbols)
        return parsed
    for name in names:
        try:
            rows, decisions = simulate(symbols, dates, opening, close, payload["universe"]["benchmark"], name,
                                       start_index=start, decision_fn=decide if name == "llm" else None)
            results[name] = {"status": "ok", "metrics": summarize(rows, payload["universe"]["annualization"]), "ledger": rows, "decisions": decisions,
                             "rejected_decisions": sum(bool(d["decision"].get("rejected")) for d in decisions), "total_model_decisions": len(decisions)}
        except Exception as exc:
            results[name] = {"status": "failed", "error": str(exc)[:250]}
    return store.save("benchmark", {"protocol": registration, "market": payload["market"], "results": results,
        "excluded_dates_by_asset": excluded, "data_warning": payload["selection"],
        "model_validation": "Historical replay only; NOT forward evidence or a production trading claim",
        "evaluation_window": {"start": dates[start], "end": dates[-1]},
        "selection_warning": "Frozen rule parameters; temporal holdout is not an untouched research holdout if results are repeatedly inspected."})


def forward_decision(store: Store, market_id: str, purpose="interactive") -> dict:
    artifact = store.get(market_id)
    if artifact["kind"] != "market": raise ValueError("Expected market snapshot")
    p = artifact["payload"]
    symbols, dates, opening, close, _ = panel(p)
    # Deduplicate by market/date: an existing decision is never overwritten.
    key = digest([p["market"], dates[-1], Model(store).config["model"]])
    if any(r["payload"].get("key") == key for r in store.artifacts("forward", 500)):
        return {"status": "already_recorded", "key": key}
    features = {s: {"momentum_20d": float(close[-1, i] / close[-21, i] - 1), "last_close": float(close[-1, i])} for i, s in enumerate(symbols)}
    model = Model(store, purpose)
    result = parse_json(model.complete([{"role": "system", "content": "Output a long-only paper portfolio as JSON {weights:{symbol:number},rationale:string}. Max weight .6, total <=1. Use only supplied features. No orders will be executed."},
        {"role": "user", "content": __import__("json").dumps(features)}])["content"])
    weights(result.get("weights"), symbols)
    recorded = now()
    # Observe first, then trade a FUTURE daily open: never backfill an already passed open.
    next_date = (datetime.fromisoformat(recorded).date() + timedelta(days=1)).isoformat()
    return store.save("forward", {"key": key, "market": p["market"], "model": model.config["model"],
        "recorded_at": recorded, "eligible_execution_date": next_date, "snapshot_id": market_id,
        "snapshot_hash": artifact["hash"], "features": features, "decision": result,
        "status": "pending_future_prices", "execution": "Next available daily open strictly after recording; paper only"})


def score_forward(store: Store, market_id: str) -> dict:
    snapshot = store.get(market_id)
    symbols, dates, opening, _, _ = panel(snapshot["payload"])
    scored = {a["payload"]["decision_id"] for a in store.artifacts("forward_score", 1000)}
    results = []
    for item in store.artifacts("forward", 500):
        p = item["payload"]
        if item["id"] in scored or p["market"] != snapshot["payload"]["market"]: continue
        eligible = [i for i, d in enumerate(dates) if d >= p["eligible_execution_date"]]
        if len(eligible) < 2: continue
        t = eligible[0]
        w = weights(p["decision"]["weights"], symbols)
        ret = opening[t+1] / opening[t] - 1
        cost, turnover = rebalance_cost(np.zeros(len(symbols)), w, .0015)
        gross = float(w @ ret)
        drift = w * (1 + ret) / (1 + gross)
        net = (1 - cost) * (1 + gross) * (1 - .0015 * float(drift.sum())) - 1
        results.append(store.save("forward_score", {"decision_id": item["id"], "snapshot_id": market_id,
            "execution_date": dates[t], "exit_date": dates[t+1], "net_return": net,
            "interpretation": "Independent one-day paper trial from cash, with entry and exit costs; not a continuous portfolio"}))
    return {"scored": len(results), "results": results}
