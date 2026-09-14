"""S4 observation adapter tests; synthetic strings, no generation or ledger."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from input_execution_v1 import relations as r
from test_evaluation import fixture


def rows():
    outputs = fixture()[1]
    for row in outputs:
        row["source"] = "The rate increases by 7%."
        row["translation"] = "비율은 7%로 증가한다." if row["configuration"] == "C0" else "비율은 7% 증가한다."
        row["sourceSha256"] = r.e.sha(row["source"])
        row["translationSha256"] = r.e.sha(row["translation"])
        row["automaticChecks"] = {"numbersPreserved": True, "currencySymbolsPreserved": True}
    return outputs


class RelationsTests(unittest.TestCase):
    def test_checker_is_frozen_and_source_translation_only(self):
        inputs = rows()
        original = deepcopy(inputs)
        with patch.object(r.checker, "inspect_relations", wraps=r.checker.inspect_relations) as call:
            result = r.diagnose_rows(inputs)
        self.assertEqual(call.call_count, 64)
        self.assertTrue(all(len(args.args) == 2 and not args.kwargs for args in call.call_args_list))
        self.assertTrue(all(all(isinstance(value, str) for value in args.args) for args in call.call_args_list))
        self.assertEqual(inputs, original)
        self.assertFalse(result["evaluationAnnotationsUsedByDetector"])

    def test_same_count_numeric_role_warnings_are_separate_from_quality(self):
        result = r.diagnose_rows(rows())
        self.assertEqual(result["outputs"], 64)
        self.assertEqual(result["relationSlots"], 384)
        self.assertEqual(result["configurations"]["C0"]["warningOutputs"], 16)
        self.assertEqual(result["configurations"]["C1"]["warningOutputs"], 0)
        self.assertEqual(result["configurations"]["C0"]["byType"]["start_delta_result"]["supported_conflict"], 16)
        self.assertEqual(result["generationQualityErrors"]["status"], "not_evaluated")
        self.assertIsNone(result["precisionRecall"]["precision"])
        self.assertFalse(result["semanticApproval"])
        self.assertTrue(all(obs["originalAutomaticChecks"]["numbersPreserved"] for obs in result["observations"]))

    def test_identical_text_across_configurations_retains_distinct_observations(self):
        inputs = rows()
        for row in inputs:
            row["translation"] = "비율은 7% 증가한다."
            row["translationSha256"] = r.e.sha(row["translation"])
        first = r.diagnose_rows(inputs)
        second = r.diagnose_rows(inputs)
        self.assertEqual(first, second)
        self.assertEqual(len({row["observationId"] for row in first["observations"]}), 64)
        self.assertTrue(all(all(field in row for field in r.JOIN_FIELDS) for row in first["observations"]))

    def test_unsupported_is_not_success_and_all_denominators_remain(self):
        inputs = rows()
        for row in inputs:
            row.update(source="The committee met.", translation="위원회가 회의를 열었다.")
            row["sourceSha256"] = r.e.sha(row["source"])
            row["translationSha256"] = r.e.sha(row["translation"])
        result = r.diagnose_rows(inputs)
        for group in result["configurations"].values():
            self.assertEqual(group["statusCounts"]["unsupported"], 96)
            self.assertEqual(group["supportedRelationSlots"], 0)
            self.assertEqual(group["warningOutputs"], 0)
        self.assertTrue(result["unsupportedAndUndeterminedAreNotPass"])
        self.assertFalse(result["fullSemanticCoverage"])

    def test_incomplete_duplicate_mixed_failed_or_changed_rows_rejected(self):
        original = rows()
        cases = [original[:-1], original[:-1] + [original[0]]]
        for field, value in (("status", "failed"), ("sourceSha256", "0" * 64),
                             ("translationSha256", "0" * 64), ("runId", "different-run")):
            changed = deepcopy(original); changed[0][field] = value; cases.append(changed)
        for case in cases:
            with self.subTest(case_length=len(case)), self.assertRaises(ValueError):
                r.diagnose_rows(case)

    def test_utf16_non_bmp_evidence_and_damaged_quote_rejected(self):
        text = "😀 비율은 7% 증가한다."
        quote = "7%"
        start = len(text[:text.index(quote)].encode("utf-16-le")) // 2
        r.exact_utf16_span(text, {"start": start, "end": start + 2, "text": quote})
        with self.assertRaises(ValueError):
            r.exact_utf16_span(text, {"start": 1, "end": 2, "text": "😀"})
        with self.assertRaises(ValueError):
            r.exact_utf16_span(text, {"start": start, "end": start + 2, "text": "8%"})

    def test_completed_run_evidence_checked_before_loading_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); run = root / "run"; run.mkdir()
            output = run / "predictions.jsonl"; output.write_text("{}\n", encoding="utf-8")
            (run / "summary.json").write_text(json.dumps({"status": "failed", "completedOutputs": 63}), encoding="utf-8")
            with patch.object(r.e, "load_development") as development:
                with self.assertRaisesRegex(ValueError, "s4_run_not_complete"):
                    r.load_outputs(output, root)
                development.assert_not_called()

    def test_changed_s3_checker_hash_refused(self):
        with patch.object(r, "CHECKER_SHA", "0" * 64), self.assertRaisesRegex(ValueError, "frozen_s3_checker_changed"):
            r.diagnose_rows(rows())


if __name__ == "__main__":
    unittest.main()
