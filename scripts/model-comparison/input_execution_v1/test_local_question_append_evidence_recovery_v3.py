"""Focused synthetic zero-call lineage/ledger tests; no real QA or append."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_append_evidence_recovery_v3 as app
from input_execution_v1 import local_question_evaluation_recovery_v3 as local
from input_execution_v1 import test_local_question_append_evidence_inventory_v2 as support


def fixture():
    data, formal, source, answers, grades, sr, gr, ar, cohort = support.complete_fixture()
    zero = {
        "version": "input-execution-v1-local-question-zero-call-recovery-freeze-v3",
        "priorRun": {"path": "synthetic/prior", "status": "failed", "expectedContexts": 64,
            "completedContexts": 0, "freshNativeProcesses": 0, "completionRequestsSent": 0,
            "exportManifest": {"path": "synthetic/export/manifest.json", "sha256": "8" * 64}, "files": []},
        "priorClaim": {"path": "synthetic/claim.json", "sha256": "9" * 64},
        "priorReview": {"path": "synthetic/review.json", "sha256": "a" * 64},
        "baseExecutionFreeze": {"path": "synthetic/base.json", "sha256": "b" * 64},
        "inventoryCorrectionFreeze": {"path": "synthetic/inventory.json", "sha256": "c" * 64},
        "recoveryFreeze": {"path": "synthetic/recovery.json", "sha256": "d" * 64}, "files": []}
    formal["zeroCallRecovery"] = deepcopy(zero)
    data["zeroCallRecovery"] = deepcopy(zero)
    data["report"]["recoveryEvidence"] = local.report_recovery_evidence(
        formal, [*formal["evidence"], sr, gr, *ar], answers)
    return data, formal, source, answers, grades, sr, gr, ar, cohort


class ZeroCallAppendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = fixture()

    def test_128_events_preserve_both_failures_and_inventory_lineage(self):
        data = deepcopy(self.saved[0]); events = app.build_events(data)
        self.assertEqual(len(events), 128)
        self.assertEqual(sum(r["originalRunStatus"] == "failed" for r in events), 76)
        self.assertEqual(sum(r["originalRunStatus"] == "completed" for r in events), 52)
        for row in events:
            self.assertEqual(row["zeroCallRecovery"], data["zeroCallRecovery"])
            self.assertEqual(row["zeroCallRecovery"]["priorRun"]["status"], "failed")
            self.assertEqual(row["zeroCallRecovery"]["priorRun"]["completionRequestsSent"], 0)
            self.assertEqual(row["questionInventoryCorrection"], data["inventoryCorrection"])
            self.assertEqual(row["recoveryWrapperVersion"], app.VERSION)

    def test_relation_links_include_new_zero_call_evidence_in_input_hash(self):
        events = app.build_events(deepcopy(self.saved[0]))
        inputs = {r["reviewId"]: r for r in events if r["kind"] == "input_preparation_observation"}
        for row in events:
            if row["kind"] == "relation_observation":
                self.assertEqual(row["linkedInputPreparationEventIds"],
                    [app.ledger.sha(app.ledger.packed(inputs[row["reviewId"]]))])

    def test_report_cannot_reclassify_prior_failure_call_count_or_claim(self):
        for target, field, value in (("priorRun", "status", "completed"),
                ("priorRun", "completionRequestsSent", 1), ("priorClaim", "sha256", "0" * 64),
                ("recoveryFreeze", "sha256", "0" * 64)):
            data, formal, source, answers, grades, sr, gr, ar, _ = deepcopy(self.saved)
            data["report"]["recoveryEvidence"]["zeroCallRecovery"][target][field] = value
            with self.subTest(target=target, field=field), self.assertRaisesRegex(ValueError, "not_reproducible"):
                app.revalidate_report(formal, source, answers, grades, data["report"], sr, gr, ar)
            with self.assertRaisesRegex(ValueError, "zero_call_event_lineage_mismatch"):
                app.build_events(data)

    def test_validated_lineage_cannot_be_replaced_before_event_build(self):
        data = deepcopy(self.saved[0]); data["zeroCallRecovery"]["priorClaim"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "zero_call_event_lineage_mismatch"):
            app.build_events(data)

    def test_default_prepare_preserves_ledger_and_records_zero_call_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder, before, baseline = support.create_ledger(root)
            with patch.object(app, "validate_final_evidence", return_value=deepcopy(self.saved[0])), \
                 patch.dict(app.original.BASELINE, baseline), patch.object(app.original, "append_prevalidated") as writer:
                result = app.prepare(folder="synthetic/s5", cohort="synthetic/cohort", source_reviews="synthetic/source",
                    grades="synthetic/grades", report_path="synthetic/report", relation_folder="synthetic/relations",
                    destination=root / app.original.BASE / "prepared", root=root)
                writer.assert_not_called()
            self.assertEqual(result["zeroCallRecovery"], self.saved[0]["zeroCallRecovery"])
            self.assertEqual(result["ledgerEventsAppended"], 0)
            self.assertEqual(app.original.ledger_snapshot(folder)["files"], before["files"])

    def test_isolated_append_is_idempotent_and_keeps_historical_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, before, baseline = support.create_ledger(Path(tmp))
            events = app.build_events(deepcopy(self.saved[0]))
            first = app.original.append_prevalidated(folder, events, before, baseline)
            after = app.original.ledger_snapshot(folder)
            second = app.original.append_prevalidated(folder, events, after, baseline)
            self.assertEqual(first["imported"], {"added": 128, "reused": 0})
            self.assertEqual(second["imported"], {"added": 0, "reused": 128})
            self.assertTrue(all(after["files"][k] == v for k, v in before["files"].items()))

    def test_recovery_validation_failure_prevents_preparation_and_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); destination = root / app.original.BASE / "rejected"
            with patch.object(app.original, "verify_evaluation_freeze"), \
                 patch.object(app.reval, "load_formal", side_effect=ValueError("zero_call_prior_not_proven")) as loader, \
                 patch.object(app.original, "append_prevalidated") as writer:
                with self.assertRaisesRegex(ValueError, "zero_call_prior_not_proven"):
                    app.prepare(folder=root / app.original.BASE / "s5", cohort="synthetic/cohort", source_reviews="synthetic/source",
                        grades="synthetic/grades", report_path="synthetic/report", relation_folder="synthetic/relations",
                        destination=destination, append=True, root=root)
                loader.assert_called_once(); writer.assert_not_called()
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
