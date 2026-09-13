import copy
import unittest

import record_tg27_recovery_reviews as r
import test_complete_tg27_root_review as fixtures


class RecoveryReviewTests(unittest.TestCase):
    def fixture(self):
        source,terms,prediction,snapshot=fixtures.CompletedRootReviewTests().fixture()
        snapshot['receipt.json']['version']=r.partial.VERSION
        snapshot['manual-review.json']['version']=r.partial.VERSION
        return source,terms,prediction,snapshot

    def closed(self,cohort):
        count=len(r.COHORTS[cohort])
        return (dict(version='translategemma-large-screen-v5',status='completed',count=count,
                     recordedCount=count,expectedCount=count,ramBudgetGiB=8,childProcessStopped=True),
                dict(childStopped=True,released=True,exitCode=0))

    def test_selected_run_success_never_becomes_dev18_success(self):
        for cohort in r.COHORTS:
            scope=r.scope(cohort)
            self.assertTrue(scope['selectedRunValidated'])
            self.assertEqual(scope['selectedRunScope'],cohort)
            self.assertFalse(scope['fullDev18RunValidated'])
            self.assertEqual(scope['fullReading6RunValidated'],cohort=='reading6')
            self.assertFalse(scope['qualityAccepted'])
        with self.assertRaisesRegex(ValueError,'fixed_recovery_cohort_required'):
            r.scope('dev18')

    def test_exact_selected_run_count_and_successful_closure_required(self):
        for cohort in r.COHORTS:
            r.check_closed(*self.closed(cohort),cohort)
            for at,change in [(0,{'count':18}),(0,{'status':'failed'}),
                              (1,{'childStopped':False}),(1,{'released':False}),(1,{'exitCode':False})]:
                values=self.closed(cohort);values[at].update(change)
                with self.subTest(cohort=cohort,change=change),self.assertRaises(ValueError):
                    r.check_closed(*values,cohort)

    def test_old_or_different_run_receipt_cannot_be_relabelled(self):
        values=self.fixture()
        r.check_snapshot(*values,'fixed-run')
        for change in [{'version':'tg27-partial-root-review-v1'},{'run':'old-failed-run'}]:
            values=self.fixture();values[3]['receipt.json'].update(change)
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'fixed_recovery_snapshot_identity'):
                r.check_snapshot(*values,'fixed-run')

    def test_provenance_and_raw_corruption_are_rejected(self):
        for field in ['humanReviewed','independentBlindReview','fullRunValidated','koreanOnlyAnswersProduced']:
            values=self.fixture();values[3]['receipt.json'][field]=True
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'original_partial_provenance_required'):
                r.check_snapshot(*values,'fixed-run')
        values=self.fixture();values[3]['response.json']['content']='invented'
        with self.assertRaisesRegex(ValueError,'raw_response_differs'):
            r.check_snapshot(*values,'fixed-run')

    def test_automatic_flags_cannot_be_silently_promoted(self):
        values=self.fixture()
        values[3]['receipt.json']['actualAutomaticChecks']={'numbersPreserved':True}
        with self.assertRaisesRegex(ValueError,'original_snapshot_observation_differs'):
            r.check_snapshot(*values,'fixed-run')

    def test_all_selected_terms_and_reviews_required_without_whole_finance_rate(self):
        for cohort in r.COHORTS:
            counts=[5,6] if cohort=='tail2' else [9,9,9,10,10,10]
            reviews=[dict(id=sid,severity='unresolved',termJudgments=[dict(verdict='unresolved')]*n)
                     for sid,n in zip(r.COHORTS[cohort],counts)]
            report=r.summarize(cohort,reviews)
            self.assertIsNone(report['wholeFinanceAccuracy'])
            self.assertFalse(report['qualityAccepted'])
            self.assertEqual(report['questionAnswerability'],'not_evaluated')
            with self.assertRaisesRegex(ValueError,'all_selected_reviews_required'):
                r.summarize(cohort,reviews[:-1])
            reviews[0]['termJudgments']=[]
            with self.assertRaisesRegex(ValueError,'fixed_selected_term_coverage_required'):
                r.summarize(cohort,reviews)


if __name__=='__main__':
    unittest.main()
