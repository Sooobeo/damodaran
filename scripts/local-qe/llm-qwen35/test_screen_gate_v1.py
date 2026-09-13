import unittest
from unittest.mock import patch

import screen_gate_v1 as gate


class ScreenGateTests(unittest.TestCase):
    def mapping(self, warning, **changes):
        return {'version': gate.MAPPING_VERSION, 'status': 'assessed_model_assertions',
                'primaryWarning': warning, **changes}

    def test_fixed_screen_and_model_projection_integrity(self):
        receipt = gate.identity()
        self.assertEqual(receipt['expectedCount'], 8)
        self.assertEqual(len(set(receipt['inputIdsInOrder'])), 8)
        self.assertFalse(receipt['allEightCorrectIsAcceptance'])
        self.assertFalse(receipt['modelRequestContainsGateOrLabels'])

    def test_all_known_labels_and_first_error_bounds(self):
        order, labels = gate._load()
        for key in order:
            with self.subTest(key=key):
                matched = gate.check(key, self.mapping(labels[key]))
                self.assertTrue(matched['continueRun'])
                self.assertFalse(matched['fullBaselineAccepted'])
                missed = gate.check(key, self.mapping(not labels[key]))
                self.assertFalse(missed['continueRun'])
                if labels[key]:
                    self.assertEqual(missed['reason'], 'false_negative')
                    self.assertLess(missed['bestPossibleFull48RecallAfterThisError'], .95)
                else:
                    self.assertEqual(missed['reason'], 'false_positive')
                    self.assertLess(missed['bestPossibleFull48PrecisionAfterThisError'], .95)

    def test_unassessed_and_numeric_bool_and_wrong_version_rejected(self):
        key = gate.identity()['inputIdsInOrder'][0]
        for mapping in (self.mapping(None), self.mapping(1), self.mapping(0), self.mapping('true'),
                        self.mapping(True, status='unassessed'), self.mapping(True, version='old')):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                gate.check(key, mapping)
        with self.assertRaises(ValueError):
            gate.check('unknown', self.mapping(True))

    def test_frozen_file_change_rejected(self):
        with patch.object(gate, 'FILES', {**gate.FILES, 'input': (gate.FILES['input'][0], '0' * 64)}):
            with self.assertRaisesRegex(ValueError, 'frozen_screen_evidence_changed:input'):
                gate.identity()

    def test_reason_text_cannot_promote_warning(self):
        key = gate.identity()['inputIdsInOrder'][0]
        result = gate.check(key, self.mapping(False, reason='major critical severe mistranslation'))
        self.assertEqual(result['reason'], 'false_negative')


if __name__ == '__main__':
    unittest.main()
