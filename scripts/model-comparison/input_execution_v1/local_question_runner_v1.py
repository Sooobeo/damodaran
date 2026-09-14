"""Question-only coordinator: one new owned native process and one call per packet.

No source/formal/annotation/evaluation module is imported. No inference on import.
The coordinator reads the exported 64 packets; each native receives only its own.
"""
from __future__ import annotations
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/model-comparison'))
from input_execution_v1 import local_question_contract_v1 as contract
from input_execution_v1 import resource_guard as resources
from input_execution_v1 import run_io

VERSION = 'input-execution-v1-local-question-runner-v1'
BASE = '.training/quality-evaluation/input-preparation-v1'
PROTOCOL = 'content/model-comparison/input-execution-v1/LOCAL_QUESTION_REVIEW_PROTOCOL_V1.md'
FREEZE = 'content/model-comparison/input-execution-v1/local-question-execution-freeze-v1.json'
INSTALL = '.translation/qe/llm-candidates/qwen35-9b/install-manifest.json'
INSTALL_SHA = 'af531ddc76544daf8c68fbc19ff5181bb2ca1e084a526ada9e497768130fc595'
MODEL = '.translation/qe/llm-candidates/qwen35-9b/' + contract.MODEL_NAME
RUNTIME = '.training/comparisons/hy-mt2-30b-a3b-q4/runtime-cpu'
GIB = 1024 ** 3
PROFILE = {'threads': 4, 'contextTokens': 4096, 'outputTokens': 2048,
           'maximumWorkingSetBytes': 6 * GIB, 'maximumPrivateBytes': 6 * GIB,
           'workingSetApiMaximumBytes': 6 * GIB - 64 * 1024 ** 2,
           'minimumStartPhysicalBytes': 9 * GIB, 'minimumStartCommitBytes': 9 * GIB,
           'minimumRunningHeadroomBytes': GIB, 'startupSeconds': 600,
           'requestSeconds': 600, 'totalSeconds': 12 * 3600, 'preflightGapSeconds': 3,
           'monitorSeconds': 0.5, 'processScanSeconds': 5,
           'priorityClass': 'BelowNormal', 'requiresACLineStatus': 1,
           'aggregateRamCap': False, 'privateBytesHardCap': False,
           'oneCompletionPerFreshProcess': True, 'automaticRetry': False}
require = contract.require
packed = contract.packed
sha = contract.sha
read = contract.read


def utc():
    return datetime.now(timezone.utc).isoformat()


def rooted(root, path):
    root = Path(root).resolve()
    path = Path(path)
    path = path if path.is_absolute() else root / path
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'redirected_path')
    path = path.resolve()
    require(path.is_relative_to(root), 'outside_root')
    return path


def development(root, path):
    path = rooted(root, path)
    base = rooted(root, BASE)
    require(path != base and path.is_relative_to(base) and 'holdout' not in path.relative_to(base).as_posix().lower(), 'development_only_path')
    return path


def stat_id(path):
    value = Path(path).stat()
    return [value.st_size, value.st_mtime_ns, value.st_ino]


def file_sha(path):
    before = stat_id(path)
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    require(stat_id(path) == before, 'file_changed_during_hash')
    return digest.hexdigest()


def python_identity():
    import jinja2
    import markupsafe
    require(sys.version_info[:2] == (3, 11) and jinja2.__version__ == '3.1.6', 'python_or_jinja_runtime_contract')
    executable = Path(sys.executable).resolve()
    loaded, runtime_library = executable, None
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
        kernel.GetModuleFileNameW.restype = ctypes.c_ulong
        def module_path(handle):
            buffer = ctypes.create_unicode_buffer(32768)
            count = kernel.GetModuleFileNameW(handle, buffer, len(buffer))
            require(0 < count < len(buffer), 'loaded_python_identity_unavailable')
            return Path(buffer.value).resolve()
        loaded = module_path(None)
        runtime_library = module_path(ctypes.pythonapi._handle)
        require(runtime_library.suffix.lower() == '.dll', 'loaded_python_runtime_library_required')
    paths = {executable, loaded}
    if runtime_library is not None:
        paths.add(runtime_library)
    for module in (jinja2, markupsafe):
        directory = Path(module.__file__).resolve().parent
        paths.update(p for p in directory.rglob('*') if p.is_file() and p.suffix in ('.py', '.pyd'))
    return {'pythonVersion': sys.version, 'pythonExecutable': str(executable), 'loadedPythonExecutable': str(loaded),
            'loadedPythonRuntimeLibrary': None if runtime_library is None else str(runtime_library),
            'jinjaVersion': jinja2.__version__, 'files': {str(p): file_sha(p) for p in sorted(paths)}}


def reference(root, path):
    path = rooted(root, path)
    return {'path': path.relative_to(root).as_posix(), 'sha256': file_sha(path)}


def verify_refs(root, records):
    for record in records:
        require(file_sha(rooted(root, record['path'])) == record['sha256'], 'evidence_hash_changed')


def write_new(path, raw):
    path = Path(path)
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'artifact_redirected')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def write_json(path, value):
    write_new(path, packed(value))


def exact_files(directory, names):
    paths = [p for p in Path(directory).rglob('*') if p.is_file()]
    require(all(not any(x.is_symlink() for x in (p, *p.parents)) for p in paths), 'artifact_redirected')
    require({p.relative_to(directory).as_posix() for p in paths} == set(names), 'file_inventory_changed')


def load_export(export_dir, root=ROOT):
    root = Path(root).resolve()
    folder = development(root, export_dir)
    exact_files(folder, ['manifest.json', *[rid + '.json' for rid in contract.IDS]])
    manifest, raw = read(folder / 'manifest.json')
    require(set(manifest) == {'version', 'questionPackets', 'packetFiles', 'protocol', 'executionFreeze', 'formalManifestSha256', 'bundleSha256'}
            and manifest['version'] == 'input-execution-v1-local-question-export-v1'
            and manifest['questionPackets'] == 64 and raw == packed(manifest), 'question_export_contract')
    require(all(isinstance(manifest[k], str) and re.fullmatch('[0-9a-f]{64}', manifest[k]) for k in ('formalManifestSha256', 'bundleSha256')), 'formal_binding_digest_required')
    require(manifest['protocol']['path'] == PROTOCOL and manifest['executionFreeze']['path'] == FREEZE, 'question_protocol_paths')
    refs = [reference(root, folder / 'manifest.json'), manifest['protocol'], manifest['executionFreeze']]
    verify_refs(root, refs)
    freeze, _ = read(rooted(root, FREEZE))
    frozen = contract.validate_freeze(root, freeze)
    verify_refs(root, frozen)
    refs.extend({'path': r['path'], 'sha256': r['sha256']} for r in frozen)
    files = manifest['packetFiles']
    require(isinstance(files, list) and [r.get('reviewId') for r in files] == list(contract.IDS), 'question_packet_inventory')
    packets = []
    for rid, record in zip(contract.IDS, files):
        require(set(record) == {'reviewId', 'path', 'sha256'} and record['path'] == rid + '.json', 'question_packet_path')
        packet, packet_raw = read(folder / record['path'])
        contract.validate_packet(packet)
        require(packet['reviewId'] == rid and sha(packet_raw) == record['sha256'] and packet_raw == packed(packet), 'question_packet_hash_or_id')
        packets.append(packet)
        refs.append(reference(root, folder / record['path']))
    verify_refs(root, refs)
    return {'manifest': manifest, 'packets': packets, 'evidence': refs, 'folder': folder}


def installation(root=ROOT):
    root = Path(root).resolve()
    manifest_path = rooted(root, INSTALL)
    require(file_sha(manifest_path) == INSTALL_SHA, 'installed_manifest_changed')
    value, _ = read(manifest_path)
    require(value['model']['sha256'] == contract.MODEL_SHA and value['model']['file'] == contract.MODEL_NAME
            and value['model']['revision'] == '3885219b6810b007914f3a7950a8d1b469d598a5'
            and value['model']['sizeBytes'] == 5680522464, 'installed_model_contract')
    require(value['runtime']['commit'] == '72797e89198ab564fd0e6baa54ab196e8dd1d884'
            and value['runtime']['archiveSha256'] == '68d0ea47c71a55f6a19219727d39075f08f7da2c9d9073c7b47b3d64a7027284', 'installed_runtime_contract')
    files = [{'path': MODEL, 'sha256': contract.MODEL_SHA}, *value['runtime']['files']]
    runtime_paths = {rooted(root, r['path']) for r in value['runtime']['files']}
    require(len(runtime_paths) == 52 and runtime_paths == {p.resolve() for p in rooted(root, RUNTIME).rglob('*') if p.is_file()}, 'runtime_52_file_inventory')
    verify_refs(root, files)
    return {'manifest': reference(root, manifest_path), 'modelSha256': contract.MODEL_SHA,
            'files': [{'path': rooted(root, r['path']).relative_to(root).as_posix(), 'sha256': r['sha256']} for r in files],
            'statIdentities': {rooted(root, r['path']).relative_to(root).as_posix(): stat_id(rooted(root, r['path'])) for r in files}}


def check_installation_stat(identity, root):
    verify_refs(root, [identity['manifest']])
    require(all(stat_id(rooted(root, p)) == wanted for p, wanted in identity['statIdentities'].items()), 'installation_changed')


def gguf_template(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(16 * 1024 ** 2)
    stream = io.BytesIO(raw)
    formats = {0: 'B', 1: 'b', 2: 'H', 3: 'h', 4: 'I', 5: 'i', 6: 'f', 7: '?', 10: 'Q', 11: 'q', 12: 'd'}
    def take(n):
        require(type(n) is int and 0 <= n <= len(raw), 'gguf_length')
        value = stream.read(n)
        require(len(value) == n, 'gguf_header_limit')
        return value
    def number(fmt):
        return struct.unpack('<' + fmt, take(struct.calcsize('<' + fmt)))[0]
    def string():
        return take(number('Q')).decode('utf-8')
    def value(kind):
        if kind == 8:
            return string()
        if kind == 9:
            inner, count = number('I'), number('Q')
            require(inner != 9 and count <= 1000000, 'gguf_array')
            return [value(inner) for _ in range(count)]
        require(kind in formats, 'gguf_type')
        return number(formats[kind])
    require(take(4) == b'GGUF' and number('I') == 3, 'gguf_version')
    tensor_count, count = number('Q'), number('Q')
    require(count < 10000, 'gguf_metadata_count')
    metadata = {}
    for _ in range(count):
        key, kind = string(), number('I')
        require(key not in metadata, 'gguf_duplicate_key')
        metadata[key] = value(kind)
    template = metadata.get('tokenizer.chat_template')
    require(metadata.get('general.architecture') == 'qwen35' and isinstance(template, str) and 'enable_thinking' in template, 'qwen_template_contract')
    boundary = {token: metadata['tokenizer.ggml.tokens'].index(token) for token in ('<|im_start|>', '<|im_end|>', '<|endoftext|>', '<think>', '</think>')}
    return template, {'templateSha256': sha(template), 'metadataSha256': sha(raw[:stream.tell()]),
                      'tensorCount': tensor_count, 'weightTensorsLoaded': False, 'boundaryTokenIds': boundary,
                      'eosTokenId': metadata['tokenizer.ggml.eos_token_id']}


def render_prompt(packet, template):
    import jinja2
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    require(jinja2.__version__ == '3.1.6', 'jinja_version')
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True, undefined=jinja2.StrictUndefined)
    def reject(_):
        raise ValueError('template_rejected_input')
    env.globals['raise_exception'] = reject
    prompt = env.from_string(template).render(messages=contract.messages(packet), tools=[], add_generation_prompt=True, enable_thinking=False, add_vision_id=False)
    require(prompt.endswith('<think>\n\n</think>\n\n'), 'nonthinking_template_suffix')
    return prompt


def server_command(root, port, key):
    return [str(rooted(root, RUNTIME) / 'llama-server.exe'), '--model', str(rooted(root, MODEL)),
            '--alias', 'question-only-qwen35-9b-v1', '--host', '127.0.0.1', '--port', str(port), '--api-key', key,
            '--ctx-size', '4096', '--parallel', '1', '--threads', '4', '--threads-batch', '4', '--prio', '-1', '--poll', '0', '--poll-batch', '0',
            '--offline', '--no-agent', '--no-warmup', '--no-repack', '--load-mode', 'mmap', '--cache-ram', '0', '--no-op-offload', '--log-verbosity', '4',
            '--cors-origins', f'http://127.0.0.1:{port}', '--batch-size', '128', '--ubatch-size', '128', '--gpu-layers', '0', '--device', 'none',
            '--fit', 'off', '--cache-type-k', 'f16', '--cache-type-v', 'f16', '--ctx-checkpoints', '3', '--no-context-shift', '--no-webui',
            '--jinja', '--reasoning-format', 'none', '--chat-template-kwargs', '{"enable_thinking":false}']


def resource_reason(state, child=None):
    if state.get('ACLineStatus') != 1:
        return 'ac_power_lost_or_unknown'
    for key in ('availablePhysicalBytes', 'availableCommitBytes'):
        if type(state.get(key)) is not int or state[key] < PROFILE['minimumRunningHeadroomBytes']:
            return 'insufficient_running_' + key
    if child is not None:
        for field in ('workingSetBytes', 'peakWorkingSetBytes', 'privateBytes'):
            if type(child.get(field)) is not int or child[field] < 0:
                return 'invalid_child_memory'
        if max(child['workingSetBytes'], child['peakWorkingSetBytes']) > PROFILE['maximumWorkingSetBytes']:
            return 'working_set_limit_exceeded'
        if child['privateBytes'] > PROFILE['maximumPrivateBytes']:
            return 'private_bytes_observation_exceeded'
    return None


def preflight_pair():
    observations = []
    for index in range(2):
        if index:
            time.sleep(PROFILE['preflightGapSeconds'])
        state, processes = resources.system_state(), resources.process_state()
        observations.append({'at': utc(), 'monotonicSeconds': time.monotonic(), 'system': state, 'processes': processes})
    for item in observations:
        state = item['system']
        require(resource_reason(state) is None and state['availablePhysicalBytes'] >= PROFILE['minimumStartPhysicalBytes']
                and state['availableCommitBytes'] >= PROFILE['minimumStartCommitBytes'], 'question_preflight_resource')
        require(not item['processes']['nativeConflicts'] and not item['processes']['classifiedConflicts'], 'question_preflight_model_or_worker')
    return observations


class Client(run_io.LocalClient):
    def __init__(self, base, key, output, root=ROOT):
        super().__init__(base, key, output)
        self.root = Path(root).resolve()

    def request(self, endpoint, payload=None, timeout=15):
        require(endpoint in ('/health', '/props', '/apply-template', '/tokenize', '/detokenize', '/completion') and 0 < timeout <= 600, 'question_http_contract')
        require(endpoint != '/completion' or self.completions == 0, 'second_completion_prohibited')
        self.sequence += 1
        prefix = self.output / 'http' / f'{self.sequence:05d}-{endpoint[1:]}'
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        metadata = {'sequence': self.sequence, 'endpoint': endpoint, 'method': 'GET' if body is None else 'POST', 'startedAt': utc(),
                    'timeoutSeconds': timeout, 'requestSha256': sha(body or b''), 'authorizationRecorded': False}
        # Both exact body and intent reach stable storage before any send.
        write_new(prefix.with_suffix('.request.bin'), body or b'')
        write_json(prefix.with_suffix('.request.json'), {'metadata': metadata, 'payload': payload})
        state = {'bodyStarted': False, 'chunks': [], 'receivedBytes': 0, 'expectedBytes': None, 'eof': False, 'bodyComplete': False}
        status = error_name = None
        started = time.monotonic()
        if endpoint == '/completion':
            self.completions += 1
        try:
            request = urllib.request.Request(self.base + endpoint, data=body, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key, 'Origin': self.base})
            try:
                response = self.opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.getcode()
                raw = self._read_body(response, prefix.with_suffix('.response.bin'), state)
            require(len(raw) <= run_io.MAX_RESPONSE_BYTES, 'response_byte_limit')
            value = contract.parse(raw.decode('utf-8'))
            require(isinstance(value, dict), 'response_object_required')
            if endpoint == '/health' and status == 503:
                raise urllib.error.URLError('health_not_ready')
            require(status == 200 and 'error' not in value, 'native_http_error')
            return value
        except BaseException as error:
            error_name = type(error).__name__
            raise
        finally:
            raw = b''.join(state['chunks']) if state['bodyStarted'] else None
            self.last = {**metadata, 'completedAt': utc(), 'elapsedSeconds': time.monotonic() - started, 'httpStatus': status,
                         'responseBytes': None if raw is None else len(raw), 'rawResponsePath': None if raw is None else prefix.with_suffix('.response.bin').relative_to(self.root).as_posix(),
                         'rawResponseSha256': None if raw is None else sha(raw), 'responseCompleteWithinLimit': state['bodyComplete'] and raw is not None and len(raw) <= run_io.MAX_RESPONSE_BYTES,
                         'responseEofObserved': state['eof'], 'expectedResponseBytes': state['expectedBytes'], 'transportOrProtocolErrorType': error_name, 'automaticRetry': False}
            write_json(prefix.with_suffix('.receipt.json'), self.last)


class Monitor:
    def __init__(self, process, limiter, folder, run_started):
        self.process, self.limiter, self.folder = process, limiter, folder
        self.run_started, self.deadline = run_started, time.monotonic() + PROFILE['startupSeconds']
        self.error = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.stream = (folder / 'resource-samples.jsonl').open('xb')
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.scan_thread = threading.Thread(target=self._scan, daemon=True)

    def abort(self, reason):
        self.error = self.error or reason
        if self.process.poll() is None:
            self.limiter.owner.assert_owned()
            self.limiter.owner._api.assert_child(self.limiter.owner._handle, int(self.process._handle))
            self.process.kill()

    def sample(self):
        state, child, now = resources.system_state(), self.limiter.sample_child(self.process), time.monotonic()
        with self.lock:
            self.stream.write(packed({'at': utc(), 'system': state, 'child': child}))
            self.stream.flush()
        reason = resource_reason(state, child)
        reason = reason or ('total_time_limit' if now - self.run_started >= PROFILE['totalSeconds'] else None)
        reason = reason or ('phase_deadline' if self.deadline is not None and now >= self.deadline else None)
        if reason:
            self.abort(reason)
        self.check()

    def _run(self):
        while not self.stop_event.wait(PROFILE['monitorSeconds']):
            if self.process.poll() is not None:
                return
            try:
                self.sample()
            except BaseException:
                self.abort(self.error or 'resource_monitor_failed')
                return

    def _scan(self):
        while not self.stop_event.wait(PROFILE['processScanSeconds']):
            if self.process.poll() is not None:
                return
            try:
                state = resources.process_state((self.process.pid,))
                if state['nativeConflicts'] or state['classifiedConflicts']:
                    self.abort('other_model_or_worker_started')
                    return
            except BaseException:
                self.abort('process_scan_failed')
                return

    def start(self):
        self.sample()
        self.thread.start()
        self.scan_thread.start()

    def check(self):
        require(self.error is None, self.error or 'resource_monitor_failed')
        require(self.process.poll() is None, 'native_exited')

    def close(self):
        self.stop_event.set()
        for thread in (self.thread, self.scan_thread):
            if thread.ident is not None:
                thread.join(timeout=12)
            require(not thread.is_alive(), 'monitor_shutdown_failed')
        if not self.stream.closed:
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.stream.close()


def spawn(owner, command, log, folder):
    require(resources.ExperimentLock._active, 'model_mutex_required')
    limiter = resources._PriorityCheckedLimiter(owner, maximum_bytes=PROFILE['workingSetApiMaximumBytes'])
    suspended = resources.SuspendedProcessOwner(owner, limiter)
    system_root = os.environ.get('SystemRoot', 'C:/Windows')
    env = {'SystemRoot': system_root, 'WINDIR': system_root, 'PATH': str(Path(command[0]).parent) + os.pathsep + str(Path(system_root) / 'System32'), 'TEMP': str(folder), 'TMP': str(folder)}
    process = suspended.spawn(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                              cwd=folder, env=env, creationflags=resources.BELOW_NORMAL | resources.CREATE_NO_WINDOW)
    try:
        receipt = dict(suspended.creation_receipt)
        receipt['identity'] = limiter.sample_child(process)
        require(receipt.get('atomicJobAssignment') is True and receipt.get('createSuspended') is True
                and receipt.get('limitAppliedBeforeResume') is True and receipt.get('resumePreviousCount') == 1, 'native_ownership_receipt')
        return process, limiter, receipt
    except BaseException:
        try:
            resources.stop_owned(process, owner)
        except BaseException as error:
            failure = ValueError('native_cleanup_unconfirmed')
            failure.code = 'native_cleanup_unconfirmed'
            raise failure from error
        raise


def parity(client, packet, template, metadata, root):
    props = client.request('/props')
    require(Path(props.get('model_path', '')).resolve() == rooted(root, MODEL)
            and props.get('chat_template') == template and props.get('total_slots') == 1
            and props.get('default_generation_settings', {}).get('n_ctx') == contract.CONTEXT, 'loaded_model_or_template_mismatch')
    for text, token_id in metadata['boundaryTokenIds'].items():
        require(client.request('/tokenize', {'content': text, 'add_special': False, 'parse_special': True}).get('tokens') == [token_id], 'native_boundary_token_mismatch')
    request = {'messages': contract.messages(packet), 'add_generation_prompt': True, 'chat_template_kwargs': {'enable_thinking': False}}
    prompt = render_prompt(packet, template)
    applied = client.request('/apply-template', request)
    require(applied.get('prompt') == prompt, 'native_apply_template_parity')
    tokens = client.request('/tokenize', {'content': prompt, 'add_special': False, 'parse_special': True}).get('tokens')
    contract.completion_payload(packet, tokens)
    require(client.request('/detokenize', {'tokens': tokens, 'special': True}).get('content') == prompt, 'native_detokenize_parity')
    return {'prompt': prompt, 'promptSha256': sha(prompt), 'tokenIds': tokens,
            'tokenIdsSha256': sha(packed(tokens)), 'inputTokens': len(tokens), 'contextTokens': contract.CONTEXT,
            'outputReservedTokens': contract.OUTPUT_TOKENS, 'modelTemplateSha256': metadata['templateSha256'],
            'allChecksPassedBeforeCompletion': True, 'completionRequestsSent': client.completions}


def snapshot(folder, root):
    files = [p for p in folder.rglob('*') if p.is_file() and p != folder / 'summary.json']
    return [reference(root, p) for p in sorted(files)]


def execute_context(packet, folder, install, template, metadata, export, root, run_started):
    folder.mkdir(parents=True, exist_ok=False)
    context_id = str(uuid.uuid4())
    plan = {'version': VERSION, 'reviewId': packet['reviewId'], 'contextId': context_id,
            'packetSha256': sha(packed(packet)), 'modelSha256': contract.MODEL_SHA,
            'previousModelRequests': 0, 'sourceExposure': False, 'allowedFields': ['reviewId', 'translation', 'questions'],
            'profile': PROFILE, 'sampling': contract.NATIVE_SAMPLING, 'exportManifest': export['evidence'][0]}
    write_new(folder / 'input-packet.json', packed(packet))
    write_json(folder / 'plan.json', plan)
    process = limiter = monitor = client = None
    shutdown = {'stopped': True, 'processCreated': False}
    failure = None
    try:
        check_installation_stat(install, root)
        verify_refs(root, export['evidence'])
        observations = preflight_pair()
        write_json(folder / 'preflight.json', observations)
        owner = resources.process_owner.claim_process_owner()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        key = secrets.token_hex(32)
        command = server_command(root, port, key)
        redacted = command.copy()
        redacted[redacted.index('--api-key') + 1] = '[EPHEMERAL_REDACTED]'
        write_json(folder / 'runtime-command.json', {'argv': redacted, 'shell': False})
        with (folder / 'runtime.log').open('xb') as log:
            try:
                process, limiter, creation = spawn(owner, command, log, folder)
                shutdown = {'stopped': False, 'processCreated': True, 'pid': process.pid, 'cleanupUnconfirmed': True}
                write_json(folder / 'process.json', creation)
                monitor = Monitor(process, limiter, folder, run_started)
                monitor.start()
                client = Client(f'http://127.0.0.1:{port}', key, folder, root)
                while True:
                    monitor.check()
                    try:
                        if client.request('/health', timeout=2).get('status') == 'ok':
                            break
                    except (urllib.error.URLError, TimeoutError, ConnectionError):
                        pass
                    time.sleep(0.25)
                # Startup deadline also covers all parity requests.
                checked = parity(client, packet, template, metadata, root)
                require(checked['completionRequestsSent'] == 0, 'prior_context_request')
                write_json(folder / 'prompt-parity.json', checked)
                verify_refs(root, export['evidence'])
                check_installation_stat(install, root)
                monitor.sample()
                monitor.deadline = time.monotonic() + PROFILE['requestSeconds']
                write_json(folder / 'completion-intent.json', {**plan, 'at': utc(), 'sequence': 1, 'automaticRetry': False})
                response = client.request('/completion', contract.completion_payload(packet, checked['tokenIds']), timeout=PROFILE['requestSeconds'])
                # Raw body/receipt already persisted before any semantic/schema check.
                monitor.sample()
                draft = contract.validate_native(response, checked['prompt'], checked['tokenIds'], packet)
                write_json(folder / 'draft.json', draft)
                monitor.deadline = None
                verify_refs(root, export['evidence'])
                check_installation_stat(install, root)
            finally:
                try:
                    if monitor is not None:
                        monitor.close()
                finally:
                    if process is not None:
                        stopped = resources.stop_owned(process, owner)
                        require(isinstance(stopped, dict) and stopped.get('stopped') is True, 'native_cleanup_unconfirmed')
                        shutdown = stopped
            log.flush()
            os.fsync(log.fileno())
    except BaseException as error:
        failure = {'type': type(error).__name__, 'code': str(error) if isinstance(error, ValueError) else type(error).__name__}
        if getattr(error, 'code', '') in ('owned_child_creation_cleanup_failed', 'unowned_child_cleanup_failed', 'owned_child_did_not_stop', 'native_cleanup_unconfirmed'):
            shutdown = {'stopped': False, 'cleanupUnconfirmed': True}
    # Joining the monitor is a final synchronization boundary. A guard may
    # latch after the last sample/response, so read the latch only after join
    # and owned stop; no next packet may follow a late guard failure.
    if failure is None and monitor is not None and monitor.error is not None:
        failure = {'type': 'ResourceMonitorError', 'code': monitor.error}
    summary = {**plan, 'status': 'completed' if failure is None else 'failed', 'failure': failure,
               'completionRequestsSent': 0 if client is None else client.completions,
               'nativeStopped': shutdown.get('stopped') is True, 'shutdown': shutdown,
               'validatedDraft': 'draft.json' if (folder / 'draft.json').is_file() else None,
               'monitorError': None if monitor is None else monitor.error, 'finishedAt': utc(),
               'artifactFiles': snapshot(folder, root)}
    write_json(folder / 'summary.json', summary)
    require(failure is None and summary['completionRequestsSent'] == 1 and summary['nativeStopped'] and summary['monitorError'] is None, 'question_context_failed')
    return summary


def run(export_dir, destination, root=ROOT):
    root = Path(root).resolve()
    destination = development(root, destination)
    require(not destination.exists(), 'question_run_must_be_new')
    export = load_export(export_dir, root)
    interpreter = python_identity()
    install = installation(root)
    template, metadata = gguf_template(rooted(root, MODEL))
    # Render every exported packet before native creation. Native token counts
    # are checked inside each fresh process before its sole completion.
    for packet in export['packets']:
        render_prompt(packet, template)
    destination.mkdir(parents=True, exist_ok=False)
    plan = {'version': VERSION, 'expectedContexts': 64, 'exportManifest': export['evidence'][0],
            'installation': install, 'pythonIdentity': interpreter, 'gguf': metadata, 'profile': PROFILE,
            'reviewIds': list(contract.IDS), 'modelSha256': contract.MODEL_SHA,
            'sourceExposure': False, 'previousModelRequestsPerContext': 0, 'actualCollaborationSpawns': 0,
            'codeAndExportEvidence': export['evidence'], 'startedAt': utc()}
    write_json(destination / 'plan.json', plan)
    write_new(destination / 'model-chat-template.jinja', template.encode('utf-8'))
    completed, failure, power = [], None, None
    started = time.monotonic()
    try:
        with resources.ExperimentLock():
            # A permanent claim prevents a new destination from silently retrying
            # any unknown/completed question call. A recovery requires a new contract.
            claim = rooted(root, BASE) / '.local-question-native-v1-generation.claim.json'
            require(not claim.exists(), 'question_generation_already_claimed')
            write_json(claim, {'version': VERSION, 'runPath': destination.relative_to(root).as_posix(), 'exportManifest': export['evidence'][0], 'at': utc()})
            with resources.PowerRequest() as power:
                for packet in export['packets']:
                    completed.append(execute_context(packet, destination / packet['reviewId'], install, template, metadata, export, root, started))
                    print(json.dumps({'reviewId': packet['reviewId'], 'completed': len(completed), 'total': 64}), flush=True)
        verify_refs(root, export['evidence'])
        require(installation(root) == install, 'final_installation_changed')
        require(python_identity() == interpreter, 'final_python_identity_changed')
    except BaseException as error:
        failure = {'type': type(error).__name__, 'code': str(error) if isinstance(error, ValueError) else type(error).__name__}
    contexts = [read(p)[0] for p in sorted(destination.glob('R[0-9][0-9][0-9]/summary.json'))]
    summary = {'version': VERSION, 'status': 'completed' if failure is None else 'failed', 'failure': failure,
               'expectedContexts': 64, 'completedContexts': len(completed), 'freshNativeProcesses': sum((p.parent / 'process.json').exists() for p in destination.glob('R[0-9][0-9][0-9]/plan.json')),
               'completionRequestsSent': sum(r['completionRequestsSent'] for r in contexts), 'nativeStopped': all(r['nativeStopped'] for r in contexts),
               'modelSha256': contract.MODEL_SHA, 'sourceExposure': False, 'actualCollaborationSpawns': 0,
               'powerRequest': None if power is None else power.receipt, 'finishedAt': utc(), 'artifactFiles': snapshot(destination, root)}
    write_json(destination / 'summary.json', summary)
    require(failure is None, 'question_run_failed')
    return validate_run(destination, export_dir, root)


class ReplayClient:
    """Replay exact saved wire bytes through the same parity routine; no HTTP."""
    def __init__(self, folder, root):
        self.folder, self.root = folder, root
        self.items = sorted((folder / 'http').glob('*.request.json'))
        self.index, self.completions = 0, 0
        require([int(p.name.split('-')[0]) for p in self.items] == list(range(1, len(self.items) + 1)), 'request_sequence_gap')

    def request(self, endpoint, payload=None, timeout=None):
        require(self.index < len(self.items), 'missing_http_request')
        path = self.items[self.index]
        self.index += 1
        request, _ = read(path)
        metadata = request['metadata']
        prefix = path.with_name(path.name.removesuffix('.request.json'))
        raw_body = prefix.with_suffix('.request.bin').read_bytes()
        expected_body = b'' if payload is None else json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        require(metadata['endpoint'] == endpoint and request['payload'] == payload and raw_body == expected_body
                and metadata['requestSha256'] == sha(raw_body) and metadata['authorizationRecorded'] is False
                and metadata['method'] == ('GET' if payload is None else 'POST'), 'raw_request_binding')
        require(0 < metadata['timeoutSeconds'] <= 600 and (endpoint != '/completion' or metadata['timeoutSeconds'] == PROFILE['requestSeconds']), 'http_timeout_changed')
        receipt, _ = read(prefix.with_suffix('.receipt.json'))
        require(all(receipt.get(k) == value for k, value in metadata.items()) and receipt['automaticRetry'] is False, 'http_receipt_binding')
        raw = prefix.with_suffix('.response.bin').read_bytes()
        require(receipt['httpStatus'] == 200 and receipt['responseCompleteWithinLimit'] is True and receipt['responseEofObserved'] is True
                and receipt['transportOrProtocolErrorType'] is None and receipt['responseBytes'] == len(raw)
                and receipt['rawResponseSha256'] == sha(raw) and receipt['expectedResponseBytes'] in (None, len(raw))
                and rooted(self.root, receipt['rawResponsePath']) == prefix.with_suffix('.response.bin'), 'raw_response_receipt_binding')
        if endpoint == '/completion':
            self.completions += 1
            require(self.completions == 1, 'second_completion_prohibited')
        value = contract.parse(raw.decode('utf-8'))
        require(isinstance(value, dict) and 'error' not in value, 'native_response_object')
        return value

    def skip_health(self):
        while self.index < len(self.items):
            request, _ = read(self.items[self.index])
            if request['metadata']['endpoint'] != '/health':
                break
            metadata = request['metadata']
            prefix = self.items[self.index].with_name(self.items[self.index].name.removesuffix('.request.json'))
            receipt, _ = read(prefix.with_suffix('.receipt.json'))
            require(request['payload'] is None and metadata['method'] == 'GET' and metadata['requestSha256'] == sha(b'')
                    and metadata['authorizationRecorded'] is False and prefix.with_suffix('.request.bin').read_bytes() == b''
                    and all(receipt.get(k) == value for k, value in metadata.items()) and receipt['automaticRetry'] is False, 'startup_health_request_binding')
            raw_path = prefix.with_suffix('.response.bin')
            if receipt['rawResponsePath'] is not None:
                require(rooted(self.root, receipt['rawResponsePath']) == raw_path and file_sha(raw_path) == receipt['rawResponseSha256']
                        and raw_path.stat().st_size == receipt['responseBytes'], 'startup_health_response_binding')
            else:
                require(not raw_path.exists() and receipt['rawResponseSha256'] is None and receipt['responseBytes'] is None, 'startup_health_missing_response')
            self.index += 1


def validated_snapshot(folder, root):
    summary, raw = read(folder / 'summary.json')
    files = snapshot(folder, root)
    require(summary['artifactFiles'] == files and raw == packed(summary), 'closed_artifact_snapshot_changed')
    return summary, files + [reference(root, folder / 'summary.json')]


def validate_run(run_dir, export_dir, root=ROOT):
    root = Path(root).resolve()
    folder = development(root, run_dir)
    export = load_export(export_dir, root)
    summary, evidence = validated_snapshot(folder, root)
    require(summary['version'] == VERSION and summary['status'] == 'completed' and summary['failure'] is None
            and all(type(summary.get(k)) is int for k in ('expectedContexts', 'completedContexts', 'freshNativeProcesses', 'completionRequestsSent'))
            and summary['expectedContexts'] == summary['completedContexts'] == summary['freshNativeProcesses'] == summary['completionRequestsSent'] == 64
            and summary['nativeStopped'] is True and summary['sourceExposure'] is False and summary['actualCollaborationSpawns'] == 0
            and summary['modelSha256'] == contract.MODEL_SHA and summary['powerRequest']['released'] is True, 'question_completed_run_required')
    plan, _ = read(folder / 'plan.json')
    require(plan.get('pythonIdentity') == python_identity(), 'python_runtime_identity_changed')
    require(plan['version'] == VERSION and plan['reviewIds'] == list(contract.IDS) and plan['profile'] == PROFILE
            and plan['codeAndExportEvidence'] == export['evidence'] and plan['exportManifest'] == export['evidence'][0]
            and plan['modelSha256'] == contract.MODEL_SHA and plan['sourceExposure'] is False
            and plan['previousModelRequestsPerContext'] == 0 and plan['actualCollaborationSpawns'] == 0, 'question_run_plan_binding')
    install = installation(root)
    require(plan['installation'] == install, 'question_run_installation_changed')
    template, metadata = gguf_template(rooted(root, MODEL))
    require((folder / 'model-chat-template.jinja').read_bytes() == template.encode('utf-8') and plan['gguf'] == metadata, 'question_run_gguf_binding')
    require({p.name for p in folder.iterdir() if p.is_dir()} == set(contract.IDS), 'question_context_folder_inventory')
    contexts, identities, context_ids = [], set(), set()
    previous_finished = None
    for packet in export['packets']:
        rid = packet['reviewId']
        context = folder / rid
        state, context_evidence = validated_snapshot(context, root)
        context_plan, _ = read(context / 'plan.json')
        require((context / 'input-packet.json').read_bytes() == packed(packet), 'context_cross_packet_input')
        cid = state['contextId']
        require(str(uuid.UUID(cid)) == cid and cid not in context_ids, 'fresh_context_id_required')
        context_ids.add(cid)
        require(all(state.get(k) == value for k, value in context_plan.items()) and context_plan['reviewId'] == rid
                and context_plan['packetSha256'] == sha(packed(packet)) and context_plan['modelSha256'] == contract.MODEL_SHA
                and context_plan['profile'] == PROFILE and context_plan['sampling'] == contract.NATIVE_SAMPLING
                and context_plan['sourceExposure'] is False and context_plan['previousModelRequests'] == 0
                and context_plan['exportManifest'] == export['evidence'][0], 'context_plan_binding')
        require(state['status'] == 'completed' and state['failure'] is None and state['completionRequestsSent'] == 1
                and type(state['completionRequestsSent']) is int and type(context_plan['previousModelRequests']) is int
                and state['nativeStopped'] is True and state['shutdown']['stopped'] is True
                and state['shutdown']['terminationByRetainedHandle'] is True and state['monitorError'] is None
                and state['validatedDraft'] == 'draft.json', 'context_completed_one_call_required')
        creation, _ = read(context / 'process.json')
        process = creation['identity']
        identity = (process['pid'], process['creationTicks'])
        require(all(type(n) is int and n > 0 for n in identity) and identity not in identities
                and creation['pid'] == process['pid'] == state['shutdown']['pid']
                and creation['atomicJobAssignment'] is True and creation['createSuspended'] is True
                and creation['limitAppliedBeforeResume'] is True and creation['resumePreviousCount'] == 1
                and process['priorityClass'] == resources.BELOW_NORMAL, 'fresh_native_owned_identity_required')
        identities.add(identity)
        created = (process['creationTicks'] - 116444736000000000) / 10000000
        require(previous_finished is None or created >= previous_finished, 'native_process_lifetimes_overlap')
        samples, sample_raw = read_jsonl(context / 'resource-samples.jsonl')
        require(samples and all(resource_reason(item['system'], item['child']) is None for item in samples), 'recorded_resource_guard_failed')
        command, _ = read(context / 'runtime-command.json')
        argv = command['argv']
        port = argv[argv.index('--port') + 1]
        require(re.fullmatch('[1-9][0-9]{0,4}', port) is not None and int(port) <= 65535
                and command == {'argv': server_command(root, int(port), '[EPHEMERAL_REDACTED]'), 'shell': False}, 'native_command_identity')
        observations, _ = read(context / 'preflight.json')
        require(len(observations) == 2 and observations[1]['monotonicSeconds'] - observations[0]['monotonicSeconds'] >= PROFILE['preflightGapSeconds'], 'fresh_preflight_pair_required')
        for observation in observations:
            memory, processes = observation['system'], observation['processes']
            require(resource_reason(memory) is None and memory['availablePhysicalBytes'] >= PROFILE['minimumStartPhysicalBytes']
                    and memory['availableCommitBytes'] >= PROFILE['minimumStartCommitBytes'] and not processes['nativeConflicts'] and not processes['classifiedConflicts'], 'saved_preflight_failure')
            require(datetime.fromisoformat(observation['at']).timestamp() <= created, 'preflight_after_process_creation')
        client = ReplayClient(context, root)
        client.skip_health()
        actual_parity = parity(client, packet, template, metadata, root)
        saved_parity, _ = read(context / 'prompt-parity.json')
        require(saved_parity == actual_parity and actual_parity['completionRequestsSent'] == 0, 'saved_prompt_parity_changed')
        intent, _ = read(context / 'completion-intent.json')
        require(all(intent.get(k) == value for k, value in context_plan.items()) and intent['sequence'] == 1 and intent['automaticRetry'] is False, 'completion_intent_binding')
        require(datetime.fromisoformat(intent['at']).timestamp() >= created, 'completion_before_process_creation')
        response = client.request('/completion', contract.completion_payload(packet, actual_parity['tokenIds']))
        require(client.index == len(client.items) and client.completions == 1, 'exactly_one_completion_and_no_later_requests')
        draft = contract.validate_native(response, actual_parity['prompt'], actual_parity['tokenIds'], packet)
        require((context / 'draft.json').read_bytes() == packed(draft), 'validated_draft_rewritten')
        contexts.append({'reviewId': rid, 'contextId': cid, 'packetSha256': sha(packed(packet)), 'draft': draft,
                         'process': process, 'evidence': context_evidence})
        previous_finished = datetime.fromisoformat(state['finishedAt']).timestamp()
    verify_refs(root, evidence + export['evidence'])
    check_installation_stat(install, root)
    require(plan['pythonIdentity'] == python_identity(), 'python_changed_during_validation')
    require(validated_snapshot(folder, root)[0] == summary, 'run_changed_during_validation')
    return {'contexts': contexts, 'summary': summary, 'evidence': evidence + export['evidence'] + [install['manifest']]}


def read_jsonl(path):
    raw = Path(path).read_bytes()
    require(raw.endswith(b'\n') and all(line.strip() for line in raw.splitlines()), 'resource_jsonl_truncated')
    return [contract.parse(line.decode('utf-8')) for line in raw.splitlines()], raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    start = sub.add_parser('run')
    start.add_argument('--export', required=True)
    start.add_argument('--destination', required=True)
    check = sub.add_parser('validate')
    check.add_argument('--export', required=True)
    check.add_argument('--run', required=True)
    args = parser.parse_args()
    result = run(args.export, args.destination) if args.command == 'run' else validate_run(args.run, args.export)
    print(json.dumps({'status': result['summary']['status'], 'completedContexts': result['summary']['completedContexts']}))


if __name__ == '__main__':
    main()
