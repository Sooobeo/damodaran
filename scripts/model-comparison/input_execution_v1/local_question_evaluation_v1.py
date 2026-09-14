"""Explicit local-native answer freeze/report over unchanged recovery-v2 packets.

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
from input_execution_v1 import local_question_transport_v1 as local_transport
from input_execution_v1 import recovery_evaluation_v2 as recovery

VERSION = "input-execution-v1-local-question-evaluation-v1"
PROTOCOL = "content/model-comparison/input-execution-v1/LOCAL_QUESTION_REVIEW_PROTOCOL_V1.md"
CODE_FILES = tuple("scripts/model-comparison/input_execution_v1/" + name for name in (
    "local_question_contract_v1.py", "local_question_runner_v1.py", "local_question_transport_v1.py",
    "local_question_evaluation_v1.py", "local_question_append_evidence_v1.py"))
cohort_manifest_path = recovery.cohort_manifest_path
validate_cohort = recovery.validate_cohort


def validate_collection(directory, formalPath, root=ROOT):
    from input_execution_v1 import local_question_transport_v1 as transport
    return transport.validate_collection(directory, formalPath, root=root)


def load_formal(folder, root=ROOT):
    """Preserve the v2 bundle; add local procedure/code evidence separately."""
    root = Path(root).resolve()
    folder = local_transport.private_path(root, folder)
    formal = recovery.load_formal(folder, root)
    protocol = tr.reference(root, root / PROTOCOL)
    code = [tr.reference(root, root / name) for name in CODE_FILES]
    formal["questionProtocol"], formal["questionCodeFiles"] = protocol, code
    formal["evidence"] = [*formal["evidence"], protocol, *code]
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
        "nativeProcessPerPacket": True, "sourceGradingCompleted": False, "humanReviewed": False}
    tr.verify_refs(root, [*formal["evidence"], *refs])
    ev.write_new(folder / "answers-frozen.json", payload)
    ev.write_new(folder / "answers-freeze.json", receipt)
    answers = {row["reviewId"]: row for row in rows}
    for packet in formal["bundle"]["sourcePackets"]:
        answer = answers[packet["reviewId"]]
        ev.write_new(folder / "grade-packets" / (packet["reviewId"] + ".json"),
                     {**packet, "answer": answer, "answerSha256": ev.sha(ev.packed(answer))})
    return receipt


def read_frozen_answers(formal, root):
    folder = local_transport.private_path(root, formal["folder"])
    receipt, raw = tr.read(folder / "answers-freeze.json")
    expected = {"version": VERSION, "status": "local_native_answers_frozen", "answerContexts": 64,
        "questions": 128, "formalManifestSha256": formal["manifestSha256"], "bundleSha256": formal["bundleSha256"],
        "recovery": formal["recovery"], "questionProtocol": formal["questionProtocol"],
        "questionCodeFiles": formal["questionCodeFiles"], "nativeProcessPerPacket": True,
        "sourceGradingCompleted": False, "humanReviewed": False}
    ev.require(all(receipt.get(k) == v for k, v in expected.items()), "local_answer_freeze_contract")
    tr.verify_refs(root, [receipt["collection"], *receipt["collectionEvidence"]])
    collection = ev.rooted(root, receipt["collection"]["path"]).parent
    rows, metadata, collection_refs = checked_collection(formal, collection, root)
    ev.require(receipt["collectionMetadata"] == metadata and receipt["collectionEvidence"] == collection_refs and
               receipt["freshContextActorIds"] == [r["reviewer"]["actorId"] for r in rows], "local_frozen_collection_changed")
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
    return rows, refs


def report_recovery_evidence(formal, evidence, answers):
    return {"wrapperVersion": VERSION, "formalManifestSha256": formal["manifestSha256"],
        **formal["recovery"], "evidenceFiles": evidence, "singleRunCompletionClaimed": False,
        "ledgerAppendPerformed": False, "questionAnswering": {"protocol": formal["questionProtocol"],
            "method": "one fresh owned native process per packet", "plannedContexts": 64,
            "validatedContexts": len(answers), "models": sorted({row["reviewer"]["model"] for row in answers}),
            "allAnswersValidatedAndFrozen": len(answers) == 64, "humanReviewed": False}}


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
    answers = []
    if grades is not None or (formal["folder"] / "answers-freeze.json").exists():
        answers, answer_refs = read_frozen_answers(formal, root); refs.extend(answer_refs)
    result = ev.aggregate(formal["bundle"], source_rows, answers, grade_rows)
    result["recoveryEvidence"] = report_recovery_evidence(formal, refs, answers)
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
