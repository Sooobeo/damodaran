"""Explicit installation-inventory correction; frozen v1 native contexts unchanged.

No inference on import. This coordinator reads only the question-only export.
The run/replay bodies retain every v1 check and call its immutable pure helpers.
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
from input_execution_v1 import local_question_contract_v1 as contract
from input_execution_v1.local_question_runner_v1 import (
    BASE, FREEZE, INSTALL, INSTALL_SHA, MODEL, RUNTIME, PROFILE, resources,
    require, packed, sha, read, rooted, development, file_sha, reference,
    verify_refs, stat_id, python_identity, gguf_template, render_prompt,
    write_json, write_new, utc, snapshot, validated_snapshot,
    check_installation_stat, resource_reason, server_command, ReplayClient,
    parity, read_jsonl,
)

VERSION = 'input-execution-v1-local-question-runner-inventory-v2'
CORRECTION_VERSION = 'input-execution-v1-local-question-inventory-correction-freeze-v2'
CORRECTION_FREEZE = 'content/model-comparison/input-execution-v1/local-question-inventory-correction-freeze-v2.json'
BASE_FREEZE_SHA = 'fe4f62dd806d698450373bd9bc159e94b361004066d02af08af9031b3ef7ba68'
REQUIRED_CORRECTION_FILES = frozenset({
    'content/model-comparison/input-execution-v1/LOCAL_QUESTION_INVENTORY_CORRECTION_V2.md',
    *('scripts/model-comparison/input_execution_v1/' + name + '.py' for name in (
        'local_question_runner_inventory_v2', 'test_local_question_runner_inventory_v2',
        'local_question_transport_inventory_v2', 'test_local_question_transport_inventory_v2',
        'local_question_evaluation_inventory_v2', 'test_local_question_evaluation_inventory_v2',
        'local_question_append_evidence_inventory_v2', 'test_local_question_append_evidence_inventory_v2',
    )),
})


def load_correction(root=ROOT):
    root = Path(root).resolve()
    base_ref = reference(root, FREEZE)
    require(base_ref['sha256'] == BASE_FREEZE_SHA, 'inventory_correction_base_freeze_changed')
    base, base_raw = read(rooted(root, FREEZE))
    require(sha(base_raw) == BASE_FREEZE_SHA, 'inventory_correction_base_freeze_changed')
    contract.validate_freeze(root, base)
    path = rooted(root, CORRECTION_FREEZE)
    value, raw = read(path)
    require(raw == packed(value) and set(value) == {'version', 'baseExecutionFreeze', 'actualQuestionCallsAtFreeze', 'files'}
            and value['version'] == CORRECTION_VERSION and value['baseExecutionFreeze'] == base_ref
            and type(value['actualQuestionCallsAtFreeze']) is int and value['actualQuestionCallsAtFreeze'] == 0,
            'inventory_correction_freeze_contract')
    files = value['files']
    require(isinstance(files, list) and len(files) == len(REQUIRED_CORRECTION_FILES)
            and all(isinstance(r, dict) and set(r) == {'path', 'sha256'} and isinstance(r['path'], str)
                    and isinstance(r['sha256'], str) and re.fullmatch('[0-9a-f]{64}', r['sha256']) for r in files)
            and {r['path'] for r in files} == REQUIRED_CORRECTION_FILES,
            'inventory_correction_exact_code_inventory')
    for item in files:
        require(rooted(root, item['path']).stat().st_size <= 4 * 1024 ** 2, 'inventory_correction_small_code_only')
    verify_refs(root, files)
    correction_ref = {'path': CORRECTION_FREEZE, 'sha256': sha(raw)}
    verify_refs(root, [base_ref, correction_ref])
    return {'version': CORRECTION_VERSION, 'baseExecutionFreeze': base_ref,
            'correctionFreeze': correction_ref, 'files': files}


def load_export(export_dir, root=ROOT):
    export = v1.load_export(export_dir, root)
    correction = load_correction(root)
    export['inventoryCorrection'] = correction
    export['evidence'].extend([correction['correctionFreeze'], *correction['files']])
    verify_refs(root, export['evidence'])
    return export


def installation(root=ROOT):
    root = Path(root).resolve()
    manifest_path = rooted(root, INSTALL)
    require(file_sha(manifest_path) == INSTALL_SHA, 'installed_manifest_changed')
    value, manifest_raw = read(manifest_path)
    require(sha(manifest_raw) == INSTALL_SHA, 'installed_manifest_changed')
    require(value['model']['sha256'] == contract.MODEL_SHA and value['model']['file'] == contract.MODEL_NAME
            and value['model']['revision'] == '3885219b6810b007914f3a7950a8d1b469d598a5'
            and value['model']['sizeBytes'] == 5680522464, 'installed_model_contract')
    require(value['runtime']['commit'] == '72797e89198ab564fd0e6baa54ab196e8dd1d884'
            and value['runtime']['archiveSha256'] == '68d0ea47c71a55f6a19219727d39075f08f7da2c9d9073c7b47b3d64a7027284', 'installed_runtime_contract')
    files = [{'path': MODEL, 'sha256': contract.MODEL_SHA}, *value['runtime']['files']]
    runtime_paths = {rooted(root, r['path']) for r in value['runtime']['files']}
    require(len(value['runtime']['files']) == len(runtime_paths) == 51
            and all(p.is_relative_to(rooted(root, RUNTIME)) for p in runtime_paths)
            and runtime_paths == {p.resolve() for p in rooted(root, RUNTIME).rglob('*') if p.is_file()},
            'runtime_51_file_inventory')
    verify_refs(root, files)
    require(file_sha(manifest_path) == INSTALL_SHA, 'installed_manifest_changed')
    return {'manifest': reference(root, manifest_path), 'modelSha256': contract.MODEL_SHA,
            'files': [{'path': rooted(root, r['path']).relative_to(root).as_posix(), 'sha256': r['sha256']} for r in files],
            'statIdentities': {rooted(root, r['path']).relative_to(root).as_posix(): stat_id(rooted(root, r['path'])) for r in files}}


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
    plan = {'version': VERSION, 'contextProducerVersion': v1.VERSION,
            'inventoryCorrection': export['inventoryCorrection'], 'expectedContexts': 64, 'exportManifest': export['evidence'][0],
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
                    completed.append(v1.execute_context(packet, destination / packet['reviewId'], install, template, metadata, export, root, started))
                    print(json.dumps({'reviewId': packet['reviewId'], 'completed': len(completed), 'total': 64}), flush=True)
        verify_refs(root, export['evidence'])
        require(installation(root) == install, 'final_installation_changed')
        require(python_identity() == interpreter, 'final_python_identity_changed')
    except BaseException as error:
        failure = {'type': type(error).__name__, 'code': str(error) if isinstance(error, ValueError) else type(error).__name__}
    contexts = [read(p)[0] for p in sorted(destination.glob('R[0-9][0-9][0-9]/summary.json'))]
    summary = {'version': VERSION, 'contextProducerVersion': v1.VERSION,
               'inventoryCorrection': export['inventoryCorrection'], 'status': 'completed' if failure is None else 'failed', 'failure': failure,
               'expectedContexts': 64, 'completedContexts': len(completed), 'freshNativeProcesses': sum((p.parent / 'process.json').exists() for p in destination.glob('R[0-9][0-9][0-9]/plan.json')),
               'completionRequestsSent': sum(r['completionRequestsSent'] for r in contexts), 'nativeStopped': all(r['nativeStopped'] for r in contexts),
               'modelSha256': contract.MODEL_SHA, 'sourceExposure': False, 'actualCollaborationSpawns': 0,
               'powerRequest': None if power is None else power.receipt, 'finishedAt': utc(), 'artifactFiles': snapshot(destination, root)}
    write_json(destination / 'summary.json', summary)
    require(failure is None, 'question_run_failed')
    return validate_run(destination, export_dir, root)


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
    previous_finished = None
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
    args = parser.parse_args()
    result = run(args.export, args.destination) if args.command == 'run' else validate_run(args.run, args.export)
    print(json.dumps({'status': result['summary']['status'], 'completedContexts': result['summary']['completedContexts']}))


if __name__ == '__main__':
    main()
