"""Synthetic local answer boundary tests; no packet from a real run is read."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_evaluation_v1 as local
from input_execution_v1 import recovery_evaluation_v2 as recovery
from input_execution_v1 import test_recovery_append_evidence_v2 as support
from input_execution_v1 import recovery_answer_transport_v2 as spawn_transport

ev, tr = local.ev, local.tr


class LocalQuestionEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        data, formal, self.source, self.answers, self.grades, *_ = support.complete_fixture()
        self.folder = self.root / tr.BASE / "synthetic-formal"
        self.collection = self.root / tr.BASE / "synthetic-local-collection"
        self.receipt_path = self.collection / "receipt.json"
        self.raw_path = self.collection / "synthetic-native-evidence.json"
        ev.write_new(self.raw_path, {"syntheticOnly": True, "actualModelCalls": 0})
        self.collection_receipt = {"version": "synthetic-local-collection", "syntheticOnly": True,
            "model": "Qwen synthetic boundary fixture only", "contexts": 64, "actualModelCalls": 0}
        ev.write_new(self.receipt_path, self.collection_receipt)
        ev.write_new(self.folder / "manifest.json", {"syntheticOnly": True})
        for name in [local.PROTOCOL, *local.CODE_FILES]:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic code or protocol boundary fixture: " + name, encoding="utf-8")
        for answer in self.answers:
            answer["reviewer"]["actorId"] = "local-native/synthetic/" + answer["reviewId"]
            answer["reviewer"]["model"] = self.collection_receipt["model"]
        for grade, answer in zip(self.grades, self.answers):
            grade["answerSha256"] = ev.sha(ev.packed(answer))
        self.bundle = data["bundle"]
        formal.update(folder=self.folder, bundleSha256=ev.sha(ev.packed(self.bundle)),
            packets={p["reviewId"]: p for p in self.bundle["questionPackets"]},
            evidence=[tr.reference(self.root, self.folder / "manifest.json")])
        self.formal = formal
        self.stack.enter_context(patch.object(recovery, "load_formal", side_effect=lambda folder, root: deepcopy(self.formal)))
        self.validation = {"rows": self.answers, "receipt": self.collection_receipt,
                           "evidence": [tr.reference(self.root, self.raw_path)]}
        self.validator = self.stack.enter_context(patch.object(local, "validate_collection", side_effect=self.validated))

    def validated(self, directory, formalPath, root):
        self.assertEqual(directory, self.collection)
        self.assertEqual(formalPath, self.folder)
        self.assertEqual(root, self.root)
        return deepcopy(self.validation)

    def freeze(self):
        return local.freeze_answers(self.folder, self.collection, self.root)

    def read(self):
        return local.read_frozen_answers(local.load_formal(self.folder, self.root), self.root)

    def rewrite(self, path, mutate):
        value = ev.read_json(path); mutate(value); path.write_bytes(ev.packed(value))

    def test_local_freeze_never_calls_spawn_transport_and_preserves_answer_bytes(self):
        with patch.object(spawn_transport, "validate_collection", side_effect=AssertionError("spawn transport forbidden")):
            receipt = self.freeze(); rows, _ = self.read()
        self.assertEqual(rows, self.answers)
        self.assertEqual((self.folder / "answers-frozen.json").read_bytes(), ev.packed({"version": ev.VERSION, "rows": self.answers}))
        self.assertEqual(receipt["collectionMetadata"], self.collection_receipt)
        self.assertEqual(receipt["recovery"]["originalAttemptStatus"], "failed")
        self.assertEqual(len(list((self.folder / "grade-packets").iterdir())), 64)
        self.assertFalse(receipt["sourceGradingCompleted"] or receipt["humanReviewed"])

    def test_formal_bundle_and_question_bytes_are_not_rewritten_for_local_method(self):
        original = deepcopy(self.formal)
        result = local.load_formal(self.folder, self.root)
        self.assertEqual(ev.packed(result["bundle"]), ev.packed(original["bundle"]))
        self.assertEqual(result["recovery"], original["recovery"])
        self.assertIn(result["questionProtocol"], result["evidence"])
        self.assertEqual(len(result["questionCodeFiles"]), 5)

    def test_failed_final_cohort_prevents_local_collection_or_any_answer_write(self):
        with patch.object(recovery, "load_formal", side_effect=ValueError("cohort_incomplete")):
            with self.assertRaisesRegex(ValueError, "cohort_incomplete"):
                self.freeze()
        self.validator.assert_not_called()
        self.assertFalse((self.folder / "answers-freeze.json").exists())

    def test_escaped_formal_paths_are_rejected_before_frozen_loader_or_read(self):
        escaped = self.root / tr.BASE / "../../../escaped-formal"
        with patch.object(recovery, "load_formal") as loader:
            with self.assertRaisesRegex(ValueError, "private_development_path_required"):
                local.load_formal(escaped, self.root)
            loader.assert_not_called()
        formal = deepcopy(self.formal); formal["folder"] = escaped
        with self.assertRaisesRegex(ValueError, "private_development_path_required"):
            local.read_frozen_answers(formal, self.root)
        self.assertFalse(escaped.resolve().exists())

    def test_escaped_collection_is_rejected_before_native_validator_or_answer_write(self):
        escaped = self.root / tr.BASE / "../../../escaped-collection"
        with self.assertRaisesRegex(ValueError, "private_development_path_required"):
            local.freeze_answers(self.folder, escaped, self.root)
        self.validator.assert_not_called()
        self.assertFalse((self.folder / "answers-frozen.json").exists())

    def test_escaped_report_and_authored_inputs_are_rejected_without_output(self):
        escaped = self.root / tr.BASE / "../../../escaped.json"
        with self.assertRaisesRegex(ValueError, "private_development_path_required"):
            local.report(self.folder, escaped, root=self.root)
        self.assertFalse(escaped.resolve().exists())
        for field in ("source_reviews", "grades"):
            output = self.root / tr.BASE / (field + "-report.json")
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "private_development_path_required"):
                local.report(self.folder, output, root=self.root, **{field: escaped})
            self.assertFalse(output.exists())

    def test_native_collection_failure_is_not_bypassed(self):
        with patch.object(local, "validate_collection", side_effect=ValueError("native_process_not_stopped")):
            with self.assertRaisesRegex(ValueError, "not_stopped"):
                self.freeze()
        self.assertFalse((self.folder / "answers-frozen.json").exists())

    def test_missing_reordered_or_duplicate_answer_is_rejected(self):
        original = deepcopy(self.answers)
        for rows in (original[:-1], list(reversed(original)), original[:-1] + [original[0]]):
            self.validation["rows"] = rows
            with self.assertRaisesRegex(ValueError, "64_ordered"):
                self.freeze()

    def test_reused_context_or_mixed_answer_model_is_rejected(self):
        original = deepcopy(self.answers)
        self.answers[1]["reviewer"]["actorId"] = self.answers[0]["reviewer"]["actorId"]
        with self.assertRaisesRegex(ValueError, "unique_same_model"):
            self.freeze()
        self.validation["rows"] = original
        original[1]["reviewer"]["model"] = "different synthetic model"
        with self.assertRaisesRegex(ValueError, "unique_same_model"):
            self.freeze()

    def test_wrong_packet_or_prior_exposure_is_rejected(self):
        original = deepcopy(self.answers)
        self.answers[0]["packetSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "answer_packet_identity"):
            self.freeze()
        self.validation["rows"] = original
        original[0]["reviewer"]["priorTaskExposure"] = True
        with self.assertRaisesRegex(ValueError, "exposure_contract"):
            self.freeze()

    def test_validator_metadata_must_match_actual_collection_receipt(self):
        self.rewrite(self.receipt_path, lambda value: value.update(contexts=63))
        with self.assertRaisesRegex(ValueError, "receipt_changed"):
            self.freeze()

    def test_native_evidence_mutation_prevents_answer_freeze(self):
        self.raw_path.write_bytes(self.raw_path.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            self.freeze()

    def test_freeze_is_new_only(self):
        self.freeze()
        before = (self.folder / "answers-frozen.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "freeze_must_be_new"):
            self.freeze()
        self.assertEqual(before, (self.folder / "answers-frozen.json").read_bytes())

    def test_frozen_native_answer_text_cannot_be_edited(self):
        self.freeze()
        self.rewrite(self.folder / "answers-frozen.json", lambda value: value["rows"][0]["answers"][0].update(answerKo="edited"))
        with self.assertRaisesRegex(ValueError, "frozen_answers_changed"):
            self.read()

    def test_native_collection_is_revalidated_after_freeze(self):
        self.freeze()
        self.raw_path.write_bytes(self.raw_path.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            self.read()

    def test_changed_local_code_or_protocol_is_rejected(self):
        self.freeze()
        path = self.root / local.PROTOCOL
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "freeze_contract"):
            self.read()

    def test_spawn_version_or_grade_packet_mutation_is_rejected(self):
        self.freeze()
        receipt = self.folder / "answers-freeze.json"
        original = receipt.read_bytes()
        self.rewrite(receipt, lambda value: value.update(version=recovery.VERSION))
        with self.assertRaisesRegex(ValueError, "freeze_contract"):
            self.read()
        receipt.write_bytes(original)
        self.rewrite(self.folder / "grade-packets/R001.json", lambda value: value["answer"]["reviewer"].update(actorId="other"))
        with self.assertRaisesRegex(ValueError, "grade_packet_changed"):
            self.read()

    def test_missing_answers_stay_incomplete_and_do_not_claim_64_contexts(self):
        result = local.report(self.folder, self.root / tr.BASE / "incomplete.json", root=self.root)
        meta = result["recoveryEvidence"]["questionAnswering"]
        self.assertEqual(meta["validatedContexts"], 0)
        self.assertFalse(meta["allAnswersValidatedAndFrozen"])
        self.assertEqual(meta["models"], [])
        self.assertIsNone(result["decision"]["selectedConfiguration"])

    def test_local_report_reuses_exact_frozen_judgments_and_aggregate(self):
        self.freeze()
        source_path = self.root / tr.BASE / "source.json"
        grade_path = self.root / tr.BASE / "grades.json"
        ev.write_new(source_path, {"rows": self.source}); ev.write_new(grade_path, {"rows": self.grades})
        result = local.report(self.folder, self.root / tr.BASE / "report.json", source_path, grade_path, self.root)
        self.assertEqual({k: v for k, v in result.items() if k != "recoveryEvidence"},
                         ev.aggregate(self.bundle, self.source, self.answers, self.grades))
        meta = result["recoveryEvidence"]["questionAnswering"]
        self.assertEqual(meta["validatedContexts"], 64)
        self.assertEqual(meta["models"], [self.collection_receipt["model"]])
        self.assertTrue(meta["allAnswersValidatedAndFrozen"])
        self.assertFalse(result["decision"]["registrationApproved"])

    def test_grades_cannot_be_used_without_local_frozen_answers(self):
        path = self.root / tr.BASE / "grades.json"
        ev.write_new(path, {"rows": []})
        with self.assertRaises(FileNotFoundError):
            local.report(self.folder, self.root / tr.BASE / "report.json", grades=path, root=self.root)


class LocalTransportIntegrationTests(unittest.TestCase):
    def test_actual_export_collect_validation_freeze_and_read_with_synthetic_native_boundary(self):
        from input_execution_v1 import local_question_transport_v1 as transport
        support_case = LocalQuestionEvaluationTests("runTest")
        support_case.setUp(); self.addCleanup(support_case.doCleanups)
        f = support_case
        # Only the completed-native-run validator and the recovery-v2 formal
        # loader are synthetic boundaries. Exercise actual export, receipt,
        # quote conversion, local collection validation and freeze/read code.
        for name in transport.contract.REQUIRED_FREEZE_FILES:
            path = f.root / name
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic required execution file: " + name, encoding="utf-8")
        freeze = {"version": "input-execution-v1-local-question-execution-freeze-v1",
                  "actualQuestionCallsAtFreeze": 0,
                  "files": [tr.reference(f.root, f.root / name) for name in sorted(transport.contract.REQUIRED_FREEZE_FILES)]}
        ev.write_new(f.root / transport.FREEZE, freeze)
        export = f.root / tr.BASE / "synthetic-question-export"
        native_run = f.root / tr.BASE / "synthetic-native-run"
        collection = f.root / tr.BASE / "actual-transport-synthetic-collection"
        contexts = []
        for i, rid in enumerate(tr.IDS):
            packet = f.formal["packets"][rid]
            contexts.append({"reviewId": rid, "contextId": "synthetic-context-" + rid,
                "packetSha256": ev.sha(ev.packed(packet)),
                "draft": {"reviewId": rid, "answers": [
                    {"questionId": q["questionId"], "answerKo": "Synthetic answer only", "reasonKo": "Synthetic reason",
                     "translationEvidence": [packet["translation"]], "cannotDetermine": False} for q in packet["questions"]]},
                "process": {"pid": i + 100, "creationFileTime": i + 1000}})
        native = {"contexts": contexts, "summary": {"syntheticOnly": True, "modelCalls": 0},
                  "evidence": [tr.reference(f.root, f.raw_path)]}
        module = types.ModuleType("input_execution_v1.local_question_runner_v1")
        module.validate_run = lambda *args, **kwargs: deepcopy(native)
        with patch.dict(sys.modules, {module.__name__: module}), \
             patch.object(local, "validate_collection", side_effect=lambda directory, formalPath, root:
                          transport.validate_collection(directory, formalPath, root=root)):
            transport.export_questions(f.folder, export, f.root)
            transport.collect(f.folder, export, native_run, collection, f.root)
            frozen = local.freeze_answers(f.folder, collection, f.root)
            rows, _ = local.read_frozen_answers(local.load_formal(f.folder, f.root), f.root)
        self.assertEqual(len(rows), 64)
        self.assertEqual(frozen["collectionMetadata"]["actualCollaborationSpawns"], 0)
        self.assertEqual({row["reviewer"]["model"] for row in rows}, {transport.contract.MODEL_DESCRIPTION})
        self.assertTrue(all(row["reviewer"]["actorId"].startswith("local-native:") for row in rows))
        self.assertEqual((collection / "answers.json").read_bytes(), (f.folder / "answers-frozen.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
