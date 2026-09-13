"""Register only a complete candidate that passes the predeclared development QE gate."""
from __future__ import annotations

import argparse
from pathlib import Path

from calibrate import calibration, require_current_registration_policy
from common import (CANDIDATE, CURRENT_POLICY_PATH, EXPERIMENT_ROOT, QE_ROOT, ROOT, canonical,
                    file_record, read_json, verify_record, write_json)


def register(prepared: Path, run: Path, evidence_path: Path):
    evidence = read_json(evidence_path)
    require_current_registration_policy(evidence)
    recomputed = calibration(prepared, run, CURRENT_POLICY_PATH)
    if canonical(evidence) != canonical(recomputed):
        raise ValueError('calibration_evidence_mismatch')
    if not evidence['accepted'] or not evidence['namedErrorsPassed'] or not evidence['observed']['accepted']:
        raise ValueError('qe_quality_gate_failed_not_registered')
    candidate = read_json(CANDIDATE / 'manifest.json')
    runtime = read_json(run / 'runtime.json')
    if runtime['modelIdentity'] != evidence['modelIdentity']:
        raise ValueError('evaluated_runtime_identity_mismatch')
    # Frozen execution code must still match before registration.
    for record in read_json(run / 'start.json')['codeFiles']:
        verify_record(record)
    files = candidate['modelFiles'] + candidate['encoderFiles'] + candidate['environmentFiles']
    files += evidence['evidenceFiles'] + [file_record(CANDIDATE / 'manifest.json'), file_record(evidence_path)]
    files += [file_record(path) for path in sorted((ROOT / 'scripts/local-qe').glob('*.py'))]
    unique = {record['path']: record for record in files}
    for record in unique.values():
        verify_record(record)
    manifest = {'schemaVersion': 1, 'modelId': candidate['modelId'], 'modelRevision': candidate['modelRevision'],
                'modelHash': candidate['modelHash'], 'modelIdentity': evidence['modelIdentity'],
                'pythonPath': '.venv-qe/Scripts/python.exe', 'bridgePath': 'scripts/local-qe/bridge.py',
                'files': list(unique.values()), 'contextUsed': False, 'spanSupport': False,
                'calibration': {'version': evidence['version'], 'threshold': evidence['threshold'],
                    'accepted': True, 'scoreDirection': 'lower-is-risk', 'comparison': 'score<=threshold',
                    'evidencePath': evidence_path.relative_to(ROOT).as_posix(),
                    'evidenceSha256': file_record(evidence_path)['sha256']},
                'humanReviewed': False, 'independentFinalCertification': False}
    write_json(QE_ROOT / 'manifest.json', manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, default=EXPERIMENT_ROOT / 'dev48-v1')
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    register(args.prepared.resolve(), args.run.resolve(), args.evidence.resolve())
    print('Registered the exact development-gate-passing QE identity. This is not independent certification.')
