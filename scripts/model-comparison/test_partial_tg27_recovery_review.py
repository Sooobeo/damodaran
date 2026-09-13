import unittest

import partial_tg27_recovery_review as p


class RecoveryPartialReviewTests(unittest.TestCase):
    def test_only_fixed_unfinished_development_ids_are_supported(self):
        self.assertEqual(p.recovery_run_name('dev18', 'LDEV26-017'), p.TAIL_RUN)
        self.assertEqual(p.recovery_run_name('dev18', 'LDEV26-018'), p.TAIL_RUN)
        with self.assertRaisesRegex(ValueError, 'only_fixed_recovery_rows_allowed'):
            p.recovery_run_name('dev18', 'LDEV26-001')

    def test_reading_scope_cannot_be_swapped_with_development(self):
        self.assertEqual(p.recovery_run_name('reading6', 'REAL26-006'), p.READING_RUN)
        with self.assertRaises(ValueError): p.recovery_run_name('reading6', 'LDEV26-017')
        with self.assertRaises(ValueError): p.recovery_run_name('reading6', 'REAL26-007')

    def fixture(self):
        source = dict(id='LDEV26-001', source='A fee.', context='')
        hashes = dict(sourceSha256=p.sha(b'A fee.'), contextSha256=p.sha(b''),
                      targetSha256=p.sha('비용.'.encode()))
        inventory = dict(id=source['id'], sourceSha256=hashes['sourceSha256'],
                         contextSha256=hashes['contextSha256'], occurrences=[
                             dict(occurrenceId='term-1', span=dict(start=2, end=5, quote='fee'))])
        raw = dict(content='비용.', tokens=[7, 106], tokens_predicted=2, tokens_evaluated=3,
                   stop_type='eos', truncated=False, generation_settings={'temperature': 0},
                   timings={'predicted_n': 2}, prompt='Translate A fee.')
        pred = dict(id=source['id'], translation=raw['content'], **hashes, status='completed',
                    truncated=False, stopType='eos', terminalTokenId=106,
                    terminationClass='original_model_eog', outputTokenIds=raw['tokens'],
                    generatedTokens=2, inputTokens=3, actualGenerationSettings=raw['generation_settings'],
                    timings=raw['timings'], promptSha256=p.sha(raw['prompt'].encode()))
        review = dict(version=p.VERSION, id=source['id'], **hashes, humanReviewed=False,
                      independentBlindReview=False, reviewerHadSeenSourceAndPriorOutputs=True,
                      trainingUseAllowed=False, koreanOnlyAnswersProduced=False,
                      severity='none', reasonKo='문맥의 비용 뜻을 보존한다.',
                      propositionChecks=[dict(sourceQuote='A fee.', targetQuotes=['비용.'],
                                              preserved=True, reasonKo='비용 개념이다.')],
                      termJudgments=[dict(occurrenceId='term-1', verdict='correct',
                                          targetQuotes=['비용'], reasonKo='비용 개념이다.')])
        return source, inventory, pred, raw, review

    def test_completed_response_accepts_manually_judged_evidence(self):
        p.check_observation(*self.fixture())

    def test_raw_content_change_is_rejected(self):
        values = self.fixture(); values[3]['content'] = '다른 내용.'
        with self.assertRaisesRegex(ValueError, 'raw_response_differs'):
            p.check_observation(*values)

    def test_false_blind_or_answer_provenance_is_rejected(self):
        for field in ('independentBlindReview', 'koreanOnlyAnswersProduced', 'humanReviewed'):
            values = self.fixture(); values[4][field] = True
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'review_provenance'):
                p.check_observation(*values)

    def test_invented_quote_is_rejected(self):
        values = self.fixture(); values[4]['termJudgments'][0]['targetQuotes'] = ['수수료']
        with self.assertRaisesRegex(ValueError, 'term_quote_not_exact'):
            p.check_observation(*values)

    def test_terms_cannot_be_dropped(self):
        values = self.fixture(); values[4]['termJudgments'] = []
        with self.assertRaisesRegex(ValueError, 'term_coverage'):
            p.check_observation(*values)

    def test_numeric_flag_cannot_stand_in_for_boolean_judgment(self):
        values = self.fixture(); values[4]['propositionChecks'][0]['preserved'] = 1
        with self.assertRaisesRegex(ValueError, 'proposition_judgment'):
            p.check_observation(*values)

    def test_incomplete_live_line_is_not_an_observation(self):
        raw = b'{"id":"one"}\n{"id":"two"'
        self.assertEqual(p.completed_line(raw, 'one')['id'], 'one')
        with self.assertRaisesRegex(ValueError, 'completed_row_unavailable'):
            p.completed_line(raw, 'two')

    def test_duplicate_prediction_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate_prediction_id'):
            p.completed_line(b'{"id":"one"}\n{"id":"one"}\n', 'one')

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate_json_key'):
            p.strict_json('{"id":"one","id":"two"}')


if __name__ == '__main__':
    unittest.main()

