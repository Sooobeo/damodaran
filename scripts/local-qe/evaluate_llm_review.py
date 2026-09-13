"""Read-only dev48 diagnostic for Qwen assertions; no inference or registration.

The primary action is the frozen validator's whole_paragraph_review flag,
including minor issues, uncertainty and failed quote localization. A failed
model request is not a successful warning. The major-only view is supplementary.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
VERSION = 'qwen35-dev48-warning-analysis-v1'
MAPPING_SHA = 'd652cee3cabb4d884d70c79d8aaff5d75a8475e541ce827849e2487036ed5fe8'
# This is deliberately a v1 warning analysis of one frozen native runtime.
# Never import a runtime supplied by a run directory or by summary.codeHashes.
RUNTIME_VERSION = 'qwen35-semantic-review-run-v3'
SUPPORTED_CODE_HASHES = {
    'runtime_v3.py': 'a253dd71f4f8d1b9524b0c736868c5a0a55d4ee373647bcbec5ab2de3e27210d',
    'contract.py': 'd4e6e8fd6e5d09729c1adf745468d383b217cba8da202b98bd66b26a76697a0b',
    'prompt-v1.txt': 'bda24929520798c7cc95643f153d92071b6bb8dc0dc733f202d319068a9fba1f',
    'response-schema-v1.json': '449b9853115e100558e0d2e8727de7e7997cb72254880be1a095cbe55252e214',
}
TEMPLATE_SHA = '7f0e529032c25183bcd66c7f238da2d377f43be754a94e2725a58c4e16d2ed67'
CONTEXT, OUTPUT_TOKENS = 8192, 2048
NATIVE_SAMPLING = {
    'temperature': .7, 'top_p': .8, 'top_k': 20, 'min_p': 0.,
    'presence_penalty': 1.5, 'frequency_penalty': 0., 'repeat_penalty': 1.,
    'repeat_last_n': CONTEXT, 'seed': 20260911,
    'samplers': ['penalties', 'top_k', 'top_p', 'temperature'],
    'n_predict': OUTPUT_TOKENS, 'ignore_eos': False, 'stop': [],
    'cache_prompt': False, 'stream': False, 'return_tokens': True, 'id_slot': 0,
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
    return json.loads(Path(path).read_text(encoding='utf-8'))


def read_lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').split('\n') if line.strip()]


def same_json(actual, expected):
    # Python's ordinary equality conflates True with 1 and False with 0.
    return json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)


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
            ('failure', 'guardCleanupError', 'cleanupError', 'integrityError', 'runLockPreservedBecauseChildStopUnconfirmed')),
        'finalIntegrityVerified': summary.get('finalIntegrityVerified') is True,
        'childProcessStopped': summary.get('childProcessStopped') is True,
    }
    return {'accepted': all(checks.values()), 'checks': checks,
            'failedChecks': [key for key, passed in checks.items() if not passed]}


def recorded_json(run, summary, name):
    require(Path(name).name == name and name in summary['artifacts'], 'unrecorded_native_evidence_' + name)
    require(digest(run / name) == summary['artifacts'][name], 'run_artifact_changed')
    return read(run / name)


def validate_native_evidence(run, summary, prefix, record, request, schema):
    """Pure checks of the supported v3 envelope; does not load/tokenize a model.

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
    require(same_json(native, NATIVE_SAMPLING | {'prompt': tokens, 'json_schema': schema}), 'native_request_differs')
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
    return content


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
    require(summary['expectedCount'] == 48 and summary['inputSha256'] == digest(input_path), 'not_full_dev48_input')
    require(summary.get('referencesOrJudgmentsIncluded') is False, 'input_provenance_invalid')
    mapping = ROOT / 'scripts/local-qe/llm-qwen35/evaluation-policy-v1.json'
    require(digest(mapping) == MAPPING_SHA, 'frozen_warning_mapping_changed')
    for name, expected in summary['artifacts'].items():
        require(Path(name).name == name and digest(run / name) == expected, 'run_artifact_changed')
    inputs, labels, projected = read_lines(DATASET / 'inputs.jsonl'), read_lines(DATASET / 'judgments.jsonl'), read_lines(input_path)
    require(projected == [{k: row[k] for k in ('id', 'source', 'translation', 'context')} for row in inputs], 'projection_changed_or_reordered')
    label_map = {row['id']: row for row in labels}
    statuses = {row['id']: row['status'] for row in summary['itemStatuses']}
    expected_ids = {row['id'] for row in inputs}
    require(len(labels) == len(label_map) == len(summary['itemStatuses']) == len(statuses) == 48
            and set(label_map) == set(statuses) == expected_ids, 'missing_extra_duplicate_ids')
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
    rows, diagnostics = [], []
    for index, item in enumerate(inputs):
        label = label_map[item['id']]
        require(all(label[key] == item[key] for key in ('sourceSha256', 'translationSha256')), 'judgment_input_differs')
        require(type(label['materialError']) is bool and (label['namedError'] is None
                or type(label['namedError']) is str and bool(label['namedError'])), 'invalid_judgment_type')
        completed, warning, major = False, False, False
        record_path = run / f'{index + 1:04d}-assessment.json'
        if record_path.exists():
            require(record_path.name in summary['artifacts'], 'unrecorded_assessment')
            record = read(record_path)
            require(record['id'] == item['id'], 'assessment_id_differs')
            content = validate_native_evidence(run, summary, f'{index + 1:04d}', record,
                                               requests[index]['request'], contract.load_schema())
            validated = contract.validate_response(projected[index], content)
            require(validated == record['assessment'], 'saved_assessment_differs_from_raw_validation')
            completed = statuses[item['id']] == record['status'] == 'completed' and validated['status'] == 'valid'
            if completed:
                warning = validated['whole_paragraph_review']
                major = any(issue['severity'] in ('major', 'critical') for issue in validated['assessment']['semantic_issues'])
                diagnostics.extend(validated['span_diagnostics'])
        require(not (statuses[item['id']] == 'completed' and not completed), 'completed_row_missing_valid_evidence')
        rows.append({**item, **label, 'completed': completed, 'warning': warning, 'majorWarning': major,
                     'requestStatus': statuses[item['id']]})
    result = summarize(rows)
    unique = sum(item['status'] == 'unique' for item in diagnostics)
    integrity = summary.get('finalIntegrityVerified') is True and summary.get('childProcessStopped') is True
    runtime = runtime_acceptance(summary)
    return {'version': VERSION, 'createdAt': datetime.now(timezone.utc).isoformat(),
            'warningMapping': 'valid_completed_response.whole_paragraph_review; failed_or_missing_is_not_detection',
            'productPolicyVersion': PRODUCT_QE_POLICY['version'], 'humanReviewed': False,
            'warningMappingSha256': MAPPING_SHA, 'nativeEvidenceValidationVersion': 'frozen-v3-envelope-v1',
            'confidenceIntervalAssumption': 'one-sided exact binomial 95%; independent identically distributed trials assumed, not established for shared-source assistant-labeled dev48; not a population guarantee or acceptance threshold',
            'independentFinalCertification': False, 'trainingPerformed': False, 'registrationPerformed': False,
            'evidence': {'runSummary': str(run / 'summary.json'), 'runSummarySha256': digest(run / 'summary.json'),
                         'inputSha256': INPUT_SHA, 'judgmentsSha256': LABEL_SHA,
                         'projectedInputSha256': digest(input_path), 'analysisCodeSha256': digest(__file__),
                         'analysisDependencySha256': {name: digest(Path(__file__).with_name(name))
                                                     for name in ('calibrate.py', 'common.py')},
                         'productPolicyContentSha256': hashlib.sha256(json.dumps(PRODUCT_QE_POLICY,
                             sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()},
            'runStatus': summary['status'], 'runIntegrityAndClosurePassed': integrity,
            'runtimeAcceptance': runtime,
            'nativeEvidence': {'supportedRuntimeVersion': RUNTIME_VERSION,
                               'supportedCodeSha256': SUPPORTED_CODE_HASHES, 'supportedTemplateSha256': TEMPLATE_SHA,
                               'frozenRequestProjectionVerified': requests is not None,
                               'tokenValidation': 'recorded tokenizer reply equals native request; counts and budget checked; no independent retokenization',
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
            'unperformedGates': ['independent_source_grounded_review_of_error_reasons_and_red_spans', 'new_independent_holdout'],
            'rows': [{key: row[key] for key in ('id', 'translationSystem', 'split', 'materialError', 'namedError',
                                               'completed', 'warning', 'majorWarning', 'requestStatus')} for row in rows]}


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
