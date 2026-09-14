"""Synthetic battery lineage/report/ledger checks; no production append."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_append_evidence_battery_v4 as app
from input_execution_v1 import local_question_evaluation_battery_v4 as local
from input_execution_v1 import test_local_question_append_evidence_recovery_v3 as support
from input_execution_v1.test_local_question_transport_battery_v4 import synthetic_battery


def fixture():
    data, formal, source, answers, grades, sr, gr, ar, cohort = support.fixture()
    battery = synthetic_battery(formal["zeroCallRecovery"])
    formal["batteryExecution"] = deepcopy(battery)
    data["batteryExecution"] = deepcopy(battery)
    data["report"]["recoveryEvidence"] = local.report_recovery_evidence(formal, [*formal["evidence"], sr, gr, *ar], answers)
    return data, formal, source, answers, grades, sr, gr, ar, cohort


class BatteryAppendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = fixture()

    def test_128_events_preserve_three_lineages_and_failed_original_statuses(self):
        data = deepcopy(self.saved[0]); events = app.build_events(data)
        self.assertEqual(len(events), 128)
        self.assertEqual(sum(row["originalRunStatus"] == "failed" for row in events), 76)
        self.assertEqual(sum(row["originalRunStatus"] == "completed" for row in events), 52)
        inputs = {row["reviewId"]: row for row in events if row["kind"] == "input_preparation_observation"}
        for row in events:
            self.assertEqual(row["batteryExecution"], data["batteryExecution"])
            self.assertEqual(row["zeroCallRecovery"], data["zeroCallRecovery"])
            self.assertEqual(row["questionInventoryCorrection"], data["inventoryCorrection"])
            self.assertEqual(row["questionContextProducerVersion"], app.local_transport.RUNNER_VERSION)
            self.assertEqual(row["recoveryWrapperVersion"], app.VERSION)
            if row["kind"] == "relation_observation":
                self.assertEqual(row["linkedInputPreparationEventIds"], [app.ledger.sha(app.ledger.packed(inputs[row["reviewId"]]))])

    def test_report_policy_authorization_freeze_or_context_fraud_is_rejected(self):
        for kind in ("policy", "authorization", "freeze", "context"):
            data, formal, source, answers, grades, sr, gr, ar, _ = deepcopy(self.saved)
            meta = data["report"]["recoveryEvidence"]
            if kind == "policy": meta["batteryExecution"]["powerPolicy"]["minimumBatteryPercentExclusive"] = 0
            elif kind == "authorization": meta["batteryExecution"]["userAuthorization"]["acOverride"] = False
            elif kind == "freeze": meta["batteryExecution"]["batteryFreeze"]["sha256"] = "0" * 64
            else: meta["contextProducerVersion"] = "old-context"
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "not_reproducible"):
                app.revalidate_report(formal, source, answers, grades, data["report"], sr, gr, ar)
            with self.assertRaisesRegex(ValueError, "battery_event_lineage_mismatch"):
                app.build_events(data)

    def test_battery_identity_cannot_rebind_original_zero_call_failure(self):
        data = deepcopy(self.saved[0])
        data["batteryExecution"]["zeroCallRecovery"]["priorRun"]["status"] = "completed"
        data["report"]["recoveryEvidence"]["batteryExecution"] = deepcopy(data["batteryExecution"])
        with self.assertRaisesRegex(ValueError, "battery_event_lineage_mismatch"):
            app.build_events(data)

    def test_default_prepare_does_not_append_and_records_battery_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); folder, before, baseline = support.support.create_ledger(root)
            with patch.object(app, "validate_final_evidence", return_value=deepcopy(self.saved[0])), \
                 patch.dict(app.original.BASELINE, baseline), patch.object(app.original, "append_prevalidated") as writer:
                result = app.prepare(folder="synthetic/s5", cohort="synthetic/cohort", source_reviews="synthetic/source",
                    grades="synthetic/grades", report_path="synthetic/report", relation_folder="synthetic/relations",
                    destination=root / app.original.BASE / "prepared", root=root)
                writer.assert_not_called()
            self.assertEqual(result["batteryExecution"], self.saved[0]["batteryExecution"])
            self.assertEqual(result["ledgerEventsAppended"], 0)
            self.assertEqual(app.original.ledger_snapshot(folder)["files"], before["files"])

    def test_isolated_append_is_idempotent_and_preserves_historical_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder, before, baseline = support.support.create_ledger(Path(temporary))
            events = app.build_events(deepcopy(self.saved[0]))
            first = app.original.append_prevalidated(folder, events, before, baseline)
            after = app.original.ledger_snapshot(folder)
            second = app.original.append_prevalidated(folder, events, after, baseline)
            self.assertEqual(first["imported"], {"added": 128, "reused": 0})
            self.assertEqual(second["imported"], {"added": 0, "reused": 128})
            self.assertTrue(all(after["files"][key] == value for key, value in before["files"].items()))

    def test_battery_loader_failure_stops_before_preparation_or_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); destination = root / app.original.BASE / "rejected"
            with patch.object(app.original, "verify_evaluation_freeze"), \
                 patch.object(app.reval, "load_formal", side_effect=ValueError("battery_authorization_not_bound")), \
                 patch.object(app.original, "append_prevalidated") as writer:
                with self.assertRaisesRegex(ValueError, "battery_authorization_not_bound"):
                    app.prepare(folder="synthetic/s5", cohort="synthetic/cohort", source_reviews="synthetic/source",
                        grades="synthetic/grades", report_path="synthetic/report", relation_folder="synthetic/relations",
                        destination=destination, append=True, root=root)
                writer.assert_not_called()
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
