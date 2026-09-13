import copy
from pathlib import Path
import tempfile
import unittest
import ledger as l

class LedgerTests(unittest.TestCase):
    def event(self, severity=2, review='original'):
        return l.review_event('s1','A pays B.','B가 A에게 지급한다.','','model','run','dev18',review,
            {'severity':severity,'errors':[{'category':'semantic_roles','sourceSpan':'A pays B','targetSpan':'B가 A에게','reasonKo':'방향 변경'}]},[])

    def test_rerun_is_idempotent_and_adjudication_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'ledger'
            first=self.event(); next_review=self.event(1,'adjudication')
            self.assertEqual(l.append_events(folder,[first]),{'added':1,'reused':0})
            self.assertEqual(l.append_events(folder,[first]),{'added':0,'reused':1})
            self.assertEqual(l.append_events(folder,[first,next_review]),{'added':1,'reused':1})
            report=l.summarize(folder)
            self.assertEqual(report['eventCount'],2)
            self.assertEqual(len(report['multipleReviewHistories']),1)
            self.assertEqual(len(report['slices']),2)
            self.assertTrue(report['reviewRoundsAreNotSummedAsNewModelErrors'])

    def test_corrupt_event_is_not_overwritten_or_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'ledger'; event=self.event()
            l.append_events(folder,[event]); path=next((folder/'events').glob('*.json'))
            path.write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'existing_event_corrupted'): l.append_events(folder,[event])
            with self.assertRaisesRegex(ValueError,'event_digest_mismatch'): l.summarize(folder)
            self.assertEqual(path.read_bytes(),b'corrupted')

    def test_source_hash_and_test_cohort_rejected(self):
        event=self.event()
        with self.assertRaisesRegex(ValueError,'only_development_cohorts_allowed'):
            l.review_event('s','a','b','','m','r','test','v',{'severity':0},[])
        with self.assertRaisesRegex(ValueError,'review_source_mismatch'):
            l.review_event('s','a','b','','m','r','dev18','v',{'severity':0,'sourceSha256':'wrong'},[])
        self.assertTrue(event['observationQuotes'][0]['sourceExact'])
        self.assertFalse(event['causeHypotheses'][0]['modelInternalCauseConfirmed'])

    def test_evidence_change_and_escape_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); path=root/'input.json'; path.write_bytes(b'{}')
            ev=l.Evidence(root); ev.read(path)
            path.write_bytes(b'{"changed":true}')
            with self.assertRaisesRegex(ValueError,'evidence_hash_mismatch'): ev.verify_unchanged()
            with self.assertRaisesRegex(ValueError,'evidence_path_redirected'): ev.path(root/'..'/'outside')

    def test_writer_lock_is_preserved_when_busy(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp); lock=folder/'.writer.lock'; lock.write_text('other writer')
            with self.assertRaises(FileExistsError): l.append_events(folder,[self.event()])
            self.assertEqual(lock.read_text(),'other writer')

    def test_output_link_keeps_reviews_but_rejects_different_translations(self):
        first=self.event(); second=self.event(1,'adjudication')
        linked=l.link_output([first,second],'s1','model',first['translationSha256'])
        self.assertEqual(len(linked['linkedReviewEventIds']),2)
        altered=copy.deepcopy(first); altered['outputId']='different'; altered['translationSha256']='different'
        with self.assertRaisesRegex(ValueError,'ambiguous_output_link'):l.link_output([first,altered],'s1','model')
        with self.assertRaisesRegex(ValueError,'ambiguous_output_link'):l.link_output([first],'s1','wrong_model')

    def test_unrun_qe_is_not_counted_as_false_negative(self):
        linked=l.link_output([self.event()],'s1','model')
        missed=l.qe_event(linked,'checker','v',dict(completed=True,warning=False,materialError=True),[])
        unrun=l.qe_event(linked,'checker','v',dict(completed=False,warning=False,materialError=True),[])
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);l.append_events(folder,[self.event(),missed,unrun])
            report=l.summarize(folder)
        self.assertEqual(report['qeSlices'][0]['observations'],{'false_negative':1,'not_observed':1})
        self.assertEqual(report['slices'][0]['reviewRows'],1)
        self.assertIsNone(unrun['warning'])

    def test_runtime_and_question_results_do_not_increment_translation_errors(self):
        event=self.event(); linked=l.link_output([event],'s1','model')
        question=dict(version=l.VERSION,kind='question_judgment',**linked,effectiveVerdict='incorrect',criticalQuestion=True)
        runtime=dict(version=l.VERSION,kind='runtime_observation',runtimeCategory='user_cancellation',translationQualityFailureInferred=False)
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);l.append_events(folder,[event,question,runtime]); report=l.summarize(folder)
        self.assertEqual(report['slices'][0]['severityCounts'],{'major':1})
        self.assertEqual(report['questionSlices'][0]['verdicts'],{'incorrect':1})
        self.assertEqual(report['runtimeCategories'],{'user_cancellation':1})

if __name__=='__main__': unittest.main()
