import copy
import unittest
import term_review as t
import ledger as l

class TermReviewTests(unittest.TestCase):
    def fixture(self):
        term=dict(occurrenceId='s-T001',span=dict(start=0,end=6,quote='equity'))
        row=dict(system='model',run='run',sourceId='s',outputId='out',source='equity',context='',translation='자기자본',
            sourceSha256=l.sha(b'equity'),translationSha256=l.sha('자기자본'.encode()),contextSha256=l.sha(b''),
            cohort='dev18',linkedReviewEventIds=[],terms=[term],evidence=[])
        packet=dict(version=t.VERSION,systems=['model'],rows=[row],rootSourceReviewEvidence={})
        j=dict(system='model',occurrenceId='s-T001',sourceSha256=row['sourceSha256'],translationSha256=row['translationSha256'],
            verdict='correct',reasonKo='자금 조달 맥락의 자기자본 뜻을 보존한다.',targetQuotes=['자기자본'])
        return packet,dict(version=t.VERSION,packetSha256='frozen',rows=[j])

    def test_manual_verdict_only_and_zero_denominator(self):
        packet,j=self.fixture();events,report=t.validate(packet,j,'frozen')
        self.assertEqual(events[0]['verdict'],'correct')
        self.assertFalse(report['fullLearningReadinessAccepted'])
        self.assertIsNone(report['rows'][1]['correctRate'])
        self.assertFalse(report['rows'][1]['termOnly95GatePassed'])

    def test_missing_duplicate_and_extra_rejected(self):
        for change,reason in [('missing','term_coverage'),('duplicate','term_coverage'),('extra','term_duplicate_or_extra')]:
            packet,j=self.fixture()
            if change=='missing':j['rows']=[]
            elif change=='duplicate':j['rows']*=2
            else:j['rows'][0]['occurrenceId']='other'
            with self.assertRaisesRegex(ValueError,reason):t.validate(packet,j,'frozen')

    def test_fabricated_quote_and_wrong_hash_rejected(self):
        for field,value,reason in [('targetQuotes',['형평성'],'term_quote_not_exact'),('translationSha256','other','term_text_identity')]:
            packet,j=self.fixture();j['rows'][0][field]=value
            with self.assertRaisesRegex(ValueError,reason):t.validate(packet,j,'frozen')
        packet,j=self.fixture()
        with self.assertRaisesRegex(ValueError,'term_packet_hash'):t.validate(packet,j,'changed')

    def test_unresolved_is_not_pass_and_omission_can_have_no_target_quote(self):
        packet,j=self.fixture();j['rows'][0]['verdict']='unresolved';j['rows'][0]['targetQuotes']=[]
        events,report=t.validate(packet,j,'frozen')
        self.assertEqual(report['rows'][0]['counts'],{'unresolved':1})
        self.assertFalse(report['rows'][0]['termOnly95GatePassed'])
        j['rows'][0]['verdict']='correct'
        with self.assertRaisesRegex(ValueError,'correct_term_evidence_required'):t.validate(packet,j,'frozen')

if __name__=='__main__':unittest.main()
