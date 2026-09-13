"""Mocked integration checks; no actual corpus, model, GPU or final test is read."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


training = module("sense_train_test", "train.py")
with patch.dict(sys.modules, {"train": training}):
    comparison = module("sense_comparison_test", "evaluate_v4.py")
with patch.dict(sys.modules, {"train": training, "evaluate_v4": comparison}):
    probe = module("sense_probe_test", "probe_word_sense.py")


class WordSenseProbeIntegrationTests(unittest.TestCase):
    def setUp(self):
        training.WORK_ROOT.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="unit-word-sense-", dir=training.WORK_ROOT)
        self.root = Path(self.temporary.name).resolve()
        self.run = self.root / "run"
        self.run.mkdir()
        (self.root / "datasets").mkdir()
        self.data = self.root / "datasets" / "diagnostic.jsonl"
        self.rows = [
            {"id": "sense-finance", "source": "The bond pays 5 percent interest.", "target": "채권은 5퍼센트의 이자를 지급한다.",
             "domain": "finance", "split": "diagnostic", "humanReviewed": False, "focusWord": "interest", "expectedSense": "이자"},
            {"id": "sense-general", "source": "Her interest in painting grew.", "target": "그녀의 그림에 대한 관심이 커졌다.",
             "domain": "general", "split": "diagnostic", "humanReviewed": False, "focusWord": "interest", "expectedSense": "관심"},
        ]
        self.write_rows()
        self.protocol = {"device": "cpu", "maxNewTokens": 384, "maxInputTokens": 384, "beams": 4}
        self.models = {name: {"weightSha256": name + "-fixture"} for name in comparison.MODEL_NAMES}
        self.paths = {name: self.root / "mock-model-paths" / name for name in comparison.MODEL_NAMES}
        self.identity = {"runId": "unit-fixture", "previousRunId": "unit-previous", "fixture": True}
        self.frozen = {"identity": self.identity, "identitySha256": comparison.canonical_hash(self.identity),
                       "frozenAt": "2026-09-09T00:00:00+00:00"}
        self.manifest = {"runId": "unit-fixture", "datasets": {"test": {"sha256": "synthetic-test-identity"}}}
        self.summary = {"trainedWeightFileSha256": self.models["candidate"]["weightSha256"]}
        self.values = (self.run, self.manifest, self.summary, SimpleNamespace(max_length=384),
                       self.paths, self.models, self.protocol, {})
        self.ledger = self.root / "synthetic-ledger.json"
        training.write_json(self.ledger, {"runId": "unit-fixture", "testDataSha256": "synthetic-test-identity",
                                         "modelSha256": self.summary["trainedWeightFileSha256"], "status": "complete",
                                         "completedAt": "2026-09-09T01:00:00+00:00"})
        training.write_json(self.run / "comparison-v4/manifest.json", self.frozen)
        reused = {"baseline": self.run / "baseline-test.jsonl", "candidate": self.run / "finetuned-test.jsonl"}
        outputs = {"summary": self.run / "comparison-v4/test-summary.json",
                   "previousPredictions": self.run / "comparison-v4/previous-test.jsonl",
                   "anonymousReview": self.run / "comparison-v4/test-anonymous-review.jsonl",
                   "reviewKey": self.run / "comparison-v4/test-review-key.json"}
        for name, path in {**reused, **outputs}.items():
            training.write_json(path, {"syntheticCompletedFile": name})
        inventory = lambda paths: {name: {"path": training.relative(path), "sha256": training.sha256(path)} for name, path in paths.items()}
        self.consumed = {"status": "complete", "split": "test", "identitySha256": self.frozen["identitySha256"],
                         "dataSha256": "synthetic-test-identity", "reusedPredictionFiles": inventory(reused), "outputs": inventory(outputs)}
        training.write_json(self.run / "comparison-v4/test-consumption.json", self.consumed)
        self.tokenizer = Mock()
        self.tokenizer.from_pretrained.return_value = object()
        self.stack = (None, object(), self.tokenizer, "cpu")
        self.generated = []
        self.interrupt_at = None

    def tearDown(self):
        self.assertTrue(self.root.is_relative_to(training.WORK_ROOT.resolve()))
        self.assertTrue(self.root.name.startswith("unit-word-sense-"))
        self.temporary.cleanup()

    def write_rows(self):
        self.data.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.rows), "utf-8")

    def fake_predict(self, _model, _tokenizer, rows, _torch, _device, _args, cache, weight_hash):
        cached = comparison.read_predictions(cache) if cache.exists() else []
        for index in range(len(cached), len(rows)):
            row = rows[index]
            item = {"index": index, "id": row["id"], "sourceHash": hashlib.sha256(row["source"].encode()).hexdigest(),
                    "modelIdentity": weight_hash, "generationProtocol": self.protocol,
                    "prediction": row["target"], "generatedTokens": 12, "atLengthLimit": False}
            training.append_json(cache, item)
            cached.append(item)
            self.generated.append((weight_hash, index))
            if self.interrupt_at == len(self.generated):
                raise training.StopRequested()
        return cached

    def invoke(self):
        with patch.object(comparison, "run_directory", return_value=self.run), \
                patch.object(comparison, "prepare", return_value=(self.values, self.stack, self.identity)), \
                patch.object(training, "WORK_ROOT", self.root), patch.object(training, "test_ledger", return_value=self.ledger), \
                patch.object(training, "encode_rows", return_value=[]), patch.object(training, "load_model", return_value=object()), \
                patch.object(training, "predict", side_effect=self.fake_predict), patch.object(training, "emit"):
            probe.run("unit-fixture", training.relative(self.data))

    def test_complete_probe_creates_all_models_and_anonymous_context_fields_without_promoting(self):
        self.invoke()
        self.assertEqual(len(self.generated), 6)
        output = self.run / "word-sense"
        complete = comparison.read_json(output / "completion.json")
        self.assertEqual(set(complete["files"]), {"predictions:baseline", "predictions:previous", "predictions:candidate", "review", "key", "summary"})
        summary = comparison.read_json(output / "summary.json")
        self.assertIsNone(summary["semanticAccuracy"])
        self.assertFalse(summary["promotionAuthorized"])
        reviews = comparison.read_predictions(output / "anonymous-review.jsonl")
        self.assertEqual([row["expectedSense"] for row in reviews], ["이자", "관심"])
        for row in reviews:
            for choice in row["choices"]:
                self.assertIsNone(choice["review"]["contextualWordSensePreserved"])

    def test_second_completed_invocation_does_not_regenerate_or_initialize_tokenizer(self):
        self.invoke()
        original = list(self.generated)
        calls = self.tokenizer.from_pretrained.call_count
        self.invoke()
        self.assertEqual(self.generated, original)
        self.assertEqual(self.tokenizer.from_pretrained.call_count, calls)

    def test_interrupted_probe_resumes_only_remaining_rows(self):
        self.interrupt_at = 3
        with self.assertRaises(training.StopRequested):
            self.invoke()
        self.assertFalse((self.run / "word-sense/completion.json").exists())
        self.interrupt_at = None
        self.invoke()
        self.assertEqual(len(self.generated), 6)
        self.assertEqual(len(set(self.generated)), 6)

    def test_incomplete_final_comparison_prevents_probe(self):
        training.write_json(self.run / "comparison-v4/test-consumption.json", {**self.consumed, "status": "generating-previous"})
        with self.assertRaisesRegex(ValueError, "Complete the sealed"):
            self.invoke()
        self.assertEqual(self.generated, [])

    def test_changed_final_comparison_identity_prevents_probe(self):
        for key, value in (("identitySha256", "different"), ("dataSha256", "different"), ("split", "dev")):
            with self.subTest(key=key):
                training.write_json(self.run / "comparison-v4/test-consumption.json", {**self.consumed, key: value})
                with self.assertRaisesRegex(ValueError, "consumption identity"):
                    self.invoke()
        self.assertEqual(self.generated, [])

    def test_changed_final_output_bytes_prevent_probe(self):
        changed = self.run / "comparison-v4/test-summary.json"
        changed.write_text('{"changed":true}', "utf-8")
        with self.assertRaisesRegex(ValueError, "final comparison output changed"):
            self.invoke()
        self.assertEqual(self.generated, [])

    def test_missing_final_file_inventory_prevents_probe(self):
        consumed = copy.deepcopy(self.consumed)
        consumed["outputs"].pop("previousPredictions")
        training.write_json(self.run / "comparison-v4/test-consumption.json", consumed)
        with self.assertRaisesRegex(ValueError, "inventory is incomplete"):
            self.invoke()
        self.assertEqual(self.generated, [])

    def test_changed_diagnostic_data_is_rejected_on_resume(self):
        self.invoke()
        self.rows[0]["expectedSense"] = "changed fixture label"
        self.write_rows()
        with self.assertRaisesRegex(ValueError, "immutable output differs"):
            self.invoke()
        self.assertEqual(len(self.generated), 6)

    def test_invalid_reference_metadata_is_rejected_before_inference(self):
        for key, value in (("humanReviewed", True), ("focusWord", ["interest"]), ("expectedSense", " ")):
            original = copy.deepcopy(self.rows)
            self.rows[0][key] = value
            self.write_rows()
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "honest word-sense"):
                self.invoke()
            self.rows = original
        self.assertEqual(self.generated, [])

    def test_completed_probe_detects_modified_predictions(self):
        self.invoke()
        (self.run / "word-sense/previous.jsonl").write_text('{"changed":true}', "utf-8")
        with self.assertRaisesRegex(ValueError, "word-sense output changed"):
            self.invoke()
        self.assertEqual(len(self.generated), 6)

    def test_partial_cache_model_identity_cannot_change_on_resume(self):
        self.interrupt_at = 3
        with self.assertRaises(training.StopRequested):
            self.invoke()
        cache = self.run / "word-sense/previous.jsonl"
        values = comparison.read_predictions(cache)
        values[0]["modelIdentity"] = "another-model"
        cache.write_text(json.dumps(values[0], ensure_ascii=False) + "\n", "utf-8")
        self.interrupt_at = None
        with self.assertRaisesRegex(ValueError, "identity/order/protocol"):
            self.invoke()
        self.assertEqual(len(self.generated), 3)


if __name__ == "__main__":
    unittest.main()
