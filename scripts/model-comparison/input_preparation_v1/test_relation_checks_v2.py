"""Frozen counterexamples, Unicode evidence, isolation, and metamorphic checks."""
import hashlib
import inspect
import json
from pathlib import Path
import unittest

from relation_checks_v2 import inspect_relations, TYPES

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "content/model-comparison/input-preparation-v1-s2s3/relation-fixtures-v1.json"
FREEZE = FIXTURES.with_name("relation-fixtures-freeze-v1.json")


class RelationTests(unittest.TestCase):
    def test_v2_frozen_natural_notation_regressions(self):
        path=FIXTURES.with_name("relation-fixtures-v2-extra.json")
        receipt=json.loads(path.with_name("relation-fixtures-freeze-v2-extra.json").read_bytes())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),receipt["fixtureSha256"])
        for case in json.loads(path.read_bytes())["cases"]:
            with self.subTest(case=case["id"]):
                row=next(r for r in inspect_relations(case["source"],case["translation"])["relations"] if r["type"]==case["type"])
                self.assertEqual(row["status"],case["expected"],row)

    def test_v2_independent_review_counterexamples(self):
        path=FIXTURES.with_name("relation-fixtures-v2-review.json")
        receipt=json.loads(path.with_name("relation-fixtures-freeze-v2-review.json").read_bytes())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),receipt["fixtureSha256"])
        for case in json.loads(path.read_bytes())["cases"]:
            with self.subTest(case=case["id"]):
                row=next(r for r in inspect_relations(case["source"],case["translation"])["relations"] if r["type"]==case["type"])
                self.assertEqual(row["status"],case["expected"],row)

    def test_frozen_counterexamples(self):
        receipt = json.loads(FREEZE.read_bytes())
        self.assertEqual(hashlib.sha256(FIXTURES.read_bytes()).hexdigest(), receipt["fixtureSha256"])
        cases = json.loads(FIXTURES.read_bytes())["cases"]
        self.assertEqual(len(cases), receipt["caseCount"])
        for case in cases:
            with self.subTest(case=case["id"]):
                result = inspect_relations(case["source"], case["translation"])
                row = next(r for r in result["relations"] if r["type"] == case["type"])
                self.assertEqual(row["status"], case["expected"], row)
                self.assertEqual(row["warning"], case["expected"] == "supported_conflict")

    def test_exact_utf16_evidence(self):
        for case in json.loads(FIXTURES.read_bytes())["cases"]:
            result = inspect_relations(case["source"], case["translation"])
            for row in result["relations"]:
                for key, text in (("sourceEvidence",case["source"]),("translationEvidence",case["translation"])):
                    for span in row[key]:
                        self.assertEqual(text.encode("utf-16-le")[span["start"]*2:span["end"]*2].decode("utf-16-le"),span["text"])
                if row["warning"]:
                    self.assertTrue(row["sourceEvidence"])
                    self.assertTrue(row["translationEvidence"])

    def test_no_judgment_or_metadata_input(self):
        self.assertEqual(list(inspect.signature(inspect_relations).parameters), ["source","translation"])
        with self.assertRaises(TypeError):
            inspect_relations({"source":"fee","reference":"정답"}, "수수료")
        with self.assertRaises(TypeError):
            inspect_relations("fee", "수수료", judgment=True)

    def test_unsupported_is_not_pass(self):
        result = inspect_relations("The committee met.", "위원회가 회의를 열었다.")
        self.assertEqual(len(result["relations"]),len(TYPES))
        self.assertTrue(all(r["status"] == "unsupported" for r in result["relations"]))
        self.assertFalse(result["completeSemanticAssessment"])

    def test_numeric_role_is_not_bag_of_numbers(self):
        cases = [("Divide $240 by $60.","60달러를 240달러로 나눈다.","division_direction"),
                 ("The rate increases by 7%.","비율은 7%로 증가한다.","start_delta_result")]
        for source,target,kind in cases:
            rows = {r["type"]:r for r in inspect_relations(source,target)["relations"]}
            self.assertEqual(rows[kind]["status"],"supported_conflict")
            self.assertEqual(rows["value_unit"]["status"],"supported_match")

    def test_variable_values_and_word_order(self):
        for a,b in ((41,7),(912,23),(1234,15)):
            source=f"Divide ${a:,} by ${b:,}."
            target=f"{b:,}달러로 {a:,}달러를 나눈다."
            row=next(r for r in inspect_relations(source,target)["relations"] if r["type"]=="division_direction")
            self.assertEqual(row["status"],"supported_match")


if __name__ == "__main__":
    unittest.main()
