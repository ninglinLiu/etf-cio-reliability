from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import subprocess

from .engine import Config, DataError, run_backtest, simplified_reference

ROOT = Path(__file__).resolve().parents[1]


class EvidenceError(ValueError):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha(raw: bytes):
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_new(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        f.write(encoded(value))


def code_identity():
    files = list((ROOT / "etf_lab").glob("*.py")) + [ROOT / "pyproject.toml", ROOT / "requirements.lock.txt"]
    hashes = {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in files if p.exists()}
    try:
        # Evidence-only or documentation commits must not spuriously change the
        # calculation revision. Actual source edits still change the file digest.
        revision = subprocess.check_output(["git", "log", "-1", "--format=%H", "--", "etf_lab", "pyproject.toml", "requirements.lock.txt"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip() or "uncommitted"
    except (OSError, subprocess.CalledProcessError):
        revision = "uncommitted"
    versions = {}
    for name in ("yfinance", "matplotlib", "exchange-calendars", "numpy", "pandas"):
        try: versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: versions[name] = "not-installed"
    return {"calculation_commit": revision, "file_hashes": hashes, "source_digest": sha(encoded(hashes)),
            "python": platform.python_version(), "dependencies": versions}


def inputs(snapshot_path: Path, cfg: Config):
    return frozen_inputs(snapshot_path.read_bytes(), cfg)


def frozen_inputs(raw: bytes, cfg: Config):
    snapshot = json.loads(raw)
    manifest = {"schema": "etf-input-v1", "strategy": cfg.to_dict(), "snapshot_sha256": sha(raw),
                "source": snapshot.get("source"), "source_url": snapshot.get("source_url"),
                "data_cutoff": snapshot.get("data_cutoff"), "observed_at": snapshot.get("observed_at"),
                "price_basis": snapshot.get("price_basis"), "code": code_identity()}
    return manifest, snapshot


def verify(run_dir: Path, expected: dict | None = None, require_success=True):
    try:
        integrity = read_json(run_dir / "integrity.json")
        allowed_files = {"input.json", "snapshot.json", "receipt.json", "result.json", "report.html", "nav.png"}
        if not isinstance(integrity, dict) or integrity.get("algorithm") != "sha256" or not isinstance(integrity.get("files"), dict):
            raise EvidenceError("INVALID_INTEGRITY_SCHEMA")
        if not {"input.json", "snapshot.json", "receipt.json"}.issubset(integrity["files"]) or set(integrity["files"]) - allowed_files:
            raise EvidenceError("INVALID_ARTIFACT_NAMES")
        actual = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file() and p.name != "integrity.json"}
        if actual != set(integrity["files"]):
            raise EvidenceError("ARTIFACT_SET_MISMATCH")
        for name, recorded in integrity["files"].items():
            path = run_dir / name
            if path.resolve().parent != run_dir.resolve() or path.is_symlink():
                raise EvidenceError("UNSAFE_ARTIFACT_PATH")
            if sha(path.read_bytes()) != recorded:
                raise EvidenceError("INTEGRITY_MISMATCH: " + name)
        frozen = read_json(run_dir / "input.json")
        if sha((run_dir / "snapshot.json").read_bytes()) != frozen["snapshot_sha256"]:
            raise EvidenceError("SNAPSHOT_BINDING_MISMATCH")
        receipt = read_json(run_dir / "receipt.json")
        if receipt["input_id"] != sha(encoded(frozen)):
            raise EvidenceError("INPUT_BINDING_MISMATCH")
        if expected is not None and frozen != expected:
            raise EvidenceError("INPUT_MISMATCH: result remains historical evidence only")
        if receipt["status"] == "succeeded":
            if not all((run_dir / name).is_file() for name in ("result.json", "report.html", "nav.png")):
                raise EvidenceError("SUCCESS_ARTIFACT_MISSING")
            if receipt["result_id"] != sha(encoded(read_json(run_dir / "result.json"))):
                raise EvidenceError("RESULT_BINDING_MISMATCH")
        elif require_success:
            raise EvidenceError("RUN_NOT_SUCCEEDED: " + receipt.get("error_code", receipt["status"]))
        return receipt
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise EvidenceError("MISSING_OR_INVALID_EVIDENCE") from None


def log_call(output: Path, run_id: str, action: str, input_id: str):
    # Invocation audit is outside immutable result directories and is not
    # presented as administrator-proof. No request bodies or exception text.
    output.mkdir(parents=True, exist_ok=True)
    with (output / "calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": timestamp(), "run_id": run_id, "action": action, "input_id": input_id}) + "\n")


def execute(snapshot_path: Path, cfg: Config, run_id: str, output: Path, fault: str | None = None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
        raise EvidenceError("INVALID_RUN_ID")
    if fault not in (None, "data", "compute"):
        raise EvidenceError("INVALID_SIMULATED_FAULT")
    snapshot_raw = snapshot_path.read_bytes()
    frozen, snapshot = frozen_inputs(snapshot_raw, cfg)
    frozen["simulated_fault"] = fault
    input_id = sha(encoded(frozen))
    run_dir = output / "runs" / run_id
    if run_dir.exists():
        try:
            receipt = verify(run_dir, frozen, require_success=False)
        except EvidenceError:
            log_call(output, run_id, "duplicate_rejected_integrity_or_input_conflict", input_id)
            raise
        log_call(output, run_id, "reused_" + receipt["status"], input_id)
        return {**receipt, "reused": True}
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_dir.mkdir()  # Exclusive directory claim; never overwrite history.
    except FileExistsError:
        raise EvidenceError("RUN_IN_PROGRESS: retry after completion") from None
    write_new(run_dir / "input.json", frozen)
    with (run_dir / "snapshot.json").open("xb") as f:
        f.write(snapshot_raw)
    stage = "data_validation"
    receipt = {"schema": "etf-result-receipt-v1", "run_id": run_id, "input_id": input_id,
               "created_at": timestamp(), "simulated": fault is not None,
               "scope": "local_research_only", "cio_integration": "pending_original_repository"}
    try:
        if fault == "data":
            raise DataError("Simulated data source outage")
        from .engine import validate
        validate(snapshot, cfg)
        stage = "calculation"
        if fault == "compute":
            raise ArithmeticError("Simulated arithmetic failure")
        result = run_backtest(snapshot, cfg)
        result["simplified_reference"] = simplified_reference(snapshot, cfg)
        result["acceptance"] = {"existing_algorithm_comparison": "PENDING_ORIGINAL_ALGORITHM",
                                "institutional_replication": "PENDING_COMPARABLE_PRIMARY_BENCHMARK",
                                "cio_scenarios": "PENDING_ORIGINAL_CIO_CONTRACT"}
        stage = "report"
        from .report import render
        render(run_dir, result, cfg, snapshot, receipt)
        write_new(run_dir / "result.json", result)
        receipt.update(status="succeeded", result_id=sha(encoded(result)))
    except Exception as exc:
        code = {"data_validation": "DATA_UNAVAILABLE_OR_INVALID", "calculation": "CALCULATION_FAILED", "report": "REPORT_FAILED"}[stage]
        receipt.update(status="failed", failed_stage=stage, error_code=code,
                       reason=("SIMULATED: " if fault else "") + {
                           "data_validation": "Data acquisition/validation did not produce usable input.",
                           "calculation": "Calculation did not complete; no successful result is available.",
                           "report": "Report generation did not complete; partial files are not valid evidence."}[stage],
                       exception_type=type(exc).__name__)
    write_new(run_dir / "receipt.json", receipt)
    hashes = {p.name: sha(p.read_bytes()) for p in run_dir.iterdir() if p.is_file()}
    write_new(run_dir / "integrity.json", {"algorithm": "sha256", "files": hashes,
                                          "limitation": "Integrity check, not administrator-proof signing"})
    log_call(output, run_id, receipt["status"], input_id)
    return receipt
