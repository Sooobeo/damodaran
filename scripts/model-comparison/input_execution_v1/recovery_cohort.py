"""Validate the fixed failed S4 run and an explicit 38+26 logical cohort.

Read-only validation has no model, HTTP, annotation, database or ledger calls.
Original records and their failed run remain unchanged. Large identity files
are hashed in bounded chunks, never read into one in-memory byte string.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_execution_v1 import evaluation as e
from input_execution_v1 import runtime_contract as runtime

VERSION = "input-execution-v1-recovery-cohort-v1"
PRIOR_VERSION = "input-preparation-v1-hy7-c0-c3-producer-v1"
RECOVERY_VERSION = "input-preparation-v1-hy7-c0-c3-recovery-producer-v1"
PRIOR_PATH = ".training/comparisons/input-preparation-v1/s4-generation/attempt-001"
PRIOR_SUMMARY_SHA256 = "229c0e749e4838ebac51ea1b714e2b9d753a75f5e993988317871777b6534fa1"
PRIOR_PLAN_SHA256 = "479b3ec44cc7a7c20662507484589bf1dc2f02eee5bf964f57aac3830305a054"
PRIOR_PREDICTIONS_SHA256 = "d4abc1c87dc7969476a0777a2e0a5c016cccd360174c90ef84731c3797b7cd67"
RECOVERY_BASE = ".training/comparisons/input-preparation-v1/s4-recovery"
COHORT_BASE = ".training/comparisons/input-preparation-v1"
INTERRUPTED_KEY = ("IP1-G02", "C2")
REQUIRED_RECOVERY_CODE = (
    "scripts/model-comparison/input_execution_v1/recovery_producer.py",
    "scripts/model-comparison/input_execution_v1/test_recovery_producer.py",
    "scripts/model-comparison/input_execution_v1/recovery_cohort.py",
    "scripts/model-comparison/input_execution_v1/test_recovery_cohort.py",
    "content/model-comparison/input-execution-v1/RECOVERY_PRIOR.json",
    "content/model-comparison/input-execution-v1/RECOVERY_V1.md",
)
require = e.require


def file_sha(path):
    path = Path(path)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    require((before.st_size, before.st_mtime_ns, before.st_ino) == (after.st_size, after.st_mtime_ns, after.st_ino), "recovery_file_changed_during_hash")
    return digest.hexdigest()


def ref(root, path):
    path = e.rooted(root, path)
    return {"path": path.relative_to(root).as_posix(), "sha256": file_sha(path), "bytes": path.stat().st_size}


def _json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("nonfinite_json_number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def read_json(path):
    return _json(Path(path).read_bytes())


def read_rows(path):
    raw = Path(path).read_bytes()
    lines = raw.splitlines(keepends=True)
    require(lines and all(line.strip() for line in lines) and raw.endswith(b"\n"), "incomplete_or_blank_jsonl")
    return [_json(line) for line in lines], raw


def _fixed_inputs(root):
    """Check only source inputs, not evaluation annotations or question keys."""
    freeze_path = e.rooted(root, e.S1 + "/freeze-manifest.json")
    require(file_sha(freeze_path) == e.FREEZE_SHA, "recovery_s1_freeze_changed")
    artifacts = {item["path"]: item for item in read_json(freeze_path)["artifacts"]}
    sources = {}
    for name in e.SOURCES:
        path = e.rooted(root, name)
        require(file_sha(path) == artifacts[name]["sha256"], "recovery_source_input_changed")
        for unit in read_json(path)["inputUnits"]:
            require(unit["id"] not in sources, "recovery_duplicate_source")
            sources[unit["id"]] = next(block["text"] for block in unit["document"]["blocks"] if block["id"] == unit["targetBlockId"])
    rows = e.load_prepared(root)
    expected = [(f"IP1-{domain}{number:02d}", cfg) for domain in ("F", "G") for number in range(1, 9) for cfg in e.CONFIGURATIONS]
    require([e.output_key(row) for row in rows] == expected and len(sources) == 16, "recovery_fixed64_input_order")
    for row in rows:
        runtime.validate_prompt_row(row)
        require(row["source"] == sources[row["id"]], "recovery_source_binding")
    return rows


def _artifact_snapshot(run, root):
    summary_path = run / "summary.json"
    summary = read_json(summary_path)
    hashes = summary.get("artifactHashes")
    require(isinstance(hashes, dict) and hashes, "recovery_artifact_inventory_missing")
    actual = {path.relative_to(run).as_posix() for path in run.rglob("*") if path.is_file() and path != summary_path}
    require(actual == set(hashes), "recovery_unlisted_or_missing_artifact")
    references = [ref(root, summary_path)]
    for name, digest in sorted(hashes.items()):
        path = e.rooted(root, run / name)
        require(path.is_relative_to(run) and not Path(name).is_absolute() and ".." not in Path(name).parts, "recovery_artifact_escape")
        record = ref(root, path)
        require(record["sha256"] == digest, "recovery_artifact_hash_changed:" + name)
        references.append(record)
    return summary, references


def _input_bindings(plan, root):
    records = plan.get("inputFiles")
    require(isinstance(records, list) and records, "recovery_missing_input_identity")
    require(len({row["path"] for row in records}) == len(records), "recovery_duplicate_input_identity")
    for expected in records:
        actual = ref(root, expected["path"])
        require(actual["sha256"] == expected["sha256"] and actual["bytes"] == expected["bytes"], "recovery_input_identity_changed:" + expected["path"])
    return records


def _http_inventory(run, root):
    requests = []
    for path in sorted((run / "http").glob("*.request.json")):
        request = read_json(path)
        metadata = request["metadata"]
        endpoint = metadata.get("endpoint")
        require(endpoint in ("/health", "/props", "/apply-template", "/tokenize", "/detokenize", "/completion"), "recovery_unknown_http_endpoint")
        sequence = metadata.get("sequence")
        require(type(sequence) is int and path.name == f"{sequence:05d}-{endpoint[1:]}.request.json", "recovery_http_sequence_identity")
        require(metadata.get("method") == ("GET" if endpoint in ("/health", "/props") else "POST") and metadata.get("authorizationRecorded") is False, "recovery_http_request_contract")
        timeout = metadata.get("timeoutSeconds")
        require(type(timeout) in (int, float) and 0 < timeout <= 1800 and (endpoint != "/completion" or timeout == 1800), "recovery_http_timeout_changed")
        # Stored JSON keys are sorted; the actual sent body used payload order.
        # Hash against the reconstructed protocol payload in _http_response.
        require(isinstance(metadata.get("requestSha256"), str) and re.fullmatch(r"[0-9a-f]{64}", metadata["requestSha256"]), "recovery_http_request_payload_hash")
        if endpoint in ("/health", "/props"):
            require(request["payload"] is None and metadata["requestSha256"] == e.sha(b""), "recovery_get_payload_changed")
        receipt_path = path.with_name(path.name.replace(".request.json", ".receipt.json"))
        receipt = read_json(receipt_path)
        require(all(receipt.get(key) == value for key, value in metadata.items()) and receipt.get("automaticRetry") is False, "recovery_http_receipt_request_mismatch")
        requests.append({"path": path, "request": request, "receiptPath": receipt_path, "receipt": receipt})
    require([item["request"]["metadata"]["sequence"] for item in requests] == list(range(1, len(requests) + 1)), "recovery_http_sequence_gap")
    require(len(list((run / "http").glob("*.receipt.json"))) == len(requests), "recovery_unpaired_http_receipt")
    return requests


def _http_response(item, run, root, expected_payload=None, *, check_payload=True):
    request, receipt = item["request"], item["receipt"]
    if check_payload:
        require(request["payload"] == expected_payload, "recovery_actual_request_changed")
        body = b"" if expected_payload is None else json.dumps(expected_payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        require(request["metadata"]["requestSha256"] == e.sha(body), "recovery_actual_request_hash_changed")
    require(receipt.get("httpStatus") == 200 and receipt.get("responseCompleteWithinLimit") is True and receipt.get("responseEofObserved") is True and receipt.get("transportOrProtocolErrorType") is None, "recovery_response_not_complete")
    path = e.rooted(root, receipt["rawResponsePath"])
    require(path.parent == run / "http" and path.name == item["path"].name.replace(".request.json", ".response.bin"), "recovery_raw_path_identity")
    require(file_sha(path) == receipt["rawResponseSha256"] and path.stat().st_size == receipt["responseBytes"], "recovery_receipt_raw_identity")
    require(receipt.get("expectedResponseBytes") is None or receipt["expectedResponseBytes"] == receipt["responseBytes"], "recovery_content_length_mismatch")
    return read_json(path)


def _parity_and_runtime(run, plan, prepared, requests, root):
    parity = read_json(run / "all-input-parity.json")
    require(parity.get("completed") == 64 and parity.get("completionRequestsSent") == 0, "recovery_all64_parity_required")
    templates = [item for item in requests if item["request"]["metadata"]["endpoint"] == "/apply-template"]
    completions = [item for item in requests if item["request"]["metadata"]["endpoint"] == "/completion"]
    require(len(templates) == 64 and completions, "recovery_template_inventory")
    first_completion = completions[0]["request"]["metadata"]
    expected_parity = []
    for prompt, item in zip(prepared, templates):
        seq = item["request"]["metadata"]["sequence"]
        require(seq + 1 < first_completion["sequence"], "recovery_completion_before_all64_parity")
        rendered = _http_response(item, run, root, runtime.template_payload(prompt))
        template = runtime.validate_template_response(rendered, prompt)
        token_item = requests[seq]
        require(token_item["request"]["metadata"]["endpoint"] == "/tokenize", "recovery_tokenization_order")
        tokenized = _http_response(token_item, run, root, runtime.tokenize_payload(prompt))
        tokens = runtime.validate_tokenize_response(tokenized, prompt)
        require(token_item["receipt"]["completedAt"] <= first_completion["startedAt"], "recovery_parity_time_order")
        expected_parity.append({"id": prompt["id"], "configuration": prompt["configuration"], "template": template, "promptSha256": prompt["promptSha256"], "tokenIdsSha256": prompt["tokenIdsSha256"], "tokenCount": len(tokens)})
    require(parity.get("rows") == expected_parity, "recovery_parity_receipt_changed")
    props_items = [item for item in requests if item["request"]["metadata"]["endpoint"] == "/props"]
    require(len(props_items) == 1, "recovery_props_inventory")
    props = _http_response(props_items[0], run, root)
    model_files = [item for item in plan["inputFiles"] if item["path"].endswith(".gguf")]
    require(len(model_files) == 1, "recovery_model_inventory")
    validated = runtime.validate_props(props, model_path=e.rooted(root, model_files[0]["path"]), template=props["chat_template"])
    identity = read_json(run / "runtime-identity.json")
    require(identity.get("props") == validated, "recovery_props_identity_mismatch")
    observed_eog = runtime.baseline.eog_from_log((run / "runtime.log").read_text("utf-8", errors="replace"))
    require(identity.get("eog") == json.loads(json.dumps(observed_eog)), "recovery_eog_identity_mismatch")
    require(set(identity.get("specialTokens", {})) == {"3", "127957", "127958", "127960", "127961", "127962", "127967"}, "recovery_special_token_inventory")
    specials = [item for item in requests if item["request"]["metadata"]["sequence"] < props_items[0]["request"]["metadata"]["sequence"] and item["request"]["metadata"]["endpoint"] in ("/tokenize", "/detokenize")]
    class Replay:
        def request(self, endpoint, payload=None, **kwargs):
            require(specials, "recovery_special_token_call_missing")
            item = specials.pop(0)
            require(item["request"]["metadata"]["endpoint"] == endpoint, "recovery_special_token_call_order")
            return _http_response(item, run, root, payload)
    observed_specials = runtime.baseline.validate_runtime_tokens(Replay())
    require(not specials and {str(k): v for k, v in observed_specials.items()} == identity["specialTokens"], "recovery_special_token_identity_changed")
    return completions


def _record(run, prepared, item, index, version, root, offset=0):
    key = e.output_key(prepared)
    path = run / "outputs" / (key[0] + "-" + key[1] + ".json")
    output = read_json(path)
    response = _http_response(item, run, root, runtime.completion_payload(prepared))
    validated = runtime.validate_completion(prepared, response, output["generationSeconds"])
    require(all(output.get(k) == v for k, v in validated.items()), "recovery_completion_revalidation_mismatch")
    require(output.get("runId") == run.name and output.get("producerVersion") == version and output.get("requestSequence") == index, "recovery_output_run_identity")
    require(output.get("preparedManifestSha256") == e.S2_SHA and output.get("sourceOutputReused") is False, "recovery_output_prepared_identity")
    require(output.get("processingIdentity") == {"configuration": key[1], "preparedPromptSha256": prepared["promptSha256"], "contextPolicy": prepared["context"]["policyVersion"], "selectorVersion": prepared["terminology"]["selectorVersion"]}, "recovery_processing_identity")
    require(output.get("rawResponsePath") == item["receipt"]["rawResponsePath"] and output.get("rawResponseSha256") == item["receipt"]["rawResponseSha256"], "recovery_output_raw_identity")
    if offset:
        require(output.get("preparedSequence") == index + offset and output.get("technicalRecoveryRetry") is (index == 1), "recovery_output_tail_sequence")
    return output


def _events(run, prepared, outputs, requests, *, interrupted=False, offset=0):
    events, _ = read_rows(run / "attempt-events.jsonl")
    require(len(events) == len(outputs) * 2 + int(interrupted), "recovery_event_count")
    for index, prompt in enumerate(prepared[:len(outputs) + int(interrupted)], 1):
        pending = events[(index - 1) * 2]
        expected = {"id": prompt["id"], "configuration": prompt["configuration"], "sequence": index, "status": "request_pending", "sourceSha256": prompt["sourceSha256"], "promptSha256": prompt["promptSha256"], "tokenIdsSha256": prompt["tokenIdsSha256"], "contextPolicy": prompt["context"]["policyVersion"], "selectorVersion": prompt["terminology"]["selectorVersion"], "automaticRetry": False, "sourceOutputReuse": False}
        if offset:
            expected.update(preparedSequence=index + offset, technicalRecoveryRetry=index == 1)
        require(all(pending.get(k) == v for k, v in expected.items()), "recovery_call_intent_identity")
        require(pending["at"] <= requests[index - 1]["request"]["metadata"]["startedAt"], "recovery_intent_after_request")
        if index <= len(outputs):
            completed = events[(index - 1) * 2 + 1]
            expected.update(status="completed", translationSha256=outputs[index - 1]["translationSha256"], rawResponseSha256=outputs[index - 1]["rawResponseSha256"])
            require(all(completed.get(k) == v for k, v in expected.items()), "recovery_completion_event_identity")


def _validate_run(run, root, prepared, *, recovered=False):
    summary, files = _artifact_snapshot(run, root)
    plan = read_json(run / "plan.json")
    version = RECOVERY_VERSION if recovered else PRIOR_VERSION
    count, sent, offset = (26, 26, 38) if recovered else (38, 39, 0)
    expected_outputs = 26 if recovered else 64
    require(summary.get("version") == plan.get("version") == version, "recovery_run_version")
    require(summary.get("status") == ("completed" if recovered else "failed") and summary.get("completedOutputs") == count and summary.get("completionRequestsSent") == sent and summary.get("expectedOutputs") == expected_outputs and summary.get("missingOutputs") == (0 if recovered else 26), "recovery_run_inventory")
    require(summary.get("childProcessStopped") is True and summary.get("integrityVerified") is True and summary.get("outputIntegrityPassed") is recovered, "recovery_shutdown_or_integrity_unverified")
    require(summary.get("powerRequestAfterRelease", {}).get("released") is True and summary.get("shutdownReceipt", {}).get("stopped") is True, "recovery_cleanup_unverified")
    require(plan.get("inferenceRequested") is True and plan.get("expectedOutputs") == expected_outputs and plan.get("preparedManifestSha256") == e.S2_SHA, "recovery_plan_contract")
    require(all(plan.get(k) is False for k in ("annotationFilesRead", "automaticRetry", "crossConfigurationReuse", "historicalOutputsUsedForPrompts")), "recovery_input_isolation_contract")
    require(plan.get("sampling") == runtime.baseline.SAMPLING and plan.get("contextSize") == 8192, "recovery_sampling_changed")
    require(plan.get("outputOrder") == [[p["id"], p["configuration"]] for p in prepared], "recovery_output_order_changed")
    inputs, _ = read_rows(run / "inputs.jsonl")
    require(inputs == prepared, "recovery_prepared_inputs_changed")
    input_files = _input_bindings(plan, root)
    requests = _http_inventory(run, root)
    completions = _parity_and_runtime(run, plan, prepared, requests, root)
    require(len(completions) == sent, "recovery_unrecorded_completion_requests")
    pending = prepared[offset:]
    outputs = [_record(run, prompt, item, i, version, root, offset) for i, (prompt, item) in enumerate(zip(pending[:count], completions), 1)]
    expected_names = {p["id"] + "-" + p["configuration"] + ".json" for p in pending[:count]}
    require({path.name for path in (run / "outputs").iterdir()} == expected_names, "recovery_output_file_inventory")
    predictions, predictions_raw = read_rows(run / "predictions.jsonl")
    require(predictions == outputs, "recovery_prediction_records_changed")
    _events(run, pending, outputs, completions, interrupted=not recovered, offset=offset)
    if recovered:
        require(read_json(run / "missing-outputs.json") == [] and summary.get("failure") is None, "recovery_run_has_missing_or_failure")
    evidence = {"summary": ref(root, run / "summary.json"), "plan": ref(root, run / "plan.json"), "predictions": ref(root, run / "predictions.jsonl"), "files": files, "inputFiles": input_files, "planData": plan}
    final_summary, final_files = _artifact_snapshot(run, root)
    require(summary == final_summary and files == final_files, "recovery_run_changed_during_validation")
    return {"rows": outputs, "evidence": evidence, "summary": summary, "requests": completions, "predictionBytes": predictions_raw}


def validate_failed_run(prior_dir, root=ROOT):
    root = Path(root).resolve()
    run = e.rooted(root, prior_dir)
    require(run == e.rooted(root, PRIOR_PATH), "recovery_wrong_prior_run")
    require(all(path.parent.parent == run for path in run.parent.glob("*/http/*completion.request.json")), "recovery_unapproved_original_calls")
    for name, digest in (("summary.json", PRIOR_SUMMARY_SHA256), ("plan.json", PRIOR_PLAN_SHA256), ("predictions.jsonl", PRIOR_PREDICTIONS_SHA256)):
        require(file_sha(run / name) == digest, "recovery_pinned_prior_changed:" + name)
    prepared = _fixed_inputs(root)
    require(e.output_key(prepared[38]) == INTERRUPTED_KEY, "recovery_interrupted_input_changed")
    checked = _validate_run(run, root, prepared)
    summary = checked["summary"]
    failure = {"type": "ResourceGuardError", "code": "ac_power_lost_or_unknown", "id": INTERRUPTED_KEY[0], "configuration": INTERRUPTED_KEY[1]}
    require(summary.get("failure") == failure and summary.get("resourceMonitoring", {}).get("abortReason") == failure["code"], "recovery_original_failure_changed")
    missing = [{"id": p["id"], "configuration": p["configuration"], "sourceSha256": p["sourceSha256"], "status": "failed" if i == 0 else "not_run"} for i, p in enumerate(prepared[38:])]
    require(read_json(run / "missing-outputs.json") == missing, "recovery_missing_tail_changed")
    interrupted = checked["requests"][-1]
    require(interrupted["request"]["payload"] == runtime.completion_payload(prepared[38]), "recovery_interrupted_request_changed")
    expected_body = json.dumps(runtime.completion_payload(prepared[38]), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    require(interrupted["request"]["metadata"]["requestSha256"] == e.sha(expected_body), "recovery_interrupted_request_hash_changed")
    receipt = interrupted["receipt"]
    require(receipt.get("transportOrProtocolErrorType") == "ConnectionResetError" and receipt.get("httpStatus") is None and receipt.get("rawResponsePath") is None and receipt.get("rawResponseSha256") is None and receipt.get("responseBytes") is None and receipt.get("responseCompleteWithinLimit") is False and receipt.get("responseEofObserved") is False, "recovery_interrupted_response_not_unknown")
    require(not interrupted["path"].with_name(interrupted["path"].name.replace(".request.json", ".response.bin")).exists(), "recovery_unexpected_interrupted_body")
    failed_item = read_json(run / "failed-item.json")
    require(failed_item == {"id": INTERRUPTED_KEY[0], "configuration": INTERRUPTED_KEY[1], "failure": failure, "lastHttpReceipt": receipt}, "recovery_failed_item_changed")
    checked["evidence"]["interruptedRequest"] = {"key": list(INTERRUPTED_KEY), "request": ref(root, interrupted["path"]), "receipt": ref(root, interrupted["receiptPath"]), "response": None, "serverWorkUnknown": True, "usageKnown": False}
    return {"rows": checked["rows"], "missingKeys": [e.output_key(row) for row in prepared[38:]], "evidence": checked["evidence"]}


def validate_recovery_run(recovery_dir, prior, root=ROOT):
    root = Path(root).resolve()
    run = e.rooted(root, recovery_dir)
    require(run.parent == e.rooted(root, RECOVERY_BASE) and re.fullmatch(r"recovery-attempt-\d{3}", run.name), "recovery_run_path")
    require(all(path.parent.parent == run for path in run.parent.glob("*/http/*completion.request.json")), "recovery_other_recovery_calls_require_new_contract")
    prepared = _fixed_inputs(root)
    checked = _validate_run(run, root, prepared, recovered=True)
    plan, old_plan = checked["evidence"]["planData"], prior["evidence"]["planData"]
    require(plan.get("cohortExpectedOutputs") == 64 and plan.get("pendingOutputOrder") == [list(k) for k in prior["missingKeys"]], "recovery_pending_tail_changed")
    recovery = plan.get("recovery", {})
    require(recovery.get("priorRunPath") == PRIOR_PATH and recovery.get("priorSummarySha256") == PRIOR_SUMMARY_SHA256 and recovery.get("priorCompletedOutputs") == 38 and recovery.get("priorCompletionRequestsSent") == 39 and recovery.get("interruptedKey") == list(INTERRUPTED_KEY) and recovery.get("originalFailurePreserved") is True and recovery.get("sourceOutputQualitySelection") is False and bool(recovery.get("reuseReason")), "recovery_prior_binding")
    for field in ("registeredIdentity", "sampling", "contextSize", "resourceProfile", "timeLimitsSeconds", "preparedManifestPath", "preparedManifestSha256", "outputOrder"):
        require(plan.get(field) == old_plan.get(field), "recovery_infrastructure_changed:" + field)
    inputs = {record["path"]: record for record in plan["inputFiles"]}
    for record in [*old_plan["inputFiles"], *prior["evidence"]["files"]]:
        require(record["path"] in inputs and inputs[record["path"]]["sha256"] == record["sha256"], "recovery_prior_evidence_not_frozen")
    require(all(name in inputs for name in REQUIRED_RECOVERY_CODE), "recovery_code_or_contract_not_frozen")
    require(read_json(run / "retained-evidence.json") == prior["evidence"], "recovery_retained_evidence_changed")
    old_run = e.rooted(root, PRIOR_PATH)
    require(read_json(run / "runtime-identity.json") == read_json(old_run / "runtime-identity.json"), "recovery_runtime_identity_changed")
    def normalized_command(path):
        command = read_json(path)
        argv = command["argv"].copy()
        require(argv[argv.index("--api-key") + 1] == "[EPHEMERAL_REDACTED]" and command["shell"] is False, "recovery_secret_or_shell_contract")
        argv[argv.index("--port") + 1] = "<LOCAL_PORT>"
        return {**command, "argv": argv}
    require(normalized_command(run / "runtime-command.json") == normalized_command(old_run / "runtime-command.json"), "recovery_runtime_command_changed")
    require(checked["summary"].get("cohortExpectedOutputs") == 64 and checked["summary"].get("retainedOutputs") == 38 and checked["summary"].get("ggufContract") == read_json(old_run / "summary.json")["ggufContract"], "recovery_summary_cohort_identity")
    return {"rows": checked["rows"], "evidence": checked["evidence"]}


def _cohort_manifest(prior, recovery, root):
    rows = prior["rows"] + recovery["rows"]
    require(len(rows) == 64 and len({e.output_key(row) for row in rows}) == 64, "recovery_cohort_inventory")
    source_runs = []
    for role, result, status, outputs, requests in (("retained", prior, "failed", 38, 39), ("recovered", recovery, "completed", 26, 26)):
        evidence = result["evidence"]
        source_runs.append({"role": role, "runPath": str(Path(evidence["summary"]["path"]).parent).replace("\\", "/"), "status": status, "summarySha256": evidence["summary"]["sha256"], "planSha256": evidence["plan"]["sha256"], "predictionsSha256": evidence["predictions"]["sha256"], "outputs": outputs, "completionRequestsSent": requests})
    return {"version": VERSION, "status": "completed_logical_cohort", "expectedOutputs": 64, "retainedOutputs": 38, "recoveredOutputs": 26, "completionRequestsSent": 65, "interruptedRequests": 1, "interruptedKey": list(INTERRUPTED_KEY), "interruptedServerWorkUnknown": True, "originalFailurePreserved": True, "singleRunCompletion": False, "sourceOutputQualitySelection": False, "semanticQualityCertified": False, "humanReviewed": False, "modelCallsByCohortBuilder": 0, "preparedManifestSha256": e.S2_SHA, "outputOrder": [[r["id"], r["configuration"]] for r in rows], "sourceRuns": source_runs}


def build(prior_dir, recovery_dir, destination, root=ROOT):
    root = Path(root).resolve()
    destination = e.rooted(root, destination)
    require(destination.is_relative_to(e.rooted(root, COHORT_BASE)) and not destination.exists(), "recovery_cohort_destination_must_be_new")
    prior = validate_failed_run(prior_dir, root)
    recovery = validate_recovery_run(recovery_dir, prior, root)
    manifest = _cohort_manifest(prior, recovery, root)
    # Concatenate the exact original NDJSON bytes; do not rewrite either row.
    parts = []
    for result in (prior, recovery):
        reference = result["evidence"]["predictions"]
        raw = e.rooted(root, reference["path"]).read_bytes()
        require(e.sha(raw) == reference["sha256"] and len(raw) == reference["bytes"], "recovery_predictions_changed_before_build")
        parts.append(raw)
    prediction_bytes = b"".join(parts)
    manifest["predictions"] = {"path": (destination / "predictions.jsonl").relative_to(root).as_posix(), "sha256": e.sha(prediction_bytes), "bytes": len(prediction_bytes)}
    manifest["evidence"] = {"prior": prior["evidence"], "recovery": recovery["evidence"], "files": prior["evidence"]["files"] + recovery["evidence"]["files"]}
    for reference in manifest["evidence"]["files"]:
        require(ref(root, reference["path"]) == reference, "recovery_evidence_changed_before_build")
    destination.mkdir(parents=True)
    with (destination / "predictions.jsonl").open("xb") as stream:
        stream.write(prediction_bytes)
    e.write_new(destination / "manifest.json", manifest)
    return {"rows": prior["rows"] + recovery["rows"], "evidence": manifest["evidence"], "manifest": manifest}


def validate_cohort(cohort_path, root=ROOT):
    root = Path(root).resolve()
    path = e.rooted(root, cohort_path)
    path = path / "manifest.json" if path.is_dir() else path
    require(path.name == "manifest.json", "recovery_cohort_manifest_required")
    manifest_raw = path.read_bytes()
    manifest = _json(manifest_raw)
    require(manifest.get("version") == VERSION and len(manifest.get("sourceRuns", [])) == 2, "recovery_cohort_contract")
    prior = validate_failed_run(manifest["sourceRuns"][0]["runPath"], root)
    recovery = validate_recovery_run(manifest["sourceRuns"][1]["runPath"], prior, root)
    expected = _cohort_manifest(prior, recovery, root)
    require(all(manifest.get(k) == v for k, v in expected.items()), "recovery_cohort_metadata_changed")
    evidence = {"prior": prior["evidence"], "recovery": recovery["evidence"], "files": prior["evidence"]["files"] + recovery["evidence"]["files"]}
    require(manifest.get("evidence") == evidence, "recovery_cohort_evidence_changed")
    predictions = e.rooted(root, manifest["predictions"]["path"])
    require(predictions == path.parent / "predictions.jsonl" and ref(root, predictions) == manifest["predictions"], "recovery_cohort_predictions_identity")
    original = b"".join(e.rooted(root, result["evidence"]["predictions"]["path"]).read_bytes() for result in (prior, recovery))
    require(predictions.read_bytes() == original, "recovery_cohort_records_rewritten")
    for reference in evidence["files"]:
        require(ref(root, reference["path"]) == reference, "recovery_evidence_changed_during_cohort_validation")
    require(path.read_bytes() == manifest_raw and ref(root, predictions) == manifest["predictions"], "recovery_cohort_changed_during_validation")
    return {"rows": prior["rows"] + recovery["rows"], "evidence": evidence, "manifest": manifest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    failed = sub.add_parser("validate-failed")
    failed.add_argument("--prior", default=PRIOR_PATH)
    failed.add_argument("--receipt")
    cohort = sub.add_parser("build")
    cohort.add_argument("--prior", default=PRIOR_PATH)
    cohort.add_argument("--recovery", required=True)
    cohort.add_argument("--destination", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--cohort", required=True)
    args = parser.parse_args()
    if args.command == "validate-failed":
        result = validate_failed_run(args.prior)
        receipt = {"version": VERSION, "status": "failed_run_evidence_verified", "completedOutputsPreserved": 38, "completionRequestsSent": 39, "missingKeys": [list(k) for k in result["missingKeys"]], "originalStatus": "failed", "modelCalls": 0, "evidence": result["evidence"]}
        if args.receipt:
            path = e.rooted(ROOT, args.receipt)
            require(path.is_relative_to(e.rooted(ROOT, COHORT_BASE)) and not path.is_relative_to(e.rooted(ROOT, PRIOR_PATH)), "recovery_receipt_destination")
            e.write_new(path, receipt)
        print(json.dumps({k: v for k, v in receipt.items() if k != "evidence"}, ensure_ascii=False))
    else:
        result = build(args.prior, args.recovery, args.destination) if args.command == "build" else validate_cohort(args.cohort)
        print(json.dumps({k: result["manifest"][k] for k in ("version", "status", "expectedOutputs", "retainedOutputs", "recoveredOutputs", "completionRequestsSent", "interruptedRequests")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
