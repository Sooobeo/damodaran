"""Evidence gates and append idempotency; all ledger writes use isolated temp roots."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import append_evidence as a
from input_execution_v1 import evaluation as e
from input_execution_v1 import relations
from input_execution_v1.test_evaluation import fixture,reviews_for


def complete_fixture():
    bundle,outputs,*_ = fixture()
    source,answers,grades = reviews_for(bundle)
    report = e.aggregate(bundle,source,answers,grades)
    refs = [{'path':'fixture/report.json','sha256':'a'*64,'pointer':''}]
    return {'bundle':bundle,'report':report,'outputs':outputs,'relations':relations.diagnose_rows(outputs),
            'evidenceRefs':refs,'relationEvidenceRefs':[{'path':'fixture/relations.json','sha256':'b'*64,'pointer':''}],
            'run':'fixture/one-run','graph':Mock(files={'fixture/report.json':'a'*64})}


def create_ledger(root):
    folder = root/a.LEDGER_PATH
    events = [a.ledger.review_event('fixture-source','Original','번역','Context','fixture-system','fixture-run',
                'general16','fixture-v1',{'severity':'none','errors':[]},[{'path':'fixture/evidence','sha256':'f'*64}]),
              {'version':a.ledger.VERSION,'kind':'relation_observation','fixture':True}]
    a.ledger.append_events(folder,events)
    snapshot = a.ledger_snapshot(folder)
    baseline = {'events':2,'translationReviews':1,'inventorySha256':snapshot['inventorySha256']}
    return folder,snapshot,baseline


class AppendEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.complete = complete_fixture()

    def data(self):
        # Mock graph carries no native/database action.
        return deepcopy(self.complete)

    def test_events_are_two_distinct_observation_kinds_with_explicit_links(self):
        data = self.data()
        events = a.build_events(data)
        self.assertEqual(len(events),128)
        inputs = {r['reviewId']:r for r in events if r['kind']=='input_preparation_observation'}
        relation_events = [r for r in events if r['kind']=='relation_observation']
        self.assertEqual((len(inputs),len(relation_events)),(64,64))
        for event in relation_events:
            original = inputs[event['reviewId']]
            self.assertEqual(event['linkedInputPreparationEventIds'],[a.ledger.sha(a.ledger.packed(original))])
            self.assertEqual(event['outputId'],original['outputId'])
            self.assertFalse(event['translationErrorAdded'])
            self.assertEqual(event['baselineReviewEventIds'],[])
        self.assertNotIn('translation_review',{r['kind'] for r in events})
        self.assertNotIn('question_judgment',{r['kind'] for r in events})

    def test_relation_join_and_missing_observation_refused(self):
        data = self.data(); data['relations']['observations'][0]['translationSha256']='0'*64
        with self.assertRaisesRegex(ValueError,'join_changed'):
            a.build_events(data)
        data = self.data(); data['relations']['observations'].pop()
        with self.assertRaisesRegex(ValueError,'observation_inventory'):
            a.build_events(data)

    def test_unresolved_source_or_grade_and_reduced_denominator_refused(self):
        for field,value in (('complete',False),('sourceReviewComplete',False),('questionsComplete',False),('unresolved',True)):
            data = self.data(); data['report']['rows'][0][field]=value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError,'unresolved_or_incomplete'):
                a.build_events(data)
        data = self.data(); data['report']['counts']['expectedQuestions']=126
        with self.assertRaisesRegex(ValueError,'denominators'):
            a.build_events(data)

    def test_repeated_event_construction_is_byte_identical(self):
        first = a.build_events(self.data())
        second = a.build_events(self.data())
        self.assertEqual(a.ledger.packed(first),a.ledger.packed(second))
        self.assertEqual(len({a.ledger.sha(a.ledger.packed(r)) for r in first}),128)

    def test_report_totals_and_actual_judgment_quotes_are_revalidated(self):
        data = self.data(); source,answers,grades = reviews_for(data['bundle'])
        a.revalidate_report(data['bundle'],source,answers,grades,data['report'])
        source[0]['propositions'][0]['verdict']='damaged'
        with self.assertRaisesRegex(ValueError,'not_reproducible'):
            a.revalidate_report(data['bundle'],source,answers,grades,data['report'])
        source,answers,grades = reviews_for(data['bundle'])
        grades[0]['questions'][0]['answerEvidence'][0]['text']='fabricated quote'
        with self.assertRaisesRegex(ValueError,'span_not_exact'):
            a.revalidate_report(data['bundle'],source,answers,grades,data['report'])

    def test_relation_changed_warning_is_not_accepted_as_a_saved_observation(self):
        data = self.data()
        a.revalidate_relations(data['relations'],data['outputs'])
        data['relations']['observations'][0]['diagnostic']['warning']=True
        with self.assertRaisesRegex(ValueError,'not_reproducible'):
            a.revalidate_relations(data['relations'],data['outputs'])

    def test_isolated_append_then_repeat_zero_preserves_every_prior_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,before,baseline = create_ledger(Path(tmp))
            events = a.build_events(self.data())
            first = a.append_prevalidated(folder,events,before,baseline)
            self.assertEqual(first['imported'],{'added':128,'reused':0})
            middle = a.ledger_snapshot(folder)
            second = a.append_prevalidated(folder,events,middle,baseline)
            self.assertEqual(second['imported'],{'added':0,'reused':128})
            self.assertEqual(first['afterEventCount'],second['afterEventCount'])
            after = a.ledger_snapshot(folder)
            self.assertTrue(all(after['files'][key]==raw for key,raw in before['files'].items()))
            self.assertEqual(after['summary']['eventKindCounts']['translation_review'],1)

    def test_isolated_partial_append_is_resumable_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,before,baseline = create_ledger(Path(tmp))
            events = a.build_events(self.data())
            a.ledger.append_events(folder,events[:4])
            partial = a.ledger_snapshot(folder)
            receipt = a.append_prevalidated(folder,events,partial,baseline)
            self.assertEqual(receipt['imported'],{'added':124,'reused':4})
            self.assertTrue(receipt['allPriorEventBytesUnchanged'])

    def test_corrupt_existing_event_refused_before_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,before,baseline = create_ledger(Path(tmp))
            path = next((folder/'events').glob('*.json'))
            path.write_bytes(b'corrupt')
            with patch.object(a.ledger,'append_events') as writer:
                with self.assertRaisesRegex(ValueError,'event_digest_mismatch'):
                    a.append_prevalidated(folder,a.build_events(self.data()),before,baseline)
                writer.assert_not_called()

    def test_unknown_new_event_does_not_disappear_from_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,before,baseline = create_ledger(Path(tmp))
            a.ledger.append_events(folder,[{'version':a.ledger.VERSION,'kind':'relation_observation','unrelated':True}])
            changed = a.ledger_snapshot(folder)
            with self.assertRaisesRegex(ValueError,'baseline_inventory_changed'):
                a.require_baseline(changed,a.build_events(self.data()),baseline)

    def test_default_prepare_does_not_call_ledger_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder,before,baseline = create_ledger(root)
            data = self.data()
            with patch.object(a,'validate_final_evidence',return_value=data), patch.dict(a.BASELINE,baseline), \
                    patch.object(a.ledger,'append_events') as writer:
                result = a.prepare(folder='fixture/s5',outputs='fixture/predictions.jsonl',source_reviews='fixture/source.json',
                    grades='fixture/grades.json',report_path='fixture/report.json',relation_folder='fixture/relations',
                    destination=root/a.BASE/'prepared',root=root)
                writer.assert_not_called()
            self.assertEqual(result['ledgerEventsAppended'],0)
            self.assertEqual(result['status'],'prepared')
            self.assertEqual(a.ledger_snapshot(folder)['files'],before['files'])

    def test_missing_final_s4_gate_cannot_prepare_or_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); destination=root/a.BASE/'prepared'
            with patch.object(a,'verify_evaluation_freeze',return_value={'s4PlanSha256':'a'*64}), \
                    patch.object(e,'validate_run_completion',side_effect=ValueError('s4_run_not_complete')), \
                    patch.object(a.ledger,'append_events') as writer:
                with self.assertRaisesRegex(ValueError,'s4_run_not_complete'):
                    a.prepare(folder='fixture/s5',outputs='fixture/predictions.jsonl',source_reviews='fixture/source.json',
                        grades='fixture/grades.json',report_path='fixture/report.json',relation_folder='fixture/relations',
                        destination=destination,append=True,root=root)
                writer.assert_not_called()
                self.assertFalse(destination.exists())

    def test_evaluation_freeze_exact_ten_inventory_and_hash_refused(self):
        graph = Mock()
        freeze = {'version':'input-execution-v1-evaluation-tool-freeze-v1','s1FreezeSha256':e.FREEZE_SHA,
                  's2ManifestSha256':e.S2_SHA,'files':[{'path':'scripts/model-comparison/input_execution_v1/'+name,
                    'sha256':'a'*64} for name in sorted(a.FROZEN_NAMES)]+[{'path':a.GUIDE,'sha256':'b'*64}]}
        graph.json.return_value = deepcopy(freeze)
        a.verify_evaluation_freeze(graph)
        self.assertEqual(graph.verify.call_count,12)
        graph.json.return_value['files'].pop()
        with self.assertRaisesRegex(ValueError,'ten_files'):
            a.verify_evaluation_freeze(graph)
        graph.json.return_value=freeze
        graph.verify.side_effect=ValueError('evidence_hash_mismatch')
        with self.assertRaisesRegex(ValueError,'hash_mismatch'):
            a.verify_evaluation_freeze(graph)

    def test_streaming_graph_detects_changed_file_and_repeated_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'weights.bin'; path.write_bytes(b'x'*(2*1024*1024+1))
            graph=a.EvidenceGraph(root)
            ref=graph.verify(path)
            self.assertEqual(ref['sha256'],e.sha(path.read_bytes()))
            path.write_bytes(b'y'*(2*1024*1024+1))
            with self.assertRaisesRegex(ValueError,'hash_mismatch'):
                graph.verify_unchanged()


if __name__ == '__main__':
    unittest.main()
