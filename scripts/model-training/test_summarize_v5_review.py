"""Synthetic A/B review fixtures only; real experiments are never accessed."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

import summarize_v5_review as aggregate


class ReviewAggregationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="v5-aggregate-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root / ".training/runs/fixture-aggregate"
        self.prepared = self.run / "assistant-review-v5"
        self.prepared.mkdir(parents=True)
        self.output = self.prepared / "summary.json"
        self.templates = []
        for number, domain in enumerate(("finance", "general")):
            source = "The fictional sum is not -7 dollars." if number == 0 else "Her interest in the fictional book has grown."
            row = {"id": "fixture-" + str(number), "source": source, "sourceSha256": aggregate.text_hash(source),
                   "assistantReference": "가상의 합계는 -7달러가 아니다." if number == 0 else "가상의 책에 대한 그녀의 관심이 커졌다.",
                   "referenceProvenance": "assistant_authored", "referenceReviewStatus": "synthetic_fixture_only",
                   "referenceHumanReviewed": False, "domain": domain, "primaryTermId": "G01" if number == 0 else None,
                   "forbiddenTerms": [] if number == 0 else [{"target": "이자", "reason": "interest refers to curiosity"}],
                   "criticalChecks": [], "protectedSymbols": [], "unitChecks": [], "reviewerType": None,
                   "humanReviewed": False, "note": "Synthetic fixture, not a human gold reference.",
                   "choices": [{"label": label, "translation": "가상 번역 " + str(number) + label,
                                "review": {field: "" if field == "evidence" else None for field in aggregate.JUDGMENT_FIELDS}}
                               for label in "AB"]}
            self.templates.append(row)
        self.key = {"version": aggregate.PREPARED_VERSION,
                    "mappings": [{"id": "fixture-0", "labels": {"A": "baseline", "B": "candidate"}},
                                 {"id": "fixture-1", "labels": {"A": "candidate", "B": "baseline"}}]}
        identity = {"version": aggregate.PREPARED_VERSION, "randomizationSeed": aggregate.RANDOMIZATION_SEED,
                    "inputs": {"runId": "fixture-aggregate", "selectedStep": 2,
                               "files": {".training/never-open/test.jsonl": "a" * 64,
                                         ".training/never-open/model.safetensors": "b" * 64}}}
        self.manifest = {"version": aggregate.PREPARED_VERSION, "status": "complete", "rowCount": 2,
                         "identity": identity, "identitySha256": aggregate.canonical_hash(identity),
                         "humanReviewed": False, "inferencePerformed": False, "glossaryApplied": False,
                         "severityScale": {str(n): "fixture-severity-" + str(n) for n in range(4)}}
        self.jsonl(self.prepared / "anonymous-review.jsonl", self.templates)
        self.write(self.prepared / "review-key.json", self.key)
        self.seal_prepared()
        self.rows = copy.deepcopy(self.templates)
        for row in self.rows:
            row.update({"reviewerType": "assistant", "humanReviewed": False, "reviewerId": "fixture-assistant"})
            for choice in row["choices"]:
                choice["review"] = {"severity": 0, "meaningPreserved": True, "negationAndConditionsPreserved": True,
                                    "contextualWordSensePreserved": True, "omission": False, "unsupportedAddition": False,
                                    "quantityOrFormulaError": False, "fluency": True,
                                    "evidence": "The source proposition is preserved in this synthetic judgment."}
        self.rows[0]["choices"][0]["review"].update({"severity": 2, "meaningPreserved": False,
                                                   "negationAndConditionsPreserved": False, "quantityOrFormulaError": True,
                                                   "evidence": "The source says not -7 dollars; the fictional output reverses that claim."})
        self.rows[1]["choices"][0]["review"].update({"severity": 1, "fluency": False,
                                                   "evidence": "The source's curiosity meaning remains, with awkward wording."})
        self.rows[1]["choices"][1]["review"].update({"severity": 3, "meaningPreserved": False,
                                                   "contextualWordSensePreserved": False, "omission": True, "unsupportedAddition": True,
                                                   "evidence": "The source refers to curiosity; the fictional result invents a financing sense."})
        self.paths = [self.prepared / "review-part-a.jsonl", self.prepared / "review-part-b.jsonl"]
        self.bind_and_write_reviews()

    def write(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")

    def jsonl(self, path, rows):
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), "utf-8")

    def seal_prepared(self):
        self.manifest["files"] = {name: aggregate.digest((self.prepared / name).read_bytes())
                                  for name in ("anonymous-review.jsonl", "review-key.json")}
        self.write(self.prepared / "manifest.json", self.manifest)

    def bind_and_write_reviews(self):
        for row in self.rows:
            row["inputSha256"] = aggregate.digest((self.prepared / "anonymous-review.jsonl").read_bytes())
            row["reviewManifestSha256"] = aggregate.digest((self.prepared / "manifest.json").read_bytes())
        for path, row in zip(self.paths, self.rows):
            self.jsonl(path, [row])

    def summarize(self, files=None, output=None):
        return aggregate.summarize("fixture-aggregate", self.paths if files is None else files, output, self.root)

    def assert_rejected(self, pattern=None, files=None, output=None):
        context = self.assertRaisesRegex(ValueError, pattern) if pattern else self.assertRaises(ValueError)
        with context:
            self.summarize(files, output)
        self.assertFalse(self.output.exists())

    def test_aggregates_each_dimension_by_model_and_domain_without_new_gate(self):
        path = self.summarize()
        summary = json.loads(path.read_text("utf-8"))
        self.assertEqual(summary["rowCount"], 2)
        self.assertEqual(summary["judgedChoices"], 4)
        self.assertEqual(summary["unresolvedJudgments"], 0)
        self.assertEqual(summary["models"]["baseline"]["severity"], {"0": 0, "1": 0, "2": 1, "3": 1})
        self.assertEqual(summary["models"]["candidate"]["severity"], {"0": 1, "1": 1, "2": 0, "3": 0})
        baseline = summary["models"]["baseline"]
        self.assertEqual(baseline["meaningPreserved"], {"true": 0, "false": 2})
        for field in ("negationAndConditionsPreserved", "contextualWordSensePreserved", "omission", "unsupportedAddition", "quantityOrFormulaError"):
            self.assertEqual(baseline[field], {"true": 1, "false": 1})
        self.assertEqual(summary["models"]["candidate"]["fluency"], {"true": 1, "false": 1})
        self.assertEqual(baseline["byDomain"]["finance"]["quantityOrFormulaError"]["true"], 1)
        self.assertEqual(baseline["byDomain"]["general"]["contextualWordSensePreserved"]["false"], 1)
        self.assertEqual(len(summary["findings"]), 3)
        self.assertFalse(summary["humanReviewed"])
        self.assertFalse(summary["automaticGateCreated"])
        self.assertIsNone(summary["promotionDecision"])
        self.assertFalse(summary["originalModelOrTestFilesRead"])
        self.assertFalse((self.root / ".training/never-open").exists())
        self.assertEqual(summary["preparedManifestSha256"], self.rows[0]["reviewManifestSha256"])

    def test_identical_input_reuses_immutable_summary_even_when_file_arguments_reverse(self):
        path = self.summarize()
        before, mtime = path.read_bytes(), path.stat().st_mtime_ns
        self.assertEqual(path, self.summarize(self.paths[::-1]))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_partial_and_unknown_ids_never_create_summary(self):
        self.assert_rejected("coverage is incomplete", files=self.paths[:1])
        self.rows[1]["id"] = "unknown"
        self.bind_and_write_reviews()
        self.assert_rejected("unknown ID")

    def test_duplicate_ids_within_and_across_files_and_duplicate_paths_are_rejected(self):
        self.jsonl(self.paths[0], [self.rows[0], self.rows[0]])
        self.assert_rejected("Duplicate reviewed ID")
        self.jsonl(self.paths[0], [self.rows[0]])
        self.jsonl(self.paths[1], [self.rows[0], self.rows[1]])
        self.assert_rejected("Duplicate reviewed ID")
        self.assert_rejected("Duplicate review file path", files=[self.paths[0], self.paths[0].parent / "." / self.paths[0].name])

    def test_review_source_reference_context_and_raw_translation_cannot_change(self):
        original = copy.deepcopy(self.rows)
        for field, value in (("source", "Mutated source"), ("sourceSha256", "f" * 64), ("assistantReference", "바뀐 참조"),
                             ("domain", "general"), ("primaryTermId", "other"), ("forbiddenTerms", [{"target": "changed"}])):
            with self.subTest(field=field):
                self.rows = copy.deepcopy(original)
                self.rows[0][field] = value
                self.bind_and_write_reviews()
                self.assert_rejected("source/reference/context changed")
        self.rows = copy.deepcopy(original)
        self.rows[0]["choices"][0]["translation"] += " 추가"
        self.bind_and_write_reviews()
        self.assert_rejected("raw translation changed")

    def test_missing_extra_duplicate_and_reordered_choice_labels_are_rejected(self):
        original = copy.deepcopy(self.rows)
        variants = [original[0]["choices"][:1], original[0]["choices"] * 2,
                    [original[0]["choices"][0]] * 2, original[0]["choices"][::-1]]
        for choices in variants:
            with self.subTest(choices=choices):
                self.rows = copy.deepcopy(original)
                self.rows[0]["choices"] = choices
                self.bind_and_write_reviews()
                self.assert_rejected("review labels")

    def test_null_judgments_are_unresolved_not_completed(self):
        original = copy.deepcopy(self.rows)
        for field in aggregate.JUDGMENT_FIELDS:
            with self.subTest(field=field):
                self.rows = copy.deepcopy(original)
                self.rows[0]["choices"][0]["review"][field] = None
                self.bind_and_write_reviews()
                self.assert_rejected("Unresolved judgment")

    def test_wrong_judgment_types_and_missing_evidence_are_rejected(self):
        original = copy.deepcopy(self.rows)
        cases = [("severity", True), ("severity", -1), ("severity", 4), ("severity", 1.0),
                 ("meaningPreserved", 1), ("omission", "false"), ("fluency", "good"), ("evidence", "  ")]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.rows = copy.deepcopy(original)
                self.rows[0]["choices"][0]["review"][field] = value
                self.bind_and_write_reviews()
                self.assert_rejected()

    def test_duplicate_json_keys_nonfinite_values_and_malformed_choices_are_rejected(self):
        original = self.paths[0].read_text("utf-8")
        self.paths[0].write_text(original.replace('"severity": 2', '"severity": 0, "severity": 2'), "utf-8")
        self.assert_rejected("Duplicate JSON")
        self.paths[0].write_text(original.replace('"severity": 2', '"severity": NaN'), "utf-8")
        self.assert_rejected("Nonfinite JSON")
        self.rows[0]["choices"] = [None, self.rows[0]["choices"][1]]
        self.bind_and_write_reviews()
        self.assert_rejected("review labels")

    def test_prejudged_template_cannot_be_used_even_if_hashes_are_consistently_rebound(self):
        self.templates[0]["choices"][0]["review"]["severity"] = 0
        self.jsonl(self.prepared / "anonymous-review.jsonl", self.templates)
        self.seal_prepared()
        self.bind_and_write_reviews()
        self.assert_rejected("blank judgments")

    def test_review_hashes_and_reviewer_provenance_are_required(self):
        original = copy.deepcopy(self.rows)
        for field, value in (("inputSha256", "c" * 64), ("reviewManifestSha256", "d" * 64),
                             ("humanReviewed", True), ("reviewerType", "human")):
            with self.subTest(field=field):
                self.rows = copy.deepcopy(original)
                self.rows[0][field] = value
                for path, row in zip(self.paths, self.rows):
                    self.jsonl(path, [row])
                self.assert_rejected()

    def test_mutated_prepared_template_key_or_manifest_is_rejected(self):
        for name in ("anonymous-review.jsonl", "review-key.json"):
            with self.subTest(name=name):
                path = self.prepared / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                self.assert_rejected("hash mismatch")
                path.write_bytes(original)
        self.manifest["identity"]["inputs"]["runId"] = "other"
        self.write(self.prepared / "manifest.json", self.manifest)
        self.assert_rejected("identity/run mismatch")

    def test_consistently_rehashed_but_invalid_key_mapping_is_rejected(self):
        original = copy.deepcopy(self.key)
        for change in ("duplicate-id", "same-model", "missing-label"):
            with self.subTest(change=change):
                self.key = copy.deepcopy(original)
                if change == "duplicate-id":
                    self.key["mappings"][1]["id"] = "fixture-0"
                elif change == "same-model":
                    self.key["mappings"][0]["labels"]["B"] = "baseline"
                else:
                    del self.key["mappings"][0]["labels"]["B"]
                self.write(self.prepared / "review-key.json", self.key)
                self.seal_prepared()
                self.bind_and_write_reviews()
                self.assert_rejected("Review key")

    def test_input_output_boundaries_and_template_aliases_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "run ID"):
            aggregate.summarize("../other", self.paths, app_root=self.root)
        outside = self.root / "outside.jsonl"
        self.jsonl(outside, [self.rows[0]])
        self.assert_rejected("escaped", files=[outside, self.paths[1]])
        self.assert_rejected("escaped", output=self.run / "training-summary-new.json")
        self.assert_rejected("replace", output=self.prepared / "review-key.json")
        self.assert_rejected("separate review", files=[self.prepared / "anonymous-review.jsonl"])
        self.assert_rejected("Output must", output=self.prepared / "summary.txt")

    def test_changed_judgments_and_partial_existing_output_are_never_overwritten(self):
        path = self.summarize()
        original = path.read_bytes()
        self.rows[0]["choices"][0]["review"]["evidence"] += " Additional source explanation."
        self.bind_and_write_reviews()
        with self.assertRaisesRegex(ValueError, "immutable summary differs"):
            self.summarize()
        self.assertEqual(path.read_bytes(), original)
        path.write_bytes(b'{"partial":')
        with self.assertRaisesRegex(ValueError, "immutable summary differs"):
            self.summarize()
        self.assertEqual(path.read_bytes(), b'{"partial":')


if __name__ == "__main__":
    unittest.main()
