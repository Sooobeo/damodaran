"""Synthetic scoring boundaries only; no model or application registration."""
import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import evaluate_llm_review_v2 as evaluator
from evaluate_llm_review_v2 import one_sided_lower, summarize


def rows():
    return [{'id': str(i), 'translationSystem': system, 'split': split,
             'materialError': True, 'namedError': str(i) if i < 6 else None,
             'completed': True, 'warning': True, 'majorWarning': True}
            for i, (system, split) in enumerate([('a', 'dev'), ('a', 'read'), ('b', 'dev'), ('b', 'read')] * 2)]


class ScoringTests(unittest.TestCase):
    def test_complete_positive_control_passes_paragraph_gate_without_claiming_span_quality(self):
        result = summarize(rows())
        self.assertTrue(result['primary']['developmentParagraphGatePassed'])
        self.assertEqual(result['primary']['truePositives'], 8)
        self.assertLess(result['primary']['recallOneSided95LowerIfIid'], .95)

    def test_failed_positive_is_fn_even_when_its_model_flag_was_true(self):
        data = rows()
        data[0]['completed'] = False
        result = summarize(data)['primary']
        self.assertEqual((result['truePositives'], result['falseNegatives']), (7, 1))
        self.assertFalse(result['developmentParagraphGatePassed'])
        self.assertFalse(result['allRequestsCompleted'])

    def test_uncertainty_false_alarm_stays_in_primary(self):
        data = rows()
        data.append({**data[0], 'id': 'minor', 'materialError': False, 'namedError': None, 'majorWarning': False})
        result = summarize(data)
        self.assertEqual(result['primary']['falsePositives'], 1)
        self.assertFalse(result['primary']['developmentParagraphGatePassed'])
        self.assertTrue(result['supplementaryMajorOnly']['developmentParagraphGatePassed'])
        self.assertFalse(result['supplementaryMajorOnly']['replacesPrimaryAcceptanceGate'])

    def test_missing_cross_slice_and_zero_precision_are_unassessed(self):
        data = [row for row in rows() if not (row['translationSystem'] == 'b' and row['split'] == 'read')]
        result = summarize(data)['primary']
        self.assertFalse(result['developmentParagraphGatePassed'])
        self.assertTrue(result['slices']['additionalValidationRequired'])
        for row in data:
            row['warning'] = False
        result = summarize(data)['primary']
        self.assertIsNone(result['precision'])
        self.assertEqual(result['recall'], 0)

    def test_exact_bounds_have_correct_empty_and_all_success_behavior(self):
        self.assertIsNone(one_sided_lower(0, 0))
        self.assertEqual(one_sided_lower(0, 14), 0)
        self.assertAlmostEqual(one_sided_lower(14, 14), .05 ** (1 / 14), places=12)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-qe-evaluation-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.dataset = self.root / '.training/quality-evaluation/dev48-v1'
        self.run = self.root / '.translation/qe/llm-candidates/qwen35-9b/runs/synthetic-only'
        self.dataset.mkdir(parents=True)
        self.run.mkdir(parents=True)
        contract_dir = self.root / 'scripts/local-qe/llm-qwen35'
        self.contract_dir = contract_dir
        contract_dir.mkdir(parents=True)
        for name in ('contract.py', 'prompt-v1.txt', 'response-schema-v1.json', 'evaluation-policy-v1.json'):
            (contract_dir / name).write_bytes((evaluator.ROOT / 'scripts/local-qe/llm-qwen35' / name).read_bytes())
        for name in ('material_warning.py', 'qwen-material-warning-policy-v2.json'):
            (contract_dir.parent / name).write_bytes((evaluator.ROOT / 'scripts/local-qe' / name).read_bytes())
        inputs = [{'id': f'fixture-{i}', 'source': 'Synthetic source.', 'translation': '합성 번역.', 'context': '',
                   'sourceSha256': 'fixture-source', 'translationSha256': 'fixture-target',
                   'translationSystem': 'a' if i < 24 else 'b', 'split': 'dev' if i % 2 else 'read'} for i in range(48)]
        labels = [{**item, 'materialError': i < 6, 'namedError': str(i) if i < 6 else None} for i, item in enumerate(inputs)]
        self.projected = self.dataset / 'projected.jsonl'
        for path, data in [(self.dataset / 'inputs.jsonl', inputs), (self.dataset / 'judgments.jsonl', labels),
                           (self.projected, [{k: row[k] for k in ('id', 'source', 'translation', 'context')} for row in inputs])]:
            path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in data), encoding='utf-8')
        self.summary = {'expectedCount': 48, 'inputSha256': evaluator.digest(self.projected),
                        'referencesOrJudgmentsIncluded': False, 'artifacts': {},
                        'itemStatuses': [{'id': item['id'], 'status': 'not_run'} for item in inputs],
                        'codeHashes': {str((contract_dir / 'contract.py').resolve()): evaluator.digest(contract_dir / 'contract.py')},
                        'status': 'synthetic_failed_before_inference', 'finalIntegrityVerified': True, 'childProcessStopped': True}
        self.frozen = {'budgetRows': {item['id']: {} for item in inputs}, 'freezeSha256': 'synthetic-freeze',
                       'tokenBudgetAuditSha256': 'synthetic-audit', 'profileSha256': 'synthetic-profile'}
        active = patch.object(evaluator, 'verify_frozen_setup', side_effect=lambda _summary: self.frozen)
        active.start()
        self.addCleanup(active.stop)
        self.save()
        for name, value in [('ROOT', self.root), ('DATASET', self.dataset),
                            ('INPUT_SHA', evaluator.digest(self.dataset / 'inputs.jsonl')),
                            ('LABEL_SHA', evaluator.digest(self.dataset / 'judgments.jsonl'))]:
            active = patch.object(evaluator, name, value)
            active.start()
            self.addCleanup(active.stop)

    def save(self):
        (self.run / 'summary.json').write_text(json.dumps(self.summary), encoding='utf-8')

    def artifact(self, name, value):
        path = self.run / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        self.summary['artifacts'][name] = evaluator.digest(path)

    def complete_synthetic_run(self):
        """A fully linked positive control; these are never real model outputs."""
        labels = evaluator.read_lines(self.dataset / 'judgments.jsonl')
        for label in labels:
            label['materialError'] = True
        label_path = self.dataset / 'judgments.jsonl'
        label_path.write_text(''.join(json.dumps(row) + '\n' for row in labels), encoding='utf-8')
        active = patch.object(evaluator, 'LABEL_SHA', evaluator.digest(label_path))
        active.start()
        self.addCleanup(active.stop)
        spec = importlib.util.spec_from_file_location('synthetic_fixed_contract', self.contract_dir / 'contract.py')
        contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contract)
        self.contract = contract
        self.summary.update(version=evaluator.RUNTIME_VERSION, status='completed', modelLoaded=True,
                            runtimeContractValidated=True, generationRequests=48,
                            guard={'abortReason': None, 'killError': None}, contextTokens=evaluator.CONTEXT,
                            ggufMetadata={'templateSha256': evaluator.TEMPLATE_SHA},
                            sampling=evaluator.NATIVE_SAMPLING.copy(),
                            thinking={'enable_thinking': False, 'suffixMustBeVerifiedAtRuntime': True})
        self.summary.update(ownedPid=1234, initialChildTelemetry={'pid': 1234, 'creationTicks': 5678})
        self.summary['codeHashes'] = {str((self.contract_dir / name).resolve()): sha
                                      for name, sha in evaluator.SUPPORTED_CODE_HASHES.items()}
        requests = []
        for index, row in enumerate(evaluator.read_lines(self.projected)):
            prefix = f'{index + 1:04d}'
            request = contract.build_request(row)
            requests.append({'id': row['id'], 'request': request})
            prompt = ''.join('<|im_start|>' + message['role'] + '\n' + message['content'].strip()
                             + '<|im_end|>\n' for message in request['messages'])
            prompt += '<|im_start|>assistant\n<think>\n\n</think>\n\n'
            self.artifact(prefix + '-template.raw.json', {'prompt': prompt})
            self.artifact(prefix + '-tokenize.raw.json', {'tokens': [1, 2, 3]})
            self.artifact(prefix + '-native-request.json', evaluator.NATIVE_SAMPLING
                          | {'prompt': [1, 2, 3], 'json_schema': contract.load_schema(),
                             'message_delimiters': evaluator.MESSAGE_DELIMITERS})
            self.frozen['budgetRows'][row['id']] = {'fitsStrict': True, 'inputTokens': 3,
                'promptSha256': evaluator.hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                'tokenIdsSha256': evaluator.hashlib.sha256(b'[1,2,3]').hexdigest()}
            content = json.dumps({'semantic_issues': [{'kind': 'mistranslation', 'dimension': 'other',
                'severity': 'major', 'source_quote': row['source'], 'translation_quote': row['translation'],
                'reason': 'Synthetic test assertion, not a meaning judgment.'}], 'uncertainties': [], 'language_notes': []})
            raw_name = prefix + '-completion.raw.json'
            self.artifact(raw_name, {'content': content, 'tokens': [4, 5], 'prompt': prompt,
                                    'stop_type': 'eos', 'truncated': False, 'stopping_word': '',
                                    'generation_settings': evaluator.NATIVE_SAMPLING.copy(),
                                    'tokens_cached': 4, 'timings': {'cache_n': 0, 'prompt_n': 3,
                                                                  'prompt_ms': 1, 'predicted_ms': 2}})
            self.artifact(prefix + '-assessment.json', {'id': row['id'], 'status': 'completed',
                'rawResponseFile': raw_name, 'rawResponseSha256': evaluator.digest(self.run / raw_name),
                'promptSha256': evaluator.hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                'inputTokens': 3, 'outputTokens': 2, 'assessment': contract.validate_response(row, content),
                'materialWarningV2': evaluator.load_mapping().classify(contract.validate_response(row, content)),
                'cache': {'reusedPromptTokens': 0, 'processedPromptTokens': 3, 'promptMilliseconds': 1,
                    'predictedMilliseconds': 2, 'nativeTimings': {'cache_n': 0, 'prompt_n': 3, 'prompt_ms': 1, 'predicted_ms': 2},
                    'slotTokensAfterResponse': 4, 'prefixReuseObserved': False, 'sameSeedGuaranteesSameOutput': False},
                'childTelemetry': {'pid': 1234, 'creationTicks': 5678, 'priorityClass': 16384,
                    'priorityName': 'BelowNormal', 'kernelCpuSeconds': 1., 'userCpuSeconds': 2., 'totalCpuSeconds': 3.}})
        path = self.run / 'requests.jsonl'
        path.write_text(''.join(json.dumps(row) + '\n' for row in requests), encoding='utf-8')
        self.summary['artifacts'][path.name] = evaluator.digest(path)
        for status in self.summary['itemStatuses']:
            status['status'] = 'completed'
        self.save()

    def test_failed_run_retains_all_48_denominators(self):
        result = evaluator.analyze_run(self.run, self.projected)
        self.assertEqual(result['requestCounts'], {'expected': 48, 'completed': 0})
        self.assertEqual(result['primary']['falseNegatives'], 6)
        self.assertFalse(result['developmentParagraphAccepted'])
        self.assertFalse(result['fullLearningReadinessAccepted'])

    def test_claimed_completion_without_raw_evidence_is_rejected(self):
        self.summary['itemStatuses'][0]['status'] = 'completed'
        self.save()
        with self.assertRaisesRegex(ValueError, 'completed_row_missing_valid_evidence'):
            evaluator.analyze_run(self.run, self.projected)

    def test_modified_artifact_cannot_be_scored(self):
        (self.run / 'fixture.txt').write_text('changed', encoding='utf-8')
        self.summary['artifacts']['fixture.txt'] = '0' * 64
        self.save()
        with self.assertRaisesRegex(ValueError, 'run_artifact_changed'):
            evaluator.analyze_run(self.run, self.projected)

    def test_completed_native_positive_control_passes_only_paragraph_gate(self):
        self.complete_synthetic_run()
        result = evaluator.analyze_run(self.run, self.projected)
        self.assertTrue(result['runtimeAcceptance']['accepted'])
        self.assertTrue(result['developmentParagraphAccepted'])
        self.assertFalse(result['fullLearningReadinessAccepted'])
        self.assertEqual(result['quoteLocalization']['semanticSpanPrecision'], 'not_evaluated')
        self.assertEqual(result['primary']['truePositives'], 48)

    def test_execution_failures_cannot_be_rescued_by_perfect_observed_accuracy(self):
        self.complete_synthetic_run()
        baseline = json.loads(json.dumps(self.summary))
        cases = [({'status': 'failed'}, 'statusCompleted'),
                 ({'modelLoaded': False}, 'modelLoaded'),
                 ({'runtimeContractValidated': False}, 'runtimeContractValidated'),
                 ({'generationRequests': 47}, 'exactly48GenerationRequests'),
                 ({'generationRequests': 49}, 'exactly48GenerationRequests'),
                 ({'generationRequests': True}, 'exactly48GenerationRequests'),
                 ({'guard': {}}, 'guardClosedWithoutErrors'),
                 ({'guard': {'abortReason': 'child_memory_budget_exceeded', 'killError': None}}, 'guardClosedWithoutErrors'),
                 ({'guard': {'abortReason': None, 'killError': 'termination_failed'}}, 'guardClosedWithoutErrors'),
                 ({'failure': {'code': 'failed_after_all_rows'}}, 'noFailureOrCleanupErrors'),
                 ({'guardCleanupError': 'synthetic_failure'}, 'noFailureOrCleanupErrors'),
                 ({'cleanupError': 'synthetic_failure'}, 'noFailureOrCleanupErrors'),
                 ({'integrityError': 'synthetic_failure'}, 'noFailureOrCleanupErrors'),
                 ({'runLockPreservedBecauseChildStopUnconfirmed': True}, 'noFailureOrCleanupErrors'),
                 ({'spawnCleanupUnconfirmed': True}, 'noFailureOrCleanupErrors'),
                 ({'finalIntegrityVerified': False}, 'finalIntegrityVerified'),
                 ({'childProcessStopped': False}, 'childProcessStopped')]
        for changes, failed_check in cases:
            with self.subTest(changes=changes):
                self.summary = baseline | changes
                self.save()
                result = evaluator.analyze_run(self.run, self.projected)
                self.assertTrue(result['primary']['developmentParagraphGatePassed'])
                self.assertEqual(result['primary']['truePositives'], 48)
                self.assertFalse(result['developmentParagraphAccepted'])
                self.assertIn(failed_check, result['runtimeAcceptance']['failedChecks'])

    def test_coherently_rehashed_native_responses_still_require_runtime_contract(self):
        self.complete_synthetic_run()
        raw_name, record_name = '0001-completion.raw.json', '0001-assessment.json'
        baseline = evaluator.read(self.run / raw_name)
        record = evaluator.read(self.run / record_name)
        cases = [({'stop_type': 'limit'}, 'response_not_complete_eos'),
                 ({'truncated': True}, 'response_not_complete_eos'),
                 ({'stopping_word': 'STOP'}, 'unexpected_stop_word'),
                 ({'prompt': 'different prompt'}, 'actual_prompt_differs'),
                 ({'tokens': []}, 'response_empty_or_output_limit'),
                 ({'tokens': [4] * evaluator.OUTPUT_TOKENS}, 'response_empty_or_output_limit'),
                 ({'tokens': [True, 5]}, 'response_empty_or_output_limit'),
                 ({'content': '<think>hidden reasoning</think>'}, 'response_control_or_thinking_leak'),
                 ({'generation_settings': evaluator.NATIVE_SAMPLING | {'temperature': .1}}, 'actual_sampling_differs_temperature'),
                 ({'generation_settings': evaluator.NATIVE_SAMPLING | {'ignore_eos': 0}}, 'actual_sampling_differs_ignore_eos')]
        for changes, expected_error in cases:
            with self.subTest(changes=list(changes)):
                self.artifact(raw_name, baseline | changes)
                self.artifact(record_name, record | {'rawResponseSha256': evaluator.digest(self.run / raw_name)})
                self.save()
                with self.assertRaisesRegex(ValueError, expected_error):
                    evaluator.analyze_run(self.run, self.projected)

    def test_prompt_template_token_request_and_record_evidence_are_linked(self):
        self.complete_synthetic_run()
        names = ('0001-template.raw.json', '0001-tokenize.raw.json', '0001-native-request.json', '0001-assessment.json')
        original = {name: evaluator.read(self.run / name) for name in names}
        cases = [
            (names[0], original[names[0]] | {'prompt': 'unrelated prompt'}, 'rendered_prompt_differs'),
            (names[1], {'tokens': [1, 2, True]}, 'invalid_prompt_tokens'),
            (names[1], {'tokens': [1] * (evaluator.CONTEXT - evaluator.OUTPUT_TOKENS)}, 'prompt_output_context_budget'),
            (names[1], {'tokens': [1, 2, 9]}, 'native_request_differs'),
            (names[2], original[names[2]] | {'temperature': .2}, 'native_request_differs'),
            (names[2], original[names[2]] | {'json_schema': {}}, 'native_request_differs'),
            (names[3], original[names[3]] | {'promptSha256': '0' * 64}, 'prompt_hash_differs'),
            (names[3], original[names[3]] | {'inputTokens': 4}, 'input_token_count_differs'),
            (names[3], original[names[3]] | {'outputTokens': 3}, 'output_token_count_differs'),
        ]
        for name, changed, error in cases:
            with self.subTest(name=name, error=error):
                for path, value in original.items():
                    self.artifact(path, value)
                self.artifact(name, changed)
                self.save()
                with self.assertRaisesRegex(ValueError, error):
                    evaluator.analyze_run(self.run, self.projected)

    def test_required_native_evidence_cannot_be_removed_from_manifest(self):
        self.complete_synthetic_run()
        for name in ('requests.jsonl', '0001-template.raw.json', '0001-tokenize.raw.json', '0001-native-request.json'):
            with self.subTest(name=name):
                sha = self.summary['artifacts'].pop(name)
                self.save()
                with self.assertRaisesRegex(ValueError, 'unrecorded_'):
                    evaluator.analyze_run(self.run, self.projected)
                self.summary['artifacts'][name] = sha

    def test_unknown_runtime_identity_is_rejected_without_importing_output_python(self):
        self.complete_synthetic_run()
        malicious = self.run / 'runtime_v4.py'
        malicious.write_text("raise AssertionError('output Python must never execute')\n", encoding='utf-8')
        self.summary['codeHashes'][str(malicious)] = evaluator.digest(malicious)
        self.summary['codeHashes'][str((self.contract_dir / 'runtime_v4.py').resolve())] = evaluator.digest(malicious)
        self.save()
        with self.assertRaisesRegex(ValueError, 'unsupported_recorded_code_runtime_v4.py'):
            evaluator.analyze_run(self.run, self.projected)

    def test_request_projection_cannot_add_label_information_even_after_rehash(self):
        self.complete_synthetic_run()
        path = self.run / 'requests.jsonl'
        data = evaluator.read_lines(path)
        data[0]['request']['messages'][1]['content'] += ' synthetic gold label'
        path.write_text(''.join(json.dumps(row) + '\n' for row in data), encoding='utf-8')
        self.summary['artifacts'][path.name] = evaluator.digest(path)
        self.save()
        with self.assertRaisesRegex(ValueError, 'recorded_requests_differ_from_frozen_inputs'):
            evaluator.analyze_run(self.run, self.projected)

    def replace_content(self, index, content):
        prefix = f'{index + 1:04d}'
        row = evaluator.read_lines(self.projected)[index]
        text = json.dumps(content, ensure_ascii=False)
        raw_name, record_name = prefix + '-completion.raw.json', prefix + '-assessment.json'
        raw = evaluator.read(self.run / raw_name)
        self.artifact(raw_name, raw | {'content': text})
        validated = self.contract.validate_response(row, text)
        record = evaluator.read(self.run / record_name)
        self.artifact(record_name, record | {'rawResponseSha256': evaluator.digest(self.run / raw_name),
            'assessment': validated, 'materialWarningV2': evaluator.load_mapping().classify(validated)})
        self.save()

    def test_minor_and_invalid_language_quote_do_not_become_v2_material_warning(self):
        self.complete_synthetic_run()
        self.replace_content(6, {'semantic_issues': [{'kind': 'mistranslation', 'dimension': 'other',
            'severity': 'minor', 'source_quote': 'Synthetic source.', 'translation_quote': '합성 번역.',
            'reason': 'Synthetic minor assertion.'}], 'uncertainties': [], 'language_notes': [{
            'kind': 'fluency', 'translation_quote': 'Absent synthetic quote.', 'reason': 'Synthetic language note.'}]})
        result = evaluator.analyze_run(self.run, self.projected)
        row = result['rows'][6]
        self.assertFalse(row['warning'])
        self.assertFalse(row['majorWarning'])
        self.assertTrue(row['v1DiagnosticWholeParagraphReview'])
        self.assertFalse(result['v1PolicyAndResultsReplaced'])
        self.assertEqual(result['primary']['falseNegatives'], 1)
        self.assertEqual(result['quoteLocalization']['semanticSpanPrecision'], 'not_evaluated')

    def test_uncertainty_remains_primary_warning_without_major_assertion(self):
        self.complete_synthetic_run()
        self.replace_content(6, {'semantic_issues': [], 'uncertainties': [{
            'source_quote': 'Synthetic source.', 'translation_quote': '합성 번역.',
            'reason': 'Synthetic uncertainty; not a real meaning judgment.'}], 'language_notes': []})
        result = evaluator.analyze_run(self.run, self.projected)
        self.assertTrue(result['rows'][6]['warning'])
        self.assertFalse(result['rows'][6]['majorWarning'])
        self.assertEqual(result['primary']['truePositives'], 48)
        self.assertEqual(result['supplementaryMajorOnly']['falseNegatives'], 1)

    def test_saved_v2_flags_cannot_override_raw_recomputed_mapping(self):
        self.complete_synthetic_run()
        name = '0001-assessment.json'
        record = evaluator.read(self.run / name)
        record['materialWarningV2']['primaryWarning'] = False
        self.artifact(name, record)
        self.save()
        with self.assertRaisesRegex(ValueError, 'saved_material_mapping_differs_from_recomputed'):
            evaluator.analyze_run(self.run, self.projected)

    def test_changed_v4_cache_delimiters_audit_and_cpu_identity_are_rejected(self):
        self.complete_synthetic_run()
        names = ('0001-native-request.json', '0001-assessment.json', '0001-completion.raw.json')
        original = {name: evaluator.read(self.run / name) for name in names}
        changes = [(names[0], original[names[0]] | {'cache_prompt': False}, 'native_request_differs'),
                   (names[0], original[names[0]] | {'message_delimiters': []}, 'native_request_differs'),
                   (names[1], original[names[1]] | {'cache': {}}, 'recorded_cache_observation_differs'),
                   (names[1], original[names[1]] | {'childTelemetry': original[names[1]]['childTelemetry']
                       | {'priorityClass': 32}}, 'recorded_child_telemetry_invalid'),
                   (names[1], original[names[1]] | {'childTelemetry': original[names[1]]['childTelemetry']
                       | {'pid': 9999}}, 'recorded_child_identity_differs')]
        for name, changed, error in changes:
            with self.subTest(error=error):
                for key, value in original.items():
                    self.artifact(key, value)
                self.artifact(name, changed)
                self.save()
                with self.assertRaisesRegex(ValueError, error):
                    evaluator.analyze_run(self.run, self.projected)
        for key, value in original.items():
            self.artifact(key, value)
        self.frozen['budgetRows']['fixture-0']['tokenIdsSha256'] = '0' * 64
        self.save()
        with self.assertRaisesRegex(ValueError, 'native_prompt_differs_from_frozen_token_audit'):
            evaluator.analyze_run(self.run, self.projected)


class FrozenSetupTests(unittest.TestCase):
    """Exercise real fixed preparation bytes only; no labels or model outputs."""
    def setUp(self):
        base = evaluator.ROOT / '.translation/qe/llm-candidates/qwen35-9b'
        freeze = evaluator.read(base / 'freeze-v4.json')
        audit_path = base / 'token-budget-v1/attempt-001/summary.json'
        audit = evaluator.read(audit_path)
        excluded = {'test_runtime_v4.py', 'token_budget.py', 'test_token_budget.py', 'README_V4.md', 'CACHE_REUSE_REVIEW_V1.md'}
        self.summary = {'version': evaluator.RUNTIME_VERSION, 'resourceProfile': evaluator.RESOURCE_PROFILE,
            'materialWarningMapping': evaluator.MAPPING_IDENTITY, 'nativeStartupConfiguration': evaluator.STARTUP_CONFIGURATION,
            'installation': {'manifestSha256': evaluator.INSTALL_SHA}, 'inputSha256': evaluator.PROJECTED_INPUT_SHA,
            'codeHashes': {p: sha for p, sha in freeze['codeHashes'].items() if Path(p).name not in excluded},
            'tokenBudgetPreflight': {'path': str(audit_path), 'sha256': evaluator.AUDIT_SHA,
                'inputSha256': evaluator.PROJECTED_INPUT_SHA, 'templateSha256': audit['templateSha256'],
                'rows': {row['id']: row for row in audit['rows']}, 'sharedPrefixAudit': audit['sharedPrefixAudit'],
                'contextTokens': evaluator.CONTEXT, 'reservedOutputTokens': evaluator.OUTPUT_TOKENS,
                'mapping': evaluator.MAPPING_IDENTITY}}

    def test_real_frozen_preparation_chain_passes_without_runtime_import(self):
        with patch.object(evaluator.importlib.util, 'spec_from_file_location') as importer:
            result = evaluator.verify_frozen_setup(self.summary)
        importer.assert_not_called()
        self.assertEqual(result['freezeSha256'], evaluator.FREEZE_SHA)
        self.assertEqual(len(result['budgetRows']), 48)
        self.assertFalse(result['modelWeightsRehashedByEvaluator'])

    def test_v3_or_changed_v4_configuration_is_not_accepted(self):
        changes = [({'version': 'qwen35-semantic-review-run-v3'}, 'unsupported_frozen_runtime_or_profile'),
                   ({'resourceProfile': 'another'}, 'unsupported_frozen_runtime_or_profile'),
                   ({'materialWarningMapping': {}}, 'recorded_material_mapping_differs'),
                   ({'nativeStartupConfiguration': evaluator.STARTUP_CONFIGURATION | {'threads': 8}}, 'recorded_startup_configuration_differs'),
                   ({'installation': {}}, 'run_install_identity_differs'),
                   ({'codeHashes': {}}, 'run_code_identity_differs_from_freeze'),
                   ({'tokenBudgetPreflight': {}}, 'recorded_token_preflight_differs')]
        for changed, error in changes:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                evaluator.verify_frozen_setup(self.summary | changed)

    def test_altered_frozen_artifacts_cannot_be_accepted(self):
        real_digest = evaluator.digest
        for suffix, error in [('freeze-v4.json', 'frozen_v4_manifest_changed'),
            ('resource-profile-v3.json', 'frozen_resource_profile_changed'),
            ('install-manifest.json', 'frozen_install_manifest_changed'),
            ('contract.py', 'frozen_source_or_interpreter_changed'),
            ('0001.stdin.bin', 'frozen_token_budget_artifact_changed')]:
            with self.subTest(suffix=suffix), patch.object(evaluator, 'digest',
                    side_effect=lambda path: '0' * 64 if str(path).endswith(suffix) else real_digest(path)):
                with self.assertRaisesRegex(ValueError, error):
                    evaluator.verify_frozen_setup(self.summary)

    def test_duplicate_or_nonfinite_wrapper_json_is_rejected(self):
        for text in ('{"flag":false,"flag":true}', '{"value":NaN}'):
            with self.assertRaises(ValueError):
                evaluator.parse_json(text)


if __name__ == '__main__':
    unittest.main()
