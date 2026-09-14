"""Independent synthetic diagnostics; no development/evaluation corpora loaded."""

import copy
import json
from pathlib import Path
import unittest

from sense_selection import select_senses


ROOT = Path(__file__).resolve().parents[3]
DICTIONARY = json.loads((ROOT / "content/model-comparison/input-preparation-v1/sense-dictionary.json").read_text(encoding="utf-8"))
REF = {"sourceVersionId": "synthetic-version", "blockId": "synthetic-target", "order": 10}

# These cover the 24 declared senses, not arbitrary paraphrases of each sense.
CASES = [
    ("equity", "ownership", "The founders own equity in the company.", "hint_financial"),
    ("equity", "residual-capital", "The company's equity equals assets minus liabilities.", "hint_financial"),
    ("equity", "fair-treatment", "Equity requires fair treatment of every student.", "suppress_nonfinancial_hint"),
    ("interest", "money-payment", "The borrower pays interest on the loan.", "hint_financial"),
    ("interest", "attention", "She expressed a keen interest in learning French.", "suppress_nonfinancial_hint"),
    ("interest", "legal-stake", "He holds a controlling interest in the company.", "hint_financial"),
    ("interest", "advantage", "They act in the public interest to protect residents.", "suppress_nonfinancial_hint"),
    ("security", "tradable-investment", "They buy securities such as bonds.", "hint_financial"),
    ("security", "protection", "Data security means protection against intrusion.", "suppress_nonfinancial_hint"),
    ("security", "collateral", "She pledged the house as security for the loan.", "hint_financial"),
    ("principal", "money-base", "The principal is the original amount borrowed.", "hint_financial"),
    ("principal", "head-person", "The school principal welcomed the students.", "suppress_nonfinancial_hint"),
    ("principal", "main-adjective", "The principal reason is poor maintenance.", "suppress_nonfinancial_hint"),
    ("overhead", "indirect-business-cost", "Manufacturing overhead includes indirect production costs.", "hint_financial"),
    ("overhead", "above-head", "Birds flew overhead.", "suppress_nonfinancial_hint"),
    ("margin", "profit-amount", "The margin is $12, the selling price minus the cost.", "hint_financial"),
    ("margin", "operating-profit-ratio", "The operating margin equals operating income divided by net revenue.", "hint_financial"),
    ("margin", "page-space", "The page margin is blank space around the printed text.", "suppress_nonfinancial_hint"),
    ("margin", "difference", "They won by a margin of 3 votes.", "suppress_nonfinancial_hint"),
    ("margin", "trading-collateral", "Margin is cash deposited with a broker to cover trading losses.", "hint_financial"),
    ("percentage-change", "relative-change", "Percentage change is change relative to a positive initial value.", "suppress_nonfinancial_hint"),
    ("percentage-change", "percentage-point-difference", "The difference is 4 percentage points.", "suppress_nonfinancial_hint"),
    ("increase-relation", "by-change", "Output increased by 10 units.", "suppress_nonfinancial_hint"),
    ("increase-relation", "to-level", "Output increased to 90 units.", "suppress_nonfinancial_hint"),
]


class SenseSelectionTests(unittest.TestCase):
    def select(self, text, context=None, dictionary=None, ref=None):
        return select_senses(text, context or [], dictionary or DICTIONARY, source_ref=ref or REF)

    def test_24_limited_senses_and_11_13_actions(self):
        seen = set()
        for concept, sense, source, action in CASES:
            with self.subTest(concept=concept, sense=sense):
                result = self.select(source)
                matching = [d for d in result["decisions"] if d["conceptId"] == concept and d["senseId"] == sense]
                self.assertEqual(len(matching), 1, result)
                self.assertEqual(matching[0]["promptAction"], action)
                self.assertEqual(bool(result["hints"]), action == "hint_financial")
                seen.add((concept, sense))
        self.assertEqual(len(seen), 24)
        self.assertEqual(sum(row[3] == "hint_financial" for row in CASES), 11)

    def test_synonymous_linked_relations(self):
        for text, sense in [
            ("The borrower received interest on the deposit.", "money-payment"),
            ("The firm's equity equals assets less liabilities.", "residual-capital"),
            ("The buyer acquired equity in the business.", "ownership"),
            ("The principal cause is erosion.", "main-adjective"),
            ("The operating margin was 17 percent.", "operating-profit-ratio"),
            ("They sold marketable securities on the bond market.", "tradable-investment"),
        ]:
            with self.subTest(text=text):
                self.assertIn(sense, [d["senseId"] for d in self.select(text)["decisions"]])

    def test_long_compound_and_defer_suppress_nested_words(self):
        for text in ["Interest rate is the payment for borrowed money.",
                     "Private equity represents an ownership stake in a company.",
                     "Gross margin is $12, the selling price minus the cost.",
                     "Security principal is the original amount borrowed.",
                     "Computational overhead includes indirect production costs.",
                     "The margin of safety is $12, the selling price minus the cost."]:
            with self.subTest(text=text):
                result = self.select(text)
                self.assertEqual(result["hints"], [])
                self.assertIn("unsupported_no_hint", [d["promptAction"] for d in result["decisions"]])
                self.assertIn("overlap_suppressed", [d["promptAction"] for d in result["decisions"]])

    def test_same_source_surface_different_senses_per_occurrence(self):
        text = "They own equity in the company; equity requires fair treatment of pupils."
        result = self.select(text)
        choices = [d for d in result["decisions"] if d["senseId"]]
        self.assertEqual([d["senseId"] for d in choices], ["ownership", "fair-treatment"])
        self.assertEqual(len(result["hints"]), 1)
        self.assertNotEqual(choices[0]["start"], choices[1]["start"])

    def test_same_surface_two_financial_senses_keep_separate_hints(self):
        text = "They pay interest on the loan; they hold interest in the company."
        result = self.select(text)
        self.assertEqual([hint["senseId"] for hint in result["hints"]], ["money-payment", "legal-stake"])
        self.assertEqual([hint["source"] for hint in result["hints"]], ["interest", "interest"])

    def test_general_financial_mixed_sentence_remains_occurrence_specific(self):
        result = self.select("The borrower pays interest on the loan, and she expressed interest in learning history.")
        self.assertEqual([d["senseId"] for d in result["decisions"]], ["money-payment", "attention"])
        self.assertEqual(len(result["hints"]), 1)

    def test_financial_topic_and_remote_keyword_do_not_force_sense(self):
        for text in ["The bank has interest in the plan.", "The report discusses equity.",
                     "Financial security matters.", "The margin is 12%.",
                     "The company values equity and fairness.", "The principal investment grew.",
                     "We found the banking lecture interesting."]:
            with self.subTest(text=text):
                self.assertEqual(self.select(text)["hints"], [])

    def test_negation_conditions_and_ambiguous_scope_withhold(self):
        for text in ["Equity does not mean an ownership stake in the company.",
                     "If the borrower pays interest on the loan, notify us.",
                     "The principal is not the original amount borrowed.",
                     "Output never increased by 10 units.",
                     "It is unclear whether the founders own equity in the company."]:
            with self.subTest(text=text):
                result = self.select(text)
                self.assertEqual(result["hints"], [])
                self.assertTrue(any(d["promptAction"] == "unsupported_no_hint" for d in result["decisions"]))

    def test_conflicting_senses_withhold(self):
        text = "Equity means an ownership stake in a company and requires fair treatment."
        self.assertEqual(self.select(text)["hints"], [])
        # Separate explicit definitions also conflict; do not take the first.
        result = self.select("Equity has the meaning defined above.", [
            {**REF, "blockId": "first", "order": 1, "text": "Equity means an ownership stake in a company."},
            {**REF, "blockId": "second", "order": 2, "text": "Equity requires fair treatment."},
            {**REF, "blockId": "third", "order": 3, "text": "Equity is fair treatment."},
        ])
        self.assertEqual(result["hints"], [])
        self.assertEqual(result["decisions"][0]["reason"], "multiple_supported_senses")

    def test_first_rendering_with_established_condition(self):
        cases = [("They trade equities on the stock market.", "주식"),
                 ("Home equity is the house's value minus the mortgage.", "순지분"),
                 ("Interest means a legal right to the property.", "권리"),
                 ("The borrower pays interest on the loan.", "이자")]
        for text, target in cases:
            with self.subTest(text=text):
                self.assertEqual(self.select(text)["hints"][0]["target"], target)

    def test_company_rendering_needs_explicit_company_or_shareholder_evidence(self):
        self.assertEqual(self.select("Equity equals assets minus liabilities.")["hints"], [])
        self.assertEqual(self.select("Shareholders' equity equals assets minus liabilities.")["hints"][0]["target"], "자기자본")

    def test_identical_hint_deduplicated_but_occurrences_retained(self):
        result = self.select("They own equity in the company. They hold equity in the firm.")
        self.assertEqual(len(result["hints"]), 1)
        self.assertEqual(len(result["decisions"]), 2)
        self.assertTrue(all(d["renderedHint"] for d in result["decisions"]))

    def test_raw_unicode_offsets_quotes_and_input_immutability(self):
        text = "😀 They own equity in the company.\r\nTheir principal is the original amount borrowed."
        before = copy.deepcopy(DICTIONARY)
        result = self.select(text)
        self.assertEqual(DICTIONARY, before)
        for decision in result["decisions"]:
            quote = decision["occurrence"]
            self.assertEqual(text[quote["start"]:quote["end"]], quote["text"])
            self.assertEqual(quote["startUtf16"], quote["start"] + 1)
            for item in decision["applicationEvidence"]:
                evidence = item["quote"]
                self.assertEqual(text[evidence["start"]:evidence["end"]], evidence["text"])

    def test_no_partial_word_or_spelling_repair(self):
        result = self.select("Disinterested principalship, inequity, principles and marginalia.")
        self.assertEqual(result["decisions"], [])

    def test_explicit_surviving_context_reference_only(self):
        context = [{**REF, "blockId": "definition", "order": 9, "text": "Equity means an ownership stake in a company."}]
        self.assertEqual(self.select("Equity matters.", context)["hints"], [])
        selected = self.select("Equity has the meaning defined above.", context)
        self.assertEqual(selected["hints"][0]["target"], "지분")
        self.assertEqual(selected["decisions"][0]["applicationEvidence"][0]["quote"]["blockId"], "definition")
        self.assertEqual(self.select("Equity has the meaning defined above.", [])["hints"], [])

    def test_definition_reference_direction_and_order_required(self):
        fragment = {**REF, "blockId": "definition", "order": 11,
                    "text": "Equity means an ownership stake in a company."}
        self.assertEqual(self.select("Equity has the meaning defined above.", [fragment])["hints"], [])
        self.assertTrue(self.select("Equity has the meaning defined below.", [fragment])["hints"])
        fragment.pop("order")
        self.assertEqual(self.select("Equity has the meaning defined below.", [fragment])["hints"], [])

    def test_definition_of_a_different_compound_does_not_define_bare_word(self):
        fragment = {**REF, "blockId": "definition", "order": 9,
                    "text": "Home equity is the house's value minus the mortgage."}
        self.assertEqual(self.select("Equity has the meaning defined above.", [fragment])["hints"], [])

    def test_definition_reference_cannot_leak_to_another_same_clause_occurrence(self):
        fragment = {**REF, "blockId": "definition", "order": 9,
                    "text": "Equity represents an ownership stake in the company."}
        result = self.select("Equity has the meaning defined above, and equity in education remains difficult to achieve.", [fragment])
        self.assertEqual([decision["senseId"] for decision in result["decisions"]], ["ownership", None])
        self.assertEqual(len(result["hints"]), 1)
        self.assertNotIn("contextReference", result["decisions"][1])

    def test_equal_length_conflicting_candidates_are_withheld(self):
        changed = copy.deepcopy(DICTIONARY)
        equity = next(entry for entry in changed["entries"] if entry["id"] == "equity")
        equity["compounds"].append({"surface": "home equity", "action": "consider_sense",
                                   "sense_id": "fair-treatment", "condition_ko": "synthetic conflicting condition"})
        result = self.select("Home equity is the house's value minus the mortgage.", dictionary=changed)
        self.assertEqual(result["hints"], [])
        self.assertEqual(result["decisions"][0]["reason"], "conflicting_equal_length_dictionary_candidates")

    def test_cross_version_context_rejected(self):
        with self.assertRaisesRegex(ValueError, "version_mismatch"):
            self.select("Equity matters.", [{"sourceVersionId": "other", "blockId": "x", "text": "Definition."}])

    def test_metadata_labels_and_answers_ignored(self):
        text = "The report discusses equity."
        base = self.select(text)
        forged = self.select(text, [{**REF, "blockId": "neutral", "text": "A heading.",
                                   "domain": "finance", "answer": "ownership"}],
                             ref={**REF, "domain": "finance", "unitId": "answer-ownership"})
        self.assertEqual(base["hints"], forged["hints"])
        self.assertEqual(base["decisions"], forged["decisions"])

    def test_domain_labels_never_select(self):
        changed = copy.deepcopy(DICTIONARY)
        for entry in changed["entries"]:
            for sense in entry["senses"]:
                sense["domain"] = "untrusted-label"
        for _, _, text, _ in CASES:
            self.assertEqual(self.select(text)["hints"], self.select(text, dictionary=changed)["hints"])

    def test_unfocused_dictionary_merge_rejected(self):
        changed = copy.deepcopy(DICTIONARY)
        changed["entries"].append({"id": "registered-extra", "forms": ["stock"]})
        with self.assertRaisesRegex(ValueError, "no_registered_dictionary_merge"):
            self.select("They buy stock.", dictionary=changed)

    def test_quantity_grammar_agent_and_time_are_not_amount(self):
        for text in ["Output increased by Monday.", "Output increased by the manager.",
                     "Output increased to improve safety.", "The percentage change is unclear."]:
            with self.subTest(text=text):
                result = self.select(text)
                self.assertEqual(result["hints"], [])
                self.assertFalse(any(d["senseId"] for d in result["decisions"]))


if __name__ == "__main__":
    unittest.main()
