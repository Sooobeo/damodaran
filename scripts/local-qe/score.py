"""Score the frozen 48-row development set with the independently installed local QE model."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from common import (EXPERIMENT_ROOT, ROOT, canonical, file_record, read_json, read_jsonl,
                    verify_record, write_json)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, default=EXPERIMENT_ROOT / 'dev48-v1')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run', action='store_true', help='Explicitly run actual local model inference.')
    args = parser.parse_args()
    if not args.run:
        parser.error('--run is required for actual local model inference')
    prepared, output = args.prepared.resolve(), args.output.resolve()
    if output.exists() or not output.is_relative_to(EXPERIMENT_ROOT.resolve()):
        raise ValueError('output_must_be_new_and_owned')
    manifest = read_json(prepared / 'manifest.json')
    inputs = read_jsonl(verify_record(manifest['inputs']))
    verify_record(manifest['judgments'])
    verify_record(manifest['policy'])
    if len(inputs) != 48:
        raise ValueError('expected_48_frozen_inputs')
    output.mkdir(parents=True)
    write_json(output / 'start.json', {'status': 'starting', 'count': len(inputs),
        'preparedManifest': file_record(prepared / 'manifest.json'),
        'codeFiles': [file_record(path) for path in sorted((ROOT / 'scripts/local-qe').glob('*.py'))],
        'referenceTranslationsUsed': False, 'humanReviewed': False, 'appRegistered': False})
    from backend import Engine, memory_status
    started, completed = time.monotonic(), 0
    engine, error = None, None
    try:
        with Engine(emergency_directory=output) as engine:
            write_json(output / 'runtime.json', {'identity': engine.identity, 'modelIdentity': engine.model_identity,
                'loadSeconds': engine.load_seconds, 'memoryAfterLoad': memory_status()})
            with (output / 'scores.jsonl').open('x', encoding='utf-8', newline='\n') as stream:
                for request in inputs:
                    result = engine.score(request)
                    stream.write(canonical(result) + '\n')
                    stream.flush()
                    completed += 1
                    print(canonical({'event': 'scored', 'completed': completed, 'count': len(inputs)}), flush=True)
    except Exception as exc:
        # Error text can include input strings in dependencies; expose only a bounded type/code.
        error = str(exc) if str(exc).startswith(('qe_', 'model_', 'candidate_', 'insufficient_', 'translation_', 'package_', 'file_', 'unsupported_')) else type(exc).__name__
    summary = {'status': 'completed' if error is None and completed == len(inputs) else 'failed',
               'completed': completed, 'selectedCount': len(inputs), 'errorCode': error,
               'seconds': round(time.monotonic() - started, 6), 'engineClosed': True,
               'peakWorkingSet': engine.peak_working_set if engine else None,
               'minimumAvailablePhysical': engine.min_available if engine else None,
               'finalMemory': memory_status(), 'humanReviewed': False, 'qualityGatePassed': False,
               'appRegistered': False}
    if (output / 'scores.jsonl').exists():
        summary['scores'] = file_record(output / 'scores.jsonl')
    write_json(output / 'summary.json', summary)
    print(canonical(summary))
    if error:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
