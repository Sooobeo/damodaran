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
import tempfile
from types import SimpleNamespace
import unittest


SPEC = importlib.util.spec_from_file_location("finance_training_under_test", Path(__file__).with_name("train.py"))
training = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(training)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
