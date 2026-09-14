"""Formal question-only export and honest fresh native-context collection."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_execution_v1 import answer_transport as tr
from input_execution_v1 import evaluation as ev
from input_execution_v1 import local_question_contract_v1 as contract
from input_execution_v1 import recovery_evaluation_v2 as reval
from input_execution_v1 import review_io

VERSION = "input-execution-v1-local-question-transport-v1"
EXPORT_VERSION = "input-execution-v1-local-question-export-v1"
PROTOCOL = "content/model-comparison/input-execution-v1/LOCAL_QUESTION_REVIEW_PROTOCOL_V1.md"
FREEZE = "content/model-comparison/input-execution-v1/local-question-execution-freeze-v1.json"
private_path = contract.private_path
LIMITATION = ("One fresh owned native process per packet, with one completion and no retained previous "
             "context. This is a local Qwen reading evaluation, not a collaboration.spawn_agent run, "
             "a human reading study, or certified evaluator accuracy. Code/process/wire provenance "
             "supports isolation; it cannot prove absence of pretraining exposure.")


def export_questions(formal_path, destination, root=ROOT):
    root = Path(root).resolve()
    formal = reval.load_formal(private_path(root, formal_path), root)
    destination = private_path(root, destination)
    ev.require(not destination.exists(), "local_question_export_must_be_new")
    protocol, freeze = tr.reference(root, root / PROTOCOL), tr.reference(root, root / FREEZE)
    freeze_data, _ = tr.read(root / FREEZE)
    contract.validate_freeze(root, freeze_data)
    files = []
    for rid in contract.IDS:
        packet = contract.validate_packet(formal["packets"][rid])
        raw = ev.packed(packet)
        files.append({"reviewId": rid, "path": rid + ".json", "sha256": ev.sha(raw)})
    manifest = {"version": EXPORT_VERSION, "questionPackets": 64, "packetFiles": files,
                "formalManifestSha256": formal["manifestSha256"], "bundleSha256": formal["bundleSha256"],
                "protocol": protocol, "executionFreeze": freeze}
    tr.verify_refs(root, [*formal["evidence"], protocol, freeze])
    destination.mkdir(parents=True, exist_ok=False)
    for rid in contract.IDS:
        ev.write_new(destination / (rid + ".json"), formal["packets"][rid])
    ev.write_new(destination / "manifest.json", manifest)
    return manifest


def verify_export(formal, directory, root):
    directory = private_path(root, directory)
    tr.exact_files(directory, ["manifest.json", *[rid + ".json" for rid in contract.IDS]])
    value, raw = tr.read(directory / "manifest.json")
    expected = {"version": EXPORT_VERSION, "questionPackets": 64,
                "formalManifestSha256": formal["manifestSha256"], "bundleSha256": formal["bundleSha256"],
                "protocol": tr.reference(root, root / PROTOCOL), "executionFreeze": tr.reference(root, root / FREEZE),
                "packetFiles": [{"reviewId": rid, "path": rid + ".json",
                                 "sha256": ev.sha(ev.packed(formal["packets"][rid]))} for rid in contract.IDS]}
    ev.require(value == expected and raw == ev.packed(expected), "local_question_export_binding")
    refs = [tr.reference(root, directory / "manifest.json", raw)]
    frozen, _ = tr.read(root / FREEZE)
    contract.validate_freeze(root, frozen)
    refs.extend([expected["protocol"], expected["executionFreeze"], *frozen["files"]])
    for rid in contract.IDS:
        packet = contract.validate_packet(formal["packets"][rid])
        path = directory / (rid + ".json")
        packet_raw = path.read_bytes()
        ev.require(packet_raw == ev.packed(packet), "local_question_export_packet_changed")
        refs.append(tr.reference(root, path, packet_raw))
    return value, refs


def reviewer(context):
    cid = context["contextId"]
    ev.require(isinstance(cid, str) and cid and not cid.startswith("/root/"), "native_context_id_required")
    return {"actorId": "local-native:" + cid, "model": contract.MODEL_DESCRIPTION,
            "freshContext": True, "priorTaskExposure": False,
            "allowedExposures": ["packet_translation", "packet_questions"]}


def contents(formal, export, run, root):
    from input_execution_v1 import local_question_runner_v1 as runner
    exported, export_refs = verify_export(formal, export, root)
    result = runner.validate_run(run, export, root=root)
    contexts = result["contexts"]
    ev.require(isinstance(contexts, list) and [r["reviewId"] for r in contexts] == list(contract.IDS)
               and len({r["contextId"] for r in contexts}) == 64, "all_64_fresh_native_contexts_required")
    rows, bindings = [], []
    for context in contexts:
        rid = context["reviewId"]
        packet, draft = formal["packets"][rid], context["draft"]
        ev.require(context["packetSha256"] == ev.sha(ev.packed(packet)), "native_formal_packet_mismatch")
        tr.validate_draft(draft, packet)
        answer = review_io.question_answer(draft, packet, reviewer(context))
        rows.append(answer)
        bindings.append({"reviewId": rid, "actorId": answer["reviewer"]["actorId"],
                         "contextId": context["contextId"], "packetSha256": context["packetSha256"],
                         "draftSha256": ev.sha(ev.packed(draft)), "process": context["process"]})
    refs = [*formal["evidence"], *export_refs, *result["evidence"]]
    tr.verify_refs(root, refs)
    return {"payload": {"version": ev.VERSION, "rows": rows}, "bindings": bindings,
            "evidence": refs, "nativeSummary": result["summary"], "exportManifest": exported}


def receipt_for(formal, built, export, run, when, root):
    return {"version": VERSION, "status": "local_question_collected_not_frozen", "recordedAtUtc": when,
            "formalManifestSha256": formal["manifestSha256"], "bundleSha256": formal["bundleSha256"],
            "export": private_path(root, export).relative_to(root).as_posix(),
            "run": private_path(root, run).relative_to(root).as_posix(),
            "answersSha256": ev.sha(ev.packed(built["payload"])), "bindings": built["bindings"],
            "evidence": built["evidence"], "nativeSummary": built["nativeSummary"],
            "answerContexts": 64, "questions": 128, "completionRequestsSent": 64,
            "model": contract.MODEL_DESCRIPTION, "modelSha256": contract.MODEL_SHA,
            "method": "one-fresh-owned-native-process-per-question-packet",
            "actualCollaborationSpawns": 0, "priorTaskExposure": False,
            "deliveredFields": ["reviewId", "translation", "questions"],
            "answerTextModified": False, "structuralConversion": "q1/q2 object keys to questionId list; verbatim quotes to codepoint spans",
            "answersFrozen": False, "sourceGradingCompleted": False, "candidateSelectionPerformed": False,
            "humanReviewed": False, "limitation": LIMITATION}


def collect(formal_path, export, run, destination, root=ROOT):
    root = Path(root).resolve()
    formal = reval.load_formal(private_path(root, formal_path), root)
    destination = private_path(root, destination)
    ev.require(not destination.exists(), "local_question_collection_must_be_new")
    built = contents(formal, export, run, root)
    receipt = receipt_for(formal, built, export, run, datetime.now(timezone.utc).isoformat(), root)
    tr.verify_refs(root, built["evidence"])
    destination.mkdir(parents=True, exist_ok=False)
    ev.write_new(destination / "answers.json", built["payload"])
    ev.write_new(destination / "receipt.json", receipt)
    return receipt


def validate_collection(directory, formalPath, root=ROOT):
    root = Path(root).resolve()
    formal = reval.load_formal(private_path(root, formalPath), root)
    directory = private_path(root, directory)
    tr.exact_files(directory, ["answers.json", "receipt.json"])
    receipt, raw = tr.read(directory / "receipt.json")
    ev.require(receipt.get("version") == VERSION, "local_question_collection_version")
    when = datetime.fromisoformat(receipt["recordedAtUtc"])
    ev.require(when.tzinfo is not None and when.utcoffset().total_seconds() == 0, "collection_utc_required")
    built = contents(formal, receipt["export"], receipt["run"], root)
    expected = receipt_for(formal, built, receipt["export"], receipt["run"], receipt["recordedAtUtc"], root)
    ev.require(receipt == expected and raw == ev.packed(expected), "local_question_collection_replay_changed")
    answer_raw = (directory / "answers.json").read_bytes()
    ev.require(answer_raw == ev.packed(built["payload"]), "local_question_collected_answers_changed")
    refs = [*built["evidence"], tr.reference(root, directory / "receipt.json", raw),
            tr.reference(root, directory / "answers.json", answer_raw)]
    tr.verify_refs(root, refs)
    return {"rows": built["payload"]["rows"], "evidence": refs, "receipt": receipt}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("export")
    p.add_argument("--formal", type=Path, required=True); p.add_argument("--destination", type=Path, required=True)
    p = sub.add_parser("collect")
    for arg in ("formal", "export", "run", "destination"):
        p.add_argument("--" + arg, type=Path, required=True)
    p = sub.add_parser("validate")
    p.add_argument("--formal", type=Path, required=True); p.add_argument("--collection", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "export":
        result = export_questions(args.formal, args.destination)
        print({"status": "question_only_exported", "packets": result["questionPackets"]})
    elif args.command == "collect":
        result = collect(args.formal, args.export, args.run, args.destination)
        print({"status": result["status"], "answerContexts": result["answerContexts"]})
    else:
        result = validate_collection(args.collection, args.formal)
        print({"status": "local_question_collection_validated", "answerContexts": len(result["rows"])})


if __name__ == "__main__":
    main()
