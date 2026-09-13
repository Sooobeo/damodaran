"""Synthetic v4 contracts only; never loads a model or real translations."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import v4_review_evidence as helper
import reading_review_common_v4 as common
import summarize_linguistic_reviews_v4 as dev
import test_reading_reviews_v4_regression as reading_fixture
import test_linguistic_reviews_v4_regression as dev_fixture


def upgrade(directory, summary, rows, hy, root):
    version = "hymt30-development-screen-v4" if hy else "translategemma-large-screen-v4"
    summary.update(version=version, timeLimitsSeconds=copy.deepcopy(helper.TIME_LIMITS[version]))
    memory = directory / "memory-samples.jsonl"
    memory.write_bytes(b'{"synthetic":true}\n')
    memory_sha = helper.stream_hash(memory)
    if hy:
        summary.update(sampling=copy.deepcopy(helper.HY_SAMPLING),
            samplingNormalization=copy.deepcopy(helper.NORMALIZATION),
            runtimeRevision=helper.NORMALIZATION["runtimeRevision"])
        summary["artifactHashes"]["memory-samples.jsonl"] = memory_sha
        # Only frozen public runtime source bytes, never any actual model output.
        for item in helper.NORMALIZATION["sourceEvidence"]:
            target = root / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((common.ROOT / item["path"]).read_bytes())
    else:
        summary.update(modelSize="27b", memorySamplesSha256=memory_sha)
    for row in rows:
        settings = {**helper.HY_SAMPLING, "top_k": 0} if hy else {"top_k": 64, "temperature": 0}
        row.update(actualGenerationSettings=settings, outputTokenIds=[1, 2], generatedTokens=2,
            stopType="eos", truncated=False)
        native = {"content": row["translation"], "generation_settings": settings,
            "tokens": row["outputTokenIds"], "tokens_predicted": 2, "stop_type": "eos", "truncated": False}
        name = row["id"] + (".raw-response.json" if hy else "-response.json")
        (directory / name).write_bytes(common.canonical(native))
        if hy:
            digest = helper.stream_hash(directory / name)
            row.update(rawResponseFile=name, rawResponseSha256=digest,
                samplingNormalization={**helper.NORMALIZATION, "actualReportedValue": 0, "applied": True})
            summary["artifactHashes"][name] = digest
    (directory / "predictions.jsonl").write_bytes(b"".join(common.canonical(row) + b"\n" for row in rows))
    digest = helper.stream_hash(directory / "predictions.jsonl")
    if hy: summary["artifactHashes"]["predictions.jsonl"] = digest
    else: summary["predictionsSha256"] = digest
    (directory / "summary.json").write_bytes(common.canonical(summary))
    return summary, rows, digest


class V4EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = reading_fixture.ReadingReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        directory = self.fixture.directories[1]
        summary = self.fixture.v3_evidence(directory / "summary.json")
        self.directory, (self.summary, self.rows, self.digest) = directory, upgrade(
            directory, summary, self.fixture.jsonl(directory / "predictions.jsonl"), False, self.root)

    def hy(self):
        self.fixture.add_producer("hy30")
        directory = self.fixture.directories[-1]
        summary = self.fixture.v3_evidence(directory / "summary.json")
        return directory, upgrade(directory, summary, self.fixture.jsonl(directory / "predictions.jsonl"), True, self.root)

    def test_both_v4_producers_prepare_and_summarize_same_quality_rules(self):
        self.hy()
        self.fixture.prepare()
        manifest = self.fixture.json(self.fixture.prepared / "manifest.json")
        self.assertNotIn(str(self.directory), json.dumps(manifest))
        self.assertTrue(any(name.endswith("v4_review_evidence.py") for name in manifest["codeFiles"]))
        self.fixture.complete_reviews()
        result = self.fixture.summarize()
        self.assertEqual(result["judgmentCount"], 18)
        self.assertFalse(result["automaticGateCreated"])
        self.assertEqual(result["systems"][1]["runMetadata"], self.summary)

    def test_bad_time_guard_cleanup_version_and_budget_are_rejected(self):
        changes = [("timeLimitsSeconds", {"startup": 1800, "request": 1800, "total": 43200}),
            ("timeLimitsSeconds", {"startup": 1800, "request": 7200.0, "total": 86400}),
            ("memoryMonitoring", {**self.summary["memoryMonitoring"], "abortReason": "limit"}),
            ("childProcessStopped", False), ("cleanupError", "failed"),
            ("codeHashes", {}), ("version", "translategemma-large-screen-v5"), ("ramBudgetGiB", 7)]
        for key, value in changes:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                helper.check_v4_evidence({**self.summary, key: value}, self.digest)

    def test_memory_bytes_must_match_recorded_hash(self):
        (self.directory / "memory-samples.jsonl").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "artifact_hash_mismatch"):
            self.fixture.prepare()
        self.assertFalse(self.fixture.prepared.exists())

    def test_raw_translation_and_generation_settings_are_bound(self):
        path = self.directory / (self.rows[0]["id"] + "-response.json")
        original = self.fixture.json(path)
        for key, value in (("content", "다른 출력"), ("generation_settings", {}), ("tokens", [3, 4])):
            self.fixture.put_json(path, {**original, key: value})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "raw_"):
                self.fixture.prepare()
            self.assertFalse(self.fixture.prepared.exists())

    def test_tg_raw_format_change_after_preparation_rejects_even_same_semantics(self):
        self.fixture.prepare()
        self.fixture.complete_reviews()
        path = self.directory / (self.rows[0]["id"] + "-response.json")
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "artifact_inventory_changed"):
            self.fixture.summarize()
        self.assertFalse(self.fixture.output.exists())

    def test_hy_normalization_does_not_accept_other_settings_or_false_zero(self):
        directory, (summary, rows, digest) = self.hy()
        helper.check_v4_evidence(summary, digest)
        for value in (-1, 40, False):
            changed = copy.deepcopy(summary)
            changed["samplingNormalization"]["expectedReportedValue"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "normalization"):
                helper.check_v4_evidence(changed, digest)
        changed = copy.deepcopy(summary)
        changed["sampling"]["temperature"] = 0.8
        with self.assertRaisesRegex(ValueError, "normalization"):
            helper.check_v4_evidence(changed, digest)
        rows[0]["samplingNormalization"]["applied"] = False
        with self.assertRaisesRegex(ValueError, "normalization"):
            helper.check_artifacts(directory, summary, rows, common.Evidence(self.root))

    def test_hy_raw_and_pinned_source_hashes_are_required(self):
        directory, (summary, rows, _) = self.hy()
        path = directory / rows[0]["rawResponseFile"]
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "artifact_hash_mismatch"):
            helper.check_artifacts(directory, summary, rows, common.Evidence(self.root))
        path.write_bytes(original)
        source = self.root / helper.NORMALIZATION["sourceEvidence"][0]["path"]
        source.write_bytes(b"changed source")
        with self.assertRaisesRegex(ValueError, "artifact_hash_mismatch"):
            helper.check_artifacts(directory, summary, rows, common.Evidence(self.root))

    def test_mutated_review_code_inventory_rejects_before_key(self):
        self.fixture.prepare()
        self.fixture.complete_reviews()
        codes = common.code_inventory()
        codes[str(Path(helper.__file__).resolve())] = "0" * 64
        with patch.object(common, "code_inventory", return_value=codes), self.assertRaisesRegex(ValueError, "code_differs"):
            self.fixture.summarize()
        self.assertFalse(self.fixture.output.exists())

    def test_streaming_memory_hash_detects_late_change_without_reading_as_json(self):
        evidence = common.Evidence(self.root)
        helper.check_artifacts(self.directory, self.summary, self.rows, evidence)
        path = self.directory / "memory-samples.jsonl"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            evidence.unchanged()


class DevV4EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = dev_fixture.ReviewSummaryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        path, summary = self.fixture.translate_gemma_summary("27b")
        summary = self.fixture.v3_evidence(summary)
        self.directory = path.parent
        self.summary, self.rows, self.digest = upgrade(self.directory, summary,
            self.fixture.predictions[1], False, self.fixture.root)
        self.fixture.key["systems"][1].update(predictionsSha256=self.digest,
            summarySha256=helper.stream_hash(path))
        evidence = dev.Inputs(self.fixture.root)
        helper.check_artifacts(self.directory, self.summary, self.rows, evidence)
        self.fixture.manifest["v4ArtifactFilesSha256"] = helper.artifact_inventory_sha(evidence)
        self.fixture.publish_prepared()

    def test_complete_dev_v4_preserves_all_36_quality_judgments(self):
        result = self.fixture.run_summary()
        self.assertEqual(result["judgmentCount"], 36)
        self.assertFalse(result["automaticGateCreated"])
        self.assertEqual(result["systems"][1]["runMetadata"], self.summary)

    def test_dev_rejects_raw_bytes_changed_after_preparation(self):
        path = self.directory / (self.rows[0]["id"] + "-response.json")
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "artifact_inventory_changed"):
            self.fixture.run_summary()
        self.assertFalse(self.fixture.output.exists())


if __name__ == "__main__":
    unittest.main()
