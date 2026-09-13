"""Only tiny synthetic Q26-shaped files; never read weights or real probes."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import summarize_quality as summary


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(summary.json_bytes(value))


def dump_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), "utf-8")


def read(path):
    return json.loads(path.read_text("utf-8"))


class QualitySummaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="quality-summary-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = self.root / ".training/comparisons/synthetic-quality"
        self.input = self.base / "dataset.jsonl"
        self.output = self.base / "review-artifacts"
        self.run_id = "synthetic-v5"
        self.rows = []
        for index in range(24):
            source = f"The synthetic case preserves {index} units and margin."
            target = f"합성 사례는 {index}개 단위와 여백을 보존한다."
            row = {"id": f"Q26-{index + 1:03d}", "source": source, "target": target,
                   "context": "Synthetic background." if index < 8 else "", "domain": "finance" if index < 16 else "general",
                   "split": "exploratory_probe", "humanReviewed": False, "provenance": "assistant_authored_unreviewed",
                   "termTargets": ["여백"] if index < 16 else [], "forbiddenTerms": ["이익률"] if index >= 16 else [],
                   "criticalChecks": ["Preserve the stated quantity."], "protectedSymbols": [],
                   "unitChecks": [{"source": f"{index} units", "target": f"{index}개 단위", "meaning": "The count is unchanged."}]}
            for field in ("source", "target", "context"):
                row[field + "Sha256"] = summary.sha_text(row[field])
            self.rows.append(row)
        dump_rows(self.input, self.rows)
        review_path = self.base / "review.json"
        dump(review_path, {"humanReviewed": False, "synthetic": True})
        self.publication = {"version": summary.hy.DATA_VERSION, "status": "frozen", "humanReviewed": False,
                            "sourceType": "assistant_authored_unreviewed", "dataset": {"file": self.input.name,
                            "sha256": summary.digest(self.input), "count": 24, "ids": [row["id"] for row in self.rows]},
                            "reviewFiles": [{"file": review_path.name, "sha256": summary.digest(review_path)}],
                            "historicalFiles": [{"file": "DO-NOT-READ-historical-test", "sha256": "0" * 64}]}
        dump(self.base / "dataset-manifest.json", self.publication)
        for name in set(summary.ARGOS_CODE + summary.MARIAN_CODE) | {
                "scripts/model-comparison/run_hymt.py", "scripts/model-comparison/setup_hymt.py"}:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic producer implementation " + name, "utf-8")
        self.hy_code = {str(self.root / name): self.root / name for name in
                        ("scripts/model-comparison/run_hymt.py", "scripts/model-comparison/setup_hymt.py")}
        patcher = patch.object(summary, "hymt_code_paths", return_value=self.hy_code)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.model_sha = "7" * 64
        for name in ("argos-app", "marian-v5"):
            self.make_standard(name)
        self.make_hymt("hymt-raw")
        self.make_hymt("hymt-contextual")

    def make_standard(self, name):
        code = summary.ARGOS_CODE if name == "argos-app" else summary.MARIAN_CODE
        start = {"version": 1, "inputSha256": summary.digest(self.input),
                 "datasetManifestSha256": summary.digest(self.base / "dataset-manifest.json"),
                 "codeHashes": {key: summary.digest(self.root / key) for key in code},
                 "humanReviewed": False, "appDeploymentPerformed": False, "paidCalls": 0,
                 "sourceType": "assistant_authored_unreviewed", "reviewFiles": [
                     {"file": item["file"], "path": str(self.base / item["file"]), "sha256": item["sha256"]}
                     for item in self.publication["reviewFiles"]]}
        if name == "argos-app":
            start.update(profile="argos-app-pipeline", inputCount=24, glossaryApplied=True,
                         numberFormulaProtectionApplied=True, translationMemoryApplied=False, contextCollected=True,
                         contextPassedToModel=False, sourceType="assistant_authored_unreviewed",
                         runtime={"provider": "argos", "configured": True, "local": True,
                                  "model": "fixture Argos", "identity": "fixture-runtime-hash"})
        else:
            start.update(runId=self.run_id, modelSha256=self.model_sha,
                         modelFiles={key: "a" * 64 for key in summary.MARIAN_RUNTIME_FILES}, selectedStep=100,
                         settings={"device": "cpu", "precision": "fp32", "threads": 4, "seed": 20260910,
                                   "beams": 4, "maxInputTokens": 512, "maxNewTokens": 512, "doSample": False,
                                   "useCache": True, "attentionImplementation": "eager", "glossaryApplied": False,
                                   "contextPassedToModel": False, "translationMemoryApplied": False,
                                   "numberFormulaProtectionApplied": False})
        predictions = []
        for row in self.rows:
            item = {"id": row["id"], "sourceSha256": row["sourceSha256"], "contextSha256": row["contextSha256"],
                    "inputSha256": start["inputSha256"], "status": "generated", "translation": row["target"],
                    "generationSeconds": 0.25, "outputLimitReached": False, "emptyOutput": False,
                    "inputTokens": None if name == "argos-app" else 32, "generatedTokens": None if name == "argos-app" else 24,
                    "numbersPreserved": False}  # Deliberately wrong producer diagnostic must be ignored.
            if name == "argos-app":
                item["modelIdentity"] = start["runtime"]["identity"]
            else:
                item["modelSha256"] = self.model_sha
            predictions.append(item)
        directory = self.base / name
        dump(directory / "start.json", start)
        dump_rows(directory / "predictions.jsonl", predictions)
        result = {"status": "complete", "inputSha256": start["inputSha256"], "count": 24, "errorType": None,
                  "resultSha256": summary.digest(directory / "predictions.jsonl"), "totalSeconds": 8.0,
                  "humanReviewed": False, "appDeploymentPerformed": False, "paidCalls": 0, "integrityVerified": True}
        if name == "argos-app":
            result.update(generatedCount=24, databaseOpened=False, translationMemoryApplied=False)
        else:
            result.update(expectedCount=24, recordedCount=24, failedRowId=None, modelSha256=self.model_sha)
        dump(directory / "summary.json", result)

    def make_hymt(self, name):
        profile = name.removeprefix("hymt-")
        catalog = self.root / ".training/datasets/finance-v5/term-catalog.json"
        dump(catalog, {"fixture": True})
        install = self.root / ".training/comparisons/hy-mt2-7b-q8/installation-manifest.json"
        dump(install, {"model": summary.hy.MODEL, "fixture": True})
        executable = install.parent / "runtime/llama-server.exe"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"never runnable synthetic bytes")
        review_evidence = [{"path": str(self.base / item["file"]), "sha256": item["sha256"]}
                           for item in self.publication["reviewFiles"]]
        input_info = {"path": str(self.input), "sha256": summary.digest(self.input),
                      "manifestPath": str(self.base / "dataset-manifest.json"),
                      "manifestSha256": summary.digest(self.base / "dataset-manifest.json"), "version": summary.hy.DATA_VERSION,
                      "count": 24, "ids": [row["id"] for row in self.rows], "reviewEvidence": review_evidence}
        paths = list(self.hy_code.values()) + [self.input, self.base / "dataset-manifest.json", catalog, install,
                                              *[Path(item["path"]) for item in review_evidence]]
        prepared = {"version": 1, "status": "prepared", "profile": profile, "model": "tencent/Hy-MT2-7B-GGUF",
                    "revision": summary.hy.REVISION, "modelSha256": summary.hy.MODEL["sha256"], "precision": "publisher Q8_0",
                    "checkpoint": None, "trainingPerformed": False, "translationMemoryApplied": False,
                    "postProcessingApplied": False, "appDeploymentPerformed": False, "humanReviewed": False,
                    "sampling": summary.hy.SAMPLING, "contextSize": summary.hy.CONTEXT_SIZE,
                    "conditionalGlossaryHintsApplied": profile == "contextual", "cpuOnly": True,
                    "inputFieldsUsed": ["source"] + (["context"] if profile == "contextual" else []),
                    "completionRequestsSent": 0, "childProcessStopped": True, "failure": None,
                    "input": input_info, "catalog": {"path": str(catalog), "sha256": summary.digest(catalog), "count": 54},
                    "initialCodeHashes": {str(p): summary.digest(p) for p in self.hy_code.values()},
                    "codeAndInputHashes": {str(p): summary.digest(p) for p in paths}, "installation": read(install),
                    "runtimeExeSha256": summary.digest(executable),
                    "promptHashes": [{"id": row["id"], "sha256": summary.sha_text("synthetic prompt " + row["id"])} for row in self.rows]}
        predictions = []
        for row, prompt in zip(self.rows, prepared["promptHashes"]):
            tokens = [10, 127960]
            predictions.append({"id": row["id"], "status": "completed", "translation": row["target"],
                                "sourceSha256": row["sourceSha256"], "contextSha256": row["contextSha256"],
                                "targetSha256": row["targetSha256"], "promptSha256": prompt["sha256"],
                                "inputTokenIdsSha256": "d" * 64, "inputTokens": 40, "generatedTokens": len(tokens),
                                "outputTokenIds": tokens, "outputTokenIdsSha256": summary.hy.sha_json(tokens),
                                "stopType": "eos", "stoppingWord": None, "truncated": False, "outputLimitReached": False,
                                "generationSeconds": 1.0, "actualGenerationSettings": summary.hy.SAMPLING,
                                "conditionalTermMatches": [], "humanReviewed": False, "postProcessingApplied": False,
                                "numbersPreserved": False})
        directory = self.base / name
        dump(directory / "run.json", prepared)
        dump_rows(directory / "predictions.jsonl", predictions)
        (directory / "runtime.log").write_bytes(b"synthetic runtime log")
        result = {**prepared, "status": "completed", "completionRequestsSent": 24, "expectedCount": 24,
                  "recordCount": 24, "completedCount": 24, "inferenceRequested": True, "elapsedSeconds": 30.0,
                  "actualContextSize": summary.hy.CONTEXT_SIZE,
                  "predictionsSha256": summary.digest(directory / "predictions.jsonl"),
                  "runtimeLogSha256": summary.digest(directory / "runtime.log")}
        dump(directory / "summary.json", result)

    def run_summary(self, output=None):
        return summary.summarize(self.input, self.base, output or self.output, self.run_id, self.root)

    def mutate_prediction(self, name, operation, *, reseal=True):
        path = self.base / name / "predictions.jsonl"
        rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
        operation(rows)
        dump_rows(path, rows)
        if reseal:
            metadata_path = path.with_name("summary.json")
            metadata = read(metadata_path)
            metadata["predictionsSha256" if name.startswith("hymt-") else "resultSha256"] = summary.digest(path)
            dump(metadata_path, metadata)

    def test_complete_publication_preserves_raw_outputs_and_reuses_same_immutable_artifacts(self):
        before = {name: (self.base / name / "predictions.jsonl").read_bytes() for name in summary.SYSTEMS}
        manifest_path = self.run_summary()
        manifest = read(manifest_path)
        self.assertFalse(manifest["qualityGateApplied"])
        self.assertFalse(manifest["humanReviewed"])
        original = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.output.iterdir()}
        self.assertEqual(self.run_summary(), manifest_path)
        self.assertEqual(original, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.output.iterdir()})
        self.assertEqual(before, {name: (self.base / name / "predictions.jsonl").read_bytes() for name in summary.SYSTEMS})
        report = read(self.output / "comparison-metrics.json")
        self.assertIn("실행 환경 동일성 검증 없음", report["latencyComparisonLimitation"])
        self.assertIsNone(report["recordedExecutionContext"])
        for name in summary.SYSTEMS:
            self.assertEqual(report["systems"][name]["domains"]["all"]["numericTokensMatchCount"], 24)
            self.assertEqual(report["systems"][name]["domains"]["finance"]["termHits"], 16)
            self.assertAlmostEqual(report["systems"][name]["domains"]["all"]["chrF"], 100.0)
        self.assertEqual(report["systems"]["argos-app"]["outputLimitUnknownCount"], 24)
        blind = [json.loads(line) for line in (self.output / "anonymous-review.jsonl").read_text("utf-8").splitlines()]
        key = read(self.output / "review-key.json")
        self.assertGreater(len({tuple(item["labels"].values()) for item in key["mappings"]}), 1)
        for row, original_row, mapping in zip(blind, self.rows, key["mappings"]):
            self.assertEqual(row["source"], original_row["source"])
            self.assertEqual(row["assistantReference"], original_row["target"])
            self.assertEqual(set(mapping["labels"]), set("ABCD"))
            for choice in row["choices"]:
                self.assertEqual(choice["translationSha256"], summary.sha_text(choice["translation"]))
                self.assertIsNone(choice["review"]["severity"])
                self.assertFalse(any(name in json.dumps(choice) for name in summary.SYSTEMS))

    def test_incomplete_or_failed_system_does_not_publish(self):
        for name in summary.SYSTEMS:
            with self.subTest(name=name):
                path = self.base / name / "summary.json"
                original = path.read_bytes()
                value = read(path)
                value["status"] = "partial"
                dump(path, value)
                with self.assertRaises(ValueError):
                    self.run_summary()
                self.assertFalse(self.output.exists())
                path.write_bytes(original)

    def test_duplicate_missing_reordered_and_extra_predictions_are_rejected(self):
        for operation in (lambda values: values.pop(), lambda values: values.append(values[0]),
                          lambda values: values.reverse(), lambda values: values.__setitem__(1, values[0])):
            self.make_standard("argos-app")
            self.mutate_prediction("argos-app", operation)
            with self.assertRaisesRegex(ValueError, "coverage"):
                self.run_summary()

    def test_source_context_dataset_and_model_identity_mutations_fail_closed(self):
        for field, value in (("sourceSha256", "0" * 64), ("contextSha256", "0" * 64),
                             ("inputSha256", "0" * 64), ("modelSha256", "0" * 64)):
            self.make_standard("marian-v5")
            self.mutate_prediction("marian-v5", lambda rows: rows[0].update({field: value}))
            with self.assertRaises(ValueError):
                self.run_summary()

    def test_raw_result_file_mutation_without_matching_seal_is_rejected(self):
        self.mutate_prediction("argos-app", lambda values: values[0].update(translation="changed"), reseal=False)
        with self.assertRaisesRegex(ValueError, "SHA"):
            self.run_summary()

    def test_current_producer_code_and_missing_code_inventory_are_rejected(self):
        path = self.root / summary.ARGOS_CODE[0]
        original = path.read_bytes()
        path.write_bytes(original + b"modified")
        with self.assertRaisesRegex(ValueError, "SHA"):
            self.run_summary()
        path.write_bytes(original)
        metadata_path = self.base / "argos-app/start.json"
        value = read(metadata_path)
        value["codeHashes"].pop(summary.ARGOS_CODE[0])
        dump(metadata_path, value)
        with self.assertRaisesRegex(ValueError, "code inventory"):
            self.run_summary()

    def test_completed_producer_must_have_final_integrity_and_review_evidence(self):
        for name in ("argos-app", "marian-v5"):
            for filename, field, value in (("summary.json", "integrityVerified", False),
                                           ("start.json", "reviewFiles", [])):
                with self.subTest(name=name, field=field):
                    self.make_standard(name)
                    path = self.base / name / filename
                    metadata = read(path)
                    metadata[field] = value
                    dump(path, metadata)
                    with self.assertRaises(ValueError):
                        self.run_summary()
            self.make_standard(name)

    def test_linked_input_is_rejected_before_reading(self):
        target = self.base / "argos-app/start.json"
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == target or original(path)):
            with self.assertRaisesRegex(ValueError, "Linked input"):
                self.run_summary()

    def test_scalar_code_is_captured_before_producer_read_and_cannot_change(self):
        code_path = self.root / "synthetic-scalar-helper.py"
        code_path.write_bytes(b"synthetic scalar implementation")
        original = summary.read_standard
        changed = False
        def modifying_read(*args):
            nonlocal changed
            result = original(*args)
            if not changed:
                code_path.write_bytes(b"changed scalar implementation")
                changed = True
            return result
        with patch.object(summary, "scalar_code_paths", return_value=[code_path]), \
             patch.object(summary, "read_standard", side_effect=modifying_read):
            with self.assertRaisesRegex(ValueError, "changed during publication"):
                self.run_summary()
        self.assertFalse(self.output.exists())

    def test_execution_context_is_hashed_as_a_historical_record_not_a_final_model_status(self):
        path = self.base / "execution-context.json"
        record = {"version": 1, "recordedAt": "2026-09-10T01:00:00Z",
                  "argosApp": {"standaloneLatencyMeasurement": False}, "otherModelInferenceStarted": False}
        dump(path, record)
        self.run_summary()
        manifest, report = read(self.output / "manifest.json"), read(self.output / "comparison-metrics.json")
        self.assertEqual(manifest["identity"]["executionContext"]["sha256"], summary.digest(path))
        self.assertEqual(report["recordedExecutionContext"], record)
        self.assertIn("기록 시점", report["executionContextInterpretation"])
        self.assertIn("속도 순위로 비교하지", report["latencyComparisonLimitation"])
        dump(path, {**record, "otherModelInferenceStarted": True})
        with self.assertRaisesRegex(ValueError, "immutable publication identity"):
            self.run_summary()

    def test_numeric_bounds_and_nonfinite_times_are_rejected(self):
        for field, value in (("inputTokens", 513), ("inputTokens", True), ("generatedTokens", 513),
                             ("generatedTokens", -1), ("generationSeconds", -0.1), ("generationSeconds", True)):
            self.make_standard("marian-v5")
            self.mutate_prediction("marian-v5", lambda values: values[0].update({field: value}))
            with self.assertRaises(ValueError):
                self.run_summary()
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            summary.parse('{"seconds":NaN}')

    def test_empty_capped_and_numeric_failures_are_counted_without_a_quality_gate(self):
        def change(values):
            values[0].update(translation="", emptyOutput=True)
            values[1].update(translation="합성 999개", generatedTokens=512, outputLimitReached=True)
        self.mutate_prediction("marian-v5", change)
        self.run_summary()
        values = read(self.output / "comparison-metrics.json")["systems"]["marian-v5"]["domains"]["all"]
        self.assertEqual(values["emptyCount"], 1)
        self.assertEqual(values["outputLimitReachedCount"], 1)
        self.assertEqual(values["numericTokensMatchCount"], 22)

    def test_hymt_target_prompt_tokens_and_stop_evidence_are_checked(self):
        for field, value in (("targetSha256", "0" * 64), ("promptSha256", "0" * 64),
                             ("outputTokenIdsSha256", "0" * 64), ("generatedTokens", 3),
                             ("outputLimitReached", True), ("inputTokens", 4096), ("stopType", "unknown")):
            self.make_hymt("hymt-raw")
            self.mutate_prediction("hymt-raw", lambda values: values[0].update({field: value}))
            with self.assertRaises(ValueError):
                self.run_summary()

    def test_hymt_runtime_log_and_profile_cannot_change(self):
        log = self.base / "hymt-raw/runtime.log"
        log.write_bytes(b"modified log")
        with self.assertRaisesRegex(ValueError, "SHA"):
            self.run_summary()
        self.make_hymt("hymt-raw")
        path = self.base / "hymt-raw/summary.json"
        value = read(path)
        value["profile"] = "contextual"
        dump(path, value)
        with self.assertRaises(ValueError):
            self.run_summary()

    def test_changed_publication_artifact_or_input_is_never_overwritten(self):
        self.run_summary()
        path = self.output / "anonymous-review.jsonl"
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.run_summary()
        self.assertEqual(path.read_bytes(), original + b" ")
        path.write_bytes(original)
        self.mutate_prediction("argos-app", lambda values: values[0].update(translation="새 결과"))
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.run_summary()
        self.assertEqual(path.read_bytes(), original)

    def test_publication_race_and_pending_directory_preserve_existing_files(self):
        original_metrics = summary.metrics
        def changing_input(*args):
            result = original_metrics(*args)
            with (self.base / "argos-app/predictions.jsonl").open("ab") as stream:
                stream.write(b" ")
            return result
        with patch.object(summary, "metrics", side_effect=changing_input):
            with self.assertRaisesRegex(ValueError, "changed during publication"):
                self.run_summary()
        self.assertFalse(self.output.exists())
        self.make_standard("argos-app")
        staging = self.output.with_name(self.output.name + ".pending")
        staging.mkdir()
        (staging / "preserve.txt").write_bytes(b"preserve interrupted publication")
        with self.assertRaises(FileExistsError):
            self.run_summary()
        self.assertEqual((staging / "preserve.txt").read_bytes(), b"preserve interrupted publication")

    def test_invalid_paths_run_id_and_duplicate_json_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "boundary"):
            summary.summarize(self.input, self.base, self.root / "outside", self.run_id, self.root)
        with self.assertRaisesRegex(ValueError, "run ID"):
            summary.summarize(self.input, self.base, self.output, "../bad", self.root)
        with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
            summary.parse('{"id":"one","id":"two"}')
        with self.assertRaisesRegex(ValueError, "Marian model identity"):
            summary.summarize(self.input, self.base, self.output, "other-v5", self.root)


    def test_archived_source_revalidates_after_live_app_code_changes(self):
        manifest = summary.code_snapshot.archive(self.base / "argos-app", self.root)
        before = manifest.read_bytes()
        (self.root / "lib/translation/local.ts").write_bytes(b"a later application change")
        self.run_summary()
        published = read(self.output / "manifest.json")
        self.assertIn(str(manifest), published["identity"]["inputFiles"])
        self.assertNotIn(str(self.root / "lib/translation/local.ts"), published["identity"]["inputFiles"])
        self.assertEqual(summary.code_snapshot.archive(self.base / "argos-app", self.root).read_bytes(), before)
        self.run_summary()

    def test_archived_bytes_cannot_be_changed_even_if_live_source_still_matches(self):
        path = summary.code_snapshot.archive(self.base / "argos-app", self.root)
        manifest = read(path)
        first = next(iter(manifest["files"].values()))
        (path.parent / first["file"]).write_bytes(b"tampered source")
        with self.assertRaisesRegex(ValueError, "Historical producer bytes"):
            self.run_summary()

    def test_archive_binds_metadata_and_rejects_changed_source_before_copy(self):
        original = self.root / "lib/translation/local.ts"
        previous = original.read_bytes()
        original.write_bytes(b"changed before archival")
        with self.assertRaisesRegex(ValueError, "source changed before archival"):
            summary.code_snapshot.archive(self.base / "argos-app", self.root)
        original.write_bytes(previous)
        summary.code_snapshot.archive(self.base / "argos-app", self.root)
        start_path = self.base / "argos-app/start.json"
        start = read(start_path)
        start["extraHistoricalField"] = "a later edit"
        dump(start_path, start)
        with self.assertRaisesRegex(ValueError, "not bound"):
            self.run_summary()

    def test_archive_rejects_incomplete_runs_traversal_and_duplicate_json(self):
        run = self.base / "argos-app"
        summary_path = run / "summary.json"
        prior = summary_path.read_bytes()
        value = read(summary_path)
        value["integrityVerified"] = False
        dump(summary_path, value)
        with self.assertRaisesRegex(ValueError, "integrity-verified"):
            summary.code_snapshot.archive(run, self.root)
        summary_path.write_bytes(prior)
        start_path = run / "start.json"
        value = read(start_path)
        value["codeHashes"] = {"scripts/../private.json": "0" * 64}
        dump(start_path, value)
        with self.assertRaisesRegex(ValueError, "Unsupported producer"):
            summary.code_snapshot.archive(run, self.root)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            summary.code_snapshot.parse('{"status":"complete","status":"failed"}')

    def test_null_code_hash_is_never_an_optional_verification(self):
        path = self.base / "argos-app/start.json"
        original = path.read_bytes()
        for archived in (False, True):
            with self.subTest(archived=archived):
                path.write_bytes(original)
                if archived:
                    summary.code_snapshot.archive(self.base / "argos-app", self.root)
                value = read(path)
                value["codeHashes"]["lib/translation/local.ts"] = None
                dump(path, value)
                with self.assertRaisesRegex(ValueError, "Producer code inventory"):
                    self.run_summary()
        path.write_bytes(original)

    def test_explicit_missing_hash_is_distinct_from_collecting_a_new_hash(self):
        inputs = summary.Inputs()
        observed = inputs.record(self.input)
        self.assertEqual(observed, summary.digest(self.input))
        for invalid in (None, "", "not-a-sha", False, 0):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(ValueError, "SHA256 changed"):
                    summary.Inputs().record(self.input, invalid)

    def test_missing_hymt_runtime_and_log_hashes_are_rejected(self):
        directory = self.base / "hymt-raw"
        for field in ("runtimeExeSha256", "runtimeLogSha256"):
            with self.subTest(field=field):
                self.make_hymt("hymt-raw")
                for name in ("run.json", "summary.json"):
                    path = directory / name
                    value = read(path)
                    value[field] = None
                    dump(path, value)
                with self.assertRaisesRegex(ValueError, "SHA256 changed"):
                    self.run_summary()


if __name__ == "__main__":
    unittest.main()
