"""Evaluate warning thresholds against fixed, previously adjudicated assistant judgments."""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from common import (CALIBRATION_POLICY, CURRENT_POLICY_PATH, EXPERIMENT_ROOT, PRODUCT_QE_POLICY, PRODUCT_QE_POLICY_V1, file_record, read_json,
                    read_jsonl, validate_score, verify_record, write_json)


def metrics(rows, threshold):
    warnings = [row for row in rows if row['score'] <= threshold]
    positives = [row for row in rows if row['materialError']]
    tp = sum(row['materialError'] for row in warnings)
    return {'count': len(rows), 'materialErrorCount': len(positives), 'warnings': len(warnings),
            'truePositives': tp, 'falsePositives': len(warnings) - tp,
            'falseNegatives': len(positives) - tp,
            'recall': tp / len(positives) if positives else None,
            'precision': tp / len(warnings) if warnings else None,
            'warningRate': len(warnings) / len(rows) if rows else None}


def meets_policy(result, named_passed, policy):
    return (result['recall'] is not None and result['recall'] >= policy['minimumRecall'] and
            result['precision'] is not None and result['precision'] >= policy['minimumPrecision'] and
            result['warningRate'] <= policy['maximumWarningRate'] and named_passed)


def slice_gate(rows, threshold, policy):
    """One common threshold must pass every baseline, split, and their cross product."""
    systems = sorted({row['translationSystem'] for row in rows})
    splits = sorted({row['split'] for row in rows})
    groups = [('translationSystem', {'translationSystem': system},
               [row for row in rows if row['translationSystem'] == system]) for system in systems]
    groups += [('split', {'split': split}, [row for row in rows if row['split'] == split]) for split in splits]
    groups += [('translationSystem_x_split', {'translationSystem': system, 'split': split},
                [row for row in rows if row['translationSystem'] == system and row['split'] == split])
               for system in systems for split in splits]
    slices = []
    for kind, key, group in groups:
        result = metrics(group, threshold)
        reasons = []
        if not result['count']:
            reasons.append('no_observations')
        if not result['materialErrorCount']:
            reasons.append('no_material_error_positives_recall_unassessed')
        if not result['warnings']:
            reasons.append('no_predicted_warnings_precision_unassessed')
        passed = not reasons and meets_policy(result, True, policy)
        slices.append({'kind': kind, 'key': key, **result, 'accepted': passed,
                       'status': 'unassessed_requires_additional_validation' if reasons else 'passed' if passed else 'failed',
                       'unassessedReasons': reasons})
    return {'requiredKinds': policy['requiredSlices'], 'slices': slices,
            'accepted': bool(slices) and all(item['accepted'] for item in slices),
            'additionalValidationRequired': any(item['unassessedReasons'] for item in slices)}


def analyze(inputs, labels, scores, policy=CALIBRATION_POLICY):
    if policy not in (CALIBRATION_POLICY, PRODUCT_QE_POLICY_V1, PRODUCT_QE_POLICY):
        raise ValueError('calibration_policy_changed')
    score_map = {row['id']: row for row in scores}
    label_map = {row['id']: row for row in labels}
    input_ids = {row['id'] for row in inputs}
    if (len(scores) != len(score_map) or len(labels) != len(label_map) or
            len(inputs) != len(input_ids) or set(score_map) != input_ids or set(label_map) != input_ids):
        raise ValueError('missing_extra_or_duplicate_rows')
    identities = {row['modelIdentity'] for row in scores}
    if len(identities) != 1:
        raise ValueError('mixed_model_identities')
    rows = []
    for item in inputs:
        score, label = score_map[item['id']], label_map[item['id']]
        for field in ('sourceSha256', 'translationSha256'):
            if not item[field] == score[field] == label[field]:
                raise ValueError('assessment_input_hash_mismatch')
        if score['status'] != 'completed' or score['contextUsed'] or score['truncated']:
            raise ValueError('incomplete_or_unsupported_score')
        rows.append({**item, **label, 'score': validate_score(score['score'])})
    thresholds = sorted({row['score'] for row in rows})
    curve = []
    for threshold in thresholds:
        result = metrics(rows, threshold)
        named = all(row['score'] <= threshold for row in rows if row['namedError'])
        point = {'threshold': threshold, **result, 'namedErrorsPassed': named,
                 'accepted': meets_policy(result, named, policy)}
        if policy == PRODUCT_QE_POLICY:
            point['perBaselineGate'] = slice_gate(rows, threshold, policy)
            point['overallGatePassed'] = point['accepted']
            point['accepted'] = point['accepted'] and point['perBaselineGate']['accepted']
        curve.append(point)
    eligible = [point for point in curve if point['accepted']]
    if eligible:
        selection = max(eligible, key=lambda point: (point['precision'], -point['warningRate'], point['recall']))
    else:
        # Diagnostic only. The failed candidate is never registered or called calibrated.
        selection = min(curve, key=lambda point: (not point['namedErrorsPassed'],
            point['recall'] < policy['minimumRecall'], point['warningRate']))
    threshold = selection['threshold']
    named_rows = [{'id': row['id'], 'type': row['namedError'], 'score': row['score'],
                   'warned': row['score'] <= threshold} for row in rows if row['namedError']]
    group_metrics = {}
    for field in ('translationSystem', 'split', 'domain', 'documentId'):
        groups = defaultdict(list)
        for row in rows:
            groups[row[field]].append(row)
        group_metrics[field] = {name: metrics(group, threshold) for name, group in sorted(groups.items())}
    midpoint = sorted(row['sourceLength'] for row in rows)[len(rows) // 2]
    group_metrics['sourceLength'] = {name: metrics([row for row in rows if (row['sourceLength'] <= midpoint) == is_short], threshold)
                                   for name, is_short in [('shorterHalf', True), ('longerHalf', False)]}
    categories = sorted({error['category'] for row in rows for error in row['errors'] if error['severity'] >= 2})
    by_category = {}
    for category in categories:
        affected = [row for row in rows if any(error['category'] == category and error['severity'] >= 2 for error in row['errors'])]
        found = sum(row['score'] <= threshold for row in affected)
        by_category[category] = {'materialErrorRows': len(affected), 'warnedRows': found, 'recall': found / len(affected)}
    accepted = bool(selection['accepted'])
    report = {'schemaVersion': 1, 'version': policy['version'], 'modelIdentity': next(iter(identities)),
            'accepted': accepted, 'appRegistrationAllowed': accepted,
            'status': 'development_gate_passed' if accepted else 'development_gate_failed',
            'threshold': threshold, 'scoreDirection': 'lower-is-risk', 'comparison': 'score<=threshold',
            'policy': policy, 'observed': selection, 'namedErrorsPassed': all(row['warned'] for row in named_rows),
            'namedErrors': named_rows, 'thresholdCurve': curve, 'byGroup': group_metrics,
            'byErrorCategory': by_category, 'sourceLengthMedian': midpoint,
            'errorSpanEvaluation': {'supported': False, 'reason': 'sentence_score_only_model_no_error_spans'},
            'humanReviewed': False, 'independentFinalCertification': False,
            'limitations': ['threshold selected and measured on the same small development set',
                'two translations of 24 shared sources are correlated observations',
                'warning status does not establish translation correctness or localize an error',
                'no general-domain or new-document performance guarantee']}
    if policy == PRODUCT_QE_POLICY:
        report['perBaselineGate'] = selection['perBaselineGate']
        report['metricUnit'] = policy['metricUnit']
        report['individualErrorDetection'] = {'assessed': False,
            'reason': 'one paragraph warning does not identify each error or prove all errors were detected'}
        report['byErrorCategoryInterpretation'] = 'recall of warned rows containing a category, not recall of detected individual errors'
        report['limitations'].append('a slice with zero positive labels or zero warnings is unassessed and cannot authorize registration')
    return report


def require_current_registration_policy(evidence):
    """A historical 85/60 pass is no longer sufficient for a new registration."""
    if (read_json(CURRENT_POLICY_PATH) != PRODUCT_QE_POLICY or
            evidence.get('policy') != PRODUCT_QE_POLICY or evidence.get('version') != PRODUCT_QE_POLICY['version']):
        raise ValueError('obsolete_or_changed_qe_registration_policy')
    if (len(evidence.get('namedErrors', [])) != PRODUCT_QE_POLICY['requiredNamedErrorCount'] or
            not meets_policy(evidence['observed'], evidence['namedErrorsPassed'], PRODUCT_QE_POLICY)):
        raise ValueError('qe_current_product_gate_failed_not_registered')
    gate = evidence.get('perBaselineGate', {})
    if (gate.get('requiredKinds') != PRODUCT_QE_POLICY['requiredSlices'] or
            gate.get('accepted') is not True or gate.get('additionalValidationRequired') is not False or
            not gate.get('slices') or
            any(item.get('accepted') is not True or item.get('status') != 'passed' or item.get('unassessedReasons') or
                not meets_policy(item, True, PRODUCT_QE_POLICY) for item in gate['slices'])):
        raise ValueError('qe_per_baseline_gate_failed_not_registered')


def calibration(prepared, run, policy_path=None):
    manifest, summary = read_json(prepared / 'manifest.json'), read_json(run / 'summary.json')
    start = read_json(run / 'start.json')
    if verify_record(start['preparedManifest']) != (prepared / 'manifest.json').resolve():
        raise ValueError('scored_prepared_manifest_mismatch')
    if summary['status'] != 'completed' or summary['completed'] != 48 or not summary['engineClosed']:
        raise ValueError('scoring_run_incomplete')
    inputs = read_jsonl(verify_record(manifest['inputs']))
    labels = read_jsonl(verify_record(manifest['judgments']))
    original_policy = read_json(verify_record(manifest['policy']))
    if original_policy != CALIBRATION_POLICY:
        raise ValueError('original_predeclared_policy_changed')
    policy = original_policy
    if policy_path is not None:
        policy = read_json(policy_path)
        if policy != PRODUCT_QE_POLICY:
            raise ValueError('unsupported_explicit_policy_override')
    scores = read_jsonl(verify_record(summary['scores']))
    if len(inputs) != 48 or len(labels) != 48 or len(scores) != 48:
        raise ValueError('calibration_requires_complete_dev48')
    result = analyze(inputs, labels, scores, policy)
    result['evidenceFiles'] = [file_record(prepared / 'manifest.json'), manifest['inputs'], manifest['judgments'], manifest['policy'],
                               file_record(run / 'start.json'), file_record(run / 'summary.json'),
                               file_record(run / 'runtime.json'), summary['scores'], file_record(Path(__file__))]
    if policy_path is not None:
        if sum(bool(row['namedError']) for row in labels) != PRODUCT_QE_POLICY['requiredNamedErrorCount']:
            raise ValueError('required_named_error_count_mismatch')
        result['interpretation'] = {
            'retrospectiveReinterpretation': True,
            'originalPredeclaredPolicy': manifest['policy'],
            'appliedPolicy': file_record(policy_path),
            'originalPolicyVersion': original_policy['version'],
            'appliedPolicyVersion': policy['version'],
            'newModelInferencePerformed': False,
            'existingScoresAndJudgmentsUnchanged': True,
            'notTheOriginalExperimentPredeclaredPolicy': True,
            'paragraphRiskGateOnly': True,
            'spanAccuracyCertified': False,
        }
        result['evidenceFiles'].append(file_record(policy_path))
        result['limitations'].append('95/95 is a subsequently adopted product policy; this is retrospective development reanalysis')
        result['limitations'].append('no error spans are provided; 100% offset validity or 95/95 underline quality is not certified')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, default=EXPERIMENT_ROOT / 'dev48-v1')
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--policy', type=Path, help='Explicit current product policy; creates retrospective reanalysis without changing the original policy/results.')
    args = parser.parse_args()
    result = calibration(args.prepared.resolve(), args.run.resolve(), args.policy.resolve() if args.policy else None)
    write_json(args.output.resolve(), result)
    print('Calibration: ' + result['status'] + '; model remains unregistered until register.py verifies it.')
