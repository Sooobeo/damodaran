import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1.review_io import quote_spans, materialize_fields


class ReviewEvidenceTests(unittest.TestCase):
    def test_unicode_codepoints_and_authored_quote_preserved(self):
        self.assertEqual(quote_spans('첫😀문장 끝', ['문장']), [{'start': 2, 'end': 4, 'text': '문장'}])

    def test_repeated_quote_requires_explicit_occurrence(self):
        with self.assertRaisesRegex(ValueError, 'ambiguous_quote'):
            quote_spans('값과 값', ['값'])
        self.assertEqual(quote_spans('값과 값', [{'start': 3, 'end': 4, 'text': '값'}])[0]['start'], 3)

    def test_quote_cannot_be_silently_corrected(self):
        with self.assertRaisesRegex(ValueError, 'not_in_text'):
            quote_spans('25유로', ['25달러'])

    def test_judgment_is_explicit_and_unchanged(self):
        draft = {'verdict': 'damaged', 'reasonKo': '부호가 바뀜', 'translationEvidence': ['-5']}
        converted = materialize_fields(draft, {'translationEvidence': '수익률 -5%'})
        self.assertEqual(converted['verdict'], 'damaged')
        self.assertEqual(draft['translationEvidence'], ['-5'])
        self.assertNotIn('sourceEvidence', converted)


if __name__ == '__main__':
    unittest.main()
