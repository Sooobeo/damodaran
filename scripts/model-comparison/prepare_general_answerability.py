"""Prepare one anonymous Korean-only general16 answer packet; never infer or score.

Use the same .venv-training Python as the producer, because the existing verifier
checks its identity. Only completed 16-row runs are eligible. Example (after the
coordinator has confirmed completion):
  .venv-training/Scripts/python.exe -B scripts/model-comparison/prepare_general_answerability.py --run RUN_DIRECTORY --output .training/quality-evaluation/general-answerability/NEW_DIRECTORY

Give a new answer session ONLY answerer/packet.jsonl, never its parent directory.
The preparer/author has seen the source and key and must not answer the questions.
Private files are separated for distribution, not protected by new OS permissions.
Run --self-test for synthetic integrity/omission/leakage checks, without a model.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / 'content/model-comparison/general-context-questions-20260911.json'
QUESTION_SHA = '4faac811b4b2644fe73f2b2adcfa9862207243988e963f4e7d331d627900ed51'
DATASET = ROOT / 'content/model-comparison/general-context-dev-20260911.jsonl'
DATASET_SHA = '4d63cbbd7299835849cdf750813e7c18a051ae98149a8ec51b73d5e8057cdfd4'
IDS = [f'GCTX26-{i:03d}' for i in range(1, 17)]
OUTPUT_ROOT = ROOT / '.training/quality-evaluation/general-answerability'
VERSION = 'general16-korean-answer-packet-v1'
RUN_VERSION = 'general-context-hymt-screen-v1'
VISIBLE_FIELDS = {'questionId', 'questionKo', 'candidateTranslationKo', 'learningContextKo'}
IMMUTABLE_RUN_FIELDS = ('version', 'candidate', 'profile', 'input', 'catalog', 'codeHashes', 'sampling',
    'contextSize', 'modelSha256', 'runtimeOverrides', 'samplingNormalization', 'ramBudgetGiB',
    'createdAtUtc', 'inferenceRequested', 'trainingPerformed', 'postProcessingApplied',
    'translationMemoryApplied', 'appDeploymentPerformed', 'humanReviewed', 'coldProcessPerRun',
    'automaticRetryEnabled', 'inputFieldsUsed', 'promptReceipts', 'timeLimitsSeconds')


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def text_sha(value):
    return sha(value.encode('utf-8'))


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: require(False, 'nonfinite_json'))


def safe_path(path, boundary):
    absolute = Path(os.path.abspath(path))
    for item in (absolute, *absolute.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            require(not item.is_symlink() and not (getattr(info, 'st_file_attributes', 0)
                    & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)), 'linked_path_refused')
    resolved, boundary = absolute.resolve(), Path(boundary).resolve()
    require(resolved.is_relative_to(boundary) and resolved != boundary, 'path_outside_allowed_directory')
    return resolved


def validate_questions(raw, sources, *, expected_sha=QUESTION_SHA):
    """Digest override is for in-process synthetic tests, never a CLI option."""
    require(sha(raw) == expected_sha, 'frozen_questions_changed')
    questions = parse(raw)
    require(questions.get('version') == 'general-context-questions-20260911-v1'
            and questions.get('datasetSha256') == DATASET_SHA, 'question_dataset_identity')
    require(questions.get('provenance', {}).get('candidateOutputTextViewedDuringAuthoring') is False
            and questions.get('sourceAudit', {}).get('completed') is True, 'question_freeze_provenance')
    require([row.get('id') for row in sources] == IDS
            and [row.get('id') for row in questions.get('items', [])] == IDS, 'question_source_coverage')
    policy = questions.get('contextPolicy', {})
    require(policy.get('allowedForIds') == IDS[12:]
            and policy.get('sameContextForProfiles') == ['raw', 'contextual'], 'shared_context_policy')
    for source, item in zip(sources, questions['items'], strict=True):
        for field, text in (('sourceSha256', source['source']), ('contextSha256', source['context'])):
            require(source.get(field) == item.get(field) == text_sha(text), 'question_source_hash')
        context = item.get('learningContextKo')
        require(type(context) is str and item.get('learningContextKoSha256') == text_sha(context), 'learning_context_hash')
        require(bool(context) == (item['id'] in IDS[12:]), 'unauthorized_learning_context')
        entries = item.get('questions', [])
        require([q.get('id') for q in entries] == [item['id'] + '-Q1', item['id'] + '-Q2'], 'question_coverage')
        for index, question in enumerate(entries):
            require(type(question.get('questionKo')) is str and bool(question['questionKo'].strip())
                    and question.get('criticalQuestion') is (index == 0)
                    and question.get('contextAloneCompleteAnswer') is False, 'question_contract')
    return questions


def validate_completed_state(plan, summary, predictions, sources):
    require(summary.get('version') == RUN_VERSION and summary.get('status') == 'completed', 'complete_run_required')
    for field in ('expectedCount', 'recordedCount', 'completed', 'completionRequestsSent'):
        require(type(summary.get(field)) is int and summary[field] == 16, 'sixteen_completed_requests_required')
    for field in ('modelLoaded', 'integrityVerified', 'childProcessStopped', 'inferenceRequested', 'outputIntegrityPassed'):
        require(summary.get(field) is True, 'run_evidence_incomplete_' + field)
    require(summary.get('failure') is None and not any(field in summary for field in
        ('monitorCleanupError', 'cleanupError', 'logCleanupError', 'finalIntegrityError', 'memoryOrTimeGuardAborted')),
        'run_failure_or_cleanup_error')
    monitor = summary.get('memoryMonitoring', {})
    require(all(field in monitor and monitor[field] is None for field in
                ('abortReason', 'monitorError', 'ownedChildKillError')), 'guard_evidence_missing_or_failed')
    require(type(summary.get('actualRuntime')) is dict and bool(summary['actualRuntime']), 'runtime_validation_missing')
    for field in IMMUTABLE_RUN_FIELDS:
        require(field in plan and field in summary and packed(plan[field]) == packed(summary[field]),
                'plan_summary_configuration_differs_' + field)
    require(plan.get('modelLoaded') is False and plan.get('completionRequestsSent') == 0, 'initial_plan_not_initial')
    require(summary.get('candidate') in ('hy7', 'hy30') and summary.get('profile') in ('raw', 'contextual'), 'unknown_configuration')
    for field in ('trainingPerformed', 'postProcessingApplied', 'translationMemoryApplied', 'appDeploymentPerformed',
                  'humanReviewed', 'automaticRetryEnabled'):
        require(summary.get(field) is False, 'unexpected_processing_' + field)
    require(summary.get('coldProcessPerRun') is True, 'fresh_process_required')
    require(summary.get('inputFieldsUsed') == ['source'] + (['context'] if summary['profile'] == 'contextual' else []),
            'profile_input_fields_differ')
    require([p.get('id') for p in predictions] == [s.get('id') for s in sources] == IDS, 'prediction_coverage')
    for prediction, source in zip(predictions, sources, strict=True):
        require(prediction.get('status') == 'completed' and prediction.get('outputIntegrityPassed') is True,
                'incomplete_prediction')
        require(prediction.get('profile') == summary['profile'] and prediction.get('candidate') == summary['candidate'],
                'mixed_profile_or_candidate')
        for field in ('sourceSha256', 'contextSha256'):
            require(prediction.get(field) == source[field], 'prediction_source_hash')
        target = prediction.get('translation')
        require(type(target) is str and bool(target.strip()) and prediction.get('targetSha256') == text_sha(target),
                'prediction_target_hash')
        require(prediction.get('humanReviewed') is False and prediction.get('postProcessingApplied') is False,
                'prediction_processing_differs')
    # Failed numeric/terminology checks remain in the sample; they are not exclusions.
    require(summary.get('automaticCheckFailureIds') == [p['id'] for p in predictions if not p.get('automaticChecksPassed')],
            'automatic_check_failure_inventory_differs')
    require(packed(summary['promptReceipts']) == packed([p.get('promptInput') for p in predictions]), 'prompt_receipt_inventory_differs')


def validate_supported_configuration(summary, api):
    small = summary['candidate'] == 'hy7'
    expected_model = api.MODEL['sha256'] if small else api.setup.MODEL['sha256']
    expected_overrides = api.OVERRIDES if small else {}
    expected_normalization = None if small else api.SAMPLING_NORMALIZATION
    require(summary['modelSha256'] == expected_model and summary['contextSize'] == api.CONTEXT_SIZE
            and packed(summary['sampling']) == packed(api.SAMPLING)
            and packed(summary['runtimeOverrides']) == packed(expected_overrides)
            and packed(summary['samplingNormalization']) == packed(expected_normalization), 'supported_model_configuration_differs')
    require(type(summary['ramBudgetGiB']) is int and summary['ramBudgetGiB'] in (8, 9, 10, 11, 12), 'unsupported_ram_budget')
    metadata = summary.get('ggufContract', {})
    require(metadata.get('metadataSha256') == api.HEADER_SHA and metadata.get('templateSha256') == api.TEMPLATE_SHA,
            'supported_gguf_contract_differs')


def validate_raw_termination(prediction, response, summary):
    text, tokens = prediction['translation'], response.get('tokens')
    require(response.get('content') == text and packed(tokens) == packed(prediction.get('outputTokenIds')),
            'raw_prediction_text_or_tokens_differ')
    vocabulary_size = 128167 if summary['candidate'] == 'hy7' else 120832
    require(type(tokens) is list and all(type(token) is int and 0 <= token < vocabulary_size for token in tokens)
            and 0 < len(tokens) < summary['sampling']['n_predict'], 'invalid_output_token_evidence')
    require(type(response.get('tokens_predicted')) is int
            and len(tokens) == response['tokens_predicted'] == prediction.get('generatedTokens')
            and prediction.get('outputTokenIdsSha256') == sha(packed(tokens)), 'output_token_count_or_hash')
    stop = response.get('stop_type')
    require(response.get('truncated') is False and prediction.get('truncated') is False
            and prediction.get('outputLimitReached') is False and stop == prediction.get('stopType'), 'truncated_or_limited_output')
    if stop == 'eos':
        require(tokens[-1] in ({127957, 127960, 127967} if summary['candidate'] == 'hy7' else {120025}), 'invalid_terminal_token')
        require(response.get('stopping_word') in ('', None), 'unexpected_stop_word')
    else:
        require(summary['candidate'] == 'hy7' and stop == 'word'
                and response.get('stopping_word') in summary['sampling']['stop'], 'output_not_complete')
    require(response.get('stopping_word') == prediction.get('stoppingWord'), 'stopping_word_differs')
    prompt = response.get('prompt')
    require(type(prompt) is str and text_sha(prompt) == prediction.get('promptSha256'), 'actual_prompt_hash_differs')
    require(type(response.get('tokens_evaluated')) is int
            and response['tokens_evaluated'] == prediction.get('inputTokens')
            and 0 < response['tokens_evaluated'] + summary['sampling']['n_predict'] < summary['contextSize'],
            'prompt_token_budget_or_count')
    require('\ufffd' not in text and not re.search(r'<\|[^\n>]*\|>|<｜[^\n>]*｜>|</?(?:think|answer|tool[^>]*)>|<eos:[^>]*>', text),
            'invalid_unicode_or_control_token')


def load_verified(run):
    # Repository-fixed modules only. No code snapshot or run-supplied Python is imported.
    import general_context_input as data
    import verify_general_context_pair as verifier
    run = safe_path(run, ROOT / '.training/comparisons')
    require((run / 'summary.json').is_file(), 'completed_summary_required')
    summary_raw = (run / 'summary.json').read_bytes()
    summary = parse(summary_raw)
    require(summary.get('status') == 'completed', 'complete_run_required')
    verified, evidence = verifier.load_run(run)
    require(packed(verified) == packed(summary) and evidence['summarySha256'] == sha(summary_raw), 'summary_changed')
    artifacts = summary['artifactHashes']
    require(all(name in artifacts for name in ('plan.json', 'predictions.jsonl', 'runtime.log', 'memory-samples.jsonl')),
            'required_run_artifacts_missing')
    plan = parse((run / 'plan.json').read_bytes())
    sources, _ = data.read_input()
    require(sha(DATASET.read_bytes()) == DATASET_SHA, 'frozen_dataset_changed')
    question_raw = QUESTIONS.read_bytes()
    questions = validate_questions(question_raw, sources)
    predictions = [parse(line) for line in (run / 'predictions.jsonl').read_bytes().split(b'\n') if line.strip()]
    validate_completed_state(plan, summary, predictions, sources)
    api = verifier.runner.backend(summary['candidate'])
    validate_supported_configuration(summary, api)
    for prediction in predictions:
        response = parse((run / prediction['rawResponseFile']).read_bytes())
        validate_raw_termination(prediction, response, summary)
    files = {str(run / 'summary.json'): sha(summary_raw), str(QUESTIONS): sha(question_raw),
             str(DATASET): DATASET_SHA, str(data.MANIFEST): data.MANIFEST_SHA,
             str(verifier.runner.common.CATALOG): summary['catalog']['sha256'],
             **summary['codeHashes'], **{str(run / name): value for name, value in artifacts.items()}}
    evidence.update(questionnaireSha256=sha(question_raw), datasetSha256=DATASET_SHA,
                    verificationFiles=files, packetPreparerSha256=sha(Path(__file__).read_bytes()),
                    verifierCodeSha256=sha(Path(verifier.__file__).read_bytes()))
    return questions, summary, predictions, evidence


def build_packet(questions, summary, predictions):
    require([p.get('id') for p in predictions] == IDS, 'packet_prediction_coverage')
    packet, key_rows = [], []
    for item, prediction in zip(questions['items'], predictions, strict=True):
        require(item['id'] == prediction['id'], 'packet_item_link')
        for question in item['questions']:
            identifier = 'q_' + secrets.token_hex(16)
            packet.append({'questionId': identifier, 'questionKo': question['questionKo'],
                           'candidateTranslationKo': prediction['translation'], 'learningContextKo': item['learningContextKo']})
            key_rows.append({'questionId': identifier, 'sourceId': item['id'], 'originalQuestionId': question['id'],
                'domain': item['domain'], 'question': question, 'sourceSha256': item['sourceSha256'],
                'contextSha256': item['contextSha256'], 'targetSha256': prediction['targetSha256'],
                'learningContextKoSha256': item['learningContextKoSha256']})
    secrets.SystemRandom().shuffle(packet)
    key = {'version': VERSION, 'candidate': summary['candidate'], 'profile': summary['profile'],
           'questionnaireSha256': QUESTION_SHA, 'datasetSha256': DATASET_SHA,
           'scoring': questions['scoring'], 'rows': key_rows}
    validate_packet(packet, key, questions, predictions)
    return packet, key


def validate_packet(packet, key, questions, predictions):
    require(len(packet) == len(key['rows']) == 32, 'packet_question_count')
    key_map = {row['questionId']: row for row in key['rows']}
    require(len(key_map) == 32 and {row.get('questionId') for row in packet} == set(key_map), 'packet_duplicate_or_missing_id')
    expected = {q['id']: (item, q) for item in questions['items'] for q in item['questions']}
    require({row['originalQuestionId'] for row in key['rows']} == set(expected)
            and Counter(row['sourceId'] for row in key['rows']) == Counter({identifier: 2 for identifier in IDS}),
            'private_key_question_coverage')
    targets = {row['id']: row for row in predictions}
    for row in packet:
        require(set(row) == VISIBLE_FIELDS and all(type(v) is str for v in row.values()), 'packet_field_allowlist')
        require(re.fullmatch(r'q_[0-9a-f]{32}', row['questionId']) is not None, 'nonanonymous_question_id')
        link = key_map[row['questionId']]
        item, question = expected[link['originalQuestionId']]
        require(link['sourceId'] == item['id'] and row['questionKo'] == question['questionKo']
                and row['learningContextKo'] == item['learningContextKo']
                and row['candidateTranslationKo'] == targets[item['id']]['translation'], 'packet_content_link')


def assert_sealed(evidence):
    for name, expected in evidence['verificationFiles'].items():
        path = Path(name)
        require(path.is_file() and not path.is_symlink() and sha(path.read_bytes()) == expected, 'verified_evidence_changed')


def write_exclusive(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(run, output):
    output = safe_path(output, OUTPUT_ROOT)
    require(not output.exists(), 'new_output_directory_required')
    questions, summary, predictions, evidence = load_verified(run)
    packet, key = build_packet(questions, summary, predictions)
    packet_raw = b''.join(packed(row) + b'\n' for row in packet)
    key['packetSha256'] = sha(packet_raw)
    key_raw = packed(key) + b'\n'
    manifest = {'schemaVersion': 1, 'version': VERSION, 'status': 'prepared_not_answered_not_scored',
        'createdAtUtc': datetime.now(timezone.utc).isoformat(), 'expectedItems': 16, 'expectedQuestions': 32,
        'candidate': summary['candidate'], 'profile': summary['profile'],
        'configuration': {field: summary[field] for field in IMMUTABLE_RUN_FIELDS},
        'evidence': evidence, 'packet': {'path': 'answerer/packet.jsonl', 'sha256': sha(packet_raw), 'fields': sorted(VISIBLE_FIELDS)},
        'privateKey': {'path': 'private/private-key.json', 'sha256': sha(key_raw)},
        'automaticCheckFailureIds': summary['automaticCheckFailureIds'], 'automaticCheckFailuresExcluded': False,
        'shareOnly': 'answerer/packet.jsonl', 'freshAnswerSessionPerPacketRequired': True,
        'answererMustNotHaveSeenSourceOrKey': True, 'preparerOrQuestionAuthorMustNotAnswer': True,
        'answersMustBeFrozenBeforeOpeningKey': True, 'privateDirectoryIsNotAnOsPermissionBoundary': True,
        'answerFields': questions['administration']['answerFields'],
        'humanReviewed': False, 'independentTest': False, 'modelInferencePerformed': False,
        'answerabilityEvaluated': False, 'meaningCertified': False}
    assert_sealed(evidence)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'answerer').mkdir()
    (output / 'private').mkdir()
    # The private completion manifest is written last. A write error is not a prepared packet.
    write_exclusive(output / 'private/private-key.json', key_raw)
    write_exclusive(output / 'answerer/packet.jsonl', packet_raw)
    require(sha((output / 'answerer/packet.jsonl').read_bytes()) == sha(packet_raw)
            and sha((output / 'private/private-key.json').read_bytes()) == sha(key_raw), 'packet_write_verification_failed')
    assert_sealed(evidence)
    write_exclusive(output / 'private/manifest.json', packed(manifest) + b'\n')
    return manifest


def self_test():
    """Only source/key and synthetic target strings; no real run is opened."""
    import copy
    import tempfile
    import unittest
    from unittest.mock import patch

    class SyntheticTests(unittest.TestCase):
        def setUp(self):
            self.sources = [{'id': identifier, 'source': 'SOURCE_SECRET_' + identifier,
                'context': 'CONTEXT_SECRET_' + identifier if i >= 12 else '',
                'domain': 'general' if i < 12 else 'general_context_polysemy'} for i, identifier in enumerate(IDS)]
            self.questions = parse(QUESTIONS.read_bytes())
            for source, item in zip(self.sources, self.questions['items'], strict=True):
                for field, text in (('sourceSha256', source['source']), ('contextSha256', source['context'])):
                    source[field] = item[field] = text_sha(text)
                for question in item['questions']:
                    question['sampleAnswerKo'] = 'ANSWER_SECRET'
                    question['requiredFactsKo'] = ['FACT_SECRET']
            raw = packed(self.questions)
            validate_questions(raw, self.sources, expected_sha=sha(raw))
            self.predictions = [{'id': source['id'], 'translation': '합성 번역 문장이다.',
                'targetSha256': text_sha('합성 번역 문장이다.'), 'status': 'completed', 'outputIntegrityPassed': True,
                'sourceSha256': source['sourceSha256'], 'contextSha256': source['contextSha256'],
                'candidate': 'hy7', 'profile': 'raw', 'humanReviewed': False, 'postProcessingApplied': False,
                'automaticChecksPassed': True, 'promptInput': {'id': source['id']},
                'referenceKo': 'REFERENCE_SECRET', 'checks': 'CHECK_SECRET'} for source in self.sources]
            self.summary = {field: {} for field in IMMUTABLE_RUN_FIELDS}
            self.summary.update(version=RUN_VERSION, candidate='hy7', profile='raw', status='completed',
                expectedCount=16, recordedCount=16, completed=16, completionRequestsSent=16,
                modelLoaded=True, integrityVerified=True, childProcessStopped=True, inferenceRequested=True,
                outputIntegrityPassed=True, failure=None, actualRuntime={'synthetic': True},
                memoryMonitoring={'abortReason': None, 'monitorError': None, 'ownedChildKillError': None},
                coldProcessPerRun=True, inputFieldsUsed=['source'], automaticCheckFailureIds=[],
                promptReceipts=[row['promptInput'] for row in self.predictions],
                sampling={'n_predict': 4096, 'stop': ['<|eos|>', '<|extra_5|>']}, contextSize=8192)
            for field in ('trainingPerformed', 'postProcessingApplied', 'translationMemoryApplied',
                          'appDeploymentPerformed', 'humanReviewed', 'automaticRetryEnabled'):
                self.summary[field] = False
            self.plan = copy.deepcopy(self.summary)
            self.plan.update(modelLoaded=False, completionRequestsSent=0, status='failed')

        def test_anonymous_allowlist_and_no_private_marker_leak(self):
            packet, key = build_packet(self.questions, self.summary, self.predictions)
            text = packed(packet).decode()
            for marker in ('SOURCE_SECRET', 'CONTEXT_SECRET', 'ANSWER_SECRET', 'FACT_SECRET', 'REFERENCE_SECRET', 'CHECK_SECRET',
                           'GCTX26-', 'hy7', 'profile', 'criticalQuestion'):
                self.assertNotIn(marker, text)
            self.assertIn('ANSWER_SECRET', packed(key).decode())
            self.assertEqual(sum(bool(row['learningContextKo']) for row in packet), 8)

        def test_missing_duplicate_or_extra_packet_fields_refused(self):
            packet, key = build_packet(self.questions, self.summary, self.predictions)
            for mutate in (lambda rows: rows.pop(), lambda rows: rows.__setitem__(1, rows[0].copy()),
                           lambda rows: rows[0].update(source='SOURCE_SECRET'),
                           lambda rows: rows[0].update(questionId='GCTX26-001-Q1')):
                changed = copy.deepcopy(packet)
                mutate(changed)
                with self.assertRaises(ValueError):
                    validate_packet(changed, key, self.questions, self.predictions)

        def test_partial_failed_guard_and_changed_plan_refused(self):
            validate_completed_state(self.plan, self.summary, self.predictions, self.sources)
            changes = [{'status': 'failed'}, {'completed': 15}, {'completionRequestsSent': 17},
                       {'childProcessStopped': False}, {'integrityVerified': False}, {'modelLoaded': False},
                       {'memoryMonitoring': {}}, {'cleanupError': 'synthetic'}, {'ramBudgetGiB': 9}]
            for change in changes:
                with self.subTest(change=change), self.assertRaises(ValueError):
                    validate_completed_state(self.plan, self.summary | change, self.predictions, self.sources)
            for changed in (self.predictions[:-1], [self.predictions[0]] * 16):
                with self.assertRaisesRegex(ValueError, 'prediction_coverage'):
                    validate_completed_state(self.plan, self.summary, changed, self.sources)

        def test_source_target_hash_and_context_change_refused(self):
            for field in ('sourceSha256', 'contextSha256', 'targetSha256'):
                changed = copy.deepcopy(self.predictions)
                changed[0][field] = '0' * 64
                with self.subTest(field=field), self.assertRaises(ValueError):
                    validate_completed_state(self.plan, self.summary, changed, self.sources)
            changed = copy.deepcopy(self.questions)
            changed['items'][0]['learningContextKo'] = '허용되지 않은 문맥'
            raw = packed(changed)
            with self.assertRaisesRegex(ValueError, 'learning_context_hash'):
                validate_questions(raw, self.sources, expected_sha=sha(raw))
            with self.assertRaisesRegex(ValueError, 'frozen_questions_changed'):
                validate_questions(packed(self.questions), self.sources)

        def test_both_profiles_have_identical_allowed_korean_context_and_new_ids(self):
            first, _ = build_packet(self.questions, self.summary, self.predictions)
            contextual = [row | {'profile': 'contextual'} for row in self.predictions]
            second, _ = build_packet(self.questions, self.summary | {'profile': 'contextual'}, contextual)
            self.assertEqual({r['questionKo']: r['learningContextKo'] for r in first},
                             {r['questionKo']: r['learningContextKo'] for r in second})
            self.assertFalse({r['questionId'] for r in first} & {r['questionId'] for r in second})

        def test_raw_truncation_prompt_and_terminal_token_rechecked(self):
            response = {'content': '합성 번역 문장이다.', 'tokens': [20, 127960], 'tokens_predicted': 2,
                        'stop_type': 'eos', 'stopping_word': '', 'truncated': False, 'prompt': 'synthetic prompt', 'tokens_evaluated': 10}
            prediction = self.predictions[0] | {'outputTokenIds': response['tokens'], 'generatedTokens': 2,
                'outputTokenIdsSha256': sha(packed(response['tokens'])), 'stopType': 'eos', 'truncated': False,
                'outputLimitReached': False, 'stoppingWord': '', 'promptSha256': text_sha(response['prompt']), 'inputTokens': 10}
            validate_raw_termination(prediction, response, self.summary)
            for change in ({'truncated': True}, {'stop_type': 'limit'}, {'prompt': 'wrong'}, {'tokens_predicted': 1}, {'tokens': [20, 0]}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    validate_raw_termination(prediction, response | change, self.summary)

        def test_model_template_sampling_and_overrides_are_explicit(self):
            from types import SimpleNamespace
            api = SimpleNamespace(MODEL={'sha256': 'synthetic-model-sha'}, CONTEXT_SIZE=8192,
                SAMPLING=self.summary['sampling'], OVERRIDES={'synthetic_override': True},
                HEADER_SHA='synthetic-header-sha', TEMPLATE_SHA='synthetic-template-sha')
            summary = self.summary | {'modelSha256': api.MODEL['sha256'], 'runtimeOverrides': api.OVERRIDES,
                'samplingNormalization': None, 'ramBudgetGiB': 8,
                'ggufContract': {'metadataSha256': api.HEADER_SHA, 'templateSha256': api.TEMPLATE_SHA}}
            validate_supported_configuration(summary, api)
            for changed in ({'modelSha256': 'different'}, {'runtimeOverrides': {}}, {'sampling': {}},
                            {'contextSize': 4096}, {'ramBudgetGiB': True}, {'ggufContract': {}}):
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    validate_supported_configuration(summary | changed, api)

        def test_numerical_failure_is_preserved_not_excluded(self):
            self.predictions[0]['automaticChecksPassed'] = False
            self.summary['automaticCheckFailureIds'] = [IDS[0]]
            validate_completed_state(self.plan, self.summary, self.predictions, self.sources)
            packet, _ = build_packet(self.questions, self.summary, self.predictions)
            self.assertEqual(len(packet), 32)

        def test_new_folder_only_partial_refusal_and_evidence_seal(self):
            module = sys.modules[__name__]
            with tempfile.TemporaryDirectory(prefix='synthetic-general-answerability-') as name:
                boundary = Path(name) / 'allowed'
                output = boundary / 'new'
                with patch.object(module, 'OUTPUT_ROOT', boundary), patch.object(module, 'load_verified', side_effect=ValueError('complete_run_required')):
                    with self.assertRaisesRegex(ValueError, 'complete_run_required'):
                        prepare(Path(name) / 'synthetic-run', output)
                    self.assertFalse(output.exists())
                source = Path(name) / 'synthetic-evidence.json'
                source.write_bytes(b'original')
                seal = {'verificationFiles': {str(source): sha(b'original')}}
                assert_sealed(seal)
                source.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'verified_evidence_changed'):
                    assert_sealed(seal)
                with self.assertRaisesRegex(ValueError, 'path_outside_allowed_directory'):
                    safe_path(Path(name) / 'elsewhere', boundary)
                output.mkdir(parents=True)
                with patch.object(module, 'OUTPUT_ROOT', boundary):
                    with self.assertRaisesRegex(ValueError, 'new_output_directory_required'):
                        prepare(Path(name) / 'synthetic-run', output)

        def test_synthetic_write_separates_distribution_and_binds_exact_bytes(self):
            module = sys.modules[__name__]
            with tempfile.TemporaryDirectory(prefix='synthetic-general-packet-write-') as name:
                boundary, output = Path(name) / 'allowed', Path(name) / 'allowed/new'
                source = Path(name) / 'synthetic-evidence.json'
                source.write_bytes(b'synthetic evidence only')
                evidence = {'verificationFiles': {str(source): sha(source.read_bytes())}}
                with patch.object(module, 'OUTPUT_ROOT', boundary), patch.object(module, 'load_verified',
                    return_value=(self.questions, self.summary, self.predictions, evidence)):
                    manifest = prepare(Path(name) / 'synthetic-run', output)
                self.assertEqual({p.name for p in (output / 'answerer').iterdir()}, {'packet.jsonl'})
                self.assertEqual({p.name for p in (output / 'private').iterdir()}, {'private-key.json', 'manifest.json'})
                raw = (output / 'answerer/packet.jsonl').read_bytes()
                private = (output / 'private/private-key.json').read_bytes()
                self.assertEqual(manifest['packet']['sha256'], sha(raw))
                self.assertEqual(manifest['privateKey']['sha256'], sha(private))
                self.assertEqual(parse(private)['packetSha256'], sha(raw))
                self.assertEqual(len(raw.splitlines()), 32)
                self.assertFalse(manifest['modelInferencePerformed'])
                self.assertFalse(manifest['answerabilityEvaluated'])

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SyntheticTests))
    return 0 if result.wasSuccessful() else 1


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--run', type=Path, help='One fully completed general16 producer directory; no model execution')
    cli.add_argument('--output', type=Path, help='New directory below .training/quality-evaluation/general-answerability')
    cli.add_argument('--self-test', action='store_true', help='Run synthetic checks only; never open real run outputs')
    args = cli.parse_args(argv)
    if args.self_test:
        require(args.run is None and args.output is None, 'self_test_cannot_prepare_a_packet')
        return self_test()
    cli.error('--run and --output are required') if args.run is None or args.output is None else None
    try:
        result = prepare(args.run, args.output)
        print(json.dumps({'status': result['status'], 'questionCount': 32,
                          'shareOnly': str(args.output / 'answerer/packet.jsonl'), 'modelInferencePerformed': False}, ensure_ascii=False))
        return 0
    except Exception as error:
        # Fixed diagnostic codes only: never echo source, key, model command line or response text.
        print(json.dumps({'status': 'refused', 'errorType': type(error).__name__,
                          'code': str(error) if type(error) is ValueError else 'verification_failed'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    raise SystemExit(main())
