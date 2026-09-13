"""Frozen v4 dev48 analysis using material-warning v2; no model execution.

Raw native evidence is revalidated before recomputing the separate v2 mapping.
V1 evidence/results and semantics certification remain unchanged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import importlib.util
import json
import math
from pathlib import Path

from calibrate import metrics, slice_gate
from common import PRODUCT_QE_POLICY

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / '.training/quality-evaluation/dev48-v1'
INPUT_SHA = '054d7f41e7f47b40b7216d047adcc35a0ab741409d954554143d0284cca3d222'
LABEL_SHA = '5e466ac8aaec742c5db7b4ed3f163f63273a5a8facbc274b1776d402f7046b19'
VERSION = 'qwen35-dev48-warning-analysis-v2'
MAPPING_SHA = '32298b2ac2cb0446fabefd2277b7bdab708e2414df7b32a395da3948a8b55208'
# Only the already-frozen v4 resource profile and v2 postprocessor are supported.
# Never import a runtime supplied by a run directory or by summary.codeHashes.
RUNTIME_VERSION = 'qwen35-semantic-review-run-v4'
SUPPORTED_CODE_HASHES = {
    'runtime_v4.py': '0a1ed555e55a21059c3395487819680d7a4c0ffb08ef4c49771269dc8fcb288c',
    'contract.py': 'd4e6e8fd6e5d09729c1adf745468d383b217cba8da202b98bd66b26a76697a0b',
    'prompt-v1.txt': 'bda24929520798c7cc95643f153d92071b6bb8dc0dc733f202d319068a9fba1f',
    'response-schema-v1.json': '449b9853115e100558e0d2e8727de7e7997cb72254880be1a095cbe55252e214',
}
TEMPLATE_SHA = '7f0e529032c25183bcd66c7f238da2d377f43be754a94e2725a58c4e16d2ed67'
CONTEXT, OUTPUT_TOKENS = 4096, 2048
FREEZE_SHA = 'd57916747df96297c5fe08c8f87118fac2ce67a6e9e04e782af049358324ee67'
AUDIT_SHA = '11352d7f17267c88608192de6c47ee6616b1f7bc9bc586f509e4a98ed44045cb'
PROFILE_SHA = '1645b90d63fe3cde2491f189a32cddaaaf371c011376094cf661d34464dd6c31'
MAPPING_CODE_SHA = '818d9aad2495e628a6fc3b2522042f925f414d0d9a39388366acf40a1f7c8cc5'
INSTALL_SHA = 'af531ddc76544daf8c68fbc19ff5181bb2ca1e084a526ada9e497768130fc595'
PROJECTED_INPUT_SHA = '828b3a5974ca3fd89fbb28a99e4dfbb8f0b5b23a4114a511b610795022e67cda'
RESOURCE_PROFILE = 'qwen35-cpu-8g-4threads-4k-prefix-v3'
MAPPING_IDENTITY = {'version': 'qwen-material-warning-policy-v2',
                    'policySha256': MAPPING_SHA, 'codeSha256': MAPPING_CODE_SHA}
MESSAGE_DELIMITERS = [{'role': role, 'delimiter': '<|im_start|>' + role + '\n'}
                      for role in ('system', 'user', 'assistant')]
STARTUP_CONFIGURATION = {'offline': True, 'agentEnabled': False, 'warmup': False,
    'repack': False, 'loadMode': 'mmap', 'cacheRamMiB': 0, 'logVerbosity': 4,
    'threads': 4, 'batchThreads': 4, 'processPriority': 'BelowNormal', 'nativePriority': -1,
    'poll': 0, 'pollBatch': 0, 'contextCheckpoints': 3, 'messageDelimiters': MESSAGE_DELIMITERS}
NATIVE_SAMPLING = {
    'temperature': .7, 'top_p': .8, 'top_k': 20, 'min_p': 0.,
    'presence_penalty': 1.5, 'frequency_penalty': 0., 'repeat_penalty': 1.,
    'repeat_last_n': CONTEXT, 'seed': 20260911,
    'samplers': ['penalties', 'top_k', 'top_p', 'temperature'],
    'n_predict': OUTPUT_TOKENS, 'ignore_eos': False, 'stop': [],
    'cache_prompt': True, 'stream': False, 'return_tokens': True, 'id_slot': 0,
    'dry_multiplier': 0., 'mirostat': 0, 'dynatemp_range': 0.,
    'typical_p': 1., 'xtc_probability': 0., 'top_n_sigma': -1.,
}
REPORTED_SAMPLING = tuple(k for k in NATIVE_SAMPLING
                          if k not in ('cache_prompt', 'stream', 'return_tokens', 'id_slot'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return parse_json(Path(path).read_text(encoding='utf-8'))


def read_lines(path):
    return [parse_json(line) for line in Path(path).read_text(encoding='utf-8').split('\n') if line.strip()]


def parse_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def reject(_value):
        raise ValueError('nonfinite_json_value')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=reject)


def same_json(actual, expected):
    # Python's ordinary equality conflates True with 1 and False with 0.
    return json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)


def load_mapping():
    path = ROOT / 'scripts/local-qe/material_warning.py'
    require(digest(path) == MAPPING_CODE_SHA and digest(path.with_name('qwen-material-warning-policy-v2.json'))
            == MAPPING_SHA, 'frozen_warning_mapping_changed')
    spec = importlib.util.spec_from_file_location('fixed_qwen_material_warning_v2', path)
    mapping = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mapping)
    require(same_json(mapping.identity(), MAPPING_IDENTITY), 'material_warning_identity_differs')
    return mapping


def verify_frozen_setup(summary):
    """Read pinned preparation evidence; never import a runtime or load weights."""
    base = ROOT / '.translation/qe/llm-candidates/qwen35-9b'
    freeze_path, audit_path = base / 'freeze-v4.json', base / 'token-budget-v1/attempt-001/summary.json'
    require(digest(freeze_path) == FREEZE_SHA, 'frozen_v4_manifest_changed')
    require(digest(audit_path) == AUDIT_SHA, 'frozen_token_budget_audit_changed')
    require(digest(ROOT / 'scripts/local-qe/llm-qwen35/resource-profile-v3.json') == PROFILE_SHA,
            'frozen_resource_profile_changed')
    require(digest(base / 'install-manifest.json') == INSTALL_SHA, 'frozen_install_manifest_changed')
    freeze, audit = read(freeze_path), read(audit_path)
    require(freeze['runtimeVersion'] == summary.get('version') == RUNTIME_VERSION
            and freeze['resourceProfile'] == summary.get('resourceProfile') == RESOURCE_PROFILE,
            'unsupported_frozen_runtime_or_profile')
    require(same_json(freeze['materialWarningMapping'], MAPPING_IDENTITY)
            and same_json(summary.get('materialWarningMapping'), MAPPING_IDENTITY), 'recorded_material_mapping_differs')
    require(same_json(summary.get('nativeStartupConfiguration'), STARTUP_CONFIGURATION),
            'recorded_startup_configuration_differs')
    require(summary.get('installation', {}).get('manifestSha256') == INSTALL_SHA, 'run_install_identity_differs')
    # The pinned freeze also lists tests/documentation that the native runner did
    # not import. Its remaining exact set is the runtime's source/interpreter set.
    non_runtime = {'test_runtime_v4.py', 'token_budget.py', 'test_token_budget.py',
                   'README_V4.md', 'CACHE_REUSE_REVIEW_V1.md'}
    run_hashes = {path: sha for path, sha in freeze['codeHashes'].items() if Path(path).name not in non_runtime}
    require(same_json(summary.get('codeHashes'), run_hashes), 'run_code_identity_differs_from_freeze')
    for path, expected in freeze['codeHashes'].items():
        require(digest(path) == expected, 'frozen_source_or_interpreter_changed')
    require(audit['status'] == 'completed' and audit['inputCount'] == 48 and audit['fitCount'] == 48
            and audit['allRowsFitStrict'] is True and audit['allFirst3TokenIdsEqual'] is True
            and audit['finalIntegrityVerified'] is True and audit['allChildrenStopped'] is True
            and audit['weightTensorsLoaded'] is False and type(audit['generationCalls']) is int
            and audit['generationCalls'] == 0 and audit['contextTokens'] == CONTEXT
            and audit['reservedOutputTokens'] == OUTPUT_TOKENS, 'token_budget_audit_not_passed')
    require(len(audit['templateParity']) == 3 and all(row['templateBytesEqual'] is True and row['tokenIdsEqual'] is True
            for row in audit['templateParity']), 'audited_template_parity_failed')
    require(audit['inputSha256'] == freeze['inputSha256'] == summary['inputSha256'] == PROJECTED_INPUT_SHA,
            'frozen_projected_input_identity_differs')
    for relative, expected in audit['artifacts'].items():
        artifact = (audit_path.parent / relative).resolve()
        require(artifact.is_relative_to(audit_path.parent) and digest(artifact) == expected,
                'frozen_token_budget_artifact_changed')
    budgets = {row['id']: row for row in audit['rows']}
    require(len(audit['rows']) == len(budgets) == 48, 'token_budget_row_inventory_differs')
    expected_preflight = {'path': str(audit_path), 'sha256': AUDIT_SHA, 'inputSha256': PROJECTED_INPUT_SHA,
        'templateSha256': audit['templateSha256'], 'rows': budgets, 'sharedPrefixAudit': audit['sharedPrefixAudit'],
        'contextTokens': CONTEXT, 'reservedOutputTokens': OUTPUT_TOKENS, 'mapping': MAPPING_IDENTITY}
    require(same_json(summary.get('tokenBudgetPreflight'), expected_preflight), 'recorded_token_preflight_differs')
    return {'freezePath': str(freeze_path), 'freezeSha256': FREEZE_SHA,
            'tokenBudgetAuditPath': str(audit_path), 'tokenBudgetAuditSha256': AUDIT_SHA,
            'profileSha256': PROFILE_SHA, 'budgetRows': budgets, 'modelWeightsRehashedByEvaluator': False}


def validate_cache_and_telemetry(record, response, input_tokens):
    timings = response.get('timings', {})
    cached, processed = timings.get('cache_n'), timings.get('prompt_n')
    require(type(cached) is int and type(processed) is int and 0 <= cached < input_tokens
            and processed > 0 and cached + processed == input_tokens, 'native_prompt_cache_accounting_differs')
    expected = {'reusedPromptTokens': cached, 'processedPromptTokens': processed,
        'promptMilliseconds': timings.get('prompt_ms'), 'predictedMilliseconds': timings.get('predicted_ms'),
        'nativeTimings': timings, 'slotTokensAfterResponse': response.get('tokens_cached'),
        'prefixReuseObserved': cached > 0, 'sameSeedGuaranteesSameOutput': False}
    require(same_json(record.get('cache'), expected), 'recorded_cache_observation_differs')
    telemetry = record.get('childTelemetry', {})
    require(type(telemetry.get('pid')) is int and telemetry['pid'] > 0
            and type(telemetry.get('creationTicks')) is int and telemetry['creationTicks'] > 0
            and type(telemetry.get('priorityClass')) is int and telemetry['priorityClass'] == 16384
            and telemetry.get('priorityName') == 'BelowNormal', 'recorded_child_telemetry_invalid')
    for field in ('kernelCpuSeconds', 'userCpuSeconds', 'totalCpuSeconds'):
        require(type(telemetry.get(field)) in (int, float) and math.isfinite(telemetry[field])
                and telemetry[field] >= 0, 'recorded_cpu_times_invalid')
    require(abs(telemetry['totalCpuSeconds'] - telemetry['kernelCpuSeconds'] - telemetry['userCpuSeconds']) < 1e-5,
            'recorded_cpu_times_inconsistent')
    return expected


def runtime_acceptance(summary):
    """Execution eligibility is independent of observed paragraph accuracy."""
    guard = summary.get('guard')
    checks = {
        'statusCompleted': summary.get('status') == 'completed',
        'modelLoaded': summary.get('modelLoaded') is True,
        'runtimeContractValidated': summary.get('runtimeContractValidated') is True,
        'exactly48GenerationRequests': type(summary.get('generationRequests')) is int
                                      and summary['generationRequests'] == 48,
        'guardClosedWithoutErrors': type(guard) is dict
                                   and all(key in guard and guard[key] is None for key in ('abortReason', 'killError')),
        'noFailureOrCleanupErrors': not any(key in summary for key in
            ('failure', 'guardCleanupError', 'cleanupError', 'integrityError', 'spawnCleanupUnconfirmed',
             'spawnCleanupCode', 'runLockPreservedBecauseChildStopUnconfirmed')),
        'finalIntegrityVerified': summary.get('finalIntegrityVerified') is True,
        'childProcessStopped': summary.get('childProcessStopped') is True,
    }
    return {'accepted': all(checks.values()), 'checks': checks,
            'failedChecks': [key for key, passed in checks.items() if not passed]}


def recorded_json(run, summary, name):
    require(Path(name).name == name and name in summary['artifacts'], 'unrecorded_native_evidence_' + name)
    require(digest(run / name) == summary['artifacts'][name], 'run_artifact_changed')
    return read(run / name)


def validate_native_evidence(run, summary, prefix, record, request, schema, frozen):
    """Pure checks of the supported v4 envelope; does not load/tokenize a model.

    Token IDs are compared to the recorded tokenizer reply and native request;
    their mapping is attested by the frozen runtime, not independently retokenized.
    The pinned Qwen template renders these two plain-text messages with trim.
    """
    require(summary.get('version') == RUNTIME_VERSION, 'unsupported_native_runtime')
    require(summary.get('ggufMetadata', {}).get('templateSha256') == TEMPLATE_SHA, 'unsupported_chat_template')
    require(summary.get('contextTokens') == CONTEXT and same_json(summary.get('sampling'), NATIVE_SAMPLING),
            'recorded_native_configuration_differs')
    require(same_json(summary.get('thinking'), {'enable_thinking': False, 'suffixMustBeVerifiedAtRuntime': True}),
            'recorded_thinking_configuration_differs')
    for name, expected in SUPPORTED_CODE_HASHES.items():
        path = ROOT / 'scripts/local-qe/llm-qwen35' / name
        require(summary['codeHashes'].get(str(path.resolve())) == expected, 'unsupported_recorded_code_' + name)
    template = recorded_json(run, summary, prefix + '-template.raw.json')
    prompt = template.get('prompt')
    expected_prompt = ''.join('<|im_start|>' + message['role'] + '\n' + message['content'].strip()
                              + '<|im_end|>\n' for message in request['messages'])
    expected_prompt += '<|im_start|>assistant\n<think>\n\n</think>\n\n'
    require(prompt == expected_prompt, 'rendered_prompt_differs_from_frozen_request')
    require(record.get('promptSha256') == hashlib.sha256(prompt.encode('utf-8')).hexdigest(), 'prompt_hash_differs')
    tokens = recorded_json(run, summary, prefix + '-tokenize.raw.json').get('tokens')
    require(type(tokens) is list and all(type(token) is int and token >= 0 for token in tokens), 'invalid_prompt_tokens')
    require(0 < len(tokens) and len(tokens) + OUTPUT_TOKENS < CONTEXT, 'prompt_output_context_budget')
    require(type(record.get('inputTokens')) is int and record['inputTokens'] == len(tokens), 'input_token_count_differs')
    native = recorded_json(run, summary, prefix + '-native-request.json')
    require(same_json(native, NATIVE_SAMPLING | {'prompt': tokens, 'json_schema': schema,
            'message_delimiters': MESSAGE_DELIMITERS}), 'native_request_differs')
    budget = frozen['budgetRows'].get(record['id'])
    require(type(budget) is dict and budget['fitsStrict'] is True and budget['inputTokens'] == len(tokens)
            and budget['promptSha256'] == record['promptSha256']
            and budget['tokenIdsSha256'] == hashlib.sha256(json.dumps(tokens, separators=(',', ':')).encode('utf-8')).hexdigest(),
            'native_prompt_differs_from_frozen_token_audit')
    require(all(prompt.count(delimiter['delimiter']) == 1 for delimiter in MESSAGE_DELIMITERS),
            'message_boundary_not_unique')
    raw_name = prefix + '-completion.raw.json'
    require(record.get('rawResponseFile') == raw_name, 'native_response_filename_differs')
    response = recorded_json(run, summary, raw_name)
    require(digest(run / raw_name) == record.get('rawResponseSha256'), 'raw_response_changed')
    require(response.get('stop_type') == 'eos' and response.get('truncated') is False, 'response_not_complete_eos')
    require(response.get('stopping_word', '') == '', 'unexpected_stop_word')
    require(response.get('prompt') == prompt, 'actual_prompt_differs')
    output, content = response.get('tokens'), response.get('content')
    require(type(content) is str and bool(content.strip()) and type(output) is list
            and 0 < len(output) < OUTPUT_TOKENS and all(type(token) is int and token >= 0 for token in output),
            'response_empty_or_output_limit')
    require(type(record.get('outputTokens')) is int and record['outputTokens'] == len(output), 'output_token_count_differs')
    require(not any(tag in content for tag in ('<think>', '</think>', '<|im_start|>', '<|im_end|>')),
            'response_control_or_thinking_leak')
    settings = response.get('generation_settings', {})
    require(type(settings) is dict, 'invalid_actual_sampling')
    for key in REPORTED_SAMPLING:
        actual, wanted = settings.get(key), NATIVE_SAMPLING[key]
        matches = (type(actual) in (int, float) and math.isfinite(actual) and abs(actual - wanted) < 1e-5
                   if type(wanted) is float else same_json(actual, wanted))
        require(matches, 'actual_sampling_differs_' + key)
    validate_cache_and_telemetry(record, response, len(tokens))
    initial = summary.get('initialChildTelemetry', {})
    require(record['childTelemetry']['pid'] == summary.get('ownedPid') == initial.get('pid')
            and record['childTelemetry']['creationTicks'] == initial.get('creationTicks'),
            'recorded_child_identity_differs')
    return content


@lru_cache(maxsize=512)
def one_sided_lower(successes, total):
    if not total:
        return None
    if not successes:
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(70):
        p = (lo + hi) / 2
        tail = sum(math.comb(total, k) * p ** k * (1 - p) ** (total - k)
                   for k in range(successes, total + 1))
        if tail < .05:
            lo = p
        else:
            hi = p
    return (lo + hi) / 2


def add_bounds(result):
    result['recallOneSided95LowerIfIid'] = one_sided_lower(result['truePositives'], result['materialErrorCount'])
    result['precisionOneSided95LowerIfIid'] = one_sided_lower(result['truePositives'], result['warnings'])


def summarize(rows):
    """binaryMetricValue encodes an action, never a model confidence score."""
    result = {}
    for name, field in [('primary', 'warning'), ('supplementaryMajorOnly', 'majorWarning')]:
        mapped = [{**row, 'score': 0 if row['completed'] and row[field] else 1} for row in rows]
        value = metrics(mapped, 0)
        slices = slice_gate(mapped, 0, PRODUCT_QE_POLICY)
        add_bounds(value)
        for item in slices['slices']:
            add_bounds(item)
        named = [row for row in mapped if row['namedError']]
        named_detected = sum(row['score'] == 0 for row in named)
        all_completed = bool(rows) and all(row['completed'] for row in rows)
        rate_passed = (value['recall'] is not None and value['recall'] >= .95
                       and value['precision'] is not None and value['precision'] >= .95)
        accepted = all_completed and rate_passed and slices['accepted'] and len(named) == named_detected == 6
        result[name] = {**value, 'namedErrorCount': len(named), 'namedErrorsDetected': named_detected,
                        'slices': slices, 'allRequestsCompleted': all_completed,
                        'developmentParagraphGatePassed': accepted,
                        'replacesPrimaryAcceptanceGate': name == 'primary'}
    return result


def analyze_run(run, input_path):
    run, input_path = run.resolve(), input_path.resolve()
    root = ROOT / '.translation/qe/llm-candidates/qwen35-9b'
    require(run.is_relative_to(root / 'runs'), 'unsupported_run_directory')
    require(digest(DATASET / 'inputs.jsonl') == INPUT_SHA and
            digest(DATASET / 'judgments.jsonl') == LABEL_SHA, 'frozen_dev48_changed')
    summary = read(run / 'summary.json')
    require(type(summary['expectedCount']) is int and summary['expectedCount'] == 48
            and summary['inputSha256'] == digest(input_path), 'not_full_dev48_input')
    require(summary.get('referencesOrJudgmentsIncluded') is False, 'input_provenance_invalid')
    frozen = verify_frozen_setup(summary)
    mapping = ROOT / 'scripts/local-qe/qwen-material-warning-policy-v2.json'
    require(digest(mapping) == MAPPING_SHA, 'frozen_warning_mapping_changed')
    mapper = load_mapping()
    for name, expected in summary['artifacts'].items():
        require(Path(name).name == name and digest(run / name) == expected, 'run_artifact_changed')
    inputs, labels, projected = read_lines(DATASET / 'inputs.jsonl'), read_lines(DATASET / 'judgments.jsonl'), read_lines(input_path)
    require(same_json(projected, [{k: row[k] for k in ('id', 'source', 'translation', 'context')} for row in inputs]),
            'projection_changed_or_reordered')
    label_map = {row['id']: row for row in labels}
    statuses = {row['id']: row['status'] for row in summary['itemStatuses']}
    expected_ids = {row['id'] for row in inputs}
    require(len(labels) == len(label_map) == len(summary['itemStatuses']) == len(statuses) == 48
            and set(label_map) == set(statuses) == expected_ids, 'missing_extra_duplicate_ids')
    require(all(status in ('completed', 'failed', 'not_run') for status in statuses.values()), 'unknown_item_status')
    require(set(frozen['budgetRows']) == expected_ids, 'audited_ids_differ_from_frozen_inputs')
    contract_path = ROOT / 'scripts/local-qe/llm-qwen35/contract.py'
    require(digest(contract_path) == SUPPORTED_CODE_HASHES['contract.py']
            and summary['codeHashes'].get(str(contract_path.resolve())) == digest(contract_path), 'run_contract_changed')
    spec = importlib.util.spec_from_file_location('qwen_review_contract', contract_path)
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    contract.contract_identity()
    requests = None
    if any(run.glob('*-assessment.json')):
        require('requests.jsonl' in summary['artifacts'], 'unrecorded_requests')
        requests = read_lines(run / 'requests.jsonl')
        require(same_json(requests, [{'id': row['id'], 'request': contract.build_request(row)} for row in projected]),
                'recorded_requests_differ_from_frozen_inputs')
    rows, diagnostics, cache_records, mapped_records = [], [], [], []
    for index, item in enumerate(inputs):
        label = label_map[item['id']]
        require(all(label[key] == item[key] for key in ('sourceSha256', 'translationSha256')), 'judgment_input_differs')
        require(type(label['materialError']) is bool and (label['namedError'] is None
                or type(label['namedError']) is str and bool(label['namedError'])), 'invalid_judgment_type')
        completed, warning, major, legacy_warning = False, False, False, None
        record_path = run / f'{index + 1:04d}-assessment.json'
        if record_path.exists():
            require(record_path.name in summary['artifacts'], 'unrecorded_assessment')
            record = read(record_path)
            require(record['id'] == item['id'], 'assessment_id_differs')
            content = validate_native_evidence(run, summary, f'{index + 1:04d}', record,
                                               requests[index]['request'], contract.load_schema(), frozen)
            validated = contract.validate_response(projected[index], content)
            require(same_json(validated, record['assessment']), 'saved_assessment_differs_from_raw_validation')
            remapped = mapper.classify(validated)
            require(same_json(record.get('materialWarningV2'), remapped), 'saved_material_mapping_differs_from_recomputed')
            completed = statuses[item['id']] == record['status'] == 'completed' and validated['status'] == 'valid'
            if completed:
                warning = remapped['primaryWarning']
                major = remapped['supplementaryMajorWarning']
                legacy_warning = validated['whole_paragraph_review']
                diagnostics.extend(validated['span_diagnostics'])
                cache_records.append({'id': item['id'], **record['cache']})
                mapped_records.append({'id': item['id'], **remapped})
        require(not (statuses[item['id']] == 'completed' and not completed), 'completed_row_missing_valid_evidence')
        rows.append({**item, **label, 'completed': completed, 'warning': warning, 'majorWarning': major,
                     'v1DiagnosticWholeParagraphReview': legacy_warning, 'requestStatus': statuses[item['id']]})
    result = summarize(rows)
    unique = sum(item['status'] == 'unique' for item in diagnostics)
    integrity = summary.get('finalIntegrityVerified') is True and summary.get('childProcessStopped') is True
    runtime = runtime_acceptance(summary)
    return {'version': VERSION, 'createdAt': datetime.now(timezone.utc).isoformat(),
            'warningMapping': 'valid_completed_response: major_or_critical_semantic_issue_or_uncertainty; failed_or_missing_is_not_detection',
            'productPolicyVersion': PRODUCT_QE_POLICY['version'], 'humanReviewed': False,
            'warningMappingSha256': MAPPING_SHA, 'nativeEvidenceValidationVersion': 'frozen-v4-envelope-material-warning-v2',
            'warningMappingVersion': MAPPING_IDENTITY['version'], 'warningMappingCodeSha256': MAPPING_CODE_SHA,
            'v1PolicyAndResultsReplaced': False, 'mappingRecomputedFromRawValidatedResponse': True,
            'confidenceIntervalAssumption': 'one-sided exact binomial 95%; independent identically distributed trials assumed, not established for shared-source assistant-labeled dev48; not a population guarantee or acceptance threshold',
            'independentFinalCertification': False, 'trainingPerformed': False, 'registrationPerformed': False,
            'evidence': {'runSummary': str(run / 'summary.json'), 'runSummarySha256': digest(run / 'summary.json'),
                         'inputSha256': INPUT_SHA, 'judgmentsSha256': LABEL_SHA,
                         'projectedInputSha256': digest(input_path), 'analysisCodeSha256': digest(__file__),
                         'freezeV4Sha256': frozen['freezeSha256'], 'tokenBudgetAuditSha256': frozen['tokenBudgetAuditSha256'],
                         'resourceProfileSha256': frozen['profileSha256'],
                         'analysisDependencySha256': {name: digest(Path(__file__).with_name(name))
                                                     for name in ('calibrate.py', 'common.py')},
                         'productPolicyContentSha256': hashlib.sha256(json.dumps(PRODUCT_QE_POLICY,
                             sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()},
            'runStatus': summary['status'], 'runIntegrityAndClosurePassed': integrity,
            'runtimeAcceptance': runtime,
            'nativeEvidence': {'supportedRuntimeVersion': RUNTIME_VERSION,
                               'supportedCodeSha256': SUPPORTED_CODE_HASHES, 'supportedTemplateSha256': TEMPLATE_SHA,
                               'frozenRequestProjectionVerified': requests is not None,
                               'tokenValidation': 'native token request and frozen vocab-only audit match exactly; actual template/prompt/counts/4096+2048 budget checked; no new retokenization',
                               'runtimeCodeImported': False},
            'requestCounts': {'expected': 48, 'completed': sum(row['completed'] for row in rows)}, **result,
            'developmentParagraphAccepted': runtime['accepted'] and result['primary']['developmentParagraphGatePassed'],
            'quoteLocalization': {'proposedNonemptyQuotes': len(diagnostics), 'uniquelyLocated': unique,
                                  'unverified': len(diagnostics) - unique,
                                  'uniqueRate': unique / len(diagnostics) if diagnostics else None,
                                  'modelSubmittedOffsets': False, 'offsetsDerivedByValidator': True,
                                  'scope': 'valid completed responses only; incomplete run cannot pass acceptance',
                                  'semanticSpanPrecision': 'not_evaluated'},
            'fullLearningReadinessAccepted': False,
            'cacheObservation': {'reusedPromptTokens': sum(row['reusedPromptTokens'] for row in cache_records),
                                 'processedPromptTokens': sum(row['processedPromptTokens'] for row in cache_records),
                                 'rows': cache_records, 'effectOnQualityInferredFromSpeed': False},
            'materialMappingEvidence': mapped_records,
            'unperformedGates': ['independent_source_grounded_review_of_error_reasons_and_red_spans', 'new_independent_holdout'],
            'rows': [{key: row[key] for key in ('id', 'translationSystem', 'split', 'materialError', 'namedError',
                                               'completed', 'warning', 'majorWarning', 'v1DiagnosticWholeParagraphReview', 'requestStatus')} for row in rows]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = analyze_run(args.run, args.input)
    require(args.output.resolve().is_relative_to(ROOT / '.training/quality-evaluation'), 'output_outside_evaluation')
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: result[key] for key in ('runStatus', 'requestCounts', 'developmentParagraphAccepted', 'fullLearningReadinessAccepted')}))
