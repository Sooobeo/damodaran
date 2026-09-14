"""Synthetic recovery evaluation/transport contracts; no real run or QA is read."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import answer_transport as tr
from input_execution_v1 import evaluation as ev
from input_execution_v1 import provisional_reviews as provisional
from input_execution_v1 import recovery_evaluation_v2 as reval
from input_execution_v1 import recovery_answer_transport_v2 as rt
from input_execution_v1.test_evaluation import fixture, reviews_for


class RecoveryEvaluationV2Tests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.reference_bundle, self.rows, self.units, self.annotations, self.prepared = fixture()
        self.prior = self.root / ".training/comparisons/input-preparation-v1/s4-generation/prior-fixture"
        self.recovered = self.root / ".training/comparisons/input-preparation-v1/s4-recovery/recovery-fixture"
        refs = []
        for index, row in enumerate(self.rows):
            run = self.prior if index < 38 else self.recovered
            row["runId"] = run.name
            row["producerVersion"] = "synthetic-original" if index < 38 else "synthetic-recovery"
            raw_path = run / "http" / f"{index:03d}-completion.response.bin"
            ev.write_new(raw_path, {"content": row["translation"]})
            row["rawResponsePath"] = raw_path.relative_to(self.root).as_posix()
            row["rawResponseSha256"] = ev.sha(raw_path.read_bytes())
            output_path = run / "outputs" / (row["id"] + "-" + row["configuration"] + ".json")
            ev.write_new(output_path, row)
            refs.extend([tr.reference(self.root, raw_path), tr.reference(self.root, output_path)])
        self.cohort_folder = self.root / ".training/comparisons/input-preparation-v1/s4-recovery/cohort-fixture"
        self.cohort_path = self.cohort_folder / "manifest.json"
        runs = []
        for run, status, count, requests in ((self.prior, "failed", 38, 39), (self.recovered, "completed", 26, 26)):
            ev.write_new(run / "summary.json", {"status": status, "completedOutputs": count, "completionRequestsSent": requests})
            refs.append(tr.reference(self.root, run / "summary.json"))
            runs.append({"role": "prior" if status == "failed" else "recovery", "runPath": run.relative_to(self.root).as_posix(),
                         "status": status, "outputs": count, "completionRequestsSent": requests,
                         "summarySha256": ev.sha((run / "summary.json").read_bytes())})
        self.manifest = {"version": reval.COHORT_VERSION, "status": "completed_logical_cohort", "expectedOutputs": 64,
                         "retainedOutputs": 38, "recoveredOutputs": 26, "completionRequestsSent": 65,
                         "interruptedRequests": 1, "sourceRuns": runs,
                         "validationVersion": reval.lineage.VALIDATION_VERSION, "validationLineage": {"syntheticOnly": True}}
        ev.write_new(self.cohort_path, self.manifest)
        self.validation = {"rows": self.rows, "evidence": {"files": refs, "prior": {}, "recovery": {}}, "manifest": self.manifest}
        self.validator = self.stack.enter_context(patch.object(reval, "validate_cohort", side_effect=self.validated_cohort))
        source_path = self.root / "synthetic-source-inventory.json"
        ev.write_new(source_path, {"syntheticOnly": True})
        self.stack.enter_context(patch.object(ev, "load_development", return_value=(self.units, self.annotations, [tr.reference(self.root, source_path)])))
        self.stack.enter_context(patch.object(ev, "load_prepared", return_value=self.prepared))
        for name in (*reval.CODE_FILES, tr.PROTOCOL, ev.S1 + "/freeze-manifest.json", ev.S2 + "/manifest.json"):
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Synthetic tool boundary fixture only: " + name, encoding="utf-8")
        # Copy real frozen small code bytes only; never read native inputs or QA.
        real_root = Path(__file__).resolve().parents[3]
        frozen = ev.read_json(real_root / reval.lineage.FREEZE_PATH)
        for name in [reval.lineage.FREEZE_PATH, *[r["path"] for r in frozen["files"]],
                     "scripts/model-comparison/input_execution_v1/recovery_provisional_reviews.py"]:
            target = self.root / name; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((real_root / name).read_bytes())
        self.folder = self.root / tr.BASE / "synthetic-recovery-formal"
        self.drafts = self.root / tr.BASE / "synthetic-answer-drafts"
        self.collection = self.root / tr.BASE / "synthetic-answer-collection"

    def validated_cohort(self, path, root):
        self.assertEqual(Path(path), self.cohort_path)
        self.assertEqual(Path(root), self.root)
        return deepcopy(self.validation)

    def prepare(self):
        result = reval.prepare_files(self.cohort_folder, self.folder, self.root)
        self.bundle = ev.read_json(self.folder / "private/bundle.json")
        return result

    def rewrite(self, path, mutate):
        value = ev.read_json(path); mutate(value); path.write_bytes(ev.packed(value))

    def populate_answers(self):
        self.prepare()
        first = rt.record(self.folder, "R001", "/root/synthetic_r001", self.root)
        for packet in self.bundle["questionPackets"]:
            rid = packet["reviewId"]
            if rid != "R001":
                assignment = deepcopy(first); assignment["reviewId"] = rid
                assignment["packetSha256"] = ev.sha(ev.packed(packet))
                assignment["reviewer"]["actorId"] = assignment["spawn"]["actorId"] = "/root/synthetic_" + rid.lower()
                ev.write_new(self.folder / "assignments" / (rid + ".json"), assignment)
            ev.write_new(self.drafts / (rid + ".json"), {"reviewId": rid, "answers": [
                {"questionId": q["questionId"], "answerKo": "현금이 포함된다.", "reasonKo": "합성 검사 근거",
                 "translationEvidence": [packet["translation"]]} for q in packet["questions"]]})

    def collect(self):
        return rt.collect(self.folder, self.drafts, self.collection, self.root)

    def freeze(self):
        return reval.freeze_answers(self.folder, self.collection, self.root)

    def make_provisional(self):
        folder = self.root / tr.BASE / "synthetic-provisional"
        unit = self.rows[0]["id"]
        provenance = {(r["id"], r["configuration"]): r for r in self.bundle["outputProvenance"]}
        mappings, evidence = [], []
        for mapping in self.bundle["mapping"]:
            if mapping["id"] != unit:
                continue
            rid = mapping["reviewId"]
            packet = next(p for p in self.bundle["sourcePackets"] if p["reviewId"] == rid)
            original = provenance[(mapping["id"], mapping["configuration"])]
            ev.write_new(folder / "source-packets" / (rid + ".json"), packet)
            mappings.append({**{k: mapping[k] for k in ("reviewId", "id", "configuration", "sourceSha256", "promptSha256", "translationSha256")},
                             "packetSha256": ev.sha(ev.packed(packet)), "outputFile": original["outputFile"],
                             "rawResponse": original["rawResponse"]})
            evidence.extend([original["outputFile"], original["rawResponse"]])
        ev.write_new(folder / "private/manifest.json", {"version": provisional.VERSION, "provisional": True,
                     "seed": ev.SEED, "sourcePackets": 4, "questionPackets": 0, "unit": unit,
                     "run": self.prior.relative_to(self.root).as_posix(), "mappings": mappings, "evidence": evidence})
        return folder

    def test_prepare_preserves_packet_bytes_and_original_mixed_run_provenance(self):
        original_rows = deepcopy(self.rows)
        prior_summary = (self.prior / "summary.json").read_bytes()
        with patch.object(ev, "validate_run_completion", side_effect=AssertionError("single-run path forbidden")), \
             patch.object(ev, "build_review_packets", side_effect=AssertionError("single-run path forbidden")):
            manifest = self.prepare()
            reval.load_formal(self.folder, self.root)
        self.assertEqual(self.rows, original_rows)
        self.assertEqual(prior_summary, (self.prior / "summary.json").read_bytes())
        self.assertEqual(manifest["originalAttemptStatus"], "failed")
        self.assertEqual(manifest["completionRequestsSent"], 65)
        self.assertEqual(self.bundle["sourcePackets"], self.reference_bundle["sourcePackets"])
        self.assertEqual(self.bundle["questionPackets"], self.reference_bundle["questionPackets"])
        self.assertEqual(sum(p["sourceRunStatus"] == "failed" for p in self.bundle["outputProvenance"]), 38)
        self.assertEqual({p["producerVersion"] for p in self.bundle["outputProvenance"]}, {"synthetic-original", "synthetic-recovery"})

    def test_real_cohort_builder_validator_interface_with_synthetic_run_boundaries(self):
        # Only the low-level run/runtime validation is stubbed. Exercise the
        # actual cohort manifest, byte concatenation and evaluation API link.
        from input_execution_v1 import recovery_cohort_v2 as cohort_module
        results = []
        for run, rows in ((self.prior, self.rows[:38]), (self.recovered, self.rows[38:])):
            ev.write_new(run / "plan.json", {"syntheticRunValidationFixture": True})
            (run / "predictions.jsonl").write_bytes(b"".join(ev.packed(row) for row in rows))
            run_key = run.relative_to(self.root).as_posix() + "/"
            paths = [self.root / r["path"] for r in self.validation["evidence"]["files"] if r["path"].startswith(run_key)]
            paths.extend([run / "plan.json", run / "predictions.jsonl"])
            evidence = {name: cohort_module.v1.ref(self.root, run / (name + ".json" if name != "predictions" else "predictions.jsonl"))
                        for name in ("summary", "plan", "predictions")}
            evidence["files"] = [cohort_module.v1.ref(self.root, path) for path in paths]
            results.append({"rows": rows, "evidence": evidence})
        validation_lineage = {}
        for key in ("frozenExecutionManifest", "baselineValidator", "baselineTests", "validator", "tests", "correctionNote"):
            path = self.root / "synthetic-lineage" / (key + ".json")
            ev.write_new(path, {"syntheticOnly": True, "key": key})
            validation_lineage[key] = cohort_module.v1.ref(self.root, path)
        destination = self.root / cohort_module.v1.COHORT_BASE / "synthetic-interface-cohort"
        with patch.object(cohort_module, "validation_identity", return_value=validation_lineage), \
             patch.object(cohort_module, "validate_failed_run", return_value=results[0]), \
             patch.object(cohort_module, "validate_recovery_run", return_value=results[1]), \
             patch.object(reval, "validate_cohort", side_effect=lambda path, root: cohort_module.validate_cohort(path, root)):
            built = cohort_module.build(self.prior, self.recovered, destination, self.root)
            reval.prepare_files(destination, self.folder, self.root)
            formal = reval.load_formal(self.folder, self.root)
        self.assertEqual(formal["cohort"]["manifest"], built["manifest"])
        self.assertEqual(formal["bundle"]["sourcePackets"], self.reference_bundle["sourcePackets"])
        self.assertEqual(formal["recovery"]["completionRequestsSent"], 65)

    def test_cohort_validator_failure_is_not_bypassed(self):
        with patch.object(reval, "validate_cohort", side_effect=ValueError("synthetic_cohort_not_complete")):
            with self.assertRaisesRegex(ValueError, "cohort_not_complete"):
                self.prepare()
        self.assertFalse(self.folder.exists())

    def test_original_failure_cannot_be_relabelled_as_success(self):
        self.manifest["sourceRuns"][0]["status"] = "completed"
        self.cohort_path.write_bytes(ev.packed(self.manifest))
        with self.assertRaisesRegex(ValueError, "failure_must_remain_failed"):
            self.prepare()

    def test_failed_request_remains_in_65_request_denominator(self):
        self.manifest["completionRequestsSent"] = 64
        self.cohort_path.write_bytes(ev.packed(self.manifest))
        with self.assertRaisesRegex(ValueError, "cohort_contract"):
            self.prepare()

    def test_incomplete_duplicate_or_reordered_rows_rejected(self):
        original = self.validation["rows"]
        for rows in (original[:-1], original[:-1] + [original[0]], list(reversed(original))):
            with self.subTest(count=len(rows)):
                self.validation["rows"] = rows
                with self.assertRaisesRegex(ValueError, "prepared_order"):
                    self.prepare()
        self.validation["rows"] = original

    def test_changed_original_output_file_rejected(self):
        path = self.prior / "outputs" / (self.rows[0]["id"] + "-C0.json")
        self.rewrite(path, lambda r: r.update(translation="modified synthetic text"))
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            self.prepare()

    def test_changed_cohort_manifest_rejected_by_formal_loader(self):
        self.prepare()
        self.cohort_path.write_bytes(self.cohort_path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            reval.load_formal(self.folder, self.root)

    def test_changed_bound_code_is_not_silently_accepted(self):
        self.prepare()
        path = self.root / reval.CODE_FILES[-1]
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            reval.load_formal(self.folder, self.root)

    def test_original_transport_refuses_explicit_recovery_manifest(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "formal_s5_manifest"):
            tr.record(self.folder, "R001", "/root/synthetic_r001", self.root)

    def test_recovery_transport_record_and_reused_actor(self):
        self.prepare()
        rt.record(self.folder, "R001", "/root/synthetic_r001", self.root)
        with self.assertRaisesRegex(ValueError, "actor_reused"):
            rt.record(self.folder, "R002", "/root/synthetic_r001", self.root)
        self.assertFalse((self.folder / ".assignment-record.lock").exists())

    def test_recovery_collect_checks_id_before_conversion(self):
        self.populate_answers()
        self.rewrite(self.drafts / "R001.json", lambda d: d.update(reviewId="R002"))
        with patch.object(rt.review_io, "question_answer", side_effect=AssertionError("conversion must not run")):
            with self.assertRaisesRegex(ValueError, "draft_review_id_mismatch"):
                self.collect()
        self.assertFalse(self.collection.exists())

    def test_recovery_collect_requires_all_64_assignments_and_drafts(self):
        self.populate_answers()
        (self.folder / "assignments/R064.json").unlink()
        with self.assertRaisesRegex(ValueError, "file_inventory"):
            self.collect()
        self.assertFalse(self.collection.exists())

    def test_freeze_revalidates_collection_and_preserves_answer_bytes(self):
        self.populate_answers(); self.collect()
        payload = (self.collection / "answers.json").read_bytes()
        receipt = self.freeze()
        self.assertEqual(payload, (self.folder / "answers-frozen.json").read_bytes())
        self.assertEqual(receipt["recovery"]["originalAttemptStatus"], "failed")
        self.assertFalse(receipt["sourceGradingCompleted"])
        rows, _ = reval.read_frozen_answers(reval.load_formal(self.folder, self.root), self.root)
        self.assertEqual(len(rows), 64)
        self.assertEqual(len(list((self.folder / "grade-packets").iterdir())), 64)
        with self.assertRaisesRegex(ValueError, "freeze_must_be_new"):
            self.freeze()

    def test_changed_collection_answer_is_not_frozen(self):
        self.populate_answers(); self.collect()
        self.rewrite(self.collection / "answers.json", lambda d: d["rows"][0]["answers"][0].update(answerKo="changed"))
        with self.assertRaisesRegex(ValueError, "collected_answers_changed"):
            self.freeze()
        self.assertFalse((self.folder / "answers-frozen.json").exists())

    def test_changed_original_draft_is_not_hidden_by_preserved_copy(self):
        self.populate_answers(); self.collect()
        self.rewrite(self.drafts / "R001.json", lambda d: d["answers"][0].update(reasonKo="changed"))
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            self.freeze()

    def test_changed_preserved_copy_rejected(self):
        self.populate_answers(); self.collect()
        self.rewrite(self.collection / "original-drafts/R001.json", lambda d: d.update(reviewId="R002"))
        with self.assertRaisesRegex(ValueError, "snapshot_changed"):
            self.freeze()

    def test_missing_review_inputs_produce_incomplete_report_not_a_candidate(self):
        self.prepare()
        result = reval.report(self.folder, self.root / tr.BASE / "synthetic-incomplete-report.json", root=self.root)
        self.assertEqual(result["counts"]["sourceReviews"], 0)
        self.assertIsNone(result["decision"]["selectedConfiguration"])
        self.assertEqual(result["recoveryEvidence"]["completionRequestsSent"], 65)
        self.assertFalse(result["recoveryEvidence"]["singleRunCompletionClaimed"])

    def test_report_reuses_frozen_aggregate_on_explicit_synthetic_judgments(self):
        self.populate_answers(); self.collect(); self.freeze()
        reviews, _, grades = reviews_for(self.bundle)
        actual_answers = ev.read_rows(self.folder / "answers-frozen.json")
        by_id = {a["reviewId"]: a for a in actual_answers}
        for grade in grades:
            grade["answerSha256"] = ev.sha(ev.packed(by_id[grade["reviewId"]]))
        source_path = self.root / tr.BASE / "synthetic-source-judgments.json"
        grade_path = self.root / tr.BASE / "synthetic-question-judgments.json"
        ev.write_new(source_path, {"rows": reviews}); ev.write_new(grade_path, {"rows": grades})
        result = reval.report(self.folder, self.root / tr.BASE / "synthetic-complete-report.json",
                              source_path, grade_path, self.root)
        semantic = {k: v for k, v in result.items() if k != "recoveryEvidence"}
        self.assertEqual(semantic, ev.aggregate(self.bundle, reviews, actual_answers, grades))
        self.assertEqual(result["counts"]["questionGrades"], 128)
        self.assertIsNone(result["decision"]["selectedConfiguration"])
        self.assertFalse(result["decision"]["registrationApproved"])

    def test_grades_require_real_frozen_answer_artifacts(self):
        self.prepare()
        path = self.root / tr.BASE / "synthetic-empty-grades.json"
        ev.write_new(path, {"rows": []})
        with self.assertRaises(FileNotFoundError):
            reval.report(self.folder, self.root / tr.BASE / "synthetic-report.json", grades=path, root=self.root)

    def test_changed_grade_packet_rejected_before_report(self):
        self.populate_answers(); self.collect(); self.freeze()
        path = self.folder / "grade-packets/R001.json"
        self.rewrite(path, lambda p: p["answer"]["answers"][0].update(answerKo="changed"))
        with self.assertRaisesRegex(ValueError, "grade_packet_changed"):
            reval.report(self.folder, self.root / tr.BASE / "synthetic-report.json", root=self.root)

    def test_promotion_checks_identical_packets_and_original_hash_without_approval(self):
        self.prepare(); provisional_folder = self.make_provisional()
        receipt = reval.verify_promotion(provisional_folder, self.folder, self.root / tr.BASE / "synthetic-promotion.json", self.root)
        self.assertTrue(receipt["packetPromotionEligible"])
        self.assertFalse(receipt["formalSourceReviewsApproved"] or receipt["originalFailedRunMarkedCompleted"])
        self.assertTrue(receipt["actualReviewerConfirmationStillRequired"])
        self.assertEqual(receipt["scoresProduced"], 0)

    def test_promotion_rejects_same_meaning_but_changed_packet_bytes(self):
        self.prepare(); provisional_folder = self.make_provisional()
        path = next((provisional_folder / "source-packets").iterdir())
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "not_byte_identical"):
            reval.verify_promotion(provisional_folder, self.folder, self.root / tr.BASE / "synthetic-promotion.json", self.root)

    def test_promotion_rejects_changed_original_output_reference(self):
        self.prepare(); provisional_folder = self.make_provisional()
        self.rewrite(provisional_folder / "private/manifest.json", lambda p: p["mappings"][0]["outputFile"].update(sha256="0" * 64))
        with self.assertRaisesRegex(ValueError, "original_output_changed"):
            reval.verify_promotion(provisional_folder, self.folder, self.root / tr.BASE / "synthetic-promotion.json", self.root)


if __name__ == "__main__":
    unittest.main()
