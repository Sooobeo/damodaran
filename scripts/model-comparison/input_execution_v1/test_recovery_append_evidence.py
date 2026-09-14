"""Synthetic recovery evidence and isolated-ledger tests; no real append or QA."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import append_evidence as original
from input_execution_v1 import evaluation as ev
from input_execution_v1 import recovery_evaluation as reval
from input_execution_v1 import recovery_relations as relations
from input_execution_v1 import recovery_append_evidence as app
from input_execution_v1.test_evaluation import fixture, reviews_for
from input_execution_v1.test_append_evidence import create_ledger


def complete_fixture():
    _, outputs, units, annotations, prepared = fixture()
    prior_path = "fixture/prior/attempt-001"
    recovery_path = "fixture/recovered/recovery-attempt-001"
    provenance = []
    for index, row in enumerate(outputs):
        prior = index < 38
        run = prior_path if prior else recovery_path
        row["runId"] = Path(run).name
        row["producerVersion"] = relations.ORIGINAL_VERSION if prior else relations.RECOVERY_VERSION
        row["rawResponsePath"] = run + "/http/" + str(index) + "-completion.response.bin"
        output_path = run + "/outputs/" + row["id"] + "-" + row["configuration"] + ".json"
        provenance.append({"id": row["id"], "configuration": row["configuration"], "runId": row["runId"],
                           "producerVersion": row["producerVersion"], "sourceRunStatus": "failed" if prior else "completed",
                           "outputFile": {"path": output_path, "sha256": ev.sha(ev.packed(row))},
                           "rawResponse": {"path": row["rawResponsePath"], "sha256": row["rawResponseSha256"]}})
    manifest = {"version": reval.COHORT_VERSION, "status": "completed_logical_cohort", "sourceRuns": [
        {"runPath": prior_path, "status": "failed", "outputs": 38, "completionRequestsSent": 39},
        {"runPath": recovery_path, "status": "completed", "outputs": 26, "completionRequestsSent": 26}]}
    manifest_ref = {"path": "fixture/cohort/manifest.json", "sha256": "c" * 64}
    refs = [{"path": "fixture/source.json", "sha256": "1" * 64}]
    cohort = {"rows": outputs, "units": units, "annotations": annotations, "prepared": prepared, "manifest": manifest,
              "manifestRef": manifest_ref, "provenance": provenance, "evidence": refs, "code": []}
    bundle = reval.build_review_packets(cohort)
    source, answers, grades = reviews_for(bundle)
    report = ev.aggregate(bundle, source, answers, grades)
    formal = {"bundle": bundle, "recovery": bundle["recovery"], "manifestSha256": "f" * 64, "evidence": refs}
    source_ref, grade_ref = {"path": "fixture/reviews.json", "sha256": "2" * 64}, {"path": "fixture/grades.json", "sha256": "3" * 64}
    answer_refs = [{"path": "fixture/answers-frozen.json", "sha256": "4" * 64}]
    report["recoveryEvidence"] = {"wrapperVersion": reval.VERSION, "formalManifestSha256": formal["manifestSha256"],
                                  **formal["recovery"], "evidenceFiles": [*refs, source_ref, grade_ref, *answer_refs],
                                  "singleRunCompletionClaimed": False, "ledgerAppendPerformed": False}
    relation_report = relations.diagnose_rows(outputs)
    cohort_validation = {"rows": outputs, "manifest": manifest, "evidence": {"files": refs, "prior": {}, "recovery": {}}}
    relation_report.update(cohortManifest=manifest, cohortEvidence=cohort_validation["evidence"],
                           originalAttemptStatus="failed", retainedOutputs=38, recoveredOutputs=26,
                           completionRequestsSent=65, interruptedRequests=1)
    result = {"bundle": bundle, "report": report, "outputs": outputs, "relations": relation_report,
              "evidenceRefs": [source_ref, grade_ref, *answer_refs], "relationEvidenceRefs": refs,
              "recovery": formal["recovery"], "graph": Mock(files={"fixture/report.json": "a" * 64})}
    return result, formal, source, answers, grades, source_ref, grade_ref, answer_refs, cohort_validation


class RecoveryAppendEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = complete_fixture()

    def data(self):
        return deepcopy(self.fixture[0])

    def test_128_events_keep_actual_failed_and_completed_run_statuses(self):
        events = app.build_events(self.data())
        inputs = {r["reviewId"]: r for r in events if r["kind"] == "input_preparation_observation"}
        relation_events = [r for r in events if r["kind"] == "relation_observation"]
        self.assertEqual((len(inputs), len(relation_events)), (64, 64))
        self.assertEqual(sum(e["originalRunStatus"] == "failed" for e in events), 76)
        self.assertEqual(sum(e["originalRunStatus"] == "completed" for e in events), 52)
        for event in events:
            self.assertEqual(event["recoveryCohort"]["completionRequestsSent"], 65)
            self.assertEqual(event["recoveryCohort"]["interruptedRequests"], 1)
            self.assertEqual(Path(event["run"]).name, event["runId"])
            self.assertEqual(event["originalOutputStatus"], "completed")
            self.assertEqual(event["sourceCohort"], "input-preparation-dev16")
            self.assertFalse(event["originalJudgmentsChanged"] or event["humanReviewed"])
            self.assertEqual(event["modelCalls"], 0)
        for event in relation_events:
            input_event = inputs[event["reviewId"]]
            self.assertEqual(event["linkedInputPreparationEventIds"], [app.ledger.sha(app.ledger.packed(input_event))])
            self.assertEqual(event["originalRunStatus"], input_event["originalRunStatus"])
            self.assertFalse(event["translationErrorAdded"])
        self.assertNotIn("translation_review", {r["kind"] for r in events})
        self.assertNotIn("question_judgment", {r["kind"] for r in events})

    def test_failed_relation_status_cannot_be_changed_to_completed(self):
        data = self.data(); data["relations"]["observations"][0]["originalRunStatus"] = "completed"
        with self.assertRaisesRegex(ValueError, "run_status_changed"):
            app.build_events(data)

    def test_wrong_run_path_or_raw_identity_is_rejected(self):
        data = self.data(); data["bundle"]["outputProvenance"][0]["outputFile"]["path"] = "fixture/wrong/outputs/unit0-C0.json"
        with self.assertRaisesRegex(ValueError, "source_run_join"):
            app.build_events(data)
        data = self.data(); data["relations"]["observations"][0]["rawResponseSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "join_changed"):
            app.build_events(data)

    def test_missing_relation_and_incomplete_reviews_are_rejected(self):
        data = self.data(); data["relations"]["observations"].pop()
        with self.assertRaisesRegex(ValueError, "observation_inventory"):
            app.build_events(data)
        data = self.data(); data["report"]["rows"][0]["unresolved"] = True
        with self.assertRaisesRegex(ValueError, "unresolved_or_incomplete"):
            app.build_events(data)

    def test_report_aggregate_and_recovery_extension_both_revalidated(self):
        data, formal, source, answers, grades, sr, gr, ar, _ = deepcopy(self.fixture)
        app.revalidate_report(formal, source, answers, grades, data["report"], sr, gr, ar)
        data["report"]["recoveryEvidence"]["completionRequestsSent"] = 64
        with self.assertRaisesRegex(ValueError, "not_reproducible"):
            app.revalidate_report(formal, source, answers, grades, data["report"], sr, gr, ar)
        data = self.data(); source[0]["propositions"][0]["verdict"] = "damaged"
        with self.assertRaisesRegex(ValueError, "not_reproducible"):
            app.revalidate_report(formal, source, answers, grades, data["report"], sr, gr, ar)

    def test_relation_diagnostics_and_full_cohort_receipt_revalidated(self):
        data, *_, cohort = deepcopy(self.fixture)
        app.revalidate_relations(data["relations"], data["outputs"], cohort)
        data["relations"]["observations"][0]["diagnostic"]["warning"] = True
        with self.assertRaisesRegex(ValueError, "not_reproducible"):
            app.revalidate_relations(data["relations"], data["outputs"], cohort)
        data = self.data(); data["relations"]["cohortEvidence"]["files"] = []
        with self.assertRaisesRegex(ValueError, "cohort_evidence_changed"):
            app.revalidate_relations(data["relations"], data["outputs"], cohort)

    def test_constructing_again_preserves_all_event_bytes(self):
        self.assertEqual(app.ledger.packed(app.build_events(self.data())), app.ledger.packed(app.build_events(self.data())))

    def test_isolated_append_then_repeat_zero_preserves_all_historical_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, before, baseline = create_ledger(Path(tmp))
            events = app.build_events(self.data())
            first = original.append_prevalidated(folder, events, before, baseline)
            after = original.ledger_snapshot(folder)
            second = original.append_prevalidated(folder, events, after, baseline)
            self.assertEqual(first["imported"], {"added": 128, "reused": 0})
            self.assertEqual(second["imported"], {"added": 0, "reused": 128})
            self.assertTrue(all(after["files"][key] == raw for key, raw in before["files"].items()))
            self.assertEqual(after["summary"]["eventKindCounts"]["translation_review"], 1)

    def test_default_prepare_never_calls_ledger_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder, before, baseline = create_ledger(root)
            with patch.object(app, "validate_final_evidence", return_value=self.data()), patch.dict(original.BASELINE, baseline), \
                 patch.object(app.ledger, "append_events") as writer:
                result = app.prepare(folder="fixture/s5", cohort="fixture/cohort", source_reviews="fixture/reviews.json",
                    grades="fixture/grades.json", report_path="fixture/report.json", relation_folder="fixture/relations",
                    destination=root / original.BASE / "prepared-fixture", root=root)
                writer.assert_not_called()
            self.assertEqual(result["ledgerEventsAppended"], 0)
            self.assertEqual(result["status"], "prepared")
            self.assertFalse(result["originalFailedRunMarkedCompleted"])
            self.assertEqual(original.ledger_snapshot(folder)["files"], before["files"])

    def test_explicit_append_in_isolated_ledger_and_repeat_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder, before, baseline = create_ledger(root)
            with patch.object(app, "validate_final_evidence", return_value=self.data()), patch.dict(original.BASELINE, baseline):
                results = [app.prepare(folder="fixture/s5", cohort="fixture/cohort", source_reviews="fixture/reviews.json",
                    grades="fixture/grades.json", report_path="fixture/report.json", relation_folder="fixture/relations",
                    destination=root / original.BASE / ("append-fixture-" + str(i)), append=True, root=root) for i in range(2)]
            self.assertEqual([r["ledgerEventsAppended"] for r in results], [128, 0])
            after = original.ledger_snapshot(folder)
            self.assertTrue(all(after["files"][key] == raw for key, raw in before["files"].items()))

    def test_changed_historical_baseline_or_unknown_event_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, _, baseline = create_ledger(Path(tmp))
            app.ledger.append_events(folder, [{"version": app.ledger.VERSION, "kind": "relation_observation", "unrelated": True}])
            with self.assertRaisesRegex(ValueError, "baseline_inventory_changed"):
                original.require_baseline(original.ledger_snapshot(folder), app.build_events(self.data()), baseline)

    def test_failed_recovery_load_prevents_preparation_and_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); destination = root / original.BASE / "failed-fixture"
            with patch.object(original, "verify_evaluation_freeze"), \
                 patch.object(reval, "load_formal", side_effect=ValueError("recovery_cohort_incomplete")) as load, \
                 patch.object(original, "validate_final_evidence", side_effect=AssertionError("old loader must not run")), \
                 patch.object(app.ledger, "append_events") as writer:
                with self.assertRaisesRegex(ValueError, "cohort_incomplete"):
                    app.prepare(folder="fixture/s5", cohort="fixture/cohort", source_reviews="fixture/reviews.json",
                        grades="fixture/grades.json", report_path="fixture/report.json", relation_folder="fixture/relations",
                        destination=destination, append=True, root=root)
                self.assertTrue(load.called)
                writer.assert_not_called(); self.assertFalse(destination.exists())

    def test_frozen_ten_file_verifier_is_mandatory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(original, "verify_evaluation_freeze", side_effect=ValueError("frozen_ten_changed")) as freeze, \
                 patch.object(reval, "load_formal") as load:
                with self.assertRaisesRegex(ValueError, "frozen_ten_changed"):
                    app.validate_final_evidence(folder="fixture/s5", cohort="fixture/cohort", source_reviews="fixture/reviews.json",
                        grades="fixture/grades.json", report_path="fixture/report.json", relation_folder="fixture/relations", root=tmp)
                freeze.assert_called_once(); load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
