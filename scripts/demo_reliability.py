"""Executable three-scenario acceptance for the user-approved independent scope."""
import argparse
from copy import deepcopy
from dataclasses import replace
import html
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from fixtures import sample
from etf_lab.artifacts import EvidenceError, execute, read_json, write_new
from etf_lab.handoff import consume, freeze_case, submit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    if output.exists(): raise SystemExit("Choose a NEW isolated directory; never overwrite evidence")
    snapshot, cfg = sample()
    cfg = replace(cfg, allocation=.5)  # Fixture gaps are large; exercise actual fills too.
    source = output / "fixtures/spy.json"
    write_new(source, snapshot)
    write_new(output / "fixtures/config.json", cfg.to_dict())
    records = []

    def rejection(label, action, code):
        try: action()
        except EvidenceError as exc:
            assert code in str(exc), str(exc)
            records.append({"scenario": label, "status": "PASS", "rejection": str(exc)})
        else: raise AssertionError(label + " unexpectedly accepted")

    freeze_case(source, cfg, "baseline", output)
    old = execute(source, cfg, "original", output)
    accepted = submit(output, "original", "baseline", "original")
    assert accepted["status"] == consume(output, "original", "baseline")["status"] == "succeeded"
    original_bytes = (output / "runs/original/result.json").read_bytes()
    variants = [("parameters", source, replace(cfg, fast=1)), ("fees", source, replace(cfg, fee_fixed=1))]
    for name in ("data", "source"):
        altered = deepcopy(snapshot)
        if name == "data": altered["bars"][0]["volume"] += 1
        else: altered["source"] = "SIMULATED_OTHER_PROVIDER"
        path = output / f"fixtures/{name}.json"
        write_new(path, altered)
        assert altered["source_url"] == snapshot["source_url"]
        variants.append((name, path, cfg))
    for name, path, config in variants:
        freeze_case(path, config, name, output)
        stale = submit(output, "stale-" + name, name, "original")
        assert stale["status"] == "rejected" and not stale["downstream_eligible"]
        assert "INPUT_MISMATCH" in stale["reason"]
        rejection("1/read/" + name, lambda: consume(output, "original", name), "CURRENT_CASE_MISMATCH")
        new = execute(path, config, name, output)
        assert new["input_id"] != old["input_id"]
        assert submit(output, name, name, name)["status"] == "succeeded"
        assert consume(output, name, name)["downstream_eligible"]
        records.append({"scenario": "1/submit-and-recover/" + name, "status": "PASS",
                        "old_input_id": old["input_id"], "new_input_id": new["input_id"],
                        "new_result_id": new["result_id"], "rejection": stale["reason"]})
    assert original_bytes == (output / "runs/original/result.json").read_bytes()
    for fault in ("data", "compute"):
        failed = execute(source, cfg, "failed-" + fault, output, fault)
        assert failed["simulated"] and failed["status"] == "failed" and "result_id" not in failed
        refused = submit(output, "failed-" + fault, "baseline", "failed-" + fault)
        assert refused["status"] == "rejected" and "result_id" not in refused
        rejection("2/read/" + fault, lambda: consume(output, "failed-" + fault, "baseline"), "SUBMISSION_NOT_SUCCEEDED")
        recovery = execute(source, cfg, "recovered-" + fault, output)
        assert submit(output, "recovered-" + fault, "baseline", "recovered-" + fault)["status"] == "succeeded"
        assert consume(output, "recovered-" + fault, "baseline")["status"] == "succeeded"
        assert read_json(output / f"runs/failed-{fault}/receipt.json")["status"] == "failed"
        records.append({"scenario": "2/failure-and-recovery/" + fault, "status": "PASS", "failure": failed,
                        "refusal": refused["reason"], "recovery_run_id": recovery["run_id"]})
    repeated = execute(source, cfg, "original", output)
    duplicate = submit(output, "original", "baseline", "original")
    assert repeated["reused"] and duplicate["reused"]
    assert len(list((output / "submissions/original").glob("receipt.json"))) == 1
    rejection("3/submission-conflict", lambda: submit(output, "original", "fees", "fees"), "IDEMPOTENCY_CONFLICT")
    rejection("3/run-conflict", lambda: execute(source, replace(cfg, fee_fixed=1), "original", output), "INPUT_MISMATCH")
    rerun = execute(source, cfg, "explicit-recompute", output)
    assert rerun["result_id"] == old["result_id"]
    assert submit(output, "explicit-recompute", "baseline", "explicit-recompute")["status"] == "succeeded"
    records.append({"scenario": "3/idempotency", "status": "PASS", "valid_results_for_original_key": 1,
                    "valid_handoffs_for_original_key": 1, "second_action": "reused_succeeded",
                    "recompute_run_id": rerun["run_id"]})
    tampered = output / "tampered-copy"
    tampered.mkdir()
    for part in ("runs", "cases", "submissions"):
        shutil.copytree(output / part, tampered / part)
    (tampered / "runs/original/result.json").write_text('{"tampered":true}', encoding="utf-8")
    rejection("3/consume-tampered", lambda: consume(tampered, "original", "baseline"), "INTEGRITY_MISMATCH")
    rejection("3/reuse-tampered", lambda: submit(tampered, "original", "baseline", "original"), "INTEGRITY_MISMATCH")
    assert consume(output, "original", "baseline")["status"] == "succeeded"
    receipts = [read_json(p) for p in (output / "submissions").glob("*/receipt.json")]
    summary = {"scope": "User-approved independent research reliability acceptance; no existing CIO integrated",
               "status": "passed", "synthetic_data": True, "scenario_count": 3,
               "assertion_records": len(records), "records": records,
               "accepted_handoffs": sum(r["status"] == "succeeded" for r in receipts),
               "rejected_handoffs": sum(r["status"] == "rejected" for r in receipts),
               "failed_runs": 2, "original_history_preserved": True}
    write_new(output / "summary.json", summary)
    cards = "".join('<article><b>' + html.escape(r["scenario"]) + '</b><span>PASS</span><pre>' +
                    html.escape(json.dumps(r, ensure_ascii=False, indent=2)) + '</pre></article>' for r in records)
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>研究结果可靠性验收</title>
<style>body{font:16px system-ui;background:#edf3f2;color:#152c2b;margin:36px auto;max-width:1100px}h1{font-size:34px}
.banner,article{background:white;padding:22px;border-radius:10px;margin:18px 0}span{float:right;color:#087a48;font-weight:bold}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px monospace;color:#374c51}a{color:#086b62}</style>
<h1>研究结果可靠性 · 三场景验收</h1><div class="banner"><strong>3 / 3 场景通过 · 隔离合成数据</strong>
<p>按用户确认的独立验收范围：提交与读取都会重新校验。没有接入现有 CIO，也不连接投资审批或交易。</p>
<p>参数、数据、来源、费用改变 → 拒用历史结果；模拟失败 → 阻断交接 → 新运行恢复；重复复用、冲突拒绝、篡改发现。</p>
<a href="summary.json">完整断言与标识 JSON</a> · <a href="calls.jsonl">实际调用日志</a></div>''' + cards
    (output / "report.html").write_text(page + '</html>', encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__": main()
