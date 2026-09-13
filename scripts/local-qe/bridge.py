"""NDJSON bridge; refuses unregistered/unaccepted QE, never falls back to an LLM."""
from __future__ import annotations

import json
import sys

from calibrate import require_current_registration_policy
from common import (MODEL_ID, MODEL_REVISION, MODEL_SHA, QE_ROOT, canonical, read_json,
                    validate_request, verify_record)


def registration():
    path = QE_ROOT / 'manifest.json'
    if not path.exists():
        raise ValueError('qe_not_registered')
    manifest = read_json(path)
    if (manifest['schemaVersion'] != 1 or manifest['calibration']['accepted'] is not True or
            manifest['calibration']['scoreDirection'] != 'lower-is-risk' or
            (manifest['modelId'], manifest['modelRevision'], manifest['modelHash']) != (MODEL_ID, MODEL_REVISION, MODEL_SHA)):
        raise ValueError('qe_registration_invalid')
    for record in manifest['files']:
        verify_record(record)
    evidence = read_json(verify_record({'path': manifest['calibration']['evidencePath'], 'sha256': manifest['calibration']['evidenceSha256']}))
    require_current_registration_policy(evidence)
    if not evidence['accepted'] or not evidence['namedErrorsPassed'] or not evidence['observed']['accepted']:
        raise ValueError('qe_quality_gate_failed')
    if evidence['threshold'] != manifest['calibration']['threshold'] or evidence['version'] != manifest['calibration']['version']:
        raise ValueError('qe_calibration_identity_mismatch')
    return manifest


def main():
    engine = None
    try:
        for line in sys.stdin:
            request_id = None
            try:
                if len(line) > 200000:
                    raise ValueError('qe_request_too_large')
                request = json.loads(line)
                request_id = request.get('id') if isinstance(request, dict) else None
                validate_request(request)
                manifest = registration()
                if engine is None:
                    from backend import Engine
                    engine = Engine()
                if engine.model_identity != manifest['modelIdentity']:
                    raise ValueError('qe_runtime_identity_mismatch')
                result = engine.score(request)
                result['calibrationVersion'] = manifest['calibration']['version']
                result['risk'] = 'review' if result['score'] <= manifest['calibration']['threshold'] else 'no_findings'
            except Exception as exc:
                code = str(exc) if isinstance(exc, (ValueError, RuntimeError)) and len(str(exc)) < 150 and ' ' not in str(exc) else type(exc).__name__
                result = {'id': request_id, 'status': 'unavailable' if code in ('qe_not_registered', 'qe_quality_gate_failed', 'translation_model_still_running', 'insufficient_memory_before_qe_load') or code.startswith('qe_runtime_lock_') else 'failed',
                          'risk': 'unknown', 'score': None, 'spans': [], 'errorCode': code}
            print(canonical(result), flush=True)
    finally:
        if engine is not None:
            engine.close()


if __name__ == '__main__':
    main()
