"""One pinned zero-call recovery; immutable v1 native contexts and v2 inventory.

No source/formal data import, inference on import, prior record mutation or retry.
Admission failure consumes neither the fixed new run nor its permanent claim.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/model-comparison'))
from input_execution_v1 import local_question_runner_v1 as v1
from input_execution_v1 import local_question_runner_inventory_v2 as v2
from input_execution_v1 import local_question_contract_v1 as contract
from input_execution_v1.local_question_runner_v1 import (
    BASE, FREEZE, MODEL, PROFILE, resources, require, packed, sha, read,
    rooted, development, file_sha, reference, verify_refs, python_identity,
    gguf_template, render_prompt, write_json, write_new, utc, snapshot,
    validated_snapshot, check_installation_stat, resource_reason,
    server_command, ReplayClient, parity, read_jsonl,
)
from input_execution_v1.local_question_runner_inventory_v2 import installation

VERSION = 'input-execution-v1-local-question-runner-zero-call-recovery-v3'
RECOVERY_VERSION = 'input-execution-v1-local-question-zero-call-recovery-freeze-v3'
RECOVERY_FREEZE = 'content/model-comparison/input-execution-v1/local-question-zero-call-recovery-freeze-v3.json'
PRIOR_RUN = BASE + '/s5-local-question-native-inventory-v2-20260914/attempt-001'
PRIOR_CLAIM = BASE + '/.local-question-native-v1-generation.claim.json'
PRIOR_REVIEW = '.training/verifications/question-zero-call-recovery-review-20260914.md'
DESTINATION = BASE + '/s5-local-question-native-recovery-v3-20260914/attempt-001'
RECOVERY_CLAIM = BASE + '/.local-question-native-recovery-v3-generation.claim.json'
AUDIT_BASE = '.training/verifications'
INVENTORY_FREEZE_SHA = '7e9524ac1d1150dece0448b508f6743bf551b9bba8483c62b855152590b4a8a1'
PRIOR_HASHES = {
    PRIOR_RUN + '/model-chat-template.jinja': '7f0e529032c25183bcd66c7f238da2d377f43be754a94e2725a58c4e16d2ed67',
    PRIOR_RUN + '/plan.json': 'a6a9cbb6d6d8bded97b486e609d5f9244a293f37839dee7bd8c5b46828ab407f',
    PRIOR_RUN + '/summary.json': 'fc1978fdd1bd7935314ae0c01f259c610446a00b9712a6f664b6f098bd713348',
    PRIOR_CLAIM: '2eb1e66d411970a9a55cc52344261dc51bffa9c50851ed51606b1b830d9ec019',
    PRIOR_REVIEW: '77c9c6573920528c093df95272e1f960875e647c303bf84151f78094af5e019f',
}
REQUIRED_RECOVERY_FILES = frozenset({
    'content/model-comparison/input-execution-v1/LOCAL_QUESTION_ZERO_CALL_RECOVERY_V3.md',
    *('scripts/model-comparison/input_execution_v1/' + name + '.py' for name in (
        'local_question_runner_recovery_v3', 'test_local_question_runner_recovery_v3',
        'local_question_transport_recovery_v3', 'test_local_question_transport_recovery_v3',
        'local_question_evaluation_recovery_v3', 'test_local_question_evaluation_recovery_v3',
        'local_question_append_evidence_recovery_v3', 'test_local_question_append_evidence_recovery_v3',
    )),
})
SAFE_ERROR_CODES = frozenset({
    'ac_power_required', 'ac_power_lost_or_unknown', 'temporary_power_request_failed',
    'temporary_power_release_failed', 'power_request_wrong_thread_release',
    'experiment_slot_already_held_in_process', 'experiment_mutex_create_failed', 'experiment_slot_busy',
    'experiment_mutex_wait_failed', 'experiment_mutex_wrong_thread_release', 'experiment_mutex_release_failed',
    'cim_process_query_failed', 'windows_required', 'question_preflight_model_or_worker',
    'insufficient_start_availablephysicalbytes', 'insufficient_start_availablecommitbytes',
    'question_context_failed', 'evidence_hash_changed', 'installation_changed',
    'recovery_already_consumed', 'question_run_must_be_new', 'single_recovery_destination_required',
    'recovery_changed_before_admission', 'recovery_changed_before_consumption',
    'final_installation_changed', 'final_python_identity_changed', 'active_power_request_required',
    'fresh_preflight_pair_required', 'python_or_jinja_runtime_contract',
    'installed_manifest_changed', 'installed_model_contract', 'installed_runtime_contract', 'runtime_51_file_inventory',
    'prior_zero_call_exact_inventory', 'prior_failed_zero_call_binding', 'prior_inventory_freeze_changed',
    'zero_call_recovery_freeze_binding', 'zero_call_recovery_exact_code_inventory',
    'zero_call_recovery_small_code_only', 'recovery_export_changed', 'file_changed_during_hash',
})


def validate_prior(root=ROOT):
    root = Path(root).resolve()
    folder = development(root, PRIOR_RUN)
    paths = sorted(p.relative_to(folder).as_posix() for p in folder.rglob('*'))
    require(paths == ['model-chat-template.jinja', 'plan.json', 'summary.json'], 'prior_zero_call_exact_inventory')
    refs = [{'path': path, 'sha256': digest} for path, digest in PRIOR_HASHES.items()]
    verify_refs(root, refs)
    summary, _ = validated_snapshot(folder, root)
    plan, plan_raw = read(folder / 'plan.json')
    claim, claim_raw = read(rooted(root, PRIOR_CLAIM))
    correction = v2.load_correction(root)
    require(correction['correctionFreeze']['sha256'] == INVENTORY_FREEZE_SHA, 'prior_inventory_freeze_changed')
    require(summary['version'] == plan['version'] == claim['version'] == v2.VERSION
            and summary['status'] == 'failed' and summary['failure'] == {'type': 'ResourceGuardError', 'code': 'ResourceGuardError'}
            and summary['powerRequest'] is None and summary['sourceExposure'] is False
            and summary['expectedContexts'] == 64
            and all(type(summary.get(k)) is int and summary[k] == 0 for k in ('completedContexts', 'freshNativeProcesses', 'completionRequestsSent'))
            and summary['inventoryCorrection'] == plan['inventoryCorrection'] == correction
            and plan['contextProducerVersion'] == summary['contextProducerVersion'] == v1.VERSION
            and claim['runPath'] == PRIOR_RUN and claim['exportManifest'] == plan['exportManifest']
            and plan_raw == packed(plan) and claim_raw == packed(claim), 'prior_failed_zero_call_binding')
    verify_refs(root, refs)
    require(validated_snapshot(folder, root)[0] == summary, 'prior_changed_during_validation')
    require(sorted(p.relative_to(folder).as_posix() for p in folder.rglob('*')) == paths, 'prior_zero_call_exact_inventory')
    return {'priorRun': {'path': PRIOR_RUN, 'status': 'failed', 'expectedContexts': 64,
                         'completedContexts': 0, 'freshNativeProcesses': 0, 'completionRequestsSent': 0,
                         'exportManifest': plan['exportManifest'], 'files': refs[:3]},
            'priorClaim': refs[3], 'priorReview': refs[4],
            'baseExecutionFreeze': correction['baseExecutionFreeze'],
            'inventoryCorrectionFreeze': correction['correctionFreeze']}


def load_recovery(root=ROOT):
    root = Path(root).resolve()
    prior = validate_prior(root)
    value, raw = read(rooted(root, RECOVERY_FREEZE))
    require(raw == packed(value) and set(value) == {'version', 'actualQuestionCallsAtFreeze', 'files', *prior}
            and value['version'] == RECOVERY_VERSION
            and type(value['actualQuestionCallsAtFreeze']) is int and value['actualQuestionCallsAtFreeze'] == 0
            and all(value[k] == v for k, v in prior.items()), 'zero_call_recovery_freeze_binding')
    files = value['files']
    require(isinstance(files, list) and len(files) == len(REQUIRED_RECOVERY_FILES)
            and all(isinstance(r, dict) and set(r) == {'path', 'sha256'} and isinstance(r['path'], str)
                    and isinstance(r['sha256'], str) and re.fullmatch('[0-9a-f]{64}', r['sha256']) for r in files)
            and {r['path'] for r in files} == REQUIRED_RECOVERY_FILES, 'zero_call_recovery_exact_code_inventory')
    for item in files:
        require(rooted(root, item['path']).stat().st_size <= 4 * 1024 ** 2, 'zero_call_recovery_small_code_only')
    result = {'version': RECOVERY_VERSION, **prior,
              'recoveryFreeze': {'path': RECOVERY_FREEZE, 'sha256': sha(raw)}, 'files': files}
    verify_refs(root, recovery_refs(result))
    require(validate_prior(root) == prior, 'prior_changed_during_recovery_validation')
    return result


def recovery_refs(identity):
    refs = [*identity['priorRun']['files'], identity['priorClaim'], identity['priorReview'],
            identity['baseExecutionFreeze'], identity['inventoryCorrectionFreeze'],
            identity['recoveryFreeze'], *identity['files']]
    seen, result = set(), []
    for item in refs:
        key = (item['path'], item['sha256'])
        if key not in seen:
            seen.add(key)
            result.append(dict(item))
    return result


def load_export(export_dir, root=ROOT):
    export = v2.load_export(export_dir, root)
    identity = load_recovery(root)
    require(export['evidence'][0] == identity['priorRun']['exportManifest'], 'recovery_export_changed')
    export['zeroCallRecovery'] = identity
    seen = {(ref['path'], ref['sha256']) for ref in export['evidence']}
    export['evidence'].extend(ref for ref in recovery_refs(identity) if (ref['path'], ref['sha256']) not in seen)
    verify_refs(root, export['evidence'])
    return export


def failure_record(error):
    code = getattr(error, 'code', None) if isinstance(error, resources.ResourceGuardError) else None
    if code is None and isinstance(error, ValueError) and str(error) in SAFE_ERROR_CODES:
        code = str(error)
    if not isinstance(code, str) or code not in SAFE_ERROR_CODES:
        code = type(error).__name__
    return {'type': type(error).__name__, 'code': code}


def observe_preflight():
    observations = []
    for index in range(2):
        if index:
            time.sleep(PROFILE['preflightGapSeconds'])
        observations.append({'at': utc(), 'monotonicSeconds': time.monotonic(),
                             'system': resources.system_state(), 'processes': resources.process_state()})
    return observations


def validate_preflight(observations):
    require(isinstance(observations, list) and len(observations) == 2
            and observations[1]['monotonicSeconds'] - observations[0]['monotonicSeconds'] >= PROFILE['preflightGapSeconds'],
            'fresh_preflight_pair_required')
    for observation in observations:
        state, processes = observation['system'], observation['processes']
        resources.require(state.get('ACLineStatus') == 1, 'ac_power_required')
        for key in ('availablePhysicalBytes', 'availableCommitBytes'):
            wanted = PROFILE['minimumStartPhysicalBytes' if key == 'availablePhysicalBytes' else 'minimumStartCommitBytes']
            resources.require(type(state.get(key)) is int and state[key] >= wanted, 'insufficient_start_' + key.lower())
        resources.require(not processes['nativeConflicts'] and not processes['classifiedConflicts'], 'question_preflight_model_or_worker')
    return observations


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
            result['zeroCallRecovery'] = load_recovery(root)
            require(not rooted(root, DESTINATION).exists() and not rooted(root, RECOVERY_CLAIM).exists(), 'recovery_already_consumed')
            result['installation'] = installation(root)
            result['pythonIdentity'] = python_identity()
            _, result['gguf'] = gguf_template(rooted(root, MODEL))
            result['observations'] = observe_preflight()
            validate_preflight(result['observations'])
            verify_refs(root, recovery_refs(result['zeroCallRecovery']))
            result['status'] = 'passed'
    except BaseException as error:
        result['failure'] = failure_record(error)
        result['status'] = 'failed'
    result['finishedAt'] = utc()
    write_json(destination, result)
    return result


def run(export_dir, destination, root=ROOT):
    root = Path(root).resolve()
    destination = development(root, destination)
    require(destination == rooted(root, DESTINATION), 'single_recovery_destination_required')
    require(not destination.exists(), 'question_run_must_be_new')
    export = load_export(export_dir, root)
    completed, failure, power, claim_ref = [], None, None, None
    admitted = False
    admission = {'version': VERSION + '-admission', 'initialPreflight': [], 'finalPreflight': []}
    started = time.monotonic()
    try:
        with resources.ExperimentLock():
            require(not rooted(root, RECOVERY_CLAIM).exists(), 'recovery_already_consumed')
            require(load_recovery(root) == export['zeroCallRecovery'], 'recovery_changed_before_admission')
            admission['initialPreflight'] = observe_preflight()
            validate_preflight(admission['initialPreflight'])
            with resources.PowerRequest() as power:
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
                require(load_recovery(root) == export['zeroCallRecovery'], 'recovery_changed_before_consumption')
                require(not rooted(root, RECOVERY_CLAIM).exists() and not destination.exists(), 'recovery_already_consumed')
                admission['consumedAt'] = utc()
                admission['powerBeforeConsumption'] = dict(power.receipt)
                require(power.receipt['requested'] is True and power.receipt['released'] is False, 'active_power_request_required')
                destination.mkdir(parents=True, exist_ok=False)
                admitted = True
                write_json(destination / 'admission.json', admission)
                plan = {'version': VERSION, 'contextProducerVersion': v1.VERSION,
                        'inventoryCorrection': export['inventoryCorrection'], 'zeroCallRecovery': export['zeroCallRecovery'],
                        'expectedContexts': 64, 'exportManifest': export['evidence'][0],
                        'installation': install, 'pythonIdentity': interpreter, 'gguf': metadata, 'profile': PROFILE,
                        'reviewIds': list(contract.IDS), 'modelSha256': contract.MODEL_SHA,
                        'sourceExposure': False, 'previousModelRequestsPerContext': 0, 'actualCollaborationSpawns': 0,
                        'codeAndExportEvidence': export['evidence'], 'startedAt': admission['consumedAt'],
                        'admission': reference(root, destination / 'admission.json'), 'recoveryClaimPath': RECOVERY_CLAIM}
                write_json(destination / 'plan.json', plan)
                write_new(destination / 'model-chat-template.jinja', template.encode('utf-8'))
                write_json(rooted(root, RECOVERY_CLAIM), {'version': VERSION, 'runPath': DESTINATION,
                           'priorClaim': export['zeroCallRecovery']['priorClaim'], 'recoveryFreeze': export['zeroCallRecovery']['recoveryFreeze'],
                           'exportManifest': export['evidence'][0], 'plan': reference(root, destination / 'plan.json'),
                           'admission': plan['admission'], 'at': utc()})
                claim_ref = reference(root, RECOVERY_CLAIM)
                execution_export = {**export, 'evidence': [*export['evidence'], claim_ref]}
                for packet in export['packets']:
                    completed.append(v1.execute_context(packet, destination / packet['reviewId'], install, template, metadata, execution_export, root, started))
                    print(json.dumps({'reviewId': packet['reviewId'], 'completed': len(completed), 'total': 64}), flush=True)
        verify_refs(root, [*export['evidence'], claim_ref])
        require(installation(root) == install, 'final_installation_changed')
        require(python_identity() == interpreter, 'final_python_identity_changed')
    except BaseException as error:
        failure = failure_record(error)
        if not admitted:
            audit = audit_path(root, AUDIT_BASE + '/question-recovery-admission-' + str(uuid.uuid4()) + '.json')
            write_json(audit, {'version': VERSION + '-admission-failure', 'failure': failure, 'admission': admission,
                              'zeroCallRecovery': export['zeroCallRecovery'], 'newRunCreated': False, 'newClaimCreated': False,
                              'nativeProcessesStarted': 0, 'completionRequestsSent': 0,
                              'powerRequest': None if power is None else power.receipt, 'finishedAt': utc()})
            raise
    contexts = [read(p)[0] for p in sorted(destination.glob('R[0-9][0-9][0-9]/summary.json'))]
    summary = {'version': VERSION, 'contextProducerVersion': v1.VERSION,
               'inventoryCorrection': export['inventoryCorrection'], 'zeroCallRecovery': export['zeroCallRecovery'],
               'status': 'completed' if failure is None else 'failed', 'failure': failure,
               'expectedContexts': 64, 'completedContexts': len(completed), 'freshNativeProcesses': sum((p.parent / 'process.json').exists() for p in destination.glob('R[0-9][0-9][0-9]/plan.json')),
               'completionRequestsSent': sum(r['completionRequestsSent'] for r in contexts), 'nativeStopped': all(r['nativeStopped'] for r in contexts),
               'modelSha256': contract.MODEL_SHA, 'sourceExposure': False, 'actualCollaborationSpawns': 0,
               'recoveryClaim': claim_ref,
               'powerRequest': None if power is None else power.receipt, 'finishedAt': utc(), 'artifactFiles': snapshot(destination, root)}
    write_json(destination / 'summary.json', summary)
    require(failure is None, 'question_run_failed')
    return validate_run(destination, export_dir, root)


def validate_admission(folder, plan, summary, export, root):
    require(folder == rooted(root, DESTINATION) and plan.get('recoveryClaimPath') == RECOVERY_CLAIM
            and summary.get('zeroCallRecovery') == plan.get('zeroCallRecovery') == export['zeroCallRecovery'], 'zero_call_recovery_run_binding')
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
            and set(claim) == {'version', 'runPath', 'priorClaim', 'recoveryFreeze', 'exportManifest', 'plan', 'admission', 'at'}
            and claim['version'] == VERSION and claim['runPath'] == DESTINATION
            and claim['priorClaim'] == export['zeroCallRecovery']['priorClaim']
            and claim['recoveryFreeze'] == export['zeroCallRecovery']['recoveryFreeze']
            and claim['exportManifest'] == export['evidence'][0] and claim['plan'] == reference(root, folder / 'plan.json')
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
            and plan.get('contextProducerVersion') == summary.get('contextProducerVersion') == v1.VERSION,
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
        require(context_plan.get('version') == state.get('version') == v1.VERSION, 'frozen_context_producer_identity')
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
    evidence.append(summary['recoveryClaim'])
    require(load_recovery(root) == export['zeroCallRecovery'], 'recovery_changed_during_validation')
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
