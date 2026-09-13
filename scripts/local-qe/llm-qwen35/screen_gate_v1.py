"""External development-screen stopping rule; never included in model input.

Reads the fixed eight-row selection and old development labels. It does not
reinterpret a model's reason, alter labels, run inference, or certify quality.
"""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[3]
VERSION = 'qwen-semantic-v2-dev8-stop-v1'
MAPPING_VERSION = 'qwen-material-warning-policy-v3'
FILES = {
    'selection': ('.training/quality-evaluation/qwen-semantic-v2-dev8/selection-v1.json',
                  'f724e2b2dcbbf264c62208889b3c37dd75ac58ef4e6878b3ccb2d368c6266f7f'),
    'input': ('.translation/qe/llm-candidates/qwen35-9b/inputs/semantic-v2-dev8-v1.jsonl',
              '55e52db41f16e35998eab1f3835f60aafc7e1a875a38b1d32a00dd5ec49e68ce'),
    'labels': ('.training/quality-evaluation/dev48-v1/judgments.jsonl',
               '5e466ac8aaec742c5db7b4ed3f163f63273a5a8facbc274b1776d402f7046b19'),
}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _load():
    raw = {}
    for key, (relative, expected) in FILES.items():
        raw[key] = (ROOT / relative).read_bytes()
        require(sha(raw[key]) == expected, 'frozen_screen_evidence_changed:' + key)
    selection = json.loads(raw['selection'])
    inputs = [json.loads(line) for line in raw['input'].decode('utf-8').splitlines()]
    labels = [json.loads(line) for line in raw['labels'].decode('utf-8').splitlines()]
    gt = {row['id']: row for row in labels}
    selected = selection['choicesInRunOrder']
    order = [row['selectedId'] for row in selected]
    require(len(labels) == len(gt) == 48 and sum(row['materialError'] is True for row in labels) == 14,
            'full48_label_inventory_invalid')
    require(len(set(order)) == len(inputs) == len(order) == 8
            and [row['id'] for row in inputs] == order, 'screen_inventory_invalid')
    for source, item in zip(inputs, selected, strict=True):
        require(set(source) == {'id', 'source', 'translation', 'context'}, 'model_projection_fields_invalid')
        label = gt[source['id']]
        require(type(label['materialError']) is bool and label['materialError'] is item['materialError'],
                'selection_label_changed')
        for field in ('source', 'translation'):
            require(sha(source[field].encode('utf-8')) == label[field + 'Sha256'], 'label_text_link_changed')
    require(sum(gt[key]['materialError'] for key in order) == 4, 'screen_balance_changed')
    return order, {key: gt[key]['materialError'] for key in order}


def identity():
    order, _ = _load()
    return {'version': VERSION, 'codeSha256': sha(Path(__file__).read_bytes()),
            'files': {key: {'path': path, 'sha256': digest} for key, (path, digest) in FILES.items()},
            'inputIdsInOrder': order, 'expectedCount': 8, 'materialErrorCount': 4,
            'full48MaterialErrorCount': 14, 'minimumRecall': .95, 'minimumPrecision': .95,
            'modelRequestContainsGateOrLabels': False, 'independentHoldout': False,
            'boundsAssumeRetainingThisObservedOutput': True,
            'automaticMergeIntoFull48': False, 'allEightCorrectIsAcceptance': False}


def check(row_id, mapping):
    """Return a stopping decision only after the caller saved a valid assessment.

    The caller must validate raw native evidence and enforce the fixed row order.
    This pure function neither grants runtime acceptance nor performs cleanup.
    """
    _, labels = _load()
    require(type(row_id) is str and row_id in labels, 'row_outside_fixed_development_screen')
    require(type(mapping) is dict and mapping.get('version') == MAPPING_VERSION,
            'unsupported_screen_mapping')
    warning = mapping.get('primaryWarning')
    require(mapping.get('status') == 'assessed_model_assertions' and type(warning) is bool,
            'screen_requires_valid_assessed_mapping')
    expected = labels[row_id]
    match = warning is expected
    reason = 'matched_fixed_development_label' if match else 'false_negative' if expected else 'false_positive'
    return {'version': VERSION, 'rowId': row_id, 'continueRun': match, 'reason': reason,
            'expectedMaterialError': expected, 'observedPrimaryWarning': warning,
            'bestPossibleFull48RecallAfterThisError': None if match or not expected else 13 / 14,
            'bestPossibleFull48PrecisionAfterThisError': None if match or expected else 14 / 15,
            'developmentDiagnosisOnly': True, 'semanticSpanPrecision': 'not_evaluated',
            'boundsAssumeRetainingThisObservedOutput': True,
            'fullBaselineAccepted': False, 'registrationPerformed': False}
