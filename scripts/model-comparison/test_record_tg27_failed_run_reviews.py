import copy
from pathlib import Path
import tempfile
import unittest

import record_tg27_failed_run_reviews as r
import test_complete_tg27_root_review as fixtures


class FailedRunDiagnosticTests(unittest.TestCase):
    def failure(self):
        proof = dict(version='tg27-user-retry-17-standby-failure-v1',run=r.RUN,
                     completed=16,failed=1,notRun=1,oldCoordinatorStopped=True,
                     temporaryDisplayRequestReleased=True)
        summary = dict(version='translategemma-large-screen-v5',status='failed',
                       count=16,recordedCount=18,expectedCount=18,ramBudgetGiB=8,
                       memoryOrTimeGuardAborted=True,childProcessStopped=True,
                       memoryMonitoring=dict(abortReason='request_time_limit'))
        awake = dict(childStopped=True,released=True,exitCode=1)
        rows = [dict(id=sid,status='completed') for sid in r.IDS]
        rows += [dict(id='LDEV26-017',status='failed'),dict(id='LDEV26-018',status='not_run')]
        return proof,summary,awake,rows

    def events(self):
        source,terms,prediction,snapshot = fixtures.CompletedRootReviewTests().fixture()
        review = snapshot['manual-review.json']
        review.update(severity='unresolved')
        review['propositionChecks'][0].update(preserved=None,targetQuotes=['비용','비용.'])
        review['termJudgments'][0]['verdict'] = 'unresolved'
        return r.events_for(source,terms,prediction,review,[]),review

    def test_only_pinned_failure_shape_is_accepted(self):
        r.check_failure(*self.failure())
        for change in ({'status':'completed'},{'count':18},{'memoryOrTimeGuardAborted':False},
                       {'memoryMonitoring':{'abortReason':None}}):
            parts = self.failure();parts[1].update(change)
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'failed_run_and_owned_closure_required'):
                r.check_failure(*parts)

    def test_open_or_different_failed_run_is_rejected(self):
        for where,change in [(0,{'run':'other'}),(0,{'oldCoordinatorStopped':False}),
                             (2,{'childStopped':False}),(2,{'released':False}),(2,{'exitCode':True})]:
            parts = self.failure();parts[where].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                r.check_failure(*parts)

    def test_failed_or_missing_row_cannot_be_imported_as_completed(self):
        for mode in ['duplicate','failed','swapped','missing']:
            parts=self.failure();rows=parts[3]
            if mode=='duplicate': rows[1]=copy.deepcopy(rows[0])
            elif mode=='failed': rows[0]['status']='failed'
            elif mode=='swapped': rows[16],rows[17]=rows[17],rows[16]
            else: rows.pop()
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'fixed_completed16_failed17_unrun18_required'):
                r.check_failure(*parts)

    def test_every_event_keeps_partial_scope_and_original_observations(self):
        events,review=self.events()
        self.assertEqual(len(events),2)
        self.assertEqual(events[0]['review'],review)
        self.assertEqual([q['targetQuote'] for q in events[0]['observationQuotes']],['비용','비용.'])
        self.assertEqual(events[1]['verdict'],'unresolved')
        for event in events:
            self.assertEqual(event['originalAutomaticChecks'],{'numbersPreserved':False})
            self.assertEqual(event['originalRunStatus'],'failed')
            self.assertEqual(event['reviewVersion'],r.VERSION)
            for field in ['fullRunEvidenceValidated','fullDev18RunValidated','fullCohortScoreProduced',
                          'qualityAccepted','independentBlindReview','trainingUseAllowed','koreanOnlyAnswersProduced']:
                self.assertIs(event[field],False)

    def test_partial_terms_cannot_be_a_whole_cohort_rate(self):
        reviews=[dict(id=sid,severity='major' if i==0 else 'none',
                      termJudgments=[dict(verdict='correct')]*(6 if i==0 else 5))
                 for i,sid in enumerate(r.IDS)]
        report=r.summarize(reviews)
        self.assertEqual(report['termCounts'],{'correct':81})
        self.assertEqual(report['fixedFinanceTermDenominator'],149)
        self.assertEqual(report['unreviewedFinanceTermOccurrences'],68)
        self.assertIsNone(report['wholeCohortTermAccuracy'])
        self.assertFalse(report['qualityAccepted'])
        with self.assertRaisesRegex(ValueError,'all_fixed16_reviews_required'):
            r.summarize(reviews[:-1])
        reviews[0]['termJudgments']=[]
        with self.assertRaisesRegex(ValueError,'fixed_partial_term_count_required'):
            r.summarize(reviews)

    def test_reimport_is_idempotent_and_corruption_is_rejected(self):
        events,_=self.events()
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            self.assertEqual(r.ledger.append_events(folder,events),dict(added=2,reused=0))
            self.assertEqual(r.ledger.append_events(folder,events),dict(added=0,reused=2))
            target=folder/'events'/(r.ledger.sha(r.ledger.packed(events[0]))+'.json')
            target.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'existing_event_corrupted'):
                r.ledger.append_events(folder,events)


if __name__=='__main__':
    unittest.main()
