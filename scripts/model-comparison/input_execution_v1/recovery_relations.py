"""Frozen S3 observations over an explicitly verified 38+26 recovery cohort."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/model-comparison'))
from input_execution_v1 import relations as original
from input_execution_v1 import evaluation as e
checker=original.checker
CHECKER_SHA=original.CHECKER_SHA
CHECKER_PATH=original.CHECKER_PATH
SUPPORTED=original.SUPPORTED
JOIN_FIELDS=original.JOIN_FIELDS
BASE=original.BASE
verify_checker=original.verify_checker
validate_diagnostic=original.validate_diagnostic
VERSION='input-execution-v1-recovery-relations-v1'
ORIGINAL_VERSION='input-preparation-v1-hy7-c0-c3-producer-v1'
RECOVERY_VERSION='input-preparation-v1-hy7-c0-c3-recovery-producer-v1'

def diagnose_rows(rows):
    """Only two strings cross the detector boundary; IDs are attached after it."""
    checker_receipt = verify_checker()
    e.require(isinstance(rows, list) and len(rows) == 64, "complete_64_outputs_required")
    keys = [(row["id"], row["configuration"]) for row in rows]
    ids = {key[0] for key in keys}
    e.require(len(keys) == len(set(keys)) == 64 and len(ids) == 16 and
              set(keys) == {(uid, cfg) for uid in ids for cfg in e.CONFIGURATIONS}, "relation_output_inventory")
    e.require(all(row.get('runId')=='attempt-001' and row.get('producerVersion')==ORIGINAL_VERSION for row in rows[:38]),'relation_retained_run_identity')
    e.require(len({row.get('runId') for row in rows[38:]})==1 and str(rows[38].get('runId','')).startswith('recovery-attempt-') and
              all(row.get('producerVersion')==RECOVERY_VERSION for row in rows[38:]),'relation_recovery_run_identity')
    observations = []
    for row in rows:
        e.require(row.get("status") == "completed" and isinstance(row.get("source"), str)
                  and isinstance(row.get("translation"), str) and row["translation"].strip(), "relation_incomplete_output")
        e.require(e.sha(row["source"]) == row["sourceSha256"] and
                  e.sha(row["translation"]) == row["translationSha256"], "relation_output_text_hash")
        # No configuration, ID, source context, baseline judgment or answer is
        # supplied, including to the registered detector's function signature.
        diagnostic = checker.inspect_relations(row["source"], row["translation"])
        validate_diagnostic(diagnostic, row["source"], row["translation"])
        join = {key: row[key] for key in JOIN_FIELDS}
        content_id = e.sha(e.packed(join))
        observation_id = e.sha(e.packed({"outputIdentity": join, "checkerSha256": CHECKER_SHA,
                                       "diagnostic": diagnostic}))
        observations.append({
            "observationId": observation_id, "outputIdentitySha256": content_id, **join,
            "runId": row["runId"], "producerVersion":row['producerVersion'],
            "originalRunStatus":'failed' if row['producerVersion']==ORIGINAL_VERSION else 'completed',
            "rawResponsePath": row["rawResponsePath"],
            "outputRowSha256": e.sha(e.packed(row)), "diagnostic": diagnostic,
            "diagnosticSha256": e.sha(e.packed(diagnostic)),
            "evidenceOffsetEncoding": "UTF-16 code units, half-open; original text",
            "originalAutomaticChecks": deepcopy(row.get("automaticChecks", {})),
            "originalTechnicalChecks": deepcopy(row.get("technicalChecks", {})),
            "originalOutputStatus": row["status"], "originalJudgmentsChanged": False,
            "translationErrorAdded": False, "trainingUseAllowed": False, "humanReviewed": False,
        })
    by_configuration = {}
    for configuration in e.CONFIGURATIONS:
        group = [row for row in observations if row["configuration"] == configuration]
        relations = [relation for row in group for relation in row["diagnostic"]["relations"]]
        statuses = Counter(relation["status"] for relation in relations)
        by_configuration[configuration] = {
            "outputs": len(group), "relationSlots": len(relations),
            "warningOutputs": sum(row["diagnostic"]["warning"] for row in group),
            "warningRelations": sum(relation["warning"] for relation in relations),
            "outputsWithAnySupportedRelation": sum(any(relation["status"] in SUPPORTED for relation in row["diagnostic"]["relations"]) for row in group),
            "supportedRelationSlots": sum(statuses[status] for status in SUPPORTED),
            "statusCounts": {status: statuses[status] for status in checker.STATUSES},
            "byType": {kind: {status: sum(relation["type"] == kind and relation["status"] == status for relation in relations)
                               for status in checker.STATUSES} for kind in checker.TYPES},
        }
    verify_checker()
    return {
        "version": VERSION, "status": "diagnosed", "checker": checker_receipt,
        "outputs": 64, "sourceUnits": 16, "relationSlots": 64 * len(checker.TYPES),
        "configurations": by_configuration, "observations": observations,
        "detectorInputFields": ["source", "translation"], "evaluationAnnotationsUsedByDetector": False,
        "generationQualityErrors": {"status": "not_evaluated", "warningCountIsNotTranslationErrorCount": True},
        "precisionRecall": {"status": "not_evaluated", "precision": None, "recall": None,
            "reasonKo": "별도 원문 검토나 관계별 정답 판정과 아직 대조하지 않았다.",
            "futureReviewJoinFields": list(JOIN_FIELDS),
            "paragraphBaselineIsNotRelationSpecificGold": True},
        "semanticApproval": False, "fullSemanticCoverage": False,
        "unsupportedAndUndeterminedAreNotPass": True, "independentHoldout": False,
        "productQE95GatePassed": False, "modelCalls": 0, "translationGenerations": 0,
        "ledgerWrites": 0, "appRegistrationPerformed": False, "humanReviewed": False,
    }


def write_report(cohort,destination,root=ROOT):
    from input_execution_v1 import recovery_cohort
    root=Path(root).resolve();destination=e.rooted(root,destination)
    boundary=root/BASE
    e.require(destination.is_relative_to(boundary) and destination!=boundary and not destination.exists(),'fresh_relation_destination_required')
    loaded=recovery_cohort.validate_cohort(cohort,root=root)
    result=diagnose_rows(loaded['rows'])
    receipt=recovery_cohort.validate_cohort(cohort,root=root)
    e.require(loaded==receipt,'cohort_changed_during_diagnosis')
    result.update(createdAtUtc=datetime.now(timezone.utc).isoformat(),cohortEvidence=loaded['evidence'],
        cohortManifest=loaded['manifest'],originalAttemptStatus='failed',retainedOutputs=38,recoveredOutputs=26,
        completionRequestsSent=65,interruptedRequests=1,
        wrapperCodeSha256=e.sha(Path(__file__).read_bytes()),
        cohortValidatorCodeSha256=e.sha(Path(recovery_cohort.__file__).read_bytes()))
    manifest={'version':VERSION,'status':'diagnosed','outputs':64,'reportFile':'relation-report.json',
        'reportSha256':e.sha(e.packed(result)),'checker':result['checker'],
        'modelCalls':0,'translationGenerations':0,'ledgerWrites':0}
    e.write_new(destination/'relation-report.json',result)
    e.write_new(destination/'manifest.json',manifest)
    return manifest

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(write_report(args.cohort,args.destination),ensure_ascii=False))
if __name__=='__main__':main()
