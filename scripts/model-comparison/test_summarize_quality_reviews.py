"""Synthetic assistant-review aggregation tests; no real probe or inference."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import summarize_quality as prepared
import summarize_quality_reviews as aggregate
import test_summarize_quality as fixture_module

dump, dump_rows, read = fixture_module.dump, fixture_module.dump_rows, fixture_module.read


class QualityReviewAggregateTests(unittest.TestCase):
    def setUp(self):
        fixture = fixture_module.QualitySummaryTests("test_complete_publication_preserves_raw_outputs_and_reuses_same_immutable_artifacts")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root, self.base, self.directory = fixture.root, fixture.base, fixture.output
        fixture.run_summary()
        self.manifest_sha = prepared.digest(self.directory / "manifest.json")
        self.anonymous_sha = prepared.digest(self.directory / "anonymous-review.jsonl")
        self.rows = [json.loads(line) for line in (self.directory / "anonymous-review.jsonl").read_text("utf-8").splitlines()]
        for row in self.rows:
            row.update(reviewerType="assistant", reviewerId="synthetic-reviewer", anonymousInputSha256=self.anonymous_sha,
                       reviewManifestSha256=self.manifest_sha)
            for choice in row["choices"]:
                choice["review"] = {"severity": 0, "meaningPreserved": True, "negationAndConditionsPreserved": True,
                                    "contextualWordSensePreserved": True, "omission": False, "unsupportedAddition": False,
                                    "quantityOrFormulaError": False, "fluency": True,
                                    "evidence": "The synthetic source quantity and condition are preserved."}
        self.first = self.base / "completed-reviews/part-a.jsonl"
        self.second = self.base / "completed-reviews/part-b.jsonl"
        self.output = self.base / "assistant-review-summary.json"
        self.write_reviews()

    def write_reviews(self):
        dump_rows(self.first, self.rows[:10])
        dump_rows(self.second, self.rows[10:])

    def run_aggregate(self, paths=None, output=None):
        return aggregate.summarize(self.directory, paths or [self.first, self.second], output or self.output, self.root)

    def test_complete_partitioned_review_counts_domains_and_preserves_all_evidence(self):
        self.rows[0]["choices"][0]["review"].update(severity=2, meaningPreserved=False, quantityOrFormulaError=True,
                                                  evidence="The fixture flags a changed quantity.")
        self.write_reviews()
        key = read(self.directory / "review-key.json")
        selected_system = key["mappings"][0]["labels"]["A"]
        self.run_aggregate()
        result = read(self.output)
        self.assertEqual(result["judgedChoices"], 96)
        self.assertEqual(result["domains"], {"finance": 16, "general": 8})
        self.assertEqual(len(result["evidence"]), 96)
        self.assertFalse(result["humanReviewed"])
        self.assertFalse(result["automaticGateCreated"])
        self.assertIsNone(result["promotionDecision"])
        self.assertEqual(result["unresolvedJudgments"], 0)
        for name in prepared.SYSTEMS:
            self.assertEqual(result["systems"][name]["judgedChoices"], 24)
            self.assertEqual(result["systems"][name]["byDomain"]["finance"]["judgedChoices"], 16)
            self.assertEqual(result["systems"][name]["byDomain"]["general"]["judgedChoices"], 8)
        self.assertEqual(result["systems"][selected_system]["severity"]["2"], 1)
        self.assertEqual(result["systems"][selected_system]["quantityOrFormulaError"]["true"], 1)
        before = self.output.read_bytes(), self.output.stat().st_mtime_ns
        self.assertEqual(self.run_aggregate(paths=[self.second, self.first]), self.output)
        self.assertEqual(before, (self.output.read_bytes(), self.output.stat().st_mtime_ns))

    def test_partial_or_duplicate_rows_never_create_a_summary(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.run_aggregate(paths=[self.first])
        dump_rows(self.second, self.rows[9:])
        with self.assertRaisesRegex(ValueError, "Duplicate reviewed ID"):
            self.run_aggregate()
        with self.assertRaisesRegex(ValueError, "Duplicate review file"):
            self.run_aggregate(paths=[self.first, self.first])
        self.assertFalse(self.output.exists())

    def test_source_context_reference_and_raw_choice_copies_cannot_change(self):
        original = copy.deepcopy(self.rows[0])
        for field in ("source", "context", "assistantReference", "criticalChecks", "inputSha256"):
            self.rows[0] = copy.deepcopy(original)
            self.rows[0][field] = ["changed"] if isinstance(original[field], list) else "changed"
            self.write_reviews()
            with self.assertRaisesRegex(ValueError, "changed"):
                self.run_aggregate()
        self.rows[0] = copy.deepcopy(original)
        self.rows[0]["choices"][0]["translation"] = "원문과 다른 새 번역"
        self.rows[0]["choices"][0]["translationSha256"] = prepared.sha_text("원문과 다른 새 번역")
        self.write_reviews()
        with self.assertRaisesRegex(ValueError, "Raw choice"):
            self.run_aggregate()
        self.assertFalse(self.output.exists())

    def test_null_invalid_severity_nonboolean_and_missing_evidence_fail_closed(self):
        original = copy.deepcopy(self.rows[0]["choices"][0]["review"])
        for field, value in (("severity", None), ("severity", True), ("severity", 4), ("severity", -1),
                             ("meaningPreserved", None), ("fluency", "fluent"), ("omission", 0), ("evidence", "  ")):
            self.rows[0]["choices"][0]["review"] = {**original, field: value}
            self.write_reviews()
            with self.assertRaises(ValueError):
                self.run_aggregate()
        self.assertFalse(self.output.exists())

    def test_unknown_duplicate_reordered_or_missing_choice_labels_are_rejected(self):
        original = copy.deepcopy(self.rows[0])
        for labels in (("A", "B", "C"), ("A", "B", "B", "D"), ("B", "A", "C", "D"), ("A", "B", "C", "E")):
            self.rows[0] = copy.deepcopy(original)
            self.rows[0]["choices"] = [{**choice, "label": label} for choice, label in zip(original["choices"], labels)]
            self.write_reviews()
            with self.assertRaisesRegex(ValueError, "choice labels"):
                self.run_aggregate()
        self.rows[0] = copy.deepcopy(original)
        self.rows[0]["id"] = "unknown-id"
        self.write_reviews()
        with self.assertRaisesRegex(ValueError, "Unknown reviewed ID"):
            self.run_aggregate()

    def test_review_hashes_and_assistant_provenance_are_required(self):
        original = copy.deepcopy(self.rows[0])
        for field, value in (("anonymousInputSha256", "0" * 64), ("reviewManifestSha256", "0" * 64),
                             ("reviewerType", "human"), ("humanReviewed", True)):
            self.rows[0] = {**original, field: value}
            self.write_reviews()
            with self.assertRaisesRegex(ValueError, "provenance|hash"):
                self.run_aggregate()

    def test_mutated_key_template_or_producer_file_is_rejected(self):
        for path in (self.directory / "review-key.json", self.directory / "anonymous-review.jsonl",
                     self.base / "argos-app/predictions.jsonl"):
            before = path.read_bytes()
            path.write_bytes(before + b" ")
            with self.assertRaises(ValueError):
                self.run_aggregate()
            path.write_bytes(before)
        self.assertFalse(self.output.exists())

    def test_existing_changed_summary_and_input_collision_are_never_overwritten(self):
        self.run_aggregate()
        previous = self.output.read_bytes()
        self.output.write_bytes(previous + b" ")
        with self.assertRaisesRegex(ValueError, "immutable review summary"):
            self.run_aggregate()
        self.assertEqual(self.output.read_bytes(), previous + b" ")
        with self.assertRaisesRegex(ValueError, "may not replace"):
            self.run_aggregate(output=self.directory / "review-key.json")
        with self.assertRaisesRegex(ValueError, "producer evidence"):
            self.run_aggregate(output=self.base / "argos-app/summary.json")

    def test_review_file_changed_during_aggregation_is_rejected(self):
        original_add = aggregate.add
        changed = False
        def tamper(counts, judgment):
            nonlocal changed
            original_add(counts, judgment)
            if not changed:
                with self.first.open("ab") as stream:
                    stream.write(b" ")
                changed = True
        with patch.object(aggregate, "add", side_effect=tamper):
            with self.assertRaisesRegex(ValueError, "changed during publication"):
                self.run_aggregate()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
