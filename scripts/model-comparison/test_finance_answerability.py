"""Synthetic local fixtures only; never open actual model outputs or create reviews."""
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import prepare_finance_answerability as p
import grade_finance_answerability as g
import test_reading_check_input as reading_fixture
from test_v5_review_evidence import upgrade, put, get


class FinanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = reading_fixture.ReadingInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.output_root = self.root / '.training/quality-evaluation/finance-answerability'
        self.output_root.mkdir(parents=True)
        self.dataset = self.output_root / 'source-only.jsonl'
        self.questions_path = self.output_root / 'questions.json'
        self.policy = self.output_root / 'policy.json'
        self.input_dev = self.root / 'content/model-comparison/linguistic-dev-20260910.jsonl'
        self.sources = []
        dev = []
        for identifier in p.COHORTS['dev18']:
            row = {'id': identifier, 'source': 'The lamp was blue.', 'context': '', 'domain': 'finance',
                   'split': 'development_screen', 'humanReviewed': False}
            row.update(sourceSha256=p.text_sha(row['source']), contextSha256=p.text_sha(''))
            dev.append(row)
        put(self.input_dev, dev, True)
        put(self.input_dev.with_name('dataset-manifest.json'), {'version': 'linguistic-dev-20260910-v1',
            'status': 'frozen', 'humanReviewed': False, 'sourceType': 'assistant_authored_unreviewed',
            'dataset': {'file': self.input_dev.name, 'sha256': p.sha(self.input_dev.read_bytes()),
                        'count': 18, 'ids': p.COHORTS['dev18']}})
        for cohort, rows in [('dev18', dev), ('reading6', self.fixture.rows)]:
            self.sources += [{k: row[k] for k in ('id', 'source', 'context', 'sourceSha256', 'contextSha256')} | {'cohort': cohort}
                             for row in rows]
        put(self.dataset, self.sources, True)
        self.questions = {'version': 'finance24-source-questionnaire-v2', 'createdAtUtc': '2026-09-10T00:00:00Z',
            'sourcePacketSha256': p.sha(self.dataset.read_bytes()), 'humanReviewed': False, 'finalHoldout': False,
            'author': {'newSession': True, 'forkTurns': 'none', 'sourceOnly': True,
                       'candidateOutputsViewed': False, 'referencesOrPriorJudgmentsViewed': False}, 'rows': []}
        for source in self.sources:
            item = {k: source[k] for k in ('id', 'cohort', 'sourceSha256', 'contextSha256')}
            item.update(learningContextKo='', learningContextEvidenceQuotes=[], questions=[])
            for number in (1, 2):
                item['questions'].append({'id': source['id'] + f'-Q{number}', 'criticalQuestion': number == 1,
                    'questionKo': '등의 색은 무엇인가요?', 'contextAloneCompleteAnswer': False,
                    'requiredFactsKo': ['PRIVATE_FACT_SECRET'], 'sampleAnswerKo': 'PRIVATE_ANSWER_SECRET',
                    'evidenceSourceQuotes': [source['source']], 'evidenceContextQuotes': []})
            self.questions['rows'].append(item)
        put(self.questions_path, self.questions)
        put(self.policy, {'policyId': 'learning-readiness-20260911-v1', 'translationGates': {
            'questionAnswerability': {'minimumRate': 0.95}, 'coreQuestionAnswerability': {'minimumRate': 1}}})
        source_codes = p.code_files()
        self.codes = {}
        for name, digest in source_codes.items():
            dest = self.root / 'scripts/model-comparison' / Path(name).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(Path(name).read_bytes())
            self.codes[str(dest)] = digest
        patches = [(p, 'ROOT', self.root), (p.screen, 'ROOT', self.root), (p, 'OUTPUT_ROOT', self.output_root),
                   (p, 'DATASET', self.dataset), (p, 'QUESTIONS', self.questions_path), (p, 'POLICY', self.policy),
                   (p, 'DATASET_SHA', p.sha(self.dataset.read_bytes())), (p, 'QUESTION_SHA', p.sha(self.questions_path.read_bytes())),
                   (p, 'POLICY_SHA', p.sha(self.policy.read_bytes())),
                   (p, 'INPUTS', {'dev18': self.input_dev, 'reading6': self.fixture.path}),
                   (p, 'INPUT_SHAS', {'dev18': p.sha(self.input_dev.read_bytes()), 'reading6': p.sha(self.fixture.path.read_bytes())}),
                   (p, 'code_files', lambda: self.codes.copy()),
                   (p.reading.reader, '__file__', str(self.root / 'scripts/model-comparison/reading_check_input.py'))]
        for obj, name, value in patches:
            active = patch.object(obj, name, value)
            active.start()
            self.addCleanup(active.stop)
        install_path = self.root / '.training/comparisons/translategemma-27b-q4/installation-manifest.json'
        install_path.parent.mkdir(parents=True)
        self.installation = {'modelSize': '27b', 'model': {'sha256': p.MODELS['tg27'][1]},
                             'runtimeFiles': [], 'templateSha256': 'c' * 64, 'syntheticFixture': True}
        put(install_path, self.installation)
        self.runs = {}
        for cohort in p.COHORTS:
            clean, identity = p.screen.read_screen(p.INPUTS[cohort])
            directory = self.root / '.training/comparisons' / ('synthetic-' + cohort)
            directory.mkdir()
            summary = upgrade(directory, clean, identity, self.root)
            rows = get(directory / 'predictions.jsonl', True)
            for row, source in zip(rows, clean):
                raw_path = directory / row['rawResponseFile']
                raw = get(raw_path)
                raw.update(content='등은 파란색이었다.', prompt='Translate: ' + source['source'])
                put(raw_path, raw)
                row.update(translation=raw['content'], targetSha256=p.text_sha(raw['content']),
                           promptSha256=p.text_sha(raw['prompt']), rawResponseSha256=p.sha(raw_path.read_bytes()))
                summary['responseFilesSha256'][raw_path.name] = row['rawResponseSha256']
            put(directory / 'predictions.jsonl', rows, True)
            summary.update(modelSha256=p.MODELS['tg27'][1], installationManifestSha256=p.sha(install_path.read_bytes()),
                runtimeFiles=[], templateSha256='c' * 64, postProcessingApplied=False, translationMemoryApplied=False,
                appDeploymentPerformed=False, predictionsSha256=p.sha((directory / 'predictions.jsonl').read_bytes()))
            put(directory / 'summary.json', summary)
            self.runs[cohort] = directory

    def prepare(self, cohort='dev18', name='packet'):
        self.packet = self.output_root / name
        return p.prepare(self.runs[cohort], cohort, p.v5.VERSION, self.packet)

    def write_answers(self):
        self.bundle = g.load_bundle(self.packet)
        self.answers = {'version': g.ANSWER_VERSION, 'packetSha256': self.bundle['hashes']['packetSha256'],
            'provenance': {'answererId': 'synthetic-answerer', 'sessionId': 'synthetic-answer-session',
                'newSession': True, 'forkTurns': 'none', 'inputScope': 'packet_only', 'sourceOrKeyPreviouslyViewed': False,
                'otherCandidateOutputsViewed': False, 'priorJudgmentsViewed': False, 'isQuestionAuthorOrPacketPreparer': False},
            'answers': [{'questionId': identifier, 'answerKo': '푸른색이다.', 'targetEvidenceQuotes': ['파란색이었다'],
                        'contextEvidenceQuotes': [], 'status': 'answered', 'uncertaintyKo': ''} for identifier in self.bundle['packet']]}
        self.answer_path, self.receipt_path, self.judgment_path = [self.output_root / name for name in ('answers.json', 'freeze.json', 'judgments.json')]

    def freeze(self):
        put(self.answer_path, self.answers)
        g.freeze_answers(self.packet, self.answer_path, self.receipt_path)
        self.judgments = {'version': g.JUDGMENT_VERSION, 'packetSha256': self.bundle['hashes']['packetSha256'],
            'privateKeySha256': self.bundle['hashes']['privateKeySha256'], 'answersSha256': p.sha(self.answer_path.read_bytes()),
            'freezeReceiptSha256': p.sha(self.receipt_path.read_bytes()), 'createdAtUtc': p.now(),
            'judge': {'id': 'synthetic-judge', 'kind': 'assistant', 'sessionId': 'synthetic-judge-session',
                'sourceGroundedReview': True, 'automaticExactMatch': False, 'independentOfAnswerer': True},
            'judgments': [{'questionId': identifier, 'verdict': 'correct', 'reasonKo': '동의 표현을 허용하는 합성 판정이다.',
                'sourceEvidenceQuotes': [self.bundle['sources'][link['sourceId']]['source']], 'contextEvidenceQuotes': []}
                for identifier, link in self.bundle['key'].items()]}

    def grade(self, name='result.json'):
        put(self.judgment_path, self.judgments)
        return g.grade(self.packet, self.answer_path, self.receipt_path, self.judgment_path, self.output_root / name)

    def test_v5_both_cohorts_full_adapter_prepare_without_private_leak_or_prefill(self):
        for cohort, count in [('dev18', 36), ('reading6', 12)]:
            manifest = self.prepare(cohort, cohort)
            raw = (self.packet / 'answerer/packet.jsonl').read_bytes()
            for secret in (b'PRIVATE_', b'REAL26-', b'LDEV26-', b'tg27', b'criticalQuestion', b'The lamp', b'Public source'):
                self.assertNotIn(secret, raw)
            rows = [p.parse(line) for line in raw.splitlines()]
            self.assertEqual(len(rows), count)
            self.assertTrue(all(set(row) == p.VISIBLE_FIELDS and row['learningContextKo'] == '' for row in rows))
            self.assertEqual(manifest['producerVersion'], p.v5.VERSION)
            self.assertFalse(manifest['answerabilityEvaluated'])
            self.assertEqual(g.load_bundle(self.packet)['manifest']['runMetadata']['version'], p.v5.VERSION)
            self.assertEqual(list((self.packet / 'answerer').iterdir()), [self.packet / 'answerer/packet.jsonl'])

    def test_completed_claim_missing_telemetry_and_unknown_version_refused(self):
        path = self.runs['dev18'] / 'summary.json'
        original = get(path)
        for mutate in [lambda s: s.update(status='running'), lambda s: s.update(version=None),
                       lambda s: s.update(count=17), lambda s: s.update(codeHashes={}),
                       lambda s: s['memoryMonitoring'].pop('ownedChildExitObserved')]:
            value = copy.deepcopy(original)
            mutate(value)
            put(path, value)
            with self.assertRaises(ValueError): self.prepare()
            self.assertFalse((self.output_root / 'packet').exists())
        put(path, original)
        with self.assertRaises(ValueError): p.load_verified(self.runs['dev18'], 'dev18', 'generic-completed')

    def test_v4_keeps_its_own_version_and_does_not_require_v5_telemetry(self):
        directory = self.runs['dev18']
        summary = get(directory / 'summary.json')
        summary['version'] = 'translategemma-large-screen-v4'
        summary.pop('telemetryVersion')
        source = Path(p.__file__).with_name('run_translategemma_large_v4.py')
        dest = self.root / 'scripts/model-comparison' / source.name
        dest.write_bytes(source.read_bytes())
        summary['codeHashes'][str(dest)] = p.sha(dest.read_bytes())
        put(directory / 'summary.json', summary)
        packet = self.output_root / 'v4-packet'
        result = p.prepare(directory, 'dev18', summary['version'], packet)
        self.assertEqual(result['runMetadata']['version'], 'translategemma-large-screen-v4')
        self.assertEqual(g.load_bundle(packet)['manifest']['producerVersion'], 'translategemma-large-screen-v4')

    def test_model_input_raw_and_producer_code_tampering_refused(self):
        path = self.runs['dev18'] / 'summary.json'
        original = get(path)
        for mutate in [lambda s: s.update(modelSha256='d' * 64), lambda s: s['input'].update(inputSha256='d' * 64),
                       lambda s: s['input']['selectedIds'].reverse(), lambda s: s.update(installationManifestSha256='d' * 64)]:
            value = copy.deepcopy(original)
            mutate(value)
            put(path, value)
            with self.assertRaises(ValueError): self.prepare()
        put(path, original)
        raw_path = self.runs['dev18'] / 'LDEV26-001-response.json'
        raw_path.write_bytes(raw_path.read_bytes() + b' ')
        with self.assertRaises(ValueError): self.prepare()

    def test_secondary_threshold_and_core_are_independent(self):
        self.prepare()
        self.write_answers()
        self.freeze()
        result = self.grade()
        self.assertTrue(result['answerabilityGate']['passed'])
        self.assertEqual((result['overall']['correct'], result['core']['correct']), (36, 18))
        self.assertFalse(result['semanticKeywordMatchingUsed'])
        self.assertFalse(result['fullLearningReadiness']['passed'])
        self.judgments['judgments'][1]['verdict'] = 'incorrect'
        self.assertTrue(self.grade('secondary.json')['answerabilityGate']['passed'])
        self.judgments['judgments'][3]['verdict'] = 'incorrect'
        self.assertFalse(self.grade('two-secondary.json')['answerabilityGate']['passed'])
        self.judgments['judgments'][1]['verdict'] = self.judgments['judgments'][3]['verdict'] = 'correct'
        self.judgments['judgments'][0]['verdict'] = 'incorrect'
        result = self.grade('core.json')
        self.assertTrue(result['answerabilityGate']['checks']['overall95Passed'])
        self.assertFalse(result['answerabilityGate']['passed'])

    def test_reading_requires_all_twelve_and_six_core(self):
        self.prepare('reading6')
        self.write_answers()
        self.freeze()
        result = self.grade()
        self.assertEqual((result['answerabilityGate']['minimumCorrect'], result['answerabilityGate']['minimumCoreCorrect']), (12, 6))
        self.judgments['judgments'][1]['verdict'] = 'incorrect'
        self.assertFalse(self.grade('reading-failure.json')['answerabilityGate']['passed'])

    def test_missing_answers_fail_and_answer_uncertainty_is_separate_from_judge_hold(self):
        self.prepare()
        self.write_answers()
        self.answers['answers'].pop()
        self.answers['answers'][0].update(status='unresolved', uncertaintyKo='판단을 보류한다.')
        unresolved = self.answers['answers'][0]['questionId']
        self.freeze()
        for row in self.judgments['judgments']:
            if row['questionId'] == unresolved: row['verdict'] = 'incorrect'
        result = self.grade()
        self.assertEqual((result['overall']['count'], result['overall']['missingAnswers'], result['overall']['unresolved']), (36, 1, 0))
        self.assertEqual(result['answerStatusCounts']['unresolved'], 1)
        self.assertFalse(result['answerabilityGate']['passed'])
        for row in self.judgments['judgments']:
            if row['questionId'] == unresolved: row['verdict'] = 'unresolved'
        self.assertEqual(self.grade('judge-hold.json')['overall']['unresolved'], 1)

    def test_ambiguous_secondary_answer_can_be_adjudicated_incorrect_but_not_correct(self):
        self.prepare()
        self.write_answers()
        identifier = next(key for key, link in self.bundle['key'].items() if not link['question']['criticalQuestion'])
        next(row for row in self.answers['answers'] if row['questionId'] == identifier).update(
            status='ambiguous', uncertaintyKo='한국어에서 관계를 확정할 수 없다.')
        self.freeze()
        judgment = next(row for row in self.judgments['judgments'] if row['questionId'] == identifier)
        judgment['verdict'] = 'incorrect'
        result = self.grade()
        self.assertEqual((result['overall']['incorrect'], result['overall']['unresolved']), (1, 0))
        self.assertEqual(result['answerStatusCounts']['ambiguous'], 1)
        self.assertTrue(result['answerabilityGate']['passed'])
        judgment['verdict'] = 'correct'
        self.assertFalse(self.grade('conflict.json')['answerabilityGate']['passed'])

    def test_fresh_answerer_and_independent_manual_judge_required(self):
        self.prepare()
        self.write_answers()
        for field, value in [('forkTurns', 'all'), ('sourceOrKeyPreviouslyViewed', True), ('priorJudgmentsViewed', True),
                             ('isQuestionAuthorOrPacketPreparer', True), ('otherCandidateOutputsViewed', True)]:
            changed = copy.deepcopy(self.answers)
            changed['provenance'][field] = value
            with self.assertRaises(ValueError): g.validate_answers(changed, self.bundle)
        self.freeze()
        for field, value in [('id', 'synthetic-answerer'), ('sessionId', 'synthetic-answer-session'), ('automaticExactMatch', True)]:
            changed = copy.deepcopy(self.judgments)
            changed['judge'][field] = value
            with self.assertRaises(ValueError): g.validate_judgments(changed, self.bundle, self.answers,
                self.judgments['answersSha256'], self.judgments['freezeReceiptSha256'])

    def test_duplicate_unknown_missing_judgments_and_invalid_quotes_refused(self):
        self.prepare()
        self.write_answers()
        for mutate in [lambda d: d['answers'].append(d['answers'][0]), lambda d: d['answers'][0].update(questionId='unknown'),
                       lambda d: d['answers'][0].update(targetEvidenceQuotes=['없는 구절']),
                       lambda d: d['answers'][0].update(contextEvidenceQuotes=['추가된 문맥'])]:
            changed = copy.deepcopy(self.answers)
            mutate(changed)
            with self.assertRaises(ValueError): g.validate_answers(changed, self.bundle)
        self.freeze()
        self.judgments['judgments'].pop()
        with self.assertRaises(ValueError): self.grade()

    def test_freeze_binding_actual_file_order_and_existing_output_refused(self):
        self.prepare()
        self.write_answers()
        self.freeze()
        original = self.answer_path.read_bytes()
        self.answer_path.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'frozen_answers'): self.grade()
        self.answer_path.write_bytes(original)
        original_sha = self.judgments['freezeReceiptSha256']
        self.judgments['freezeReceiptSha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'hash_binding'): self.grade()
        self.judgments['freezeReceiptSha256'] = original_sha
        put(self.judgment_path, self.judgments)
        old = self.receipt_path.stat().st_mtime_ns - 10000000000
        os.utime(self.judgment_path, ns=(old, old))
        with self.assertRaisesRegex(ValueError, 'predates_freeze'):
            g.grade(self.packet, self.answer_path, self.receipt_path, self.judgment_path, self.output_root / 'result.json')
        self.grade()
        with self.assertRaisesRegex(ValueError, 'new_output'): self.grade()
        with self.assertRaisesRegex(ValueError, 'new_output'): self.prepare()

    def test_rehashed_private_question_or_changed_producer_still_refused(self):
        self.prepare()
        key_path, manifest_path = self.packet / 'private/private-key.json', self.packet / 'private/manifest.json'
        key, manifest = get(key_path), get(manifest_path)
        key['rows'][0]['question']['criticalQuestion'] = False
        put(key_path, key)
        manifest['privateKeySha256'] = p.sha(key_path.read_bytes())
        put(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, 'private_question_changed'): g.load_bundle(self.packet)

    def test_zero_denominator_and_empty_answers_are_never_perfect(self):
        self.assertEqual(g.statistics([])['status'], 'not_evaluated')
        self.assertIsNone(g.statistics([])['correctRate'])
        self.prepare()
        self.write_answers()
        self.answers['answers'] = []
        self.freeze()
        result = self.grade()
        self.assertEqual((result['overall']['correct'], result['overall']['incorrect'], result['overall']['missingAnswers']), (0, 36, 36))
        self.assertFalse(result['answerabilityGate']['passed'])


if __name__ == '__main__':
    unittest.main()
