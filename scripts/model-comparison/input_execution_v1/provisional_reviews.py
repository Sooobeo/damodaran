"""Read-only, anonymous source-review drafts for four completed S4 outputs.

This is not S5 completion. No questions, scores, decisions, model/native calls,
or writes inside the active S4 run are permitted. Formal packet promotion still
requires evaluation.prepare's successful 64-output and owned-shutdown gate.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_execution_v1 import evaluation as e
from input_execution_v1 import runtime_contract as runtime

VERSION = "input-execution-v1-provisional-source-reviews-v1"
PRODUCER_VERSION = "input-preparation-v1-hy7-c0-c3-producer-v1"
REVIEW_BASE = ".training/quality-evaluation/input-preparation-v1"
RUNTIME_CODE = "scripts/model-comparison/input_execution_v1/runtime_contract.py"
BASELINE_CODE = "scripts/model-comparison/run_hymt.py"


def ref(root, path):
    path = e.rooted(root, path)
    raw = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "sha256": e.sha(raw), "bytes": len(raw)}


def verify_refs(root, refs):
    for record in refs:
        e.require(e.sha(e.rooted(root, record["path"]).read_bytes()) == record["sha256"], "provisional_evidence_changed")


def packet_for(unit_id, configuration, output, units, annotations, prepared):
    """Exact final source packet schema and SHA shuffle, without other outputs."""
    order = sorted([(uid, cfg) for uid in units for cfg in e.CONFIGURATIONS],
                   key=lambda key: e.sha(e.SEED + "\n" + key[0] + "\n" + key[1]))
    e.require(len(order) == 64 and (unit_id, configuration) in order, "fixed_64_review_order_required")
    e.require({e.output_key(row) for row in prepared} == set(order), "prepared_review_inventory")
    review_id = "R" + format(order.index((unit_id, configuration)) + 1, "03d")
    return {"reviewId": review_id, "source": output["source"], "sourceSha256": output["sourceSha256"],
            "translation": output["translation"], "translationSha256": output["translationSha256"],
            "evaluation": deepcopy(annotations[unit_id]), "sourceDocument": deepcopy(units[unit_id]["document"]),
            "humanReviewed": False}


def verify_output(run, unit_id, configuration, row, root=ROOT):
    """Revalidate the original completion and request, never a new inference."""
    run = e.rooted(root, run)
    output_path = run / "outputs" / (unit_id + "-" + configuration + ".json")
    output_ref = ref(root, output_path)
    output = e.read_json(output_path)
    e.require(e.output_key(output) == (unit_id, configuration) and output.get("status") == "completed", "provisional_output_not_complete")
    e.require(output.get("runId") == run.name and output.get("producerVersion") == PRODUCER_VERSION,
              "provisional_output_run_identity")
    e.require(output.get("preparedManifestSha256") == e.S2_SHA, "provisional_output_s2_identity")
    raw_path = e.rooted(root, output["rawResponsePath"])
    e.require(raw_path.is_relative_to(run / "http") and raw_path.name.endswith("-completion.response.bin"), "provisional_raw_path")
    raw_ref = ref(root, raw_path)
    e.require(raw_ref["sha256"] == output["rawResponseSha256"], "provisional_raw_hash")
    response = e.read_json(raw_path)
    revalidated = runtime.validate_completion(row, response, output["generationSeconds"])
    e.require(all(output.get(key) == value for key, value in revalidated.items()), "provisional_completion_revalidation_mismatch")
    base_name = raw_path.name.removesuffix(".response.bin")
    request_path = raw_path.with_name(base_name + ".request.json")
    receipt_path = raw_path.with_name(base_name + ".receipt.json")
    request, receipt = e.read_json(request_path), e.read_json(receipt_path)
    expected_payload = runtime.completion_payload(row)
    request_sha = e.sha(json.dumps(expected_payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    metadata = request["metadata"]
    e.require(request["payload"] == expected_payload and metadata.get("requestSha256") == request_sha,
              "provisional_actual_request_mismatch")
    e.require(metadata.get("endpoint") == "/completion" and metadata.get("method") == "POST" and
              metadata.get("authorizationRecorded") is False, "provisional_request_contract")
    e.require(all(receipt.get(key) == metadata.get(key) for key in metadata), "provisional_receipt_request_identity")
    e.require(receipt.get("httpStatus") == 200 and receipt.get("responseCompleteWithinLimit") is True and
              receipt.get("responseEofObserved") is True and receipt.get("transportOrProtocolErrorType") is None and
              receipt.get("automaticRetry") is False, "provisional_http_response_incomplete")
    e.require(receipt.get("rawResponseSha256") == raw_ref["sha256"] and
              receipt.get("rawResponsePath") == raw_ref["path"] and receipt.get("responseBytes") == raw_ref["bytes"],
              "provisional_receipt_response_identity")
    return output, [output_ref, raw_ref, ref(root, request_path), ref(root, receipt_path)]


def prepare(run, unit_id, destination, root=ROOT):
    root = Path(root).resolve()
    run, destination = e.rooted(root, run), e.rooted(root, destination)
    e.require(run.is_relative_to(root / ".training/comparisons/input-preparation-v1/s4-generation"), "provisional_s4_run_path")
    e.require(destination.is_relative_to(root / REVIEW_BASE) and destination != root / REVIEW_BASE,
              "provisional_destination_must_be_private_training_subfolder")
    e.require(not destination.exists(), "provisional_destination_must_be_new")
    plan_path = run / "plan.json"
    plan_ref = ref(root, plan_path)
    plan = e.read_json(plan_path)
    e.require(plan.get("version") == PRODUCER_VERSION and plan.get("inferenceRequested") is True and
              plan.get("expectedOutputs") == 64 and plan.get("preparedManifestSha256") == e.S2_SHA,
              "provisional_run_plan_contract")
    e.require(plan.get("annotationFilesRead") is False and plan.get("automaticRetry") is False and
              plan.get("crossConfigurationReuse") is False, "provisional_run_isolation_contract")
    e.require(plan.get("sampling") == runtime.baseline.SAMPLING and plan.get("contextSize") == 8192,
              "provisional_run_generation_contract")
    bindings = {record["path"]: record for record in plan["inputFiles"]}
    for name in (RUNTIME_CODE, BASELINE_CODE):
        e.require(name in bindings and ref(root, name)["sha256"] == bindings[name]["sha256"], "provisional_runtime_code_changed")
    # These loads check S1 evaluation/source and S2 code/artifacts. They do not
    # load weights, create a native context or interact with the active server.
    units, annotations, frozen_refs = e.load_development(root)
    prepared = e.load_prepared(root)
    e.require(unit_id in units, "provisional_unknown_unit")
    expected_order = [[row["id"], row["configuration"]] for row in prepared]
    e.require(plan.get("outputOrder") == expected_order, "provisional_s4_order_changed")
    inputs_path = run / "inputs.jsonl"
    e.require(e.read_rows(inputs_path) == prepared, "provisional_s4_inputs_changed")
    # Check all four exist before reading or writing any packet. Incomplete
    # groups remain unavailable; no placeholders or reduced denominator.
    paths = [run / "outputs" / (unit_id + "-" + cfg + ".json") for cfg in e.CONFIGURATIONS]
    e.require(all(path.is_file() for path in paths), "provisional_all_four_outputs_required")
    rows = {e.output_key(row): row for row in prepared}
    packets, mappings = [], []
    evidence = [plan_ref, ref(root, inputs_path), *(ref(root, name) for name in (RUNTIME_CODE, BASELINE_CODE)), *frozen_refs]
    for cfg in e.CONFIGURATIONS:
        output, refs = verify_output(run, unit_id, cfg, rows[(unit_id, cfg)], root)
        target = next(block for block in units[unit_id]["document"]["blocks"] if block["id"] == units[unit_id]["targetBlockId"])
        e.require(output["source"] == target["text"] and output["sourceSha256"] == annotations[unit_id]["targetTextSha256"],
                  "provisional_frozen_source_mismatch")
        e.require(output.get("requestSequence") == expected_order.index([unit_id, cfg]) + 1, "provisional_execution_order_mismatch")
        packet = packet_for(unit_id, cfg, output, units, annotations, prepared)
        packets.append(packet); evidence.extend(refs)
        mappings.append({"reviewId": packet["reviewId"], "id": unit_id, "configuration": cfg,
                         "sourceSha256": output["sourceSha256"], "promptSha256": output["promptSha256"],
                         "translationSha256": output["translationSha256"], "packetSha256": e.sha(e.packed(packet)),
                         "outputFile": refs[0], "rawResponse": refs[1]})
    verify_refs(root, evidence)
    for packet in sorted(packets, key=lambda packet: packet["reviewId"]):
        e.write_new(destination / "source-packets" / (packet["reviewId"] + ".json"), packet)
    manifest = {"version": VERSION, "createdAt": datetime.now(timezone.utc).isoformat(), "provisional": True,
        "status": "provisional_source_packets_only", "run": run.relative_to(root).as_posix(), "plan": plan_ref,
        "unit": unit_id, "seed": e.SEED, "sourcePackets": 4, "questionPackets": 0, "scoresProduced": 0,
        "candidateSelections": 0, "modelCalls": 0, "humanReviewed": False,
        "final64RunValidationPerformed": False, "formalSourceReviewsApproved": False,
        "confirmationBeforeFinal64ValidationForbidden": True,
        "finalPacketHashEqualityRequired": True, "mappings": mappings, "evidence": evidence,
        "preparationCode": ref(root, Path(__file__)), "evaluationCode": ref(root, Path(e.__file__))}
    e.write_new(destination / "private/manifest.json", manifest)
    return {key: manifest[key] for key in ("version", "status", "unit", "sourcePackets", "questionPackets", "scoresProduced", "modelCalls")}


def verify_promotion(provisional, final_folder, receipt_path, root=ROOT):
    """Verify identity only. An actual reviewer must still confirm each draft."""
    root = Path(root).resolve()
    provisional, final_folder, receipt_path = (e.rooted(root, path) for path in (provisional, final_folder, receipt_path))
    e.require(receipt_path.is_relative_to(root / REVIEW_BASE), "promotion_receipt_must_be_private_training")
    manifest_path = provisional / "private/manifest.json"
    manifest = e.read_json(manifest_path)
    e.require(manifest.get("version") == VERSION and manifest.get("provisional") is True and len(manifest["mappings"]) == 4,
              "provisional_manifest_contract")
    e.validate_run_completion(root / manifest["run"] / "predictions.jsonl", root)
    verify_refs(root, manifest["evidence"])
    bundle = e.read_json(final_folder / "private/bundle.json")
    final_manifest = e.read_json(final_folder / "manifest.json")
    e.require(final_manifest.get("status") == "packets_prepared" and final_manifest.get("questionPackets") == 64 and
              final_manifest.get("sourcePackets") == 64 and e.sha(e.packed(bundle)) == final_manifest["bundleSha256"],
              "formal_64_packet_preparation_required")
    expected_predictions = manifest["run"] + "/predictions.jsonl"
    e.require(any(record["path"] == expected_predictions for record in bundle["evidenceFiles"]), "formal_packets_other_run")
    final_packets = {packet["reviewId"]: packet for packet in bundle["sourcePackets"]}
    final_mappings = {mapping["reviewId"]: mapping for mapping in bundle["mapping"]}
    rows = []
    for mapping in manifest["mappings"]:
        rid = mapping["reviewId"]
        final_mapping = final_mappings[rid]
        e.require(all(final_mapping[key] == mapping[key] for key in ("id", "configuration", "sourceSha256", "promptSha256", "translationSha256"))
                  and final_mapping["rawResponsePath"] == mapping["rawResponse"]["path"]
                  and final_mapping["rawResponseSha256"] == mapping["rawResponse"]["sha256"], "provisional_final_mapping_mismatch")
        draft_path = provisional / "source-packets" / (rid + ".json")
        final_path = final_folder / "source-packets" / (rid + ".json")
        draft_raw, final_raw = draft_path.read_bytes(), final_path.read_bytes()
        e.require(e.sha(draft_raw) == e.sha(final_raw) == mapping["packetSha256"] and final_raw == e.packed(final_packets[rid]),
                  "provisional_final_packet_not_identical")
        rows.append({"reviewId": rid, "packetSha256": mapping["packetSha256"], "byteIdentical": True})
    receipt = {"version": VERSION, "status": "provisional_packet_identity_verified", "rows": rows,
        "provisionalManifestSha256": e.sha(manifest_path.read_bytes()), "formalManifestSha256": e.sha(e.packed(final_manifest)),
        "final64RunValidationPerformed": True, "packetPromotionEligible": True,
        "formalSourceReviewsApproved": False, "actualReviewerConfirmationStillRequired": True,
        "questionsEvaluated": 0, "scoresProduced": 0, "candidateSelections": 0, "humanReviewed": False}
    e.write_new(receipt_path, receipt)
    return {key: receipt[key] for key in ("version", "status", "packetPromotionEligible", "formalSourceReviewsApproved")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", type=Path)
    mode.add_argument("--verify-promotion", type=Path)
    parser.add_argument("--unit"); parser.add_argument("--destination", type=Path)
    parser.add_argument("--final-folder", type=Path); parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.run:
        if not args.unit or not args.destination:
            parser.error("--unit and --destination are required")
        result = prepare(args.run, args.unit, args.destination)
    else:
        if not args.final_folder or not args.receipt:
            parser.error("--final-folder and --receipt are required")
        result = verify_promotion(args.verify_promotion, args.final_folder, args.receipt)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
