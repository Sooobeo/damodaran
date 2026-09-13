"""Synthetic contracts only: no real source packets, outputs, models or DB."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import summarize_linguistic_reviews_v5 as scorer


class ReviewSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.content = self.root / "content/model-comparison"
        self.content.mkdir(parents=True)
        self.directory = self.root / ".training/comparisons/synthetic"
        self.prepared = self.directory / "prepared"
        self.prepared.mkdir(parents=True)
        self.output = self.directory / "review-summary.json"
        self.source_path = self.content / "dataset.jsonl"
        self.sources = []
        for index in range(18):
            row = {"id": f"S{index:02d}", "source": f"Original source {index}.", "context": "",
                   "reference": "도우미가 작성한 참조다.", "split": "development_screen",
                   "humanReviewed": False, "domain": "finance" if index < 16 else "general",
                   "category": "semantic_roles" if index < 2 else "other",
                   "canonicalTermChecks": [{"id": "G23"}] if index == 0 else []}
            for key in ("source", "context", "reference"):
                row[key + "Sha256"] = scorer.hash_bytes(row[key].encode())
            if index < 12:
                row["pair"] = {"id": f"P{index // 2}", "contrastWith": f"S{index ^ 1:02d}"}
            self.sources.append(row)
        self.write_jsonl(self.source_path, self.sources)
        self.ids = [r["id"] for r in self.sources]
        dm = {"version": "linguistic-dev-20260910-v1", "status": "frozen", "humanReviewed": False,
              "sourceType": "assistant_authored_unreviewed", "dataset": {
                  "file": self.source_path.name, "sha256": self.sha(self.source_path), "count": 18, "ids": self.ids}}
        self.dm_path = self.content / "dataset-manifest.json"
        self.write_json(self.dm_path, dm)
        self.manifest = {"inputPath": str(self.source_path), "inputSha256": self.sha(self.source_path),
            "manifestPath": str(self.dm_path), "manifestSha256": self.sha(self.dm_path), "totalRows": 18,
            "selectedIds": self.ids, "sourceCount": 18, "judgmentCount": 36,
            "humanReview": False, "humanReviewed": False, "files": {},
            "reviewEvidenceVersion": "v5", "reviewCodeFiles": scorer.review_code_inventory(),
            "v5ArtifactFilesSha256": scorer.v4_review_evidence.artifact_inventory_sha(scorer.Inputs(self.root))}
        self.predictions = []
        self.key = {"systems": [], "mapping": []}
        for index in range(2):
            directory = self.directory / f"system-{index}"
            directory.mkdir()
            predictions = [{"id": r["id"], "sourceSha256": r["sourceSha256"],
                "contextSha256": r["contextSha256"], "translation": f"합성 결과 {index} {r['id']}.",
                "status": "completed"} for r in self.sources]
            for prediction in predictions:
                prediction["targetSha256"] = scorer.hash_bytes(prediction["translation"].encode())
            self.predictions.append(predictions)
            self.write_jsonl(directory / "predictions.jsonl", predictions)
            self.write_json(directory / "summary.json", {"status": "completed", "completed": 18,
                "inputSha256": self.manifest["inputSha256"], "manifestSha256": self.manifest["manifestSha256"],
                "selectedIds": self.ids, "totalRows": 18, "model": f"synthetic-{index}",
                "modelSha256": str(index) * 64, "paidCalls": 0})
            self.key["systems"].append({"directory": str(directory),
                "predictionsSha256": self.sha(directory / "predictions.jsonl"),
                "summarySha256": self.sha(directory / "summary.json")})
        self.packets, self.reviews = [], []
        for index, source in enumerate(self.sources):
            candidates, judgments = [], []
            for label_index, label in enumerate("AB"):
                system_index = label_index ^ (index % 2)
                prediction = self.predictions[system_index][index]
                candidates.append({k: prediction[k] for k in ("translation", "targetSha256")} | {"label": label})
                self.key["mapping"].append({"id": source["id"], "label": label, "systemIndex": system_index,
                                            "targetSha256": prediction["targetSha256"]})
                judgments.append({"id": source["id"], "label": label, "targetSha256": prediction["targetSha256"],
                    "severity": 0, "fluent": True, "contrastPreserved": True if index < 12 else None,
                    "errors": [], "termChecks": [{"id": "G23", "correctSense": True, "canonicalForm": True,
                        "reasonKo": "해당 용어의 뜻과 표기가 맞다."}] if index == 0 else [],
                    "humanReviewed": False, "reasonKo": "합성 원문의 주체와 관계가 유지된다."})
            self.packets.append({"input": copy.deepcopy(source), "candidates": candidates, "reviewInstruction": "합성 검사"})
            self.reviews.append({"id": source["id"], "sourceSha256": source["sourceSha256"],
                "reviewerType": "assistant", "humanReviewed": False, "preparedManifestSha256": "pending",
                "packetSha256": "pending", "judgments": judgments, "reviewerId": "synthetic"})
        self.review_path = self.directory / "reviews.jsonl"
        self.publish_prepared()

    def sha(self, path):
        return scorer.hash_bytes(path.read_bytes())

    def write_json(self, path, value):
        path.write_bytes(json.dumps(value, ensure_ascii=False).encode() + b"\n")

    def write_jsonl(self, path, rows):
        path.write_bytes(b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in rows))

    def publish_prepared(self):
        for index in range(3):
            name = f"packet-{index + 1}.jsonl"
            path = self.prepared / name
            self.write_jsonl(path, self.packets[index * 6:index * 6 + 6])
            self.manifest["files"][name] = self.sha(path)
        self.write_json(self.prepared / "review-key.json", self.key)
        self.manifest["keySha256"] = self.sha(self.prepared / "review-key.json")
        self.write_json(self.prepared / "manifest.json", self.manifest)
        for index, row in enumerate(self.reviews):
            row["preparedManifestSha256"] = self.sha(self.prepared / "manifest.json")
            row["packetSha256"] = self.manifest["files"][f"packet-{index // 6 + 1}.jsonl"]
        self.write_jsonl(self.review_path, self.reviews)

    def run_summary(self):
        return scorer.summarize(self.prepared, [self.review_path], self.output, root=self.root)

    def error(self, severity=2, category="semantic_role", target="결과"):
        return {"severity": severity, "category": category, "sourceSpan": "source 0",
                "targetSpan": target, "reasonKo": "원문의 주체 관계가 바뀌었다."}

    def test_complete_summary_separates_sense_form_fluency_and_pairs(self):
        judgment = self.reviews[0]["judgments"][0]
        judgment.update(severity=2, errors=[self.error()], contrastPreserved=False)
        judgment["termChecks"][0]["correctSense"] = False
        self.write_jsonl(self.review_path, self.reviews)
        result = self.run_summary()
        self.assertEqual(result["judgmentCount"], 36)
        a, b = result["systems"]
        self.assertEqual(a["counts"]["materialRows"], 1)
        self.assertEqual(a["counts"]["fluentRows"], 18)
        self.assertEqual(a["counts"]["canonicalForm"], 1)
        self.assertEqual(a["counts"]["jointCorrect"], 0)
        self.assertEqual(b["counts"]["jointCorrect"], 1)
        self.assertEqual(a["completeContrastPairsPreserved"], 5)
        self.assertEqual(b["completeContrastPairsPreserved"], 6)
        self.assertEqual(a["worstErrorCategories"], ["semantic_role"])
        self.assertFalse(result["automaticGateCreated"])
        self.assertIsNone(result["promotionDecision"])

    def test_missing_review_rejects_before_key_access(self):
        self.write_jsonl(self.review_path, self.reviews[:-1])
        real_read = scorer.Inputs.read
        def guarded(instance, path, *args, **kwargs):
            self.assertNotEqual(Path(path).name, "review-key.json")
            return real_read(instance, path, *args, **kwargs)
        with patch.object(scorer.Inputs, "read", guarded), self.assertRaisesRegex(ValueError, "all_18"):
            self.run_summary()
        self.assertFalse(self.output.exists())

    def test_duplicate_review_id_and_duplicate_file_rejected(self):
        self.write_jsonl(self.review_path, self.reviews + self.reviews[:1])
        with self.assertRaisesRegex(ValueError, "duplicate_review_id"):
            self.run_summary()
        with self.assertRaisesRegex(ValueError, "duplicate_review_files"):
            scorer.summarize(self.prepared, [self.review_path] * 2, self.output, self.root)

    def test_bad_source_target_packet_manifest_hashes_rejected(self):
        for field in ("sourceSha256", "packetSha256", "preparedManifestSha256"):
            original = self.reviews[0][field]
            self.reviews[0][field] = "a" * 64
            self.write_jsonl(self.review_path, self.reviews)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "identity"):
                self.run_summary()
            self.reviews[0][field] = original
        self.reviews[0]["judgments"][0]["targetSha256"] = "a" * 64
        self.write_jsonl(self.review_path, self.reviews)
        with self.assertRaisesRegex(ValueError, "target_identity"):
            self.run_summary()

    def test_boolean_severity_and_unresolved_flags_rejected(self):
        judgment = self.reviews[0]["judgments"][0]
        for field, bad in (("severity", True), ("fluent", None), ("contrastPreserved", None)):
            original = judgment[field]
            judgment[field] = bad
            self.write_jsonl(self.review_path, self.reviews)
            with self.subTest(field=field), self.assertRaises(ValueError): self.run_summary()
            judgment[field] = original

    def test_empty_positive_reason_rejected(self):
        self.reviews[0]["judgments"][0]["reasonKo"] = ""
        self.write_jsonl(self.review_path, self.reviews)
        with self.assertRaisesRegex(ValueError, "reason_missing"): self.run_summary()

    def test_term_coverage_duplicates_and_nonboolean_rejected(self):
        judgment = self.reviews[0]["judgments"][0]
        original = copy.deepcopy(judgment["termChecks"])
        for bad in ([], original * 2, [dict(original[0], id="WRONG")], [dict(original[0], correctSense=1)]):
            judgment["termChecks"] = bad
            self.write_jsonl(self.review_path, self.reviews)
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "term_check"): self.run_summary()

    def test_wrong_max_severity_and_nonexistent_spans_rejected(self):
        judgment = self.reviews[0]["judgments"][0]
        for severity, error in ((1, self.error()), (2, dict(self.error(), sourceSpan="absent")),
                                (2, dict(self.error(), targetSpan="absent")), (2, self.error(target=""))):
            judgment.update(severity=severity, errors=[error])
            self.write_jsonl(self.review_path, self.reviews)
            with self.subTest(error=error), self.assertRaises(ValueError): self.run_summary()

    def test_omission_allows_empty_target_span(self):
        self.reviews[0]["judgments"][0].update(severity=2, errors=[self.error(category="omission", target="")])
        self.write_jsonl(self.review_path, self.reviews)
        self.assertEqual(self.run_summary()["systems"][0]["counts"]["materialRows"], 1)

    def test_packet_annotation_cannot_be_resigned_away(self):
        self.packets[0]["input"]["reference"] = "바뀐 참조"
        self.publish_prepared()
        with self.assertRaisesRegex(ValueError, "annotations_changed"): self.run_summary()

    def test_duplicate_key_mapping_system_is_rejected(self):
        # Same candidate text across systems is legal; repeated system mapping is not.
        for predictions in self.predictions:
            predictions[0].update(self.predictions[0][0])
        self.packets[0]["candidates"][1].update({k: self.predictions[0][0][k] for k in ("translation", "targetSha256")})
        self.key["mapping"][1].update(systemIndex=0, targetSha256=self.predictions[0][0]["targetSha256"])
        self.reviews[0]["judgments"][1]["targetSha256"] = self.predictions[0][0]["targetSha256"]
        self.publish_prepared()
        with self.assertRaisesRegex(ValueError, "each_system_once"): self.run_summary()

    def test_wrong_producer_input_or_incomplete_status_rejected(self):
        path = Path(self.key["systems"][0]["directory"]) / "summary.json"
        value = json.loads(path.read_text("utf-8"))
        value["completed"] = 17
        self.write_json(path, value)
        self.key["systems"][0]["summarySha256"] = self.sha(path)
        self.publish_prepared()
        with self.assertRaisesRegex(ValueError, "not_complete"): self.run_summary()

    def test_raw_hash_tamper_and_json_duplicate_key_rejected(self):
        packet = self.prepared / "packet-1.jsonl"
        packet.write_bytes(packet.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "hash_mismatch"): self.run_summary()
        self.publish_prepared()
        raw = self.review_path.read_text("utf-8")
        self.review_path.write_text(raw.replace('"humanReviewed": false', '"humanReviewed": false,"humanReviewed": false', 1), "utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate_json_key"): self.run_summary()

    def test_changed_during_aggregation_refuses_output(self):
        original = scorer.Inputs.unchanged
        def changed(inputs):
            self.review_path.write_bytes(self.review_path.read_bytes() + b"\n")
            return original(inputs)
        with patch.object(scorer.Inputs, "unchanged", changed), self.assertRaisesRegex(ValueError, "changed_during"):
            self.run_summary()
        self.assertFalse(self.output.exists())

    def test_output_never_overwrites_and_cannot_enter_prepared(self):
        self.run_summary()
        before = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, "already_exists"): self.run_summary()
        self.assertEqual(before, self.output.read_bytes())
        with self.assertRaisesRegex(ValueError, "outside_prepared"):
            scorer.summarize(self.prepared, [self.review_path], self.prepared / "summary.json", self.root)

    def test_nonfinite_json_and_parent_escape_rejected(self):
        with self.assertRaisesRegex(ValueError, "nonfinite"): scorer.strict_json('{"x":NaN}')
        with self.assertRaisesRegex(ValueError, "parent_path"):
            scorer.local(self.directory / ".." / "elsewhere", self.root)

    def test_missing_declared_source_hash_is_not_optional(self):
        del self.manifest["inputSha256"]
        self.publish_prepared()
        with self.assertRaisesRegex(ValueError, "input_hash_missing"): self.run_summary()

    def test_noninteger_manifest_count_is_rejected(self):
        self.manifest["sourceCount"] = 18.0
        self.publish_prepared()
        with self.assertRaisesRegex(ValueError, "manifest_contract"): self.run_summary()

    def extend_system_count(self, count):
        for index in range(2, count):
            directory = self.directory / f"system-{index}"
            directory.mkdir()
            predictions = copy.deepcopy(self.predictions[0])
            for row in predictions:
                row["translation"] = f"추가 합성 결과 {index} {row['id']}."
                row["targetSha256"] = scorer.hash_bytes(row["translation"].encode())
            self.predictions.append(predictions)
            self.write_jsonl(directory / "predictions.jsonl", predictions)
            summary = json.loads((self.directory / "system-0/summary.json").read_text("utf-8"))
            summary.update(model=f"synthetic-{index}", modelSha256=str(index) * 64)
            self.write_json(directory / "summary.json", summary)
            self.key["systems"].append({"directory": str(directory),
                "predictionsSha256": self.sha(directory / "predictions.jsonl"),
                "summarySha256": self.sha(directory / "summary.json")})
        self.key["mapping"] = []
        for row_index, (packet, review) in enumerate(zip(self.packets, self.reviews)):
            original_judgment = copy.deepcopy(review["judgments"][0])
            packet["candidates"], review["judgments"] = [], []
            for label_index, label in enumerate("ABCD"[:count]):
                system_index = (label_index + row_index) % count
                prediction = self.predictions[system_index][row_index]
                candidate = {k: prediction[k] for k in ("translation", "targetSha256")} | {"label": label}
                packet["candidates"].append(candidate)
                judgment = copy.deepcopy(original_judgment)
                judgment.update(label=label, targetSha256=prediction["targetSha256"])
                review["judgments"].append(judgment)
                self.key["mapping"].append({"id": review["id"], "label": label,
                    "systemIndex": system_index, "targetSha256": prediction["targetSha256"]})
        self.manifest["judgmentCount"] = 18 * count
        self.publish_prepared()

    def test_three_systems_require_all_54_judgments(self):
        self.extend_system_count(3)
        result = self.run_summary()
        self.assertEqual(result["systemCount"], 3)
        self.assertEqual(result["judgmentCount"], 54)
        self.assertTrue(all(s["counts"]["rows"] == 18 and s["completeContrastPairsPreserved"] == 6
                            for s in result["systems"]))

    def test_four_systems_require_all_72_judgments(self):
        self.extend_system_count(4)
        result = self.run_summary()
        self.assertEqual(result["systemCount"], 4)
        self.assertEqual(result["judgmentCount"], 72)
        self.assertEqual(len(result["evidence"]), 72)

    def test_inconsistent_candidate_count_rejected_before_key(self):
        self.extend_system_count(4)
        self.packets[0]["candidates"].pop()
        self.publish_prepared()
        with self.assertRaisesRegex(ValueError, "candidate_coverage"): self.run_summary()

    def test_missing_fourth_judgment_rejected_before_key(self):
        self.extend_system_count(4)
        self.reviews[0]["judgments"].pop()
        self.write_jsonl(self.review_path, self.reviews)
        with self.assertRaisesRegex(ValueError, "judgment_label_coverage"): self.run_summary()

    def translate_gemma_summary(self, model_size="12b"):
        system = self.key["systems"][1]
        path = Path(system["directory"]) / "summary.json"
        old = json.loads(path.read_text("utf-8"))
        return path, {"version": "translategemma-large-screen-v1", "status": "completed",
            "count": 18, "recordedCount": 18, "expectedCount": 18,
            "input": {k: old[k] for k in ("totalRows", "inputSha256", "manifestSha256", "selectedIds")},
            "modelSize": model_size, "profile": "source-only", "settings": {"seed": 42},
            "modelSha256": old["modelSha256"], "integrityVerified": True,
            "childProcessStopped": True, "predictionsSha256": system["predictionsSha256"]}

    def publish_gemma_summary(self, path, summary):
        self.write_json(path, summary)
        self.key["systems"][1]["summarySha256"] = self.sha(path)
        self.publish_prepared()

    def test_translate_gemma_nested_input_and_complete_counts_are_supported(self):
        path, summary = self.translate_gemma_summary()
        self.publish_gemma_summary(path, summary)
        result = self.run_summary()
        self.assertEqual(result["systems"][1]["summarySchema"], "translategemma-large-screen-v1")
        self.assertEqual(result["systems"][1]["runMetadata"], summary)
        self.assertEqual(result["systems"][1]["counts"]["rows"], 18)

    def test_translate_gemma_v2_preserves_raw_metadata_and_schema(self):
        path, summary = self.translate_gemma_summary("27b")
        summary["version"] = "translategemma-large-screen-v2"
        self.publish_gemma_summary(path, summary)
        result = self.run_summary()
        self.assertEqual(result["systems"][1]["summarySchema"], summary["version"])
        self.assertEqual(result["systems"][1]["runMetadata"], summary)
        self.assertEqual(result["systems"][1]["counts"]["rows"], 18)

    def v3_evidence(self, summary):
        summary.update(pagingExperiment=True, ramBudgetGiB=10, codeHashes={"synthetic.py": "a" * 64},
            installationManifestSha256="b" * 64, suspendedCreation={"synthetic": True},
            childWorkingSetLimit={"synthetic": True}, memorySamplesSha256="c" * 64,
            memoryMonitoring={"abortReason": None, "monitorError": None, "ownedChildKillError": None,
                              "observations": 20, "recordedSamples": 2})
        return summary

    def test_v3_tg_preserves_metadata_and_all_existing_quality_counts(self):
        path, summary = self.translate_gemma_summary("27b")
        summary["version"] = "translategemma-large-screen-v3"
        self.publish_gemma_summary(path, self.v3_evidence(summary))
        result = self.run_summary()
        self.assertEqual(result["systems"][1]["runMetadata"], summary)
        self.assertEqual(result["systems"][1]["counts"]["rows"], 18)
        self.assertFalse(result["automaticGateCreated"])

    def test_v3_guard_cleanup_hash_and_input_failures_are_rejected(self):
        path, original = self.translate_gemma_summary("27b")
        original["version"] = "translategemma-large-screen-v3"
        self.v3_evidence(original)
        changes = [(None, field, value) for field, value in (
            ("childProcessStopped", False), ("integrityVerified", False), ("memoryOrTimeGuardAborted", True),
            ("codeHashes", {}), ("predictionsSha256", "0" * 64), ("memorySamplesSha256", None),
            ("finalIntegrityError", "changed"), ("count", 17))]
        changes += [("memoryMonitoring", field, value) for field, value in (
            ("abortReason", "working_set_budget_exceeded"), ("monitorError", "failed"),
            ("ownedChildKillError", "failed"), ("recordedSamples", 0))]
        changes += [("input", "inputSha256", "0" * 64)]
        for parent, field, value in changes:
            summary = copy.deepcopy(original)
            (summary if parent is None else summary[parent])[field] = value
            self.publish_gemma_summary(path, summary)
            with self.subTest(field=field), self.assertRaises(ValueError): self.run_summary()
            self.assertFalse(self.output.exists())

    def test_v3_hy30_keeps_invalid_output_diagnostic_separate_from_completion(self):
        path = Path(self.key["systems"][1]["directory"]) / "summary.json"
        summary = self.v3_evidence(json.loads(path.read_text("utf-8")))
        summary.update(version="hymt30-development-screen-v3", modelLoaded=True, childProcessStopped=True,
            profile="contextual", model={"name": "synthetic.gguf", "size": 100, "sha256": "d" * 64},
            modelRevision="e" * 40, runtimeRevision="f" * 40, outputIntegrityPassed=False,
            artifactHashes={"predictions.jsonl": self.key["systems"][1]["predictionsSha256"],
                            "memory-samples.jsonl": "c" * 64})
        self.publish_gemma_summary(path, summary)
        result = self.run_summary()
        self.assertEqual(result["systems"][1]["runMetadata"], summary)
        self.assertFalse(result["automaticGateCreated"])

    def test_preparer_checks_producer_contract_before_creating_packets(self):
        import prepare_linguistic_reviews_v5 as preparer
        output = self.directory / "new-prepared"
        audit = self.content / "source-audit.json"
        self.write_json(audit, {"version": "linguistic-dev-independent-source-audit-v1",
            "modelOutputsViewed": False, "humanReviewed": False,
            "inputs": {self.source_path.relative_to(self.root).as_posix(): self.manifest["inputSha256"]}})
        path, summary = self.translate_gemma_summary()
        summary["childProcessStopped"] = False
        self.write_json(path, summary)
        argv = ["prepare", "--input", str(self.source_path), "--source-audit", str(audit),
                "--output", str(output), "--results", *[s["directory"] for s in self.key["systems"]]]
        with patch.object(preparer, "ROOT", self.root), patch.object(preparer, "read_screen",
                return_value=(self.sources, self.manifest)), patch.object(preparer, "assert_input_unchanged"), \
                patch("sys.argv", argv), self.assertRaisesRegex(ValueError, "completion_identity"):
            preparer.main()
        self.assertFalse(output.exists())

    def test_translate_gemma_normalizer_rejects_missing_or_incomplete_evidence(self):
        path, original = self.translate_gemma_summary("27b")
        for field, value in (("count", 17), ("recordedCount", 17), ("expectedCount", 17),
                             ("count", 18.0), ("integrityVerified", False),
                             ("childProcessStopped", False), ("predictionsSha256", "0" * 64),
                             ("completed", 18)):
            summary = copy.deepcopy(original)
            summary[field] = value
            self.publish_gemma_summary(path, summary)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "translategemma"):
                self.run_summary()

    def test_translate_gemma_nested_input_hash_and_order_are_not_relaxed(self):
        path, original = self.translate_gemma_summary("27b")
        for field, value in (("inputSha256", "a" * 64), ("manifestSha256", "b" * 64),
                             ("selectedIds", list(reversed(self.ids))), ("totalRows", 17)):
            summary = copy.deepcopy(original)
            summary["input"][field] = value
            self.publish_gemma_summary(path, summary)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "not_complete"):
                self.run_summary()

    def test_translate_gemma_v2_keeps_all_v1_identity_and_completion_checks(self):
        path, original = self.translate_gemma_summary("27b")
        original["version"] = "translategemma-large-screen-v2"
        changes = [(None, field, value) for field, value in (
            ("count", 17), ("recordedCount", 17), ("expectedCount", 17), ("count", 18.0),
            ("integrityVerified", False), ("childProcessStopped", False),
            ("predictionsSha256", "0" * 64), ("completed", 18), ("status", "failed"))]
        changes += [("input", field, value) for field, value in (
            ("inputSha256", "a" * 64), ("manifestSha256", "b" * 64),
            ("selectedIds", list(reversed(self.ids))), ("totalRows", 17))]
        for parent, field, value in changes:
            summary = copy.deepcopy(original)
            (summary if parent is None else summary[parent])[field] = value
            self.publish_gemma_summary(path, summary)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_summary()
            self.assertFalse(self.output.exists())

    def test_unknown_summary_version_is_not_silently_treated_as_baseline(self):
        path, summary = self.translate_gemma_summary()
        summary["version"] = "unrecognized-producer-v99"
        self.publish_gemma_summary(path, summary)
        with self.assertRaisesRegex(ValueError, "unsupported_producer"): self.run_summary()

    def test_hymt30_explicit_version_uses_same_strong_flat_identity(self):
        path = Path(self.key["systems"][1]["directory"]) / "summary.json"
        summary = json.loads(path.read_text("utf-8"))
        summary["version"] = "hymt30-development-screen-v1"
        self.publish_gemma_summary(path, summary)
        result = self.run_summary()
        self.assertEqual(result["systems"][1]["summarySchema"], "hymt30-development-screen-v1")

    def test_hymt30_v2_uses_same_strong_flat_identity(self):
        path = Path(self.key["systems"][1]["directory"]) / "summary.json"
        summary = json.loads(path.read_text("utf-8"))
        summary["version"] = "hymt30-development-screen-v2"
        self.publish_gemma_summary(path, summary)
        result = self.run_summary()
        self.assertEqual(result["systems"][1]["summarySchema"], summary["version"])
        self.assertEqual(result["systems"][1]["runMetadata"], summary)

    def test_hymt30_v2_rejects_incomplete_or_different_input(self):
        path = Path(self.key["systems"][1]["directory"]) / "summary.json"
        original = json.loads(path.read_text("utf-8"))
        original["version"] = "hymt30-development-screen-v2"
        for field, value in (("completed", 17), ("completed", 18.0), ("status", "failed"),
                             ("totalRows", 17), ("inputSha256", "a" * 64),
                             ("manifestSha256", "b" * 64), ("selectedIds", list(reversed(self.ids)))):
            summary = dict(original, **{field: value})
            self.publish_gemma_summary(path, summary)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "not_complete"):
                self.run_summary()
            self.assertFalse(self.output.exists())

    def source_audit(self):
        audit = {"version": "linguistic-dev-independent-source-audit-v1", "modelOutputsViewed": False,
                 "humanReviewed": False, "inputs": {
                     self.source_path.relative_to(self.root).as_posix(): self.manifest["inputSha256"]}}
        path = self.prepared / "source-audit.json"
        self.write_json(path, audit)
        self.manifest["sourceAudit"] = {"file": path.name, "sha256": self.sha(path)}
        self.publish_prepared()
        return path, audit

    def test_source_audit_is_bound_to_output_and_evidence_inventory(self):
        path, _ = self.source_audit()
        result = self.run_summary()
        self.assertEqual(result["sourceAuditSha256"], self.sha(path))
        self.assertEqual(result["validatedFiles"][str(path)], self.sha(path))

    def test_source_audit_wrong_dataset_or_model_exposure_is_rejected(self):
        path, original = self.source_audit()
        for field, value in (("inputs", {self.source_path.relative_to(self.root).as_posix(): "f" * 64}),
                             ("modelOutputsViewed", True), ("humanReviewed", True), ("version", "other")):
            self.write_json(path, dict(original, **{field: value}))
            self.manifest["sourceAudit"]["sha256"] = self.sha(path)
            self.publish_prepared()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "source_audit_provenance"):
                self.run_summary()

    def test_source_audit_cannot_change_filename_or_bytes(self):
        path, _ = self.source_audit()
        self.manifest["sourceAudit"]["file"] = "../source-audit.json"
        self.publish_prepared()
        with self.assertRaisesRegex(ValueError, "source_audit_file_identity"): self.run_summary()
        self.manifest["sourceAudit"]["file"] = "source-audit.json"
        self.publish_prepared()
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "hash_mismatch"): self.run_summary()


if __name__ == "__main__":
    unittest.main()
