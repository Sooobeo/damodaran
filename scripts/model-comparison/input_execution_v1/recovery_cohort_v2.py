"""Explicit postvalidation correction for the legitimate local port/origin pair.

The frozen v1 producer and validator remain untouched. Their row/protocol,
failed-run, artifact, shutdown and 38+26 checks are retained. This module alone
corrects comparison of --port together with its exact localhost CORS origin.
It never changes an execution identity, re-generates output or monkeypatches v1.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_execution_v1 import recovery_cohort as v1

e = v1.e
VERSION = v1.VERSION
VALIDATION_VERSION = "input-execution-v1-recovery-cohort-validator-v2"
V1_CODE_SHA256 = "f0aaf20c9cbb6a19e918d7ed9a7fdbb08114168493088ae7917cbb6e88a28b67"
EXECUTION_FREEZE = "content/model-comparison/input-execution-v1/recovery-execution-freeze-v1.json"
EXECUTION_FREEZE_SHA256 = "de5ae401d39bee9a263eb6d4bfe08bd3e3ac97db554ca9f3c6eac6c6066a83ec"
CORRECTION_NOTE = "content/model-comparison/input-execution-v1/RECOVERY_COHORT_VALIDATION_V2.md"
require = v1.require
validate_failed_run = v1.validate_failed_run


def normalize_runtime_command(command):
    """Allow only a valid ephemeral port and its exact loopback HTTP origin."""
    require(isinstance(command, dict) and command.get("shell") is False, "recovery_v2_shell_contract")
    argv = command.get("argv")
    require(isinstance(argv, list) and all(isinstance(value, str) for value in argv), "recovery_v2_argv_contract")
    argv = argv.copy()
    for flag in ("--host", "--port", "--cors-origins", "--api-key"):
        require(argv.count(flag) == 1 and argv.index(flag) + 1 < len(argv), "recovery_v2_unique_network_option")
        require(not any(value.startswith(flag + "=") for value in argv), "recovery_v2_network_option_alias")
    host = argv[argv.index("--host") + 1]
    port = argv[argv.index("--port") + 1]
    origin = argv[argv.index("--cors-origins") + 1]
    require(host == "127.0.0.1", "recovery_v2_loopback_host_required")
    require(re.fullmatch(r"[1-9][0-9]{0,4}", port) is not None and int(port) <= 65535, "recovery_v2_invalid_local_port")
    require(origin == "http://127.0.0.1:" + port, "recovery_v2_origin_must_match_local_port")
    require(argv[argv.index("--api-key") + 1] == "[EPHEMERAL_REDACTED]", "recovery_v2_secret_contract")
    argv[argv.index("--port") + 1] = "<LOCAL_PORT>"
    argv[argv.index("--cors-origins") + 1] = "http://127.0.0.1:<LOCAL_PORT>"
    return {**command, "argv": argv}


def validation_identity(root=ROOT):
    root = Path(root).resolve()
    paths = {
        "frozenExecutionManifest": EXECUTION_FREEZE,
        "baselineValidator": "scripts/model-comparison/input_execution_v1/recovery_cohort.py",
        "baselineTests": "scripts/model-comparison/input_execution_v1/test_recovery_cohort.py",
        "validator": "scripts/model-comparison/input_execution_v1/recovery_cohort_v2.py",
        "tests": "scripts/model-comparison/input_execution_v1/test_recovery_cohort_v2.py",
        "correctionNote": CORRECTION_NOTE,
    }
    refs = {key: v1.ref(root, path) for key, path in paths.items()}
    require(refs["baselineValidator"]["sha256"] == V1_CODE_SHA256 and refs["frozenExecutionManifest"]["sha256"] == EXECUTION_FREEZE_SHA256, "recovery_v2_frozen_lineage_changed")
    frozen = v1.read_json(e.rooted(root, EXECUTION_FREEZE))
    for item in frozen["files"]:
        require(v1.file_sha(e.rooted(root, item["path"])) == item["sha256"], "recovery_v2_original_execution_code_changed")
    return refs


def validate_recovery_run(recovery_dir, prior, root=ROOT):
    """Frozen v1 checks, with the explicit paired port/origin comparison fix."""
    root = Path(root).resolve()
    run = e.rooted(root, recovery_dir)
    require(run.parent == e.rooted(root, v1.RECOVERY_BASE) and re.fullmatch(r"recovery-attempt-\d{3}", run.name), "recovery_run_path")
    require(all(path.parent.parent == run for path in run.parent.glob("*/http/*completion.request.json")), "recovery_other_recovery_calls_require_new_contract")
    prepared = v1._fixed_inputs(root)
    checked = v1._validate_run(run, root, prepared, recovered=True)
    plan, old_plan = checked["evidence"]["planData"], prior["evidence"]["planData"]
    require(plan.get("cohortExpectedOutputs") == 64 and plan.get("pendingOutputOrder") == [list(k) for k in prior["missingKeys"]], "recovery_pending_tail_changed")
    recovery = plan.get("recovery", {})
    require(recovery.get("priorRunPath") == v1.PRIOR_PATH and recovery.get("priorSummarySha256") == v1.PRIOR_SUMMARY_SHA256 and recovery.get("priorCompletedOutputs") == 38 and recovery.get("priorCompletionRequestsSent") == 39 and recovery.get("interruptedKey") == list(v1.INTERRUPTED_KEY) and recovery.get("originalFailurePreserved") is True and recovery.get("sourceOutputQualitySelection") is False and bool(recovery.get("reuseReason")), "recovery_prior_binding")
    for field in ("registeredIdentity", "sampling", "contextSize", "resourceProfile", "timeLimitsSeconds", "preparedManifestPath", "preparedManifestSha256", "outputOrder"):
        require(plan.get(field) == old_plan.get(field), "recovery_infrastructure_changed:" + field)
    inputs = {record["path"]: record for record in plan["inputFiles"]}
    for record in [*old_plan["inputFiles"], *prior["evidence"]["files"]]:
        require(record["path"] in inputs and inputs[record["path"]]["sha256"] == record["sha256"], "recovery_prior_evidence_not_frozen")
    require(all(name in inputs for name in v1.REQUIRED_RECOVERY_CODE), "recovery_code_or_contract_not_frozen")
    require(v1.read_json(run / "retained-evidence.json") == prior["evidence"], "recovery_retained_evidence_changed")
    old_run = e.rooted(root, v1.PRIOR_PATH)
    require(v1.read_json(run / "runtime-identity.json") == v1.read_json(old_run / "runtime-identity.json"), "recovery_runtime_identity_changed")
    require(normalize_runtime_command(v1.read_json(run / "runtime-command.json")) == normalize_runtime_command(v1.read_json(old_run / "runtime-command.json")), "recovery_runtime_command_changed")
    require(checked["summary"].get("cohortExpectedOutputs") == 64 and checked["summary"].get("retainedOutputs") == 38 and checked["summary"].get("ggufContract") == v1.read_json(old_run / "summary.json")["ggufContract"], "recovery_summary_cohort_identity")
    return {"rows": checked["rows"], "evidence": checked["evidence"]}


def _metadata(prior, recovery, root):
    lineage = validation_identity(root)
    manifest = v1._cohort_manifest(prior, recovery, root)
    manifest.update(validationVersion=VALIDATION_VERSION, validationLineage=lineage)
    evidence = {"prior": prior["evidence"], "recovery": recovery["evidence"], "validationLineage": lineage,
                "files": prior["evidence"]["files"] + recovery["evidence"]["files"] + list(lineage.values())}
    return manifest, evidence


def _prediction_bytes(prior, recovery, root):
    parts = []
    for result in (prior, recovery):
        reference = result["evidence"]["predictions"]
        raw = e.rooted(root, reference["path"]).read_bytes()
        require(e.sha(raw) == reference["sha256"] and len(raw) == reference["bytes"], "recovery_predictions_changed_before_build")
        parts.append(raw)
    return b"".join(parts)


def _unchanged_refs(evidence, root):
    for reference in evidence["files"]:
        require(v1.ref(root, reference["path"]) == reference, "recovery_v2_evidence_changed_during_validation")


def build(prior_dir, recovery_dir, destination, root=ROOT):
    root = Path(root).resolve()
    destination = e.rooted(root, destination)
    require(destination.is_relative_to(e.rooted(root, v1.COHORT_BASE)) and not destination.exists(), "recovery_cohort_destination_must_be_new")
    lineage_before = validation_identity(root)
    prior = validate_failed_run(prior_dir, root)
    recovery = validate_recovery_run(recovery_dir, prior, root)
    manifest, evidence = _metadata(prior, recovery, root)
    require(manifest["validationLineage"] == lineage_before, "recovery_v2_code_changed_during_validation")
    prediction_bytes = _prediction_bytes(prior, recovery, root)
    manifest["predictions"] = {"path": (destination / "predictions.jsonl").relative_to(root).as_posix(), "sha256": e.sha(prediction_bytes), "bytes": len(prediction_bytes)}
    manifest["evidence"] = evidence
    _unchanged_refs(evidence, root)
    destination.mkdir(parents=True)
    with (destination / "predictions.jsonl").open("xb") as stream:
        stream.write(prediction_bytes)
    e.write_new(destination / "manifest.json", manifest)
    return {"rows": prior["rows"] + recovery["rows"], "evidence": evidence, "manifest": manifest}


def validate_cohort(cohort_path, root=ROOT):
    root = Path(root).resolve()
    path = e.rooted(root, cohort_path)
    path = path / "manifest.json" if path.is_dir() else path
    require(path.name == "manifest.json", "recovery_cohort_manifest_required")
    manifest_raw = path.read_bytes()
    manifest = v1._json(manifest_raw)
    require(manifest.get("version") == VERSION and manifest.get("validationVersion") == VALIDATION_VERSION and len(manifest.get("sourceRuns", [])) == 2, "recovery_v2_cohort_contract")
    require(manifest.get("validationLineage") == validation_identity(root), "recovery_v2_validation_lineage_changed")
    prior = validate_failed_run(manifest["sourceRuns"][0]["runPath"], root)
    recovery = validate_recovery_run(manifest["sourceRuns"][1]["runPath"], prior, root)
    expected, evidence = _metadata(prior, recovery, root)
    require(all(manifest.get(k) == value for k, value in expected.items()), "recovery_cohort_metadata_changed")
    require(manifest.get("evidence") == evidence, "recovery_cohort_evidence_changed")
    predictions = e.rooted(root, manifest["predictions"]["path"])
    require(predictions == path.parent / "predictions.jsonl" and v1.ref(root, predictions) == manifest["predictions"], "recovery_cohort_predictions_identity")
    require(predictions.read_bytes() == _prediction_bytes(prior, recovery, root), "recovery_cohort_records_rewritten")
    _unchanged_refs(evidence, root)
    require(path.read_bytes() == manifest_raw and v1.ref(root, predictions) == manifest["predictions"], "recovery_cohort_changed_during_validation")
    return {"rows": prior["rows"] + recovery["rows"], "evidence": evidence, "manifest": manifest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("build")
    create.add_argument("--prior", default=v1.PRIOR_PATH)
    create.add_argument("--recovery", required=True)
    create.add_argument("--destination", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--cohort", required=True)
    args = parser.parse_args()
    result = build(args.prior, args.recovery, args.destination) if args.command == "build" else validate_cohort(args.cohort)
    print(v1.json.dumps({key: result["manifest"][key] for key in ("version", "validationVersion", "status", "expectedOutputs", "retainedOutputs", "recoveredOutputs", "completionRequestsSent", "interruptedRequests")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
