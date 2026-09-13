"""Synthetic boundary checks only; no actual model outputs, key, DB or inference."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_reading_check_input as input_fixture
import reading_review_common_v4 as common
import prepare_reading_reviews_v4 as preparer
import summarize_reading_reviews_v4 as scorer


class ReadingReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = input_fixture.ReadingInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.input = self.fixture.root, self.fixture.path
        self.clean, self.identity = common.reader.read_reading_check(self.input)
        self.base = self.root / ".training/comparisons/reading-synthetic"
        self.base.mkdir(parents=True)
        self.prepared = self.base / "prepared"
        self.output = self.base / "summary-review.json"
        self.review = self.base / "review.jsonl"
        self.directories = []
        self.add_producer("baseline")
        self.add_producer("tg")

    def json(self, path):
        return json.loads(path.read_text("utf-8"))

    def jsonl(self, path):
        return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]

    def put_json(self, path, value):
        path.write_bytes(common.canonical(value) + b"\n")

    def put_jsonl(self, path, rows):
        path.write_bytes(b"".join(common.canonical(row) + b"\n" for row in rows))

    def add_producer(self, kind):
        index = len(self.directories)
        directory = self.base / f"system-{index}"
        directory.mkdir()
        predictions = []
        for row in self.clean:
            translation = f"합성 번역 {index}: {row['id']}의 관계를 담은 예시다."
            predictions.append({"id": row["id"], "status": "completed", "translation": translation,
                "targetSha256": common.reader.text_hash(translation), "sourceSha256": row["sourceSha256"],
                "contextSha256": row["contextSha256"]})
        self.put_jsonl(directory / "predictions.jsonl", predictions)
        prediction_sha = common.reader.digest(directory / "predictions.jsonl")
        summary = {"status": "completed", "codeHashes": {"fixture-only.py": "d" * 64},
                   "profile": "contextual", "humanReviewed": False}
        if kind == "tg":
            summary.update(version="translategemma-large-screen-v2", input=copy.deepcopy(self.identity),
                count=6, recordedCount=6, expectedCount=6, integrityVerified=True, childProcessStopped=True,
                predictionsSha256=prediction_sha, modelSize="12b", profile="source-only", modelSha256="a" * 64,
                installationManifestSha256="b" * 64)
        else:
            summary.update(copy.deepcopy(self.identity), completed=6)
            if kind == "baseline":
                summary.update(model="Hy-MT2-7B-Q8_0", modelSha256="a" * 64,
                    deployedIdentity="hymt:" + "a" * 64 + ":" + "b" * 64, childStopped=True)
            else:
                summary.update(version="hymt30-development-screen-v2", childProcessStopped=True, modelLoaded=True,
                    model={"sha256": "c" * 64, "name": "synthetic.gguf", "size": 100},
                    modelRevision="a" * 40, runtimeRevision="b" * 40, installationManifestSha256="b" * 64,
                    artifactHashes={"predictions.jsonl": prediction_sha})
        self.put_json(directory / "summary.json", summary)
        self.directories.append(directory)

    def prepare(self):
        return preparer.prepare(self.input, self.directories, self.prepared, root=self.root)

    def complete_reviews(self):
        rows = self.jsonl(self.prepared / "review-template.jsonl")
        for row in rows:
            row["reviewerId"] = "synthetic"
            for judgment in row["judgments"]:
                judgment.update(severity=0, fluent=True, reasonKo="합성 원문의 주체와 관계를 대조한 테스트 판단이다.")
        self.put_jsonl(self.review, rows)
        return rows

    def summarize(self):
        return scorer.summarize(self.prepared, [self.review], self.output, root=self.root)

    def publish_prepared_change(self, name):
        manifest = self.json(self.prepared / "manifest.json")
        if name == "review-key.json":
            manifest["keySha256"] = common.reader.digest(self.prepared / name)
        else:
            manifest["files"][name] = common.reader.digest(self.prepared / name)
        self.put_json(self.prepared / "manifest.json", manifest)
        reviews = self.jsonl(self.review)
        for row in reviews:
            row["preparedManifestSha256"] = common.reader.digest(self.prepared / "manifest.json")
            row["packetSha256"] = manifest["files"]["packet.jsonl"]
        self.put_jsonl(self.review, reviews)

    def test_complete_12_judgments_keep_clean_inputs_and_separate_review_annotations(self):
        manifest = self.prepare()
        packet = self.jsonl(self.prepared / "packet.jsonl")
        self.assertEqual(manifest["judgmentCount"], 12)
        self.assertEqual(set(packet[0]["input"]), set(common.reader.ALLOWLIST))
        self.assertNotIn("SECRET", json.dumps(packet[0]["input"]))
        self.assertIn("SECRET", json.dumps(packet[0]["reference"]))
        self.assertEqual((self.prepared / "review-advisory.json").read_bytes(),
                         (self.input.parent / "review-advisory.json").read_bytes())
        rows = self.complete_reviews()
        # A natural sentence can still have a material error: fluency remains independent.
        judgment = rows[0]["judgments"][0]
        judgment.update(severity=2, errors=[{"severity": 2, "category": "semantic_role",
            "sourceSpan": "Public source example 0", "targetSpan": "합성 번역", "reasonKo": "주체 대응 오류의 합성 증거다."}])
        self.put_jsonl(self.review, rows)
        result = self.summarize()
        self.assertEqual(len(result["evidence"]), 12)
        self.assertEqual(sum(r["counts"]["materialErrorRows"] for r in result["systems"]), 1)
        self.assertEqual(sum(r["counts"]["fluentRows"] for r in result["systems"]), 12)
        self.assertFalse(result["automaticGateCreated"])
        self.assertFalse(result["canonicalTermScoreCreated"])
        self.assertTrue(all(r["runMetadataSha256"] == common.sha(common.canonical(r["runMetadata"])) for r in result["systems"]))

    def test_four_systems_require_24_judgments_and_shuffle_each_source(self):
        self.add_producer("hy30")
        self.add_producer("tg")
        orders = []
        class Alternating:
            def shuffle(_, values):
                index = len(orders) % len(values)
                values[:] = values[index:] + values[:index]
                orders.append(list(values))
        with patch.object(preparer.random, "SystemRandom", return_value=Alternating()):
            self.prepare()
        self.assertEqual(len(orders), 6)
        self.assertNotEqual(orders[0], orders[1])
        self.complete_reviews()
        result = self.summarize()
        self.assertEqual(result["systemCount"], 4)
        self.assertEqual(result["judgmentCount"], 24)
        self.assertTrue(all(r["counts"]["rows"] == 6 for r in result["systems"]))

    def test_missing_judgment_or_row_rejects_before_any_key_read(self):
        self.prepare()
        complete = self.complete_reviews()
        original_read = common.Evidence.read
        def guarded(instance, path, *args, **kwargs):
            self.assertNotEqual(Path(path).name, "review-key.json", "key opened before completed judgments")
            return original_read(instance, path, *args, **kwargs)
        for missing in ("row", "judgment"):
            rows = copy.deepcopy(complete)
            if missing == "row":
                rows.pop()
            else:
                rows[0]["judgments"].pop()
            self.put_jsonl(self.review, rows)
            with self.subTest(missing=missing), patch.object(common.Evidence, "read", guarded), self.assertRaises(ValueError):
                self.summarize()
            self.assertFalse(self.output.exists())

    def test_incomplete_producer_rejected_before_prepared_folder_creation(self):
        path = self.directories[1] / "summary.json"
        original = self.json(path)
        for field, value in (("status", "failed"), ("count", 5), ("recordedCount", 5), ("expectedCount", 5),
            ("count", 6.0), ("integrityVerified", False), ("childProcessStopped", False),
            ("modelSha256", None), ("codeHashes", {}), ("predictionsSha256", "0" * 64)):
            self.put_json(path, dict(original, **{field: value}))
            with self.subTest(field=field), self.assertRaises(ValueError): self.prepare()
            self.assertFalse(self.prepared.exists())

    def test_baseline_deployment_identity_is_structured_and_matches_model(self):
        path = self.directories[0] / "summary.json"
        original = self.json(path)
        for identity in ("b" * 64, "hymt:" + "c" * 64 + ":" + "b" * 64):
            self.put_json(path, dict(original, deployedIdentity=identity))
            with self.assertRaisesRegex(ValueError, "baseline_completion_identity"): self.prepare()

    def test_hy30_incomplete_metadata_and_unbound_output_are_rejected(self):
        self.add_producer("hy30")
        path = self.directories[-1] / "summary.json"
        original = self.json(path)
        for field, value in (("completed", 5), ("model", None), ("runtimeRevision", None),
                             ("installationManifestSha256", None), ("childProcessStopped", False), ("artifactHashes", {})):
            self.put_json(path, dict(original, **{field: value}))
            with self.subTest(field=field), self.assertRaises(ValueError): self.prepare()

    def v3_evidence(self, path):
        summary = self.json(path)
        summary["version"] = summary["version"].replace("-v2", "-v3")
        summary.update(pagingExperiment=True, ramBudgetGiB=10,
            suspendedCreation={"synthetic": True}, childWorkingSetLimit={"synthetic": True},
            memorySamplesSha256="c" * 64, memoryMonitoring={"abortReason": None,
                "monitorError": None, "ownedChildKillError": None, "observations": 20, "recordedSamples": 2})
        if "artifactHashes" in summary:
            summary["artifactHashes"]["memory-samples.jsonl"] = "c" * 64
        self.put_json(path, summary)
        return summary

    def test_v3_tg_and_hy30_keep_original_metadata_and_quality_rules(self):
        tg = self.v3_evidence(self.directories[1] / "summary.json")
        self.add_producer("hy30")
        hy = self.v3_evidence(self.directories[2] / "summary.json")
        self.prepare()
        self.complete_reviews()
        result = self.summarize()
        self.assertEqual(result["judgmentCount"], 18)
        self.assertEqual(result["systems"][1]["runMetadata"], tg)
        self.assertEqual(result["systems"][2]["runMetadata"], hy)
        self.assertFalse(result["automaticGateCreated"])

    def test_v3_guard_failure_is_rejected_before_packet_creation(self):
        path = self.directories[1] / "summary.json"
        original = self.v3_evidence(path)
        for field, value in (("childProcessStopped", False), ("memoryOrTimeGuardAborted", True),
                             ("memoryMonitoring", {}), ("memorySamplesSha256", None), ("codeHashes", {})):
            self.put_json(path, dict(original, **{field: value}))
            with self.subTest(field=field), self.assertRaises(ValueError): self.prepare()
            self.assertFalse(self.prepared.exists())

    def test_producer_input_hash_and_prediction_coverage_cannot_be_substituted(self):
        path = self.directories[1] / "summary.json"
        original = self.json(path)
        for field, value in (("inputSha256", "f" * 64), ("contextSha256", "f" * 64), ("selectedIds", []),
                             ("inputFiles", {}), ("readerCodeFiles", {})):
            self.put_json(path, original)
            summary = copy.deepcopy(original)
            if field == "contextSha256":
                predictions = self.jsonl(self.directories[0] / "predictions.jsonl")
                predictions[0][field] = value
                self.put_jsonl(self.directories[0] / "predictions.jsonl", predictions)
            else:
                summary["input"][field] = value
                self.put_json(path, summary)
            with self.subTest(field=field), self.assertRaises(ValueError): self.prepare()
            if field == "contextSha256":
                predictions[0][field] = self.clean[0][field]
                self.put_jsonl(self.directories[0] / "predictions.jsonl", predictions)
        self.put_json(path, original)
        prediction_path = self.directories[0] / "predictions.jsonl"
        rows = self.jsonl(prediction_path)
        self.put_jsonl(prediction_path, rows[:-1])
        with self.assertRaisesRegex(ValueError, "coverage"): self.prepare()

    def test_unfinished_template_duplicate_ids_and_wrong_hashes_are_rejected(self):
        self.prepare()
        template = self.jsonl(self.prepared / "review-template.jsonl")
        self.put_jsonl(self.review, template)
        with self.assertRaises(ValueError): self.summarize()
        complete = self.complete_reviews()
        for kind in ("duplicate", "source", "context", "packet", "target"):
            rows = copy.deepcopy(complete)
            if kind == "duplicate": rows.append(rows[0])
            elif kind == "target": rows[0]["judgments"][0]["targetSha256"] = "0" * 64
            else: rows[0][{"source": "sourceSha256", "context": "contextSha256", "packet": "packetSha256"}[kind]] = "0" * 64
            self.put_jsonl(self.review, rows)
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.summarize()

    def test_error_spans_severity_and_individual_reason_are_required(self):
        self.prepare()
        complete = self.complete_reviews()
        error = {"severity": 2, "category": "omission", "sourceSpan": "Public source example 0",
                 "targetSpan": "", "reasonKo": "원문의 일부가 빠진 합성 사례다."}
        for kind in ("source", "target", "max", "bool", "reason"):
            rows = copy.deepcopy(complete)
            judgment = rows[0]["judgments"][0]
            judgment.update(severity=2, errors=[dict(error)])
            if kind == "source": judgment["errors"][0]["sourceSpan"] = "ABSENT_SOURCE"
            if kind == "target": judgment["errors"][0].update(category="semantic_role", targetSpan="ABSENT_TARGET")
            if kind == "max": judgment["severity"] = 1
            if kind == "bool": judgment["severity"] = True
            if kind == "reason": judgment["reasonKo"] = ""
            self.put_jsonl(self.review, rows)
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.summarize()
        rows = copy.deepcopy(complete)
        rows[0]["judgments"][0].update(severity=2, errors=[error])
        self.put_jsonl(self.review, rows)
        self.assertTrue(self.summarize()["completed"])

    def test_packet_reference_cannot_be_resigned_to_a_different_answer(self):
        self.prepare()
        self.complete_reviews()
        path = self.prepared / "packet.jsonl"
        packet = self.jsonl(path)
        packet[0]["reference"]["referenceKo"] = "조작한 답안"
        self.put_jsonl(path, packet)
        self.publish_prepared_change(path.name)
        with self.assertRaisesRegex(ValueError, "annotation"): self.summarize()

    def test_raw_advisory_and_producer_bytes_must_stay_bound(self):
        self.prepare()
        self.complete_reviews()
        for path in (self.prepared / "review-advisory.json", self.directories[0] / "predictions.jsonl",
                     self.directories[1] / "summary.json"):
            original = path.read_bytes()
            path.write_bytes(original + b"\n")
            with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, "hash_mismatch"): self.summarize()
            path.write_bytes(original)

    def test_duplicate_key_system_mapping_is_rejected(self):
        self.prepare()
        self.complete_reviews()
        path = self.prepared / "review-key.json"
        key = self.json(path)
        key["mapping"][1]["systemIndex"] = key["mapping"][0]["systemIndex"]
        self.put_json(path, key)
        self.publish_prepared_change(path.name)
        with self.assertRaises(ValueError): self.summarize()

    def test_immutable_output_and_prepared_paths(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "new_prepared"): self.prepare()
        self.complete_reviews()
        self.summarize()
        before = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, "new_output"): self.summarize()
        self.assertEqual(self.output.read_bytes(), before)
        with self.assertRaises(ValueError):
            scorer.summarize(self.prepared, [self.prepared / "review-template.jsonl"], self.base / "other.json", root=self.root)


if __name__ == "__main__":
    unittest.main()
