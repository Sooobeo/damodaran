"""Focused transport tests with synthetic prior/native validator boundaries."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_transport_recovery_v3 as t
from input_execution_v1 import local_question_runner_recovery_v3 as runner
from input_execution_v1 import local_question_runner_inventory_v2 as inventory_runner
from input_execution_v1 import test_local_question_transport_inventory_v2 as support
from input_execution_v1.test_local_question_runner_recovery_v3 import recovery_fixture


def synthetic_recovery(root, inventory, export_ref):
    """Schema-shaped temporary evidence; not proof of a real zero-call run."""
    base = root / t.tr.BASE / "synthetic-zero-call-prior"
    refs = []
    for name in ("plan.json", "summary.json", "model-chat-template.jinja"):
        path = base / name
        t.ev.write_new(path, {"syntheticOnly": True, "status": "failed", "completionRequestsSent": 0})
        refs.append(t.tr.reference(root, path))
    def artifact(name):
        path = root / t.tr.BASE / name
        t.ev.write_new(path, {"syntheticOnly": True})
        return t.tr.reference(root, path)
    files = []
    for index in range(9):
        files.append(artifact("synthetic-recovery-code-" + str(index) + ".json"))
    return {"version": "input-execution-v1-local-question-zero-call-recovery-freeze-v3",
        "priorRun": {"path": base.relative_to(root).as_posix(), "status": "failed", "expectedContexts": 64,
            "completedContexts": 0, "freshNativeProcesses": 0, "completionRequestsSent": 0,
            "exportManifest": export_ref, "files": refs},
        "priorClaim": artifact("synthetic-old-claim.json"), "priorReview": artifact("synthetic-prior-review.json"),
        "baseExecutionFreeze": inventory["baseExecutionFreeze"],
        "inventoryCorrectionFreeze": inventory["correctionFreeze"],
        "recoveryFreeze": artifact("synthetic-recovery-freeze.json"), "files": files}


class ZeroCallTransportTests(unittest.TestCase):
    def setUp(self):
        self.f = support.InventoryTransportTests("runTest")
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.prepare()
        self.zero = synthetic_recovery(self.f.root, self.f.identity,
            t.tr.reference(self.f.root, self.f.export / "manifest.json"))
        self.native = deepcopy(self.f.native)
        self.native["summary"].update(version=runner.VERSION, zeroCallRecovery=deepcopy(self.zero))
        self.prior = self.f.stack.enter_context(patch.object(runner, "load_recovery", side_effect=lambda root: deepcopy(self.zero)))
        self.native_validator = self.f.stack.enter_context(patch.object(runner, "validate_run", side_effect=lambda *a, **kw: deepcopy(self.native)))
        self.f.stack.enter_context(patch.object(inventory_runner, "validate_run", side_effect=AssertionError("inventory-only validator forbidden")))

    def collect(self):
        return t.collect(self.f.formal_path, self.f.export, self.f.run, self.f.dest, self.f.root)

    def test_explicit_recovery_roundtrip_keeps_export_and_prior_bytes(self):
        paths = [*self.f.export.iterdir(), *[self.f.root / r["path"] for r in runner.recovery_refs(self.zero)]]
        before = {p: p.read_bytes() for p in paths}
        receipt = self.collect()
        result = t.validate_collection(self.f.dest, self.f.formal_path, self.f.root)
        self.assertEqual(set(result), {"rows", "evidence", "receipt"})
        self.assertEqual(receipt["zeroCallRecovery"], self.zero)
        self.assertEqual(receipt["inventoryCorrection"], self.f.identity)
        self.assertEqual(receipt["runnerValidatorVersion"], runner.VERSION)
        self.assertEqual(len(result["rows"]), 64)
        self.assertTrue(all(r in result["evidence"] for r in runner.recovery_refs(self.zero)))
        self.assertEqual(before, {p: p.read_bytes() for p in paths})
        self.assertEqual(self.native_validator.call_count, 2)

    def test_prior_validator_rejection_stops_before_native_validation_or_write(self):
        with patch.object(runner, "load_recovery", side_effect=ValueError("prior_completion_not_zero")):
            with self.assertRaisesRegex(ValueError, "prior_completion_not_zero"):
                self.collect()
        self.native_validator.assert_not_called(); self.assertFalse(self.f.dest.exists())

    def test_prior_export_cannot_be_rebound_even_when_summary_agrees(self):
        self.zero["priorRun"]["exportManifest"]["sha256"] = "0" * 64
        self.native["summary"]["zeroCallRecovery"] = deepcopy(self.zero)
        with self.assertRaisesRegex(ValueError, "recovery_prior_export_changed"):
            self.collect()
        self.native_validator.assert_not_called(); self.assertFalse(self.f.dest.exists())

    def test_native_summary_cannot_forge_status_calls_claim_or_freeze(self):
        original = deepcopy(self.native["summary"])
        for target, field, value in (("priorRun", "status", "completed"),
                ("priorRun", "completionRequestsSent", 1), ("priorClaim", "sha256", "0" * 64),
                ("recoveryFreeze", "sha256", "0" * 64)):
            self.native["summary"] = deepcopy(original)
            self.native["summary"]["zeroCallRecovery"][target][field] = value
            with self.subTest(target=target, field=field), self.assertRaisesRegex(ValueError, "zero_call_runner_lineage_mismatch"):
                self.collect()
            self.assertFalse(self.f.dest.exists())

    def test_old_runner_version_and_missing_recovery_are_rejected(self):
        self.native["summary"]["version"] = inventory_runner.VERSION
        with self.assertRaisesRegex(ValueError, "inventory_runner_lineage_mismatch"):
            self.collect()
        self.native["summary"]["version"] = runner.VERSION
        del self.native["summary"]["zeroCallRecovery"]
        with self.assertRaisesRegex(ValueError, "zero_call_runner_lineage_mismatch"):
            self.collect()

    def test_mutated_prior_evidence_cannot_reach_collection(self):
        path = self.f.root / self.zero["priorClaim"]["path"]
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            self.collect()
        self.assertFalse(self.f.dest.exists())

    def test_receipt_edit_and_wrong_draft_id_are_rejected(self):
        self.native["contexts"][0]["draft"]["reviewId"] = "R002"
        with self.assertRaises(ValueError):
            self.collect()
        self.assertFalse(self.f.dest.exists())
        self.native["contexts"][0]["draft"]["reviewId"] = "R001"
        self.collect()
        path = self.f.dest / "receipt.json"
        value = t.ev.read_json(path); value["zeroCallRecovery"]["priorRun"]["completionRequestsSent"] = 1
        path.write_bytes(t.ev.packed(value))
        with self.assertRaisesRegex(ValueError, "collection_replay_changed"):
            t.validate_collection(self.f.dest, self.f.formal_path, self.f.root)

    def test_escaped_destination_is_rejected_before_prior_or_native_checks(self):
        escaped = self.f.root / t.tr.BASE / "../../../escaped"
        with self.assertRaisesRegex(ValueError, "private_development_path_required"):
            t.collect(self.f.formal_path, self.f.export, self.f.run, escaped, self.f.root)
        self.prior.assert_not_called(); self.native_validator.assert_not_called()
        self.assertFalse(escaped.resolve().exists())


class ActualRecoveryLoaderBoundaryTests(unittest.TestCase):
    def test_actual_prior_loader_rejects_extra_artifact_before_native(self):
        with tempfile.TemporaryDirectory(prefix="transport-v3-prior-") as temporary:
            root = Path(temporary).resolve()
            with recovery_fixture(root) as identity:
                self.assertEqual(t.load_recovery(root), identity)
                self.assertEqual(len(t.recovery_refs(identity)), 17)
                export_ref = identity["priorRun"]["exportManifest"]
                self.assertFalse((root / export_ref["path"]).exists())
                extra = root / identity["priorRun"]["path"] / "unexpected.json"
                extra.write_bytes(b"synthetic unexpected evidence")
                with patch.object(t, "verify_export", return_value=({}, [export_ref])), \
                     patch.object(t, "load_correction", return_value={}), \
                     patch.object(runner, "validate_run") as native:
                    with self.assertRaisesRegex(ValueError, "prior_zero_call_exact_inventory"):
                        t.contents({}, root / "unused-export", root / "unused-run", root)
                native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
