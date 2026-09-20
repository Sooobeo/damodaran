"""Synthetic v6 provenance/ledger checks; S4 38+26 and QA 9+55 stay distinct."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_append_evidence_memory_v6 as app
from input_execution_v1 import local_question_evaluation_memory_v6 as local
from input_execution_v1 import local_question_transport_memory_v6 as transport
from input_execution_v1 import test_local_question_append_evidence_battery_v4 as support
from input_execution_v1.test_local_question_transport_memory_v6 import synthetic_partial, logical_summary, source_run


def fixture():
    data, formal, source, answers, grades, sr, gr, ar, cohort = support.fixture()
    partial, runs = synthetic_partial(formal["batteryExecution"])
    bindings = [{"reviewId": rid, "contextId": "synthetic-partial-" + rid, "contextProducerVersion": transport.PRIOR_CONTEXT_VERSION if i < 9 else transport.CONTEXT_VERSION,
        "sourceRun": source_run(i, runs)} for i, rid in enumerate(transport.contract.IDS)]
    provenance = transport.question_provenance(logical_summary(partial, runs, formal["inventoryCorrection"]), bindings, partial)
    formal["partialRecovery"] = deepcopy(partial)
    data.update(partialRecovery=deepcopy(partial), questionProvenance=deepcopy(provenance))
    data["report"]["recoveryEvidence"] = local.report_recovery_evidence(formal, [*formal["evidence"], sr, gr, *ar], answers, provenance)
    return data, formal, source, answers, grades, sr, gr, ar, cohort


class PartialAppendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.saved = fixture()

    def test_128_events_keep_s4_38_26_separate_from_question_9_55(self):
        data = deepcopy(self.saved[0]); events = app.build_events(data)
        self.assertEqual(len(events), 128)
        self.assertEqual(sum(row["originalRunStatus"] == "failed" for row in events), 76)
        self.assertEqual(sum(row["questionContext"]["sourceRun"]["reused"] for row in events), 18)
        inputs = {r["reviewId"]: r for r in events if r["kind"] == "input_preparation_observation"}
        for row in events:
            self.assertEqual(row["questionSourceRuns"], data["questionProvenance"]["sourceRuns"])
            self.assertEqual([r["completionRequestsSent"] for r in row["questionSourceRuns"]], [9, 55])
            self.assertEqual(row["questionCoordinatorVersion"], transport.RUNNER_VERSION)
            self.assertEqual(row["questionContextProducerVersion"],
                transport.PRIOR_CONTEXT_VERSION if row["questionContext"]["sourceRun"]["reused"] else transport.CONTEXT_VERSION)
            self.assertEqual(row["partialRecovery"], data["partialRecovery"])
            if row["kind"] == "relation_observation":
                self.assertEqual(row["linkedInputPreparationEventIds"], [app.ledger.sha(app.ledger.packed(inputs[row["reviewId"]]))])

    def test_report_or_validated_question_provenance_cannot_be_reclassified(self):
        data, formal, source, answers, grades, sr, gr, ar, _ = deepcopy(self.saved)
        data["report"]["recoveryEvidence"]["questionProvenance"]["sourceRuns"][0]["status"] = "completed"
        with self.assertRaisesRegex(ValueError, "not_reproducible"):
            app.revalidate_report(formal, source, answers, grades, data["report"], sr, gr, ar, data["questionProvenance"])
        with self.assertRaisesRegex(ValueError, "partial_event_lineage_mismatch"): app.build_events(data)
        data = deepcopy(self.saved[0])
        data["questionProvenance"]["contexts"][9]["sourceRun"]["reused"] = True
        data["report"]["recoveryEvidence"]["questionProvenance"] = deepcopy(data["questionProvenance"])
        with self.assertRaisesRegex(ValueError, "partial_context_original_run_changed"): app.build_events(data)

    def test_changed_recovery_battery_or_context_version_is_rejected(self):
        for kind in ("battery", "context", "source-run-count"):
            data = deepcopy(self.saved[0])
            if kind == "battery": data["partialRecovery"]["batteryExecution"]["userAuthorization"]["acOverride"] = False
            elif kind == "context": data["report"]["recoveryEvidence"]["contextProducerVersion"] = transport.RUNNER_VERSION
            else:
                data["questionProvenance"]["sourceRuns"][1]["completionRequestsSent"] = 64
                data["report"]["recoveryEvidence"]["questionProvenance"] = deepcopy(data["questionProvenance"])
            with self.subTest(kind=kind), self.assertRaises(ValueError): app.build_events(data)

    def test_default_prepare_keeps_ledger_and_records_partial_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); folder, before, baseline = support.support.support.create_ledger(root)
            with patch.object(app, "validate_final_evidence", return_value=deepcopy(self.saved[0])), \
                 patch.dict(app.original.BASELINE, baseline), patch.object(app.original, "append_prevalidated") as writer:
                result = app.prepare(folder="synthetic/s5", cohort="synthetic/cohort", source_reviews="synthetic/source",
                    grades="synthetic/grades", report_path="synthetic/report", relation_folder="synthetic/relations",
                    destination=root / app.original.BASE / "prepared", root=root)
                writer.assert_not_called()
            self.assertEqual(result["questionProvenance"], self.saved[0]["questionProvenance"])
            self.assertEqual(result["ledgerEventsAppended"], 0)
            self.assertEqual(app.original.ledger_snapshot(folder)["files"], before["files"])

    def test_temporary_append_remains_idempotent_with_old_bytes_intact(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder, before, baseline = support.support.support.create_ledger(Path(temporary))
            events = app.build_events(deepcopy(self.saved[0]))
            first = app.original.append_prevalidated(folder, events, before, baseline)
            after = app.original.ledger_snapshot(folder)
            second = app.original.append_prevalidated(folder, events, after, baseline)
            self.assertEqual(first["imported"], {"added": 128, "reused": 0})
            self.assertEqual(second["imported"], {"added": 0, "reused": 128})
            self.assertTrue(all(after["files"][k] == v for k, v in before["files"].items()))

    def test_partial_loader_rejection_stops_before_any_preparation_or_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); destination = root / app.original.BASE / "rejected"
            with patch.object(app.original, "verify_evaluation_freeze"), \
                 patch.object(app.reval, "load_formal", side_effect=ValueError("prior_nine_changed")), \
                 patch.object(app.original, "append_prevalidated") as writer:
                with self.assertRaisesRegex(ValueError, "prior_nine_changed"):
                    app.prepare(folder="synthetic/s5", cohort="synthetic/cohort", source_reviews="synthetic/source",
                        grades="synthetic/grades", report_path="synthetic/report", relation_folder="synthetic/relations",
                        destination=destination, append=True, root=root)
                writer.assert_not_called()
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()


