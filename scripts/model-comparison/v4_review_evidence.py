"""V4 recorded-run evidence checks; no inference, rescoring, or resource remeasurement.

Raw TG responses are first hash-bound at review preparation, because that producer
does not record their completion-time hashes. Hy responses and memory logs also
must match producer-recorded hashes. This is not a full native-server audit.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import v3_review_evidence as v3

VERSIONS = {"translategemma-large-screen-v4", "hymt30-development-screen-v4"}
TIME_LIMITS = {
    "translategemma-large-screen-v4": {"startup": 1800, "request": 7200, "total": 86400},
    "hymt30-development-screen-v4": {"startup": 1800, "request": 1800, "total": 43200},
}
NORMALIZATION = {
    "parameter": "top_k", "requestedValue": -1, "expectedReportedValue": 0,
    "meaning": "disabled", "runtimeRevision": "72797e89198ab564fd0e6baa54ab196e8dd1d884",
    "otherSettingsUnchanged": True,
    "sourceEvidence": [
        {"path": ".training/comparisons/hy-mt2-30b-a3b-q4/publisher-metadata/top-k-normalization-source/tools_server_server-schema.cpp",
         "sha256": "c3d2899d2a078adae6c4971b3da825bad1aab9cda6b2af652e41e6aef1665274", "lines": [89, 90, 91, 594]},
        {"path": ".training/comparisons/hy-mt2-30b-a3b-q4/publisher-metadata/top-k-normalization-source/src_llama-sampler.cpp",
         "sha256": "30b739d8d2bab2c57d5bdb9d082cb115f9942d9668871501506360b55c3360a0", "lines": [1519, 1520, 1522, 1523]},
    ],
}
HY_SAMPLING = {
    "temperature": 0.7, "top_p": 1.0, "top_k": -1, "repeat_penalty": 1.0,
    "repeat_last_n": 8192, "min_p": 0.0, "seed": 42,
    "samplers": ["penalties", "temperature", "top_k", "top_p"],
    "n_predict": 4096, "stop": [], "ignore_eos": False,
    "cache_prompt": False, "stream": False, "return_tokens": True,
    "n_keep": 0, "id_slot": 0, "presence_penalty": 0.0, "frequency_penalty": 0.0,
    "dry_multiplier": 0.0, "mirostat": 0, "dynatemp_range": 0.0,
    "typical_p": 1.0, "xtc_probability": 0.0, "top_n_sigma": -1.0,
}
HY_REPORTED_KEYS = {"temperature", "top_p", "top_k", "repeat_penalty", "repeat_last_n", "min_p", "seed",
    "samplers", "n_predict", "ignore_eos", "presence_penalty", "frequency_penalty", "dry_multiplier",
    "mirostat", "dynatemp_range", "typical_p", "xtc_probability", "stop", "top_n_sigma"}
require = v3.require


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def stream_hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def check_v4_evidence(summary, predictions_sha):
    version = summary.get("version")
    require(version in VERSIONS, "unsupported_v4_producer_version")
    # Explicitly adapt only the version field to the unchanged v3 guard contract.
    v3.check_v3_evidence({**summary, "version": version[:-1] + "3"}, predictions_sha)
    require(canonical(summary.get("timeLimitsSeconds")) == canonical(TIME_LIMITS[version]),
            "v4_time_limits_differ")
    require(type(summary.get("ramBudgetGiB")) is int and summary["ramBudgetGiB"] in (8, 9, 10, 11, 12),
            "v4_ram_budget_differs")
    if version == "hymt30-development-screen-v4":
        require(canonical(summary.get("samplingNormalization")) == canonical(NORMALIZATION)
                and summary.get("runtimeRevision") == NORMALIZATION["runtimeRevision"]
                and canonical(summary.get("sampling")) == canonical(HY_SAMPLING), "v4_top_k_normalization_differs")
    else:
        require(summary.get("modelSize") == "27b", "v4_translategemma_model_size_differs")


def _register(path, evidence, expected=None):
    path = Path(path)
    require(".." not in path.parts, "v4_parent_path_not_allowed")
    path = path if path.is_absolute() else evidence.root / path
    require(not any(p.is_symlink() for p in (path, *path.parents)), "v4_symlink_not_allowed")
    path = path.resolve()
    require(path.is_relative_to(evidence.root.resolve()) and path.is_file(), "v4_artifact_outside_workspace_or_missing")
    digest = stream_hash(path)
    require(expected is None or (v3.is_sha(expected) and digest == expected), "v4_artifact_hash_mismatch")
    require(path not in evidence.files or evidence.files[path] == digest, "v4_artifact_changed_during_read")
    evidence.files[path] = digest
    if not hasattr(evidence, "v4_artifact_files"):
        evidence.v4_artifact_files = {}
    evidence.v4_artifact_files[str(path)] = digest
    return path, digest


def check_artifacts(directory, summary, predictions, evidence):
    """Bind source evidence, memory log and raw output; use caller's tracked reads."""
    version = summary.get("version")
    if version not in VERSIONS:
        return
    directory = Path(directory).resolve()
    hy = version == "hymt30-development-screen-v4"
    expected_memory = summary["artifactHashes"]["memory-samples.jsonl"] if hy else summary["memorySamplesSha256"]
    _register(directory / "memory-samples.jsonl", evidence, expected_memory)
    if hy:
        for source in NORMALIZATION["sourceEvidence"]:
            _register(source["path"], evidence, source["sha256"])
    for row in predictions:
        rid = row.get("id")
        require(isinstance(rid, str) and rid and all(c.isalnum() or c in "-_" for c in rid), "v4_invalid_row_id")
        name = rid + (".raw-response.json" if hy else "-response.json")
        expected = None
        if hy:
            expected = row.get("rawResponseSha256")
            require(row.get("rawResponseFile") == name and v3.is_sha(expected)
                    and summary["artifactHashes"].get(name) == expected, "v4_raw_response_identity_differs")
        path, digest = _register(directory / name, evidence, expected)
        response = evidence.read(path, digest)
        actual = row.get("actualGenerationSettings")
        native = response.get("generation_settings") if isinstance(response, dict) else None
        require(isinstance(actual, dict) and isinstance(native, dict)
                and response.get("content") == row.get("translation"), "v4_raw_output_differs")
        # Hy stores the requested-key subset; TG stores the full native object.
        require((all(k in native and canonical(native[k]) == canonical(v) for k, v in actual.items()) if hy
                 else canonical(native) == canonical(actual)), "v4_raw_generation_settings_differ")
        require(isinstance(response.get("tokens"), list)
                and all(type(token) is int for token in response["tokens"])
                and type(response.get("tokens_predicted")) is int
                and len(response["tokens"]) == response["tokens_predicted"]
                and response.get("stop_type") in ("eos", "limit")
                and type(response.get("truncated")) is bool
                and response.get("tokens") == row.get("outputTokenIds")
                and response.get("tokens_predicted") == row.get("generatedTokens")
                and response.get("stop_type") == row.get("stopType")
                and response.get("truncated") == row.get("truncated"), "v4_raw_token_or_stop_evidence_differs")
        if hy:
            expected_normalization = {**NORMALIZATION, "actualReportedValue": 0, "applied": True}
            require(canonical(row.get("samplingNormalization")) == canonical(expected_normalization)
                    and type(actual.get("top_k")) is int and actual["top_k"] == 0,
                    "v4_prediction_top_k_normalization_differs")
            require(HY_REPORTED_KEYS <= set(actual) <= set(HY_SAMPLING), "v4_hy_reported_settings_missing")
            for key in actual:
                wanted, value = (0 if key == "top_k" else HY_SAMPLING[key]), actual[key]
                equal = math.isclose(value, wanted, rel_tol=1e-6, abs_tol=1e-8) if (
                    type(value) in (float, int) and type(wanted) in (float, int)) else canonical(value) == canonical(wanted)
                require(equal, "v4_hy_other_sampling_setting_differs")


def artifact_inventory_sha(evidence):
    # Opaque digest in the public prepared manifest keeps model paths hidden.
    return hashlib.sha256(canonical(getattr(evidence, "v4_artifact_files", {}))).hexdigest()
