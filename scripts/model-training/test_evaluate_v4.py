"""Offline fixtures only: no real corpus, translation model, GPU or test consumed."""
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
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("v4_training_under_test", Path(__file__).with_name("train.py"))
training = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(training)
SPEC = importlib.util.spec_from_file_location("v4_comparison_under_test", Path(__file__).with_name("evaluate_v4.py"))
comparison = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"train": training}):
    SPEC.loader.exec_module(comparison)


def row():
    return {"id": "unit-fixture", "source": "The cost is $12 million; V=CF/(r-g).",
            "target": "비용은 12백만 달러이며 V=CF/(r-g).", "split": "dev", "domain": "finance", "terms": [],
            "provenance": "unit fixture, not a real translation", "reviewStatus": "assistant-only",
            "protectedSymbols": ["V=CF/(r-g)"],
            "unitChecks": [{"source": "$12 million", "targetAny": ["12백만 달러", "12 million dollars"], "forbidden": ["12억원"]}],
            "criticalChecks": ["Preserve formula and dollar scale."]}


def prediction(source_row, text=None):
    return {"index": 0, "id": source_row["id"], "sourceHash": hashlib.sha256(source_row["source"].encode("utf-8")).hexdigest(),
            "modelIdentity": "fixture-hash", "generationProtocol": {"device": "cpu", "maxNewTokens": 32},
            "prediction": source_row["target"] if text is None else text, "generatedTokens": 20, "atLengthLimit": False}


class UnitAndSymbolTests(unittest.TestCase):
    def test_added_won_scale_is_detected_when_digits_match(self):
        sample = {"id": "united", "source": "The value is 12.", "target": "가치는 12이다."}
        result = comparison.diagnostic_row(sample, "가치는 12억원이다.")
        self.assertTrue(result["numericTokensPreserved"])
        self.assertEqual(result["addedUnitMarkers"], {"currency:won": 1, "scale:1e8": 1})
        self.assertIsNone(result["semanticAccuracy"])

    def test_missing_dollar_and_million_are_separate_from_digits(self):
        result = comparison.diagnostic_row(row(), "비용은 12이며 V=CF/(r-g).")
        self.assertTrue(result["numericTokensPreserved"])
        self.assertEqual(result["missingUnitMarkers"], {"currency:dollar": 1, "scale:1e6": 1})
        self.assertEqual(result["annotatedUnitWarnings"], 1)

    def test_equivalent_explicit_unit_markers_do_not_warn(self):
        result = comparison.diagnostic_row(row(), row()["target"])
        self.assertEqual(result["addedUnitMarkers"], {})
        self.assertEqual(result["missingUnitMarkers"], {})
        self.assertEqual(result["annotatedUnitWarnings"], 0)

    def test_dollar_alias_is_not_double_counted(self):
        self.assertEqual(comparison.explicit_units("US$12"), {"currency:dollar": 1})
        self.assertEqual(comparison.explicit_units("USD 12"), comparison.explicit_units("12 달러"))

    def test_negative_numbers_and_percent_remain_lexical(self):
        sample = {"id": "negative", "source": "Returns are -5 percent.", "target": "수익률은 -5%다."}
        self.assertTrue(comparison.diagnostic_row(sample, sample["target"])["numericTokensPreserved"])
        self.assertFalse(comparison.diagnostic_row(sample, "수익률은 5%다.")["numericTokensPreserved"])

    def test_common_korean_words_do_not_create_currency(self):
        self.assertEqual(comparison.explicit_units("직원은 원가와 조정값을 지원한다."), {})

    def test_formula_change_fails_even_when_numbers_do_not_change(self):
        result = comparison.diagnostic_row(row(), "비용은 12백만 달러이며 V=CF/(r+g).")
        self.assertTrue(result["numericTokensPreserved"])
        self.assertFalse(result["protectedSymbolsPreserved"])

    def test_formula_repetition_is_flagged(self):
        self.assertFalse(comparison.diagnostic_row(row(), row()["target"] + " V=CF/(r-g)")["protectedSymbolsPreserved"])

    def test_identifier_boundary_avoids_counting_letters_inside_words(self):
        self.assertEqual(comparison.literal_count("growth g good g2", "g"), 1)
        self.assertEqual(comparison.literal_count("FCFF FCFF_extra AFCFF", "FCFF"), 1)

    def test_exact_formula_does_not_normalize_whitespace(self):
        self.assertEqual(comparison.literal_count("V = CF/(r-g)", "V=CF/(r-g)"), 0)

    def test_forbidden_unit_is_flagged_even_if_target_also_present(self):
        result = comparison.diagnostic_row(row(), row()["target"] + " 12억원")
        self.assertEqual(result["annotatedUnitWarnings"], 1)

    def test_reference_annotations_are_source_anchored(self):
        comparison.validate_annotations(row())
        bad = row()
        bad["unitChecks"][0]["source"] = "missing source"
        with self.assertRaisesRegex(ValueError, "anchor"):
            comparison.validate_annotations(bad)

    def test_invalid_reference_formula_is_rejected(self):
        bad = row()
        bad["target"] = bad["target"].replace("r-g", "r+g")
        with self.assertRaisesRegex(ValueError, "protected reference"):
            comparison.validate_annotations(bad)

    def test_duplicate_symbols_are_rejected(self):
        bad = row()
        bad["protectedSymbols"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate"):
            comparison.validate_annotations(bad)

    def test_empty_annotations_do_not_report_measured_formula_accuracy(self):
        source_row = {"id": "plain", "source": "A note.", "target": "메모."}
        result = comparison.diagnostics([source_row], [{"prediction": "메모."}])
        self.assertEqual(result["rowsWithProtectedSymbols"], 0)
        self.assertIsNone(result["semanticAccuracy"])


class IdentityAndReviewTests(unittest.TestCase):
    def test_prediction_identity_and_order_are_strict(self):
        source_row, item = row(), prediction(row())
        comparison.validate_predictions([source_row], [item], "fixture-hash", item["generationProtocol"])
        for key, value in (("id", "other-id"), ("sourceHash", "other-source"), ("modelIdentity", "other-model"),
                           ("index", 1), ("generationProtocol", {"device": "xpu"})):
            wrong = {**item, key: value}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "identity/order/protocol"):
                comparison.validate_predictions([source_row], [wrong], "fixture-hash", item["generationProtocol"])

    def test_partial_cache_is_only_accepted_for_resume(self):
        item = prediction(row())
        comparison.validate_predictions([row()], [], "fixture-hash", item["generationProtocol"], complete=False)
        with self.assertRaisesRegex(ValueError, "count"):
            comparison.validate_predictions([row()], [], "fixture-hash", item["generationProtocol"])
        with self.assertRaisesRegex(ValueError, "count"):
            comparison.validate_predictions([row()], [item, item], "fixture-hash", item["generationProtocol"], complete=False)

    def test_invalid_generation_metadata_is_rejected(self):
        item = prediction(row())
        for key, value in (("generatedTokens", True), ("generatedTokens", -1), ("atLengthLimit", "false"), ("prediction", None)):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, "metadata"):
                comparison.validate_predictions([row()], [{**item, key: value}], "fixture-hash", item["generationProtocol"])

    def test_anonymous_outputs_have_separate_mapping_and_no_model_identity(self):
        source_row = row()
        outputs = {name: [prediction(source_row, f"번역 {index}")] for index, name in enumerate(comparison.MODEL_NAMES)}
        reviews, keys = comparison.anonymous_review([source_row], outputs, "fixed-fixture-salt")
        self.assertEqual((reviews, keys), comparison.anonymous_review([source_row], outputs, "fixed-fixture-salt"))
        self.assertEqual(set(keys[0]["labels"].values()), set(comparison.MODEL_NAMES))
        self.assertNotIn("modelIdentity", json.dumps(reviews))
        self.assertNotIn("baseline", json.dumps(reviews))
        self.assertFalse(reviews[0]["humanReviewed"])
        self.assertIsNone(reviews[0]["choices"][0]["review"]["meaningPreserved"])
        self.assertIsNone(reviews[0]["choices"][0]["review"]["contextualWordSensePreserved"])
        for choice in reviews[0]["choices"]:
            name = keys[0]["labels"][choice["label"]]
            self.assertEqual(choice["translation"], outputs[name][0]["prediction"])

    def test_contextual_word_sense_review_preserves_unmodified_model_outputs(self):
        source_row = {**row(), "source": "Her interest in art did not change the bond yield.",
                      "target": "그녀의 미술에 대한 관심은 채권 수익률을 바꾸지 않았다.",
                      "protectedSymbols": [], "unitChecks": []}
        outputs = {name: [prediction(source_row, "미술에 대한 이자는 채권 수익률을 바꾸지 않았다.")]
                   for name in comparison.MODEL_NAMES}
        original = copy.deepcopy(outputs)
        reviews, _keys = comparison.anonymous_review([source_row], outputs, "word-sense-fixture")
        self.assertEqual(outputs, original)
        for choice in reviews[0]["choices"]:
            self.assertEqual(choice["translation"], original["baseline"][0]["prediction"])
            self.assertIsNone(choice["review"]["contextualWordSensePreserved"])
        self.assertEqual(reviews[0]["source"], source_row["source"])
        for word in ("interest", "return", "capital", "bond", "equity", "period"):
            self.assertIn(word, comparison.POLICY["contextualWordSense"])
        self.assertIn("not exhaustive or replacement rules", comparison.POLICY["contextualWordSense"])

    def test_sealed_test_requires_completed_matching_run_model_and_data(self):
        manifest = {"runId": "new", "datasets": {"test": {"sha256": "dataset"}}}
        summary = {"trainedWeightFileSha256": "model"}
        frozen = {"frozenAt": "2026-09-09T00:00:00+00:00"}
        ledger = {"runId": "new", "testDataSha256": "dataset", "modelSha256": "model", "status": "complete", "completedAt": "2026-09-09T01:00:00+00:00"}
        comparison.check_test_ledger(manifest, summary, frozen, ledger)
        for key, value in (("runId", "another"), ("testDataSha256", "different"), ("modelSha256", "changed"), ("status", "evaluating")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                comparison.check_test_ledger(manifest, summary, frozen, {**ledger, key: value})
        with self.assertRaisesRegex(ValueError, "frozen"):
            comparison.check_test_ledger(manifest, summary, frozen, {**ledger, "completedAt": "2026-09-08T00:00:00+00:00"})

    def test_run_id_cannot_escape_training_runs(self):
        for value in ("../outside", "a/b", "..", "C:\\outside"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "run ID"):
                comparison.run_directory(value)

    def test_hash_covers_runtime_and_model_changes(self):
        original = {"models": {"baseline": "hash1"}, "runtime": {"threads": 4}}
        changed = copy.deepcopy(original)
        changed["runtime"]["threads"] = 8
        self.assertNotEqual(comparison.canonical_hash(original), comparison.canonical_hash(changed))


class SourceGroupMetricsTests(unittest.TestCase):
    def fixture(self):
        stored = "stored_source_assistant_translation"
        authored = "assistant_authored"
        rows = [
            {**row(), "id": "document-first", "provenance": stored},
            {"id": "authored-general", "source": "The box contains 4 books.", "target": "상자에는 책이 4권 있다.",
             "domain": "general", "provenance": authored, "terms": []},
            {**row(), "id": "document-last", "provenance": stored},
            {"id": "authored-finance", "source": "Revenue is 9 dollars.", "target": "매출은 9달러다.",
             "domain": "finance", "provenance": authored, "terms": [{"source": "Revenue", "target": "매출"}]},
        ]
        predictions = {name: [{**prediction(source), "index": index, "modelIdentity": name}
                              for index, source in enumerate(rows)] for name in comparison.MODEL_NAMES}
        for name in comparison.MODEL_NAMES:
            comparison.validate_predictions(rows, predictions[name], name, predictions[name][0]["generationProtocol"])
        return rows, predictions

    def test_interleaved_groups_reuse_each_models_original_indexes(self):
        rows, predictions = self.fixture()
        def metric_spy(subset, outputs):
            return {"rowIds": [row["id"] for row in subset], "predictionIndexes": [item["index"] for item in outputs],
                    "predictionIds": [item["id"] for item in outputs], "models": [item["modelIdentity"] for item in outputs]}
        with patch.object(training, "metrics", side_effect=metric_spy), \
                patch.object(training, "predict", side_effect=AssertionError("grouping must not generate")):
            result = comparison.source_group_metrics(rows, predictions)
        for group, indexes in (("stored_source_assistant_translation", [0, 2]), ("assistant_authored", [1, 3])):
            self.assertEqual(result[group]["count"], 2)
            self.assertIsNone(result[group]["semanticAccuracy"])
            for name in comparison.MODEL_NAMES:
                metrics = result[group]["lexicalMetrics"][name]
                self.assertEqual(metrics["predictionIndexes"], indexes)
                self.assertEqual(metrics["rowIds"], metrics["predictionIds"])
                self.assertEqual(metrics["models"], [name, name])

    def test_actual_lexical_metrics_and_unit_diagnostics_stay_with_their_group(self):
        rows, predictions = self.fixture()
        predictions["candidate"][3]["prediction"] = "매출은 9억원이다."
        result = comparison.source_group_metrics(rows, predictions)
        self.assertEqual(sum(group["count"] for group in result.values()), len(rows))
        stored, authored = result["stored_source_assistant_translation"], result["assistant_authored"]
        self.assertEqual(stored["lexicalMetrics"]["candidate"]["chrF"], 100.0)
        self.assertEqual(set(stored["lexicalMetrics"]["candidate"]["byDomain"]), {"finance"})
        self.assertEqual(set(authored["lexicalMetrics"]["candidate"]["byDomain"]), {"finance", "general"})
        self.assertEqual(stored["diagnostics"]["candidate"]["rowsWithAddedUnitMarkers"], 0)
        self.assertEqual(authored["diagnostics"]["candidate"]["rowsWithAddedUnitMarkers"], 1)
        self.assertEqual(authored["diagnostics"]["candidate"]["rowsWithMissingUnitMarkers"], 1)
        self.assertEqual(authored["diagnostics"]["baseline"]["rowsWithAddedUnitMarkers"], 0)
        self.assertNotIn("gate", authored)

    def test_missing_provenance_is_explicit_and_does_not_claim_a_source_type(self):
        rows, predictions = self.fixture()
        rows[0].pop("provenance")
        result = comparison.source_group_metrics(rows, predictions)
        self.assertEqual(result["unspecified"]["count"], 1)
        self.assertEqual(result["stored_source_assistant_translation"]["count"], 1)
        self.assertIsNone(result["unspecified"]["semanticAccuracy"])

    def test_incomplete_or_reordered_predictions_are_rejected_before_grouping(self):
        rows, predictions = self.fixture()
        incomplete = copy.deepcopy(predictions)
        incomplete["previous"].pop()
        with patch.object(training, "metrics", side_effect=AssertionError("must validate before metrics")):
            with self.assertRaisesRegex(ValueError, "complete"):
                comparison.source_group_metrics(rows, incomplete)
            reordered = copy.deepcopy(predictions)
            reordered["candidate"][0], reordered["candidate"][2] = reordered["candidate"][2], reordered["candidate"][0]
            with self.assertRaisesRegex(ValueError, "original row indexes"):
                comparison.source_group_metrics(rows, reordered)

    def test_nonstring_provenance_is_not_silently_relabeled(self):
        rows, predictions = self.fixture()
        rows[0]["provenance"] = {"kind": "unrecognized"}
        with self.assertRaisesRegex(ValueError, "string provenance"):
            comparison.source_group_metrics(rows, predictions)


class ImmutableFileTests(unittest.TestCase):
    def setUp(self):
        training.WORK_ROOT.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="unit-v4-", dir=training.WORK_ROOT)
        self.directory = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.assertTrue(self.directory.is_relative_to(training.WORK_ROOT.resolve()))
        self.assertTrue(self.directory.name.startswith("unit-v4-"))
        self.temporary.cleanup()

    def test_immutable_outputs_can_resume_but_cannot_be_rewritten(self):
        path = self.directory / "frozen.json"
        comparison.immutable_json(path, {"model": "one"})
        comparison.immutable_json(path, {"model": "one"})
        with self.assertRaisesRegex(ValueError, "immutable"):
            comparison.immutable_json(path, {"model": "two"})
        self.assertEqual(comparison.read_json(path), {"model": "one"})

    def test_review_jsonl_resumes_exactly_and_rejects_changed_rows(self):
        path = self.directory / "review.jsonl"
        comparison.immutable_jsonl(path, [{"id": "fixture", "text": "검사"}])
        comparison.immutable_jsonl(path, [{"id": "fixture", "text": "검사"}])
        with self.assertRaisesRegex(ValueError, "immutable"):
            comparison.immutable_jsonl(path, [{"id": "fixture", "text": "변경"}])

    def test_torn_prediction_cache_is_rejected(self):
        path = self.directory / "cache.jsonl"
        path.write_text('{"id":"first"}\n{"id":', "utf-8")
        with self.assertRaises(json.JSONDecodeError):
            comparison.read_predictions(path)

    def test_freeze_must_precede_test_consumption(self):
        ledger = self.directory / "already-consumed.json"
        ledger.write_text("{}", "utf-8")
        manifest = {"datasets": {"test": {"sha256": "fixture"}}}
        with patch.object(comparison, "prepare", return_value=((self.directory, manifest), None, {"runId": "fixture"})), \
                patch.object(training, "test_ledger", return_value=ledger):
            with self.assertRaisesRegex(ValueError, "before final test"):
                comparison.freeze("fixture", "previous")
        self.assertFalse((self.directory / "comparison-v4" / "manifest.json").exists())

    def test_frozen_identity_resumes_but_rejects_a_different_model(self):
        ledger = self.directory / "ledger.json"
        manifest = {"datasets": {"test": {"sha256": "fixture"}}}
        identity = {"runId": "fixture", "models": {"previous": "original"}}
        with patch.object(comparison, "prepare", return_value=((self.directory, manifest), None, identity)), \
                patch.object(training, "test_ledger", return_value=ledger), patch.object(training, "emit"):
            comparison.freeze("fixture", "previous")
            ledger.write_text("{}", "utf-8")
            comparison.freeze("fixture", "previous")
        different = {"runId": "fixture", "models": {"previous": "changed"}}
        with patch.object(comparison, "prepare", return_value=((self.directory, manifest), None, different)):
            with self.assertRaisesRegex(ValueError, "identity changed"):
                comparison.freeze("fixture", "previous")

    def completed_dev_fixture(self):
        source_row = row()
        dataset = self.directory / "dev.jsonl"
        comparison.immutable_jsonl(dataset, [source_row])
        checkpoint = self.directory / "checkpoint"
        checkpoint.mkdir()
        cache_paths = {"baseline": self.directory / "baseline-dev.jsonl", "candidate": checkpoint / "dev-predictions.jsonl"}
        for path in cache_paths.values():
            comparison.immutable_jsonl(path, [prediction(source_row)])
        manifest = {"datasets": {"dev": {"path": training.relative(dataset), "sha256": training.sha256(dataset)}},
                    "selectedCheckpoint": training.relative(checkpoint)}
        identity = {"previousRunId": "previous-fixture"}
        output = self.directory / "comparison-v4"
        frozen_path = output / "manifest.json"
        comparison.immutable_json(frozen_path, {"identity": identity, "identitySha256": comparison.canonical_hash(identity)})
        final_output = output / "dev-summary.json"
        comparison.immutable_json(final_output, {"fixture": True})
        comparison.immutable_json(output / "dev-consumption.json", {
            "split": "dev", "identitySha256": comparison.canonical_hash(identity), "dataSha256": training.sha256(dataset),
            "reusedPredictionFiles": {name: {"path": training.relative(path), "sha256": training.sha256(path)} for name, path in cache_paths.items()},
            "status": "complete", "outputs": {"summary": {"path": training.relative(final_output), "sha256": training.sha256(final_output)}}})
        models = {name: {"weightSha256": "fixture-hash"} for name in comparison.MODEL_NAMES}
        values = (self.directory, manifest, {}, SimpleNamespace(), {}, models, prediction(source_row)["generationProtocol"], {})
        return values, identity, final_output

    def test_completed_comparison_verifies_files_without_loading_or_generating(self):
        values, identity, _path = self.completed_dev_fixture()
        with patch.object(comparison, "run_directory", return_value=self.directory), \
                patch.object(comparison, "prepare", return_value=(values, None, identity)), \
                patch.object(training, "load_model", side_effect=AssertionError("must not load a model")), \
                patch.object(training, "predict", side_effect=AssertionError("must not regenerate")), patch.object(training, "emit"):
            comparison.compare("fixture", "dev")

    def test_completed_comparison_rejects_modified_output(self):
        values, identity, path = self.completed_dev_fixture()
        path.write_text('{"fixture": "changed"}', "utf-8")
        with patch.object(comparison, "run_directory", return_value=self.directory), \
                patch.object(comparison, "prepare", return_value=(values, None, identity)):
            with self.assertRaisesRegex(ValueError, "output changed"):
                comparison.compare("fixture", "dev")


if __name__ == "__main__":
    unittest.main()
