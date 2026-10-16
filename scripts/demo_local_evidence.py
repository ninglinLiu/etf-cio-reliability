"""Reproduce local evidence preconditions in a NEW isolated directory.

This deliberately does not claim any CIO state transitions or downstream checks.
"""
import argparse
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from fixtures import sample
from etf_lab.artifacts import EvidenceError, execute, inputs, read_json, verify, write_new


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Use a new isolated output directory; history is never overwritten")
    snapshot, cfg = sample()
    source = args.output / "fixtures/spy.json"
    write_new(source, snapshot)
    write_new(args.output / "fixtures/config.json", cfg.to_dict())
    records = []

    def rejected(label, action):
        try: action()
        except EvidenceError as exc:
            records.append({"case":label,"assertion":"PASS_LOCAL_ONLY","refusal":str(exc)})
            return
        raise AssertionError(label + " was not rejected")

    def expected(path, config):
        frozen = inputs(path, config)[0]
        frozen["simulated_fault"] = None
        return frozen

    before = execute(source, cfg, "original", args.output)
    assert before["status"] == "succeeded"
    original_dir = args.output / "runs/original"
    original_bytes = (original_dir / "result.json").read_bytes()
    for name, altered in (("parameter", replace(cfg, fast=1)), ("fees", replace(cfg, fee_fixed=1))):
        rejected("scenario_1_"+name, lambda:verify(original_dir, expected(source, altered)))
        after = execute(source, altered, name, args.output)
        assert after["input_id"] != before["input_id"] and after["status"] == "succeeded"
        records.append({"case":name+"_rerun","old_input_id":before["input_id"],"new_input_id":after["input_id"],"new_result_id":after["result_id"]})
    for name in ("snapshot", "provider"):
        altered = deepcopy(snapshot)
        if name == "snapshot": altered["bars"][0]["volume"] += 1
        else: altered["source"] = "SIMULATED_OTHER_PROVIDER"
        path = args.output / f"fixtures/{name}.json"
        write_new(path,altered)
        rejected("scenario_1_"+name, lambda:verify(original_dir, expected(path,cfg)))
        after = execute(path,cfg,name,args.output)
        assert after["input_id"] != before["input_id"] and after["status"] == "succeeded"
        records.append({"case":name+"_rerun","same_url":altered["source_url"]==snapshot["source_url"],
                        "old_input_id":before["input_id"],"new_input_id":after["input_id"],"new_result_id":after["result_id"]})
    assert (original_dir/"result.json").read_bytes() == original_bytes
    verify(original_dir)
    for fault in ("data", "compute"):
        failed = execute(source,cfg,"failure-"+fault,args.output,fault)
        assert failed["status"] == "failed" and failed["simulated"]
        assert not (args.output/f"runs/failure-{fault}/result.json").exists()
        rejected("scenario_2_failed_"+fault,lambda:verify(args.output/f"runs/failure-{fault}"))
        recovered = execute(source,cfg,"recovery-"+fault,args.output)
        assert recovered["status"] == "succeeded"
        assert read_json(args.output/f"runs/failure-{fault}/receipt.json")["status"] == "failed"
        records.append({"case":"scenario_2_recovery_"+fault,"failure":failed,"recovery":recovered})
    first = execute(source,cfg,"repeat",args.output)
    second = execute(source,cfg,"repeat",args.output)
    assert second["reused"] and first["result_id"] == second["result_id"]
    assert len(list((args.output/"runs/repeat").glob("result.json"))) == 1
    records.append({"case":"scenario_3_idempotency","business_results_for_key":1,"calls_for_key":2,"second_action":"reused_succeeded"})
    rejected("scenario_3_key_conflict",lambda:execute(source,replace(cfg,fee_fixed=2),"repeat",args.output))
    fresh = execute(source,cfg,"explicit-recompute",args.output)
    assert fresh["result_id"] == first["result_id"] and fresh["run_id"] != first["run_id"]
    tampered = args.output/"tampered-copy/original"
    shutil.copytree(original_dir,tampered)
    (tampered/"result.json").write_text('{"tampered":true}',encoding="utf-8")
    rejected("scenario_3_integrity",lambda:verify(tampered))
    verify(original_dir)
    summary = {"scope":"Local backtest artifact preconditions only; CIO NOT integrated", "simulated_data":True,
               "status":"passed_local_prerequisites", "cio_acceptance":"PENDING_ORIGINAL_CIO_REPOSITORY",
               "records":records, "successful_business_results":len(list((args.output/"runs").glob("*/result.json"))),
               "failed_runs":2, "history_preserved":True}
    write_new(args.output/"summary.json",summary)
    import json
    print(json.dumps(summary,ensure_ascii=True,indent=2))


if __name__ == "__main__": main()
