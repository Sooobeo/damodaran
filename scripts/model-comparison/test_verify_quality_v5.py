"""Synthetic sealed models only; no real model, training data or test outputs."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import verify_quality_v5 as api


def token_objects():
    return {key: {"content": value, **{name: False for name in api.TOKEN_OPTIONS}}
            for key, value in api.TOKENS.items()}


class SpecialTokenSerializationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="special-token-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tokens.json"

    def read(self, value):
        self.path.write_text(json.dumps(value), "utf-8")
        return api.special_token_map(self.path)

    def test_exact_legacy_and_serialized_default_options_are_equivalent(self):
        self.assertEqual(self.read(api.TOKENS), self.read(token_objects()))

    def test_modified_token_content_is_rejected(self):
        for representation in (dict(api.TOKENS), token_objects()):
            for key in api.TOKENS:
                changed = copy.deepcopy(representation)
                if isinstance(changed[key], dict):
                    changed[key]["content"] = "<wrong>"
                else:
                    changed[key] = "<wrong>"
                with self.subTest(key=key, representation=type(changed[key]).__name__):
                    with self.assertRaisesRegex(api.infer.InferenceError, "content"):
                        self.read(changed)

    def test_nondefault_or_nonboolean_options_are_rejected(self):
        for option in api.TOKEN_OPTIONS:
            for value in (True, 0, 1, "false", None):
                changed = token_objects()
                changed["eos_token"][option] = value
                with self.subTest(option=option, value=value):
                    with self.assertRaisesRegex(api.infer.InferenceError, "options"):
                        self.read(changed)

    def test_unknown_or_missing_token_options_are_rejected(self):
        for option in ("special", "__type", "new_unknown_option"):
            changed = token_objects()
            changed["pad_token"][option] = False
            with self.subTest(option=option):
                with self.assertRaisesRegex(api.infer.InferenceError, "options"):
                    self.read(changed)
        changed = token_objects()
        del changed["pad_token"]["normalized"]
        with self.assertRaisesRegex(api.infer.InferenceError, "options"):
            self.read(changed)

    def test_unknown_or_missing_special_token_is_rejected(self):
        for value in ({**api.TOKENS, "bos_token": "<s>"}, {"eos_token": "</s>"}, []):
            with self.assertRaisesRegex(api.infer.InferenceError, "keys"):
                self.read(value)

    def test_duplicate_nonfinite_and_malformed_json_are_rejected(self):
        values = ('{"eos_token":"</s>","eos_token":"</s>"}',
                  '{"eos_token":NaN}', '{"eos_token":')
        for value in values:
            self.path.write_text(value, "utf-8")
            with self.subTest(value=value):
                with self.assertRaises(api.infer.InferenceError):
                    api.special_token_map(self.path)


class CompletedModelIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.original_root = api.v5.WORK_ROOT.resolve()
        self.temp = tempfile.TemporaryDirectory(prefix="unit-quality-v5-", dir=self.original_root)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "runs/fixture"
        self.model = self.run / "model"
        self.selected = self.run / "checkpoints/step-1"
        self.parent = self.root / "parent"
        for path in (self.model, self.selected / "model", self.parent):
            path.mkdir(parents=True)
        self.files = {}
        for name in api.v5.MODEL_FILES:
            original = ("parent-" + name).encode()
            changed = b"updated-model" if name == "model.safetensors" else original
            if name == "special_tokens_map.json":
                original = json.dumps(api.TOKENS).encode()
                changed = json.dumps(token_objects()).encode()
            (self.parent / name).write_bytes(original)
            (self.model / name).write_bytes(changed)
            (self.selected / "model" / name).write_bytes(changed)
            self.files[name] = api.infer.sha256(self.parent / name)
        self.lineage = {"originalBase": {"model": api.v5.ORIGINAL_MODEL_ID, "revision": api.v5.ORIGINAL_REVISION,
                                       "weightSha256": api.v5.ORIGINAL_WEIGHT_SHA256}, "parentSelectedStep": 218}
        self.manifest = {"runId": "fixture", "scriptVersion": api.v5.VERSION,
                         "scriptSha256": api.infer.sha256(Path(api.v5.__file__)), "dependencyFiles": api.v5.dependency_hashes(),
                         "baseModel": api.v5.ORIGINAL_MODEL_ID, "baseRevision": api.v5.ORIGINAL_REVISION,
                         "basePath": api.infer.relative(self.parent), "baseFiles": self.files,
                         "originalBase": self.lineage["originalBase"], "baselineRole": "synthetic-parent", "lineage": self.lineage,
                         "datasets": {"test": {"sha256": "synthetic-sealed-test"}}, "datasetPublication": {},
                         "config": {}, "gate": api.v5.POLICY, "selectedCheckpoint": api.infer.relative(self.selected)}
        self.manifest["identitySha256"] = api.v5.canonical_hash({k: self.manifest[k] for k in api.IDENTITY_KEYS})
        self.weight = api.infer.sha256(self.model / "model.safetensors")
        self.training = {"runId": "fixture", "baseWeightFileSha256": api.v5.PARENT_WEIGHT_SHA256,
                         "completedUpdates": 1, "weightEvidence": {"changedTensorCount": 1},
                         "modelPath": api.infer.relative(self.model), "selectedStep": 1,
                         "trainedWeightFileSha256": self.weight, "devGate": {"passed": True}}
        self.write(self.selected / "checkpoint.json", {"step": 1, "modelSha256": self.weight})
        self.stamp_inventory()
        self.patches = [patch.object(api.v5, "WORK_ROOT", self.root),
                        patch.object(api.v5.engine, "WORK_ROOT", self.root),
                        patch.object(api.v5, "verify_parent", return_value=(self.parent, self.files, self.lineage)),
                        patch.object(api.infer, "validate_tokenizer_files", return_value={})]
        for item in self.patches:
            item.start()

    def write(self, path, value):
        path.write_text(json.dumps(value), "utf-8")

    def stamp_inventory(self):
        inventory = api.v5.model_inventory(self.run, self.manifest, self.training)
        stamp = {"modelInventory": inventory, "modelInventorySha256": api.v5.canonical_hash(inventory)}
        self.manifest.update(stamp)
        self.training.update(stamp)
        self.write(self.run / "manifest.json", self.manifest)
        self.write(self.run / "training-summary.json", self.training)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        assert self.root.is_relative_to(self.original_root) and self.root.name.startswith("unit-quality-v5-")
        self.temp.cleanup()

    def evaluation(self):
        result = {"runId": "fixture", "trainedWeightFileSha256": self.weight,
                  "baseWeightFileSha256": api.v5.PARENT_WEIGHT_SHA256, "gatePolicy": api.v5.POLICY,
                  "modelPath": api.infer.relative(self.model), "promotionEligible": True,
                  "devGate": {"passed": True}, "testGate": {"passed": True}, "testDataSha256": "synthetic-sealed-test"}
        self.write(self.run / "evaluation-summary.json", result)
        return result

    def ledger(self):
        path = api.v5.engine.test_ledger(self.manifest)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.write(path, {"status": "complete", "runId": "fixture", "modelSha256": self.weight,
                         "testDataSha256": "synthetic-sealed-test",
                         "summaryPath": api.infer.relative(self.run / "evaluation-summary.json")})
        return path

    def test_valid_serialization_preserves_both_original_hashes_and_experimental_status(self):
        result = api.verified_model("fixture")
        self.assertEqual(result["weightHash"], self.weight)
        self.assertFalse(result["promotionEligible"])
        self.assertFalse(result["testWasEvaluated"])
        report = result["tokenizerSerialization"]
        self.assertTrue(report["bytesDiffer"])
        self.assertEqual(report["parentSha256"], self.files["special_tokens_map.json"])
        self.assertEqual(report["selectedSha256"], result["fileHashes"]["special_tokens_map.json"])

    def test_passing_evaluation_still_requires_exact_completed_ledger(self):
        self.evaluation()
        with self.assertRaisesRegex(api.infer.InferenceError, "completion ledger"):
            api.verified_model("fixture")
        path = self.ledger()
        self.assertTrue(api.verified_model("fixture")["promotionEligible"])
        ledger = json.loads(path.read_text("utf-8"))
        ledger["status"] = "evaluating"
        self.write(path, ledger)
        with self.assertRaisesRegex(api.infer.InferenceError, "completion ledger"):
            api.verified_model("fixture")

    def test_evaluation_identity_and_test_identity_are_checked(self):
        original = self.evaluation()
        self.ledger()
        for key, value in (("runId", "other"), ("trainedWeightFileSha256", "other"),
                           ("baseWeightFileSha256", "other"), ("gatePolicy", {}),
                           ("testDataSha256", "other-test"), ("devGate", {"passed": False})):
            self.write(self.run / "evaluation-summary.json", {**original, key: value})
            with self.subTest(key=key):
                with self.assertRaisesRegex(api.infer.InferenceError, "evaluation identity"):
                    api.verified_model("fixture")

    def test_selected_copy_checkpoint_and_sealed_inventory_are_checked(self):
        path = self.model / "special_tokens_map.json"
        original = path.read_bytes()
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(api.infer.InferenceError, "model/tokenizer integrity"):
            api.verified_model("fixture")
        path.write_bytes(original)
        for folder in (self.model, self.selected / "model"):
            (folder / "source.spm").write_bytes(b"changed-together")
        with self.assertRaisesRegex(api.infer.InferenceError, "runtime inventory"):
            api.verified_model("fixture")

    def test_resealed_synthetic_special_token_change_still_fails_semantic_check(self):
        value = token_objects()
        value["pad_token"]["content"] = "<other>"
        for folder in (self.model, self.selected / "model"):
            self.write(folder / "special_tokens_map.json", value)
        self.stamp_inventory()
        with self.assertRaisesRegex(api.infer.InferenceError, "content"):
            api.verified_model("fixture")

    def test_resealed_non_special_tokenizer_change_is_not_relaxed(self):
        for folder in (self.model, self.selected / "model"):
            (folder / "tokenizer_config.json").write_bytes(b"changed-options")
        self.stamp_inventory()
        with self.assertRaisesRegex(api.infer.InferenceError, "tokenizer differs"):
            api.verified_model("fixture")

    def test_training_config_and_frozen_dependency_checks_remain(self):
        self.manifest["config"] = {"beams": 99}
        self.write(self.run / "manifest.json", self.manifest)
        with self.assertRaisesRegex(api.infer.InferenceError, "frozen policy"):
            api.verified_model("fixture")
        self.manifest["config"] = {}
        self.write(self.run / "manifest.json", self.manifest)
        with patch.object(api.v5, "dependency_hashes", return_value={}):
            with self.assertRaisesRegex(api.infer.InferenceError, "dependency integrity"):
                api.verified_model("fixture")
        with patch.object(api, "FROZEN_INFERENCE_SHA256", "0" * 64):
            with self.assertRaisesRegex(api.infer.InferenceError, "inference dependency"):
                api.verified_model("fixture")


if __name__ == "__main__":
    unittest.main()
