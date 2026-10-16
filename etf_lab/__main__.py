import argparse
import json
from pathlib import Path
import sys

from .artifacts import EvidenceError, execute, inputs, read_json, timestamp, verify, write_new
from .engine import Config, DataError


def main():
    parser = argparse.ArgumentParser(description="Single-ETF local research; no CIO or trading execution")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--start", required=True)
    fetch.add_argument("--end", required=True, help="exclusive")
    fetch.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--snapshot", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--output", type=Path, default=Path("output"))
    run.add_argument("--simulate-failure", choices=["data", "compute"])
    check = sub.add_parser("verify")
    check.add_argument("--run", type=Path, required=True)
    check.add_argument("--snapshot", type=Path)
    check.add_argument("--config", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "fetch":
            from .data import download_spy
            if args.output.exists():
                raise EvidenceError("SNAPSHOT_EXISTS: choose a new output path")
            try:
                snapshot = download_spy(args.start, args.end, Path(".cache/yfinance"))
                write_new(args.output, snapshot)
            except Exception as exc:
                failure = {"status": "failed", "stage": "download", "simulated": False,
                           "reason": "Unable to obtain and validate requested yfinance snapshot; no success snapshot created",
                           "exception_type": type(exc).__name__, "time": timestamp()}
                import uuid
                write_new(args.output.parent / ("download-failed-" + uuid.uuid4().hex[:12] + ".json"), failure)
                print(json.dumps(failure)); return 1
            result = {"status": "succeeded", "snapshot": str(args.output), "bars": len(snapshot["bars"])}
        elif args.command == "run":
            result = execute(args.snapshot, Config(**read_json(args.config)), args.run_id, args.output, args.simulate_failure)
        else:
            if bool(args.snapshot) != bool(args.config):
                raise EvidenceError("EXPECTED_INPUT_REQUIRES_BOTH_SNAPSHOT_AND_CONFIG")
            expected = inputs(args.snapshot, Config(**read_json(args.config)))[0] if args.snapshot else None
            if expected is not None: expected["simulated_fault"] = None
            result = verify(args.run, expected)
            result = {**result, "verified": True, "check_scope": "current_input_match" if expected else "historical_integrity_only"}
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result.get("status") == "succeeded" else 1
    except (EvidenceError, DataError, ValueError, OSError, TypeError) as exc:
        # Only our fixed EvidenceError codes are printable; arbitrary input or
        # provider exception messages may contain secrets and are withheld.
        reason = str(exc) if isinstance(exc, EvidenceError) else "INVALID_OR_UNAVAILABLE_INPUT; inspect the supplied files"
        print(json.dumps({"status": "rejected", "reason": reason, "exception_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
