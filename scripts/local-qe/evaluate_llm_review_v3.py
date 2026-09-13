"""Read-only, fixed dev8 evaluator for semantic runtime v5; never imports a runtime."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / '.translation/qe/llm-candidates/qwen35-9b'
CODE = ROOT / 'scripts/local-qe/llm-qwen35'
DATA = ROOT / '.training/quality-evaluation/dev48-v1'
INPUT = DEST / 'inputs/semantic-v2-dev8-v1.jsonl'
VERSION = 'qwen-semantic-dev8-evaluation-v3'
RUNTIME = 'qwen35-semantic-review-run-v5'
PROFILE = 'qwen35-cpu-6g-4threads-4k-dev8-v4'
BINDING = 'qwen-v5-dev8-execution-binding-v1'
INPUT_SHA = '55e52db41f16e35998eab1f3835f60aafc7e1a875a38b1d32a00dd5ec49e68ce'
BASE_INPUT_SHA = '054d7f41e7f47b40b7216d047adcc35a0ab741409d954554143d0284cca3d222'
LABEL_SHA = '5e466ac8aaec742c5db7b4ed3f163f63273a5a8facbc274b1776d402f7046b19'
INSTALL_SHA = 'af531ddc76544daf8c68fbc19ff5181bb2ca1e084a526ada9e497768130fc595'
TEMPLATE_SHA = '7f0e529032c25183bcd66c7f238da2d377f43be754a94e2725a58c4e16d2ed67'
GIB, CONTEXT, OUTPUT = 1024**3, 4096, 2048
PYTHON_BASE = Path('C:/Program Files/WindowsApps/PythonSoftwareFoundation.Python.3.11_3.11.2544.0_x64__qbz5n2kfra8p0')
PYTHONS = (ROOT / '.venv-training/Scripts/python.exe', PYTHON_BASE / 'python3.11.exe')
PINS = {
    'scripts/local-qe/llm-qwen35/contract_v2.py': '463561009a9eb966b97893d8447315730cf66b454332f7811c48a96b974b8378',
    'scripts/local-qe/llm-qwen35/contract.py': 'd4e6e8fd6e5d09729c1adf745468d383b217cba8da202b98bd66b26a76697a0b',
    'scripts/local-qe/llm-qwen35/prompt-semantic-v2.txt': '3227a46ec7f9f5d4669a783dc5938df3c3f57720be16dc993eb8dce2c97b5ae7',
    'scripts/local-qe/llm-qwen35/response-schema-v2.json': 'a6785ded870ca55af1408c97db558ecfb23bb074d81e81b2546b5b17b0419cd7',
    'scripts/local-qe/llm-qwen35/screen_gate_v1.py': 'a622f193b34403b87199dda0ea2017697f9249464c290e64ec7dbc23c32e283a',
    'scripts/local-qe/material_warning_v3.py': 'c1e8209c8ecc5e4c6ffdb3563c27bc2a6d1eb27de08b652fb6362a3e1bfc9cd7',
    'scripts/local-qe/qwen-material-warning-policy-v3.json': '964593f6d8af8c5b819871922eee82227e6a75ec22042f5f869e263c647929ba',
    'scripts/local-qe/llm-qwen35/prepare.py': '0189cb8f3dd5be5a8cb5b1c23ad18ac44475f723b93da38fb849474a89698af9',
    'scripts/local-hymt/process_owner.py': '9a00d809466e304359fb47c374c8fc2464ce31cdbc9557fbc9eee089bfe9d8b2',
    'scripts/model-comparison/working_set_limit.py': '4f8a45772a0f3d3f63274c7309bc7e673446eebb47a4a3e71dc558b95a4cbcdb',
    'scripts/model-comparison/suspended_process_owner.py': 'f01ca8a2fd5a75e501c343d2632b2f534b4e5f0397fda502449c69c5d363dfcd',
}
DELIMITERS = [{'role': role, 'delimiter': '<|im_start|>' + role + '\n'}
              for role in ('system', 'user', 'assistant')]
SAMPLING = dict(temperature=.7, top_p=.8, top_k=20, min_p=0., presence_penalty=1.5,
    frequency_penalty=0., repeat_penalty=1., repeat_last_n=4096, seed=20260911,
    samplers=['penalties', 'top_k', 'top_p', 'temperature'], n_predict=2048,
    ignore_eos=False, stop=[], cache_prompt=True, stream=False, return_tokens=True, id_slot=0,
    dry_multiplier=0., mirostat=0, dynatemp_range=0., typical_p=1., xtc_probability=0., top_n_sigma=-1.)
STARTUP = dict(offline=True, agentEnabled=False, warmup=False, repack=False, loadMode='mmap',
    cacheRamMiB=0, logVerbosity=4, threads=4, batchThreads=4, processPriority='BelowNormal',
    nativePriority=-1, poll=0, pollBatch=0, contextCheckpoints=3, messageDelimiters=DELIMITERS)


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def same(a, b):
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def reject(_value):
        raise ValueError('nonfinite_json')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)


def read(path):
    return parse(Path(path).read_bytes())


def lines(path):
    return [parse(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def safe(path, parent):
    path = Path(path)
    require(path.is_file() and path.resolve().is_relative_to(Path(parent).resolve())
            and not any(p.is_symlink() for p in (path, *path.parents)), 'redirected_or_missing_file')
    return path.resolve()


def verify_hashes(recorded, allowed):
    """Exact local allowlist; receipt data never selects executable imports."""
    expected = {str(Path(path).resolve()) for path in allowed}
    require(type(recorded) is dict and set(recorded) == expected, 'code_allowlist_differs')
    for name, expected_sha in recorded.items():
        path = safe(name, Path(name).parent)
        require(digest(path) == expected_sha, 'code_or_interpreter_changed')


def load_modules():
    for relative, expected in PINS.items():
        require(digest(safe(ROOT / relative, ROOT)) == expected, 'pinned_dependency_changed')
    modules = []
    for name, path in [('meaning_contract_v2', CODE / 'contract_v2.py'),
                       ('meaning_mapping_v3', ROOT / 'scripts/local-qe/material_warning_v3.py'),
                       ('meaning_screen_v1', CODE / 'screen_gate_v1.py')]:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return tuple(modules)


def artifact(folder, manifest, relative):
    require(type(relative) is str and not Path(relative).is_absolute(), 'invalid_artifact_name')
    path = safe(folder / relative, folder)
    require(relative in manifest and digest(path) == manifest[relative], 'artifact_hash_differs')
    return path


def inventory(folder, hashes, *, recursive=False, exclude=()):
    require(type(hashes) is dict, 'artifact_inventory_missing')
    paths = folder.rglob('*') if recursive else folder.iterdir()
    actual = {p.relative_to(folder).as_posix() for p in paths if p.is_file() and p.name not in exclude}
    require(actual == set(hashes), 'artifact_inventory_differs')
    for relative in hashes:
        artifact(folder, hashes, relative)


def rendered(request):
    return ''.join('<|im_start|>' + m['role'] + '\n' + m['content'].strip() + '<|im_end|>\n'
                   for m in request['messages']) + '<|im_start|>assistant\n<think>\n\n</think>\n\n'


def token_hash(tokens):
    return sha(json.dumps(tokens, separators=(',', ':')).encode('utf-8'))


def tokens_valid(tokens):
    return type(tokens) is list and bool(tokens) and all(type(t) is int and t >= 0 for t in tokens)


def fixed_inputs(input_path, gate):
    require(Path(input_path).resolve() == INPUT.resolve() and digest(input_path) == INPUT_SHA,
            'only_fixed_dev8_supported')
    require(digest(DATA / 'inputs.jsonl') == BASE_INPUT_SHA and digest(DATA / 'judgments.jsonl') == LABEL_SHA,
            'original_dev48_changed')
    identity = gate.identity()
    rows, base, labels = lines(input_path), lines(DATA / 'inputs.jsonl'), lines(DATA / 'judgments.jsonl')
    by_id, gt = {r['id']: r for r in base}, {r['id']: r for r in labels}
    require(len(base) == len(by_id) == len(labels) == len(gt) == 48
            and sum(r['materialError'] is True for r in labels) == 14, 'fixed_baseline_inventory_differs')
    require(len(rows) == 8 and [r['id'] for r in rows] == identity['inputIdsInOrder'], 'dev8_order_differs')
    for row in rows:
        original, label = by_id[row['id']], gt[row['id']]
        require(same(row, {k: original[k] for k in ('id', 'source', 'translation', 'context')}), 'model_input_leak_or_change')
        require(all(sha(row[k].encode('utf-8')) == original[k + 'Sha256'] for k in ('source', 'translation', 'context'))
                and all(label[k] == original[k] for k in ('sourceSha256', 'translationSha256'))
                and type(label['materialError']) is bool, 'source_or_judgment_binding_differs')
    return rows, [{**by_id[r['id']], **gt[r['id']]} for r in rows], identity


def verify_audit(audit_path, expected_sha, rows, contract, mapper):
    path = safe(audit_path, DEST / 'token-budget-v2')
    require(digest(path) == expected_sha, 'token_audit_sha_differs')
    audit = read(path)
    fixed = dict(version='qwen35-vocab-only-token-budget-v2', status='completed', inputCount=8,
        fitCount=8, generationCalls=0, weightTensorsLoaded=False, allRowsFitStrict=True,
        finalIntegrityVerified=True, allChildrenStopped=True, contextTokens=4096, reservedOutputTokens=2048,
        nativeTemplateParity='pending_actual_v5_apply_template', inputSha256=INPUT_SHA,
        contractIdentity=contract.contract_identity())
    require(all(same(audit.get(k), v) for k, v in fixed.items()) and 'templateParity' not in audit
            and 'allFirst3TokenIdsEqual' not in audit, 'unsupported_or_failed_v2_audit')
    require(Path(audit['inputPath']).resolve() == INPUT.resolve(), 'audit_input_path_differs')
    code_files = [CODE / name for name in ('token_budget_v2.py', 'test_token_budget_v2.py', 'contract_v2.py',
                  'contract.py', 'prepare.py', 'runtime_v5.py', 'prompt-semantic-v2.txt', 'response-schema-v2.json')]
    code_files += [ROOT / 'scripts/local-hymt/process_owner.py', *PYTHONS, PYTHON_BASE / 'python311.dll']
    for package in ('jinja2', 'markupsafe'):
        code_files += [p for p in (ROOT / '.venv-training/Lib/site-packages' / package).rglob('*')
                       if p.is_file() and p.suffix in ('.py', '.pyd')]
    verify_hashes(audit['codeAndInterpreter']['files'], code_files)
    inventory(path.parent, audit['artifacts'], recursive=True, exclude=('summary.json',))
    require(audit.get('nativeProcessCount') == 10 and audit.get('networkRequests') == 0
            and audit.get('priorPromptParityReused') is False and audit.get('vocabularyLoadedOnly') is True,
            'audit_native_scope_differs')
    source_sha = 'db0cd035294b91009250029a1435a1a688d72eb489c406b630d2293b30d6fb34'
    source = artifact(path.parent, audit['artifacts'], 'evidence/tokenize.cpp')
    require(digest(source) == source_sha == audit['officialSource']['sha256']
            and audit['officialSource']['vocabOnlyAssignmentVerified'] is True
            and audit['officialSource']['decodeOrSamplingCallPresent'] is False
            and audit['officialSource']['networkRequestPerformed'] is False, 'vocab_only_source_not_verified')
    receipts = [r for r in audit['artifacts'] if r.endswith('.process.json')]
    require(len(receipts) == 10, 'audit_process_inventory_differs')
    for relative in receipts:
        process = read(artifact(path.parent, audit['artifacts'], relative))
        require(process['childStopped'] is True and same(process['exitCode'], 0) and process['timedOut'] is False
                and 'failure' not in process and 'cleanupError' not in process
                and process['ownershipVerifiedBySpawn'] is True and same(process['atStart']['priorityClass'], 16384)
                and process['atStart']['pid'] == process['pid'] == process['atExit']['pid']
                and type(process['atStart']['createdFileTime100ns']) is int
                and process['atStart']['createdFileTime100ns'] > 0
                and process['atStart']['createdFileTime100ns'] == process['atExit']['createdFileTime100ns']
                and process['atExit']['exitedFileTime100ns'] > 0, 'audit_owned_process_invalid')
    require(digest(artifact(path.parent, audit['artifacts'], 'actual-chat-template.jinja')) == TEMPLATE_SHA
            == audit['templateSha256'], 'audit_template_differs')
    require([r['id'] for r in audit['rows']] == [r['id'] for r in rows], 'audit_row_order_differs')
    budgets = {}
    for row, item in zip(rows, audit['rows']):
        ids = read(artifact(path.parent, audit['artifacts'], item['rawTokenIdsFile']))
        prompt = artifact(path.parent, audit['artifacts'], item['rawPromptStdinFile']).read_bytes()
        require(tokens_valid(ids) and len(ids) + OUTPUT < CONTEXT and item['inputTokens'] == len(ids)
                and item['fitsStrict'] is True and token_hash(ids) == item['tokenIdsSha256']
                and sha(prompt) == item['promptSha256'] and prompt == rendered(contract.build_request(row)).encode('utf-8'),
                'audit_prompt_or_tokens_differs')
        process = read(artifact(path.parent, audit['artifacts'], item['processEvidenceFile']))
        require(process['childStopped'] is True and process['exitCode'] == 0 and process['timedOut'] is False
                and not process.get('failure') and process['pid'] == item['childPid']
                and process['ownershipVerifiedBySpawn'] is True and process['atStart']['priorityClass'] == 16384
                and type(process['atStart']['createdFileTime100ns']) is int
                and process['atStart']['createdFileTime100ns'] > 0
                and process['atStart']['createdFileTime100ns'] == process['atExit']['createdFileTime100ns']
                and process['atExit']['exitedFileTime100ns'] > 0, 'audit_owned_tokenizer_not_verified')
        budgets[row['id']] = item
    preflight = dict(path=str(path), sha256=expected_sha, inputSha256=INPUT_SHA, templateSha256=TEMPLATE_SHA,
        rows=budgets, sharedPrefixAudit=audit['sharedPrefixAudit'], contextTokens=CONTEXT,
        nativeTemplateParity='pending_actual_v5_apply_template', reservedOutputTokens=OUTPUT, mapping=mapper.identity())
    return preflight


def verify_installation_stats(installation, runtime_files, model, manifest):
    # The producer records the manifest alongside the native files and model.
    # It remains part of the integrity boundary even though it is not a binary.
    expected = {*runtime_files, str(model), str(manifest)}
    require(set(installation['statIdentities']) == expected, 'installation_inventory_differs')
    for name, value in installation['statIdentities'].items():
        st = safe(name, Path(name).parent).stat()
        require(same(value, [st.st_size, st.st_mtime_ns, st.st_ino]), 'installation_stat_differs')


def verify_setup(summary, binding_path, rows, contract, mapper, gate_identity):
    binding_path = safe(binding_path, ROOT / '.training/verifications')
    binding = read(binding_path)
    require(binding.get('version') == BINDING and binding.get('rootReviewed') is True, 'root_review_binding_required')
    freeze_path = safe(ROOT / binding['freezePath'], DEST)
    require(freeze_path == (DEST / 'freeze-v5-dev8.json').resolve()
            and digest(freeze_path) == binding['freezeSha256'], 'freeze_binding_differs')
    freeze, profile = read(freeze_path), read(CODE / 'resource-profile-v4.json')
    allowed = [ROOT / p for p in PINS] + [CODE / 'runtime_v5.py', CODE / 'resource-profile-v4.json', *PYTHONS]
    allowed += [ROOT / record['path'] for record in gate_identity['files'].values()]
    verify_hashes(freeze['codeHashes'], allowed)
    expected = dict(version='qwen-v5-dev8-freeze-v1', runtimeVersion=RUNTIME, inputSha256=INPUT_SHA,
        resourceProfileSha256=digest(CODE / 'resource-profile-v4.json'),
        tokenBudgetAuditSha256=profile['tokenBudgetAudit']['sha256'], materialWarningMapping=mapper.identity(),
        screenGate=gate_identity, evaluationAdapter={'path': 'scripts/local-qe/evaluate_llm_review_v3.py',
                                                   'sha256': digest(__file__)})
    require(all(same(freeze.get(k), v) for k, v in expected.items()), 'freeze_contract_differs')
    receipt = dict(version=BINDING, path=str(binding_path), sha256=digest(binding_path), freezePath=str(freeze_path),
        freezeSha256=digest(freeze_path), rootReviewed=True,
        files={str(p.resolve()): digest(p) for p in (binding_path, freeze_path, Path(__file__))})
    require(same(summary.get('executionBinding'), receipt) and same(summary.get('codeHashes'), freeze['codeHashes']),
            'run_identity_differs_from_binding')
    fixed = dict(version=PROFILE, contextTokens=4096, outputReservedTokens=2048, threads=4, batchThreads=4,
        batchSize=128, physicalBatchSize=128, parallelSlots=1, contextCheckpoints=3, cachePrompt=True, cacheRamMiB=0,
        messageDelimiters=DELIMITERS, priorityClass='BelowNormal', nativePriority=-1, poll=0, pollBatch=0,
        maximumChildWorkingSetBytes=6*GIB, maximumChildPrivateBytes=6*GIB,
        minimumStartAvailablePhysicalBytes=9*GIB, minimumStartAvailableCommitBytes=9*GIB,
        minimumRunningSystemHeadroomBytes=GIB, executionBinding='required_external_receipt', expectedCount=8,
        inputSha256=INPUT_SHA, contractVersion='qwen35-meaning-v2')
    require(all(same(profile.get(k), v) for k, v in fixed.items()), 'unsupported_resource_profile')
    preflight = verify_audit(ROOT / profile['tokenBudgetAudit']['path'], expected['tokenBudgetAuditSha256'], rows, contract, mapper)
    require(same(summary.get('tokenBudgetPreflight'), preflight), 'recorded_preflight_differs')
    require(digest(DEST / 'install-manifest.json') == INSTALL_SHA
            and summary['installation']['manifestSha256'] == INSTALL_SHA, 'installation_manifest_differs')
    install = read(DEST / 'install-manifest.json')
    runtime_files = {str((ROOT / r['path']).resolve()): r['sha256'] for r in install['runtime']['files']}
    verify_hashes(runtime_files, [Path(p) for p in runtime_files])
    audit = read(ROOT / profile['tokenBudgetAudit']['path'])
    require(same(audit['runtimeFiles'], runtime_files)
            and audit['runtimeArchiveSha256'] == install['runtime']['archiveSha256'], 'audited_native_runtime_differs')
    model = DEST / install['model']['file']
    verify_installation_stats(summary['installation'], runtime_files, model, DEST / 'install-manifest.json')
    return {'binding': receipt, 'budgetRows': preflight['rows'], 'profileSha256': expected['resourceProfileSha256'],
            'tokenBudgetAuditSha256': expected['tokenBudgetAuditSha256'], 'modelWeightsRehashedByEvaluator': False}


def telemetry(value, initial=None):
    require(type(value.get('pid')) is int and value['pid'] > 0 and type(value.get('creationTicks')) is int
            and value['creationTicks'] > 0 and same(value.get('priorityClass'), 16384)
            and value.get('priorityName') == 'BelowNormal', 'owned_identity_or_priority_invalid')
    for key in ('kernelCpuSeconds', 'userCpuSeconds', 'totalCpuSeconds'):
        require(type(value.get(key)) in (int, float) and math.isfinite(value[key]) and value[key] >= 0, 'cpu_time_invalid')
    require(abs(value['totalCpuSeconds'] - value['kernelCpuSeconds'] - value['userCpuSeconds']) < 1e-5, 'cpu_sum_differs')
    if initial:
        require(all(value[k] == initial[k] for k in ('pid', 'creationTicks'))
                and value['totalCpuSeconds'] >= initial['totalCpuSeconds'], 'owned_identity_or_cpu_regressed')


def native_row(run, summary, index, row, record, contract, frozen):
    prefix = f'{index:04d}'
    get = lambda suffix: read(artifact(run, summary['artifacts'], prefix + suffix))
    prompt = get('-template.raw.json')['prompt']
    require(prompt == rendered(contract.build_request(row))
            and sha(prompt.encode('utf-8')) == record['promptSha256'], 'actual_template_prompt_differs')
    require(all(prompt.count(d['delimiter']) == 1 for d in DELIMITERS), 'message_boundary_not_unique')
    tokens = get('-tokenize.raw.json')['tokens']
    require(tokens_valid(tokens) and 0 < len(tokens) + OUTPUT < CONTEXT
            and same(record['inputTokens'], len(tokens)), 'input_token_budget_differs')
    budget = frozen['budgetRows'][row['id']]
    require(budget['inputTokens'] == len(tokens) and budget['fitsStrict'] is True
            and budget['promptSha256'] == record['promptSha256'] and budget['tokenIdsSha256'] == token_hash(tokens),
            'native_parity_with_audit_failed')
    require(same(get('-native-request.json'), SAMPLING | {'prompt': tokens,
                 'json_schema': contract.load_schema(), 'message_delimiters': DELIMITERS}), 'native_request_differs')
    name = prefix + '-completion.raw.json'
    response = get('-completion.raw.json')
    require(record['rawResponseFile'] == name and record['rawResponseSha256'] == digest(run / name), 'raw_response_sha_differs')
    receipt = get('-completion-receipt.json')
    require(receipt['id'] == row['id'] and receipt['rawResponseFile'] == name
            and receipt['rawResponseSha256'] == digest(run / name)
            and receipt['lastDeadlineExceededAtEnd'] is False, 'completion_deadline_or_receipt_differs')
    require(response.get('stop_type') == 'eos' and response.get('truncated') is False
            and response.get('stopping_word', '') == '' and response.get('prompt') == prompt, 'not_complete_native_eos')
    content, output = response.get('content'), response.get('tokens')
    require(type(content) is str and bool(content.strip()) and tokens_valid(output) and len(output) < OUTPUT
            and same(record['outputTokens'], len(output))
            and not any(s in content for s in ('<think>', '</think>', '<|im_start|>', '<|im_end|>')), 'invalid_or_truncated_output')
    for key, wanted in SAMPLING.items():
        if key in ('cache_prompt', 'stream', 'return_tokens', 'id_slot'):
            continue
        actual = response.get('generation_settings', {}).get(key)
        equal = (type(actual) in (int, float) and math.isfinite(actual) and abs(actual-wanted) < 1e-5
                 if type(wanted) is float else same(actual, wanted))
        require(equal, 'actual_sampling_differs_' + key)
    timing = response.get('timings', {})
    cached, processed = timing.get('cache_n'), timing.get('prompt_n')
    require(type(cached) is int and type(processed) is int and 0 <= cached < len(tokens)
            and processed > 0 and cached + processed == len(tokens), 'cache_token_accounting_differs')
    for key in ('prompt_ms', 'predicted_ms'):
        require(type(timing.get(key)) in (int, float) and math.isfinite(timing[key]) and timing[key] >= 0, 'cache_time_invalid')
    cache = dict(reusedPromptTokens=cached, processedPromptTokens=processed, promptMilliseconds=timing['prompt_ms'],
        predictedMilliseconds=timing['predicted_ms'], nativeTimings=timing, slotTokensAfterResponse=response.get('tokens_cached'),
        prefixReuseObserved=cached > 0, sameSeedGuaranteesSameOutput=False)
    require(same(record['cache'], cache), 'saved_cache_differs')
    telemetry(record['childTelemetry'], summary['initialChildTelemetry'])
    return content


def resource_evidence(run, summary, ids):
    """Validate recorded ownership/guard evidence, without touching any process."""
    if not summary.get('ownedPid'):
        require(summary.get('generationRequests') == 0, 'generation_without_owned_process')
        return {'passed': False, 'samples': 0, 'reason': 'native_not_started'}
    initial, spawn, ws = summary['initialChildTelemetry'], summary['spawn'], summary['workingSet']
    telemetry(initial)
    require(initial['pid'] == summary['ownedPid'] == spawn['pid'] == ws['pid']
            and ws['creationTicks'] == initial['creationTicks'] and same(spawn['workingSetLimit'], ws), 'spawn_identity_differs')
    require(spawn['version'] == 'comparison-suspended-owned-spawn-v1'
            and all(spawn.get(k) is True for k in ('atomicJobAssignment', 'createSuspended', 'limitAppliedBeforeResume'))
            and same(spawn['resumePreviousCount'], 1) and ws['after']['hardMaximumEnabled'] is True
            and ws['after']['maximumBytes'] == 6*GIB - 64*1024**2, 'owned_limit_not_applied_before_resume')
    pre = summary['memoryPreflight']
    require(pre['physicalPassed'] is True and pre['commitPassed'] is True
            and pre['observed']['availablePhysical'] >= 9*GIB and pre['observed']['availableCommit'] >= 9*GIB
            and pre['maximumChildWorkingSetBytes'] == pre['maximumChildPrivateBytes'] == 6*GIB
            and pre['minimumAvailablePhysicalBytes'] == pre['minimumAvailableCommitBytes'] == 9*GIB, 'start_memory_preflight_invalid')
    events = lines(artifact(run, summary['artifacts'], 'memory.jsonl'))
    require(bool(events), 'missing_guard_samples')
    clean, peak_ws, peak_private, previous_cpu, previous_time = True, 0, 0, initial['totalCpuSeconds'], None
    for event in events:
        child, system = event['child'], event['system']
        telemetry(child, initial)
        stamp = datetime.fromisoformat(event['at'])
        require(previous_time is None or stamp >= previous_time, 'memory_time_regressed')
        previous_time = stamp
        require(child['totalCpuSeconds'] >= previous_cpu, 'memory_cpu_regressed')
        previous_cpu = child['totalCpuSeconds']
        require(event['phase'] in ('startup', 'request', 'between_rows')
                and type(event['heartbeatGapSeconds']) in (int, float) and event['heartbeatGapSeconds'] >= 0
                and event['actualGeneratedTokenProgress'] == 'unknown_nonstreaming', 'guard_phase_invalid')
        if event['phase'] == 'request':
            require(event['rowId'] in ids and type(event['requestElapsedSeconds']) in (int, float)
                    and event['requestElapsedSeconds'] >= 0 and bool(event['requestStartUTC']), 'request_heartbeat_invalid')
        else:
            require(event['rowId'] is None and event['requestElapsedSeconds'] is None, 'idle_heartbeat_has_request')
        peak_ws = max(peak_ws, child['workingSetBytes'], child['peakWorkingSetBytes'])
        peak_private = max(peak_private, child['privateBytes'])
        violation = (max(child['workingSetBytes'], child['peakWorkingSetBytes'], child['privateBytes']) > 6*GIB
                     or min(system['availablePhysical'], system['availableCommit']) < GIB
                     or event['phase'] == 'request' and event['requestElapsedSeconds'] >= 600)
        clean = clean and not violation and event['abortReason'] is None
    guard = summary.get('guard', {})
    clean = clean and same(guard, dict(abortReason=None, killError=None, lastDeadlineExceededAtEnd=False))
    return dict(passed=clean, samples=len(events), peakWorkingSetBytes=peak_ws, peakPrivateBytes=peak_private,
                lastObservedCpuSeconds=previous_cpu, elapsedHeartbeatMaximum=max(e['heartbeatGapSeconds'] for e in events))


def metric(rows):
    tp = sum(r['completed'] and r['warning'] is True and r['materialError'] for r in rows)
    fp = sum(r['completed'] and r['warning'] is True and not r['materialError'] for r in rows)
    positive = sum(r['materialError'] for r in rows)
    return dict(count=len(rows), completed=sum(r['completed'] for r in rows), materialErrorCount=positive,
        truePositives=tp, falsePositives=fp, falseNegatives=positive-tp,
        recall=tp/positive if positive else None, precision=tp/(tp+fp) if tp+fp else None,
        incompleteCount=sum(not r['completed'] for r in rows), zeroDenominator='not_evaluated')


def analyze_run(run, input_path, binding_path):
    run = Path(run).resolve()
    safe(run / 'summary.json', DEST / 'runs')
    contract, mapper, gate = load_modules()
    rows, labels, gate_id = fixed_inputs(input_path, gate)
    summary = read(run / 'summary.json')
    fixed = dict(version=RUNTIME, expectedCount=8, inputSha256=INPUT_SHA, resourceProfile=PROFILE,
        referencesOrJudgmentsIncluded=False, inputFields=['source', 'translation', 'context'], contextTokens=CONTEXT,
        sampling=SAMPLING, nativeStartupConfiguration=STARTUP, materialWarningMapping=mapper.identity(), screenGate=gate_id,
        thinking={'enable_thinking': False, 'suffixMustBeVerifiedAtRuntime': True},
        timeLimitsSeconds={'startup': 600, 'request': 600, 'total': 14400}, responseByteLimit=2*1024**2,
        fullBaselineAccepted=False, allEightCorrectIsAcceptance=False, automaticRetries=False,
        independentHoldout=False, developmentDiagnosisOnly=True, semanticSpanPrecision='not_evaluated')
    require(all(same(summary.get(k), v) for k, v in fixed.items()), 'unsupported_v5_dev8_summary')
    require(summary['status'] in ('completed', 'stopped_futility', 'failed'), 'unsupported_run_status')
    frozen = verify_setup(summary, binding_path, rows, contract, mapper, gate_id)
    inventory(run, summary['artifacts'], exclude=('summary.json',))
    get = lambda name: read(artifact(run, summary['artifacts'], name))
    require(same(lines(artifact(run, summary['artifacts'], 'requests.jsonl')),
                 [{'id': r['id'], 'request': contract.build_request(r)} for r in rows]), 'request_projection_differs')
    ids = [r['id'] for r in rows]
    statuses = summary['itemStatuses']
    require([r['id'] for r in statuses] == ids and all(r['status'] in ('completed', 'failed', 'not_run') for r in statuses),
            'item_status_inventory_differs')
    count = sum(r['status'] == 'completed' for r in statuses)
    require(same(summary['completedCount'], count) and all(r['status'] == 'completed' for r in statuses[:count])
            and sum(r['status'] == 'failed' for r in statuses) <= 1
            and same(summary['unexecutedIds'], [r['id'] for r in statuses if r['status'] == 'not_run']), 'status_order_or_count_differs')
    generated = summary['generationRequests']
    require(type(generated) is int and count <= generated <= min(count+1, 8), 'generation_count_invalid')
    for name in summary['artifacts']:
        match = re.match(r'^(\d{4})-', name)
        if match:
            index = int(match[1])
            require(1 <= index <= min(generated+1, 8), 'artifact_after_last_attempt')
            if name.endswith(('-completion.raw.json', '-completion-receipt.json', '-assessment.json', '-gate.json')):
                require(index <= generated, 'response_or_assessment_without_generation')
    native_indices = {int(n[:4]) for n in summary['artifacts'] if re.fullmatch(r'\d{4}-native-request.json', n)}
    require(native_indices in (set(range(1, generated+1)), set(range(1, min(generated+1, 8)+1))), 'native_generation_inventory_differs')
    if generated:
        claim = summary['diagnosticClaim']
        claim_path = safe(claim['path'], DEST)
        require(claim_path == (DEST / '.dev8-v5-generation.claim.json').resolve() and digest(claim_path) == claim['sha256'], 'diagnostic_claim_differs')
        claimed = read(claim_path)
        require(claimed['version'] == RUNTIME and Path(claimed['output']).resolve() == run
                and claimed['inputSha256'] == INPUT_SHA and claimed['executionBindingSha256'] == frozen['binding']['sha256']
                and claimed['singleDiagnosticAttemptOnly'] is True and claimed['automaticRetries'] is False, 'diagnostic_claim_binding_differs')
        require(summary['modelLoaded'] is True and summary['runtimeContractValidated'] is True
                and summary['ggufMetadata']['templateSha256'] == TEMPLATE_SHA
                and summary['runtimeEogTokens']['248046'] == '<|im_end|>', 'native_runtime_not_validated')
        props = get('runtime-props.raw.json')
        require(sha(props['chat_template'].encode('utf-8')) == TEMPLATE_SHA and props['total_slots'] == 1
                and props['default_generation_settings']['n_ctx'] == CONTEXT
                and Path(props['model_path']).resolve() == (DEST / 'Qwen3.5-9B-Q4_K_M.gguf').resolve(), 'native_props_differs')
        log = artifact(run, summary['artifacts'], 'runtime.log').read_text(encoding='utf-8', errors='replace')
        require('n_threads = 4 (n_threads_batch = 4)' in log and 'context checkpoints enabled, max = 3' in log
                and re.search(r'n_ctx\s+= 4096\b', log) and 'printing all EOG tokens:' in log, 'native_log_configuration_missing')
        eog = {key: value for key, value in re.findall(r"^.*?:\s+-\s+(\d+)\s+\('([^']*)'\)\s*$",
              log.split('printing all EOG tokens:')[-1], re.MULTILINE)}
        require(same(eog, summary['runtimeEogTokens']), 'logged_eog_tokens_differs')
    evidence, decisions, diagnostics = [], [], []
    for index, (row, label, state) in enumerate(zip(rows, labels, statuses), 1):
        entry = {k: label[k] for k in ('id', 'translationSystem', 'split', 'materialError', 'namedError')}
        entry.update(completed=False, warning=None, majorWarning=None, requestStatus=state['status'])
        name = f'{index:04d}-assessment.json'
        if name in summary['artifacts']:
            record = get(name)
            require(record['id'] == row['id'] and record['humanReviewed'] is False
                    and record['semanticQualityCertified'] is False, 'assessment_identity_or_claim_differs')
            raw = native_row(run, summary, index, row, record, contract, frozen)
            validated = contract.validate_response(row, raw)
            mapping = mapper.classify(validated)
            require(same(validated, record['assessment']) and same(mapping, record['materialWarningV3']), 'raw_contract_or_mapping_differs')
            if state['status'] == 'completed':
                require(record['status'] == 'completed' and validated['status'] == 'valid', 'completed_invalid_assessment')
                decision = gate.check(row['id'], mapping) | dict(index=index, assessmentFile=name,
                    assessmentSha256=digest(run/name), rawResponseFile=record['rawResponseFile'], rawResponseSha256=record['rawResponseSha256'])
                require(same(get(f'{index:04d}-gate.json'), decision), 'saved_gate_differs_from_recomputed')
                decisions.append(decision)
                diagnostics += validated['span_diagnostics']
                entry.update(completed=True, warning=mapping['primaryWarning'], majorWarning=mapping['supplementaryMajorWarning'],
                             materialMapping=mapping, cache=record['cache'])
        require(state['status'] != 'completed' or entry['completed'], 'completed_evidence_missing')
        evidence.append(entry)
    require(same(summary['screenDecisions'], decisions), 'summary_screen_decisions_differs')
    mismatch = [d for d in decisions if d['continueRun'] is False]
    if mismatch:
        stop = mismatch[0]
        require(len(mismatch) == 1 and stop['index'] == count == generated and native_indices == set(range(1, count+1))
                and same(summary.get('futilityStop'), stop) and all(r['status'] == 'not_run' for r in statuses[count:]),
                'generation_or_assessment_after_first_mismatch')
        require(all(not re.match(r'^\d{4}-', name) or int(name[:4]) <= count for name in summary['artifacts']),
                'native_preparation_after_first_mismatch')
        require(summary['status'] in ('stopped_futility', 'failed'), 'futility_marked_completed')
    else:
        require('futilityStop' not in summary and summary['status'] != 'stopped_futility', 'unjustified_futility_stop')
    if summary['status'] == 'completed':
        require(count == generated == 8 and not mismatch, 'incomplete_diagnostic_marked_completed')
    if summary['status'] != 'failed':
        require(generated == count and native_indices == set(range(1, count+1)), 'unfinished_generation_on_success')
    parity = summary.get('nativeTemplateParity')
    if count:
        require(type(parity) is dict and parity['status'] == 'verified'
                and parity['firstRequestVerifiedBeforeGeneration'] is True
                and same(parity['rowsVerified'], len(native_indices)), 'actual_native_template_parity_missing')
    resource = resource_evidence(run, summary, ids)
    closure = summary.get('finalIntegrityVerified') is True and summary.get('childProcessStopped') is True
    failure_fields = ('failure', 'guardCleanupError', 'cleanupError', 'integrityError', 'preStopIntegrityError',
                      'spawnCleanupUnconfirmed', 'runLockPreservedBecauseChildStopUnconfirmed')
    operational = summary['status'] != 'failed' and closure and resource['passed'] and not any(k in summary for k in failure_fields)
    require(summary['status'] == 'failed' or operational, 'success_without_clean_integrity_guard_and_cleanup')
    slices = []
    for field in ('translationSystem', 'split'):
        for value in sorted({r[field] for r in evidence}):
            slices.append(dict(scope=field, value=value, **metric([r for r in evidence if r[field] == value])))
    for model, split in sorted({(r['translationSystem'], r['split']) for r in evidence}):
        slices.append(dict(scope='translationSystem+split', value=[model, split],
                           **metric([r for r in evidence if (r['translationSystem'], r['split']) == (model, split)])))
    return dict(version=VERSION, createdAt=datetime.now(timezone.utc).isoformat(), runStatus=summary['status'],
        evidence=dict(runSummarySha256=digest(run/'summary.json'), inputSha256=INPUT_SHA, baselineInputSha256=BASE_INPUT_SHA,
                      judgmentsSha256=LABEL_SHA, evaluatorSha256=digest(__file__), **frozen),
        runtimeCodeImported=False, modelWeightsLoadedByEvaluator=False, nativeCallsByEvaluator=0,
        rawContractAndMappingRecomputed=True, screenDecisionsRecomputed=decisions,
        diagnosticOperationalClosurePassed=operational, partialCompletedEvidenceCount=count,
        diagnosticAll8Matched=count == 8 and not mismatch,
        fullBaselineAccepted=False, fullLearningReadinessAccepted=False, developmentParagraphAccepted=False,
        independentFinalCertification=False, trainingPerformed=False, registrationPerformed=False,
        semanticSpanPrecision='not_evaluated', resourceEvidence=resource,
        diagnosticMetrics=metric(evidence), diagnosticSlices=slices, rows=evidence,
        conditionalFutility=mismatch[0] if mismatch else None,
        baselineRequirements=dict(count=48, materialErrors=14, minimumRecall=.95, minimumPrecision=.95,
            eachModelSplitAndCrossSliceRequired=True, mandatorySentinels=6, status='not_evaluated_by_dev8', automaticMerge=False),
        quoteLocalization=dict(proposed=len(diagnostics), unique=sum(d['status'] == 'unique' for d in diagnostics),
            semanticPrecision='not_evaluated', offsetsSubmittedByModel=False),
        limitations=['exposed_fixed_development_selection_not_independent_test', 'four_errors_four_normals',
            'eight_unique_source_hashes_do_not_remove_near_pair_dependence', 'all_normal_items_are_hy7_selection_bias',
            'missing_or_failed_positive_is_not_detection', 'futility_bounds_require_retaining_observed_output',
            'prompt_and_memory_cap_changed_together_no_separate_causal_effect', 'no_automatic_full48_merge_or_adoption'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True, help='Existing v5 diagnostic run directory; no generation')
    parser.add_argument('--input', type=Path, default=INPUT)
    parser.add_argument('--execution-binding', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New evaluation JSON file')
    args = parser.parse_args()
    require(not args.output.exists(), 'output_already_exists')
    result = analyze_run(args.run, args.input, args.execution_binding)
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'version': VERSION, 'runStatus': result['runStatus'],
                      'diagnosticAll8Matched': result['diagnosticAll8Matched'], 'fullBaselineAccepted': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
