"""Isolated research evidence contract. No scheduler, investment rules or orders."""
from pathlib import Path
import re

from .artifacts import (EvidenceError, code_identity, encoded, frozen_inputs,
                        log_call, read_json, sha, timestamp, verify, write_new)
from .engine import Config


def checked_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise EvidenceError("INVALID_RESEARCH_ID")
    return value


def seal(directory):
    write_new(directory / "integrity.json", {"algorithm": "sha256", "files": {
        p.name: sha(p.read_bytes()) for p in directory.iterdir() if p.is_file()}})


def check_seal(directory, required):
    try:
        manifest = read_json(directory / "integrity.json")
        if manifest.get("algorithm") != "sha256" or set(manifest["files"]) != set(required):
            raise EvidenceError("RESEARCH_INTEGRITY_SCHEMA")
        if {p.name for p in directory.iterdir()} != set(required) | {"integrity.json"}:
            raise EvidenceError("RESEARCH_ARTIFACT_SET_MISMATCH")
        for name in required:
            p = directory / name
            if p.is_symlink() or not p.is_file() or sha(p.read_bytes()) != manifest["files"][name]:
                raise EvidenceError("RESEARCH_INTEGRITY_MISMATCH")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, EvidenceError):
            raise
        raise EvidenceError("RESEARCH_EVIDENCE_UNAVAILABLE") from None


def freeze_case(source: Path, cfg: Config, case_id: str, output: Path):
    checked_id(case_id)
    raw = source.read_bytes()
    frozen, _ = frozen_inputs(raw, cfg)
    frozen["simulated_fault"] = None
    case = {"schema": "research-case-v1", "case_id": case_id,
            "input_id": sha(encoded(frozen)), "input": frozen,
            "scope": "isolated_research_only", "cio_integrated": False}
    folder = output / "cases" / case_id
    if folder.exists():
        previous = load_case(output, case_id)
        if previous != case:
            raise EvidenceError("FROZEN_CASE_CONFLICT: create a new case")
        return {"status": "succeeded", "case_id": case_id, "input_id": case["input_id"], "reused": True}
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        folder.mkdir()
    except FileExistsError:
        raise EvidenceError("CASE_IN_PROGRESS") from None
    write_new(folder / "case.json", case)
    with (folder / "snapshot.json").open("xb") as stream:
        stream.write(raw)
    seal(folder)
    return {"status": "succeeded", "case_id": case_id, "input_id": case["input_id"]}


def load_case(output: Path, case_id: str):
    folder = output / "cases" / checked_id(case_id)
    check_seal(folder, {"case.json", "snapshot.json"})
    case = read_json(folder / "case.json")
    try:
        if case["schema"] != "research-case-v1" or case["case_id"] != case_id:
            raise EvidenceError("CASE_IDENTITY_MISMATCH")
        if case["input_id"] != sha(encoded(case["input"])):
            raise EvidenceError("CASE_INPUT_MISMATCH")
        if case["input"]["snapshot_sha256"] != sha((folder / "snapshot.json").read_bytes()):
            raise EvidenceError("CASE_SNAPSHOT_MISMATCH")
    except (KeyError, TypeError):
        raise EvidenceError("INVALID_CASE_SCHEMA") from None
    return case


def gate(output: Path, case_id: str, run_id: str):
    case = load_case(output, case_id)
    # Old cases remain readable via load_case, but cannot be used under new code.
    if case["input"]["code"] != code_identity():
        raise EvidenceError("CASE_CODE_STALE: freeze a new case and recompute")
    folder = output / "runs" / checked_id(run_id)
    receipt = verify(folder)  # Failure is reported before any input mismatch.
    verify(folder, case["input"])
    return case, receipt


def submit(output: Path, submission_id: str, case_id: str, run_id: str):
    for value in (submission_id, case_id, run_id):
        checked_id(value)
    request = {"schema": "research-submission-v1", "submission_id": submission_id,
               "case_id": case_id, "run_id": run_id}
    request_id = sha(encoded(request))
    folder = output / "submissions" / submission_id
    if folder.exists():
        check_seal(folder, {"request.json", "receipt.json"})
        if read_json(folder / "request.json") != request:
            log_call(output, submission_id, "research_idempotency_conflict", request_id)
            raise EvidenceError("SUBMISSION_IDEMPOTENCY_CONFLICT")
        previous = read_json(folder / "receipt.json")
        # Reusing an acceptance must never bypass current integrity checks.
        if previous["status"] == "succeeded":
            consume(output, submission_id, case_id)
        log_call(output, submission_id, "research_reused_" + previous["status"], request_id)
        return {**previous, "reused": True}
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        folder.mkdir()
    except FileExistsError:
        raise EvidenceError("SUBMISSION_IN_PROGRESS") from None
    write_new(folder / "request.json", request)
    receipt = {**request, "request_id": request_id, "created_at": timestamp(),
               "scope": "isolated_research_only", "cio_integrated": False}
    try:
        case, result = gate(output, case_id, run_id)
        receipt.update(status="succeeded", input_id=case["input_id"], result_id=result["result_id"],
                       downstream_eligible=True)
    except EvidenceError as exc:
        receipt.update(status="rejected", reason=str(exc), downstream_eligible=False)
    except (OSError, ValueError, TypeError, KeyError):
        receipt.update(status="rejected", reason="INVALID_RESEARCH_EVIDENCE", downstream_eligible=False)
    write_new(folder / "receipt.json", receipt)
    seal(folder)
    log_call(output, submission_id, "research_" + receipt["status"], request_id)
    return receipt


def consume(output: Path, submission_id: str, current_case_id: str):
    folder = output / "submissions" / checked_id(submission_id)
    checked_id(current_case_id)
    check_seal(folder, {"request.json", "receipt.json"})
    receipt = read_json(folder / "receipt.json")
    request = read_json(folder / "request.json")
    if (receipt.get("request_id") != sha(encoded(request)) or
            any(receipt.get(k) != v for k, v in request.items())):
        raise EvidenceError("SUBMISSION_BINDING_MISMATCH")
    if receipt.get("status") != "succeeded":
        raise EvidenceError("SUBMISSION_NOT_SUCCEEDED")
    if request["case_id"] != current_case_id:
        log_call(output, submission_id, "research_read_rejected_current_case", receipt["request_id"])
        raise EvidenceError("CURRENT_CASE_MISMATCH: historical acceptance is not current")
    try:
        case, result = gate(output, current_case_id, request["run_id"])
        if receipt.get("input_id") != case["input_id"] or receipt.get("result_id") != result["result_id"]:
            raise EvidenceError("ACCEPTED_RESULT_BINDING_MISMATCH")
    except EvidenceError:
        log_call(output, submission_id, "research_read_rejected_evidence", receipt["request_id"])
        raise
    log_call(output, submission_id, "research_read_verified", receipt["request_id"])
    return {**receipt, "verified_at": timestamp(), "check_scope": "current_case_and_live_integrity"}
