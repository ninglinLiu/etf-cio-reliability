"""Independent metamorphic experiments; no duplicated matching implementation."""
import argparse
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from etf_lab.artifacts import read_json, sha, write_new
from etf_lab.backtrader_engine import run_backtest
from etf_lab.engine import Config, run_backtest as legacy
from fixtures import sample, change_price


def intents(result, cutoff):
    return [{k: v for k, v in order.items() if k != "events"}
            for order in result["orders"] if order["date"] <= cutoff]


def assert_prefix(reference, changed, cutoff):
    for key in ("daily", "trades"):
        assert [x for x in reference[key] if x["date"] <= cutoff] == [x for x in changed[key] if x["date"] <= cutoff]
    assert intents(reference, cutoff) == intents(changed, cutoff)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    if output.exists(): raise SystemExit("Choose a new evidence path")
    snapshot = read_json(ROOT / "data/spy.json")
    cfg = Config(**read_json(ROOT / "config/spy.json"))
    original = run_backtest(snapshot, cfg)
    checks = []
    for cutoff in ("2017-12-29", "2020-12-31", "2024-12-31"):
        truncated = deepcopy(snapshot)
        truncated["bars"] = [x for x in snapshot["bars"] if x["date"] <= cutoff]
        truncated["expected_sessions"] = [x["date"] for x in truncated["bars"]]
        truncated["coverage"]["end_inclusive"] = cutoff
        truncated["data_cutoff"] = cutoff
        replay = run_backtest(truncated, replace(cfg, end=cutoff))
        assert_prefix(original, replay, cutoff)
        checks.append({"experiment": "truncate_at", "cutoff": cutoff, "status": "passed",
                       "equal_daily_rows": len(replay["daily"]), "equal_order_intents": len(replay["orders"])})
        poisoned = deepcopy(snapshot)
        for bar in poisoned["bars"]:
            if bar["date"] > cutoff:
                for key in ("open", "high", "low", "close"): bar[key] *= 10
                bar["volume"] = 1
                bar["dividend"] = 0
                bar["adj_close"] = -1e9
        replay = run_backtest(poisoned, cfg)
        assert_prefix(original, replay, cutoff)
        checks.append({"experiment": "poison_future_tail", "cutoff": cutoff, "status": "passed"})
    toy, toy_cfg = sample()
    before_legacy = legacy(toy, toy_cfg)["trades"][0]
    change_price(toy["bars"][6], open=20)
    after_legacy = legacy(toy, toy_cfg)["trades"][0]
    assert (before_legacy["quantity"], after_legacy["quantity"]) == (71, 50)
    before_new = run_backtest(sample()[0], replace(toy_cfg, allocation=.5))
    after_new = run_backtest(toy, replace(toy_cfg, allocation=.5))
    assert intents(before_new, "2020-01-06") == intents(after_new, "2020-01-06")
    regression = {"signal_close": 12, "next_open_before": 14, "next_open_after": 20,
        "legacy_quantity_before": 71, "legacy_quantity_after": 50,
        "fixed_order_quantity_before": before_new["orders"][0]["quantity"],
        "fixed_order_quantity_after": after_new["orders"][0]["quantity"],
        "new_fixture_allocation": .5,
        "meaning": "Changing tomorrow's open alters legacy sizing, but not today's new submitted quantity. Absolute quantities across versions are not compared because allocation differs."}
    report = {"status": "passed", "snapshot_sha256": sha((ROOT/"data/spy.json").read_bytes()),
        "script_sha256": sha(Path(__file__).read_bytes()), "config": cfg.to_dict(),
        "engine": original["engine"], "checks": checks, "regression": regression,
        "limits": "Evidence for this fixed strategy and data path; does not establish vendor point-in-time data, absence of overfitting, or hostile-code isolation."}
    write_new(output, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__": main()
