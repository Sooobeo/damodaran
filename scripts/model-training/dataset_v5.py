"""Validate canonical, source-grounded v5 annotations without model predictions.

Dataset text remains under ignored .training. Automatic checks establish spelling,
numeral and split integrity; an assistant source review is still required.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import train

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / '.training/datasets/finance-v5'
CATALOG_SHA256 = 'd4af6a9fed8c6dba4c528abc52c64c19621c5fac98fd2bdb09e035769963de67'


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def load_catalog(root=None):
    file = (root or DEST) / 'term-catalog.json'
    if train.sha256(file) != CATALOG_SHA256:
        raise ValueError('Frozen v5 terminology catalog changed')
    return json.loads(file.read_text('utf-8'))['terms']


PUBLICATION_VERSION = 'finance-v5-reviewed-canonical-v1'
PUBLICATION_CODE_FILES = ('assemble_v5_dataset.py', 'dataset_v5.py', 'dataset_io.py', 'train_v5.py', 'train.py')


def read_json(path):
    return json.loads(path.read_text('utf-8-sig'))


def read_dataset(path):
    return [json.loads(line) for line in path.read_text('utf-8-sig').splitlines() if line.strip()]


def reviewed_input(name, split, catalog, root=None):
    """Check the review receipt against both immutable input and review evidence."""
    root = root or DEST
    if name not in ('train-a', 'train-b', 'dev', 'test'):
        raise ValueError('Unknown reviewed input')
    file = root / (name + '-reviewed.jsonl')
    receipt_path = root / (name + '-review-receipt.json')
    receipt = read_json(receipt_path)
    if (receipt.get('humanReviewed') is not False or receipt.get('reviewerType') != 'assistant'
            or receipt.get('completed') is not True or receipt.get('outputSha256') != train.sha256(file)):
        raise ValueError('Missing or stale independent assistant review: ' + name)
    evidence = receipt.get('evidenceFiles')
    candidate_name, review_name = name + '-candidate.jsonl', name + '-second-review.json'
    if not isinstance(evidence, dict) or not {candidate_name, review_name}.issubset(evidence):
        raise ValueError('Review receipt lacks required input/review evidence: ' + name)
    for relative, expected in evidence.items():
        path = root / relative
        if (not isinstance(relative, str) or Path(relative).name != relative or path.is_symlink()
                or path.parent.resolve() != root.resolve() or train.sha256(path) != expected):
            raise ValueError('Review evidence changed: ' + name)
    values = read_dataset(file)
    review = read_json(root / review_name)
    # The sealed test review predates the common receipt schema. Accept only its
    # exact equivalent fields; never modify that already reviewed artifact.
    legacy_test = name == 'test' and review.get('schemaVersion') == 'finance-v5-test-second-review-v1'
    count = review.get('rowCount', review.get('coverage', {}).get('rowCount') if legacy_test else None)
    identifiers = review.get('reviewedIDs', review.get('reviewScope', {}).get('reviewedRowIds') if legacy_test else None)
    completed = review.get('completed') is True or (legacy_test and review.get('status') == 'pass_after_prefreeze_corrections')
    assistant = review.get('reviewerType') == 'assistant' or (legacy_test and review.get('reviewer') == 'independent_assistant_review_agent_v5_test_review')
    if (receipt.get('rowCount') != len(values) or count != len(values) or not completed or not assistant
            or review.get('humanReviewed') is not False or review.get('sourceGrounded') is not True
            or review.get('inputSha256') != evidence[candidate_name]
            or review.get('outputSha256') != receipt['outputSha256']
            or not isinstance(identifiers, list) or len(identifiers) != len(values)
            or len(set(identifiers)) != len(values) or set(identifiers) != {row['id'] for row in values}):
        raise ValueError('Review coverage/evidence contract mismatch: ' + name)
    report = validate_rows(values, catalog, split)
    if not report['passed']:
        raise ValueError(json.dumps({'file': name, 'issues': report['issues']}))
    return values, {'path': name + '-reviewed.jsonl', 'sha256': train.sha256(file),
                    'receiptSha256': train.sha256(receipt_path), 'evidenceFiles': evidence, 'validation': report}


def validate_coverage(data, catalog):
    expected_ids = {term['id'] for term in catalog}
    new = data['train-a'] + data['train-b']
    primary = Counter(row.get('primaryTermId') for row in new if row['domain'] == 'finance')
    if (len(new) != 728 or set(primary) != expected_ids or set(primary.values()) != {12}
            or sum(row['domain'] == 'general' for row in new) != 80):
        raise ValueError('New training coverage must be 12 examples for all 54 terms plus 80 general rows')
    for label in ('dev', 'test'):
        values = data[label]
        coverage = Counter(row.get('primaryTermId') for row in values if row['domain'] == 'finance')
        if (len(values) != 140 or set(coverage) != expected_ids or set(coverage.values()) != {2}
                or sum(row['domain'] == 'general' for row in values) != 32
                or sum(row['domain'] == 'general' and bool(row.get('forbiddenTerms')) for row in values) < 16):
            raise ValueError('Evaluation coverage changed: ' + label)


def verify_published_dataset(paths, root=None):
    """Fail closed before training reads rows from anything but reviewed publication."""
    root = (root or DEST).resolve()
    manifest_path = root / 'dataset-manifest.json'
    if not manifest_path.is_file():
        raise ValueError('Published v5 dataset-manifest.json is required before training')
    manifest = read_json(manifest_path)
    policy_path = root / 'annotation-policy.json'
    catalog = load_catalog(root)
    if (manifest.get('version') != PUBLICATION_VERSION or manifest.get('humanReviewed') is not False
            or manifest.get('catalogSha256') != CATALOG_SHA256
            or manifest.get('annotationPolicySha256') != train.sha256(policy_path)
            or read_json(policy_path).get('catalogSha256') != CATALOG_SHA256
            or manifest.get('validation', {}).get('passed') is not True
            or manifest.get('validation', {}).get('issues') != []
            or manifest.get('validation', {}).get('historicHoldoutIssues') != []):
        raise ValueError('Published v5 policy or validation changed')
    expected_code = {name: train.sha256(Path(__file__).with_name(name)) for name in PUBLICATION_CODE_FILES}
    if manifest.get('codeFiles') != expected_code:
        raise ValueError('Published v5 data dependency code changed')
    if set(paths) != {'train', 'dev', 'test'} or set(manifest.get('files', {})) != set(paths):
        raise ValueError('Published split contract changed')
    for split, path in paths.items():
        if path.resolve() != root / (split + '.jsonl') or path.is_symlink():
            raise ValueError('Use only the final published v5 split paths')
        if train.sha256(path) != manifest['files'][split].get('sha256'):
            raise ValueError('Published v5 split hash changed: ' + split)
    reviewed = {}
    for name in ('train-a', 'train-b', 'dev', 'test'):
        values, record = reviewed_input(name, 'train' if name.startswith('train-') else name, catalog, root)
        if manifest.get('reviewedInputs', {}).get(name) != record:
            raise ValueError('Published review receipt/evidence changed: ' + name)
        reviewed[name] = values
    validate_coverage(reviewed, catalog)
    for split, path in paths.items():
        values = read_dataset(path)
        record = manifest['files'][split]
        if (record.get('count') != len(values) or record.get('domains') != dict(Counter(r['domain'] for r in values))
                or record.get('sourceWords') != sum(len(r['source'].split()) for r in values)):
            raise ValueError('Published split statistics changed: ' + split)
        # Every final fresh row must be the exact enriched reviewed row. Replay
        # rows precede new training and keep the separately pinned v4 lineage.
        expected = reviewed['train-a'] + reviewed['train-b'] if split == 'train' else reviewed[split]
        fresh = [enrich(r) for r in expected]
        if (values[-len(fresh):] if split == 'train' else values) != fresh:
            raise ValueError('Published rows differ from their reviewed input: ' + split)
    return {'manifestPath': train.relative(manifest_path), 'manifestSha256': train.sha256(manifest_path),
            'catalogSha256': CATALOG_SHA256, 'annotationPolicySha256': train.sha256(policy_path),
            'codeFiles': expected_code, 'reviewReceiptHashes': {name: value['receiptSha256'] for name, value in manifest['reviewedInputs'].items()}}


def catalog_matches(source, catalog):
    """Longest non-overlapping literal phrase; one annotation per catalog ID."""
    matches = []
    for term in catalog:
        for variant in [term['source'], *term.get('aliases', [])]:
            if not re.search('[A-Za-z]', variant):
                continue
            pattern = r'(?<![A-Za-z0-9_])' + r'\s+'.join(re.escape(v) for v in variant.split()) + r'(?![A-Za-z0-9_])'
            flags = 0 if re.fullmatch(r'[A-Z][A-Z0-9/&.-]{1,10}', variant) else re.IGNORECASE
            for match in re.finditer(pattern, source, flags):
                matches.append((match.start(), match.end(), term, match.group()))
    selected, seen = [], set()
    for begin, end, term, literal in sorted(matches, key=lambda v: (-(v[1]-v[0]), v[0], v[2]['id'])):
        if any(begin < e and end > b for b, e, _, _ in selected):
            continue
        selected.append((begin, end, term, literal))
    result = []
    for _, _, term, literal in sorted(selected, key=lambda v: v[0]):
        if term['id'] not in seen:
            result.append({'id': term['id'], 'source': literal, 'target': term['target']})
            seen.add(term['id'])
    return result


def enrich(row):
    return {**row, 'sourceSha256': digest(row['source']), 'targetSha256': digest(row['target']),
            'sourceWordCount': len(row['source'].split())}


def validate_rows(rows, catalog, expected_split, require_canonical=True):
    lookup = {r['id']: r for r in catalog}
    seen = set()
    issues = []
    for row in rows:
        rid = row['id']
        row_issues = []
        if rid in seen or row.get('split') != expected_split:
            row_issues.append('id-or-split-mismatch')
        seen.add(rid)
        if row.get('humanReviewed') is not False:
            row_issues.append('human-review-provenance-mismatch')
        if row.get('domain') not in ('finance', 'general'):
            row_issues.append('invalid-domain')
        if '\ufffd' in row['source'] + row['target'] or '??' in row['target']:
            row_issues.append('encoding-corruption')
        if train.numeric_tokens(row['source']) != train.numeric_tokens(row['target']):
            row_issues.append('numeral-mismatch')
        for key, field in [('sourceSha256', 'source'), ('targetSha256', 'target')]:
            if key in row and row[key] != digest(row[field]):
                row_issues.append(key + '-mismatch')
        annotated = row.get('terms', [])
        if require_canonical:
            if any(not isinstance(t, dict) or t.get('id') not in lookup for t in annotated):
                row_issues.append('unknown-catalog-annotation')
            else:
                ids = [t['id'] for t in annotated]
                if len(ids) != len(set(ids)):
                    row_issues.append('duplicate-catalog-id')
                expected = catalog_matches(row['source'], catalog) if row['domain'] == 'finance' else []
                exclusions = row.get('excludedTermIds', {})
                if (not isinstance(exclusions, dict) or any(not isinstance(v, str) or not v.strip() for v in exclusions.values())):
                    row_issues.append('invalid-source-sense-exclusion')
                    exclusions = {}
                expected_ids = {t['id'] for t in expected} - set(exclusions)
                if set(ids) != expected_ids:
                    row_issues.append('source-term-coverage:' + ','.join(sorted(set(ids) ^ expected_ids)))
                for term in annotated:
                    if term['target'] != lookup[term['id']]['target']:
                        row_issues.append('canonical-target-mismatch:' + term['id'])
                    if term.get('source', '').casefold() not in row['source'].casefold() or not term.get('source'):
                        row_issues.append('source-term-absent:' + term['id'])
                if row['domain'] == 'finance' and row.get('primaryTermId') not in ids:
                    row_issues.append('missing-primary-term')
        for term in train.term_targets(row):
            if train.normalized_term(term) not in train.normalized_term(row['target']):
                row_issues.append('canonical-reference-term-absent')
        for term in row.get('forbiddenTerms', []):
            if not isinstance(term, dict) or not term.get('target') or not term.get('reason'):
                row_issues.append('invalid-forbidden-annotation')
            elif train.normalized_term(term['target']) in train.normalized_term(row['target']):
                row_issues.append('forbidden-term-in-reference')
        if row_issues:
            issues.append({'id': rid, 'issues': row_issues})
    return {'count': len(rows), 'domains': dict(Counter(r['domain'] for r in rows)),
            'primaryCoverage': dict(Counter(r.get('primaryTermId') for r in rows if r['domain'] == 'finance')),
            'termAnnotations': sum(len(train.term_targets(r)) for r in rows),
            'numericRows': sum(bool(train.numeric_tokens(r['source'])) for r in rows),
            'forbiddenRows': sum(bool(r.get('forbiddenTerms')) for r in rows),
            'sourceWords': sum(len(r['source'].split()) for r in rows), 'issues': issues, 'passed': not issues}
