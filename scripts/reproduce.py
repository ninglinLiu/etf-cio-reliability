"""Offline end-to-end reproduction; records actual process exit codes and output."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    if output.exists(): raise SystemExit("Use a new directory; never overwrite evidence")
    output.mkdir(parents=True)
    checks = []

    def run(name, command, expected=0):
        process = subprocess.run([sys.executable, *command], cwd=ROOT, capture_output=True)
        (output / (name + ".stdout.txt")).write_bytes(process.stdout)
        (output / (name + ".stderr.txt")).write_bytes(process.stderr)
        item = {"check": name, "command": ["python", *command], "exit_code": process.returncode,
                "expected_exit_code": expected, "passed": process.returncode == expected}
        checks.append(item)
        print(json.dumps(item), flush=True)
        if not item["passed"]: raise RuntimeError(name + " failed; inspect saved output")

    try:
        run("tests", ["-m", "unittest", "discover", "-s", "tests", "-v"])
        market = output / "market"
        run("backtest", ["-m", "etf_lab", "run", "--snapshot", "data/spy.json", "--config", "config/spy.json",
                         "--run-id", "offline-reproduction", "--output", str(market)])
        run("verify", ["-m", "etf_lab", "verify", "--run", str(market / "runs/offline-reproduction"),
                       "--snapshot", "data/spy.json", "--config", "config/spy.json"])
        run("audit", ["scripts/audit_result.py", str(market / "runs/offline-reproduction")])
        run("causality", ["scripts/audit_causality.py", "--output", str(output / "causality.json")])
        run("existing-comparison", ["scripts/compare_existing.py", "--output", str(output / "existing-comparison")])
        run("institution", ["scripts/replicate_paired.py", "--output", str(output / "institution")])
        isolated = output / "isolated-reliability"
        run("three-scenarios", ["scripts/demo_reliability.py", "--output", str(isolated)])
        common = ["-m", "etf_lab", "research-read", "--output", str(isolated), "--submission-id", "original"]
        run("consumer-success", common + ["--case-id", "baseline"])
        run("consumer-stale", common + ["--case-id", "fees"], expected=2)
        run("consumer-failed", ["-m", "etf_lab", "research-read", "--output", str(isolated),
                                "--submission-id", "failed-data", "--case-id", "baseline"], expected=2)
        run("consumer-tampered", ["-m", "etf_lab", "research-read", "--output", str(isolated/"tampered-copy"),
                                  "--submission-id", "original", "--case-id", "baseline"], expected=2)
    finally:
        (output / "reproduction.json").write_text(json.dumps({"checks": checks,
             "passed": len(checks) == 12 and all(x["passed"] for x in checks)}, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__": main()
