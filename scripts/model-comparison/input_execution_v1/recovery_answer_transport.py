"""Explicit recovery-cohort assignment/answer transport; no answering or grading.

Only the operator runs this wrapper. It uses recovery_evaluation.load_formal,
never monkeypatches the original single-run transport, and retains its actual
spawn attestation and exact-quote validation contract.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_execution_v1 import answer_transport as tr
from input_execution_v1 import evaluation as ev
from input_execution_v1 import recovery_evaluation as reval
from input_execution_v1 import review_io

VERSION = "input-execution-v1-recovery-answer-transport-v1"


def record(folder, review_id, actor, root=ROOT):
    root = Path(root).resolve()
    ev.require(review_id in tr.IDS, "review_id_outside_fixed_64")
    reviewer = tr.reviewer(actor)
    formal = reval.load_formal(folder, root)
    lock = ev.rooted(root, formal["folder"] / ".assignment-record.lock")
    handle = lock.open("xb")
    try:
        with handle:
            records, refs = tr.read_assignments(formal, root)
            ev.require(review_id not in records, "assignment_must_be_new")
            ev.require(all(r["reviewer"]["actorId"] != actor for r in records.values()), "answer_context_actor_reused")
            # The unchanged assignment schema binds the explicit recovery S5
            # manifest SHA; its version denotes the shared transport contract.
            value = {"version": tr.VERSION, "reviewId": review_id, "recordedAtUtc": tr.utc(),
                     "formalManifestSha256": formal["manifestSha256"], "bundleSha256": formal["bundleSha256"],
                     "packetSha256": ev.sha(ev.packed(formal["packets"][review_id])), "protocol": formal["protocol"],
                     "reviewer": reviewer, "spawn": {"tool": "collaboration.spawn_agent", "forkTurns": "none",
                         "actorId": actor, "source": "operator-recorded actual spawn result", "exactRuntimeModelIdAvailable": False},
                     "deliveredFields": ["reviewId", "translation", "questions"], "humanReviewed": False,
                     "isolationIndependentlyVerified": False, "limitation": tr.LIMITATION}
            tr.validate_assignment(value, review_id, formal)
            tr.verify_refs(root, formal["evidence"] + refs)
            ev.write_new(formal["folder"] / "assignments" / (review_id + ".json"), value)
    finally:
        lock.unlink()
    return value


def collection_contents(formal, drafts, root):
    ev.require(not (formal["folder"] / ".assignment-record.lock").exists(), "assignment_record_in_progress")
    assignments, assignment_refs = tr.read_assignments(formal, root, complete=True)
    drafts = tr.private_path(root, drafts)
    paths = tr.exact_files(drafts, [rid + ".json" for rid in tr.IDS])
    answers, draft_refs, snapshots = [], [], []
    for index, path in enumerate(paths):
        path = ev.rooted(root, path)
        rid = path.stem
        draft, raw = tr.read(path)
        packet, assignment = formal["packets"][rid], assignments[rid]
        tr.validate_draft(draft, packet)  # Required before helper overwrites IDs.
        answers.append(review_io.question_answer(draft, packet, assignment["reviewer"]))
        draft_refs.append(tr.reference(root, path, raw))
        assignment_path = formal["folder"] / "assignments" / path.name
        assignment_raw = assignment_path.read_bytes()
        ev.require(ev.sha(assignment_raw) == assignment_refs[index]["sha256"], "assignment_changed_during_collection")
        snapshots.extend([(Path("original-drafts") / path.name, raw),
                          (Path("original-assignments") / path.name, assignment_raw)])
    ev.require(len(answers) == 64 and len({a["reviewer"]["actorId"] for a in answers}) == 64,
               "all_64_actual_assignments_required")
    payload = {"version": ev.VERSION, "rows": answers}
    bindings = [{"reviewId": a["reviewId"], "actorId": a["reviewer"]["actorId"], "packetSha256": a["packetSha256"],
                 "assignmentSha256": ar["sha256"], "draftSha256": dr["sha256"]}
                for a, ar, dr in zip(answers, assignment_refs, draft_refs)]
    refs = formal["evidence"] + assignment_refs + draft_refs
    tr.verify_refs(root, refs)
    return {"payload": payload, "assignments": assignment_refs, "drafts": draft_refs,
            "bindings": bindings, "snapshots": snapshots, "evidence": refs}


def receipt_for(formal, contents, recorded_at):
    return {"version": VERSION, "status": "recovery_collected_not_frozen", "recordedAtUtc": recorded_at,
            "formalManifestSha256": formal["manifestSha256"], "bundleSha256": formal["bundleSha256"],
            "recovery": deepcopy(formal["recovery"]), "answerContexts": 64, "questions": 128,
            "answersSha256": ev.sha(ev.packed(contents["payload"])), "assignments": contents["assignments"],
            "drafts": contents["drafts"], "bindings": contents["bindings"], "formalEvidence": formal["evidence"],
            "answerTextModified": False, "evidenceConversion": "explicit quotes to Unicode codepoint spans only",
            "answersFrozen": False, "sourceGradingCompleted": False, "candidateSelectionPerformed": False,
            "modelCalls": 0, "humanReviewed": False, "isolationIndependentlyVerified": False, "limitation": tr.LIMITATION}


def collect(folder, drafts, destination, root=ROOT):
    root = Path(root).resolve()
    destination = tr.private_path(root, destination)
    ev.require(not destination.exists(), "collection_destination_must_be_new")
    formal = reval.load_formal(folder, root)
    contents = collection_contents(formal, drafts, root)
    receipt = receipt_for(formal, contents, tr.utc())
    tr.verify_refs(root, contents["evidence"])
    destination.mkdir(parents=True, exist_ok=False)
    for relative, raw in contents["snapshots"]:
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
    ev.write_new(destination / "answers.json", contents["payload"])
    ev.write_new(destination / "receipt.json", receipt)
    return receipt


def validate_collection(formal, collection, root=ROOT):
    """Revalidate actual assignment/draft bindings before freeze or reporting."""
    root = Path(root).resolve()
    collection = tr.private_path(root, collection)
    receipt, receipt_raw = tr.read(collection / "receipt.json")
    ev.require(receipt.get("version") == VERSION and receipt.get("status") == "recovery_collected_not_frozen" and
               isinstance(receipt.get("drafts"), list) and len(receipt["drafts"]) == 64, "recovery_collection_required")
    tr.verify_refs(root, [*receipt["formalEvidence"], *receipt["assignments"], *receipt["drafts"]])
    draft_folder = ev.rooted(root, receipt["drafts"][0]["path"]).parent
    contents = collection_contents(formal, draft_folder, root)
    when = datetime.fromisoformat(receipt["recordedAtUtc"])
    ev.require(when.tzinfo is not None and when.utcoffset().total_seconds() == 0, "collection_utc_required")
    ev.require(receipt == receipt_for(formal, contents, receipt["recordedAtUtc"]), "recovery_collection_binding_changed")
    answers_raw = (collection / "answers.json").read_bytes()
    ev.require(answers_raw == ev.packed(contents["payload"]) and ev.sha(answers_raw) == receipt["answersSha256"],
               "recovery_collected_answers_changed")
    refs = [*contents["evidence"], tr.reference(root, collection / "receipt.json", receipt_raw),
            tr.reference(root, collection / "answers.json", answers_raw)]
    for name in ("original-drafts", "original-assignments"):
        tr.exact_files(collection / name, [rid + ".json" for rid in tr.IDS])
    for relative, expected_raw in contents["snapshots"]:
        path = ev.rooted(root, collection / relative)
        raw = path.read_bytes()
        ev.require(raw == expected_raw, "recovery_original_answer_snapshot_changed")
        refs.append(tr.reference(root, path, raw))
    tr.verify_refs(root, refs)
    return contents["payload"]["rows"], refs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    assignment = sub.add_parser("record")
    assignment.add_argument("--folder", type=Path, required=True); assignment.add_argument("--review-id", required=True)
    assignment.add_argument("--actor", required=True)
    collection = sub.add_parser("collect")
    collection.add_argument("--folder", type=Path, required=True); collection.add_argument("--drafts", type=Path, required=True)
    collection.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "record":
        result = record(args.folder, args.review_id, args.actor)
        print(json.dumps({"status": "recovery_assignment_recorded", "reviewId": result["reviewId"],
                          "actorId": result["reviewer"]["actorId"], "packetSha256": result["packetSha256"]}))
    else:
        result = collect(args.folder, args.drafts, args.destination)
        print(json.dumps({k: result[k] for k in ("status", "answerContexts", "questions", "answersSha256")}))


if __name__ == "__main__":
    main()
