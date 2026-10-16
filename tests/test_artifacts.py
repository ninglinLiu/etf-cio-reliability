from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from etf_lab.artifacts import EvidenceError, execute, inputs, read_json, verify, write_new
from fixtures import sample


def lightweight_report(directory, *args):
    # Test-only substitute for a costly renderer. Financial calculations are real.
    (directory / "report.html").write_text("<p>TEST RENDERER ONLY</p>")
    (directory / "nav.png").write_bytes(b"TEST-PNG-PLACEHOLDER")


class LocalArtifactPrerequisites(unittest.TestCase):
    """These assert local evidence behavior, NOT the missing CIO integration."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.snapshot, self.cfg = sample()
        self.source = self.root / "snapshot.json"
        write_new(self.source, self.snapshot)
        self.out = self.root / "output"
        self.render = patch("etf_lab.report.render", lightweight_report)
        self.render.start()

    def tearDown(self):
        self.render.stop()
        self.temp.cleanup()

    def expected(self, source=None, cfg=None, fault=None):
        frozen = inputs(source or self.source, cfg or self.cfg)[0]
        frozen["simulated_fault"] = fault
        return frozen

    def test_parameter_and_fee_changes_block_old_result_keep_history(self):
        old = execute(self.source, self.cfg, "v1", self.out)
        folder = self.out / "runs/v1"
        original_bytes = (folder / "result.json").read_bytes()
        for i, cfg in enumerate((replace(self.cfg, fast=1), replace(self.cfg, fee_fixed=1))):
            with self.assertRaisesRegex(EvidenceError,"INPUT_MISMATCH"):
                verify(folder, self.expected(cfg=cfg))
            new = execute(self.source, cfg, f"v{i+2}", self.out)
            self.assertNotEqual(old["input_id"], new["input_id"])
            self.assertEqual(verify(self.out/f"runs/v{i+2}", self.expected(cfg=cfg))["status"], "succeeded")
        self.assertEqual((folder/"result.json").read_bytes(), original_bytes)
        self.assertEqual(verify(folder)["status"], "succeeded")

    def test_same_url_changed_snapshot_or_source_blocks_old_result(self):
        old = execute(self.source, self.cfg, "v1", self.out)
        for name, mutate in (("data", lambda s:s["bars"][0].update(volume=2)),
                             ("provider", lambda s:s.update(source="SIMULATED_OTHER_PROVIDER"))):
            changed = deepcopy(self.snapshot)
            mutate(changed)
            path = self.root / (name + ".json")
            write_new(path, changed)
            with self.assertRaisesRegex(EvidenceError,"INPUT_MISMATCH"):
                verify(self.out/"runs/v1", self.expected(source=path))
            new = execute(path, self.cfg, name, self.out)
            self.assertNotEqual(old["input_id"], new["input_id"])

    def test_code_identity_changes_invalidate_current_eligibility(self):
        execute(self.source, self.cfg, "v1", self.out)
        with patch("etf_lab.artifacts.code_identity", return_value={"source_digest":"changed-code"}):
            with self.assertRaisesRegex(EvidenceError,"INPUT_MISMATCH"):
                verify(self.out/"runs/v1", self.expected())
        self.assertEqual(verify(self.out/"runs/v1")["status"], "succeeded")

    def test_simulated_failures_do_not_reuse_success_and_recovery_is_new_run(self):
        success = execute(self.source, self.cfg, "old-success", self.out)
        for fault in ("data", "compute"):
            failed = execute(self.source, self.cfg, "failure-"+fault, self.out, fault)
            folder = self.out / ("runs/failure-"+fault)
            self.assertEqual(failed["status"], "failed")
            self.assertTrue(failed["simulated"])
            self.assertTrue(failed["reason"].startswith("SIMULATED:"))
            self.assertNotIn("result_id", failed)
            self.assertFalse((folder/"result.json").exists())
            with self.assertRaisesRegex(EvidenceError,"RUN_NOT_SUCCEEDED"): verify(folder)
            recovered = execute(self.source, self.cfg, "recovery-"+fault, self.out)
            self.assertEqual(recovered["status"], "succeeded")
            self.assertEqual(recovered["result_id"], success["result_id"])
            self.assertTrue((folder/"receipt.json").exists())

    def test_same_id_same_input_reuses_one_business_result(self):
        first = execute(self.source, self.cfg, "repeat", self.out)
        second = execute(self.source, self.cfg, "repeat", self.out)
        self.assertTrue(second["reused"])
        self.assertEqual(first["result_id"], second["result_id"])
        self.assertEqual(len(list(self.out.glob("runs/*/result.json"))), 1)
        calls = (self.out/"calls.jsonl").read_text().splitlines()
        self.assertEqual(len(calls), 2)
        self.assertIn("reused_succeeded", calls[-1])

    def test_same_id_different_input_conflicts_without_overwrite(self):
        execute(self.source, self.cfg, "repeat", self.out)
        original = (self.out/"runs/repeat/input.json").read_bytes()
        with self.assertRaisesRegex(EvidenceError,"INPUT_MISMATCH"):
            execute(self.source, replace(self.cfg, fee_fixed=2), "repeat", self.out)
        self.assertEqual((self.out/"runs/repeat/input.json").read_bytes(), original)

    def test_tampering_with_result_is_rejected_and_not_reused(self):
        execute(self.source, self.cfg, "original", self.out)
        path = self.out/"runs/original/result.json"
        path.write_text('{"metrics":{"total_return":99}}')
        with self.assertRaisesRegex(EvidenceError,"INTEGRITY_MISMATCH"): verify(path.parent)
        with self.assertRaises(EvidenceError): execute(self.source, self.cfg, "original", self.out)

    def test_missing_evidence_or_incomplete_run_cannot_pass(self):
        execute(self.source, self.cfg, "original", self.out)
        (self.out/"runs/original/report.html").unlink()
        with self.assertRaisesRegex(EvidenceError,"ARTIFACT_SET_MISMATCH"): verify(self.out/"runs/original")
        (self.out/"runs/aborted").mkdir()
        with self.assertRaises(EvidenceError): execute(self.source, self.cfg, "aborted", self.out)

    def test_error_output_does_not_store_sensitive_exception_content(self):
        secret = "secret-test-value-abcdef"
        with patch("etf_lab.artifacts.run_backtest", side_effect=RuntimeError("https://example.test?api_key="+secret)):
            failed = execute(self.source, self.cfg, "secret-check", self.out)
        self.assertEqual(failed["failed_stage"], "calculation")
        for path in self.out.rglob("*"):
            if path.is_file(): self.assertNotIn(secret.encode(), path.read_bytes())

    def test_report_failure_is_failed_even_with_partial_chart(self):
        def fail_render(directory,*args):
            (directory/"nav.png").write_bytes(b"partial")
            raise RuntimeError("SIMULATED_RENDER_FAILURE")
        with patch("etf_lab.report.render", fail_render):
            failed = execute(self.source, self.cfg, "render-failure", self.out)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failed_stage"], "report")
        with self.assertRaises(EvidenceError): verify(self.out/"runs/render-failure")

    def test_path_traversal_run_id_rejected(self):
        with self.assertRaisesRegex(EvidenceError,"INVALID_RUN_ID"):
            execute(self.source, self.cfg, "../escape", self.out)


if __name__ == "__main__": unittest.main()
