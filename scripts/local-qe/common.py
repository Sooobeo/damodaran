"""Isolated, reference-free QE contracts. No model is imported by this module."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QE_ROOT = ROOT / '.translation' / 'qe'
EXPERIMENT_ROOT = ROOT / '.training' / 'quality-evaluation'
VERSION = 'local-qe-v1'
MODEL_ID = 'Unbabel/wmt20-comet-qe-da'
MODEL_REVISION = '2e7ffc84fb67d99cf92506611766463bb9230cfb'
MODEL_SHA = '05d892bf4a3e34b9a4de239109387d43107b2a8c55ad34b73a929ca6c1ede24e'
ENCODER_ID = 'FacebookAI/xlm-roberta-large'
ENCODER_REVISION = 'c23d21b0620b635a76227c604d44e43a9f0ee389'
CANDIDATE = QE_ROOT / 'candidates' / 'wmt20-comet-qe-da'
CALIBRATION_POLICY = {
    'version': 'assistant-dev48-screen-v1',
    'scoreDirection': 'lower-is-risk',
    'thresholdComparison': 'score<=threshold',
    'minimumRecall': 0.85,
    'minimumPrecision': 0.60,
    'maximumWarningRate': 0.50,
    'allNamedErrorsRequired': True,
    'humanReviewed': False,
    'independentFinalCertification': False,
}
PRODUCT_QE_POLICY_V1 = {
    'version': 'product-qe-95-v1',
    'scoreDirection': 'lower-is-risk',
    'thresholdComparison': 'score<=threshold',
    'minimumRecall': 0.95,
    'minimumPrecision': 0.95,
    'maximumWarningRate': 1.0,
    'allNamedErrorsRequired': True,
    'requiredNamedErrorCount': 6,
    'requiredSpanOffsetValidity': 1.0,
    'spanPerformanceMustBeEvaluatedSeparately': True,
    'basis': 'user-delegated product policy, not a universal academic accuracy threshold',
    'adoptedAfterInitialDev48Scoring': True,
    'humanReviewed': False,
    'independentFinalCertification': False,
}
PRODUCT_QE_POLICY = {
    **PRODUCT_QE_POLICY_V1,
    'version': 'product-qe-95-v2',
    'requiredSlices': ['translationSystem', 'split', 'translationSystem_x_split'],
    'requiredSlicesMustEachPass': True,
    'requireCompleteObservedSystemSplitCrossProduct': True,
    'zeroDenominators': 'unassessed_requires_additional_validation',
    'metricUnit': 'translation_output_with_at_least_one_material_error',
    'individualErrorDetectionCertified': False,
}
CURRENT_POLICY_PATH = ROOT / 'scripts/local-qe/policy-product-95-v2.json'


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def write_json(path: Path, value, *, exclusive=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if exclusive else 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n')


def write_jsonl(path: Path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        for value in values:
            stream.write(canonical(value) + '\n')


def safe_path(relative: str, root=ROOT) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('path_outside_owned_root')
    return path


def file_record(path: Path):
    return {'path': path.resolve().relative_to(ROOT).as_posix(), 'sha256': sha_file(path), 'size': path.stat().st_size}


def verify_record(record):
    path = safe_path(record['path'])
    if not path.is_file() or sha_file(path) != record['sha256']:
        raise ValueError('file_identity_mismatch:' + record['path'])
    if 'size' in record and path.stat().st_size != record['size']:
        raise ValueError('file_size_mismatch:' + record['path'])
    return path


def offline_environment():
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    os.environ['HF_HOME'] = str(QE_ROOT / 'hf-cache')


def validate_request(value):
    if not isinstance(value, dict) or not isinstance(value.get('id'), str) or not value['id'] or len(value['id']) > 200:
        raise ValueError('invalid_request_id')
    for field in ('source', 'translation'):
        if not isinstance(value.get(field), str) or not value[field].strip() or len(value[field]) > 30000:
            raise ValueError('invalid_' + field)
    if not isinstance(value.get('context', ''), str) or len(value.get('context', '')) > 30000:
        raise ValueError('invalid_context')
    return value


def validate_score(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('invalid_score')
    return float(value)


def validate_spans(spans, source: str, translation: str):
    """Offsets are explicitly UTF-16 code units, matching JavaScript String.slice."""
    if not isinstance(spans, list) or len(spans) > 100:
        raise ValueError('invalid_spans')
    for span in spans:
        if span.get('side') not in ('source', 'target') or span.get('severity') not in ('minor', 'major', 'critical'):
            raise ValueError('invalid_span_class')
        start, end = span.get('start'), span.get('end')
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            raise ValueError('invalid_span_offset')
        text = source if span['side'] == 'source' else translation
        encoded = text.encode('utf-16-le')
        if not 0 <= start < end <= len(encoded) // 2:
            raise ValueError('span_out_of_bounds')
        try:
            fragment = encoded[start * 2:end * 2].decode('utf-16-le')
        except UnicodeDecodeError as exc:
            raise ValueError('span_splits_surrogate') from exc
        if fragment != span.get('text'):
            raise ValueError('span_text_mismatch')
    return spans
