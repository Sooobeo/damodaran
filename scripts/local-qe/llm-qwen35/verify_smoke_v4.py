"""Subset-three technical audit only; no inference, weights hash or quality gate.

Uses the evaluators' pure native-envelope checks. Their analyze_run() and 48-row
acceptance paths are never called, and no summary count or input hash is changed.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
DEST = ROOT / '.translation/qe/llm-candidates/qwen35-9b'
SMOKE_SHA = 'c2adb5f26dbf1405193bde562b07dd3238813659b6f7a8ffac2605554a9f9b4a'
FULL_INPUT_SHA = '828b3a5974ca3fd89fbb28a99e4dfbb8f0b5b23a4114a511b610795022e67cda'
AUDIT_SHA = '11352d7f17267c88608192de6c47ee6616b1f7bc9bc586f509e4a98ed44045cb'
FREEZE_V4_SHA = 'd57916747df96297c5fe08c8f87118fac2ce67a6e9e04e782af049358324ee67'
FREEZE_V3_SHA = '9d458d187db89e876b6c904d8f12e602172489f7d4ea3f58693518c1aa470925'
DEPENDENCIES = {
    'scripts/local-qe/evaluate_llm_review.py': 'e72d91c5a4956d3c9cbdb3353c8fd1fb911fc15b4ed5eaa446633e8635ed10ee',
    'scripts/local-qe/evaluate_llm_review_v2.py': '9c822fc50ad0202f647ea0690d74b2c1a67144f37f88a4094668271d9c9e44fa',
    'scripts/local-qe/llm-qwen35/contract.py': 'd4e6e8fd6e5d09729c1adf745468d383b217cba8da202b98bd66b26a76697a0b',
}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_fixed(relative, name):
    path = ROOT / relative
    require(sha(path) == DEPENDENCIES[relative], 'audit_dependency_changed')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def relative(path):
    return str(Path(path).resolve().relative_to(ROOT))


def memory_evidence(evaluator, path, summary, *, require_priority):
    samples = evaluator.read_lines(path)
    require(bool(samples), 'memory_samples_missing')
    identity = summary['workingSet']
    for sample in samples:
        child, system = sample['child'], sample['system']
        require(child['pid'] == summary['ownedPid'] == identity['pid']
                and child['creationTicks'] == identity['creationTicks'], 'memory_child_identity_differs')
        require(sample['abortReason'] is None and max(child['workingSetBytes'], child['peakWorkingSetBytes'],
                child['privateBytes']) <= 8 * 1024**3, 'memory_guard_limit_failed')
        require(min(system['availablePhysical'], system['availableCommit']) >= 1024**3, 'memory_headroom_floor_failed')
        if require_priority:
            require(child['priorityClass'] == 16384 and child['priorityName'] == 'BelowNormal', 'memory_priority_differs')
    return {'peakWorkingSetBytes': max(s['child']['peakWorkingSetBytes'] for s in samples),
        'maximumObservedPrivateBytes': max(s['child']['privateBytes'] for s in samples),
        'privateIsSampledMaximumNotOsPeakCounter': True,
        'minimumFreePhysicalBytes': min(s['system']['availablePhysical'] for s in samples),
        'minimumFreeCommitBytes': min(s['system']['availableCommit'] for s in samples),
        'sampleCount': len(samples), 'allGuardSamplesPassed': True,
        'allSamplesBelowNormal': True if require_priority else None,
        'priorityAndCpuCountersAvailable': require_priority,
        'latestSampledCpuSeconds': samples[-1]['child'].get('totalCpuSeconds')}


def verify_run(evaluator, version, inputs, contract, audit, mapping=None):
    run = DEST / 'runs' / ('smoke-first3-' + version)
    summary = evaluator.read(run / 'summary.json')
    require(type(summary['expectedCount']) is int and summary['expectedCount'] == 3
            and summary['inputSha256'] == SMOKE_SHA and summary['generationRequests'] == 3,
            'not_original_first3_smoke')
    require(summary['status'] == 'completed' and summary['modelLoaded'] is True
            and summary['runtimeContractValidated'] is True and summary['finalIntegrityVerified'] is True
            and summary['childProcessStopped'] is True
            and evaluator.same_json(summary['guard'], {'abortReason': None, 'killError': None}), 'smoke_execution_incomplete')
    require(not any(k in summary for k in ('failure', 'guardCleanupError', 'cleanupError', 'integrityError',
        'spawnCleanupUnconfirmed', 'spawnCleanupCode', 'runLockPreservedBecauseChildStopUnconfirmed')), 'smoke_cleanup_or_integrity_failure')
    require(evaluator.same_json(summary['itemStatuses'], [{'id': row['id'], 'status': 'completed'} for row in inputs]),
            'smoke_ids_or_order_changed')
    for name, expected in summary['artifacts'].items():
        require(Path(name).name == name and sha(run / name) == expected, 'smoke_artifact_changed')
    require(set(summary['artifacts']) == {p.name for p in run.iterdir() if p.is_file() and p.name != 'summary.json'},
            'smoke_unrecorded_artifact')
    requests = evaluator.read_lines(run / 'requests.jsonl')
    require(evaluator.same_json(requests, [{'id': row['id'], 'request': contract.build_request(row)} for row in inputs]),
            'smoke_projection_request_differs')
    require(summary['referencesOrJudgmentsIncluded'] is False and summary['inputFields'] == ['source', 'translation', 'context'],
            'smoke_input_provenance_differs')
    for path, expected in summary['codeHashes'].items():
        require(sha(path) == expected, 'smoke_current_source_or_python_changed')
    preflight = summary['memoryPreflight']
    require(preflight['physicalPassed'] is True and preflight['commitPassed'] is True
            and min(preflight['observed']['availablePhysical'], preflight['observed']['availableCommit']) >= 11 * 1024**3,
            'smoke_start_memory_headroom_failed')
    spawn = summary['spawn']
    require(spawn['pid'] == summary['ownedPid'] and spawn['atomicJobAssignment'] is True
            and spawn['createSuspended'] is True and spawn['limitAppliedBeforeResume'] is True
            and spawn['resumePreviousCount'] == 1 and evaluator.same_json(spawn['workingSetLimit'], summary['workingSet']),
            'smoke_owned_spawn_receipt_failed')
    runtime_log = (run / 'runtime.log').read_text(encoding='utf-8')
    require(re.search(r"printing all EOG tokens:[\s\S]*?248046 \('<\|im_end\|>'\)", runtime_log), 'runtime_eos_log_missing')
    props = evaluator.read(run / 'runtime-props.raw.json')
    expected_template = (DEST / 'evidence/gguf-chat-template.jinja').read_text(encoding='utf-8')
    require(props['chat_template'] == expected_template and props['total_slots'] == 1
            and props['default_generation_settings']['n_ctx'] == evaluator.CONTEXT, 'runtime_props_differs')
    if version == 'v4':
        require(summary['resourceProfile'] == evaluator.RESOURCE_PROFILE
                and evaluator.same_json(summary['nativeStartupConfiguration'], evaluator.STARTUP_CONFIGURATION)
                and evaluator.same_json(summary['materialWarningMapping'], evaluator.MAPPING_IDENTITY), 'v4_profile_or_mapping_differs')
        require('n_threads = 4 (n_threads_batch = 4)' in runtime_log and 'n_ctx_seq             = 4096' in runtime_log,
                'v4_cpu_context_log_missing')
        initial = summary['initialChildTelemetry']
        require(initial['priorityClass'] == 16384 and initial['priorityName'] == 'BelowNormal', 'initial_priority_differs')
    rows = []
    for index, row in enumerate(inputs, 1):
        prefix = f'{index:04d}'
        record = evaluator.read(run / (prefix + '-assessment.json'))
        arguments = [run, summary, prefix, record, requests[index-1]['request'], contract.load_schema()]
        if version == 'v4':
            arguments.append({'budgetRows': {item['id']: item for item in audit['rows']}})
        content = evaluator.validate_native_evidence(*arguments)
        validated = contract.validate_response(row, content)
        require(validated['status'] == 'valid' and evaluator.same_json(validated, record['assessment']), 'raw_contract_revalidation_differs')
        raw = evaluator.read(run / (prefix + '-completion.raw.json'))
        result = {'id': row['id'], 'inputTokens': record['inputTokens'], 'outputTokens': record['outputTokens'],
            'requestSeconds': record['seconds'], 'nativeTimings': raw['timings'],
            'rawResponseSha256': sha(run / (prefix + '-completion.raw.json')),
            'generatedContentSha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
            'promptSha256': record['promptSha256'], 'tokenReplySha256': sha(run / (prefix + '-tokenize.raw.json')),
            'eosAndTemplateTokenSamplingValidated': True, 'rawContractRevalidated': True}
        if version == 'v4':
            remapped = mapping.classify(validated)
            require(evaluator.same_json(remapped, record['materialWarningV2']), 'material_v2_recomputation_differs')
            result.update(materialWarningV2=remapped, childTelemetry=record['childTelemetry'])
        rows.append(result)
    checkpoints = [{'line': i, 'text': line} for i, line in enumerate(runtime_log.splitlines(), 1)
                   if 'restored context checkpoint' in line]
    if version == 'v4':
        require(len(checkpoints) == 2 and all('n_tokens = 1058, n_past = 1058' in item['text'] for item in checkpoints),
                'expected_two_1058_checkpoints_not_restored')
        require([row['nativeTimings']['cache_n'] for row in rows] == [0, 1058, 1058], 'expected_prefix_reuse_not_reported')
    return {'runPath': relative(run), 'summarySha256': sha(run / 'summary.json'), 'status': summary['status'],
        'expectedCount': 3, 'generationRequests': 3, 'artifactCount': len(summary['artifacts']),
        'artifactHashesAllMatch': True, 'currentSourceAndInterpreterHashesMatch': True,
        'childProcessStopped': True, 'finalIntegrityVerified': True, 'guard': summary['guard'],
        'ownedPid': summary['ownedPid'], 'creationTicks': summary['workingSet']['creationTicks'],
        'rows': rows, 'requestSecondsTotal': sum(row['requestSeconds'] for row in rows),
        'runElapsedSeconds': (datetime.fromisoformat(summary['finishedAt']) - datetime.fromisoformat(summary['createdAt'])).total_seconds(),
        'nativePromptMillisecondsTotal': sum(row['nativeTimings']['prompt_ms'] for row in rows),
        'nativeGenerationMillisecondsTotal': sum(row['nativeTimings']['predicted_ms'] for row in rows),
        'outputTokensTotal': sum(row['outputTokens'] for row in rows),
        'processedPromptTokensTotal': sum(row['nativeTimings']['prompt_n'] for row in rows),
        'reusedPromptTokensTotal': sum(row['nativeTimings']['cache_n'] for row in rows),
        'checkpointRestorationLog': checkpoints,
        'memory': memory_evidence(evaluator, run / 'memory.jsonl', summary, require_priority=version == 'v4')}


def main():
    output = DEST / 'smoke-technical-receipt-v4.json'
    require(not output.exists(), 'refusing_existing_receipt')
    sys.path.insert(0, str(ROOT / 'scripts/local-qe'))
    old = load_fixed('scripts/local-qe/evaluate_llm_review.py', 'smoke_envelope_v3')
    new = load_fixed('scripts/local-qe/evaluate_llm_review_v2.py', 'smoke_envelope_v4')
    contract = load_fixed('scripts/local-qe/llm-qwen35/contract.py', 'smoke_contract_v1')
    require(sha(DEST / 'freeze-v4.json') == FREEZE_V4_SHA and sha(DEST / 'freeze-v3.json') == FREEZE_V3_SHA,
            'preserved_freeze_changed')
    for name in ('v3', 'v4'):
        freeze = new.read(DEST / ('freeze-' + name + '.json'))
        field = 'runtimeCodeHashes' if name == 'v3' else 'codeHashes'
        for path, expected in freeze[field].items():
            require(sha(path) == expected, 'frozen_source_or_document_changed')
    inputs, input_sha = contract.read_input(DEST / 'inputs/smoke-first3-v1.jsonl')
    full_inputs, full_sha = contract.read_input(DEST / 'inputs/dev48-source-only-v1.jsonl')
    require(input_sha == SMOKE_SHA and full_sha == FULL_INPUT_SHA and inputs == full_inputs[:3] and len(inputs) == 3,
            'smoke_selection_changed')
    audit_path = DEST / 'token-budget-v1/attempt-001/summary.json'
    require(sha(audit_path) == AUDIT_SHA, 'token_budget_audit_changed')
    audit = new.read(audit_path)
    mapping = new.load_mapping()
    previous = verify_run(old, 'v3', inputs, contract, audit)
    current = verify_run(new, 'v4', inputs, contract, audit, mapping)
    require(all(a['promptSha256'] == b['promptSha256'] and a['tokenReplySha256'] == b['tokenReplySha256']
                for a, b in zip(previous['rows'], current['rows'])), 'v3_v4_input_prompt_tokens_differ')
    wrapper_path = ROOT / '.training/verifications/keep-awake-qwen-v4-smoke-20260911.json'
    wrapper = new.read(wrapper_path)
    require(wrapper['status'] == 'completed' and wrapper['exitCode'] == 0 and wrapper['childStopped'] is True
            and wrapper['released'] is True and wrapper['globalPowerSettingsChanged'] is False, 'keep_awake_cleanup_unconfirmed')
    comparison = {key: {'v3': previous[key], 'v4': current[key], 'deltaV4MinusV3': current[key] - previous[key]}
        for key in ('requestSecondsTotal', 'runElapsedSeconds', 'nativePromptMillisecondsTotal',
                    'nativeGenerationMillisecondsTotal', 'outputTokensTotal', 'processedPromptTokensTotal', 'reusedPromptTokensTotal')}
    for key in ('peakWorkingSetBytes', 'maximumObservedPrivateBytes'):
        comparison[key] = {'v3': previous['memory'][key], 'v4': current['memory'][key],
                          'deltaV4MinusV3': current['memory'][key] - previous['memory'][key]}
    receipt = {'version': 'qwen35-first3-technical-receipt-v4', 'createdAt': datetime.now(timezone.utc).isoformat(),
        'scope': 'original_order_first3_technical_smoke_subset_only', 'subsetCount': 3, 'fullDatasetCount': 48,
        'selection': 'First three original dev48 input rows, selected before Qwen candidate output observation.',
        'inputSha256': SMOKE_SHA, 'fullProjectedInputSha256': FULL_INPUT_SHA,
        'freezeV4Sha256': FREEZE_V4_SHA, 'freezeV3Sha256': FREEZE_V3_SHA, 'tokenAuditSha256': AUDIT_SHA,
        'dependencies': DEPENDENCIES, 'helperSha256': sha(__file__), 'mappingIdentity': mapping.identity(),
        'full48EvaluatorAnalyzeRunCalled': False, 'summaryCountsOrInputHashesRewritten': False,
        'newModelLoads': 0, 'newGenerationRequests': 0, 'modelWeightsRehashed': False,
        'existingFrozenSourcesAndDocumentsUnchanged': True, 'keepAwake': {'path': relative(wrapper_path),
            'sha256': sha(wrapper_path), 'receipt': wrapper}, 'v3': previous, 'v4': current, 'comparison': comparison,
        'allThreeV2PrimaryWarnings': [row['materialWarningV2']['primaryWarning'] for row in current['rows']],
        'allThreeV2MajorWarnings': [row['materialWarningV2']['supplementaryMajorWarning'] for row in current['rows']],
        'contentBytesIdenticalAcrossProfiles': [a['generatedContentSha256'] == b['generatedContentSha256']
                                              for a, b in zip(previous['rows'], current['rows'])],
        'cpuCounterComparison': {'v3': None, 'v4LatestRowCumulativeCpuSeconds': current['rows'][-1]['childTelemetry']['totalCpuSeconds'],
            'v3NotMeasured': True},
        'technicalSmokePassed': True, 'developmentParagraphGatePassed': False, 'full48QualityAccepted': False,
        'semanticQualityCertified': False, 'semanticSpanPrecision': 'not_evaluated', 'humanReviewed': False,
        'limitations': ['Only three inputs; no fixed judgments or semantic error-reason review used in this receipt.',
            'CPU threads, priority, polling, context and checkpoint/cache behavior changed together; this is not a cache-only causal experiment.',
            'Output lengths and content differ; matching seed does not guarantee byte-identical output.',
            'Three no-material-warning assertions do not establish 95% precision/recall or absence of meaning errors.',
            'Private memory is the maximum of periodic observations, not an OS peak-private counter.',
            'Different wall-clock runs and uncontrolled background/system conditions limit timing comparisons.']}
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'path': str(output), 'sha256': sha(output), 'technicalSmokePassed': True,
                      'subsetCount': 3, 'full48QualityAccepted': False, 'comparison': comparison}, ensure_ascii=False))


if __name__ == '__main__':
    main()
