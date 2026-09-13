"""Validate recorded v3 run completion, without inference or quality scoring."""
from __future__ import annotations

import re


VERSIONS = {"translategemma-large-screen-v3", "hymt30-development-screen-v3"}


def require(value, message):
    if not value:
        raise ValueError(message)


def is_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def check_v3_evidence(summary, predictions_sha):
    """Check completion metadata; no claim to independently remeasure resources."""
    version = summary.get("version")
    require(version in VERSIONS, "unsupported_v3_producer_version")
    codes = summary.get("codeHashes")
    require(isinstance(codes, dict) and bool(codes)
            and all(isinstance(path, str) and path.strip() and is_sha(value) for path, value in codes.items()),
            "v3_code_identity_missing")
    monitor = summary.get("memoryMonitoring")
    require(summary.get("status") == "completed" and summary.get("pagingExperiment") is True
            and summary.get("childProcessStopped") is True
            and type(summary.get("ramBudgetGiB")) is int and summary["ramBudgetGiB"] > 0
            and isinstance(summary.get("suspendedCreation"), dict) and bool(summary["suspendedCreation"])
            and isinstance(summary.get("childWorkingSetLimit"), dict) and bool(summary["childWorkingSetLimit"])
            and isinstance(monitor, dict)
            and all(key in monitor and monitor[key] is None for key in
                    ("abortReason", "monitorError", "ownedChildKillError"))
            and all(type(monitor.get(key)) is int and monitor[key] > 0 for key in
                    ("observations", "recordedSamples"))
            and not any(summary.get(key) for key in ("memoryOrTimeGuardAborted", "finalIntegrityError",
                    "cleanupError", "monitorCleanupError")), "v3_completion_or_memory_guard_evidence_differs")
    require(is_sha(summary.get("installationManifestSha256")), "v3_installation_identity_missing")
    if version == "translategemma-large-screen-v3":
        require(summary.get("integrityVerified") is True and is_sha(summary.get("modelSha256"))
                and is_sha(summary.get("memorySamplesSha256"))
                and summary.get("predictionsSha256") == predictions_sha, "v3_translategemma_identity_differs")
    else:
        model, artifacts = summary.get("model"), summary.get("artifactHashes")
        require(summary.get("modelLoaded") is True and summary.get("profile") == "contextual"
                and isinstance(model, dict) and is_sha(model.get("sha256"))
                and isinstance(model.get("name"), str) and bool(model["name"].strip())
                and type(model.get("size")) is int and model["size"] > 0
                and all(isinstance(summary.get(key), str) and bool(summary[key].strip()) for key in
                        ("modelRevision", "runtimeRevision"))
                and isinstance(artifacts, dict) and artifacts.get("predictions.jsonl") == predictions_sha
                and is_sha(artifacts.get("memory-samples.jsonl")), "v3_hymt30_identity_differs")
