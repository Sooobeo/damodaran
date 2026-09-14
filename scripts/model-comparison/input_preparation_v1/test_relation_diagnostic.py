"""Diagnostic accounting, immutable evidence, and existing-ledger idempotency."""
import tempfile
from pathlib import Path
import unittest

import run_relation_diagnostic_v2 as diagnostic
from relation_checks_v2 import inspect_relations


class DiagnosticTests(unittest.TestCase):
    def test_uncovered_is_explicit_and_unresolved_not_pass(self):
        assessed=inspect_relations("The committee met.","위원회가 회의를 열었다.")
        rows=[{"diagnostic":assessed,"baselineMaterialError":material} for material in (True,False,None)]
        for kind in (None,*diagnostic.TYPES):
            scored=diagnostic.score(rows,kind)
            self.assertEqual((scored["TP"],scored["FP"],scored["FN"],scored["TN"]),(0,0,1,1))
            self.assertEqual(scored["baselineUnresolved"],1)
            self.assertEqual(scored["supportedRows"],0)
            self.assertEqual(scored["uncoveredMaterialErrors"],1)
            self.assertEqual(scored["uncoveredNoMaterialErrors"],1)

    def test_warning_against_each_baseline_is_separate(self):
        assessed=inspect_relations("The fee is $25.","수수료는 25유로다.")
        a=diagnostic.score([{"diagnostic":assessed,"baselineMaterialError":True}])
        b=diagnostic.score([{"diagnostic":assessed,"baselineMaterialError":False}])
        self.assertEqual((a["TP"],a["FP"]),(1,0))
        self.assertEqual((b["TP"],b["FP"]),(0,1))

    def test_existing_append_api_reuses_exact_events(self):
        with tempfile.TemporaryDirectory() as folder:
            event={"version":diagnostic.ledger.VERSION,"kind":"relation_observation","observation":"synthetic"}
            a=diagnostic.ledger.append_events(folder,[event])
            b=diagnostic.ledger.append_events(folder,[event])
            self.assertEqual(a,{"added":1,"reused":0})
            self.assertEqual(b,{"added":0,"reused":1})

    def test_evidence_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); path=root/"source.json"; path.write_text("original",encoding="utf-8")
            ev=diagnostic.ledger.Evidence(root); ev.read(path)
            path.write_text("changed",encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"evidence_hash_mismatch"):
                ev.verify_unchanged()

    def test_diagnostic_existing_file_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"result.json"
            diagnostic.write_new(path,{"result":1})
            with self.assertRaises(FileExistsError):
                diagnostic.write_new(path,{"result":2})


if __name__=="__main__":unittest.main()
