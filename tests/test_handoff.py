from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from etf_lab.artifacts import EvidenceError, execute, read_json, write_new
from etf_lab.handoff import consume, freeze_case, load_case, submit
from fixtures import sample
from test_artifacts import lightweight_report


class ResearchContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.snapshot, self.cfg = sample()
        self.source = self.root / "source.json"
        write_new(self.source, self.snapshot)
        self.renderer = patch("etf_lab.report.render", lightweight_report)
        self.renderer.start()
        freeze_case(self.source, self.cfg, "v1", self.root)
        execute(self.source, self.cfg, "run1", self.root)
        self.first = submit(self.root, "first", "v1", "run1")

    def tearDown(self):
        self.renderer.stop()
        self.temp.cleanup()

    def test_all_input_changes_block_submission_and_consumption(self):
        original = (self.root / "runs/run1/result.json").read_bytes()
        variants = [("parameter", self.source, replace(self.cfg, fast=1)),
                    ("fee", self.source, replace(self.cfg, fee_fixed=1))]
        for name in ("snapshot", "provider"):
            altered = deepcopy(self.snapshot)
            if name == "snapshot": altered["bars"][0]["volume"] += 1
            else: altered["source"] = "SIMULATED_OTHER_SOURCE"
            path = self.root / (name + ".json")
            write_new(path, altered)
            variants.append((name, path, self.cfg))
        for name, source, cfg in variants:
            with self.subTest(name=name):
                freeze_case(source, cfg, name, self.root)
                rejected = submit(self.root, "stale-" + name, name, "run1")
                self.assertEqual(rejected["status"], "rejected")
                self.assertIn("INPUT_MISMATCH", rejected["reason"])
                self.assertFalse(rejected["downstream_eligible"])
                with self.assertRaisesRegex(EvidenceError, "CURRENT_CASE_MISMATCH"):
                    consume(self.root, "first", name)
                execute(source, cfg, name, self.root)
                self.assertEqual(submit(self.root, "new-" + name, name, name)["status"], "succeeded")
                self.assertEqual(consume(self.root, "new-" + name, name)["status"], "succeeded")
        self.assertEqual(original, (self.root / "runs/run1/result.json").read_bytes())
        self.assertEqual(load_case(self.root, "v1")["case_id"], "v1")

    def test_failure_never_advances_or_falls_back_and_recovery_preserves_failure(self):
        for fault in ("data", "compute"):
            failed = execute(self.source, self.cfg, fault, self.root, fault)
            self.assertEqual(failed["status"], "failed")
            rejected = submit(self.root, fault, "v1", fault)
            self.assertIn("RUN_NOT_SUCCEEDED", rejected["reason"])
            self.assertNotIn("result_id", rejected)
            with self.assertRaisesRegex(EvidenceError, "SUBMISSION_NOT_SUCCEEDED"):
                consume(self.root, fault, "v1")
            execute(self.source, self.cfg, fault + "-recovered", self.root)
            accepted = submit(self.root, fault + "-recovered", "v1", fault + "-recovered")
            self.assertEqual(accepted["status"], "succeeded")
            self.assertEqual(read_json(self.root / f"submissions/{fault}/receipt.json")["status"], "rejected")

    def test_idempotency_one_acceptance_and_different_request_conflicts(self):
        second = submit(self.root, "first", "v1", "run1")
        self.assertTrue(second["reused"])
        self.assertEqual(second["result_id"], self.first["result_id"])
        self.assertEqual(len(list((self.root / "submissions").glob("*/receipt.json"))), 1)
        execute(self.source, self.cfg, "forced-new-run", self.root)
        with self.assertRaisesRegex(EvidenceError, "SUBMISSION_IDEMPOTENCY_CONFLICT"):
            submit(self.root, "first", "v1", "forced-new-run")
        self.assertEqual(submit(self.root, "forced-new-submission", "v1", "forced-new-run")["status"], "succeeded")

    def test_cached_acceptance_does_not_bypass_tamper_check(self):
        result = self.root / "runs/run1/result.json"
        result.write_text('{"tampered":true}', encoding="utf-8")
        for action in (lambda: consume(self.root, "first", "v1"),
                       lambda: submit(self.root, "first", "v1", "run1")):
            with self.assertRaisesRegex(EvidenceError, "INTEGRITY_MISMATCH"):
                action()

    def test_frozen_case_cannot_be_overwritten(self):
        with self.assertRaisesRegex(EvidenceError, "FROZEN_CASE_CONFLICT"):
            freeze_case(self.source, replace(self.cfg, fast=1), "v1", self.root)
        self.assertTrue(freeze_case(self.source, self.cfg, "v1", self.root)["reused"])

    def test_code_changes_reject_current_use_but_preserve_history(self):
        with patch("etf_lab.handoff.code_identity", return_value={"changed": True}):
            with self.assertRaisesRegex(EvidenceError, "CASE_CODE_STALE"):
                consume(self.root, "first", "v1")
            self.assertEqual(load_case(self.root, "v1")["case_id"], "v1")

    def test_case_and_submission_corruption_rejected(self):
        (self.root / "cases/v1/case.json").write_text('{}')
        with self.assertRaisesRegex(EvidenceError, "RESEARCH_INTEGRITY_MISMATCH"):
            consume(self.root, "first", "v1")

    def test_invalid_identifier_rejected(self):
        with self.assertRaisesRegex(EvidenceError, "INVALID_RESEARCH_ID"):
            submit(self.root, "../escape", "v1", "run1")
