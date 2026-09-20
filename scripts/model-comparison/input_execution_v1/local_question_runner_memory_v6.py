"""Preserve nine v4 contexts; 55 fresh contexts use explicit 7 GiB admission.

Explicit partial-run validation and logical cohort provenance. No model on import.
Only memory admission may wait; any native/context failure ends this new attempt.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/model-comparison'))
from input_execution_v1 import local_question_runner_battery_v4 as v4
from input_execution_v1 import local_question_runner_partial_recovery_v5 as v5
from input_execution_v1 import local_question_context_memory_v6 as native
from input_execution_v1 import local_question_contract_v1 as contract
from input_execution_v1.local_question_runner_battery_v4 import (
    BASE, MODEL, resources, require, packed, sha, read, rooted,
    development, file_sha, reference, verify_refs, python_identity, gguf_template,
    render_prompt, write_json, write_new, utc, snapshot, validated_snapshot,
    check_installation_stat, server_command, ReplayClient, parity, read_jsonl,
    installation, system_state, battery_reason, PowerRequest,
)
PROFILE = native.PROFILE
resource_reason = native.resource_reason
VERSION = 'input-execution-v1-local-question-runner-memory-v6'
COHORT_VERSION = 'input-execution-v1-local-question-memory-cohort-v6'
PARTIAL_VERSION = 'input-execution-v1-local-question-memory-execution-freeze-v6'
PARTIAL_FREEZE = 'content/model-comparison/input-execution-v1/local-question-memory-execution-freeze-v6.json'
PRIOR_RUN = v4.DESTINATION
DESTINATION = BASE + '/s5-local-question-native-memory-v6-20260920/attempt-001'
PARTIAL_CLAIM = BASE + '/.local-question-native-memory-v6-generation.claim.json'
SUPERSESSION_PATH = v5.PARTIAL_CLAIM
SUPERSESSION_VERSION = 'input-execution-v1-local-question-v5-superseded-by-memory-v6'
MEMORY_POLICY = {'version': 'question-memory-admission-policy-v6',
                 'changedProfileFields': ['minimumStartPhysicalBytes'],
                 'minimumStartPhysicalBytes': PROFILE['minimumStartPhysicalBytes'],
                 'minimumStartCommitBytes': PROFILE['minimumStartCommitBytes'],
                 'minimumRunningHeadroomBytes': PROFILE['minimumRunningHeadroomBytes'],
                 'maximumWorkingSetBytes': PROFILE['maximumWorkingSetBytes'],
                 'maximumPrivateBytes': PROFILE['maximumPrivateBytes'],
                 'workingSetApiMaximumBytes': PROFILE['workingSetApiMaximumBytes'],
                 'automaticModelRetry': False}
USER_AUTHORIZATION = {'source': 'user-current-session', 'instruction': '그냥 지금 시작하면 안됨?',
                      'interpretation': 'Start under an explicit lower physical-memory admission policy; retain runtime guards and quality criteria.'}
RETAINED_IDS, PENDING_IDS = tuple(contract.IDS[:9]), tuple(contract.IDS[9:])
WAIT_POLICY = {'maximumSeconds': 300, 'pollSeconds': 5, 'pairSeparationSeconds': 3,
               'allowedWaitReasons': ['insufficient_start_availablephysicalbytes', 'insufficient_start_availablecommitbytes']}
REQUIRED_PARTIAL_FILES = frozenset({
    'content/model-comparison/input-execution-v1/LOCAL_QUESTION_MEMORY_EXECUTION_V6.md',
    *('scripts/model-comparison/input_execution_v1/' + name + '.py' for name in (
        'local_question_context_memory_v6', 'test_local_question_context_memory_v6',
        'local_question_runner_memory_v6', 'test_local_question_runner_memory_v6',
        'local_question_transport_memory_v6', 'test_local_question_transport_memory_v6',
        'local_question_evaluation_memory_v6', 'test_local_question_evaluation_memory_v6',
        'local_question_append_evidence_memory_v6', 'test_local_question_append_evidence_memory_v6',
    )),
})
SAFE_ERROR_CODES = v4.SAFE_ERROR_CODES | frozenset({'memory_admission_timeout', 'invalid_memory_observation',
        'partial_recovery_already_consumed', 'prior_completed_context_changed', 'question_run_failed',
        'total_time_limit', 'partial_runtime_identity_changed', 'partial_identity_changed'})


def failure_record(error):
    code = getattr(error, 'code', None) if isinstance(error, resources.ResourceGuardError) else None
    if code is None and isinstance(error, ValueError) and str(error) in SAFE_ERROR_CODES:
        code = str(error)
    return {'type': type(error).__name__, 'code': code if isinstance(code, str) and code in SAFE_ERROR_CODES else type(error).__name__}


prior_snapshot = v5.prior_snapshot
validate_prior = v5.validate_prior


def partial_freeze_inputs(root=ROOT):
    previous = v5.load_partial_recovery(root)
    expected = v5.partial_freeze_inputs(root)
    expected.update(newRunPath=DESTINATION, newClaimPath=PARTIAL_CLAIM, waitPolicy=WAIT_POLICY,
                    previousPartialRecovery=previous, memoryPolicy=MEMORY_POLICY,
                    originalProfile=v4.PROFILE, profile=PROFILE, userAuthorization=USER_AUTHORIZATION)
    return expected


def partial_recovery_refs(identity):
    refs = [*v5.partial_recovery_refs(identity['previousPartialRecovery']), *v4.battery_refs(identity['batteryExecution']), identity['priorRun']['summary'], identity['priorRun']['plan'],
            *identity['priorRun']['failedContext']['files'], identity['priorClaim'], identity['priorSupersession'], identity['priorReview'],
            identity['partialRecoveryFreeze'], *identity['files']]
    unique = {}
    for ref in refs:
        unique[(ref['path'], ref['sha256'])] = dict(ref)
    return list(unique.values())


def load_partial_recovery(root=ROOT):
    root = Path(root).resolve()
    expected = partial_freeze_inputs(root)
    value, raw = read(rooted(root, PARTIAL_FREEZE))
    require(raw == packed(value) and set(value) == {'version', 'actualNewQuestionCallsAtFreeze', 'files', *expected}
            and value['version'] == PARTIAL_VERSION
            and type(value['actualNewQuestionCallsAtFreeze']) is int and value['actualNewQuestionCallsAtFreeze'] == 0
            and all(value[k] == v for k, v in expected.items()), 'partial_recovery_freeze_binding')
    files = value['files']
    require(isinstance(files, list) and len(files) == len(REQUIRED_PARTIAL_FILES)
            and all(isinstance(item, dict) and set(item) == {'path', 'sha256'} and isinstance(item['path'], str)
                    and isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256']) for item in files)
            and {item['path'] for item in files} == REQUIRED_PARTIAL_FILES, 'partial_recovery_exact_code_inventory')
    require(all(rooted(root, item['path']).stat().st_size <= 4 * 1024 ** 2 for item in files), 'partial_recovery_small_code_only')
    identity = {'version': PARTIAL_VERSION, **expected, 'partialRecoveryFreeze': {'path': PARTIAL_FREEZE, 'sha256': sha(raw)}, 'files': files}
    verify_refs(root, partial_recovery_refs(identity))
    return identity


def load_export(export_dir, root=ROOT):
    export = v4.load_export(export_dir, root)
    identity = load_partial_recovery(root)
    require(export['batteryExecution'] == identity['batteryExecution'], 'partial_battery_identity_changed')
    prior_plan, _ = read(rooted(root, PRIOR_RUN + '/plan.json'))
    require(export['evidence'][0] == prior_plan['exportManifest'], 'partial_export_manifest_changed')
    export['partialRecovery'] = identity
    known = {(ref['path'], ref['sha256']) for ref in export['evidence']}
    export['evidence'].extend(ref for ref in partial_recovery_refs(identity) if (ref['path'], ref['sha256']) not in known)
    verify_refs(root, export['evidence'])
    return export


def validate_plan(folder, plan, export, install, template, metadata, version, ids, root):
    require(plan['version'] == version and plan['contextProducerVersion'] == native.VERSION
            and plan['reviewIds'] == list(ids) and plan['expectedContexts'] == len(ids) and plan['profile'] == PROFILE
            and plan['codeAndExportEvidence'] == export['evidence'] and plan['exportManifest'] == export['evidence'][0]
            and plan['modelSha256'] == contract.MODEL_SHA and plan['sourceExposure'] is False
            and plan['previousModelRequestsPerContext'] == 0 and plan['actualCollaborationSpawns'] == 0
            and plan['inventoryCorrection'] == export['inventoryCorrection']
            and plan['zeroCallRecovery'] == export['zeroCallRecovery'] and plan['batteryExecution'] == export['batteryExecution'],
            'partial_run_plan_binding')
    require(plan['installation'] == install and plan['pythonIdentity'] == python_identity(), 'partial_runtime_identity_changed')
    require((folder / 'model-chat-template.jinja').read_bytes() == template.encode('utf-8') and plan['gguf'] == metadata, 'partial_gguf_identity_changed')


def wait_path(root, path):
    path = rooted(root, path)
    require(not path.exists() and (path.parent == rooted(root, '.training/verifications')
            or path.parent == rooted(root, DESTINATION + '/waits')), 'new_memory_wait_log_required')
    return path


def append_wait(path, item):
    with Path(path).open('ab') as stream:
        stream.write(packed(item))
        stream.flush()
        os.fsync(stream.fileno())


def non_memory_guard(observation):
    state, processes = observation['system'], observation['processes']
    reason = battery_reason(state)
    resources.require(reason is None, reason or 'battery_level_unknown')
    resources.require(not processes['nativeConflicts'] and not processes['classifiedConflicts'], 'question_preflight_model_or_worker')
    require(all(type(state.get(key)) is int and state[key] >= 0 for key in ('availablePhysicalBytes', 'availableCommitBytes')), 'invalid_memory_observation')


def wait_for_memory(path, root=ROOT):
    """No native/HTTP; only memory shortage may wait, at most 300 seconds."""
    root = Path(root).resolve()
    path = wait_path(root, path)
    write_new(path, b'')
    started = time.monotonic()
    header = {'event': 'started', 'version': VERSION + '-memory-admission', 'at': utc(), 'monotonicSeconds': started, 'waitPolicy': WAIT_POLICY}
    append_wait(path, header)
    attempt = 0
    try:
        while True:
            attempt += 1
            pair = []
            for index in range(2):
                if index:
                    remaining = WAIT_POLICY['maximumSeconds'] - (time.monotonic() - started)
                    resources.require(remaining >= WAIT_POLICY['pairSeparationSeconds'], 'memory_admission_timeout')
                    time.sleep(WAIT_POLICY['pairSeparationSeconds'])
                resources.require(time.monotonic() - started <= WAIT_POLICY['maximumSeconds'], 'memory_admission_timeout')
                observation = {'at': utc(), 'monotonicSeconds': time.monotonic(), 'system': system_state(), 'processes': resources.process_state()}
                append_wait(path, {'event': 'observation', 'attempt': attempt, 'index': index, **observation})
                non_memory_guard(observation)
                pair.append(observation)
            reasons = []
            for observation in pair:
                for key, limit in (('availablePhysicalBytes', 'minimumStartPhysicalBytes'), ('availableCommitBytes', 'minimumStartCommitBytes')):
                    if observation['system'][key] < PROFILE[limit]:
                        reasons.append('insufficient_start_' + key.lower())
            if not reasons:
                native.validate_preflight(pair)
                finished = time.monotonic()
                receipt = {'event': 'passed', 'at': utc(), 'monotonicSeconds': finished, 'attempts': attempt, 'elapsedSeconds': finished - started,
                           'passedPair': pair, 'nativeProcessesStarted': 0, 'completionRequestsSent': 0}
                require(receipt['elapsedSeconds'] <= WAIT_POLICY['maximumSeconds'], 'memory_admission_timeout')
                append_wait(path, receipt)
                return {'file': reference(root, path), **receipt}
            append_wait(path, {'event': 'memory_wait', 'attempt': attempt, 'reasons': sorted(set(reasons)), 'at': utc(), 'monotonicSeconds': time.monotonic()})
            remaining = WAIT_POLICY['maximumSeconds'] - (time.monotonic() - started)
            resources.require(remaining > 0, 'memory_admission_timeout')
            time.sleep(min(WAIT_POLICY['pollSeconds'], remaining))
    except BaseException as error:
        append_wait(path, {'event': 'failed', 'at': utc(), 'failure': failure_record(error),
                           'elapsedSeconds': time.monotonic() - started, 'nativeProcessesStarted': 0, 'completionRequestsSent': 0})
        raise


def validate_wait(ref, root):
    verify_refs(root, [ref])
    rows, _ = read_jsonl(rooted(root, ref['path']))
    require(rows[0]['event'] == 'started' and rows[0]['version'] == VERSION + '-memory-admission' and rows[0]['waitPolicy'] == WAIT_POLICY and rows[-1]['event'] == 'passed'
            and 0 <= rows[-1]['elapsedSeconds'] <= WAIT_POLICY['maximumSeconds'], 'memory_wait_receipt_binding')
    started = rows[0]['monotonicSeconds']
    require(abs(rows[-1]['monotonicSeconds'] - started - rows[-1]['elapsedSeconds']) < 1e-6, 'memory_wait_elapsed_binding')
    index, attempt, previous_wait = 1, 1, None
    prior_at = datetime.fromisoformat(rows[0]['at']).timestamp()
    while index < len(rows) - 1:
        require(index + 2 < len(rows), 'memory_wait_incomplete_attempt')
        pair = rows[index:index + 2]
        for number, observation in enumerate(pair):
            require(observation['event'] == 'observation' and type(observation['attempt']) is int and observation['attempt'] == attempt
                    and type(observation['index']) is int and observation['index'] == number,
                    'memory_wait_attempt_sequence')
            non_memory_guard(observation)
            at = datetime.fromisoformat(observation['at']).timestamp()
            require(at >= prior_at and started <= observation['monotonicSeconds'] <= started + WAIT_POLICY['maximumSeconds'], 'memory_wait_chronology')
            prior_at = at
        require(pair[1]['monotonicSeconds'] - pair[0]['monotonicSeconds'] >= WAIT_POLICY['pairSeparationSeconds']
                and (previous_wait is None or pair[0]['monotonicSeconds'] - previous_wait >= WAIT_POLICY['pollSeconds']), 'memory_wait_spacing')
        missing = sorted({'insufficient_start_' + key.lower() for observation in pair
                          for key, limit in (('availablePhysicalBytes', 'minimumStartPhysicalBytes'), ('availableCommitBytes', 'minimumStartCommitBytes'))
                          if observation['system'][key] < PROFILE[limit]})
        outcome = rows[index + 2]
        require(outcome['monotonicSeconds'] >= pair[1]['monotonicSeconds']
                and datetime.fromisoformat(outcome['at']).timestamp() >= prior_at, 'memory_wait_outcome_chronology')
        prior_at = datetime.fromisoformat(outcome['at']).timestamp()
        if outcome['event'] == 'passed':
            final = [{key: item[key] for key in ('at', 'monotonicSeconds', 'system', 'processes')} for item in pair]
            require(index + 2 == len(rows) - 1 and not missing and type(outcome['attempts']) is int and outcome['attempts'] == attempt
                    and outcome['passedPair'] == final and type(outcome['nativeProcessesStarted']) is int
                    and type(outcome['completionRequestsSent']) is int and outcome['nativeProcessesStarted'] == outcome['completionRequestsSent'] == 0,
                    'memory_wait_final_pair')
            native.validate_preflight(final)
            break
        require(outcome['event'] == 'memory_wait' and type(outcome['attempt']) is int and outcome['attempt'] == attempt
                and missing and outcome['reasons'] == missing, 'memory_wait_shortage_required')
        previous_wait = outcome['monotonicSeconds']
        index += 3
        attempt += 1
    else:
        raise ValueError('memory_wait_pass_missing')
    return rows[-1]


def replay_contexts(folder, packets, export, template, metadata, root, after):
    contexts, identities, context_ids = [], set(), set()
    previous_finished = after
    for packet in packets:
        rid = packet['reviewId']
        context = folder / rid
        state, context_evidence = validated_snapshot(context, root)
        context_plan, _ = read(context / 'plan.json')
        require(context_plan.get('version') == state.get('version') == native.VERSION, 'frozen_context_producer_identity')
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
                         'process': process, 'evidence': context_evidence, 'contextProducerVersion': native.VERSION})
        previous_finished = datetime.fromisoformat(state['finishedAt']).timestamp()
    verify_refs(root, [ref for context in contexts for ref in context['evidence']])
    return contexts


def require_old_unconsumed(root):
    require(not rooted(root, v5.DESTINATION).exists() and not rooted(root, SUPERSESSION_PATH).exists(),
            'old_entrypoint_already_consumed')


def write_supersession(root, export):
    require_old_unconsumed(root)
    value = {'version': SUPERSESSION_VERSION, 'blockedOldEntryPoint': v5.VERSION,
             'blockedRunPath': v5.DESTINATION, 'supersededBy': VERSION,
             'runPath': DESTINATION, 'newClaimPath': PARTIAL_CLAIM,
             'previousPartialRecoveryFreeze': export['partialRecovery']['previousPartialRecovery']['partialRecoveryFreeze'],
             'memoryExecutionFreeze': export['partialRecovery']['partialRecoveryFreeze'],
             'representsV5Execution': False, 'completionRequestsSent': 0, 'at': utc()}
    write_json(rooted(root, SUPERSESSION_PATH), value)
    return reference(root, SUPERSESSION_PATH)


def validate_supersession(ref, export, root):
    require(not rooted(root, v5.DESTINATION).exists(), 'old_entrypoint_already_consumed')
    require(ref == reference(root, SUPERSESSION_PATH), 'memory_supersession_reference')
    value, raw = read(rooted(root, SUPERSESSION_PATH))
    expected = {'version': SUPERSESSION_VERSION, 'blockedOldEntryPoint': v5.VERSION,
                'blockedRunPath': v5.DESTINATION, 'supersededBy': VERSION,
                'runPath': DESTINATION, 'newClaimPath': PARTIAL_CLAIM,
                'previousPartialRecoveryFreeze': export['partialRecovery']['previousPartialRecovery']['partialRecoveryFreeze'],
                'memoryExecutionFreeze': export['partialRecovery']['partialRecoveryFreeze'],
                'representsV5Execution': False, 'completionRequestsSent': 0}
    require(raw == packed(value) and set(value) == {*expected, 'at'}
            and all(value[k] == v for k, v in expected.items())
            and value['representsV5Execution'] is False and type(value['completionRequestsSent']) is int,
            'memory_supersession_binding')
    datetime.fromisoformat(value['at'])
    return value


def preflight(destination, root=ROOT):
    root = Path(root).resolve()
    destination = v4.audit_path(root, destination)
    result = {'version': VERSION + '-preflight', 'status': 'failed', 'failure': None,
              'nativeProcessesStarted': 0, 'completionRequestsSent': 0, 'newRunCreated': False,
              'newClaimCreated': False, 'powerRequestCalled': False, 'requiresFreshRunAdmission': True}
    try:
        with resources.ExperimentLock():
            result['partialRecovery'] = load_partial_recovery(root)
            require_old_unconsumed(root)
            require(not rooted(root, DESTINATION).exists() and not rooted(root, PARTIAL_CLAIM).exists(), 'partial_recovery_already_consumed')
            result['installation'] = installation(root)
            result['pythonIdentity'] = python_identity()
            result['memoryAdmission'] = wait_for_memory('.training/verifications/partial-preflight-memory-' + str(uuid.uuid4()) + '.jsonl', root)
            result['status'] = 'passed'
    except BaseException as error:
        result['failure'] = failure_record(error)
    result['finishedAt'] = utc()
    write_json(destination, result)
    return result


def run(export_dir, destination, root=ROOT):
    root = Path(root).resolve()
    destination = development(root, destination)
    require(destination == rooted(root, DESTINATION) and not destination.exists(), 'question_run_must_be_new')
    prior = validate_prior(export_dir, root)
    export = load_export(export_dir, root)
    require(export['packets'] == prior['export']['packets'], 'partial_export_packets_changed')
    install, template, metadata = prior['installation'], prior['template'], prior['metadata']
    interpreter = python_identity()
    for packet in export['packets']:
        render_prompt(packet, template)
    started = time.monotonic()
    completed, waits, failure, power, claim_ref, supersession_ref = [], [], None, None, None, None
    admitted = False
    admission = {'version': VERSION + '-admission'}
    try:
        with resources.ExperimentLock():
            require_old_unconsumed(root)
            require(not rooted(root, PARTIAL_CLAIM).exists(), 'partial_recovery_already_consumed')
            require(load_partial_recovery(root) == export['partialRecovery'], 'partial_identity_changed')
            initial = wait_for_memory('.training/verifications/partial-initial-memory-' + str(uuid.uuid4()) + '.jsonl', root)
            admission['initialWait'] = initial['file']
            with PowerRequest() as power:
                admission['powerEnteredAt'] = utc()
                check_installation_stat(install, root)
                verify_refs(root, export['evidence'])
                powered = wait_for_memory('.training/verifications/partial-powered-memory-' + str(uuid.uuid4()) + '.jsonl', root)
                admission['poweredWait'] = powered['file']
                require_old_unconsumed(root)
                require(not rooted(root, PARTIAL_CLAIM).exists() and not destination.exists(), 'partial_recovery_already_consumed')
                require(load_partial_recovery(root) == export['partialRecovery'], 'partial_identity_changed')
                admission['consumedAt'] = utc()
                admission['powerBeforeConsumption'] = dict(power.receipt)
                require(power.receipt['requested'] is True and power.receipt['released'] is False, 'active_power_request_required')
                destination.mkdir(parents=True, exist_ok=False)
                admitted = True
                write_json(destination / 'admission.json', admission)
                plan = {'version': VERSION, 'contextProducerVersion': native.VERSION, 'expectedContexts': 55,
                        'cohortExpectedContexts': 64, 'retainedContexts': 9, 'reviewIds': list(PENDING_IDS),
                        'exportManifest': export['evidence'][0], 'installation': install, 'pythonIdentity': interpreter,
                        'gguf': metadata, 'profile': PROFILE, 'modelSha256': contract.MODEL_SHA,
                        'inventoryCorrection': export['inventoryCorrection'], 'zeroCallRecovery': export['zeroCallRecovery'],
                        'batteryExecution': export['batteryExecution'], 'partialRecovery': export['partialRecovery'],
                        'sourceExposure': False, 'previousModelRequestsPerContext': 0, 'actualCollaborationSpawns': 0,
                        'codeAndExportEvidence': export['evidence'], 'startedAt': admission['consumedAt'],
                        'admission': reference(root, destination / 'admission.json'), 'partialClaimPath': PARTIAL_CLAIM,
                        'oldEntrypointSupersessionPath': SUPERSESSION_PATH}
                write_json(destination / 'plan.json', plan)
                write_new(destination / 'model-chat-template.jinja', template.encode('utf-8'))
                supersession_ref = write_supersession(root, export)
                write_json(rooted(root, PARTIAL_CLAIM), {'version': VERSION, 'runPath': DESTINATION,
                           'partialRecoveryFreeze': export['partialRecovery']['partialRecoveryFreeze'],
                           'priorRunSummary': export['partialRecovery']['priorRun']['summary'],
                           'priorClaim': export['partialRecovery']['priorClaim'], 'exportManifest': export['evidence'][0],
                           'plan': reference(root, destination / 'plan.json'), 'admission': plan['admission'],
                           'oldEntrypointSupersession': supersession_ref, 'at': utc()})
                claim_ref = reference(root, PARTIAL_CLAIM)
                for packet in export['packets'][9:]:
                    require(time.monotonic() - started < PROFILE['totalSeconds'], 'total_time_limit')
                    wait = wait_for_memory(destination / 'waits' / (packet['reviewId'] + '.jsonl'), root)
                    waits.append({'reviewId': packet['reviewId'], 'file': wait['file']})
                    execution_export = {**export, 'evidence': [*export['evidence'], claim_ref, supersession_ref, wait['file']]}
                    verify_refs(root, execution_export['evidence'])
                    require(time.monotonic() - started < PROFILE['totalSeconds'], 'total_time_limit')
                    # The explicit v6 context repeats its own fresh 7 GiB admission.
                    # Any failure from this call ends the run, never retries it.
                    completed.append(native.execute_context(packet, destination / packet['reviewId'], install, template, metadata, execution_export, root, started))
                    print(json.dumps({'reviewId': packet['reviewId'], 'completed': 9 + len(completed), 'total': 64, 'reused': 9}), flush=True)
        verify_refs(root, [*export['evidence'], claim_ref, supersession_ref, *[row['file'] for row in waits]])
        require(installation(root) == install and python_identity() == interpreter, 'partial_runtime_identity_changed')
    except BaseException as error:
        failure = failure_record(error)
        if not admitted:
            write_json(rooted(root, '.training/verifications/partial-admission-failed-' + str(uuid.uuid4()) + '.json'),
                       {'version': VERSION + '-admission-failure', 'failure': failure, 'admission': admission,
                        'newRunCreated': False, 'newClaimCreated': False, 'nativeProcessesStarted': 0,
                        'completionRequestsSent': 0, 'partialRecovery': export['partialRecovery'],
                        'powerRequest': None if power is None else power.receipt, 'finishedAt': utc()})
            raise
    contexts = [read(path)[0] for path in sorted(destination.glob('R[0-9][0-9][0-9]/summary.json'))]
    summary = {'version': VERSION, 'contextProducerVersion': native.VERSION, 'status': 'completed' if failure is None else 'failed',
               'failure': failure, 'expectedContexts': 55, 'completedContexts': len(completed), 'cohortExpectedContexts': 64, 'retainedContexts': 9,
               'freshNativeProcesses': sum((path.parent / 'process.json').exists() for path in destination.glob('R[0-9][0-9][0-9]/plan.json')),
               'completionRequestsSent': sum(row['completionRequestsSent'] for row in contexts),
               'nativeStopped': all(row['nativeStopped'] for row in contexts), 'partialClaim': claim_ref, 'waitReceipts': waits,
               'oldEntrypointSupersession': supersession_ref,
               'inventoryCorrection': export['inventoryCorrection'], 'zeroCallRecovery': export['zeroCallRecovery'],
               'batteryExecution': export['batteryExecution'], 'partialRecovery': export['partialRecovery'],
               'sourceExposure': False, 'actualCollaborationSpawns': 0, 'modelSha256': contract.MODEL_SHA,
               'powerRequest': None if power is None else power.receipt, 'finishedAt': utc(), 'artifactFiles': snapshot(destination, root)}
    write_json(destination / 'summary.json', summary)
    require(failure is None, 'question_run_failed')
    return validate_run(destination, export_dir, root)


def validate_new_admission(folder, plan, summary, export, root):
    require(folder == rooted(root, DESTINATION) and plan['partialClaimPath'] == PARTIAL_CLAIM
            and plan['oldEntrypointSupersessionPath'] == SUPERSESSION_PATH
            and plan['partialRecovery'] == summary['partialRecovery'] == export['partialRecovery']
            and plan['cohortExpectedContexts'] == summary['cohortExpectedContexts'] == 64
            and plan['retainedContexts'] == summary['retainedContexts'] == 9, 'partial_run_identity_binding')
    admission, raw = read(folder / 'admission.json')
    require(raw == packed(admission) and reference(root, folder / 'admission.json') == plan['admission']
            and admission['version'] == VERSION + '-admission' and admission['consumedAt'] == plan['startedAt'], 'partial_admission_binding')
    initial, powered = (validate_wait(admission[key], root) for key in ('initialWait', 'poweredWait'))
    entered, consumed = (datetime.fromisoformat(admission[key]).timestamp() for key in ('powerEnteredAt', 'consumedAt'))
    require(datetime.fromisoformat(initial['at']).timestamp() <= entered
            and all(entered <= datetime.fromisoformat(row['at']).timestamp() <= consumed for row in powered['passedPair'])
            and admission['powerBeforeConsumption']['requested'] is True and admission['powerBeforeConsumption']['released'] is False,
            'partial_consumed_before_power_and_memory')
    claim, claim_raw = read(rooted(root, PARTIAL_CLAIM))
    require(claim_raw == packed(claim) and summary['partialClaim'] == reference(root, PARTIAL_CLAIM)
            and set(claim) == {'version', 'runPath', 'partialRecoveryFreeze', 'priorRunSummary', 'priorClaim', 'exportManifest', 'plan', 'admission', 'oldEntrypointSupersession', 'at'}
            and claim['version'] == VERSION and claim['runPath'] == DESTINATION
            and claim['partialRecoveryFreeze'] == export['partialRecovery']['partialRecoveryFreeze']
            and claim['priorRunSummary'] == export['partialRecovery']['priorRun']['summary']
            and claim['priorClaim'] == export['partialRecovery']['priorClaim'] and claim['exportManifest'] == export['evidence'][0]
            and claim['plan'] == reference(root, folder / 'plan.json') and claim['admission'] == plan['admission']
            and claim['oldEntrypointSupersession'] == summary['oldEntrypointSupersession']
            and datetime.fromisoformat(claim['at']).timestamp() >= consumed, 'partial_claim_binding')
    supersession = validate_supersession(summary['oldEntrypointSupersession'], export, root)
    require(consumed <= datetime.fromisoformat(supersession['at']).timestamp()
            <= datetime.fromisoformat(claim['at']).timestamp(), 'memory_supersession_chronology')
    return claim, [admission['initialWait'], admission['poweredWait'], summary['oldEntrypointSupersession']]


def combine_contexts(retained, fresh):
    require(len(retained) == 9 and len(fresh) == 55, 'logical_cohort_partition_counts')
    require([row['reviewId'] for row in retained] == list(RETAINED_IDS)
            and [row['reviewId'] for row in fresh] == list(PENDING_IDS)
            and all(row['contextProducerVersion'] == v4.VERSION for row in retained)
            and all(row['contextProducerVersion'] == native.VERSION for row in fresh), 'logical_cohort_partition_identity')
    contexts = []
    for rows, path, version, status, reused in ((retained, PRIOR_RUN, v4.VERSION, 'failed', True),
                                               (fresh, DESTINATION, VERSION, 'completed', False)):
        for row in rows:
            contexts.append({**row, 'sourceRun': {'runPath': path, 'coordinatorVersion': version, 'status': status, 'reused': reused}})
    require(len({row['contextId'] for row in contexts}) == 64
            and len({(row['process']['pid'], row['process']['creationTicks']) for row in contexts}) == 64, 'logical_cohort_duplicate_or_missing')
    return contexts


def validate_run(run_dir, export_dir, root=ROOT):
    root = Path(root).resolve()
    folder = development(root, run_dir)
    require(folder == rooted(root, DESTINATION), 'single_partial_run_required')
    prior = validate_prior(export_dir, root)
    export = load_export(export_dir, root)
    summary, evidence = validated_snapshot(folder, root)
    require(summary['version'] == VERSION and summary['status'] == 'completed' and summary['failure'] is None
            and all(type(summary[k]) is int and summary[k] == 55 for k in ('expectedContexts', 'completedContexts', 'freshNativeProcesses', 'completionRequestsSent'))
            and summary['contextProducerVersion'] == native.VERSION and summary['nativeStopped'] is True
            and summary['sourceExposure'] is False and summary['actualCollaborationSpawns'] == 0
            and summary['modelSha256'] == contract.MODEL_SHA and summary['powerRequest']['released'] is True,
            'completed_partial_run_55_required')
    plan, _ = read(folder / 'plan.json')
    validate_plan(folder, plan, export, prior['installation'], prior['template'], prior['metadata'], VERSION, PENDING_IDS, root)
    require(all(summary[key] == export[key] for key in ('inventoryCorrection', 'zeroCallRecovery', 'batteryExecution', 'partialRecovery')), 'partial_summary_lineage')
    claim, admission_refs = validate_new_admission(folder, plan, summary, export, root)
    require({path.name for path in folder.iterdir() if path.is_dir()} == {*PENDING_IDS, 'waits'}, 'pending_context_inventory')
    require([row['reviewId'] for row in summary['waitReceipts']] == list(PENDING_IDS)
            and {path.name for path in (folder / 'waits').iterdir()} == {rid + '.jsonl' for rid in PENDING_IDS}, 'pending_wait_inventory')
    for row in summary['waitReceipts']:
        require(rooted(root, row['file']['path']) == folder / 'waits' / (row['reviewId'] + '.jsonl'), 'pending_wait_path')
        wait = validate_wait(row['file'], root)
        creation, _ = read(folder / row['reviewId'] / 'process.json')
        created = (creation['identity']['creationTicks'] - 116444736000000000) / 10000000
        require(datetime.fromisoformat(wait['at']).timestamp() <= created, 'context_started_before_memory_admission')
    fresh = replay_contexts(folder, export['packets'][9:], export, prior['template'], prior['metadata'], root,
                            datetime.fromisoformat(claim['at']).timestamp())
    contexts = combine_contexts(prior['contexts'], fresh)
    prior_last, _ = read(rooted(root, PRIOR_RUN + '/R009/summary.json'))
    require((fresh[0]['process']['creationTicks'] - 116444736000000000) / 10000000 >= datetime.fromisoformat(prior_last['finishedAt']).timestamp(), 'source_run_native_overlap')
    evidence += prior['evidence'] + export['evidence'] + admission_refs + [summary['partialClaim']]
    verify_refs(root, evidence)
    check_installation_stat(prior['installation'], root)
    require(plan['pythonIdentity'] == python_identity(), 'python_changed_during_validation')
    require(load_partial_recovery(root) == export['partialRecovery'] and validated_snapshot(folder, root)[0] == summary, 'cohort_changed_during_validation')
    source_runs = []
    for path, value in ((PRIOR_RUN, prior['summary']), (DESTINATION, summary)):
        source_runs.append({'runPath': path, **{key: value[key] for key in ('version', 'status', 'expectedContexts', 'completedContexts', 'freshNativeProcesses', 'completionRequestsSent', 'powerRequest')},
                            'summary': reference(root, path + '/summary.json')})
    logical = {'version': COHORT_VERSION, 'status': 'completed_logical_cohort', 'expectedContexts': 64, 'completedContexts': 64,
               'freshNativeProcesses': 64, 'completionRequestsSent': 64, 'retainedContexts': 9, 'freshContexts': 55,
               'contextProducerVersion': native.VERSION, 'sourceRuns': source_runs, 'nativeStopped': True,
               'sourceExposure': False, 'actualCollaborationSpawns': 0, 'modelSha256': contract.MODEL_SHA,
               'partialRecovery': export['partialRecovery'], 'batteryExecution': export['batteryExecution'],
               'zeroCallRecovery': export['zeroCallRecovery'], 'inventoryCorrection': export['inventoryCorrection'],
               'priorFailedContext': export['partialRecovery']['priorRun']['failedContext']}
    return {'contexts': contexts, 'summary': logical, 'evidence': evidence}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    start = sub.add_parser('run')
    start.add_argument('--export', required=True)
    start.add_argument('--destination', required=True)
    check = sub.add_parser('validate')
    check.add_argument('--export', required=True)
    check.add_argument('--run', required=True)
    pre = sub.add_parser('preflight')
    pre.add_argument('--destination', required=True)
    args = parser.parse_args()
    if args.command == 'preflight':
        result = preflight(args.destination)
        print(json.dumps({'status': result['status'], 'failure': result['failure'], 'completionRequestsSent': 0}))
        return 0 if result['status'] == 'passed' else 2
    result = run(args.export, args.destination) if args.command == 'run' else validate_run(args.run, args.export)
    print(json.dumps({'status': result['summary']['status'], 'completedContexts': result['summary']['completedContexts']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


