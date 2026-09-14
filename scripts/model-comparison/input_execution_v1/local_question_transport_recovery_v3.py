"""Explicit zero-call recovery-v3 runner collection over byte-unchanged v1 exports.

Lifecycle methods are explicit static copies of frozen v1; no module mutation.
The v1 export verifier and reviewer contract are reused without adaptation.
"""
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
from input_execution_v1 import local_question_transport_v1 as baseline

VERSION = "input-execution-v1-local-question-transport-zero-call-recovery-v3"
RUNNER_VERSION = "input-execution-v1-local-question-runner-zero-call-recovery-v3"
EXPORT_VERSION = "input-execution-v1-local-question-export-v1"
PROTOCOL = "content/model-comparison/input-execution-v1/LOCAL_QUESTION_REVIEW_PROTOCOL_V1.md"
FREEZE = "content/model-comparison/input-execution-v1/local-question-execution-freeze-v1.json"
private_path = contract.private_path
LIMITATION = ("One fresh owned native process per packet, with one completion and no retained previous "
             "context. This is a local Qwen reading evaluation, not a collaboration.spawn_agent run, "
             "a human reading study, or certified evaluator accuracy. Code/process/wire provenance "
             "supports isolation; it cannot prove absence of pretraining exposure.")


export_questions = baseline.export_questions
verify_export = baseline.verify_export
reviewer = baseline.reviewer


def load_correction(root=ROOT):
    from input_execution_v1 import local_question_runner_inventory_v2 as runner
    return runner.load_correction(root)


def correction_refs(identity):
    return [identity["baseExecutionFreeze"], identity["correctionFreeze"], *identity["files"]]


def load_recovery(root=ROOT):
    from input_execution_v1 import local_question_runner_recovery_v3 as runner
    return runner.load_recovery(root)


def recovery_refs(identity):
    from input_execution_v1 import local_question_runner_recovery_v3 as runner
    return runner.recovery_refs(identity)


def contents(formal, export, run, root):
    from input_execution_v1 import local_question_runner_recovery_v3 as runner
    exported, export_refs = verify_export(formal, export, root)
    identity = load_correction(root)
    zero_call = load_recovery(root)
    ev.require(zero_call["priorRun"]["exportManifest"] == export_refs[0], "recovery_prior_export_changed")
    result = runner.validate_run(run, export, root=root)
    ev.require(result["summary"].get("version") == RUNNER_VERSION and
               result["summary"].get("inventoryCorrection") == identity, "inventory_runner_lineage_mismatch")
    ev.require(result["summary"].get("zeroCallRecovery") == zero_call, "zero_call_runner_lineage_mismatch")
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
    refs = [*formal["evidence"], *export_refs, *result["evidence"], *correction_refs(identity), *recovery_refs(zero_call)]
    tr.verify_refs(root, refs)
    return {"payload": {"version": ev.VERSION, "rows": rows}, "bindings": bindings,
            "evidence": refs, "nativeSummary": result["summary"], "exportManifest": exported,
            "inventoryCorrection": identity, "zeroCallRecovery": zero_call}


def receipt_for(formal, built, export, run, when, root):
    return {"version": VERSION, "status": "local_question_collected_not_frozen", "recordedAtUtc": when,
            "formalManifestSha256": formal["manifestSha256"], "bundleSha256": formal["bundleSha256"],
            "export": private_path(root, export).relative_to(root).as_posix(),
            "run": private_path(root, run).relative_to(root).as_posix(),
            "answersSha256": ev.sha(ev.packed(built["payload"])), "bindings": built["bindings"],
            "inventoryCorrection": built["inventoryCorrection"], "runnerValidatorVersion": RUNNER_VERSION,
            "zeroCallRecovery": built["zeroCallRecovery"],
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
