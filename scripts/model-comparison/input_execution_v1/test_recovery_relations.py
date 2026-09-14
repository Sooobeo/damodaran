"""Synthetic recovery observations; never starts generation or writes a ledger."""
from copy import deepcopy
from pathlib import Path
import sys,unittest
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent));sys.path.insert(0,str(HERE))
from input_execution_v1 import recovery_relations as r
from test_relations import rows as base_rows

def rows():
    result=base_rows()
    for i,row in enumerate(result):
        row.update(runId='attempt-001' if i<38 else 'recovery-attempt-001',
                   producerVersion=r.ORIGINAL_VERSION if i<38 else r.RECOVERY_VERSION)
    return result

class RecoveryRelationsTests(unittest.TestCase):
    def test_two_string_boundary_original_run_status_and_rows_preserved(self):
        inputs=rows();before=deepcopy(inputs)
        with patch.object(r.checker,'inspect_relations',wraps=r.checker.inspect_relations) as call:
            result=r.diagnose_rows(inputs)
        self.assertEqual(inputs,before);self.assertEqual(call.call_count,64)
        self.assertTrue(all(len(c.args)==2 and not c.kwargs and all(isinstance(v,str) for v in c.args) for c in call.call_args_list))
        observations=result['observations']
        self.assertEqual(sum(v['originalRunStatus']=='failed' for v in observations),38)
        self.assertEqual(sum(v['originalRunStatus']=='completed' for v in observations),26)
        self.assertEqual(len({v['observationId'] for v in observations}),64)

    def test_denominators_warning_and_meaning_are_separate(self):
        result=r.diagnose_rows(rows())
        self.assertEqual(result['relationSlots'],384)
        self.assertEqual(result['configurations']['C0']['warningOutputs'],16)
        self.assertEqual(result['configurations']['C1']['warningOutputs'],0)
        self.assertEqual(result['generationQualityErrors']['status'],'not_evaluated')
        self.assertIsNone(result['precisionRecall']['precision'])
        self.assertFalse(result['semanticApproval'])

    def test_incomplete_duplicate_false_origin_and_text_tampering_fail(self):
        original=rows();cases=[original[:-1],original[:-1]+[original[0]]]
        for index,field,value in ((0,'runId','recovery-attempt-001'),(38,'runId','attempt-001'),
                                  (38,'producerVersion',r.ORIGINAL_VERSION),(3,'sourceSha256','0'*64),
                                  (50,'status','failed'),(39,'runId','recovery-attempt-002')):
            altered=deepcopy(original);altered[index][field]=value;cases.append(altered)
        for case in cases:
            with self.subTest(count=len(case)),self.assertRaises(ValueError):r.diagnose_rows(case)

    def test_no_warning_does_not_certify_semantics(self):
        inputs=rows()
        for row in inputs:
            row.update(source='The committee met.',translation='위원회가 회의를 열었다.')
            row['sourceSha256']=r.e.sha(row['source']);row['translationSha256']=r.e.sha(row['translation'])
        result=r.diagnose_rows(inputs)
        self.assertTrue(result['unsupportedAndUndeterminedAreNotPass'])
        self.assertFalse(result['fullSemanticCoverage'])
        self.assertTrue(all(v['statusCounts']['unsupported']==96 for v in result['configurations'].values()))

if __name__=='__main__':unittest.main()
