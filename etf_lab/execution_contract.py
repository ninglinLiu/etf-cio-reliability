"""Check the auditable order/fill time contract; not a proof for arbitrary code."""
import math

from .engine import Config


def validate_execution(result, snapshot, cfg: Config):
    engine = result.get("engine", {})
    if (engine.get("name") != "backtrader" or engine.get("version") != "1.9.78.123" or
            any(engine.get(k) is not False for k in ("preload", "runonce", "cheat_on_open", "cheat_on_close"))):
        raise ValueError("UNAPPROVED_EXECUTION_POLICY")
    bars = snapshot["bars"]
    indices = {b["date"]: i for i, b in enumerate(bars)}
    orders = {o["sequence"]: o for o in result["orders"]}
    if len(orders) != len(result["orders"]): raise ValueError("DUPLICATE_ORDER_SEQUENCE")
    for order in orders.values():
        i = indices[order["date"]]
        if order["latest_observed_session"] != order["date"]:
            raise ValueError("INVALID_INFORMATION_CUTOFF")
        if order["sizing_close"] != bars[i]["close"]:
            raise ValueError("SIZING_PRICE_IS_NOT_SIGNAL_CLOSE")
        adv = sum(b["close"] * b["volume"] for b in bars[i-cfg.adv_window+1:i+1]) / cfg.adv_window
        if not math.isclose(adv, order["prior_adv_dollars"], rel_tol=1e-12):
            raise ValueError("ADV_INCLUDES_WRONG_SESSIONS")
        if order["side"] == "BUY":
            price = bars[i]["close"] * (1 + (cfg.slippage_bps + cfg.impact_bps)/10000)
            qty = math.floor((order["cash_at_submission"] * cfg.allocation - cfg.fee_fixed) /
                             (price * (1 + cfg.fee_bps/10000)))
            if qty != order["quantity"]: raise ValueError("QUANTITY_NOT_FIXED_AT_SIGNAL_CLOSE")
    for fill in result["trades"]:
        order = orders[fill["order_sequence"]]
        if indices[fill["date"]] != indices[order["date"]] + 1:
            raise ValueError("FILL_NOT_NEXT_SESSION")
        if fill["quantity"] != order["quantity"] or fill["side"] != order["side"]:
            raise ValueError("FILL_CHANGED_SUBMITTED_ORDER")
        if fill["signal_date"] != order["date"]: raise ValueError("SIGNAL_BINDING_MISMATCH")
    return {"status": "passed", "policy": "fixed-close-order-next-open-v1",
            "orders_checked": len(orders), "fills_checked": len(result["trades"]),
            "limitation": "Mechanical execution contract only; no vendor point-in-time or arbitrary-code isolation guarantee"}
