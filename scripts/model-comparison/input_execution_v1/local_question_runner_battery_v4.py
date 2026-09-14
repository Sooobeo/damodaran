"""Battery-authorized coordinator, explicit v4 native contexts, immutable prior runs.

No model execution on import. Every native gets one question-only packet/call.
Battery <=20 percent or unknown power readings abort before the next request.
"""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/model-comparison'))
from input_execution_v1 import local_question_runner_v1 as v1
from input_execution_v1 import local_question_runner_recovery_v3 as v3
from input_execution_v1 import local_question_contract_v1 as contract
from input_execution_v1.local_question_runner_v1 import (
    BASE, FREEZE, MODEL, resources, require, packed, sha, read, rooted,
    development, file_sha, reference, verify_refs, python_identity, gguf_template,
    render_prompt, write_json, write_new, utc, snapshot, validated_snapshot,
    check_installation_stat, server_command, ReplayClient, parity, read_jsonl,
    Client, spawn,
)
from input_execution_v1.local_question_runner_inventory_v2 import installation

VERSION = 'input-execution-v1-local-question-runner-battery-v4'
BATTERY_VERSION = 'input-execution-v1-local-question-battery-execution-freeze-v4'
BATTERY_FREEZE = 'content/model-comparison/input-execution-v1/local-question-battery-execution-freeze-v4.json'
PRIOR_RECOVERY_FREEZE_SHA = '14d0874f7db816ced7b0db76bf05745a52c8ccb93cda1aceb5b15830a81dc644'
USER_AUTHORIZATION = {'source': 'user-current-session', 'instruction': '충전기 연결은 안 됐는데 배터리 잔량은 충분함. 진행해봐', 'acOverride': True}
POWER_POLICY = {'allowedACLineStatus': [0, 1], 'minimumBatteryPercentExclusive': 20,
                'unknownBatteryAction': 'abort', 'batterySource': 'GetSystemPowerStatus.BatteryLifePercent'}
PROFILE = {**v1.PROFILE, 'requiresACLineStatus': None, 'allowedACLineStatus': [0, 1],
           'minimumBatteryPercentExclusive': 20, 'unknownBatteryAction': 'abort'}
DESTINATION = BASE + '/s5-local-question-native-battery-v4-20260914/attempt-001'
RECOVERY_CLAIM = BASE + '/.local-question-native-battery-v4-generation.claim.json'
SUPERSESSION_PATH = v3.RECOVERY_CLAIM
SUPERSESSION_VERSION = 'input-execution-v1-local-question-v3-superseded-by-battery-v4'
AUDIT_BASE = '.training/verifications'
REQUIRED_BATTERY_FILES = frozenset({
    'content/model-comparison/input-execution-v1/LOCAL_QUESTION_BATTERY_EXECUTION_V4.md',
    *('scripts/model-comparison/input_execution_v1/' + name + '.py' for name in (
        'local_question_runner_battery_v4', 'test_local_question_runner_battery_v4',
        'local_question_transport_battery_v4', 'test_local_question_transport_battery_v4',
        'local_question_evaluation_battery_v4', 'test_local_question_evaluation_battery_v4',
        'local_question_append_evidence_battery_v4', 'test_local_question_append_evidence_battery_v4',
    )),
})
SAFE_ERROR_CODES = v3.SAFE_ERROR_CODES | frozenset({
    'battery_level_unknown', 'battery_reserve_reached', 'ac_line_status_unknown',
    'memory_query_failed', 'power_query_failed', 'old_entrypoint_already_consumed',
    'battery_freeze_binding', 'battery_exact_code_inventory', 'prior_recovery_freeze_changed',
})
load_recovery = v3.load_recovery
recovery_refs = v3.recovery_refs


def load_battery(root=ROOT):
    root = Path(root).resolve()
    prior = v3.load_recovery(root)
    require(prior['recoveryFreeze']['sha256'] == PRIOR_RECOVERY_FREEZE_SHA, 'prior_recovery_freeze_changed')
    value, raw = read(rooted(root, BATTERY_FREEZE))
    require(raw == packed(value) and set(value) == {'version', 'zeroCallRecovery', 'userAuthorization', 'powerPolicy', 'actualQuestionCallsAtFreeze', 'files'}
            and value['version'] == BATTERY_VERSION and value['zeroCallRecovery'] == prior
            and value['userAuthorization'] == USER_AUTHORIZATION and value['powerPolicy'] == POWER_POLICY
            and type(value['actualQuestionCallsAtFreeze']) is int and value['actualQuestionCallsAtFreeze'] == 0, 'battery_freeze_binding')
    files = value['files']
    require(isinstance(files, list) and len(files) == len(REQUIRED_BATTERY_FILES)
            and all(isinstance(r, dict) and set(r) == {'path', 'sha256'} and isinstance(r['path'], str)
                    and isinstance(r['sha256'], str) and re.fullmatch('[0-9a-f]{64}', r['sha256']) for r in files)
            and {r['path'] for r in files} == REQUIRED_BATTERY_FILES, 'battery_exact_code_inventory')
    for item in files:
        require(rooted(root, item['path']).stat().st_size <= 4 * 1024 ** 2, 'battery_small_code_only')
    result = {'version': BATTERY_VERSION, 'zeroCallRecovery': prior,
              'userAuthorization': USER_AUTHORIZATION, 'powerPolicy': POWER_POLICY,
              'batteryFreeze': {'path': BATTERY_FREEZE, 'sha256': sha(raw)}, 'files': files}
    verify_refs(root, battery_refs(result))
    require(v3.load_recovery(root) == prior, 'prior_changed_during_battery_validation')
    require(not rooted(root, v3.DESTINATION).exists(), 'old_entrypoint_already_consumed')
    return result


def battery_refs(identity):
    return [*v3.recovery_refs(identity['zeroCallRecovery']), identity['batteryFreeze'], *identity['files']]


def load_export(export_dir, root=ROOT):
    export = v3.load_export(export_dir, root)
    identity = load_battery(root)
    require(export['zeroCallRecovery'] == identity['zeroCallRecovery'], 'battery_prior_export_changed')
    export['batteryExecution'] = identity
    seen = {(r['path'], r['sha256']) for r in export['evidence']}
    export['evidence'].extend(r for r in battery_refs(identity) if (r['path'], r['sha256']) not in seen)
    verify_refs(root, export['evidence'])
    return export


def battery_reason(state):
    if type(state.get('ACLineStatus')) is not int or state['ACLineStatus'] not in (0, 1):
        return 'ac_line_status_unknown'
    percent = state.get('BatteryLifePercent')
    if type(percent) is not int or not 0 <= percent <= 100:
        return 'battery_level_unknown'
    if percent <= POWER_POLICY['minimumBatteryPercentExclusive']:
        return 'battery_reserve_reached'
    return None


def write_supersession(root, export):
    require(not rooted(root, SUPERSESSION_PATH).exists() and not rooted(root, v3.DESTINATION).exists(), 'old_entrypoint_already_consumed')
    write_json(rooted(root, SUPERSESSION_PATH), {'version': SUPERSESSION_VERSION,
               'blockedOldEntryPoint': v3.VERSION, 'blockedRunPath': v3.DESTINATION,
               'supersededBy': VERSION, 'runPath': DESTINATION, 'newClaimPath': RECOVERY_CLAIM,
               'batteryFreeze': export['batteryExecution']['batteryFreeze'],
               'originalClaim': export['zeroCallRecovery']['priorClaim'], 'at': utc(),
               'representsV3Execution': False, 'completionRequestsSent': 0})
    return reference(root, SUPERSESSION_PATH)


def validate_supersession(root, ref, export):
    require(ref['path'] == SUPERSESSION_PATH and ref == reference(root, SUPERSESSION_PATH)
            and not rooted(root, v3.DESTINATION).exists(), 'supersession_marker_binding')
    value, raw = read(rooted(root, SUPERSESSION_PATH))
    require(raw == packed(value) and set(value) == {'version', 'blockedOldEntryPoint', 'blockedRunPath', 'supersededBy', 'runPath', 'newClaimPath', 'batteryFreeze', 'originalClaim', 'at', 'representsV3Execution', 'completionRequestsSent'}
            and value['version'] == SUPERSESSION_VERSION and value['blockedOldEntryPoint'] == v3.VERSION
            and value['blockedRunPath'] == v3.DESTINATION and value['supersededBy'] == VERSION
            and value['runPath'] == DESTINATION and value['newClaimPath'] == RECOVERY_CLAIM
            and value['batteryFreeze'] == export['batteryExecution']['batteryFreeze']
            and value['originalClaim'] == export['zeroCallRecovery']['priorClaim']
            and value['representsV3Execution'] is False and type(value['completionRequestsSent']) is int
            and value['completionRequestsSent'] == 0, 'supersession_marker_binding')
    return value


def system_state():
    kernel = resources._kernel()
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(resources._MemoryStatus)]
    kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
    kernel.GetSystemPowerStatus.argtypes = [ctypes.POINTER(resources._PowerStatus)]
    kernel.GetSystemPowerStatus.restype = wintypes.BOOL
    memory = resources._MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    power = resources._PowerStatus()
    resources.require(kernel.GlobalMemoryStatusEx(ctypes.byref(memory)), "memory_query_failed")
    resources.require(kernel.GetSystemPowerStatus(ctypes.byref(power)), "power_query_failed")
    return {"availablePhysicalBytes": int(memory.availablePhysical),
            "availableCommitBytes": int(memory.availableCommit),
            "totalPhysicalBytes": int(memory.totalPhysical),
            "totalCommitBytes": int(memory.totalCommit),
            "commitSource": "GlobalMemoryStatusEx.ullAvailPageFile",
            "ACLineStatus": int(power.ACLineStatus), "BatteryFlag": int(power.BatteryFlag), "BatteryLifePercent": int(power.BatteryLifePercent), "powerSource": POWER_POLICY["batterySource"]}


def resource_reason(state, child=None):
    reason = battery_reason(state)
    if reason:
        return reason
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


class PowerRequest:
    """Temporary display+system request, cleared on its owning thread; no settings."""
    def __init__(self):
        self.active, self.thread_id = False, None
        self.receipt = {"requested": False, "released": False, "globalSettingsChanged": False,
                        "manualSleepPrevented": False, "displayAndSystem": True}

    def __enter__(self):
        reason = battery_reason(system_state())
        resources.require(reason is None, reason or "battery_level_unknown")
        self.kernel = resources._kernel()
        self.kernel.SetThreadExecutionState.argtypes = [wintypes.DWORD]
        self.kernel.SetThreadExecutionState.restype = wintypes.DWORD
        resources.require(self.kernel.SetThreadExecutionState(0x80000003), "temporary_power_request_failed")
        self.active, self.thread_id = True, threading.get_ident()
        self.receipt["requested"] = True
        return self

    def __exit__(self, *args):
        if self.active:
            resources.require(threading.get_ident() == self.thread_id, "power_request_wrong_thread_release")
            resources.require(self.kernel.SetThreadExecutionState(0x80000000), "temporary_power_release_failed")
            self.active = False
            self.receipt["released"] = True


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
        state, child, now = system_state(), self.limiter.sample_child(self.process), time.monotonic()
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


def observe_preflight():
    observations = []
    for index in range(2):
        if index:
            time.sleep(PROFILE['preflightGapSeconds'])
        observations.append({'at': utc(), 'monotonicSeconds': time.monotonic(),
                             'system': system_state(), 'processes': resources.process_state()})
    return observations


def validate_preflight(observations):
    require(isinstance(observations, list) and len(observations) == 2
            and observations[1]['monotonicSeconds'] - observations[0]['monotonicSeconds'] >= PROFILE['preflightGapSeconds'],
            'fresh_preflight_pair_required')
    for observation in observations:
        state, processes = observation['system'], observation['processes']
        reason = battery_reason(state)
        resources.require(reason is None, reason or 'battery_level_unknown')
        for key in ('availablePhysicalBytes', 'availableCommitBytes'):
            wanted = PROFILE['minimumStartPhysicalBytes' if key == 'availablePhysicalBytes' else 'minimumStartCommitBytes']
            resources.require(type(state.get(key)) is int and state[key] >= wanted, 'insufficient_start_' + key.lower())
        resources.require(not processes['nativeConflicts'] and not processes['classifiedConflicts'], 'question_preflight_model_or_worker')
    return observations


def preflight_pair():
    return validate_preflight(observe_preflight())


def failure_record(error):
    code = getattr(error, 'code', None) if isinstance(error, resources.ResourceGuardError) else None
    if code is None and isinstance(error, ValueError) and str(error) in SAFE_ERROR_CODES:
        code = str(error)
    if not isinstance(code, str) or code not in SAFE_ERROR_CODES:
        code = type(error).__name__
    return {'type': type(error).__name__, 'code': code}


def audit_path(root, destination):
    path = rooted(root, destination)
    require(path.parent == rooted(root, AUDIT_BASE) and path.suffix == '.json' and not path.exists(), 'new_verification_audit_required')
    return path


def preflight(destination, root=ROOT):
    """No question packets, native process, PowerRequest, run directory or claim."""
    root = Path(root).resolve()
    destination = audit_path(root, destination)
    result = {'version': VERSION + '-preflight', 'startedAt': utc(), 'status': 'failed', 'failure': None,
              'nativeProcessesStarted': 0, 'completionRequestsSent': 0, 'newRunCreated': False,
              'newClaimCreated': False, 'powerRequestCalled': False, 'requiresFreshRunAdmission': True}
    try:
        with resources.ExperimentLock():
            result['batteryExecution'] = load_battery(root)
            result['zeroCallRecovery'] = result['batteryExecution']['zeroCallRecovery']
            require(not rooted(root, SUPERSESSION_PATH).exists() and not rooted(root, v3.DESTINATION).exists(), 'old_entrypoint_already_consumed')
            require(not rooted(root, DESTINATION).exists() and not rooted(root, RECOVERY_CLAIM).exists(), 'recovery_already_consumed')
            result['installation'] = installation(root)
            result['pythonIdentity'] = python_identity()
            _, result['gguf'] = gguf_template(rooted(root, MODEL))
            result['observations'] = observe_preflight()
            validate_preflight(result['observations'])
            verify_refs(root, battery_refs(result['batteryExecution']))
            result['status'] = 'passed'
    except BaseException as error:
        result['failure'] = failure_record(error)
        result['status'] = 'failed'
    result['finishedAt'] = utc()
    write_json(destination, result)
    return result


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
        observations = observe_preflight()
        write_json(folder / 'preflight.json', observations)
        validate_preflight(observations)
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
        failure = failure_record(error)
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
    require(destination == rooted(root, DESTINATION), 'single_recovery_destination_required')
    require(not destination.exists(), 'question_run_must_be_new')
    export = load_export(export_dir, root)
    completed, failure, power, claim_ref, marker_ref = [], None, None, None, None
    admitted = False
    admission = {'version': VERSION + '-admission', 'initialPreflight': [], 'finalPreflight': []}
    started = time.monotonic()
    try:
        with resources.ExperimentLock():
            require(not rooted(root, SUPERSESSION_PATH).exists() and not rooted(root, v3.DESTINATION).exists(), 'old_entrypoint_already_consumed')
            require(not rooted(root, RECOVERY_CLAIM).exists(), 'recovery_already_consumed')
            require(load_battery(root) == export['batteryExecution'], 'recovery_changed_before_admission')
            admission['initialPreflight'] = observe_preflight()
            validate_preflight(admission['initialPreflight'])
            with PowerRequest() as power:
                admission['powerEnteredAt'] = utc()
                interpreter = python_identity()
                install = installation(root)
                template, metadata = gguf_template(rooted(root, MODEL))
                for packet in export['packets']:
                    render_prompt(packet, template)
                admission['finalPreflight'] = observe_preflight()
                validate_preflight(admission['finalPreflight'])
                verify_refs(root, export['evidence'])
                check_installation_stat(install, root)
                require(load_battery(root) == export['batteryExecution'], 'recovery_changed_before_consumption')
                require(not rooted(root, RECOVERY_CLAIM).exists() and not destination.exists(), 'recovery_already_consumed')
                admission['consumedAt'] = utc()
                admission['powerBeforeConsumption'] = dict(power.receipt)
                require(power.receipt['requested'] is True and power.receipt['released'] is False, 'active_power_request_required')
                destination.mkdir(parents=True, exist_ok=False)
                admitted = True
                write_json(destination / 'admission.json', admission)
                plan = {'version': VERSION, 'contextProducerVersion': VERSION,
                        'inventoryCorrection': export['inventoryCorrection'], 'zeroCallRecovery': export['zeroCallRecovery'], 'batteryExecution': export['batteryExecution'],
                        'expectedContexts': 64, 'exportManifest': export['evidence'][0],
                        'installation': install, 'pythonIdentity': interpreter, 'gguf': metadata, 'profile': PROFILE,
                        'reviewIds': list(contract.IDS), 'modelSha256': contract.MODEL_SHA,
                        'sourceExposure': False, 'previousModelRequestsPerContext': 0, 'actualCollaborationSpawns': 0,
                        'codeAndExportEvidence': export['evidence'], 'startedAt': admission['consumedAt'],
                        'admission': reference(root, destination / 'admission.json'), 'recoveryClaimPath': RECOVERY_CLAIM}
                write_json(destination / 'plan.json', plan)
                write_new(destination / 'model-chat-template.jinja', template.encode('utf-8'))
                marker_ref = write_supersession(root, export)
                write_json(rooted(root, RECOVERY_CLAIM), {'version': VERSION, 'runPath': DESTINATION,
                           'priorClaim': export['zeroCallRecovery']['priorClaim'], 'recoveryFreeze': export['zeroCallRecovery']['recoveryFreeze'],
                           'exportManifest': export['evidence'][0], 'plan': reference(root, destination / 'plan.json'),
                           'admission': plan['admission'], 'at': utc(), 'batteryFreeze': export['batteryExecution']['batteryFreeze'], 'supersessionMarker': marker_ref})
                claim_ref = reference(root, RECOVERY_CLAIM)
                execution_export = {**export, 'evidence': [*export['evidence'], claim_ref, marker_ref]}
                for packet in export['packets']:
                    completed.append(execute_context(packet, destination / packet['reviewId'], install, template, metadata, execution_export, root, started))
                    print(json.dumps({'reviewId': packet['reviewId'], 'completed': len(completed), 'total': 64}), flush=True)
        verify_refs(root, [*export['evidence'], claim_ref, marker_ref])
        require(installation(root) == install, 'final_installation_changed')
        require(python_identity() == interpreter, 'final_python_identity_changed')
    except BaseException as error:
        failure = failure_record(error)
        if not admitted:
            audit = audit_path(root, AUDIT_BASE + '/question-recovery-admission-' + str(uuid.uuid4()) + '.json')
            write_json(audit, {'version': VERSION + '-admission-failure', 'failure': failure, 'admission': admission,
                              'zeroCallRecovery': export['zeroCallRecovery'], 'batteryExecution': export['batteryExecution'], 'newRunCreated': False, 'newClaimCreated': False,
                              'nativeProcessesStarted': 0, 'completionRequestsSent': 0,
                              'powerRequest': None if power is None else power.receipt, 'finishedAt': utc()})
            raise
    contexts = [read(p)[0] for p in sorted(destination.glob('R[0-9][0-9][0-9]/summary.json'))]
    summary = {'version': VERSION, 'contextProducerVersion': VERSION,
               'inventoryCorrection': export['inventoryCorrection'], 'zeroCallRecovery': export['zeroCallRecovery'], 'batteryExecution': export['batteryExecution'],
               'status': 'completed' if failure is None else 'failed', 'failure': failure,
               'expectedContexts': 64, 'completedContexts': len(completed), 'freshNativeProcesses': sum((p.parent / 'process.json').exists() for p in destination.glob('R[0-9][0-9][0-9]/plan.json')),
               'completionRequestsSent': sum(r['completionRequestsSent'] for r in contexts), 'nativeStopped': all(r['nativeStopped'] for r in contexts),
               'modelSha256': contract.MODEL_SHA, 'sourceExposure': False, 'actualCollaborationSpawns': 0,
               'recoveryClaim': claim_ref, 'supersessionMarker': marker_ref,
               'powerRequest': None if power is None else power.receipt, 'finishedAt': utc(), 'artifactFiles': snapshot(destination, root)}
    write_json(destination / 'summary.json', summary)
    require(failure is None, 'question_run_failed')
    return validate_run(destination, export_dir, root)


def validate_admission(folder, plan, summary, export, root):
    require(folder == rooted(root, DESTINATION) and plan.get('recoveryClaimPath') == RECOVERY_CLAIM
            and summary.get('zeroCallRecovery') == plan.get('zeroCallRecovery') == export['zeroCallRecovery'], 'zero_call_recovery_run_binding')
    require(summary.get('batteryExecution') == plan.get('batteryExecution') == export['batteryExecution'], 'battery_run_binding')
    marker = validate_supersession(root, summary['supersessionMarker'], export)
    admission, raw = read(folder / 'admission.json')
    require(raw == packed(admission) and plan['admission'] == reference(root, folder / 'admission.json')
            and admission['version'] == VERSION + '-admission' and admission['consumedAt'] == plan['startedAt'], 'recovery_admission_binding')
    validate_preflight(admission['initialPreflight'])
    validate_preflight(admission['finalPreflight'])
    entered, consumed = (datetime.fromisoformat(admission[k]).timestamp() for k in ('powerEnteredAt', 'consumedAt'))
    require(all(datetime.fromisoformat(o['at']).timestamp() <= entered for o in admission['initialPreflight'])
            and all(entered <= datetime.fromisoformat(o['at']).timestamp() <= consumed for o in admission['finalPreflight'])
            and admission['powerBeforeConsumption']['requested'] is True and admission['powerBeforeConsumption']['released'] is False,
            'recovery_consumed_before_power_and_preflight')
    claim, claim_raw = read(rooted(root, RECOVERY_CLAIM))
    require(claim_raw == packed(claim) and summary['recoveryClaim'] == reference(root, RECOVERY_CLAIM)
            and set(claim) == {'version', 'runPath', 'priorClaim', 'recoveryFreeze', 'exportManifest', 'plan', 'admission', 'at', 'batteryFreeze', 'supersessionMarker'}
            and claim['version'] == VERSION and claim['runPath'] == DESTINATION
            and claim['priorClaim'] == export['zeroCallRecovery']['priorClaim']
            and claim['recoveryFreeze'] == export['zeroCallRecovery']['recoveryFreeze']
            and claim['exportManifest'] == export['evidence'][0] and claim['plan'] == reference(root, folder / 'plan.json')
            and claim['batteryFreeze'] == export['batteryExecution']['batteryFreeze']
            and claim['supersessionMarker'] == summary['supersessionMarker']
            and consumed <= datetime.fromisoformat(marker['at']).timestamp() <= datetime.fromisoformat(claim['at']).timestamp()
            and claim['admission'] == plan['admission'] and datetime.fromisoformat(claim['at']).timestamp() >= consumed,
            'recovery_claim_binding')
    return claim


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
    recovery_claim = validate_admission(folder, plan, summary, export, root)
    require(plan.get('inventoryCorrection') == summary.get('inventoryCorrection') == export['inventoryCorrection']
            and plan.get('contextProducerVersion') == summary.get('contextProducerVersion') == VERSION,
            'inventory_correction_run_binding')
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
    previous_finished = datetime.fromisoformat(recovery_claim['at']).timestamp()
    for packet in export['packets']:
        rid = packet['reviewId']
        context = folder / rid
        state, context_evidence = validated_snapshot(context, root)
        context_plan, _ = read(context / 'plan.json')
        require(context_plan.get('version') == state.get('version') == VERSION, 'frozen_context_producer_identity')
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
                         'process': process, 'evidence': context_evidence, 'contextProducerVersion': VERSION})
        previous_finished = datetime.fromisoformat(state['finishedAt']).timestamp()
    evidence.extend([summary['recoveryClaim'], summary['supersessionMarker']])
    require(load_battery(root) == export['batteryExecution'], 'recovery_changed_during_validation')
    verify_refs(root, evidence + export['evidence'])
    check_installation_stat(install, root)
    require(plan['pythonIdentity'] == python_identity(), 'python_changed_during_validation')
    require(validated_snapshot(folder, root)[0] == summary, 'run_changed_during_validation')
    return {'contexts': contexts, 'summary': summary, 'evidence': evidence + export['evidence'] + [install['manifest']]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    start = sub.add_parser('run')
    start.add_argument('--export', required=True)
    start.add_argument('--destination', required=True)
    check = sub.add_parser('validate')
    check.add_argument('--export', required=True)
    check.add_argument('--run', required=True)
    observe = sub.add_parser('preflight')
    observe.add_argument('--destination', required=True)
    args = parser.parse_args()
    if args.command == 'preflight':
        result = preflight(args.destination)
        print(json.dumps({'status': result['status'], 'failure': result['failure'], 'nativeProcessesStarted': 0, 'completionRequestsSent': 0}))
        return 0 if result['status'] == 'passed' else 2
    result = run(args.export, args.destination) if args.command == 'run' else validate_run(args.run, args.export)
    print(json.dumps({'status': result['summary']['status'], 'completedContexts': result['summary']['completedContexts']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
