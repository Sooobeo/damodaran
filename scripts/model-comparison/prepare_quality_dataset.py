"""Freeze reviewed long-passage comparisons without opening model predictions."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / '.training/comparisons/finance-quality-20260910'
sys.path.insert(0, str(ROOT / 'scripts/model-training'))
from train_v5 import leakage_issues
from train import normalized_source, numeric_tokens
from dataset_io import write_outputs_once


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text('utf-8-sig').splitlines() if line.strip()]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    require(not (DEST / 'dataset.jsonl').exists() and not (DEST / 'dataset-manifest.json').exists(),
            'Preserve the published comparison dataset')
    candidate, reviewed = DEST / 'candidates.jsonl', DEST / 'reviewed.jsonl'
    require(sha(candidate) == '714eebe0ce1464d79fc19e0ddbe081327cae662d0ef035a349360abc104f86e7'
            and sha(reviewed) == '73fb2b2c4291090e49205aeff34cecfa24132d91c19522834fe779139a457737',
            'Reviewed source bytes changed')
    original, data = rows(candidate), rows(reviewed)
    review = json.loads((DEST / 'second-review.json').read_text('utf-8'))
    require(review['status'] == 'complete' and review['rowCount'] == 24 and review['humanReviewed'] is False
            and review['sourceGrounded'] is True and review['modelPredictionsViewed'] is False
            and review['inputSha256'] == sha(candidate) and review['outputSha256'] == sha(reviewed),
            'Second-review evidence differs')
    require(sha(DEST / 'author-review.json') == '93cf1bafbc1669f745a7a8bc0b2fe5e9cc6dbec245d75565a4709ab6d527254c',
            'Author review changed')
    identifiers = [f'Q26-{index:03d}' for index in range(1, 25)]
    require([row['id'] for row in data] == identifiers and review['reviewedIds'] == identifiers
            and [row['id'] for row in review['rows']] == identifiers, 'Quality review coverage differs')
    require(Counter(row['domain'] for row in data) == {'finance': 16, 'general': 8}, 'Wrong domain coverage')
    require(sum(bool(row['context']) for row in data) == 8, 'Wrong context coverage')
    diagnostic = []
    for previous, row, judgment in zip(original, data, review['rows']):
        require(row['split'] == 'exploratory_probe' and row['humanReviewed'] is False
                and row['provenance'] == 'assistant_authored_unreviewed'
                and previous['source'] == row['source'] and previous['context'] == row['context'],
                'Source, split, or provenance changed')
        for name in ('source', 'target', 'context'):
            require(isinstance(row[name], str) and (row[name].strip() or name == 'context')
                    and hashlib.sha256(row[name].encode()).hexdigest() == row[name + 'Sha256'],
                    'Source/reference/context hash mismatch')
        require(judgment['sourceGroundedReviewComplete'] is True and judgment['sourceChanged'] is False
                and judgment['sourceSha256'] == row['sourceSha256']
                and judgment['outputTargetSha256'] == row['targetSha256']
                and judgment['evidence'].strip(), 'Incomplete source-grounded judgment')
        minimum, maximum = (60, 100) if row['domain'] == 'finance' else (40, 70)
        require(minimum <= len(row['source'].split()) <= maximum, 'Source length outside planned coverage')
        require(not row['context'] or 20 <= len(row['context'].split()) <= 45, 'Context length outside planned coverage')
        for field in ('termTargets', 'forbiddenTerms', 'protectedSymbols', 'criticalChecks'):
            require(isinstance(row[field], list) and all(isinstance(value, str) and value.strip() for value in row[field]),
                    'Malformed quality annotation')
        require(bool(row['criticalChecks']), 'Missing source-grounded quality checks')
        for expression in row['protectedSymbols']:
            require(expression in row['source'] and expression in row['target'], 'Protected formula changed in reference')
        for check in row['unitChecks']:
            require(check['source'] in row['source'] and check['target'] in row['target'] and check['meaning'].strip(),
                    'Unit reference anchor missing')
        diagnostic.append({'id': row['id'], 'sourceWords': len(row['source'].split()),
                           'contextWords': len(row['context'].split()),
                           'legacyNumericTokensMatch': numeric_tokens(row['source']) == numeric_tokens(row['target'])})
    historic_paths = [ROOT / 'content/training' / name for name in ('train.jsonl', 'dev.jsonl', 'evaluation.jsonl')]
    historic_paths += [ROOT / '.training/datasets' / version / name for version in ('finance-v4', 'finance-v5')
                       for name in ('train.jsonl', 'dev.jsonl', 'test.jsonl')]
    historic_paths += [ROOT / '.training/datasets/finance-v4/polysemy-probe.jsonl',
                       ROOT / 'content/model-comparison/finance-probe-20260909.jsonl']
    historical, seen = [], set()
    for file in historic_paths:
        for row in rows(file):
            normalized = normalized_source(row['source'])
            if normalized in seen:
                continue
            seen.add(normalized)
            historical.append({'id': f'{file.relative_to(ROOT).as_posix()}:{row["id"]}', 'source': row['source']})
    statistics = {}
    issues = leakage_issues({'quality-probe': data, 'historical': historical}, statistics)
    context_issues = [issue for issue in leakage_issues({
        'quality-context': [{'id': row['id'] + ':context', 'source': row['context']} for row in data if row['context']],
        'historical': historical}) if issue['first']['split'] != issue['second']['split']]
    require(not issues and not context_issues, 'Quality source/context leakage detected; do not publish')
    manifest = {'version': 'finance-quality-20260910-v1', 'status': 'frozen',
                'createdAt': datetime.now(timezone.utc).isoformat(), 'humanReviewed': False,
                'sourceType': 'assistant_authored_unreviewed',
                'dataset': {'file': 'dataset.jsonl', 'sha256': sha(reviewed), 'count': len(data), 'ids': identifiers},
                'reviewFiles': [{'file': name, 'sha256': sha(DEST / name)}
                                for name in ('candidates.jsonl', 'author-review.json', 'reviewed.jsonl', 'second-review.json')],
                'historicalFiles': [{'file': file.relative_to(ROOT).as_posix(), 'sha256': sha(file)} for file in historic_paths],
                'leakage': {'passed': True, 'issues': [], 'contextIssues': [], 'statistics': statistics,
                            'uniqueHistoricalSources': len(historical)},
                'rows': diagnostic, 'domains': {'finance': 16, 'general': 8}, 'contextRows': 8,
                'formulaRows': sum(bool(row['protectedSymbols']) for row in data),
                'unitAnnotatedRows': sum(bool(row['unitChecks']) for row in data),
                'legacyNumericMatcherLimitation': 'Lexical warnings only. Percentage-point spelling, words, units and formula semantics need source-grounded review; this does not change the frozen v5 matcher.',
                'modelPredictionsRead': False, 'trainingPerformed': False,
                'publicationScriptSha256': sha(Path(__file__)),
                'comparisonPurpose': 'Local system comparison on newly authored long passages; not a representative professional financial translation test or a human gold reference.'}
    write_outputs_once(DEST, {'dataset.jsonl': reviewed.read_text('utf-8'),
                             'dataset-manifest.json': json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'})
    print(json.dumps({'status': 'frozen', 'dataset': manifest['dataset'], 'contextRows': 8,
                      'leakagePassed': True, 'referenceNumericWarnings': [row['id'] for row in diagnostic if not row['legacyNumericTokensMatch']]}))


if __name__ == '__main__':
    main()
