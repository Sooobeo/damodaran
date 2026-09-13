import copy
import unittest

import complete_tg27_root_review as c
from test_partial_tg27_review import PartialReviewTests


class CompletedRootReviewTests(unittest.TestCase):
    def fixture(self):
        source, terms, prediction, raw, review = PartialReviewTests().fixture()
        source['cohort'] = 'dev18'
        prediction['checks'] = {'numbersPreserved': False}
        receipt = dict(id=source['id'], cohort='dev18', run='fixed-run', partialOnly=True,
                       fullRunValidated=False, fullCohortScoreProduced=False, qualityAccepted=False,
                       humanReviewed=False, independentBlindReview=False, koreanOnlyAnswersProduced=False,
                       trainingUseAllowed=False, nativeCalls=0,
                       selectedPredictionSha256=c.partial.sha(c.partial.packed(prediction)),
                       actualAutomaticChecks=prediction['checks'])
        snapshot = {'receipt.json': receipt, 'source.json': copy.deepcopy(source),
                    'source-terms.json': copy.deepcopy(terms), 'prediction.json': copy.deepcopy(prediction),
                    'response.json': raw, 'manual-review.json': review}
        return source, terms, prediction, snapshot

    def closed(self):
        summary = dict(version='translategemma-large-screen-v5', status='completed', count=18,
                       recordedCount=18, expectedCount=18, ramBudgetGiB=8, childProcessStopped=True)
        awake = dict(childStopped=True, released=True, exitCode=0)
        return summary, awake

    def test_partial_or_failed_generation_cannot_be_promoted(self):
        for change in ({'count':15}, {'status':'failed'}, {'recordedCount':17}, {'expectedCount':6}):
            summary, awake = self.closed()
            summary.update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'completed_full_cohort_required'):
                c.check_closed(summary, awake, 'dev18')

    def test_success_requires_wrapper_and_native_closure(self):
        c.check_closed(*self.closed(), 'dev18')
        for key, value in [('childStopped',False), ('released',False), ('exitCode',1), ('exitCode',False)]:
            summary, awake = self.closed()
            awake[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'successful_owned_closure_required'):
                c.check_closed(summary, awake, 'dev18')

    def test_completed_row_must_match_original_snapshot(self):
        source, terms, prediction, snapshot = self.fixture()
        c.check_snapshot(source, terms, prediction, snapshot, 'fixed-run')
        snapshot['prediction.json']['translation'] = '다른 번역'
        with self.assertRaisesRegex(ValueError, 'snapshot_does_not_match_completed_run'):
            c.check_snapshot(source, terms, prediction, snapshot, 'fixed-run')

    def test_receipt_cannot_claim_retroactive_blind_review(self):
        for field in ['independentBlindReview','humanReviewed','fullRunValidated','qualityAccepted','koreanOnlyAnswersProduced']:
            source, terms, prediction, snapshot = self.fixture()
            snapshot['receipt.json'][field] = True
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'partial_snapshot_provenance_changed'):
                c.check_snapshot(source, terms, prediction, snapshot, 'fixed-run')

    def test_severity_unresolved_and_multiple_quotes_are_retained(self):
        source, terms, prediction, snapshot = self.fixture()
        review = snapshot['manual-review.json']
        review['severity'] = 'unresolved'
        review['propositionChecks'][0].update(preserved=None, targetQuotes=['비용', '비용.'])
        review['termJudgments'][0]['verdict'] = 'unresolved'
        events = c.events_for(source, terms, prediction, review, 'dev18', 'fixed-run', [])
        self.assertEqual(events[0]['severity'], 'unresolved')
        self.assertEqual([q['targetQuote'] for q in events[0]['observationQuotes']], ['비용','비용.'])
        self.assertEqual(events[0]['review'], review)
        self.assertFalse(events[0]['independentBlindReview'])
        self.assertEqual(events[1]['verdict'], 'unresolved')

    def test_term_success_does_not_turn_major_meaning_error_into_acceptance(self):
        reviews = []
        for i, sid in enumerate(c.verified_run.COHORTS['dev18']):
            count = 7 if i == 0 else 5
            reviews.append(dict(id=sid, severity='major' if i == 0 else 'none',
                                termJudgments=[dict(verdict='correct')] * count))
        result = c.summarize_reviews('dev18', reviews)
        self.assertEqual(result['termCounts'], {'correct':92})
        self.assertEqual(result['materialErrorParagraphs'], 1)
        self.assertFalse(result['qualityAccepted'])
        self.assertEqual(result['questionAnswerability'], 'not_evaluated')
        with self.assertRaisesRegex(ValueError, 'complete_ordered_manual_review_coverage_required'):
            c.summarize_reviews('dev18', reviews[:-1])
        reviews[-1]['termJudgments'] = []
        with self.assertRaisesRegex(ValueError, 'fixed_term_denominator_differs'):
            c.summarize_reviews('dev18', reviews)


if __name__ == '__main__':
    unittest.main()
