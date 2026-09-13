"""Freeze 48 existing translations and adjusted assistant judgments without regeneration."""
from __future__ import annotations

import argparse
from pathlib import Path

from common import (CALIBRATION_POLICY, EXPERIMENT_ROOT, ROOT, file_record,
                    read_json, read_jsonl, sha_file, sha_text, write_json, write_jsonl)

DATASETS = [
    ('dev', 'content/model-comparison/linguistic-dev-20260910.jsonl',
     'b8a2b91802e36d96654631a9965566f774e050ea4139ca7270d9f84d2a916848',
     '.training/comparisons/linguistic-dev-20260910/assistant-review-hy7-tg12-v1.json',
     '58d6263620fb5000e187370341cac160ecafe734e14432a35a866b8f309315f2'),
    ('reading', 'content/model-comparison/real-reading-check-20260910/sources.jsonl',
     'becd55204f1533adf68c0512638f17db676d4848a175367c841ed71fca34f8d0',
     '.training/comparisons/real-reading-check-20260910/assistant-review-hy7-tg12-v1.json',
     '0e88a6312d1ef220bf3ce59bbd6fce05187e62c9129d3f6d8b8357937d830786'),
]
NAMED_SOURCE_IDS = {'LDEV26-008': 'equity_word_sense', 'REAL26-004': 'treasury_bill_maturity',
                    'REAL26-005': 'division_direction', 'REAL26-006': 'perpetual_growth_condition'}


def existing_path(path):
    # Frozen summaries use absolute paths. Require them to remain in this workspace.
    result = Path(path).resolve()
    if not result.is_relative_to(ROOT):
        raise ValueError('frozen_evidence_outside_workspace')
    return result


def prepare(output: Path):
    if output.exists():
        raise ValueError('output_already_exists')
    all_inputs, all_labels, records = [], [], []
    for split, source_path, source_hash, summary_path, summary_hash in DATASETS:
        source_path, summary_path = ROOT / source_path, ROOT / summary_path
        if sha_file(source_path) != source_hash or sha_file(summary_path) != summary_hash:
            raise ValueError('frozen_source_or_summary_changed')
        sources = {row['id']: row for row in read_jsonl(source_path)}
        summary = read_json(summary_path)
        if not summary['completed'] or summary['humanReviewed'] or summary['judgmentCount'] != len(sources) * 2:
            raise ValueError('unexpected_frozen_summary')
        records += [file_record(source_path), file_record(summary_path)]
        judgments = {}
        for review_file in summary['reviewFiles']:
            review_path = existing_path(review_file['path'])
            if sha_file(review_path) != review_file['sha256']:
                raise ValueError('frozen_adjusted_review_changed')
            records.append(file_record(review_path))
            for review in read_jsonl(review_path):
                if review['humanReviewed'] or review['reviewerType'] != 'assistant':
                    raise ValueError('incorrect_review_provenance')
                if review['sourceSha256'] != sha_text(sources[review['id']]['source']):
                    raise ValueError('review_source_mismatch')
                for judgment in review['judgments']:
                    key = (review['id'], judgment['targetSha256'])
                    if key in judgments:
                        raise ValueError('duplicate_adjusted_judgment')
                    judgments[key] = judgment
        for system in summary['systems']:
            directory = existing_path(system['directory'])
            prediction_path = directory / 'predictions.jsonl'
            if sha_file(prediction_path) != system['predictionsSha256']:
                raise ValueError('frozen_predictions_changed')
            records.append(file_record(prediction_path))
            predictions = read_jsonl(prediction_path)
            if len(predictions) != len(sources) or {row['id'] for row in predictions} != set(sources):
                raise ValueError('missing_or_duplicate_prediction')
            for prediction in predictions:
                source = sources[prediction['id']]
                translation = prediction['translation']
                source_sha, target_sha = sha_text(source['source']), sha_text(translation)
                if prediction['status'] != 'completed' or prediction['sourceSha256'] != source_sha:
                    raise ValueError('prediction_source_mismatch')
                judgment = judgments.pop((source['id'], target_sha))
                identifier = split + ':' + directory.name + ':' + source['id']
                context = source.get('context', '')
                provenance = source.get('provenance')
                document_id = source.get('documentId') or (provenance.get('sourceVersionId', split) if isinstance(provenance, dict) else split)
                all_inputs.append({'id': identifier, 'sourceId': source['id'], 'split': split,
                    'translationSystem': directory.name, 'source': source['source'], 'translation': translation,
                    'context': context, 'sourceSha256': source_sha, 'translationSha256': target_sha,
                    'contextSha256': sha_text(context), 'domain': source.get('domain', 'finance'),
                    'documentId': document_id,
                    'sourceLength': len(source['source']), 'translationLength': len(translation)})
                material = judgment['severity'] >= 2
                all_labels.append({'id': identifier, 'sourceId': source['id'],
                    'sourceSha256': source_sha, 'translationSha256': target_sha,
                    'severity': judgment['severity'], 'materialError': material,
                    'fluent': judgment['fluent'], 'errors': judgment['errors'],
                    'namedError': NAMED_SOURCE_IDS.get(source['id']) if material else None,
                    'reviewerType': 'assistant', 'humanReviewed': False,
                    'judgmentProvenance': 'existing_adjusted_review_reused_without_changes'})
        if judgments:
            raise ValueError('unmatched_adjusted_judgment')
    if len(all_inputs) != 48 or len({row['id'] for row in all_inputs}) != 48:
        raise ValueError('expected_exactly_48_unique_items')
    if sum(row['materialError'] for row in all_labels) != 14:
        raise ValueError('expected_14_existing_material_errors')
    if sum(bool(row['namedError']) for row in all_labels) != 6:
        raise ValueError('expected_6_named_error_outputs')
    output.mkdir(parents=True)
    write_jsonl(output / 'inputs.jsonl', sorted(all_inputs, key=lambda row: row['id']))
    write_jsonl(output / 'judgments.jsonl', sorted(all_labels, key=lambda row: row['id']))
    write_json(output / 'policy.json', CALIBRATION_POLICY)
    write_json(output / 'manifest.json', {'schemaVersion': 1, 'purpose': 'initial_QE_development_calibration_only',
        'count': 48, 'sourceCount': 24, 'materialErrorCount': 14, 'humanReviewed': False,
        'independentFinalCertification': False, 'trainingUseAllowed': False,
        'referenceTranslationsSentToModel': False, 'generationPerformed': False,
        'inputs': file_record(output / 'inputs.jsonl'), 'judgments': file_record(output / 'judgments.jsonl'),
        'policy': file_record(output / 'policy.json'), 'evidenceFiles': records,
        'preparationCode': file_record(Path(__file__)),
        'limitations': ['24 sources with two correlated translation systems; not 48 independent sources',
            'only two general-domain sources; no broad Korean quality certification',
            'threshold is selected on these same development observations and is optimistic']})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=EXPERIMENT_ROOT / 'dev48-v1')
    arguments = parser.parse_args()
    prepare(arguments.output.resolve())
    print('Prepared exactly 48 frozen source/translation pairs and existing adjusted judgments.')
