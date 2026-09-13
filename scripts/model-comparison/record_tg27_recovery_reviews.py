"""Join fixed recovery reviews to their own successful tail2 or reading6 evidence.

Never concatenate the old failed dev18 run and this tail2 into a successful run.
Manual reviews are required; this tool does not judge meaning or answer questions.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

import complete_tg27_root_review as completed
import partial_tg27_recovery_review as partial
import verify_tg27_tail2 as tail

ROOT = completed.ROOT
BASE = ROOT / '.training/quality-evaluation/tg27-recovery-root-review-20260912'
VERSION = 'tg27-recovery-run-root-review-v1'
ledger = completed.ledger
require = partial.require
COHORTS = dict(tail2=['LDEV26-017','LDEV26-018'],reading6=[f'REAL26-{i:03d}' for i in range(1,7)])
PINS = dict(completed.PINS, **{
    'scripts/model-comparison/complete_tg27_root_review.py':
        '11deaa4ff1eb39eb1477f133f35431d32024d7db64249588c6ec5d38e946fcec',
    'scripts/model-comparison/partial_tg27_recovery_review.py':
        '2524b22d0e7298e28b47369515346f37e438548cf48802713d4c1c329f04b04a',
    'scripts/model-comparison/verify_tg27_tail2.py':
        '8d67631ec1771cc8d5b1010432e0cfc3fc5546894471c93b07836a808df2bf11',
    'scripts/model-comparison/v5_tail2_review_evidence.py':
        '4006c1173a1f1c9d7f827b4bcbde6d36a49e6f6050e7ffc4f7e0a9ee213f3861',
})


def scope(cohort):
    require(cohort in COHORTS,'fixed_recovery_cohort_required')
    return dict(diagnosticOnly=True,selectedRunValidated=True,
                selectedRunScope=cohort,originalRunStatus='completed',
                fullRunEvidenceValidated=True,fullDev18RunValidated=False,
                fullReading6RunValidated=cohort=='reading6',
                allSelectedManualReviewsPresent=True,originalPartialReviewPreserved=True,
                qualityAccepted=False,humanReviewed=False,independentBlindReview=False,
                independentHoldout=False,trainingUseAllowed=False,koreanOnlyAnswersProduced=False,
                sourceAndPriorOutputsSeenByRoot=True,nativeCalls=0,appRegistrationPerformed=False)


def check_closed(summary,awake,cohort):
    count=len(COHORTS[cohort])
    require(summary.get('version')=='translategemma-large-screen-v5'
            and summary.get('status')=='completed'
            and all(type(summary.get(k)) is int and summary[k]==count
                    for k in ('count','recordedCount','expectedCount'))
            and summary.get('ramBudgetGiB')==8
            and summary.get('childProcessStopped') is True
            and awake.get('childStopped') is True and awake.get('released') is True
            and type(awake.get('exitCode')) is int and awake['exitCode']==0,
            'complete_selected_run_and_owned_closure_required')


def check_snapshot(source,terms,prediction,snapshot,run):
    receipt=snapshot['receipt.json']
    require(receipt.get('version')==partial.VERSION and receipt.get('id')==source['id']
            and receipt.get('cohort')==source['cohort'] and receipt.get('run')==run,
            'fixed_recovery_snapshot_identity')
    require(all(receipt.get(k) is False for k in ('fullRunValidated','fullCohortScoreProduced',
                'qualityAccepted','humanReviewed','independentBlindReview',
                'koreanOnlyAnswersProduced','trainingUseAllowed'))
            and receipt.get('partialOnly') is True
            and type(receipt.get('nativeCalls')) is int and receipt['nativeCalls']==0,
            'original_partial_provenance_required')
    require(snapshot['source.json']==source and snapshot['source-terms.json']==terms
            and snapshot['prediction.json']==prediction
            and receipt.get('selectedPredictionSha256')==partial.sha(partial.packed(prediction))
            and receipt.get('actualAutomaticChecks')==prediction['checks'],
            'original_snapshot_observation_differs')
    review=snapshot['manual-review.json']
    partial.check_observation(source,terms,prediction,snapshot['response.json'],review)
    return review


def summarize(cohort,reviews):
    require([r['id'] for r in reviews]==COHORTS[cohort],'all_selected_reviews_required')
    terms=Counter(t['verdict'] for r in reviews for t in r['termJudgments'])
    expected={'tail2':11,'reading6':57}[cohort]
    require(sum(terms.values())==expected,'fixed_selected_term_coverage_required')
    return dict(version=VERSION,**scope(cohort),reviewedIds=COHORTS[cohort],
                reviewedParagraphs=len(reviews),severityCounts=dict(Counter(r['severity'] for r in reviews)),
                reviewedTermOccurrences=expected,termCounts=dict(terms),
                wholeFinanceTermDenominator=149,wholeFinanceAccuracy=None,
                questionAnswerability='not_evaluated')


def record(cohort,output,append_to_ledger=False):
    output=completed.verified_run.safe_path(output,BASE)
    require(not output.exists(),'new_recovery_review_output_required')
    run=tail.RUN if cohort=='tail2' else ROOT/'.training/comparisons/real-reading-check-20260910'/partial.READING_RUN
    run_name=run.relative_to(ROOT).as_posix()
    ev=ledger.Evidence(ROOT)
    for name,digest in PINS.items():ev.read(name,digest)
    code_path=Path(__file__).resolve();ev.read(code_path)
    require((run/'summary.json').exists(),'completed_recovery_summary_missing')
    summary=ev.json(run/'summary.json')
    awake_path=ROOT/'.training/verifications'/f'tg27-{cohort}-recovery-20260912-awake.json'
    awake=ev.json(awake_path);check_closed(summary,awake,cohort)
    if cohort=='tail2':
        verified=tail.verify();files=verified['evidenceFiles']
    else:
        verified=completed.verified_run.load_verified(run,'reading6','translategemma-large-screen-v5')
        files=verified['files']
    for name,digest in files.items():ev.read(name,digest)
    predictions=ev.lines(run/'predictions.jsonl')
    require([p['id'] for p in predictions]==COHORTS[cohort],'fixed_recovery_ids_required')
    sources={r['id']:r for r in ev.lines(partial.SOURCES,partial.SOURCES_SHA)}
    inventory=ev.json(partial.INVENTORY,partial.INVENTORY_SHA)
    terms_by_id={r['id']:r for r in inventory['sources']}
    source_review=ev.json(partial.SOURCE_REVIEW)
    require(source_review['sourceInventorySha256']==partial.INVENTORY_SHA
            and source_review['sourceFileSha256']==partial.SOURCES_SHA
            and source_review['revisedDenominator']==149 and source_review['changedSelections']==0,
            'source_inventory_review_differs')
    reviews,events=[],[]
    for prediction in predictions:
        sid=prediction['id'];folder=BASE/'partial'/sid
        receipt_path=folder/'receipt.json';receipt=ev.json(receipt_path)
        require(set(receipt.get('snapshotFilesSha256',{}))==completed.SNAPSHOT_FILES,
                'complete_snapshot_files_required')
        snapshot={'receipt.json':receipt}
        for name,digest in receipt['snapshotFilesSha256'].items():
            raw=ev.read(folder/name,digest)
            if name.endswith('.json'):snapshot[name]=partial.strict_json(raw)
            else:require(partial.sha(raw)==PINS['scripts/model-comparison/partial_tg27_recovery_review.py'],
                         'recovery_review_code_changed')
        for name,digest in receipt['evidenceFiles'].items():ev.read(name,digest)
        require(prediction['rawResponseFile']==sid+'-response.json','fixed_response_path_required')
        require(snapshot['response.json']==ev.json(run/prediction['rawResponseFile'],prediction['rawResponseSha256']),
                'original_response_differs')
        review=check_snapshot(sources[sid],terms_by_id[sid],prediction,snapshot,run_name)
        refs=[ev.ref(receipt_path),ev.ref(folder/'manual-review.json'),ev.ref(folder/'source-terms.json'),
              ev.ref(run/'summary.json'),ev.ref(awake_path),ev.ref(code_path)]
        new_events=completed.events_for(sources[sid],terms_by_id[sid],prediction,review,
                                        sources[sid]['cohort'],run_name,refs)
        for event in new_events:
            event.update(scope(cohort),system=run.name,reviewVersion=VERSION,
                         originalReviewVersion=partial.VERSION,originalAutomaticChecks=prediction['checks'])
        events.extend(new_events);reviews.append(review)
    report=summarize(cohort,reviews)
    ev.verify_unchanged()
    raw=b''.join(ledger.packed(e) for e in events)
    report.update(run=run_name,createdAtUTC=datetime.now(timezone.utc).isoformat(),
                  eventCount=len(events),evidenceFiles=ev.files,eventsSha256=partial.sha(raw))
    output.mkdir(parents=True,exist_ok=False)
    with (output/'events.jsonl').open('xb') as f:f.write(raw)
    with (output/'report.json').open('xb') as f:f.write(ledger.packed(report))
    if append_to_ledger:
        ev.verify_unchanged();imported=ledger.append_events(completed.LEDGER,events)
        with (output/'ledger-import.json').open('xb') as f:f.write(ledger.packed(imported))
        report['ledgerImport']=imported
    return report


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--cohort',choices=COHORTS,required=True)
    cli.add_argument('--output',type=Path,required=True)
    cli.add_argument('--append-to-ledger',action='store_true')
    args=cli.parse_args();report=record(args.cohort,args.output,args.append_to_ledger)
    print(json.dumps({k:report[k] for k in ('selectedRunScope','reviewedParagraphs','eventCount',
        'fullDev18RunValidated','nativeCalls','ledgerImport') if k in report}))
