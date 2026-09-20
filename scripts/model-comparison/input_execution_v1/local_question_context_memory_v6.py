"""Explicit 7 GiB physical admission with the unchanged frozen v4 native lifecycle.

Only the physical startup threshold changes. Commit, runtime memory guards,
model, prompt/token parity, one-call isolation and owned cleanup remain fixed.
"""
from __future__ import annotations
import os
import secrets
import socket
import time
import urllib.error
import uuid

from input_execution_v1 import local_question_runner_battery_v4 as v4
from input_execution_v1.local_question_runner_battery_v4 import (
    contract, resources, require, packed, sha, write_new, write_json, utc,
    check_installation_stat, verify_refs, system_state, battery_reason,
    server_command, spawn, Monitor, Client, parity, snapshot, failure_record,
    resource_reason, PowerRequest,
)

VERSION = 'input-execution-v1-local-question-context-memory-v6'
PROFILE = {**v4.PROFILE, 'minimumStartPhysicalBytes': 7 * 1024 ** 3}

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



