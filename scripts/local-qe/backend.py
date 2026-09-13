"""CPU COMET backend. Import is cheap; Engine construction performs model loading."""
from __future__ import annotations

import ctypes
import importlib.metadata
import os
import sys
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

from common import (CANDIDATE, MODEL_ID, MODEL_REVISION, MODEL_SHA, QE_ROOT,
                    ROOT, canonical, file_record, offline_environment, read_json,
                    safe_path, sha_text, validate_request, validate_score, verify_record,
                    write_json)


def checked_legacy_state(state, current_position_ids):
    """Old Transformers persisted this derived buffer; newer versions do not.

    Remove only after exact equality to the current model buffer. All learned
    parameter names and shapes are still passed to strict=True state loading.
    """
    import torch
    key = 'encoder.model.embeddings.position_ids'
    if key not in state:
        raise RuntimeError('model_expected_legacy_position_buffer_missing')
    legacy = state[key]
    if legacy.dtype != current_position_ids.dtype or legacy.shape != current_position_ids.shape or not torch.equal(legacy, current_position_ids):
        raise RuntimeError('model_legacy_position_buffer_mismatch')
    normalized = state.copy()
    del normalized[key]
    return normalized


class MemoryStatus(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
               ('totalPhysical', ctypes.c_ulonglong), ('availablePhysical', ctypes.c_ulonglong),
               ('totalPageFile', ctypes.c_ulonglong), ('availablePageFile', ctypes.c_ulonglong),
               ('totalVirtual', ctypes.c_ulonglong), ('availableVirtual', ctypes.c_ulonglong),
               ('availableExtendedVirtual', ctypes.c_ulonglong)]


def memory_status():
    import psutil
    process = psutil.Process()
    info = process.memory_info()
    result = {'workingSet': info.rss, 'peakWorkingSet': getattr(info, 'peak_wset', info.rss),
              'availablePhysical': psutil.virtual_memory().available}
    if os.name == 'nt':
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError('memory_status_failed')
        result['availableCommit'] = status.availablePageFile
    return result


def verify_candidate():
    manifest = read_json(CANDIDATE / 'manifest.json')
    if (manifest['modelId'], manifest['modelRevision'], manifest['modelHash']) != (MODEL_ID, MODEL_REVISION, MODEL_SHA):
        raise ValueError('candidate_identity_mismatch')
    if manifest['referenceRequired'] or manifest['contextUsed'] or manifest['spanSupport']:
        raise ValueError('unsupported_candidate_contract')
    expected_checkpoint = CANDIDATE / 'model/checkpoints/model.ckpt'
    if safe_path(manifest['checkpointPath']) != expected_checkpoint.resolve() or safe_path(manifest['encoderPath']) != (CANDIDATE / 'encoder').resolve():
        raise ValueError('candidate_paths_mismatch')
    checkpoint_records = [record for record in manifest['modelFiles'] if safe_path(record['path']) == expected_checkpoint.resolve()]
    if len(checkpoint_records) != 1 or checkpoint_records[0]['sha256'] != MODEL_SHA:
        raise ValueError('candidate_checkpoint_hash_mismatch')
    for record in manifest['modelFiles'] + manifest['encoderFiles'] + manifest['environmentFiles']:
        verify_record(record)
    lock = (QE_ROOT / 'requirements.lock.txt').read_text(encoding='utf-8')
    for line in lock.splitlines():
        if '==' not in line:
            raise ValueError('unlocked_package')
        name, version = line.split('==', 1)
        if importlib.metadata.version(name).lower() != version.lower():
            raise ValueError('package_version_mismatch:' + name)
    if importlib.metadata.version('setuptools') != '81.0.0':
        raise ValueError('package_version_mismatch:setuptools')
    if sys.version_info[:2] != (3, 11):
        raise ValueError('unsupported_python_version')
    return manifest


class Engine:
    def __init__(self, *, emergency_directory=None, threads=4, timeout_seconds=3600, max_working_set_gib=8):
        offline_environment()
        self.model = None
        self.stop_event = threading.Event()
        self.lock_path = QE_ROOT / 'runtime.lock'
        self.owns_lock = False
        self.started = time.monotonic()
        self.max_working_set = max_working_set_gib * 1024 ** 3
        self.min_available = None
        self.peak_working_set = 0
        self.emergency_directory = emergency_directory
        import psutil
        if any((p.info['name'] or '').lower().startswith('llama-server') for p in psutil.process_iter(['name'])):
            raise RuntimeError('translation_model_still_running')
        initial = memory_status()
        if min(initial['availablePhysical'], initial.get('availableCommit', initial['availablePhysical'])) < 8 * 1024 ** 3:
            raise RuntimeError('insufficient_memory_before_qe_load')
        from runtime_lock import acquire
        self.lock_owner = acquire(self.lock_path)
        self.owns_lock = True
        self.watchdog = threading.Thread(target=self._monitor, args=(timeout_seconds,), daemon=True)
        self.watchdog.start()
        try:
            self.manifest = verify_candidate()
            self.identity = {'modelId': MODEL_ID, 'modelRevision': MODEL_REVISION, 'modelHash': MODEL_SHA,
                             'candidateManifest': file_record(CANDIDATE / 'manifest.json'),
                             'runtimeFiles': [file_record(Path(__file__)), file_record(ROOT / 'scripts/local-qe/common.py'),
                                              file_record(ROOT / 'scripts/local-qe/runtime_lock.py')],
                             'pythonVersion': sys.version.split()[0], 'device': 'cpu', 'dtype': 'float32',
                             'threads': threads, 'batchSize': 1, 'contextUsed': False, 'truncationAllowed': False,
                             'checkpointCompatibility': 'verified-derived-position-ids-buffer-v1',
                             'learnedParametersStrict': True}
            self.model_identity = sha_text(canonical(self.identity))
            # The checkpoint is an official, exact-SHA verified local artifact. No hub loader,
            # remote custom code, reference translation, generation model or GPU is used.
            with redirect_stdout(sys.stderr):
                import torch
                from comet.models.regression.referenceless import ReferencelessRegression
                class CompatibleReferencelessRegression(ReferencelessRegression):
                    def load_state_dict(self, state_dict, strict=True, assign=False):
                        if strict is not True:
                            raise RuntimeError('model_strict_load_required')
                        normalized = checked_legacy_state(state_dict, self.encoder.model.embeddings.position_ids)
                        return super().load_state_dict(normalized, strict=True, assign=assign)
                torch.set_num_threads(threads)
                torch.set_num_interop_threads(1)
                torch.manual_seed(20260911)
                self.model = CompatibleReferencelessRegression.load_from_checkpoint(
                    str(safe_path(self.manifest['checkpointPath'])),
                    pretrained_model=str(safe_path(self.manifest['encoderPath'])),
                    load_pretrained_weights=False, local_files_only=True,
                    map_location='cpu', strict=True)
                if self.model.requires_references():
                    raise RuntimeError('model_requires_reference')
                self.model.eval()
                self.model.freeze()
            self.load_seconds = time.monotonic() - self.started
        except BaseException as exc:
            if self.emergency_directory:
                write_json(self.emergency_directory / 'load_failure.json', {
                    'status': 'failed', 'phase': 'model_load_before_any_input',
                    'errorType': type(exc).__name__, 'error': str(exc),
                    'memory': memory_status(), 'inputSent': False})
            self.close()
            raise

    def _monitor(self, timeout_seconds):
        low_since = None
        while not self.stop_event.wait(0.5):
            try:
                memory = memory_status()
                self.min_available = memory['availablePhysical'] if self.min_available is None else min(self.min_available, memory['availablePhysical'])
                self.peak_working_set = max(self.peak_working_set, memory['peakWorkingSet'])
                available = min(memory['availablePhysical'], memory.get('availableCommit', memory['availablePhysical']))
                low_since = (low_since or time.monotonic()) if available < 512 * 1024 ** 2 else None
                reason = None
                if memory['workingSet'] > self.max_working_set:
                    reason = 'qe_working_set_budget_exceeded'
                elif low_since and time.monotonic() - low_since >= 3:
                    reason = 'qe_system_memory_guard'
                elif time.monotonic() - self.started >= timeout_seconds:
                    reason = 'qe_process_timeout'
                if reason:
                    if self.emergency_directory:
                        write_json(self.emergency_directory / 'emergency.json', {'status': 'failed', 'errorCode': reason, 'memory': memory})
                    self._release_lock()
                    os._exit(70)
            except BaseException:
                self._release_lock()
                os._exit(71)

    def _release_lock(self):
        if self.owns_lock:
            self.owns_lock = False
            from runtime_lock import release
            release(self.lock_path, self.lock_owner)

    def score(self, request):
        validate_request(request)
        started = time.monotonic()
        limit = self.model.encoder.max_positions
        token_counts = {field: len(self.model.encoder.tokenizer(request[field], truncation=False)['input_ids'])
                        for field in ('source', 'translation')}
        if any(count > limit for count in token_counts.values()):
            raise ValueError('qe_input_exceeds_model_context')
        with redirect_stdout(sys.stderr):
            result = self.model.predict([{'src': request['source'], 'mt': request['translation']}],
                batch_size=1, gpus=0, accelerator='cpu', num_workers=0, progress_bar=False, length_batching=False)
        if len(result.scores) != 1:
            raise RuntimeError('qe_result_count_mismatch')
        return {'id': request['id'], 'status': 'completed', 'score': validate_score(result.scores[0]),
                'scoreDirection': 'higher-is-better', 'scoreMeaning': 'raw ranking score; not error probability',
                'modelId': MODEL_ID, 'modelRevision': MODEL_REVISION, 'modelHash': MODEL_SHA,
                'modelIdentity': self.model_identity, 'contextUsed': False, 'spans': [],
                'sourceSha256': sha_text(request['source']), 'translationSha256': sha_text(request['translation']),
                'contextSha256': sha_text(request.get('context', '')), 'inputTokenCounts': token_counts,
                'inputTokenLimit': limit, 'truncated': False, 'seconds': round(time.monotonic() - started, 6)}

    def close(self):
        self.stop_event.set()
        if hasattr(self, 'watchdog') and self.watchdog is not threading.current_thread():
            self.watchdog.join(timeout=2)
        self.model = None
        self._release_lock()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
