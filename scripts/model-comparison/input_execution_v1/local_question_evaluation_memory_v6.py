"""Explicit memory-v6 answer freeze/report over unchanged recovery-v2 packets.

The full memory-v6 identity and prior v5 freeze travel inside partialRecovery;
per-context provenance preserves retained v4 and fresh v6 producers.

The controller may read the formal bundle. The native model runner must consume
only the separately validated question-only export, never this module.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_execution_v1 import evaluation as ev
from input_execution_v1 import answer_transport as tr
from input_execution_v1 import local_question_transport_memory_v6 as local_transport
from input_execution_v1 import local_question_evaluation_battery_v4 as baseline
from input_execution_v1 import recovery_evaluation_v2 as recovery

VERSION = "input-execution-v1-local-question-evaluation-memory-v6"
PROTOCOL = "content/model-comparison/input-execution-v1/LOCAL_QUESTION_REVIEW_PROTOCOL_V1.md"
CODE_FILES = tuple("scripts/model-comparison/input_execution_v1/" + name for name in (
    "local_question_contract_v1.py", "local_question_runner_v1.py", "local_question_transport_v1.py",
    "local_question_evaluation_v1.py", "local_question_append_evidence_v1.py"))
cohort_manifest_path = recovery.cohort_manifest_path
validate_cohort = recovery.validate_cohort


def validate_collection(directory, formalPath, root=ROOT):
    from input_execution_v1 import local_question_transport_memory_v6 as transport
    return transport.validate_collection(directory, formalPath, root=root)


def load_formal(folder, root=ROOT):
    """Keep frozen v1-v4 provenance and bind the explicit 9+55 partial recovery."""
    root = Path(root).resolve()
    formal = baseline.load_formal(local_transport.private_path(root, folder), root)
    partial = local_transport.load_partial_recovery(root)
    ev.require(partial["batteryExecution"] == formal["batteryExecution"], "partial_battery_lineage_mismatch")
    formal["partialRecovery"] = partial
    formal["evidence"] = [*formal["evidence"], *local_transport.partial_recovery_refs(partial)]
    tr.verify_refs(root, formal["evidence"])
    return formal


def checked_collection(formal, directory, root):
    directory = local_transport.private_path(root, directory)
    result = validate_collection(directory, formal["folder"], root=root)
    ev.require(isinstance(result, dict) and set(result) == {"rows", "evidence", "receipt"},
               "local_collection_validator_contract")
    receipt_path = directory / "receipt.json"
    receipt, raw = tr.read(receipt_path)
    ev.require(receipt == result["receipt"], "local_collection_receipt_changed")
    ev.require(receipt.get("inventoryCorrection") == formal["inventoryCorrection"] and
               receipt.get("runnerValidatorVersion") == local_transport.RUNNER_VERSION,
               "inventory_collection_lineage_mismatch")
    ev.require(receipt.get("zeroCallRecovery") == formal["zeroCallRecovery"], "zero_call_collection_lineage_mismatch")
    ev.require(receipt.get("batteryExecution") == formal["batteryExecution"] and
               receipt.get("contextProducerVersion") == local_transport.CONTEXT_VERSION, "battery_collection_lineage_mismatch")
    ev.require(receipt.get("partialRecovery") == formal["partialRecovery"], "partial_collection_lineage_mismatch")
    expected_provenance = local_transport.question_provenance(receipt["nativeSummary"], receipt["bindings"], formal["partialRecovery"])
    ev.require(receipt.get("questionProvenance") == expected_provenance and
               receipt.get("singleQuestionRunCompletionClaimed") is False, "partial_collection_provenance_mismatch")
    rows = result["rows"]
    ev.require(isinstance(rows, list) and [row["reviewId"] for row in rows] == list(tr.IDS),
               "local_all_64_ordered_answers_required")
    actors, models = [], []
    for row in rows:
        ev.validate_answer(row, formal["packets"][row["reviewId"]])
        actors.append(row["reviewer"]["actorId"])
        models.append(row["reviewer"]["model"])
    ev.require(all(isinstance(v, str) and v.strip() for v in actors + models) and
               len(set(actors)) == 64 and len(set(models)) == 1, "local_contexts_unique_same_model_required")
    evidence = result["evidence"]
    ev.require(isinstance(evidence, list) and evidence and
               all(isinstance(record, dict) and set(record) >= {"path", "sha256"} for record in evidence),
               "local_collection_evidence_required")
    refs = [*evidence, tr.reference(root, receipt_path, raw)]
    tr.verify_refs(root, [*formal["evidence"], *refs])
    return rows, receipt, refs


def freeze_answers(folder, collection, root=ROOT):
    root = Path(root).resolve()
    formal = load_formal(folder, root)
    folder = formal["folder"]
    ev.require(not any((folder / name).exists() for name in ("answers-frozen.json", "answers-freeze.json", "grade-packets")),
               "local_answers_freeze_must_be_new")
    rows, collection_receipt, refs = checked_collection(formal, collection, root)
    payload = {"version": ev.VERSION, "rows": rows}
    receipt = {"version": VERSION, "status": "local_native_answers_frozen",
        "answersSha256": ev.sha(ev.packed(payload)), "formalManifestSha256": formal["manifestSha256"],
        "bundleSha256": formal["bundleSha256"], "recovery": formal["recovery"],
        "answerContexts": 64, "questions": 128, "freshContextActorIds": [r["reviewer"]["actorId"] for r in rows],
        "collection": tr.reference(root, local_transport.private_path(root, collection) / "receipt.json"),
        "collectionMetadata": deepcopy(collection_receipt), "collectionEvidence": refs,
        "questionProtocol": formal["questionProtocol"], "questionCodeFiles": formal["questionCodeFiles"],
        "inventoryCorrection": formal["inventoryCorrection"], "zeroCallRecovery": formal["zeroCallRecovery"],
        "batteryExecution": formal["batteryExecution"], "contextProducerVersion": local_transport.CONTEXT_VERSION,
        "partialRecovery": formal["partialRecovery"],
        "nativeProcessPerPacket": True, "sourceGradingCompleted": False, "humanReviewed": False,
        "questionProvenance": deepcopy(collection_receipt["questionProvenance"])}
    tr.verify_refs(root, [*formal["evidence"], *refs])
    ev.write_new(folder / "answers-frozen.json", payload)
    ev.write_new(folder / "answers-freeze.json", receipt)
    answers = {row["reviewId"]: row for row in rows}
    for packet in formal["bundle"]["sourcePackets"]:
        answer = answers[packet["reviewId"]]
        ev.write_new(folder / "grade-packets" / (packet["reviewId"] + ".json"),
                     {**packet, "answer": answer, "answerSha256": ev.sha(ev.packed(answer))})
    return receipt


def read_frozen_answers_with_provenance(formal, root):
    """Return validated answers, refs and explicit original/new QA-run provenance."""
    folder = local_transport.private_path(root, formal["folder"])
    receipt, raw = tr.read(folder / "answers-freeze.json")
    expected = {"version": VERSION, "status": "local_native_answers_frozen", "answerContexts": 64,
        "questions": 128, "formalManifestSha256": formal["manifestSha256"], "bundleSha256": formal["bundleSha256"],
        "recovery": formal["recovery"], "questionProtocol": formal["questionProtocol"],
        "questionCodeFiles": formal["questionCodeFiles"], "inventoryCorrection": formal["inventoryCorrection"], "zeroCallRecovery": formal["zeroCallRecovery"],
        "batteryExecution": formal["batteryExecution"], "contextProducerVersion": local_transport.CONTEXT_VERSION,
        "partialRecovery": formal["partialRecovery"],
        "nativeProcessPerPacket": True,
        "sourceGradingCompleted": False, "humanReviewed": False}
    ev.require(all(receipt.get(k) == v for k, v in expected.items()), "local_answer_freeze_contract")
    tr.verify_refs(root, [receipt["collection"], *receipt["collectionEvidence"]])
    collection = ev.rooted(root, receipt["collection"]["path"]).parent
    rows, metadata, collection_refs = checked_collection(formal, collection, root)
    ev.require(receipt["collectionMetadata"] == metadata and receipt["collectionEvidence"] == collection_refs and
               receipt["freshContextActorIds"] == [r["reviewer"]["actorId"] for r in rows], "local_frozen_collection_changed")
    ev.require(receipt.get("questionProvenance") == metadata["questionProvenance"], "partial_frozen_provenance_mismatch")
    answer_raw = (folder / "answers-frozen.json").read_bytes()
    ev.require(answer_raw == ev.packed({"version": ev.VERSION, "rows": rows}) and
               ev.sha(answer_raw) == receipt["answersSha256"], "local_frozen_answers_changed")
    refs = [tr.reference(root, folder / "answers-freeze.json", raw),
            tr.reference(root, folder / "answers-frozen.json", answer_raw), *collection_refs]
    tr.exact_files(folder / "grade-packets", [rid + ".json" for rid in tr.IDS])
    answers = {row["reviewId"]: row for row in rows}
    for packet in formal["bundle"]["sourcePackets"]:
        answer = answers[packet["reviewId"]]
        expected_packet = {**packet, "answer": answer, "answerSha256": ev.sha(ev.packed(answer))}
        path = folder / "grade-packets" / (packet["reviewId"] + ".json")
        actual = ev.rooted(root, path).read_bytes()
        ev.require(actual == ev.packed(expected_packet), "local_grade_packet_changed")
        refs.append(tr.reference(root, path, actual))
    tr.verify_refs(root, [*formal["evidence"], *refs])
    return rows, refs, deepcopy(metadata["questionProvenance"])


def read_frozen_answers(formal, root):
    rows, refs, _ = read_frozen_answers_with_provenance(formal, root)
    return rows, refs


def report_recovery_evidence(formal, evidence, answers, question_provenance=None):
    ev.require((not answers and question_provenance is None) or (len(answers) == 64 and question_provenance is not None),
               "partial_report_question_provenance_required")
    if question_provenance is not None:
        local_transport.validate_question_provenance(question_provenance, formal["partialRecovery"])
    value = baseline.report_recovery_evidence(formal, evidence, answers)
    return {**value, "wrapperVersion": VERSION, "inventoryCorrection": deepcopy(formal["inventoryCorrection"]),
            "zeroCallRecovery": deepcopy(formal["zeroCallRecovery"]), "batteryExecution": deepcopy(formal["batteryExecution"]),
            "contextProducerVersion": local_transport.CONTEXT_VERSION, "coordinatorVersion": local_transport.RUNNER_VERSION,
            "partialRecovery": deepcopy(formal["partialRecovery"]), "questionProvenance": deepcopy(question_provenance)}


def report(folder, output, source_reviews=None, grades=None, root=ROOT):
    root = Path(root).resolve(); output = local_transport.private_path(root, output)
    ev.require(not output.exists(), "local_report_must_be_new")
    formal = load_formal(folder, root)
    refs = list(formal["evidence"])
    def authored(path):
        if path is None:
            return []
        path = local_transport.private_path(root, path)
        value, raw = tr.read(path); refs.append(tr.reference(root, path, raw))
        return value if isinstance(value, list) else value["rows"]
    source_rows, grade_rows = authored(source_reviews), authored(grades)
    answers = []; provenance = None
    if grades is not None or (formal["folder"] / "answers-freeze.json").exists():
        answers, answer_refs, provenance = read_frozen_answers_with_provenance(formal, root); refs.extend(answer_refs)
    result = ev.aggregate(formal["bundle"], source_rows, answers, grade_rows)
    result["recoveryEvidence"] = report_recovery_evidence(formal, refs, answers, provenance)
    tr.verify_refs(root, refs)
    ev.write_new(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze-answers")
    freeze.add_argument("--folder", type=Path, required=True); freeze.add_argument("--collection", type=Path, required=True)
    summary = sub.add_parser("report")
    summary.add_argument("--folder", type=Path, required=True); summary.add_argument("--output", type=Path, required=True)
    summary.add_argument("--source-reviews", type=Path); summary.add_argument("--grades", type=Path)
    args = parser.parse_args()
    value = (freeze_answers(args.folder, args.collection) if args.command == "freeze-answers" else
             report(args.folder, args.output, args.source_reviews, args.grades))
    print(__import__("json").dumps({k: value[k] for k in value if k in ("version", "status", "answerContexts", "questions", "counts")}))


if __name__ == "__main__":
    main()


