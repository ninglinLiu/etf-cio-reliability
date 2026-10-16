"""Independent cash/holdings reconciliation; no imports from the engine."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run",type=Path)
    args = parser.parse_args()
    read = lambda name:json.loads((args.run/name).read_text(encoding="utf-8"))
    result, frozen, snapshot = read("result.json"),read("input.json"),read("snapshot.json")
    cash = frozen["strategy"]["initial_cash"]
    shares = 0
    by_date = {r["date"]:r for r in snapshot["bars"]}
    fills = {t["date"]:t for t in result["trades"]}
    max_residual = 0
    for day in result["daily"]:
        bar = by_date[day["date"]]
        shares *= bar["split"]
        cash += shares * bar["dividend"]
        if day["date"] in fills:
            trade = fills[day["date"]]
            assert trade["signal_date"] < trade["date"]
            if trade["side"] == "BUY":
                cash -= trade["quantity"] * trade["price"] + trade["fee"]
                shares += trade["quantity"]
            else:
                cash += trade["quantity"] * trade["price"] - trade["fee"]
                shares -= trade["quantity"]
        nav = cash + shares * bar["close"]
        residual = max(abs(cash-day["cash"]),abs(shares-day["shares"]),abs(nav-day["equity"]))
        max_residual = max(max_residual,residual)
        assert cash >= -1e-8 and shares >= 0 and residual < 1e-6
    assert abs(nav-result["metrics"]["ending_equity"]) < 1e-6
    print(json.dumps({"status":"passed","days_reconstructed":len(result["daily"]),"fills_reconstructed":len(fills),
                      "ending_equity":nav,"max_absolute_residual":max_residual},indent=2))


if __name__ == "__main__":main()
