"""Synthetic artifacts only: no actual translation/model/DB/process execution."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import v5_review_evidence as evidence
import reading_review_common_v5 as common
import prepare_reading_reviews_v5 as reading_prepare
import summarize_reading_reviews_v5 as reading_summary
import prepare_linguistic_reviews_v5 as dev_prepare
import summarize_linguistic_reviews_v5 as dev_summary
import test_reading_reviews_v4_regression as reading_fixture
import test_linguistic_reviews_v4_regression as dev_fixture


def put(path, value, lines=False):
    raw = b"".join(evidence.canonical(row) + b"\n" for row in value) if lines else evidence.canonical(value) + b"\n"
    path.write_bytes(raw)


def get(path, lines=False):
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()] if lines else json.loads(path.read_text("utf-8"))


def upgrade(directory, clean, identity, root):
    """Write fabricated numeric telemetry and raw outputs under a temp fixture."""
    source_root = Path(__file__).resolve().parents[2]
    for relative in evidence.SUPPORTED_CODE:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes((source_root / relative).read_bytes())
    start = datetime(2026, 9, 11, tzinfo=timezone.utc)
    timestamp = lambda seconds: (start + timedelta(seconds=seconds)).isoformat()
    own = {"pid": 123, "creationTicks": 456}
    records, predictions, hashes = [], [], {}
    def sample(seconds, phase, rid=None, request_start=None, elapsed=None):
        gap = seconds - records[-1]["seconds"] if records else None
        record = {"seconds": seconds, "telemetryVersion": evidence.TELEMETRY, "observedAtUTC": timestamp(seconds),
            "previousObservationUTC": records[-1]["observedAtUTC"] if records else None,
            "phase": phase, "rowId": rid, "requestStartUTC": request_start, "requestElapsedSeconds": elapsed,
            "requestActive": phase == "generation-request", "heartbeatGapSeconds": gap,
            "maximumHeartbeatGapSincePreviousRecordSeconds": gap or 0, "recordGapSeconds": gap,
            "requestTokenProgress": None, "requestTokenProgressAvailable": False,
            "load": 40, "totalPhysical": 24 * evidence.GIB, "availablePhysical": 3 * evidence.GIB,
            "totalPageFile": 32 * evidence.GIB, "availablePageFile": 8 * evidence.GIB,
            "child": {**own, "workingSetBytes": evidence.GIB, "peakWorkingSetBytes": evidence.GIB,
                "privateBytes": evidence.GIB, "pageFaultCount": int(seconds * 10),
                "cpuKernelTicks": int(seconds * 10000000), "cpuUserTicks": 0,
                "cpuKernelSeconds": seconds, "cpuUserSeconds": 0.0, "cpuTotalSeconds": seconds,
                "priorityClass": 0x4000, "belowNormalVerified": True}}
        records.append(record)
    sample(0, "startup")
    sample(0.5, "runtime-validation")
    sample(0.6, "prompt-preflight")
    for index, row in enumerate(clean):
        rid, seconds = row["id"], 1 + 2 * index
        request_start = timestamp(seconds)
        sample(seconds, "generation-request", rid, request_start, 0.0)
        sample(seconds + 1, "response-validation", rid, request_start, 1.0)
        translation = "합성 출력 " + rid
        native = {"content": translation, "tokens": [123, 1], "tokens_predicted": 2,
                  "stop_type": "eos", "truncated": False, "generation_settings": copy.deepcopy(evidence.TG_SETTINGS)}
        name = rid + "-response.json"
        put(directory / name, native)
        hashes[name] = evidence.stream_hash(directory / name)
        predictions.append({"id": rid, "status": "completed", "translation": translation,
            "sourceSha256": row["sourceSha256"], "contextSha256": row["contextSha256"],
            "targetSha256": hashlib.sha256(translation.encode("utf-8")).hexdigest(),
            "actualGenerationSettings": native["generation_settings"], "outputTokenIds": [123, 1],
            "outputTokenIdsSha256": hashlib.sha256(evidence.canonical([123, 1])).hexdigest(),
            "generatedTokens": 2, "inputTokens": 10, "stopType": "eos", "truncated": False,
            "outputLimitReached": False, "terminalTokenId": 1, "terminationClass": "original_model_eog",
            "rawResponseFile": name, "rawResponseSha256": hashes[name]})
    sample(seconds + 1.1, "final-integrity", rid, request_start, 1.0)
    put(directory / "memory-samples.jsonl", records, True)
    put(directory / "predictions.jsonl", predictions, True)
    receipt = {**own, "after": {"minimumBytes": 1024 ** 2, "maximumBytes": 8 * evidence.GIB - 64 * 1024 ** 2, "flags": 6,
                              "hardMaximumEnabled": True, "hardMinimumDisabled": True}}
    summary = {"version": evidence.VERSION, "status": "completed", "startedAt": timestamp(0), "finishedAt": timestamp(seconds + 2),
        "input": copy.deepcopy(identity), "expectedCount": len(clean), "count": len(clean), "recordedCount": len(clean),
        "modelSize": "27b", "profile": "source-only", "humanReviewed": False, "contextUsed": False,
        "pagingExperiment": True, "ramBudgetGiB": 8, "integrityVerified": True, "childProcessStopped": True,
        "memoryPolicy": {"ramBudgetGiB": 8, "requiredAvailablePhysicalBytes": 11 * evidence.GIB,
            "requiredAvailableCommitBytes": 4261412864, "maximumWorkingSetBytes": 8 * evidence.GIB,
            "requestedHardMaximumWorkingSetBytes": 8 * evidence.GIB - 64 * 1024 ** 2,
            "workingSetReservationBytes": 64 * 1024 ** 2, "minimumWorkingSetBytes": 1024 ** 2,
            "weightRepackingEnabled": False},
        "memoryBefore": {"availablePhysical": 11 * evidence.GIB, "availablePageFile": 8 * evidence.GIB},
        "memoryImmediatelyBeforeStartup": {"availablePhysical": 11 * evidence.GIB, "availablePageFile": 8 * evidence.GIB},
        "modelSha256": "a" * 64, "installationManifestSha256": "b" * 64,
        "codeHashes": {str((root / relative).resolve()): digest for relative, digest in evidence.SUPPORTED_CODE.items()},
        "timeLimitsSeconds": {"startup": 1800, "request": 7200, "total": 86400},
        "settings": copy.deepcopy(evidence.TG_SETTINGS), "threads": 4, "runtimeTokenizationAndEogValidated": True,
        "telemetryVersion": evidence.TELEMETRY, "copiedFromV4Sha256": evidence.BASE_V4_SHA256,
        "requiredNativePriorityClass": 0x4000, "nativePrioritySetAfterSpawnByRunner": False,
        "childCreationCleanupUnconfirmed": False,
        "requestTokenProgressAvailable": False, "rawNativeLogsRetained": False,
        "childWorkingSetLimit": receipt, "suspendedCreation": {"version": "comparison-suspended-owned-spawn-v1",
            "pid": own["pid"], "atomicJobAssignment": True, "createSuspended": True,
            "limitAppliedBeforeResume": True, "resumePreviousCount": 1, "workingSetLimit": copy.deepcopy(receipt)},
        "responseFilesSha256": hashes, "predictionsSha256": evidence.stream_hash(directory / "predictions.jsonl"),
        "memorySamplesSha256": evidence.stream_hash(directory / "memory-samples.jsonl"),
        "memoryMonitoring": {"abortReason": None, "monitorError": None, "ownedChildKillError": None,
            "lastRequestDeadlineExceededAtEnd": False,
            "observations": len(records), "recordedSamples": len(records), "telemetryVersion": evidence.TELEMETRY,
            "ownedIdentity": own, "requiredPriorityClass": 0x4000, "observeIntervalSeconds": 0.5, "recordIntervalSeconds": 5,
            "minimumAvailablePhysicalBytes": 512 * 1024 ** 2, "minimumAvailableCommitBytes": 512 * 1024 ** 2,
            "lowDurationSeconds": 3, "independentLowResourceTimers": True, "maximumObservedWorkingSetBytes": 8 * evidence.GIB,
            "sampleFile": "memory-samples.jsonl", "requestTokenProgressAvailable": False, "rawNativeLogsRetained": False,
            "lastPhase": "final-integrity", "lastRowId": rid, "lastRequestElapsedSeconds": 1.0,
            "lastRequestStartUTC": request_start, "lastObservationUTC": records[-1]["observedAtUTC"],
            "maximumHeartbeatGapSeconds": max(row["maximumHeartbeatGapSincePreviousRecordSeconds"] for row in records),
            "firstCpuTicks": 0, "lastCpuTicks": records[-1]["child"]["cpuKernelTicks"], "cpuTickUnitSeconds": 1e-7,
            "ownedChildExitObserved": {**own, "exitCode": 0, "seconds": seconds + 1.5, "observedAtUTC": timestamp(seconds + 1.5)},
            "minimumAvailablePhysical": 3 * evidence.GIB, "minimumAvailableCommit": 8 * evidence.GIB,
            "maximumChildWorkingSet": evidence.GIB, "maximumChildPeakWorkingSet": evidence.GIB,
            "maximumChildPrivateBytes": evidence.GIB}}
    put(directory / "summary.json", summary)
    return summary


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = reading_fixture.ReadingReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.directory = self.fixture.root, self.fixture.directories[1]
        self.summary = upgrade(self.directory, self.fixture.clean, self.fixture.identity, self.root)

    def check(self, summary=None):
        evidence.check_artifacts(self.directory, summary or self.summary,
            get(self.directory / "predictions.jsonl", True), common.Evidence(self.root))

    def test_complete_real_schema_keeps_original_metadata(self):
        original = copy.deepcopy(self.summary)
        with patch.object(evidence.legacy, "check_v4_evidence", wraps=evidence.legacy.check_v4_evidence) as legacy_check:
            self.check()
        self.assertEqual(legacy_check.call_args.args[0]["version"], "translategemma-large-screen-v4")
        self.assertEqual(self.summary, original)

    def test_v4_guard_rejections_still_apply(self):
        for field, value in (("childProcessStopped", False), ("status", "failed"), ("integrityVerified", False),
            ("memoryOrTimeGuardAborted", True), ("cleanupError", "owned_child_creation_cleanup_failed"),
            ("recordedCount", 5), ("codeHashes", {})):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check(self.summary | {field: value})

    def test_missing_owned_exit_or_none_process_claim_cannot_pass(self):
        for field in ("ownedChildExitObserved", "ownedIdentity"):
            changed = copy.deepcopy(self.summary)
            changed["memoryMonitoring"].pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError): self.check(changed)
        for field in ("suspendedCreation", "childWorkingSetLimit"):
            with self.subTest(field=field), self.assertRaises(ValueError): self.check(self.summary | {field: None})

    def test_startup_headroom_and_two_hour_limit_are_not_relaxed(self):
        for field in ("memoryBefore", "memoryImmediatelyBeforeStartup"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "headroom"):
                self.check(self.summary | {field: {"availablePhysical": 11 * evidence.GIB - 1, "availablePageFile": 8 * evidence.GIB}})
        changed = copy.deepcopy(self.summary)
        changed["memoryMonitoring"]["lastRequestElapsedSeconds"] = 7200
        with self.assertRaisesRegex(ValueError, "final_state"): self.check(changed)
        changed = copy.deepcopy(self.summary)
        changed["memoryMonitoring"]["lastRequestDeadlineExceededAtEnd"] = True
        with self.assertRaises(ValueError): self.check(changed)
        changed["memoryMonitoring"].pop("lastRequestDeadlineExceededAtEnd")
        with self.assertRaises(ValueError): self.check(changed)
        for value in (True, None):
            with self.assertRaises(ValueError): self.check(self.summary | {"childCreationCleanupUnconfirmed": value})
        changed = copy.deepcopy(self.summary)
        changed["childWorkingSetLimit"]["after"]["hardMaximumEnabled"] = False
        changed["suspendedCreation"]["workingSetLimit"] = copy.deepcopy(changed["childWorkingSetLimit"])
        with self.assertRaisesRegex(ValueError, "readback"): self.check(changed)

    def test_telemetry_mutations_cannot_be_resigned_as_valid(self):
        path = self.directory / "memory-samples.jsonl"
        original = get(path, True)
        changes = [lambda rows: rows[0].pop("heartbeatGapSeconds"),
            lambda rows: rows[0]["child"].pop("belowNormalVerified"),
            lambda rows: rows[3]["child"].update(creationTicks=457),
            lambda rows: rows[3]["child"].update(priorityClass=0x20, belowNormalVerified=False),
            lambda rows: rows[3]["child"].update(cpuTotalSeconds=99),
            lambda rows: rows[4].update(heartbeatGapSeconds=99),
            lambda rows: rows[4].update(rowId="UNKNOWN"),
            lambda rows: rows[4].update(requestStartUTC="2026-09-12T00:00:00+00:00"),
            lambda rows: rows[-1].update(phase="response-validation"),
            lambda rows: rows[3].update(source="PRIVATE SOURCE"),
            lambda rows: rows[3].update(requestTokenProgress=3),
            lambda rows: rows[3].update(requestElapsedSeconds=7200)]
        for index, change in enumerate(changes):
            rows = copy.deepcopy(original)
            change(rows)
            put(path, rows, True)
            summary = self.summary | {"memorySamplesSha256": evidence.stream_hash(path)}
            with self.subTest(index=index), self.assertRaises(ValueError): self.check(summary)
        put(path, original, True)

    def test_row_request_coverage_and_summary_counter_cannot_be_removed(self):
        path = self.directory / "memory-samples.jsonl"
        rows = get(path, True)
        rows[3]["phase"] = "response-validation"
        rows[3]["requestActive"] = False
        put(path, rows, True)
        with self.assertRaisesRegex(ValueError, "without_request"):
            self.check(self.summary | {"memorySamplesSha256": evidence.stream_hash(path)})

    def test_raw_output_and_completion_time_hash_are_both_required(self):
        row = get(self.directory / "predictions.jsonl", True)[0]
        raw_path = self.directory / row["rawResponseFile"]
        before = raw_path.read_bytes()
        raw_path.write_bytes(before + b"\n")
        with self.assertRaisesRegex(ValueError, "hash_mismatch"): self.check()
        raw_path.write_bytes(before)
        summary = copy.deepcopy(self.summary)
        summary["responseFilesSha256"].pop(row["rawResponseFile"])
        with self.assertRaisesRegex(ValueError, "inventory"): self.check(summary)

    def test_arbitrary_output_python_is_never_executed(self):
        script = self.directory / "producer.py"
        script.write_text("raise AssertionError('must never execute')", encoding="utf-8")
        changed = copy.deepcopy(self.summary)
        changed["codeHashes"] = {str(script): evidence.stream_hash(script)}
        with self.assertRaisesRegex(ValueError, "supported_producer_code"):
            self.check(changed)

    def test_reading_prepare_and_aggregate_keep_v5_and_legacy_separate(self):
        fixture = self.fixture
        before = (self.directory / "summary.json").read_bytes()
        manifest = reading_prepare.prepare(fixture.input, fixture.directories, fixture.prepared, self.root)
        self.assertEqual(manifest["version"], "real-reading-blind-review-packets-v5")
        public_packet = (fixture.prepared / "packet.jsonl").read_text("utf-8")
        for forbidden in ("translategemma-large-screen-v5", "codeHashes", "creationTicks", "system-1"):
            self.assertNotIn(forbidden, public_packet)
        fixture.complete_reviews()
        result = reading_summary.summarize(fixture.prepared, [fixture.review], fixture.output, self.root)
        self.assertEqual(result["judgmentCount"], 12)
        self.assertEqual(result["systems"][1]["runMetadata"], self.summary)
        self.assertEqual(result["systems"][1]["summarySchema"], evidence.VERSION)
        self.assertFalse(result["automaticGateCreated"])
        self.assertEqual((self.directory / "summary.json").read_bytes(), before)

    def test_missing_telemetry_refuses_packet_folder(self):
        changed = copy.deepcopy(self.summary)
        changed["memoryMonitoring"].pop("ownedIdentity")
        put(self.directory / "summary.json", changed)
        fixture = self.fixture
        with self.assertRaises(ValueError):
            reading_prepare.prepare(fixture.input, fixture.directories, fixture.prepared, self.root)
        self.assertFalse(fixture.prepared.exists())

    def test_aggregation_rechecks_frozen_raw_telemetry_after_judgments(self):
        fixture = self.fixture
        reading_prepare.prepare(fixture.input, fixture.directories, fixture.prepared, self.root)
        fixture.complete_reviews()
        path = self.directory / "memory-samples.jsonl"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            reading_summary.summarize(fixture.prepared, [fixture.review], fixture.output, self.root)
        self.assertFalse(fixture.output.exists())


class DevAdapterTests(unittest.TestCase):
    def test_dev18_preparer_and_aggregator_with_v5_raw_evidence(self):
        fixture = dev_fixture.ReviewSummaryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        directories = [Path(value["directory"]) for value in fixture.key["systems"]]
        summary = upgrade(directories[1], fixture.sources, fixture.manifest, fixture.root)
        output = fixture.directory / "prepared-v5"
        audit = fixture.content / "audit.json"
        put(audit, {"version": "linguistic-dev-independent-source-audit-v1", "modelOutputsViewed": False,
            "humanReviewed": False, "inputs": {fixture.source_path.relative_to(fixture.root).as_posix(): fixture.manifest["inputSha256"]}})
        argv = ["prepare", "--input", str(fixture.source_path), "--source-audit", str(audit),
                "--output", str(output), "--results", *map(str, directories)]
        with patch.object(dev_prepare, "ROOT", fixture.root), patch.object(dev_prepare, "read_screen",
                return_value=(fixture.sources, fixture.manifest)), patch.object(dev_prepare, "assert_input_unchanged"), patch("sys.argv", argv):
            dev_prepare.main()
        manifest = get(output / "manifest.json")
        self.assertEqual(manifest["reviewEvidenceVersion"], "v5")
        reviews = []
        for index, source in enumerate(fixture.sources):
            name = f"packet-{index // 6 + 1}.jsonl"
            packet = get(output / name, True)[index % 6]
            row = copy.deepcopy(fixture.reviews[index])
            row["preparedManifestSha256"] = evidence.stream_hash(output / "manifest.json")
            row["packetSha256"] = manifest["files"][name]
            for judgment, candidate in zip(row["judgments"], packet["candidates"]):
                judgment["targetSha256"] = candidate["targetSha256"]
            reviews.append(row)
        review_path = fixture.directory / "reviews-v5.jsonl"
        put(review_path, reviews, True)
        result = dev_summary.summarize(output, [review_path], fixture.output, fixture.root)
        self.assertEqual(result["judgmentCount"], 36)
        self.assertEqual(result["systems"][1]["runMetadata"], summary)
        self.assertEqual(result["systems"][1]["summarySchema"], evidence.VERSION)
        self.assertFalse(result["automaticGateCreated"])
        self.assertIsNone(result["promotionDecision"])


if __name__ == "__main__":
    unittest.main()
