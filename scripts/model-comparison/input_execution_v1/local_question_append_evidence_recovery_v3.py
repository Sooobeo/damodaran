"""Prepare zero-call recovery-v3 local QA observations; append only with --append.

Reuse the frozen ledger identity, streaming evidence graph and idempotent writer,
but never its single-run loader or event builder. Original failed rows retain
failed run status even when their individual output is technically completed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_execution_v1 import append_evidence as original
from input_execution_v1 import answer_transport as transport
from input_execution_v1 import local_question_transport_recovery_v3 as local_transport
from input_execution_v1 import evaluation as ev
from input_execution_v1 import local_question_evaluation_recovery_v3 as reval
from input_execution_v1 import recovery_relations_v2 as relations

VERSION = "input-execution-v1-local-question-ledger-evidence-zero-call-recovery-v3"
ledger = original.ledger
CODE_PATH = "scripts/model-comparison/input_execution_v1/local_question_append_evidence_recovery_v3.py"
RELATION_CODE = "scripts/model-comparison/input_execution_v1/recovery_relations_v2.py"
COHORT_CODE = "scripts/model-comparison/input_execution_v1/recovery_cohort_v2.py"


def simple_ref(graph, path):
    value = graph.ref(path)
    return {"path": value["path"], "sha256": value["sha256"]}


def revalidate_report(formal, source_rows, answers, grade_rows, report, source_ref, grades_ref, answer_refs):
    original.require_complete_report(report)
    expected = ev.aggregate(formal["bundle"], source_rows, answers, grade_rows)
    expected["recoveryEvidence"] = reval.report_recovery_evidence(
        formal, [*formal["evidence"], source_ref, grades_ref, *answer_refs], answers)
    ev.require(report == expected, "recovery_report_not_reproducible_from_judgments")


def revalidate_relations(report, output_rows, validated_cohort):
    expected = relations.diagnose_rows(output_rows)
    ev.require(all(report.get(k) == v for k, v in expected.items()), "recovery_relations_not_reproducible")
    ev.require(report["cohortManifest"] == validated_cohort["manifest"] and
               report["cohortEvidence"] == validated_cohort["evidence"] and report["originalAttemptStatus"] == "failed" and
               report["retainedOutputs"] == 38 and report["recoveredOutputs"] == 26 and
               report["completionRequestsSent"] == 65 and report["interruptedRequests"] == 1,
               "recovery_relation_cohort_evidence_changed")


def validate_final_evidence(*, folder, cohort, source_reviews, grades, report_path, relation_folder, root=ROOT):
    graph = original.EvidenceGraph(root)
    # This verifies the same ten files and historic ledger/checker SHA; it does
    # not invoke the original single-run completion path.
    original.verify_evaluation_freeze(graph)
    formal = reval.load_formal(folder, graph.root)
    cohort_path = reval.cohort_manifest_path(cohort, graph.root)
    cohort_ref = formal["recovery"]["cohortManifest"]
    ev.require(graph.key(cohort_path) == cohort_ref["path"], "recovery_append_wrong_cohort")
    graph.verify(cohort_path, cohort_ref["sha256"])
    for record in formal["evidence"]:
        graph.verify(record["path"], record["sha256"])
    answers, answer_refs = reval.read_frozen_answers(formal, graph.root)
    for record in answer_refs:
        graph.verify(record["path"], record["sha256"])
    source_reviews, grades, report_path, relation_folder = (
        local_transport.private_path(graph.root, path) for path in (source_reviews, grades, report_path, relation_folder))
    source_rows, grade_rows = graph.rows(source_reviews), graph.rows(grades)
    report = graph.json(report_path)
    revalidate_report(formal, source_rows, answers, grade_rows, report,
                      simple_ref(graph, source_reviews), simple_ref(graph, grades), answer_refs)
    # Relations retain their complete validator receipt (including separately
    # streamed input identities), so compare against the explicit validator API.
    cohort_validation = reval.validate_cohort(cohort_path, root=graph.root)
    ev.require(cohort_validation["rows"] == formal["cohort"]["rows"] and
               cohort_validation["manifest"] == formal["cohort"]["manifest"], "recovery_cohort_changed_during_append_validation")
    relation_manifest = graph.json(relation_folder / "manifest.json")
    relation_path = relation_folder / "relation-report.json"
    relation_report = graph.json(relation_path, relation_manifest["reportSha256"])
    expected_manifest = {"version": relations.VERSION, "status": "diagnosed", "outputs": 64,
                         "reportFile": "relation-report.json", "reportSha256": simple_ref(graph, relation_path)["sha256"],
                         "checker": relation_report["checker"], "modelCalls": 0, "translationGenerations": 0, "ledgerWrites": 0}
    ev.require(relation_manifest == expected_manifest, "recovery_relation_manifest_inventory")
    revalidate_relations(relation_report, formal["cohort"]["rows"], cohort_validation)
    ev.require(relation_report.get("evaluationLineage") == formal["recovery"]["evaluationLineage"],
               "recovery_v2_relation_evaluation_lineage")
    graph.verify(RELATION_CODE, relation_report["wrapperCodeSha256"])
    graph.verify(COHORT_CODE, relation_report["cohortValidatorCodeSha256"])
    graph.verify(relations.CHECKER_PATH, relations.CHECKER_SHA)
    graph.verify(CODE_PATH)
    # Re-read the logical cohort manifest without changing either run status.
    graph.verify(cohort_path, cohort_ref["sha256"])
    graph.verify_unchanged()
    folder = formal["folder"]
    refs = [graph.ref(path) for path in (report_path, source_reviews, grades, folder / "answers-frozen.json",
            folder / "answers-freeze.json", folder / "private/bundle.json", folder / "manifest.json", cohort_path,
            original.FREEZE_PATH, original.LEDGER_CODE, CODE_PATH)]
    relation_refs = [graph.ref(path) for path in (relation_path, relation_folder / "manifest.json",
                                               relations.CHECKER_PATH, RELATION_CODE, COHORT_CODE)]
    return {"graph": graph, "bundle": formal["bundle"], "report": report, "outputs": formal["cohort"]["rows"],
            "relations": relation_report, "evidenceRefs": refs, "relationEvidenceRefs": relation_refs,
            "recovery": deepcopy(formal["recovery"]), "inventoryCorrection": deepcopy(formal["inventoryCorrection"]),
            "zeroCallRecovery": deepcopy(formal["zeroCallRecovery"])}


def original_metadata(mapping, provenance, output, recovery):
    ev.require(provenance["runId"] == output["runId"] and provenance["producerVersion"] == output["producerVersion"] and
               provenance["sourceRunStatus"] in ("failed", "completed"), "recovery_event_original_run_identity")
    run_path = Path(provenance["outputFile"]["path"]).parent.parent.as_posix()
    runs = [run for run in recovery["sourceRuns"] if run["runPath"] == run_path and run["status"] == provenance["sourceRunStatus"]]
    ev.require(len(runs) == 1 and Path(run_path).name == output["runId"], "recovery_event_source_run_join")
    ev.require(output["status"] == "completed" and provenance["rawResponse"] ==
               {"path": output["rawResponsePath"], "sha256": output["rawResponseSha256"]}, "recovery_event_original_output_join")
    return {"run": run_path, "runId": output["runId"], "producerVersion": output["producerVersion"],
            "originalRunStatus": provenance["sourceRunStatus"], "originalOutputStatus": output["status"],
            "originalOutputFile": deepcopy(provenance["outputFile"]), "originalRawResponse": deepcopy(provenance["rawResponse"]),
            "sourceCohort": "input-preparation-dev16", "sourceStratum": mapping["stratum"], "sourceDomain": mapping["domain"],
            "sourceDocumentGroup": mapping["documentGroup"], "recoveryCohort": deepcopy(recovery),
            "recoveryWrapperVersion": VERSION, "originalJudgmentsChanged": False, "modelCalls": 0}


def build_events(validated):
    bundle, report, recovery = (validated[k] for k in ("bundle", "report", "recovery"))
    original.require_complete_report(report)
    inventory = validated["inventoryCorrection"]
    zero_call = validated["zeroCallRecovery"]
    ev.require(report["recoveryEvidence"]["zeroCallRecovery"] == zero_call, "zero_call_event_lineage_mismatch")
    ev.require(report["recoveryEvidence"]["inventoryCorrection"] == inventory, "inventory_event_lineage_mismatch")
    ev.require(bundle["recovery"] == recovery and recovery["originalAttemptStatus"] == "failed" and
               recovery["originalFailureReclassified"] is False and recovery["completionRequestsSent"] == 65 and
               recovery["interruptedRequests"] == 1, "recovery_event_cohort_identity")
    mappings = {(m["id"], m["configuration"]): m for m in bundle["mapping"]}
    provenance = {(p["id"], p["configuration"]): p for p in bundle["outputProvenance"]}
    outputs = {ev.output_key(row): row for row in validated["outputs"]}
    ev.require(len(mappings) == len(provenance) == len(outputs) == 64 and set(mappings) == set(provenance) == set(outputs),
               "recovery_event_inventory")
    inputs = []
    for event in ev.ledger_observations(bundle, report, validated["evidenceRefs"]):
        key = event["sourceId"], event["system"]
        event.update(original_metadata(mappings[key], provenance[key], outputs[key], recovery))
        event["questionInventoryCorrection"] = deepcopy(inventory)
        event["zeroCallRecovery"] = deepcopy(zero_call)
        inputs.append(event)
    ev.require(len(inputs) == 64 and len({r["reviewId"] for r in inputs}) == 64, "recovery_input_observation_inventory")
    linked = {(r["sourceId"], r["system"]): r for r in inputs}
    observations = validated["relations"]["observations"]
    ev.require(len(observations) == 64 and len({(r["id"], r["configuration"]) for r in observations}) == 64,
               "recovery_relation_observation_inventory")
    events = list(inputs)
    for row in observations:
        key = row["id"], row["configuration"]
        ev.require(key in mappings and key in linked, "recovery_relation_without_input_observation")
        mapped, input_event, output = mappings[key], linked[key], outputs[key]
        ev.require(all(row[k] == mapped[k] for k in ("sourceSha256", "translationSha256", "promptSha256", "rawResponseSha256")) and
                   row["rawResponsePath"] == output["rawResponsePath"] and row["outputRowSha256"] == ev.sha(ev.packed(output)),
                   "recovery_relation_input_join_changed")
        metadata = original_metadata(mapped, provenance[key], output, recovery)
        metadata["questionInventoryCorrection"] = deepcopy(inventory)
        metadata["zeroCallRecovery"] = deepcopy(zero_call)
        ev.require(all(row[k] == metadata[k] for k in ("runId", "producerVersion", "originalRunStatus", "originalOutputStatus")),
                   "recovery_relation_run_status_changed")
        events.append({"version": ledger.VERSION, "kind": "relation_observation", "checker": relations.checker.VERSION,
            "checkerCodeSha256": relations.CHECKER_SHA, "reviewVersion": ev.VERSION,
            "instanceId": ev.sha(ev.packed({"cohortManifest": recovery["cohortManifest"], "run": metadata["run"],
                            "id": row["id"], "configuration": row["configuration"], "rawResponseSha256": row["rawResponseSha256"]})),
            **{k: input_event[k] for k in ("outputId", "sourceId", "sourceSha256", "translationSha256", "contextSha256", "cohort", "system")},
            **metadata, "reviewId": mapped["reviewId"], "observationId": row["observationId"], "baselineReviewEventIds": [],
            "linkedInputPreparationEventIds": [ledger.sha(ledger.packed(input_event))],
            "originalAutomaticChecks": deepcopy(row["originalAutomaticChecks"]),
            "originalTechnicalChecks": deepcopy(row["originalTechnicalChecks"]), "diagnostic": deepcopy(row["diagnostic"]),
            "evidenceOffsetEncoding": row["evidenceOffsetEncoding"],
            "evidence": deepcopy(validated["evidenceRefs"] + validated["relationEvidenceRefs"]),
            "translationErrorAdded": False, "trainingUseAllowed": False, "independentHoldout": False, "humanReviewed": False,
            "warningCountIsNotTranslationErrorCount": True, "paragraphBaselineIsNotRelationSpecificGold": True})
    ev.require(Counter(event["kind"] for event in events) == {"input_preparation_observation": 64, "relation_observation": 64} and
               Counter(event["originalRunStatus"] for event in events) == {"failed": 76, "completed": 52},
               "recovery_event_kind_or_source_run_inventory")
    ev.require(len({ledger.sha(ledger.packed(event)) for event in events}) == 128, "duplicate_recovery_event")
    return events


def prepare(*, folder, cohort, source_reviews, grades, report_path, relation_folder, destination, append=False, root=ROOT):
    root = Path(root).resolve()
    destination = local_transport.private_path(root, destination)
    ev.require(not destination.exists(), "fresh_recovery_evidence_destination_required")
    validated = validate_final_evidence(folder=folder, cohort=cohort, source_reviews=source_reviews, grades=grades,
        report_path=report_path, relation_folder=relation_folder, root=root)
    events = build_events(validated)
    ledger_folder = root / original.LEDGER_PATH
    before = original.ledger_snapshot(ledger_folder)
    baseline = original.require_baseline(before, events)
    validated["graph"].verify_unchanged()
    ev.write_new(destination / "events.json", {"version": VERSION, "events": events})
    result = {"version": VERSION, "status": "prepared", "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "events": 128, "eventKindCounts": dict(Counter(event["kind"] for event in events)),
        "eventsSha256": ev.sha((destination / "events.json").read_bytes()), "appendRequested": bool(append),
        "ledgerEventsAppended": 0, "baseline": baseline, "existingEventCount": len(before["files"]),
        "existingInventorySha256": before["inventorySha256"], "evidenceFiles": dict(validated["graph"].files),
        "recovery": validated["recovery"], "inventoryCorrection": validated["inventoryCorrection"], "zeroCallRecovery": validated["zeroCallRecovery"], "humanReviewed": False, "modelCalls": 0, "translationReviewEventsAdded": 0,
        "originalFailedRunMarkedCompleted": False, "translationErrorCountIncreasedByObservations": False,
        "questionErrorsInferredAsTranslationErrors": False, "relationWarningCountIsNotErrorCount": True,
        "originalJudgmentsChanged": False, "trainingUseAllowed": False, "independentHoldout": False, "appRegistrationPerformed": False}
    ev.write_new(destination / "prepared-manifest.json", result)
    if append:
        validated["graph"].verify_unchanged()
        receipt = original.append_prevalidated(ledger_folder, events, before)
        result = {**result, "status": "appended", "ledgerEventsAppended": receipt["imported"]["added"], **receipt}
        ev.write_new(destination / "append-receipt.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("folder", "cohort", "source-reviews", "grades", "report", "relations", "destination"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--append", action="store_true", help="Explicitly append after full recovery evidence revalidation")
    args = parser.parse_args()
    result = prepare(folder=args.folder, cohort=args.cohort, source_reviews=args.source_reviews, grades=args.grades,
                     report_path=args.report, relation_folder=args.relations, destination=args.destination, append=args.append)
    print(json.dumps({key: result[key] for key in ("status", "events", "eventKindCounts", "ledgerEventsAppended")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
