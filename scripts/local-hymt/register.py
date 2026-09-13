"""Register an explicitly selected, fully reviewed local Hy-MT2 profile.

Registration preserves immutable evidence and never selects an application
provider, edits environment settings, opens a database, or performs inference.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sys

import deployment as dep


def _relative(root, value):
    root, value = Path(root).absolute(), Path(value)
    path = value if value.is_absolute() else root / value
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        raise dep.DeploymentError("registration_input_outside_project") from None
    dep.relative_name(relative)
    dep.require(relative.startswith(".training/comparisons/"), "registration_evidence_outside_comparison")
    return relative, dep.safe_path(root, relative)


def _revalidate_reviews(root, prepared_path, reviews_path, review_summary):
    """Recompute an existing summary using its complete source-grounded reviews.

    The existing aggregator rechecks all 24 x 4 raw choices, judgments, source
    hashes, producer metadata and source snapshots. It cannot invent a gate or
    rerun a model. The requested summary must already exist before this call.
    """
    directory = str(dep.ROOT / "scripts/model-comparison")
    if directory not in sys.path:
        sys.path.insert(0, directory)
    import summarize_quality_reviews as review
    records = review_summary.get("reviews")
    dep.require(isinstance(records, list) and records, "review_source_files_missing")
    paths = []
    for record in records:
        dep.require(isinstance(record, dict) and dep.valid_sha(record.get("sha256")), "invalid_review_source_record")
        _relative_name, path = _relative(root, record.get("path", ""))
        dep.require(path.suffix == ".jsonl" and path not in paths, "invalid_review_source_path")
        paths.append(path)
    before = reviews_path.read_bytes()
    result = review.summarize(prepared_path, paths, reviews_path, root)
    dep.require(Path(result) == reviews_path and reviews_path.read_bytes() == before, "completed_review_summary_changed")


def _write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _track_review_inputs(tracker, prepared, reviews):
    """Retain every input fingerprint across revalidation and publication."""
    inputs = dep.track_python_runtime(tracker, prepared)
    records = reviews.get("reviews")
    dep.require(isinstance(records, list) and 1 <= len(records) <= 24, "review_source_files_missing")
    review_files = {}
    for record in records:
        dep.require(isinstance(record, dict) and isinstance(record.get("path"), str)
                    and dep.valid_sha(record.get("sha256")), "invalid_review_source_record")
        relative, path = _relative(tracker.root, record["path"])
        dep.require(path.suffix == ".jsonl" and relative.casefold() not in review_files, "invalid_review_source_path")
        review_files[relative.casefold()] = (relative, record["sha256"])
    for relative, expected in inputs.items():
        tracker.file(relative, expected=expected)
    for relative, expected in review_files.values():
        tracker.file(relative, expected=expected)
    tracker.assert_unchanged()


def register(profile, prepared, reviews, selection, registration_id, root=dep.ROOT):
    dep.require(profile in ("raw", "contextual") and isinstance(registration_id, str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", registration_id), "invalid_registration_request")
    root = Path(root).absolute()
    prepared_name, prepared_path = _relative(root, prepared)
    dep.require(prepared_path.is_dir(), "prepared_review_directory_missing")
    reviews_name, reviews_path = _relative(root, reviews)
    selection_name, _selection_path = _relative(root, selection)
    dep.require(reviews_path.suffix == ".json" and selection_name.endswith(".json"), "registration_json_required")
    prepared_manifest_name = prepared_name + "/manifest.json"
    metrics_name = prepared_name + "/comparison-metrics.json"
    evidence_names = [prepared_manifest_name, metrics_name, reviews_name, selection_name]
    dep.require(len(set(name.casefold() for name in evidence_names)) == 4, "duplicate_registration_evidence")
    tracker = dep.FileTracker(root)
    code_files = [tracker.file(name) for name in dep.CODE_PATHS]
    evidence = {name: dep.parse(tracker.file(name, content=True)) for name in evidence_names}
    dep.check_review_metadata(evidence[prepared_manifest_name], evidence[metrics_name], evidence[reviews_name],
                              tracker.hashes[prepared_manifest_name], tracker.hashes[metrics_name])
    dep.check_selection(evidence[selection_name], profile, tracker.hashes[prepared_manifest_name], tracker.hashes[reviews_name])
    _track_review_inputs(tracker, evidence[prepared_manifest_name], evidence[reviews_name])
    _revalidate_reviews(root, prepared_path, reviews_path, evidence[reviews_name])
    tracker.assert_unchanged()
    registration = f".translation/hymt/registrations/{registration_id}"
    destination = dep.safe_path(root, registration, exists=False)
    pending = dep.safe_path(root, registration + ".pending", exists=False)
    dep.require(not destination.exists() and not pending.exists(), "preserve_existing_registration")
    active = dep.safe_path(root, dep.ACTIVE_PATH, exists=False)
    dep.require(not active.exists() or active.is_file() and active.stat().st_size <= dep.MAX_METADATA,
                "existing_active_registration_invalid")
    active_raw = active.read_bytes() if active.exists() else None
    if active_raw is not None:
        # An existing activation must already have its identical immutable copy.
        old = dep.parse(active_raw)
        old_id = old.get("registrationId") if isinstance(old, dict) else None
        dep.require(isinstance(old_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", old_id),
                    "existing_active_registration_invalid")
        old_copy = dep.safe_path(root, f".translation/hymt/registrations/{old_id}/manifest.json")
        dep.require(old_copy.is_file() and old_copy.stat().st_size <= dep.MAX_METADATA, "existing_active_registration_invalid")
        dep.require(old_copy.read_bytes() == active_raw, "existing_active_registration_not_preserved")
    # Read sources once for their hashes. The final checks below bind the bytes
    # used to build registration to the inputs that passed complete review.
    catalog_raw = tracker.file(dep.CATALOG_SOURCE, expected=dep.CATALOG_SHA256, content=True)
    dep.read_terms(catalog_raw)
    execution = dep.execution_contract()
    model = tracker.file(f"{dep.MODEL_DIR}/{dep.MODEL_FILE}", expected=dep.MODEL_SHA256, size=dep.MODEL_SIZE)
    runtime_files = dep.runtime_inventory(root, tracker)
    for entry in runtime_files:
        tracker.entry(entry)
    tracker.inventory(f"{dep.MODEL_DIR}/runtime", [entry["path"] for entry in runtime_files])
    tracker.file(dep.PYTHON_PATH)
    manifest = {"schemaVersion": 1, "provider": "hymt", "model": dep.MODEL_NAME, "modelHash": dep.MODEL_SHA256,
                "runtimeVersion": "hymt-local-v1:" + dep.sha(dep.canonical(execution)),
                "installedAt": datetime.now(timezone.utc).isoformat(), "registrationId": registration_id,
                "pythonPath": dep.PYTHON_PATH, "modelPath": dep.MODEL_DIR, "profile": profile,
                "modelFiles": [model], "runtimeFiles": runtime_files, "codeFiles": code_files,
                "catalog": {"path": registration + "/catalog.json", "size": len(catalog_raw), "sha256": dep.sha(catalog_raw)},
                "evidenceFiles": [tracker.file(name) for name in evidence_names], "execution": execution}
    manifest_raw = dep.canonical(manifest) + b"\n"
    tracker.assert_unchanged()
    destination.parent.mkdir(parents=True, exist_ok=True)
    dep.safe_path(root, registration, exists=False)
    pending.mkdir(exist_ok=False)
    _write_new(pending / "catalog.json", catalog_raw)
    _write_new(pending / "manifest.json", manifest_raw)
    tracker.assert_unchanged()
    dep.require(not destination.exists(), "registration_appeared_concurrently")
    pending.rename(destination)
    # Activation is the only mutable pointer. Preserve the previous registration,
    # and refuse a concurrent activation rather than silently replacing it.
    activation_pending = dep.safe_path(root, ".translation/hymt/manifest.json.pending", exists=False)
    _write_new(activation_pending, manifest_raw)
    tracker.assert_unchanged()
    dep.require((active.read_bytes() if active.exists() else None) == active_raw, "active_registration_changed_concurrently")
    dep.safe_path(root, dep.ACTIVE_PATH, exists=False)
    os.replace(activation_pending, active)
    dep.require(active.read_bytes() == (destination / "manifest.json").read_bytes() == manifest_raw,
                "active_and_immutable_registration_differ")
    return active


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("raw", "contextual"), required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--registration-id", required=True)
    args = parser.parse_args(argv)
    path = register(args.profile, args.prepared, args.reviews, args.selection, args.registration_id)
    print(dep.canonical({"event": "hymt-profile-registered", "manifest": str(path),
                         "manifestSha256": dep.sha(path.read_bytes()), "providerChanged": False,
                         "inferencePerformed": False}).decode("utf-8"))


if __name__ == "__main__":
    main()
