"""Synthetic files only: never read real test text, outputs, weights, or a GPU."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prepare_v5_review as review


class ReviewPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="v5-review-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / ".training/runs/fixture-review"
        self.data = self.root / ".training/datasets/finance-v5"
        self.base = self.root / (".training/runs/" + review.v5.PARENT_RUN_ID + "/model")
        self.model = self.directory / "model"
        self.selected = self.directory / "checkpoints/step-000002"
        for directory in (self.directory, self.data, self.base, self.model, self.selected / "model"):
            directory.mkdir(parents=True, exist_ok=True)
        for name in review.v5.MODEL_FILES:
            (self.base / name).write_bytes(("fixture baseline " + name).encode())
            for directory in (self.model, self.selected / "model"):
                (directory / name).write_bytes(("fixture candidate " + name).encode())
        self.base_hash = review.sha256(self.base / "model.safetensors")
        self.candidate_hash = review.sha256(self.model / "model.safetensors")
        patcher = patch.object(review.v5, "PARENT_WEIGHT_SHA256", self.base_hash)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("prepare_v5_review.py", "train_v5.py", *review.v5.RUNTIME_DEPENDENCIES):
            file = self.root / "scripts/model-training" / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(Path(review.__file__).with_name(name).read_bytes())
        self.protocol = {"device": "cpu", "effectivePrecision": "fp32", "weightDtype": "fp32",
                         "attentionImplementation": "eager", "doSample": False, "beams": 2,
                         "maxNewTokens": 64, "maxInputTokens": 128,
                         "runtimeVersions": {"torch": "fixture", "transformers": "fixture", "sentencepiece": "fixture"}}
        self.rows = [{"id": "fixture-finance", "source": "The imaginary sum is not -7 dollars.",
                      "target": "가상의 합계는 -7달러가 아니다.", "domain": "finance", "primaryTermId": "G01", "forbiddenTerms": []},
                     {"id": "fixture-general", "source": "Her interest in the invented story did not disappear.",
                      "target": "가상의 이야기에 대한 그녀의 관심은 사라지지 않았다.", "domain": "general",
                      "forbiddenTerms": [{"target": "이자", "reason": "interest refers to curiosity in the source"}]}]
        for row in self.rows:
            row.update({"split": "test", "humanReviewed": False, "provenance": "assistant_authored",
                        "reviewStatus": "fixture_only_not_real_corpus", "sourceSha256": review.text_hash(row["source"]),
                        "targetSha256": review.text_hash(row["target"])})
        self.jsonl(self.data / "test.jsonl", self.rows)
        self.test_hash = review.sha256(self.data / "test.jsonl")
        self.write(self.data / "term-catalog.json", {"fixture": "not a real catalog"})
        self.write(self.data / "annotation-policy.json", {"fixture": "not real annotations"})
        self.write(self.data / "dataset-manifest.json", {"files": {"test": {"sha256": self.test_hash}}})
        inventory = {"version": 1, "selectedStep": 2, "modelPath": self.relative(self.model),
                     "selectedCheckpoint": self.relative(self.selected),
                     "files": {name: review.sha256(self.model / name) for name in review.v5.MODEL_FILES}}
        self.manifest = {"runId": "fixture-review", "status": "evaluated", "completedUpdates": 3,
                         "scriptVersion": review.v5.VERSION, "scriptSha256": review.sha256(Path(review.v5.__file__)),
                         "dependencyFiles": review.v5.dependency_hashes(), "baseModel": review.v5.ORIGINAL_MODEL_ID,
                         "baseRevision": review.v5.ORIGINAL_REVISION, "basePath": self.relative(self.base),
                         "baseFiles": {name: review.sha256(self.base / name) for name in review.v5.MODEL_FILES},
                         "lineage": {"parentRunId": review.v5.PARENT_RUN_ID, "parentSelectedStep": review.v5.PARENT_SELECTED_STEP,
                                     "parentWeightSha256": self.base_hash},
                         "datasets": {"test": {"path": self.relative(self.data / "test.jsonl"), "sha256": self.test_hash}},
                         "datasetPublication": {"manifestPath": self.relative(self.data / "dataset-manifest.json"),
                                                "manifestSha256": review.sha256(self.data / "dataset-manifest.json"),
                                                "catalogSha256": review.sha256(self.data / "term-catalog.json"),
                                                "annotationPolicySha256": review.sha256(self.data / "annotation-policy.json")},
                         "config": {"beams": 2, "max_new_tokens": 64, "max_length": 128}, "gate": copy.deepcopy(review.v5.POLICY),
                         "modelInventory": inventory, "modelInventorySha256": review.v5.canonical_hash(inventory),
                         "modelPath": self.relative(self.model), "selectedCheckpoint": self.relative(self.selected)}
        self.reseal_manifest()
        self.write(self.selected / "checkpoint.json", {"step": 2, "modelSha256": self.candidate_hash})
        scores = self.scores()
        gate = review.v5.gate(scores, scores)
        self.training = {"runId": "fixture-review", "finishedAt": "2026-01-01T01:00:00+00:00",
                         "selectedStep": 2, "completedUpdates": 3, "baseWeightFileSha256": self.base_hash,
                         "trainedWeightFileSha256": self.candidate_hash, "glossaryApplied": False,
                         "weightEvidence": {"changedTensorCount": 1}, "baselineDev": scores, "selectedDev": scores,
                         "devGate": gate, "modelPath": self.relative(self.model), "modelInventory": inventory,
                         "modelInventorySha256": review.v5.canonical_hash(inventory)}
        self.evaluation = {"runId": "fixture-review", "evaluatedAt": "2026-01-01T02:00:00+00:00",
                           "baseWeightFileSha256": self.base_hash, "trainedWeightFileSha256": self.candidate_hash,
                           "glossaryApplied": False, "modelPath": self.relative(self.model), "testDataSha256": self.test_hash,
                           "baseline": scores, "finetuned": scores, "devGate": gate, "testGate": gate,
                           "promotionEligible": True, "gatePolicy": copy.deepcopy(review.v5.POLICY)}
        self.ledger_path = self.root / ".training/test-evaluations" / (self.test_hash + ".json")
        self.ledger = {"runId": "fixture-review", "modelSha256": self.candidate_hash, "testDataSha256": self.test_hash,
                       "status": "complete", "completedAt": "2026-01-01T03:00:00+00:00",
                       "summaryPath": self.relative(self.directory / "evaluation-summary.json")}
        self.write(self.directory / "training-summary.json", self.training)
        self.write(self.directory / "evaluation-summary.json", self.evaluation)
        self.write(self.ledger_path, self.ledger)
        for filename, model_hash in (("baseline-test.jsonl", self.base_hash), ("finetuned-test.jsonl", self.candidate_hash)):
            self.jsonl(self.directory / filename, [{"index": i, "id": row["id"], "sourceHash": row["sourceSha256"],
                                                   "modelIdentity": model_hash, "generationProtocol": self.protocol,
                                                   "prediction": ("원시 가상 번역 " if filename.startswith("baseline") else "다른 가상 번역 ") + str(i),
                                                   "generatedTokens": 12, "atLengthLimit": False} for i, row in enumerate(self.rows)])

    def relative(self, path):
        return path.relative_to(self.root).as_posix()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(review.json_bytes(value))

    def jsonl(self, path, values):
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in values), "utf-8")

    def reseal_manifest(self):
        self.manifest["identitySha256"] = review.v5.canonical_hash({key: self.manifest.get(key) for key in review.v5.IDENTITY_KEYS})
        self.write(self.directory / "manifest.json", self.manifest)

    def scores(self):
        result = {"count": 2, "generationProtocol": self.protocol, "chrF": 50, "bleu": 30,
                  "numericPreservation": 1, "emptyOutputs": 0, "cappedOutputs": 0,
                  "termAccuracy": 1, "termCount": 1, "forbiddenTermRows": 1, "forbiddenTermCount": 1, "forbiddenTermHits": 0}
        return {**result, "byDomain": {name: dict(result) for name in ("finance", "general")}}

    def run_prepare(self):
        return review.prepare("fixture-review", self.root)

    def test_complete_cache_becomes_anonymous_and_reuses_identical_artifacts_without_inference(self):
        with patch.object(review.v5.engine, "predict", side_effect=AssertionError("No inference")), patch.object(review.v5.engine, "stack", side_effect=AssertionError("No model loading")):
            path = self.run_prepare()
            before = {file.name: (review.sha256(file), file.stat().st_mtime_ns) for file in path.parent.iterdir()}
            self.assertEqual(path, self.run_prepare())
            self.assertEqual(before, {file.name: (review.sha256(file), file.stat().st_mtime_ns) for file in path.parent.iterdir()})
        anonymous = review.read_jsonl(path.parent / "anonymous-review.jsonl")
        key = review.read_json(path.parent / "review-key.json")["mappings"]
        self.assertEqual([r["id"] for r in anonymous], [r["id"] for r in self.rows])
        self.assertEqual(anonymous[1]["forbiddenTerms"], self.rows[1]["forbiddenTerms"])
        for row, mapping in zip(anonymous, key):
            self.assertFalse(row["humanReviewed"])
            self.assertFalse(row["referenceHumanReviewed"])
            self.assertIsNone(row["reviewerType"])
            self.assertEqual(set(mapping["labels"].values()), {"baseline", "candidate"})
            for choice in row["choices"]:
                self.assertEqual(set(choice), {"label", "translation", "review"})
                self.assertIsNone(choice["review"]["severity"])
                name = mapping["labels"][choice["label"]]
                filename = "baseline-test.jsonl" if name == "baseline" else "finetuned-test.jsonl"
                original = next(p for p in review.read_jsonl(self.directory / filename) if p["id"] == row["id"])
                self.assertEqual(choice["translation"], original["prediction"])

    def test_incomplete_ledger_blocks_before_any_heldout_text_read(self):
        self.ledger["status"] = "evaluating"
        self.write(self.ledger_path, self.ledger)
        with patch.object(review, "read_jsonl", side_effect=AssertionError("Must not open heldout text")):
            with self.assertRaisesRegex(ValueError, "ledger"):
                self.run_prepare()
        self.assertFalse((self.directory / "assistant-review-v5").exists())

    def test_mismatched_run_selected_step_dev_gate_policy_and_model_are_rejected(self):
        mutations = [("runId", "other"), ("selectedStep", 1), ("devGate", {"passed": False}),
                     ("trainedWeightFileSha256", "a" * 64), ("glossaryApplied", True)]
        for key, value in mutations:
            with self.subTest(key=key):
                changed = {**self.training, key: value}
                self.write(self.directory / "training-summary.json", changed)
                with self.assertRaises(ValueError):
                    self.run_prepare()
        self.write(self.directory / "training-summary.json", self.training)
        self.evaluation["gatePolicy"]["minimumFinancialTermAccuracy"] = 0.5
        self.write(self.directory / "evaluation-summary.json", self.evaluation)
        with self.assertRaisesRegex(ValueError, "policy"):
            self.run_prepare()

    def test_changed_tokenizer_and_source_bytes_fail(self):
        path = self.model / "target.spm"
        original = path.read_bytes()
        path.write_bytes(b"changed tokenizer")
        with self.assertRaisesRegex(ValueError, "runtime file"):
            self.run_prepare()
        path.write_bytes(original)
        (self.data / "test.jsonl").write_text("changed source", "utf-8")
        with self.assertRaisesRegex(ValueError, "test bytes"):
            self.run_prepare()

    def test_every_prediction_identity_field_and_completion_are_validated(self):
        path = self.directory / "baseline-test.jsonl"
        original = review.read_jsonl(path)
        for key, value in (("id", "wrong"), ("index", True), ("sourceHash", "b" * 64), ("modelIdentity", self.candidate_hash),
                           ("generationProtocol", {}), ("generatedTokens", -1), ("atLengthLimit", "false"), ("atLengthLimit", True)):
            with self.subTest(key=key):
                changed = copy.deepcopy(original)
                changed[0][key] = value
                self.jsonl(path, changed)
                with self.assertRaises(ValueError):
                    self.run_prepare()
        for values in (original[:-1], original + original[:1], original[::-1]):
            self.jsonl(path, values)
            with self.assertRaises(ValueError):
                self.run_prepare()

    def test_failed_test_still_allows_unscored_semantic_review(self):
        self.evaluation["finetuned"] = copy.deepcopy(self.evaluation["finetuned"])
        self.evaluation["finetuned"]["byDomain"]["finance"]["termAccuracy"] = 0.5
        self.evaluation["testGate"] = review.v5.gate(self.evaluation["baseline"], self.evaluation["finetuned"])
        self.evaluation["promotionEligible"] = False
        self.write(self.directory / "evaluation-summary.json", self.evaluation)
        self.assertTrue(self.run_prepare().is_file())

    def test_modified_input_or_output_cannot_overwrite_completed_review(self):
        manifest_path = self.run_prepare()
        output = manifest_path.parent / "anonymous-review.jsonl"
        original = output.read_bytes()
        output.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "output changed"):
            self.run_prepare()
        self.assertEqual(output.read_bytes(), original + b"\n")
        output.write_bytes(original)
        predictions = review.read_jsonl(self.directory / "baseline-test.jsonl")
        predictions[0]["prediction"] = "different stored raw output"
        self.jsonl(self.directory / "baseline-test.jsonl", predictions)
        with self.assertRaisesRegex(ValueError, "input identity changed"):
            self.run_prepare()
        self.assertEqual(output.read_bytes(), original)

    def test_invalid_path_and_incomplete_pending_directory_are_preserved(self):
        with self.assertRaisesRegex(ValueError, "run ID"):
            review.prepare("../other", self.root)
        pending = self.directory / "assistant-review-v5.pending"
        pending.mkdir()
        marker = pending / "partial.txt"
        marker.write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            self.run_prepare()
        self.assertEqual(marker.read_bytes(), b"preserve")
        self.assertFalse((self.directory / "assistant-review-v5").exists())


if __name__ == "__main__":
    unittest.main()
