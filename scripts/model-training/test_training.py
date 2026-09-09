"""Small offline tests for data isolation, promotion gates, and weight evidence.

Run: .venv-training/Scripts/python.exe scripts/model-training/test_training.py
Only synthetic fixtures under .training/unit-training-* are written. This module
does not read the real train/dev/final-test corpus or load any translation model.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("finance_training_under_test", Path(__file__).with_name("train.py"))
training = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(training)
INFER_SPEC = importlib.util.spec_from_file_location("finance_inference_under_test", Path(__file__).with_name("infer.py"))
inference = importlib.util.module_from_spec(INFER_SPEC)
with patch.dict(sys.modules, {"train": training}):
    INFER_SPEC.loader.exec_module(inference)


def row(identifier, source, target, split):
    return {"id": identifier, "source": source, "target": target, "domain": "finance",
            "terms": [], "provenance": "unit-test fixture; not a real translation", "split": split}


class DatasetIsolationTests(unittest.TestCase):
    def setUp(self):
        training.WORK_ROOT.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="unit-training-", dir=training.WORK_ROOT)
        self.directory = Path(self.temporary.name).resolve()
        self.assertTrue(self.directory.is_relative_to(training.WORK_ROOT.resolve()))
        self.assertTrue(self.directory.name.startswith("unit-training-"))

    def tearDown(self):
        # Verify the resolved deletion target before TemporaryDirectory cleanup.
        self.assertTrue(self.directory.is_relative_to(training.WORK_ROOT.resolve()))
        self.assertTrue(self.directory.name.startswith("unit-training-"))
        self.temporary.cleanup()

    def validate(self, train_row, dev_row):
        values = {"train": train_row, "dev": dev_row,
                  "test": row("test-unrelated", "A clerk attached a receipt to the delivery record.",
                              "직원이 배송 기록에 영수증을 붙였다.", "test")}
        paths = {}
        for split, value in values.items():
            path = self.directory / (split + ".jsonl")
            path.write_text(json.dumps(value, ensure_ascii=False) + "\n", "utf-8")
            paths[split + "_data"] = training.relative(path)
        return training.validate_data(SimpleNamespace(**paths), self.directory)

    def report_kinds(self):
        report = json.loads((self.directory / "data-validation.json").read_text("utf-8"))
        self.assertFalse(report["passed"])
        return {item["kind"] for item in report["issues"]}

    def test_cross_split_number_substitutions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Dataset leakage check failed"):
            self.validate(
                row("train-numbers", "The project earns USD 120 after 3 years.",
                    "이 사업은 3년 뒤 USD 120을 벌어들인다.", "train"),
                row("dev-numbers", "The project earns USD 260 after 5 years.",
                    "이 사업은 5년 뒤 USD 260을 벌어들인다.", "dev"))
        self.assertIn("numeric-template-leakage", self.report_kinds())

    def test_case_and_punctuation_cannot_hide_source_duplicates(self):
        with self.assertRaisesRegex(ValueError, "Dataset leakage check failed"):
            self.validate(
                row("train-case", "Review the accounting note carefully.", "회계 주석을 주의 깊게 검토한다.", "train"),
                row("dev-case", "REVIEW the accounting note carefully!", "회계 주석을 주의 깊게 검토한다.", "dev"))
        self.assertIn("normalized-source-duplicate", self.report_kinds())

    def test_a_minor_adjective_edit_is_not_an_independent_split(self):
        with self.assertRaisesRegex(ValueError, "Dataset leakage check failed"):
            self.validate(
                row("train-near", "The analyst carefully checks every financial assumption before approving the revised valuation report.",
                    "분석가는 수정된 가치평가 보고서를 승인하기 전에 모든 재무 가정을 주의 깊게 확인한다.", "train"),
                row("dev-near", "The analyst carefully checks every financial assumption before approving the updated valuation report.",
                    "분석가는 갱신된 가치평가 보고서를 승인하기 전에 모든 재무 가정을 주의 깊게 확인한다.", "dev"))
        self.assertIn("near-template-leakage", self.report_kinds())

    def test_shared_subject_without_a_shared_template_is_accepted(self):
        rows, report = self.validate(
            row("train-independent", "Debt financing creates a contractual obligation to lenders.",
                "부채를 통한 자금조달은 대출기관에 대한 계약상 의무를 만든다.", "train"),
            row("dev-independent", "Why did the reviewer question the schedule of debt repayments?",
                "검토자는 왜 부채 상환 일정에 의문을 제기했는가?", "dev"))
        self.assertTrue(report["passed"])
        self.assertEqual(report["issues"], [])
        self.assertEqual({key: len(value) for key, value in rows.items()}, {"train": 1, "dev": 1, "test": 1})


def scores(term_accuracy=0.4, chrf=60.0, bleu=35.0, numeric=1.0):
    return {"termAccuracy": term_accuracy, "chrF": chrf, "bleu": bleu,
            "numericPreservation": numeric, "emptyOutputs": 0, "cappedOutputs": 0}


def improving_pair():
    base, candidate = scores(), scores(0.6, 65.0, 40.0)
    base["byDomain"] = {"finance": scores(), "general": scores(None)}
    candidate["byDomain"] = {"finance": scores(0.6, 65.0, 40.0), "general": scores(None)}
    return base, candidate


class PromotionGateTests(unittest.TestCase):
    def test_financial_improvement_and_general_retention_can_pass(self):
        base, candidate = improving_pair()
        result = training.gate(base, candidate)
        self.assertTrue(result["passed"], result)
        self.assertTrue(result["byDomain"]["general"]["passed"])

    def test_overall_and_term_gains_do_not_mask_general_quality_regression(self):
        for metric, reason in (("chrF", "chrf-regression"), ("bleu", "bleu-regression")):
            with self.subTest(metric=metric):
                base, candidate = improving_pair()
                candidate["byDomain"]["general"][metric] = base["byDomain"]["general"][metric] - 1.5
                result = training.gate(base, candidate)
                self.assertFalse(result["passed"])
                self.assertIn("general-gate-failed", result["reasons"])
                self.assertIn(reason, result["byDomain"]["general"]["reasons"])
                self.assertTrue(result["byDomain"]["finance"]["passed"])

    def test_improved_overall_numbers_do_not_mask_general_numeric_regression(self):
        base, candidate = improving_pair()
        # Ten finance rows improve from eight to ten preserved rows; two general
        # rows decline from two to one. Overall preservation still increases.
        base["numericPreservation"], candidate["numericPreservation"] = 10 / 12, 11 / 12
        base["byDomain"]["finance"]["numericPreservation"] = 0.8
        candidate["byDomain"]["general"]["numericPreservation"] = 0.5
        result = training.gate(base, candidate)
        self.assertFalse(result["passed"])
        self.assertIn("general-gate-failed", result["reasons"])
        self.assertIn("numeric-regression", result["byDomain"]["general"]["reasons"])
        self.assertNotIn("numeric-regression", result["reasons"])

    def test_missing_general_evaluation_cannot_be_called_retention(self):
        base, candidate = improving_pair()
        del candidate["byDomain"]["general"]
        result = training.gate(base, candidate)
        self.assertFalse(result["passed"])
        self.assertIn("general-evaluation-missing", result["reasons"])

    def test_empty_or_truncated_outputs_fail_despite_metric_gains(self):
        for field in ("emptyOutputs", "cappedOutputs"):
            with self.subTest(field=field):
                base, candidate = improving_pair()
                candidate[field] = 1
                result = training.gate(base, candidate)
                self.assertFalse(result["passed"])
                self.assertIn("empty-or-capped-output", result["reasons"])

    def test_changed_decoding_settings_do_not_qualify_as_a_fair_improvement(self):
        base, candidate = improving_pair()
        base["generationProtocol"] = {"beams": 4, "maxNewTokens": 256}
        candidate["generationProtocol"] = {"beams": 1, "maxNewTokens": 256}
        result = training.gate(base, candidate)
        self.assertFalse(result["passed"])
        self.assertIn("inference-protocol-mismatch", result["reasons"])


def fingerprints():
    return {"logicalFP32Sha256": "logical-before", "tensorHashes": {"encoder.weight": "tensor-a", "decoder.weight": "tensor-b"},
            "first64ValueSamples": {"encoder.weight": [0.25, -0.5], "decoder.weight": [1.0, 0.0]}}


class WeightEvidenceTests(unittest.TestCase):
    def test_an_aggregate_hash_change_alone_is_not_weight_update_evidence(self):
        before = fingerprints()
        after = copy.deepcopy(before)
        after["logicalFP32Sha256"] = "different-container-or-metadata-hash"
        result = training.compare_fingerprints(before, after)
        self.assertEqual(result["changedTensorCount"], 0)
        self.assertEqual(result["changedTensorNames"], [])
        self.assertEqual(result["sampledChangedScalarCount"], 0)
        self.assertEqual(result["sampledMaxAbsDelta"], 0.0)

    def test_a_changed_tensor_counts_even_when_the_sampled_prefix_is_unchanged(self):
        before = fingerprints()
        after = copy.deepcopy(before)
        after["logicalFP32Sha256"] = "logical-after"
        after["tensorHashes"]["decoder.weight"] = "changed-outside-prefix"
        result = training.compare_fingerprints(before, after)
        self.assertEqual(result["changedTensorCount"], 1)
        self.assertEqual(result["changedTensorNames"], ["decoder.weight"])
        self.assertEqual(result["sampledChangedScalarCount"], 0)

    def test_parameter_structure_changes_are_rejected(self):
        before = fingerprints()
        after = copy.deepcopy(before)
        del after["tensorHashes"]["decoder.weight"]
        with self.assertRaisesRegex(ValueError, "Model parameter structure changed"):
            training.compare_fingerprints(before, after)


class SelectedModelIntegrityTests(unittest.TestCase):
    def setUp(self):
        training.WORK_ROOT.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="unit-training-infer-", dir=training.WORK_ROOT)
        self.directory = Path(self.temporary.name).resolve()
        self.assertTrue(self.directory.is_relative_to(training.WORK_ROOT.resolve()))
        self.assertTrue(self.directory.name.startswith("unit-training-infer-"))
        self.build_fixture()

    def tearDown(self):
        self.assertTrue(self.directory.is_relative_to(training.WORK_ROOT.resolve()))
        self.assertTrue(self.directory.name.startswith("unit-training-infer-"))
        self.temporary.cleanup()

    def build_fixture(self):
        self.run_dir = self.directory / "runs" / "fixture"
        self.selected = self.run_dir / "checkpoints" / "step-000001"
        self.model = self.run_dir / "model"
        self.base = self.directory / "prepared-model"
        tokenizer = {"source_lang": "en", "target_lang": "ko", "unk_token": "<unk>",
                     "eos_token": "</s>", "pad_token": "<pad>", "separate_vocabs": True,
                     "target_vocab_file": "target_vocab.json"}
        files = {"source.spm": b"unit fixture, not a real source tokenizer",
                 "target.spm": b"unit fixture, not a real target tokenizer",
                 "vocab.json": json.dumps({"<unk>": 0, "</s>": 2, "<pad>": 32000, "sample": 3}).encode(),
                 "target_vocab.json": json.dumps({"<unk>": 0, "</s>": 2, "<pad>": 32000, "예시": 3}).encode(),
                 "tokenizer_config.json": json.dumps(tokenizer).encode(),
                 "special_tokens_map.json": b'{}', "config.json": b'{"model_type":"marian"}',
                 "generation_config.json": b'{}', "model.safetensors": b"unit fixture, not model weights"}
        for directory in (self.base, self.selected / "model", self.model):
            directory.mkdir(parents=True, exist_ok=True)
            for name, data in files.items():
                (directory / name).write_bytes(data)
        model_hash = training.sha256(self.model / "model.safetensors")
        training.write_json(self.base / "prepared-manifest.json", {
            "schemaVersion": 1, "preparationVersion": training.TOKENIZER_REPAIR,
            "baseRepository": inference.BASE_ID, "baseRevision": inference.BASE_REVISION,
            "baseWeightSha256": inference.BASE_WEIGHT_HASH, "weightTrainingPerformed": False,
            "separateVocabs": True,
            "files": [{"name": name, "sha256": training.sha256(self.base / name),
                       "size": (self.base / name).stat().st_size} for name in files]})
        training.write_json(self.selected / "checkpoint.json", {"modelSha256": model_hash})
        training.write_json(self.run_dir / "manifest.json", {
            "runId": "fixture", "baseModel": inference.BASE_ID, "baseRevision": inference.BASE_REVISION,
            "basePath": training.relative(self.base),
            "baseFiles": {name: training.sha256(self.base / name) for name in (*files, "prepared-manifest.json")},
            "tokenizerRepair": {"repair": training.TOKENIZER_REPAIR, "weightChange": False},
            "selectedCheckpoint": training.relative(self.selected)})
        training.write_json(self.run_dir / "training-summary.json", {
            "runId": "fixture", "baseWeightFileSha256": inference.BASE_WEIGHT_HASH,
            "trainedWeightFileSha256": model_hash, "weightEvidence": {"changedTensorCount": 1},
            "completedUpdates": 1, "modelPath": training.relative(self.model)})

    def verify_fixture(self):
        # The selected-copy checks run for real. Only SPM parsing is replaced:
        # these deliberately tiny fixture bytes are not actual tokenizer models.
        report = {"repair": training.TOKENIZER_REPAIR, "weightChange": False, "files": {}}
        with patch.object(inference, "WORK_ROOT", self.directory), patch.object(
                inference, "validate_tokenizer_files", return_value=report):
            return inference.verified_model("fixture")

    def test_a_selected_but_unevaluated_model_remains_experimental(self):
        verified = self.verify_fixture()
        self.assertEqual(verified["path"], self.model)
        self.assertEqual(verified["evaluationStatus"], "experimental")
        self.assertFalse(verified["promotionEligible"])
        self.assertFalse(verified["testWasEvaluated"])
        self.assertEqual(verified["weightHash"], training.sha256(self.model / "model.safetensors"))

    def test_changed_final_weights_are_rejected(self):
        (self.model / "model.safetensors").write_bytes(b"tampered final weight fixture")
        with self.assertRaisesRegex(inference.InferenceError, "Selected model weight integrity"):
            self.verify_fixture()

    def test_tokenizer_changes_are_rejected_even_when_copies_agree(self):
        cases = (
            ("source.spm", b"changed both copies of source tokenizer", "both", "unexpected SentencePiece"),
            ("vocab.json", b'{"<unk>":0,"</s>":2,"<pad>":32000,"different":3}', "both", "vocabulary differs"),
            ("target_vocab.json", b'{"<unk>":0,"</s>":2,"<pad>":32000,"wrong":3}', "both", "vocabulary differs"),
            ("tokenizer_config.json", b'{}', "final", "model/tokenizer file integrity"),
            ("target.spm", b"changed pinned base tokenizer", "base", "Pinned base tokenizer integrity"),
        )
        for name, data, scope, error in cases:
            with self.subTest(name=name, scope=scope):
                self.build_fixture()
                targets = [self.model, self.selected / "model"] if scope == "both" else [self.base if scope == "base" else self.model]
                for target in targets:
                    (target / name).write_bytes(data)
                with self.assertRaisesRegex(inference.InferenceError, error):
                    self.verify_fixture()


class FakeSentencePieceProcessor:
    """A deterministic ID table, without loading or training any SPM model."""
    def __init__(self, model_file):
        self.prefix = "source" if Path(model_file).name == "source.spm" else "target"

    def get_piece_size(self):
        return 32000

    def id_to_piece(self, index):
        return ("<unk>", "<s>", "</s>")[index] if index < 3 else f"{self.prefix}_piece_{index}"


class TokenizerMappingTests(unittest.TestCase):
    def setUp(self):
        training.WORK_ROOT.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="unit-training-vocab-", dir=training.WORK_ROOT)
        self.directory = Path(self.temporary.name).resolve()
        self.assertTrue(self.directory.is_relative_to(training.WORK_ROOT.resolve()))
        self.assertTrue(self.directory.name.startswith("unit-training-vocab-"))
        training.write_json(self.directory / "tokenizer_config.json", {
            "source_lang": "en", "target_lang": "ko", "unk_token": "<unk>", "eos_token": "</s>",
            "pad_token": "<pad>", "separate_vocabs": True, "target_vocab_file": "target_vocab.json"})
        self.hashes, self.vocabs = {}, {}
        for spm_name, vocab_name in (("source.spm", "vocab.json"), ("target.spm", "target_vocab.json")):
            path = self.directory / spm_name
            path.write_bytes(("SPM test fixture " + spm_name).encode())
            self.hashes[spm_name] = training.sha256(path)
            processor = FakeSentencePieceProcessor(str(path))
            vocab = {processor.id_to_piece(index): index for index in range(processor.get_piece_size())}
            vocab["<pad>"] = 32000
            self.vocabs[vocab_name] = vocab
            training.write_json(self.directory / vocab_name, vocab)

    def tearDown(self):
        self.assertTrue(self.directory.is_relative_to(training.WORK_ROOT.resolve()))
        self.assertTrue(self.directory.name.startswith("unit-training-vocab-"))
        self.temporary.cleanup()

    def verify_fixture(self):
        fake_module = SimpleNamespace(SentencePieceProcessor=FakeSentencePieceProcessor)
        with patch.dict(sys.modules, {"sentencepiece": fake_module}), patch.dict(training.BASE_SPM_HASHES, self.hashes):
            return training.validate_tokenizer_files(self.directory)

    def test_distinct_source_and_target_id_tables_are_accepted(self):
        report = self.verify_fixture()
        self.assertFalse(report["weightChange"])
        self.assertEqual(set(report["files"]), {"source.spm", "target.spm", "vocab.json", "target_vocab.json"})

    def test_korean_id_table_cannot_be_used_as_the_english_vocabulary(self):
        # Reproduces the upstream defect structurally: both files describe the
        # target SPM even though English input is segmented by the source SPM.
        training.write_json(self.directory / "vocab.json", self.vocabs["target_vocab.json"])
        with self.assertRaisesRegex(ValueError, "Vocabulary IDs differ"):
            self.verify_fixture()

    def test_valid_target_words_with_swapped_ids_are_rejected(self):
        changed = dict(self.vocabs["target_vocab.json"])
        changed["target_piece_8"], changed["target_piece_9"] = changed["target_piece_9"], changed["target_piece_8"]
        training.write_json(self.directory / "target_vocab.json", changed)
        with self.assertRaisesRegex(ValueError, "Vocabulary IDs differ"):
            self.verify_fixture()


class FakeEncodingTokenizer:
    unk_token_id = 0

    def __init__(self, source_ids, target_ids):
        self.source_ids, self.target_ids = source_ids, target_ids

    def __call__(self, text=None, *, text_target=None, **_options):
        return {"input_ids": self.target_ids if text_target is not None else self.source_ids}


class BaselineSanityTests(unittest.TestCase):
    fixture = {"id": "encoding-fixture", "source": "Independent sample sentence.", "target": "별도로 작성한 예시 문장이다."}

    def test_unknown_token_explosion_is_rejected_on_either_side(self):
        for side in ("source", "target"):
            with self.subTest(side=side):
                valid, broken = [8, 9, 10, 2], [0, 0, 10, 2]
                tokenizer = FakeEncodingTokenizer(broken if side == "source" else valid,
                                                 broken if side == "target" else valid)
                with self.assertRaisesRegex(ValueError, f"excessive unknown {side} tokens"):
                    training.encode_rows(tokenizer, [self.fixture], 192)

    def test_a_rare_single_unknown_or_the_five_percent_boundary_is_allowed(self):
        for ids in ([0, 8, 9, 2], [0, 0] + [8] * 38 + [2]):
            with self.subTest(length=len(ids)):
                encoded = training.encode_rows(FakeEncodingTokenizer(ids, [8, 9, 2]), [self.fixture], 192)
                self.assertEqual(encoded[0]["input_ids"], ids)

    def test_empty_missing_or_length_capped_baselines_block_training(self):
        for values in ({"count": 0}, {"count": 40, "emptyOutputs": 1}, {"count": 40, "cappedOutputs": 1}):
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, "Baseline sanity failed"):
                    training.validate_baseline(values)

    def test_complete_uncapped_baseline_passes_the_sanity_check(self):
        self.assertIsNone(training.validate_baseline({"count": 40, "emptyOutputs": 0, "cappedOutputs": 0}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
