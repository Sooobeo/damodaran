"""Policy regressions with a real strict validator and synthetic texts only."""
import importlib.util
import json
from pathlib import Path
import unittest

from material_warning import classify

spec = importlib.util.spec_from_file_location('synthetic_qwen_contract', Path(__file__).with_name('llm-qwen35') / 'contract.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
ROW = {'id': 'synthetic', 'source': 'Divide 72 by the rate.', 'translation': '이자율을 72로 나눈다.', 'context': ''}


def validate(issues=None, uncertainties=None, notes=None):
    return contract.validate_response(ROW, json.dumps({'semantic_issues': issues or [],
        'uncertainties': uncertainties or [], 'language_notes': notes or []}, ensure_ascii=False))


class MaterialWarningTests(unittest.TestCase):
    def test_unlocatable_fluency_note_is_not_a_material_warning(self):
        original = validate(notes=[{'kind': 'fluency', 'translation_quote': '없는 인용문', 'reason': 'synthetic style suggestion'}])
        before = json.dumps(original)
        self.assertTrue(original['whole_paragraph_review'])
        result = classify(original)
        self.assertFalse(result['primaryWarning'])
        self.assertEqual(result['allUnverifiedQuoteCount'], 1)
        self.assertEqual(json.dumps(original), before)

    def test_major_assertion_with_bad_quote_keeps_coarse_warning(self):
        result = classify(validate(issues=[{'kind': 'mistranslation', 'dimension': 'quantity_operation', 'severity': 'major',
            'source_quote': 'Divide 72 by the rate.', 'translation_quote': '없는 인용문', 'reason': 'synthetic reversed operation'}]))
        self.assertTrue(result['primaryWarning'])
        self.assertEqual(result['unlocalizedMajorCriticalCount'], 1)
        self.assertEqual(result['redCandidateIndices'], [])

    def test_located_major_is_a_candidate_not_certified_highlight(self):
        result = classify(validate(issues=[{'kind': 'mistranslation', 'dimension': 'quantity_operation', 'severity': 'major',
            'source_quote': 'Divide 72 by the rate.', 'translation_quote': '이자율을 72로 나눈다.', 'reason': 'synthetic reversed operation'}]))
        self.assertEqual(result['redCandidateIndices'], [0])
        self.assertEqual(result['semanticSpanPrecision'], 'not_evaluated')

    def test_uncertainty_remains_a_primary_warning(self):
        result = classify(validate(uncertainties=[{'source_quote': 'the rate', 'translation_quote': '이자율', 'reason': 'synthetic uncertainty'}]))
        self.assertTrue(result['primaryWarning'])
        self.assertFalse(result['supplementaryMajorWarning'])

    def test_minor_only_is_retained_as_diagnostic_and_bad_response_stays_unknown(self):
        result = classify(validate(issues=[{'kind': 'mistranslation', 'dimension': 'other', 'severity': 'minor',
            'source_quote': 'the rate', 'translation_quote': '이자율', 'reason': 'synthetic minor issue'}]))
        self.assertFalse(result['primaryWarning'])
        self.assertEqual(result['minorAssertionCount'], 1)
        failed = classify(contract.validate_response(ROW, '{broken'))
        self.assertIsNone(failed['primaryWarning'])
        self.assertFalse(failed['noMaterialRiskAsserted'])


if __name__ == '__main__':
    unittest.main()
