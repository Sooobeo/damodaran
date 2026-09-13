"""Preserve a manually reviewed, completed TG27 row without certifying its live run."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'tg27-partial-root-review-v1'
RUN_NAME = 'translategemma-27b-v5-user-retry-20260911-b8'
INVENTORY = '.training/quality-evaluation/finance-terms/source-inventory-v1/source-inventory.json'
INVENTORY_SHA = '61d9b4a1535b2bcb0e67e593e975bc6945da987ec277cf4a8bd5ee9e2eff7930'
SOURCES = '.training/quality-evaluation/finance-answerability/source-only-v1/source-only.jsonl'
SOURCES_SHA = '2ed90d5a9f6250020ba0e73c1fae5a77ecd9f63032badf9ddee7b9a1668432bb'
SOURCE_REVIEW = '.training/quality-evaluation/finance-terms/source-inventory-review-20260911/root-review.json'
OUTPUT = '.training/quality-evaluation/tg27-user-retry-root-review-20260912/partial'


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def packed(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                       separators=(',', ':')) + '\n').encode('utf-8')


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result

    def invalid(_):
        raise ValueError('nonfinite_json_number')

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def safe(path, root):
    path = root / path
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'redirected_path')
    require(path.resolve().is_relative_to(root.resolve()), 'path_outside_workspace')
    return path


def completed_line(raw, sid):
    # The producer appends one JSON line after completing each response. An unfinished
    # final line is not an observation; completed lines are never altered or inferred.
    rows = [strict_json(line) for line in raw.splitlines(keepends=True)
            if line.endswith(b'\n') and line.strip()]
    require(len({r['id'] for r in rows}) == len(rows), 'duplicate_prediction_id')
    matches = [r for r in rows if r['id'] == sid]
    require(len(matches) == 1, 'completed_row_unavailable')
    return matches[0]


def check_observation(source, inventory, pred, raw, review):
    sid = source['id']
    require(review['version'] == VERSION and review['id'] == pred['id'] == inventory['id'] == sid,
            'review_identity')
    for key, text in (('sourceSha256', source['source']), ('contextSha256', source['context']),
                      ('targetSha256', pred['translation'])):
        require(sha(text.encode('utf-8')) == pred[key] == review[key], 'review_text_hash')
    require(inventory['sourceSha256'] == pred['sourceSha256'] and
            inventory['contextSha256'] == pred['contextSha256'], 'inventory_text_hash')
    require(pred['status'] == 'completed' and pred['truncated'] is False and
            pred['stopType'] == 'eos' and pred['terminalTokenId'] == 106 and
            pred['terminationClass'] == 'original_model_eog', 'row_not_completed_with_eos')
    require(raw['content'] == pred['translation'] and raw['tokens'] == pred['outputTokenIds'] and
            len(raw['tokens']) == raw['tokens_predicted'] == pred['generatedTokens'] and
            raw['tokens'][-1] == 106 and raw['stop_type'] == 'eos' and raw['truncated'] is False,
            'raw_response_differs')
    require(raw['tokens_evaluated'] == pred['inputTokens'] and
            raw['generation_settings'] == pred['actualGenerationSettings'] and
            raw['timings'] == pred['timings'] and sha(raw['prompt'].encode('utf-8')) == pred['promptSha256'] and
            raw['prompt'].count(source['source']) == 1, 'raw_prompt_settings_or_timings_differ')
    require(review['humanReviewed'] is False and review['independentBlindReview'] is False and
            review['reviewerHadSeenSourceAndPriorOutputs'] is True and
            review['trainingUseAllowed'] is False and review['koreanOnlyAnswersProduced'] is False,
            'review_provenance')
    require(review['severity'] in ('none', 'minor', 'major', 'critical', 'unresolved') and
            isinstance(review['reasonKo'], str) and review['reasonKo'].strip(), 'meaning_judgment_missing')
    require(isinstance(review['propositionChecks'], list) and review['propositionChecks'], 'propositions_required')
    for check in review['propositionChecks']:
        require(check['sourceQuote'] and check['sourceQuote'] in source['source'], 'source_quote_not_exact')
        require((type(check['preserved']) is bool or check['preserved'] is None) and isinstance(check['targetQuotes'], list),
                'proposition_judgment')
        require(check['targetQuotes'] or check['preserved'] is not True, 'preserved_quote_missing')
        require(all(q and q in pred['translation'] for q in check['targetQuotes']), 'target_quote_not_exact')
        require(isinstance(check['reasonKo'], str) and check['reasonKo'].strip(), 'proposition_reason_missing')
    expected = {t['occurrenceId']: t for t in inventory['occurrences']}
    terms = review['termJudgments']
    require(len(terms) == len(expected) and {t['occurrenceId'] for t in terms} == set(expected),
            'term_coverage')
    for term in terms:
        span = expected[term['occurrenceId']]['span']
        require(source['source'][span['start']:span['end']] == span['quote'], 'term_source_span')
        require(term['verdict'] in ('correct', 'incorrect', 'unresolved') and term['reasonKo'].strip(),
                'term_judgment')
        require(isinstance(term['targetQuotes'], list) and
                (term['targetQuotes'] or term['verdict'] != 'correct'), 'term_quote_required')
        require(all(q and q in pred['translation'] for q in term['targetQuotes']), 'term_quote_not_exact')


def preserve(review_path, root=ROOT):
    root = Path(root).resolve()
    evidence = {}

    def read(path, expected=None):
        path = safe(Path(path), root)
        raw = path.read_bytes()
        require(expected is None or sha(raw) == expected, 'evidence_hash_mismatch')
        evidence[path.relative_to(root).as_posix()] = sha(raw)
        return raw

    review_raw = read(review_path)
    review = strict_json(review_raw)
    inventory = strict_json(read(INVENTORY, INVENTORY_SHA))
    sources = [strict_json(line) for line in read(SOURCES, SOURCES_SHA).splitlines() if line.strip()]
    source_review = strict_json(read(SOURCE_REVIEW))
    require(source_review['sourceInventorySha256'] == INVENTORY_SHA and
            source_review['sourceFileSha256'] == SOURCES_SHA and
            source_review['revisedDenominator'] == 149 and source_review['changedSelections'] == 0,
            'source_inventory_review_differs')
    source = next(r for r in sources if r['id'] == review['id'])
    terms = next(r for r in inventory['sources'] if r['id'] == review['id'])
    cohort = terms['cohort']
    require(cohort in ('dev18', 'reading6'), 'development_only')
    folder = 'linguistic-dev-20260910' if cohort == 'dev18' else 'real-reading-check-20260910'
    run = safe(Path('.training/comparisons') / folder / RUN_NAME, root)
    start_raw = read(run / 'start.json')
    start = strict_json(start_raw)
    require(start['version'] == 'translategemma-large-screen-v5' and
            start['modelSize'] == '27b' and start['ramBudgetGiB'] == 8, 'live_run_identity')
    predictions_path = run / 'predictions.jsonl'
    pred = completed_line(predictions_path.read_bytes(), review['id'])
    require(pred['rawResponseFile'] == review['id'] + '-response.json', 'raw_response_path')
    response_raw = read(run / pred['rawResponseFile'], pred['rawResponseSha256'])
    check_observation(source, terms, pred, strict_json(response_raw), review)
    # Pin the selected row, not the hash of a growing predictions file.
    require(completed_line(predictions_path.read_bytes(), review['id']) == pred, 'selected_live_row_changed')
    for path, digest in evidence.items():
        require(sha(safe(Path(path), root).read_bytes()) == digest, 'evidence_changed_during_review')
    out = safe(Path(OUTPUT) / review['id'], root)
    require(not out.exists(), 'immutable_review_already_exists')
    code = Path(__file__).read_bytes()
    files = {'source.json': packed(source), 'source-terms.json': packed(terms),
             'prediction.json': packed(pred), 'response.json': response_raw,
             'manual-review.json': review_raw, 'review-code.py': code}
    receipt = dict(version=VERSION, createdAtUTC=datetime.now(timezone.utc).isoformat(),
                   id=review['id'], cohort=cohort, run=run.relative_to(root).as_posix(),
                   selectedPredictionSha256=sha(packed(pred)), evidenceFiles=evidence,
                   snapshotFilesSha256={name: sha(raw) for name, raw in files.items()},
                   actualAutomaticChecks=pred['checks'], partialOnly=True,
                   fullRunValidated=False, fullCohortScoreProduced=False, qualityAccepted=False,
                   humanReviewed=False, independentBlindReview=False, koreanOnlyAnswersProduced=False,
                   trainingUseAllowed=False, rootSourceReviewSha256=sha(packed(source_review)),
                   producerModified=False, nativeCalls=0)
    out.mkdir(parents=True, exist_ok=False)
    for name, raw in {**files, 'receipt.json': packed(receipt)}.items():
        with (out / name).open('xb') as output:
            output.write(raw)
    return receipt


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--review', type=Path, required=True)
    args = cli.parse_args()
    receipt = preserve(args.review)
    print(json.dumps({k: receipt[k] for k in ('id', 'partialOnly', 'fullRunValidated', 'nativeCalls')}))


if __name__ == '__main__':
    main()
