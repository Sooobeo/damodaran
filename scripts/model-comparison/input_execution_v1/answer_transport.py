"""Record actual fresh-agent assignments and collect authored answers, without grading.

Only the operator runs this module. Question agents see one inline packet and
write one draft; they never read this module, the formal bundle or assignments.
Spawn/delivery isolation is an operator attestation, not proven by these files.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_execution_v1 import evaluation as ev
from input_execution_v1 import review_io

VERSION = "input-execution-v1-answer-transport-v1"
BASE = ".training/quality-evaluation/input-preparation-v1"
PROTOCOL = "content/model-comparison/input-execution-v1/QUESTION_REVIEW_PROTOCOL.md"
MODEL = "Codex (inherited model; exact runtime model ID unavailable)"
IDS = tuple(f"R{i:03d}" for i in range(1, 65))
EXPOSURES = ["packet_translation", "packet_questions"]
LIMITATION = ("Actual spawn, single-packet delivery and absence of prior exposure rely on "
              "the operator's actual collaboration.spawn_agent record; filesystem validation "
              "cannot independently prove agent isolation or draft authorship.")


def utc():
    return datetime.now(timezone.utc).isoformat()


def private_path(root, path):
    path = ev.rooted(root, path)
    base = Path(root) / BASE
    ev.require(path.is_relative_to(base) and path != base and
               "holdout" not in path.relative_to(base).as_posix().lower(), "private_development_path_required")
    return path


def read(path):
    """Reject duplicate JSON keys rather than silently choosing the last ID."""
    def pairs(items):
        result = {}
        for key, value in items:
            ev.require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    raw = Path(path).read_bytes()
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                       parse_constant=lambda _: ev.require(False, "nonfinite_json"))
    return value, raw


def reference(root, path, raw=None):
    path = ev.rooted(root, path)
    raw = path.read_bytes() if raw is None else raw
    return {"path": path.relative_to(root).as_posix(), "sha256": ev.sha(raw)}


def verify_refs(root, records):
    for record in records:
        ev.require(ev.sha(ev.rooted(root, record["path"]).read_bytes()) == record["sha256"],
                   "transport_evidence_changed")


def exact_files(folder, names, *, allow_missing=False):
    if allow_missing and not folder.exists():
        return []
    ev.require(folder.is_dir(), "transport_directory_required")
    entries = list(folder.iterdir())
    ev.require(all(p.is_file() and not p.is_symlink() for p in entries), "unexpected_transport_entry")
    actual = {p.name for p in entries}
    ev.require(actual <= set(names) if allow_missing else actual == set(names), "transport_file_inventory")
    return sorted(entries)


def load_formal(folder, root=ROOT):
    """Rebuild frozen S5 evidence; provisional folders cannot stand in for S5."""
    root = Path(root).resolve()
    folder = private_path(root, folder)
    manifest, manifest_raw = read(folder / "manifest.json")
    bundle, bundle_raw = read(folder / "private/bundle.json")
    expected_manifest = {"version": ev.VERSION, "status": "packets_prepared", "questionPackets": 64,
                         "sourcePackets": 64, "bundleSha256": ev.sha(ev.packed(bundle)), "sourceReviews": 0,
                         "questionAnswers": 0, "grades": 0, "translationGenerations": 0, "humanReviewed": False}
    ev.require(manifest == expected_manifest and bundle_raw == ev.packed(bundle), "formal_s5_manifest_required")
    ev.require(bundle.get("version") == ev.VERSION and bundle.get("seed") == ev.SEED and
               bundle.get("s1FreezeSha256") == ev.FREEZE_SHA and bundle.get("s2ManifestSha256") == ev.S2_SHA,
               "formal_s5_identity")
    evidence = bundle.get("evidenceFiles")
    ev.require(isinstance(evidence, list), "formal_evidence_required")
    predictions = [r for r in evidence if Path(r["path"]).name == "predictions.jsonl"]
    ev.require(len(predictions) == 1, "formal_prediction_evidence_required")
    outputs_path = ev.rooted(root, predictions[0]["path"])
    completion = ev.validate_run_completion(outputs_path, root)
    units, annotations, source_evidence = ev.load_development(root)
    prepared = ev.load_prepared(root)
    outputs = ev.read_rows(outputs_path)
    rebuilt = ev.build_review_packets(outputs, units, annotations, prepared, root)
    rebuilt["evidenceFiles"] = source_evidence + [completion, reference(root, outputs_path)]
    ev.require(bundle == rebuilt, "formal_bundle_replay_mismatch")
    refs = [reference(root, folder / "manifest.json", manifest_raw),
            reference(root, folder / "private/bundle.json", bundle_raw), *evidence]
    packets = {}
    names = [rid + ".json" for rid in IDS]
    for group in ("question", "source"):
        exact_files(folder / (group + "-packets"), names)
        expected_packets = bundle[group + "Packets"]
        ev.require(len(expected_packets) == 64 and [p["reviewId"] for p in expected_packets] == list(IDS),
                   "formal_64_packet_inventory")
        for packet in expected_packets:
            rid = packet["reviewId"]
            path = ev.rooted(root, folder / (group + "-packets") / (rid + ".json"))
            actual, raw = read(path)
            ev.require(actual == packet and raw == ev.packed(packet), "formal_packet_changed")
            refs.append(reference(root, path, raw))
            if group == "question":
                ev.require(set(packet) == {"reviewId", "translation", "questions"} and
                           isinstance(packet["translation"], str) and packet["translation"].strip(),
                           "question_packet_allowlist")
                ev.require([q["questionId"] for q in packet["questions"]] == ["q1", "q2"] and
                           all(set(q) == {"questionId", "questionKo"} and isinstance(q["questionKo"], str)
                               and q["questionKo"].strip() for q in packet["questions"]), "question_field_allowlist")
                packets[rid] = packet
    protocol = reference(root, root / PROTOCOL)
    refs.append(protocol)
    verify_refs(root, refs)
    return {"folder": folder, "packets": packets, "manifestSha256": ev.sha(manifest_raw),
            "bundleSha256": manifest["bundleSha256"], "protocol": protocol, "evidence": refs}


def reviewer(actor):
    ev.require(isinstance(actor, str) and re.fullmatch(r"/root/(?:[a-z0-9_]+/)*[a-z0-9_]+", actor),
               "actual_canonical_spawn_actor_required")
    return {"actorId": actor, "model": MODEL, "freshContext": True, "priorTaskExposure": False,
            "allowedExposures": list(EXPOSURES)}


def validate_assignment(value, rid, formal):
    required = {"version", "reviewId", "recordedAtUtc", "formalManifestSha256", "bundleSha256",
                "packetSha256", "protocol", "reviewer", "spawn", "deliveredFields", "humanReviewed",
                "isolationIndependentlyVerified", "limitation"}
    ev.require(isinstance(value, dict) and set(value) == required and value["version"] == VERSION,
               "assignment_schema")
    ev.require(rid in IDS and value["reviewId"] == rid and
               value["formalManifestSha256"] == formal["manifestSha256"] and
               value["bundleSha256"] == formal["bundleSha256"] and
               value["packetSha256"] == ev.sha(ev.packed(formal["packets"][rid])) and
               value["protocol"] == formal["protocol"], "assignment_packet_identity")
    actor = value["reviewer"]["actorId"]
    ev.require(value["reviewer"] == reviewer(actor), "assignment_exposure_provenance")
    ev.require(value["spawn"] == {"tool": "collaboration.spawn_agent", "forkTurns": "none",
                                  "actorId": actor, "source": "operator-recorded actual spawn result",
                                  "exactRuntimeModelIdAvailable": False}, "assignment_spawn_contract")
    ev.require(value["deliveredFields"] == ["reviewId", "translation", "questions"] and
               value["humanReviewed"] is False and value["isolationIndependentlyVerified"] is False and
               value["limitation"] == LIMITATION, "assignment_delivery_contract")
    when = datetime.fromisoformat(value["recordedAtUtc"])
    ev.require(when.tzinfo is not None and when.utcoffset().total_seconds() == 0, "assignment_utc_required")
    return value


def read_assignments(formal, root, complete=False):
    paths = exact_files(formal["folder"] / "assignments", [rid + ".json" for rid in IDS],
                        allow_missing=not complete)
    records, refs, actors = {}, [], set()
    for path in paths:
        path = ev.rooted(root, path)
        value, raw = read(path)
        validate_assignment(value, path.stem, formal)
        actor = value["reviewer"]["actorId"]
        ev.require(actor not in actors, "answer_context_actor_reused")
        actors.add(actor); records[path.stem] = value
        refs.append(reference(root, path, raw))
    return records, refs


def record(folder, review_id, actor, root=ROOT):
    """Call only after the real fork_turns=none spawn returned this actor ID."""
    root = Path(root).resolve()
    ev.require(review_id in IDS, "review_id_outside_fixed_64")
    provenance = reviewer(actor)
    formal = load_formal(folder, root)
    lock = ev.rooted(root, formal["folder"] / ".assignment-record.lock")
    # Exclusive file creation serializes duplicate-actor checks across processes.
    lock_stream = lock.open("xb")
    try:
        with lock_stream:
            records, existing_refs = read_assignments(formal, root)
            ev.require(review_id not in records, "assignment_must_be_new")
            ev.require(all(v["reviewer"]["actorId"] != actor for v in records.values()), "answer_context_actor_reused")
            value = {"version": VERSION, "reviewId": review_id, "recordedAtUtc": utc(),
                     "formalManifestSha256": formal["manifestSha256"], "bundleSha256": formal["bundleSha256"],
                     "packetSha256": ev.sha(ev.packed(formal["packets"][review_id])), "protocol": formal["protocol"],
                     "reviewer": provenance, "spawn": {"tool": "collaboration.spawn_agent", "forkTurns": "none",
                         "actorId": actor, "source": "operator-recorded actual spawn result", "exactRuntimeModelIdAvailable": False},
                     "deliveredFields": ["reviewId", "translation", "questions"], "humanReviewed": False,
                     "isolationIndependentlyVerified": False, "limitation": LIMITATION}
            validate_assignment(value, review_id, formal)
            verify_refs(root, formal["evidence"] + existing_refs)
            ev.write_new(formal["folder"] / "assignments" / (review_id + ".json"), value)
    finally:
        # Windows cannot delete an open file; the with block closed our lock.
        lock.unlink()
    return value


def validate_draft(draft, packet):
    ev.require(isinstance(draft, dict) and set(draft) == {"reviewId", "answers"}, "draft_schema")
    # review_io overwrites this field: check it BEFORE conversion.
    ev.require(draft["reviewId"] == packet["reviewId"], "draft_review_id_mismatch")
    ev.require(isinstance(draft["answers"], list), "draft_answers_required")
    required = {"questionId", "answerKo", "reasonKo", "translationEvidence"}
    for answer in draft["answers"]:
        ev.require(isinstance(answer, dict) and required <= set(answer) <= required | {"cannotDetermine"},
                   "draft_answer_allowlist")
        ev.require("cannotDetermine" not in answer or type(answer["cannotDetermine"]) is bool,
                   "cannot_determine_must_be_boolean")


def collect(folder, drafts, destination, root=ROOT):
    root = Path(root).resolve()
    destination = private_path(root, destination)
    ev.require(not destination.exists(), "collection_destination_must_be_new")
    formal = load_formal(folder, root)
    ev.require(not (formal["folder"] / ".assignment-record.lock").exists(), "assignment_record_in_progress")
    assignments, assignment_refs = read_assignments(formal, root, complete=True)
    drafts = private_path(root, drafts)
    paths = exact_files(drafts, [rid + ".json" for rid in IDS])
    answers, draft_refs, preserved = [], [], []
    for path in paths:
        path = ev.rooted(root, path)
        draft, raw = read(path)
        rid = path.stem
        packet, assignment = formal["packets"][rid], assignments[rid]
        validate_draft(draft, packet)
        answer = review_io.question_answer(draft, packet, assignment["reviewer"])
        answers.append(answer)
        draft_ref = reference(root, path, raw)
        draft_refs.append(draft_ref)
        assignment_path = formal["folder"] / "assignments" / (rid + ".json")
        assignment_raw = assignment_path.read_bytes()
        ev.require(ev.sha(assignment_raw) == assignment_refs[len(answers) - 1]["sha256"],
                   "assignment_changed_during_collection")
        preserved.extend([(Path("original-drafts") / path.name, raw),
                          (Path("original-assignments") / path.name, assignment_raw)])
    ev.require(len(answers) == 64 and len({a["reviewer"]["actorId"] for a in answers}) == 64,
               "all_64_actual_assignments_required")
    refs = formal["evidence"] + assignment_refs + draft_refs
    verify_refs(root, refs)
    payload = {"version": ev.VERSION, "rows": answers}
    receipt = {"version": VERSION, "status": "collected_not_frozen", "recordedAtUtc": utc(),
               "formalManifestSha256": formal["manifestSha256"], "bundleSha256": formal["bundleSha256"],
               "answerContexts": 64, "questions": 128, "answersSha256": ev.sha(ev.packed(payload)),
               "assignments": assignment_refs, "drafts": draft_refs, "formalEvidence": formal["evidence"],
               "bindings": [{"reviewId": a["reviewId"], "actorId": a["reviewer"]["actorId"],
                             "packetSha256": a["packetSha256"], "assignmentSha256": ar["sha256"],
                             "draftSha256": dr["sha256"]} for a, ar, dr in zip(answers, assignment_refs, draft_refs)],
               "answerTextModified": False, "evidenceConversion": "explicit quotes to Unicode codepoint spans only",
               "answersFrozen": False, "sourceGradingCompleted": False, "candidateSelectionPerformed": False,
               "modelCalls": 0, "humanReviewed": False, "isolationIndependentlyVerified": False, "limitation": LIMITATION}
    # All input validation precedes the first output. Exclusive mkdir refuses reuse.
    destination.mkdir(parents=True, exist_ok=False)
    for relative, raw in preserved:
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
    ev.write_new(destination / "answers.json", payload)
    ev.write_new(destination / "receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    assignment = sub.add_parser("record", help="Record an actor returned by an actual fresh spawn")
    assignment.add_argument("--folder", type=Path, required=True)
    assignment.add_argument("--review-id", required=True)
    assignment.add_argument("--actor", required=True)
    collection = sub.add_parser("collect", help="Validate and convert all 64 authored drafts; do not freeze or grade")
    collection.add_argument("--folder", type=Path, required=True)
    collection.add_argument("--drafts", type=Path, required=True)
    collection.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "record":
        result = record(args.folder, args.review_id, args.actor)
        print(json.dumps({"status": "assignment_recorded", "reviewId": result["reviewId"],
                          "actorId": result["reviewer"]["actorId"], "packetSha256": result["packetSha256"]}))
    else:
        result = collect(args.folder, args.drafts, args.destination)
        print(json.dumps({k: result[k] for k in ("status", "answerContexts", "questions", "answersSha256")}))


if __name__ == "__main__":
    main()
