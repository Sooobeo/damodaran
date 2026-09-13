"""Join completed TG27 v5 evidence to all immutable root reviews in one cohort.

No inference or question answering. A running/partial/failed run cannot be joined.
Existing snapshots, producer code and previous ledger events are never rewritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import partial_tg27_review as partial
import prepare_finance_answerability as verified_run

sys.path.insert(0, str(Path(__file__).parent / 'error-ledger'))
import ledger

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'tg27-completed-root-review-v1'
BASE = ROOT / '.training/quality-evaluation/tg27-user-retry-root-review-20260912'
LEDGER = ROOT / '.training/quality-evaluation/error-ledger/v1'
PINS = {
    'scripts/model-comparison/partial_tg27_review.py': '4cb671ff6da6c9251d72bdf7dec9a4136bedcd7289e2959b0765edc3244b0937',
    'scripts/model-comparison/prepare_finance_answerability.py': '9d4ca9f52f041a8a40272924b7d3e6850eb705bd60e0923df84363e3d9e66617',
    'scripts/model-comparison/error-ledger/ledger.py': '13090664fa5e2cbb4e0e22d4ab2e1cbe2092e83251961098c1a4665403462d22',
}
SNAPSHOT_FILES = {'source.json', 'source-terms.json', 'prediction.json', 'response.json',
                  'manual-review.json', 'review-code.py'}
require = partial.require


def check_closed(summary, awake, cohort):
    count = len(verified_run.COHORTS[cohort])
    require(summary.get('version') == 'translategemma-large-screen-v5'
            and summary.get('status') == 'completed'
            and all(type(summary.get(k)) is int and summary[k] == count
                    for k in ('count', 'recordedCount', 'expectedCount')),
            'completed_full_cohort_required')
    require(summary.get('ramBudgetGiB') == 8 and summary.get('childProcessStopped') is True
            and awake.get('childStopped') is True and awake.get('released') is True
            and type(awake.get('exitCode')) is int and awake['exitCode'] == 0,
            'successful_owned_closure_required')


def check_snapshot(source, terms, prediction, snapshot, expected_run):
    receipt = snapshot['receipt.json']
    require(receipt.get('id') == source['id'] and receipt.get('cohort') == source['cohort']
            and receipt.get('run') == expected_run, 'snapshot_run_or_source_differs')
    require(receipt.get('partialOnly') is True and receipt.get('fullRunValidated') is False
            and receipt.get('fullCohortScoreProduced') is False and receipt.get('qualityAccepted') is False
            and receipt.get('humanReviewed') is False and receipt.get('independentBlindReview') is False
            and receipt.get('koreanOnlyAnswersProduced') is False and receipt.get('trainingUseAllowed') is False
            and type(receipt.get('nativeCalls')) is int and receipt['nativeCalls'] == 0,
            'partial_snapshot_provenance_changed')
    require(snapshot['source.json'] == source and snapshot['source-terms.json'] == terms
            and snapshot['prediction.json'] == prediction, 'snapshot_does_not_match_completed_run')
    require(receipt.get('selectedPredictionSha256') == partial.sha(partial.packed(prediction))
            and receipt.get('actualAutomaticChecks') == prediction['checks'], 'snapshot_observation_identity')
    review = snapshot['manual-review.json']
    partial.check_observation(source, terms, prediction, snapshot['response.json'], review)
    return review


def events_for(source, terms, prediction, review, cohort, run, refs):
    event = ledger.review_event(source['id'], source['source'], prediction['translation'], source['context'],
                                partial.RUN_NAME, run, cohort, VERSION, review, refs)
    # Keep every exact target fragment; never concatenate disjoint text as an exact span.
    event['observationQuotes'] = [
        dict(sourceQuote=c['sourceQuote'], targetQuote=q, sourceExact=True,
             targetExact=bool(q), annotation=c)
        for c in review['propositionChecks'] if c['preserved'] is not True
        for q in (c['targetQuotes'] or [''])]
    event.update(independentBlindReview=False, originalPartialReviewPreserved=True,
                 fullRunEvidenceValidated=True, koreanOnlyAnswersProduced=False,
                 sourceAndPriorOutputsSeenByRoot=True)
    events = [event]
    by_id = {t['occurrenceId']: t for t in terms['occurrences']}
    for judgment in review['termJudgments']:
        events.append(dict(version=ledger.VERSION, kind='term_judgment', cohort=cohort,
                           system=partial.RUN_NAME, run=run, sourceId=source['id'], outputId=event['outputId'],
                           sourceSha256=event['sourceSha256'], translationSha256=event['translationSha256'],
                           contextSha256=event['contextSha256'], reviewVersion=VERSION,
                           term=by_id[judgment['occurrenceId']], verdict=judgment['verdict'],
                           targetQuotes=judgment['targetQuotes'], reasonKo=judgment['reasonKo'],
                           evidence=refs, humanReviewed=False, independentBlindReview=False,
                           independentHoldout=False, trainingUseAllowed=False,
                           fullRunEvidenceValidated=True, originalPartialReviewPreserved=True))
    return events


def summarize_reviews(cohort, reviews):
    ids = verified_run.COHORTS[cohort]
    require([r['id'] for r in reviews] == ids, 'complete_ordered_manual_review_coverage_required')
    severity = Counter(r['severity'] for r in reviews)
    terms = Counter(t['verdict'] for r in reviews for t in r['termJudgments'])
    expected = {'dev18': 92, 'reading6': 57}[cohort]
    require(sum(terms.values()) == expected, 'fixed_term_denominator_differs')
    return dict(cohort=cohort, reviewedParagraphs=len(reviews), severityCounts=dict(severity),
                termOccurrenceCount=expected, termCounts=dict(terms),
                materialErrorParagraphs=severity['major'] + severity['critical'],
                unresolvedParagraphs=severity['unresolved'],
                fullRunEvidenceValidated=True, allCohortManualReviewsPresent=True,
                originalPartialReviewSnapshotsPreserved=True, humanReviewed=False,
                independentBlindReview=False, koreanOnlyAnswersProduced=False,
                questionAnswerability='not_evaluated', general16='not_evaluated',
                independentHoldout=False, trainingUseAllowed=False, qualityAccepted=False,
                nativeCalls=0, appRegistrationPerformed=False)


def complete(cohort, output, append_to_ledger=False):
    output = verified_run.safe_path(output, BASE)
    require(not output.exists(), 'new_completed_review_output_required')
    folder = {'dev18': 'linguistic-dev-20260910', 'reading6': 'real-reading-check-20260910'}[cohort]
    run = ROOT / '.training/comparisons' / folder / partial.RUN_NAME
    awake_path = ROOT / ('.training/verifications/tg27-v5-user-retry-20260911-'
                         + ('dev18' if cohort == 'dev18' else 'reading6') + '-b8-awake.json')
    evidence = ledger.Evidence(ROOT)
    for name, digest in PINS.items():
        evidence.read(name, digest)
    evidence.read(Path(__file__).resolve())
    # Refuse current unfinished work before opening its growing predictions file.
    require((run / 'summary.json').is_file(), 'completed_run_summary_missing')
    summary = evidence.json(run / 'summary.json')
    awake = evidence.json(awake_path)
    check_closed(summary, awake, cohort)
    verified = verified_run.load_verified(run, cohort, 'translategemma-large-screen-v5')
    require(verified['summary'] == summary, 'summary_changed_during_verification')
    inventory = partial.strict_json(evidence.read(partial.INVENTORY, partial.INVENTORY_SHA))
    source_review = evidence.json(partial.SOURCE_REVIEW)
    require(source_review['sourceInventorySha256'] == partial.INVENTORY_SHA
            and source_review['sourceFileSha256'] == partial.SOURCES_SHA
            and source_review['revisedDenominator'] == 149 and source_review['changedSelections'] == 0,
            'source_inventory_review_differs')
    by_id = {r['id']: r for r in inventory['sources']}
    events, reviews = [], []
    relative_run = run.relative_to(ROOT).as_posix()
    for source, prediction in zip(verified['sources'], verified['predictions'], strict=True):
        folder = BASE / 'partial' / source['id']
        receipt_path = folder / 'receipt.json'
        receipt = partial.strict_json(evidence.read(receipt_path))
        require(set(receipt.get('snapshotFilesSha256', {})) == SNAPSHOT_FILES, 'snapshot_file_coverage')
        snapshot = {'receipt.json': receipt}
        for name, digest in receipt['snapshotFilesSha256'].items():
            raw = evidence.read(folder / name, digest)
            if name.endswith('.json'):
                snapshot[name] = partial.strict_json(raw)
            else:
                require(partial.sha(raw) == PINS['scripts/model-comparison/partial_tg27_review.py'], 'snapshot_review_code_differs')
        for name, digest in receipt['evidenceFiles'].items():
            evidence.read(name, digest)
        require(snapshot['response.json'] == partial.strict_json(evidence.read(run / prediction['rawResponseFile'], prediction['rawResponseSha256'])),
                'snapshot_raw_response_differs')
        review = check_snapshot(source, by_id[source['id']], prediction, snapshot, relative_run)
        reviews.append(review)
        refs = [evidence.ref(receipt_path), evidence.ref(folder / 'manual-review.json'),
                evidence.ref(folder / 'source-terms.json'), evidence.ref(run / 'summary.json'),
                evidence.ref(awake_path), evidence.ref(Path(__file__).resolve())]
        events.extend(events_for(source, by_id[source['id']], prediction, review, cohort, relative_run, refs))
    report = summarize_reviews(cohort, reviews)
    verified_run.seal(verified['files'])
    evidence.verify_unchanged()
    report.update(version=VERSION, createdAtUTC=datetime.now(timezone.utc).isoformat(), run=relative_run,
                  evidenceFiles=evidence.files, verifiedRunFiles=verified['files'],
                  sourceInventorySha256=partial.INVENTORY_SHA,
                  eventsSha256=partial.sha(b''.join(ledger.packed(e) for e in events)))
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'events.jsonl').open('xb') as f:
        for event in events:
            f.write(ledger.packed(event))
    with (output / 'report.json').open('xb') as f:
        f.write(ledger.packed(report))
    if append_to_ledger:
        verified_run.seal(verified['files'])
        evidence.verify_unchanged()
        imported = ledger.append_events(LEDGER, events)
        with (output / 'ledger-import.json').open('xb') as f:
            f.write(ledger.packed(dict(**imported, ledger=str(LEDGER),
                eventsSha256=report['eventsSha256'], original610EventsNotReimported=True)))
        report['ledgerImport'] = imported
    return report


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--cohort', choices=['dev18', 'reading6'], required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--append-to-ledger', action='store_true')
    args = cli.parse_args()
    result = complete(args.cohort, args.output, args.append_to_ledger)
    print(json.dumps({k: result[k] for k in ('cohort', 'reviewedParagraphs', 'severityCounts',
                                            'termCounts', 'fullRunEvidenceValidated', 'qualityAccepted', 'nativeCalls')}))


if __name__ == '__main__':
    main()
