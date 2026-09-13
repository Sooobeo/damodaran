"""Record the fixed 16 reviewed outputs from a closed, failed TG27 run.

This is diagnostic evidence, never a completed dev18 run or acceptance result.
The original failure, partial receipts, automatic flags and reviews stay intact.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

import complete_tg27_root_review as completed

partial = completed.partial
ledger = completed.ledger
require = partial.require
ROOT = completed.ROOT
VERSION = 'tg27-failed-run-16-diagnostic-review-v1'
IDS = [f'LDEV26-{i:03d}' for i in range(1, 17)]
RUN = '.training/comparisons/linguistic-dev-20260910/' + partial.RUN_NAME
FAILURE = '.training/verifications/tg27-retry-17-standby-failure-20260912.json'
AWAKE = '.training/verifications/tg27-v5-user-retry-20260911-dev18-b8-awake.json'
PINS = dict(completed.PINS, **{
    'scripts/model-comparison/complete_tg27_root_review.py':
        '11deaa4ff1eb39eb1477f133f35431d32024d7db64249588c6ec5d38e946fcec',
    FAILURE: 'c9b5e2e7ccabc3dd8e4a676464c24591e52f3c8bb9cd27a65e14e212ed0c9c5e',
})
SCOPE = dict(diagnosticOnly=True, partialOnly=True, originalRunStatus='failed',
             completedRunAccepted=False, fullRunEvidenceValidated=False,
             fullDev18RunValidated=False, fullCohortScoreProduced=False,
             failedRunClosureEvidenceValidated=True, originalPartialReviewPreserved=True,
             qualityAccepted=False, humanReviewed=False, independentBlindReview=False,
             independentHoldout=False, trainingUseAllowed=False,
             koreanOnlyAnswersProduced=False, sourceAndPriorOutputsSeenByRoot=True,
             nativeCalls=0, appRegistrationPerformed=False)


def check_failure(failure, summary, awake, rows):
    require(failure.get('version') == 'tg27-user-retry-17-standby-failure-v1'
            and failure.get('run') == RUN
            and (failure.get('completed'), failure.get('failed'), failure.get('notRun')) == (16, 1, 1)
            and failure.get('oldCoordinatorStopped') is True
            and failure.get('temporaryDisplayRequestReleased') is True,
            'fixed_failed_run_evidence_required')
    require(summary.get('version') == 'translategemma-large-screen-v5'
            and summary.get('status') == 'failed'
            and all(type(summary.get(k)) is int and summary[k] == n
                    for k, n in [('count',16), ('recordedCount',18), ('expectedCount',18)])
            and summary.get('ramBudgetGiB') == 8
            and summary.get('memoryOrTimeGuardAborted') is True
            and summary.get('memoryMonitoring', {}).get('abortReason') == 'request_time_limit'
            and summary.get('childProcessStopped') is True
            and awake.get('childStopped') is True and awake.get('released') is True
            and type(awake.get('exitCode')) is int and awake['exitCode'] == 1,
            'failed_run_and_owned_closure_required')
    require([r.get('id') for r in rows] == IDS + ['LDEV26-017','LDEV26-018']
            and [r.get('status') for r in rows] == ['completed'] * 16 + ['failed','not_run'],
            'fixed_completed16_failed17_unrun18_required')


def events_for(source, terms, prediction, review, refs):
    # Reuse the established quote and term schema without its completed-run claim.
    events = completed.events_for(source, terms, prediction, review, 'dev18', RUN, refs)
    for event in events:
        event.update(SCOPE, reviewVersion=VERSION,
                     originalReviewVersion=review['version'],
                     originalAutomaticChecks=prediction['checks'])
    return events


def summarize(reviews):
    require([r['id'] for r in reviews] == IDS, 'all_fixed16_reviews_required')
    terms = Counter(t['verdict'] for r in reviews for t in r['termJudgments'])
    require(sum(terms.values()) == 81, 'fixed_partial_term_count_required')
    return dict(version=VERSION, run=RUN, **SCOPE, reviewedIds=IDS,
                reviewedParagraphs=16, originalExpectedParagraphs=18,
                unreviewedOriginalIds=['LDEV26-017','LDEV26-018'],
                originalCompletionCounts=dict(completed=16, failed=1, notRun=1),
                severityCounts=dict(Counter(r['severity'] for r in reviews)),
                reviewedTermOccurrences=81, fixedDevTermDenominator=92,
                fixedFinanceTermDenominator=149, unreviewedFinanceTermOccurrences=68,
                termCounts=dict(terms), wholeCohortTermAccuracy=None,
                questionAnswerability='not_evaluated')


def record(output, append_to_ledger=False):
    output = partial.safe(partial.safe(Path(output), ROOT), completed.BASE)
    require(not output.exists(), 'new_diagnostic_output_required')
    ev = ledger.Evidence(ROOT)
    for name, digest in PINS.items():
        ev.read(name, digest)
    code_path = Path(__file__).resolve()
    ev.read(code_path)
    failure = ev.json(FAILURE)
    for name, digest in failure['files'].items():
        ev.read(name, digest)
    summary = ev.json(RUN + '/summary.json')
    awake = ev.json(AWAKE)
    rows = ev.lines(RUN + '/predictions.jsonl')
    check_failure(failure, summary, awake, rows)
    sources = {r['id']: r for r in ev.lines(partial.SOURCES, partial.SOURCES_SHA)}
    inventory = ev.json(partial.INVENTORY, partial.INVENTORY_SHA)
    by_id = {r['id']: r for r in inventory['sources']}
    source_review = ev.json(partial.SOURCE_REVIEW)
    require(source_review['sourceInventorySha256'] == partial.INVENTORY_SHA
            and source_review['sourceFileSha256'] == partial.SOURCES_SHA
            and source_review['revisedDenominator'] == 149 and source_review['changedSelections'] == 0,
            'source_inventory_review_differs')
    events, reviews = [], []
    for sid, prediction in zip(IDS, rows[:16], strict=True):
        folder = completed.BASE / 'partial' / sid
        receipt_path = folder / 'receipt.json'
        receipt = ev.json(receipt_path)
        require(receipt.get('version') == partial.VERSION
                and set(receipt.get('snapshotFilesSha256', {})) == completed.SNAPSHOT_FILES,
                'fixed_snapshot_contract_required')
        snapshot = {'receipt.json': receipt}
        for name, digest in receipt['snapshotFilesSha256'].items():
            raw = ev.read(folder / name, digest)
            if name.endswith('.json'):
                snapshot[name] = partial.strict_json(raw)
            else:
                require(partial.sha(raw) == PINS['scripts/model-comparison/partial_tg27_review.py'],
                        'original_review_code_differs')
        for name, digest in receipt['evidenceFiles'].items():
            ev.read(name, digest)
        require(prediction['rawResponseFile'] == sid + '-response.json', 'fixed_raw_path_required')
        raw_path = RUN + '/' + prediction['rawResponseFile']
        require(snapshot['response.json'] == ev.json(raw_path, prediction['rawResponseSha256']),
                'raw_response_changed')
        review = completed.check_snapshot(sources[sid], by_id[sid], prediction, snapshot, RUN)
        refs = [ev.ref(receipt_path), ev.ref(folder / 'manual-review.json'),
                ev.ref(folder / 'source-terms.json'), ev.ref(RUN + '/summary.json'),
                ev.ref(FAILURE), ev.ref(AWAKE), ev.ref(code_path)]
        events.extend(events_for(sources[sid], by_id[sid], prediction, review, refs))
        reviews.append(review)
    report = summarize(reviews)
    ev.verify_unchanged()
    event_bytes = b''.join(ledger.packed(e) for e in events)
    report.update(createdAtUTC=datetime.now(timezone.utc).isoformat(),
                  evidenceFiles=ev.files, eventsSha256=partial.sha(event_bytes),
                  eventCount=len(events), sourceInventorySha256=partial.INVENTORY_SHA)
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'events.jsonl').open('xb') as stream:
        stream.write(event_bytes)
    with (output / 'report.json').open('xb') as stream:
        stream.write(ledger.packed(report))
    if append_to_ledger:
        ev.verify_unchanged()
        imported = ledger.append_events(completed.LEDGER, events)
        with (output / 'ledger-import.json').open('xb') as stream:
            stream.write(ledger.packed(dict(**imported, ledger=str(completed.LEDGER))))
        # Keep the original ledger consumer frozen; qualify its historical coverage here.
        ledger_report = ledger.summarize(completed.LEDGER)
        ledger_report['coverage'].append('TG27 failed run: 16 partial reviews and 81 term judgments')
        ledger_report['pendingCoverage'] = ['TG27 recovery17/18 and reading6',
                                            'TG27 independent question evaluation',
                                            'other historical runtime incidents']
        ledger_report.update(imported=imported, diagnosticImport=report,
                             codeSha256=partial.sha(code_path.read_bytes()))
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        report_path = completed.LEDGER / 'reports' / (stamp + '.json')
        report_path.parent.mkdir(exist_ok=True)
        with report_path.open('xb') as stream:
            stream.write(ledger.packed(ledger_report))
        report.update(ledgerImport=imported, ledgerReport=str(report_path),
                      ledgerEventCount=ledger_report['eventCount'])
    return report


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--append-to-ledger', action='store_true')
    args = cli.parse_args()
    result = record(args.output, args.append_to_ledger)
    print(json.dumps({k: result[k] for k in ('version','reviewedParagraphs','eventCount',
        'fullRunEvidenceValidated','nativeCalls','ledgerImport','ledgerReport','ledgerEventCount') if k in result}))
