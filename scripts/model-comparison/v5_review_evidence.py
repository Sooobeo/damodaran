"""Strict recorded TG27-v5 evidence, plus unchanged legacy v4 validation.

No producer code is imported/executed, model weight opened, or process queried.
The v4 projection is a private in-memory validation view only; original metadata
and hashes are never relabelled, rewritten, or returned as a v4 run.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

import v4_review_evidence as legacy

VERSION = "translategemma-large-screen-v5"
TELEMETRY = "owned-phase-resource-v1"
BASE_V4_SHA256 = "6277e73c9ee9f8d35c79fa197028eb8a5345a77078779802d81405845c0a7a2d"
GIB = 1024 ** 3
PRIORITY = 0x4000
PHASES = {"startup", "runtime-validation", "prompt-preflight", "generation-request",
          "response-validation", "request-failed", "final-integrity", "cleanup"}
RECORD_FIELDS = {"seconds", "telemetryVersion", "observedAtUTC", "previousObservationUTC", "phase", "rowId",
    "requestStartUTC", "requestElapsedSeconds", "requestActive", "heartbeatGapSeconds",
    "maximumHeartbeatGapSincePreviousRecordSeconds", "recordGapSeconds", "requestTokenProgress",
    "requestTokenProgressAvailable", "load", "totalPhysical", "availablePhysical", "totalPageFile", "availablePageFile", "child"}
CHILD_FIELDS = {"pid", "creationTicks", "workingSetBytes", "peakWorkingSetBytes", "privateBytes", "pageFaultCount",
    "cpuKernelTicks", "cpuUserTicks", "cpuKernelSeconds", "cpuUserSeconds", "cpuTotalSeconds", "priorityClass", "belowNormalVerified"}
# Explicit reviewed implementation bytes, not arbitrary Python from run output.
SUPPORTED_CODE = {
    "scripts/model-comparison/run_translategemma_large_v5.py": "946119b19e982e311680491cc6cd4b4ab9109336873ae4ffac27fecf0d2c6d54",
    "scripts/model-comparison/working_set_limit.py": "4f8a45772a0f3d3f63274c7309bc7e673446eebb47a4a3e71dc558b95a4cbcdb",
    "scripts/model-comparison/suspended_process_owner.py": "f01ca8a2fd5a75e501c343d2632b2f534b4e5f0397fda502449c69c5d363dfcd",
    "scripts/local-hymt/process_owner.py": "9a00d809466e304359fb47c374c8fc2464ce31cdbc9557fbc9eee089bfe9d8b2",
}
TG_SETTINGS = {"n_predict": 768, "temperature": 0.0, "seed": 20260910,
    "repeat_penalty": 1.0, "repeat_last_n": 0, "top_k": 0, "top_p": 1.0, "min_p": 0.0,
    "samplers": ["temperature"], "stop": [], "ignore_eos": False, "cache_prompt": False,
    "stream": False, "return_tokens": True, "n_keep": 0, "id_slot": 0,
    "presence_penalty": 0.0, "frequency_penalty": 0.0, "dry_multiplier": 0.0,
    "mirostat": 0, "dynatemp_range": 0.0, "typical_p": 1.0, "xtc_probability": 0.0,
    "top_n_sigma": -1.0, "logit_bias": []}
require, canonical, stream_hash = legacy.require, legacy.canonical, legacy.stream_hash
is_sha = legacy.v3.is_sha


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def near(a, b, tolerance=0.002):
    return number(a) and number(b) and abs(a - b) <= tolerance


def utc(value):
    require(isinstance(value, str), "v5_utc_missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("v5_utc_invalid") from None
    require(parsed.utcoffset() == timezone.utc.utcoffset(parsed), "v5_timestamp_not_utc")
    return parsed


def owned(value, expected=None):
    require(isinstance(value, dict) and all(type(value.get(k)) is int and value[k] > 0
            for k in ("pid", "creationTicks")), "v5_owned_identity_missing")
    identity = {k: value[k] for k in ("pid", "creationTicks")}
    require(expected is None or identity == expected, "v5_owned_identity_differs")
    return identity


def v4_projection(summary):
    require(summary.get("version") == VERSION, "v5_projection_requires_original_v5")
    return {**summary, "version": "translategemma-large-screen-v4"}


def check_v5_evidence(summary, predictions_sha):
    require(isinstance(summary, dict) and summary.get("version") == VERSION, "unsupported_v5_producer_version")
    legacy.check_v4_evidence(v4_projection(summary), predictions_sha)
    require(summary.get("telemetryVersion") == TELEMETRY and summary.get("copiedFromV4Sha256") == BASE_V4_SHA256
            and summary.get("childCreationCleanupUnconfirmed") is False
            and not summary.get("ownershipErrorCode")
            and summary.get("requiredNativePriorityClass") == PRIORITY
            and summary.get("nativePrioritySetAfterSpawnByRunner") is False
            and summary.get("requestTokenProgressAvailable") is False
            and summary.get("rawNativeLogsRetained") is False, "v5_telemetry_contract_missing")
    require(summary.get("runtimeTokenizationAndEogValidated") is True and summary.get("threads") == 4
            and type(summary.get("threads")) is int and summary.get("profile") == "source-only"
            and summary.get("contextUsed") is False and canonical(summary.get("settings")) == canonical(TG_SETTINGS),
            "v5_generation_contract_differs")
    require(type(summary.get("expectedCount")) is int and summary["expectedCount"] in (6, 18)
            and all(type(summary.get(k)) is int and summary[k] == summary["expectedCount"] for k in ("count", "recordedCount")),
            "v5_full_six_or_eighteen_required")
    ids = summary.get("input", {}).get("selectedIds")
    require(isinstance(ids, list) and len(ids) == summary["expectedCount"] and len(set(ids)) == len(ids)
            and all(isinstance(rid, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", rid) for rid in ids),
            "v5_input_ids_invalid")
    require(utc(summary.get("finishedAt")) >= utc(summary.get("startedAt")), "v5_run_time_order")
    monitor = summary["memoryMonitoring"]
    identity = owned(monitor.get("ownedIdentity"))
    receipt = summary["childWorkingSetLimit"]
    owned(receipt, identity)
    creation = summary["suspendedCreation"]
    require(creation.get("version") == "comparison-suspended-owned-spawn-v1"
            and creation.get("pid") == identity["pid"] and creation.get("atomicJobAssignment") is True
            and creation.get("createSuspended") is True and creation.get("limitAppliedBeforeResume") is True
            and type(creation.get("resumePreviousCount")) is int and creation["resumePreviousCount"] == 1
            and canonical(creation.get("workingSetLimit")) == canonical(receipt), "v5_suspended_ownership_differs")
    budget = summary["ramBudgetGiB"] * GIB
    policy = summary.get("memoryPolicy")
    require(isinstance(policy, dict) and policy.get("ramBudgetGiB") == summary["ramBudgetGiB"]
            and policy.get("requiredAvailablePhysicalBytes") == budget + 3 * GIB
            and policy.get("requiredAvailableCommitBytes") == 4261412864
            and policy.get("maximumWorkingSetBytes") == budget
            and policy.get("requestedHardMaximumWorkingSetBytes") == budget - 64 * 1024 ** 2
            and policy.get("workingSetReservationBytes") == 64 * 1024 ** 2
            and policy.get("minimumWorkingSetBytes") == 1024 ** 2
            and policy.get("weightRepackingEnabled") is False, "v5_memory_policy_differs")
    for name in ("memoryBefore", "memoryImmediatelyBeforeStartup"):
        state = summary.get(name)
        require(isinstance(state, dict) and type(state.get("availablePhysical")) is int
                and state["availablePhysical"] >= budget + 3 * GIB
                and type(state.get("availablePageFile")) is int and state["availablePageFile"] >= 4261412864,
                "v5_startup_headroom_missing")
    limits = receipt.get("after", {})
    require(canonical(limits) == canonical({"minimumBytes": 1024 ** 2, "maximumBytes": budget - 64 * 1024 ** 2, "flags": 6,
            "hardMaximumEnabled": True, "hardMinimumDisabled": True}),
            "v5_hard_working_set_readback_differs")
    require(monitor.get("telemetryVersion") == TELEMETRY and monitor.get("requiredPriorityClass") == PRIORITY
            and monitor.get("lastRequestDeadlineExceededAtEnd") is False
            and monitor.get("observeIntervalSeconds") == 0.5 and monitor.get("recordIntervalSeconds") == 5
            and monitor.get("minimumAvailablePhysicalBytes") == 512 * 1024 ** 2
            and monitor.get("minimumAvailableCommitBytes") == 512 * 1024 ** 2
            and monitor.get("lowDurationSeconds") == 3 and monitor.get("independentLowResourceTimers") is True
            and monitor.get("maximumObservedWorkingSetBytes") == budget
            and monitor.get("sampleFile") == "memory-samples.jsonl"
            and monitor.get("requestTokenProgressAvailable") is False and monitor.get("rawNativeLogsRetained") is False,
            "v5_monitor_guard_contract_differs")
    require(monitor.get("lastPhase") == "final-integrity" and monitor.get("lastRowId") == ids[-1]
            and number(monitor.get("lastRequestElapsedSeconds")) and monitor["lastRequestElapsedSeconds"] < 7200
            and number(monitor.get("maximumHeartbeatGapSeconds")), "v5_monitor_final_state_missing")
    utc(monitor.get("lastRequestStartUTC"))
    utc(monitor.get("lastObservationUTC"))
    exit_record = monitor.get("ownedChildExitObserved")
    owned(exit_record, identity)
    require(type(exit_record.get("exitCode")) is int and number(exit_record.get("seconds")), "v5_owned_exit_missing")
    require(utc(exit_record.get("observedAtUTC")) <= utc(summary["finishedAt"]), "v5_exit_after_summary")
    inventory = summary.get("responseFilesSha256")
    require(isinstance(inventory, dict) and set(inventory) == {rid + "-response.json" for rid in ids}
            and all(is_sha(digest) for digest in inventory.values()), "v5_raw_inventory_missing_or_extra")


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "v5_duplicate_json_key")
        result[key] = value
    return result


def memory_rows(path):
    # A legitimate day of 5-second records can exceed the legacy 16 MiB reader
    # cap. Stream bounded lines instead of loading a full memory log into RAM.
    require(path.stat().st_size <= 128 * 1024 ** 2, "v5_memory_log_too_large")
    with path.open("rb") as stream:
        for line in stream:
            require(0 < len(line) <= 65536, "v5_memory_line_too_large")
            require(bool(line.strip()), "v5_blank_memory_record")
            yield json.loads(line.decode("utf-8"), object_pairs_hook=_strict_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("v5_nonfinite_json")))


def check_memory_log(path, summary, ids):
    monitor, identity = summary["memoryMonitoring"], summary["memoryMonitoring"]["ownedIdentity"]
    budget = summary["ramBudgetGiB"] * GIB
    previous, first, count, max_gap = None, None, 0, 0.0
    request_rows, response_rows, phase_order, last_elapsed, request_origins = {}, set(), [], {}, {}
    recorded_extrema = {"minimumAvailablePhysical": None, "minimumAvailableCommit": None,
                        "maximumChildWorkingSet": 0, "maximumChildPeakWorkingSet": 0, "maximumChildPrivateBytes": 0}
    for sample in memory_rows(path):
        require(isinstance(sample, dict) and set(sample) == RECORD_FIELDS and sample.get("telemetryVersion") == TELEMETRY,
                "v5_memory_record_contract_missing")
        now, phase, rid = sample.get("seconds"), sample.get("phase"), sample.get("rowId")
        require(number(now) and now < 86400 and phase in PHASES and phase not in ("request-failed", "cleanup")
                and (rid is None or rid in ids), "v5_memory_phase_or_time_invalid")
        utc(sample.get("observedAtUTC"))
        require(sample.get("requestTokenProgressAvailable") is False and "requestTokenProgress" in sample
                and sample["requestTokenProgress"] is None, "v5_token_progress_invented_or_missing")
        child = sample.get("child")
        owned(child, identity)
        require(set(child) == CHILD_FIELDS, "v5_child_fields_missing_or_extra")
        require(type(child.get("priorityClass")) is int and child["priorityClass"] == PRIORITY
                and child.get("belowNormalVerified") is True, "v5_native_priority_differs")
        for field in ("workingSetBytes", "peakWorkingSetBytes", "privateBytes", "pageFaultCount", "cpuKernelTicks", "cpuUserTicks"):
            require(type(child.get(field)) is int and child[field] >= 0, "v5_numeric_child_sample_missing")
        require(child["workingSetBytes"] <= child["peakWorkingSetBytes"] <= budget, "v5_recorded_working_set_exceeded")
        require(near(child.get("cpuKernelSeconds"), child["cpuKernelTicks"] / 10000000, 1e-7)
                and near(child.get("cpuUserSeconds"), child["cpuUserTicks"] / 10000000, 1e-7)
                and near(child.get("cpuTotalSeconds"), (child["cpuKernelTicks"] + child["cpuUserTicks"]) / 10000000, 1e-7),
                "v5_cpu_units_or_total_differs")
        for field in ("load", "totalPhysical", "availablePhysical", "totalPageFile", "availablePageFile"):
            require(type(sample.get(field)) is int and sample[field] >= 0, "v5_system_memory_missing")
        for key, value, choose in (("minimumAvailablePhysical", sample["availablePhysical"], min),
            ("minimumAvailableCommit", sample["availablePageFile"], min),
            ("maximumChildWorkingSet", child["workingSetBytes"], max),
            ("maximumChildPeakWorkingSet", child["peakWorkingSetBytes"], max),
            ("maximumChildPrivateBytes", child["privateBytes"], max)):
            recorded_extrema[key] = value if recorded_extrema[key] is None else choose(recorded_extrema[key], value)
        gap, window = sample.get("heartbeatGapSeconds"), sample.get("maximumHeartbeatGapSincePreviousRecordSeconds")
        require(number(window), "v5_heartbeat_window_missing")
        if previous is None:
            require(phase == "startup" and rid is None and sample.get("recordGapSeconds") is None
                    and sample.get("previousObservationUTC") is None and gap is None and window == 0,
                    "v5_initial_telemetry_missing")
            first = sample
        else:
            delta = now - previous["seconds"]
            require(delta >= 0 and near(sample.get("recordGapSeconds"), delta)
                    and number(gap) and gap <= window + 0.002 and window <= delta + 0.002,
                    "v5_heartbeat_interval_inconsistent")
            utc(sample.get("previousObservationUTC"))
            require(child["cpuKernelTicks"] >= previous["child"]["cpuKernelTicks"]
                    and child["cpuUserTicks"] >= previous["child"]["cpuUserTicks"], "v5_cpu_time_regressed")
        max_gap = max(max_gap, window)
        if not phase_order or phase_order[-1] != phase:
            phase_order.append(phase)
        if phase in ("generation-request", "response-validation"):
            require(rid in ids and isinstance(sample.get("requestStartUTC"), str)
                    and number(sample.get("requestElapsedSeconds")) and sample["requestElapsedSeconds"] < 7200,
                    "v5_request_telemetry_missing")
            utc(sample["requestStartUTC"])
            require(sample.get("requestActive") is (phase == "generation-request"), "v5_request_active_differs")
            if phase == "generation-request":
                require(rid not in response_rows, "v5_request_reappeared_after_response")
                if rid not in request_rows:
                    require(rid == ids[len(request_rows)], "v5_request_order_differs")
                    request_rows[rid] = sample["requestStartUTC"]
                    request_origins[rid] = now - sample["requestElapsedSeconds"]
                require(request_rows[rid] == sample["requestStartUTC"], "v5_request_start_changed")
                require(near(now - sample["requestElapsedSeconds"], request_origins[rid]), "v5_active_request_elapsed_inconsistent")
            else:
                require(rid in request_rows and request_rows[rid] == sample["requestStartUTC"], "v5_response_without_request")
                response_rows.add(rid)
            require(sample["requestElapsedSeconds"] >= last_elapsed.get(rid, 0), "v5_request_elapsed_regressed")
            last_elapsed[rid] = sample["requestElapsedSeconds"]
        elif phase in ("startup", "runtime-validation", "prompt-preflight"):
            require(rid is None and sample["requestStartUTC"] is None and sample["requestElapsedSeconds"] is None
                    and sample["requestActive"] is False, "v5_pre_request_state_differs")
        else:
            require(sample["requestActive"] is False and rid == ids[-1]
                    and sample["requestStartUTC"] == request_rows.get(rid)
                    and near(sample["requestElapsedSeconds"], last_elapsed.get(rid)), "v5_final_request_state_differs")
        count += 1
        previous = sample
    require(count > 0 and count == monitor["recordedSamples"] and monitor["observations"] >= count,
            "v5_memory_record_count_differs")
    require(set(request_rows) == set(ids) == response_rows and previous["phase"] == "final-integrity"
            and previous["rowId"] == ids[-1] and "runtime-validation" in phase_order,
            "v5_request_or_final_phase_coverage_missing")
    require(near(monitor["maximumHeartbeatGapSeconds"], max_gap)
            and monitor.get("firstCpuTicks") == first["child"]["cpuKernelTicks"] + first["child"]["cpuUserTicks"]
            and type(monitor.get("lastCpuTicks")) is int
            and monitor["lastCpuTicks"] >= previous["child"]["cpuKernelTicks"] + previous["child"]["cpuUserTicks"]
            and monitor.get("cpuTickUnitSeconds") == 0.0000001, "v5_cpu_or_heartbeat_summary_differs")
    require(monitor["lastRequestStartUTC"] == request_rows[ids[-1]]
            and near(monitor["lastRequestElapsedSeconds"], previous["requestElapsedSeconds"])
            and monitor["ownedChildExitObserved"]["seconds"] + 0.002 >= previous["seconds"],
            "v5_final_request_or_exit_differs")
    for key, value in recorded_extrema.items():
        observed = monitor.get(key)
        require(type(observed) is int and observed >= 0 and (observed <= value if key.startswith("minimum") else observed >= value),
                "v5_summary_extrema_inconsistent")
    require(monitor["maximumChildWorkingSet"] <= budget and monitor["maximumChildPeakWorkingSet"] <= budget,
            "v5_summary_working_set_exceeded")
    return {"records": count, "requestRows": len(request_rows), "maximumHeartbeatGapSeconds": max_gap,
            "unrecordedHalfSecondSamplesReconstructed": False, "hardFaultsMeasured": False}


def check_artifacts(directory, summary, predictions, evidence):
    if summary.get("version") != VERSION:
        return legacy.check_artifacts(directory, summary, predictions, evidence)
    directory = Path(directory).resolve()
    check_v5_evidence(summary, stream_hash(directory / "predictions.jsonl"))
    ids = summary["input"]["selectedIds"]
    require(isinstance(predictions, list) and [row.get("id") for row in predictions] == ids,
            "v5_prediction_coverage_differs")
    # All existing v4 memory/raw-output/token/settings checks run unchanged.
    legacy.check_artifacts(directory, v4_projection(summary), predictions, evidence)
    for row in predictions:
        name = row["id"] + "-response.json"
        expected = summary["responseFilesSha256"][name]
        require(row.get("rawResponseFile") == name and row.get("rawResponseSha256") == expected,
                "v5_raw_prediction_identity_differs")
        legacy._register(directory / name, evidence, expected)
        require(row.get("status") == "completed" and row.get("stopType") == "eos" and row.get("truncated") is False
                and row.get("outputLimitReached") is False and row.get("terminalTokenId") in (1, 106)
                and row.get("terminationClass") == "original_model_eog"
                and all(type(row.get(key)) is int for key in ("generatedTokens", "inputTokens", "terminalTokenId"))
                and 0 < row["generatedTokens"] < 768 and 0 < row["inputTokens"] <= 1280,
                "v5_output_completion_contract_differs")
        require(row["outputTokenIds"] and row["outputTokenIds"][-1] == row["terminalTokenId"]
                and all(token >= 0 and token not in (1, 106, 212) for token in row["outputTokenIds"][:-1])
                and row.get("outputTokenIdsSha256") == hashlib.sha256(canonical(row["outputTokenIds"])).hexdigest(),
                "v5_output_token_hash_or_end_differs")
        actual = row["actualGenerationSettings"]
        require(all(key in actual and canonical(actual[key]) == canonical(TG_SETTINGS[key]) for key in
                    ("n_predict", "temperature", "seed", "repeat_penalty", "top_k", "top_p", "min_p", "samplers", "ignore_eos", "stop", "logit_bias")),
                "v5_native_sampling_differs")
    path = directory / "memory-samples.jsonl"
    check_memory_log(path, summary, ids)
    # Check both before/after streamed parsing, closing a mutation window.
    legacy._register(path, evidence, summary["memorySamplesSha256"])
    for relative, expected in SUPPORTED_CODE.items():
        path = evidence.root / relative
        require(summary["codeHashes"].get(str(path.resolve())) == expected, "v5_supported_producer_code_missing_or_changed")
        legacy._register(path, evidence, expected)


def artifact_inventory_sha(evidence):
    return legacy.artifact_inventory_sha(evidence)
