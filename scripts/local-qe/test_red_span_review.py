"""Synthetic red-span contract/aggregation tests; zero model calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import red_span_review as helper


def evidence():
    inputs, records, rows, mappings = [], [], [], []
    for index in range(48):
        item = {'id': f'synthetic-{index}', 'source': 'Revenue rose.',
                'translation': '😀 수익이 줄었다.', 'context': ''}
        issue = {'severity': 'major', 'kind': 'mistranslation', 'dimension': 'other',
                 'source_quote': 'rose', 'translation_quote': '줄었다',
                 'reason': '합성 검증용 주장. 실제 품질 근거가 아니다.'}
        diagnostics = []
        for side in ('source', 'translation'):
            quote = issue[side + '_quote']
            status, span = helper.locate(item[side], quote)
            diagnostics.append({'collection': 'semantic_issues', 'index': 0, 'side': side,
                                'quote': quote, 'status': status, 'start': span['start'], 'end': span['end']})
        mapping = {'version': 'synthetic', 'redCandidateIndices': [0], 'unlocalizedMajorCriticalCount': 0}
        records.append({'id': item['id'], 'assessment': {'status': 'valid', 'span_diagnostics': diagnostics,
            'assessment': {'semantic_issues': [issue], 'uncertainties': [], 'language_notes': []}},
            'materialWarningV2': mapping})
        mappings.append({'id': item['id'], **mapping})
        inputs.append(item)
        rows.append({'id': item['id'], 'translationSystem': 'hidden-model-a' if index < 24 else 'hidden-model-b',
                     'split': 'dev' if index % 2 else 'reading', 'completed': True, 'requestStatus': 'completed',
                     'materialError': False, 'namedError': 'HIDDEN-GT'})
    report = {'version': 'qwen35-dev48-warning-analysis-v2', 'requestCounts': {'expected': 48, 'completed': 48},
              'runStatus': 'completed', 'runIntegrityAndClosurePassed': True,
              'runtimeAcceptance': {'accepted': True}, 'mappingRecomputedFromRawValidatedResponse': True,
              'nativeEvidence': {'runtimeCodeImported': False}, 'humanReviewed': False,
              'rows': rows, 'materialMappingEvidence': mappings, 'developmentParagraphAccepted': False}
    return {'report': report, 'inputs': inputs, 'records': records, 'hashes': {},
            'analysisPath': 'synthetic-analysis', 'runPath': 'synthetic-run', 'inputPath': 'synthetic-input'}


def review_for(packet, packet_sha='synthetic-packet-hash'):
    return {'version': helper.REVIEW_VERSION, 'reviewerType': 'assistant', 'humanReviewed': False,
            'packetSha256': packet_sha, 'rubricSha256': packet['rubricSha256'],
            'priorExposureKo': '합성 테스트이며 실제 후보를 검토하지 않았다.',
            'rows': [{'id': row['id'], 'verdict': 'correct', 'actualSeverity': 'major',
                      'locationCorrect': True, 'causeCorrect': True, 'extentAppropriate': True,
                      'sourceQuote': 'rose', 'translationQuote': row['proposal']['translationQuote'],
                      'reasonKo': '합성 fixture 판정이며 실제 의미 품질이 아니다.'} for row in packet['rows']]}


class RedSpanTests(unittest.TestCase):
    def setUp(self):
        self.data = evidence()
        self.packet, self.private = helper.build_bundle(self.data)

    def test_packet_has_no_identity_truth_or_threshold_results(self):
        encoded = helper.canonical(self.packet)
        for hidden in ('hidden-model', 'HIDDEN-GT', 'materialError', 'translationSystem',
                       'developmentParagraphAccepted', 'minimumRate', 'synthetic-0'):
            self.assertNotIn(hidden, encoded)
        self.assertEqual(len(self.packet['rows']), 48)
        self.assertEqual(len(self.private['technicalQuotes']), 96)

    def test_complete_false_paragraph_gate_does_not_fake_span_gate(self):
        helper.validate_complete(self.data['report'])
        decisions = helper.validate_review(review_for(self.packet), self.packet, 'synthetic-packet-hash')
        result = helper.summarize(self.private, decisions)
        self.assertTrue(result['allCandidateAssertionGroupsPassed'])
        self.assertFalse(result['fullLearningReadinessAccepted'])
        self.assertFalse(result['paragraphMetricsReplaced'])
        self.assertEqual(len(result['groups']), 9)

    def test_partial_or_cleanup_failure_rejected_before_evaluator_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'analysis.json'
            for changes in ({'requestCounts': {'expected': 48, 'completed': 12}},
                            {'runStatus': 'failed'}, {'runIntegrityAndClosurePassed': False},
                            {'runtimeAcceptance': {'accepted': False}}):
                path.write_text(json.dumps(self.data['report'] | changes), encoding='utf-8')
                with patch.object(helper, 'load_evaluator') as loader, self.assertRaises(ValueError):
                    helper.verify_evidence(path, folder, path)
                loader.assert_not_called()

    def test_missing_completed_row_is_rejected(self):
        report = copy.deepcopy(self.data['report'])
        report['rows'][0]['completed'] = False
        with self.assertRaisesRegex(ValueError, 'partial'):
            helper.validate_complete(report)

    def test_duplicate_red_index_and_bad_index_rejected(self):
        for indices in ([0, 0], [99], [True]):
            data = copy.deepcopy(self.data)
            data['report']['materialMappingEvidence'][0]['redCandidateIndices'] = indices
            data['records'][0]['materialWarningV2']['redCandidateIndices'] = indices
            with self.assertRaises(ValueError):
                helper.build_bundle(data)

    def test_utf16_and_overlapping_ambiguity(self):
        self.assertEqual(helper.locate('😀가', '가'), ('unique', {'start': 2, 'end': 3, 'text': '가'}))
        self.assertEqual(helper.locate('aaa', 'aa'), ('ambiguous', None))
        data = copy.deepcopy(self.data)
        data['records'][0]['assessment']['span_diagnostics'][1]['start'] -= 1
        with self.assertRaisesRegex(ValueError, 'offset'):
            helper.build_bundle(data)

    def test_original_unmatched_source_quote_kept_in_technical_denominator(self):
        data = copy.deepcopy(self.data)
        record = data['records'][0]['assessment']
        record['assessment']['semantic_issues'][0]['source_quote'] = 'missing quote'
        record['span_diagnostics'][0].update(quote='missing quote', status='not_found', start=None, end=None)
        packet, private = helper.build_bundle(data)
        decisions = helper.validate_review(review_for(packet), packet, 'synthetic-packet-hash')
        overall = helper.summarize(private, decisions)['groups'][0]
        self.assertEqual(overall['allV2RedCandidateAssertions']['denominator'], 48)
        self.assertEqual(overall['technicalLocalization']['denominatorAllSubmittedNonemptySourceAndTargetQuotes'], 96)
        self.assertEqual(overall['technicalLocalization']['rejected'], 1)

    def test_same_interval_assertions_are_not_cherry_picked(self):
        data = copy.deepcopy(self.data)
        record = data['records'][0]
        record['assessment']['assessment']['semantic_issues'].append(
            record['assessment']['assessment']['semantic_issues'][0] | {'reason': '두 번째 합성 주장'})
        record['assessment']['span_diagnostics'].extend([
            item | {'index': 1} for item in record['assessment']['span_diagnostics'][:2]])
        record['materialWarningV2']['redCandidateIndices'] = [0, 1]
        data['report']['materialMappingEvidence'][0]['redCandidateIndices'] = [0, 1]
        packet, private = helper.build_bundle(data)
        review = review_for(packet)
        second = next(link['id'] for link in private['links'] if link['issueIndex'] == 1)
        decision = next(row for row in review['rows'] if row['id'] == second)
        decision.update(verdict='false_positive', causeCorrect=False)
        result = helper.summarize(private, helper.validate_review(review, packet, 'synthetic-packet-hash'))
        overall = result['groups'][0]
        self.assertEqual(overall['allV2RedCandidateAssertions']['denominator'], 49)
        self.assertEqual(overall['uniqueDisplayedKoreanSpans']['denominator'], 48)
        self.assertEqual(overall['uniqueDisplayedKoreanSpans']['false_positive'], 1)

    def test_substring_or_whole_paragraph_does_not_score_semantic_correctness(self):
        review = review_for(self.packet)
        review['rows'][0].update(verdict='false_positive', extentAppropriate=False)
        result = helper.summarize(self.private, helper.validate_review(review, self.packet, 'synthetic-packet-hash'))
        self.assertEqual(result['groups'][0]['allV2RedCandidateAssertions']['false_positive'], 1)

    def test_unresolved_stays_in_denominator_and_blocks_95_percent(self):
        result = helper.precision(['correct'] * 19 + ['unresolved'])
        self.assertEqual(result['precision'], .95)
        self.assertEqual(result['denominator'], 20)
        self.assertEqual(result['status'], 'hold')
        self.assertFalse(result['passed'])

    def test_empty_span_groups_are_not_evaluated(self):
        private = copy.deepcopy(self.private)
        private['links'] = []
        result = helper.summarize(private, {})
        self.assertFalse(result['allCandidateAssertionGroupsPassed'])
        for group in result['groups']:
            self.assertIsNone(group['allV2RedCandidateAssertions']['precision'])
            self.assertEqual(group['uniqueDisplayedKoreanSpans']['status'], 'not_evaluated')

    def test_cross_slice_failure_cannot_be_hidden_by_overall_95(self):
        private = copy.deepcopy(self.private)
        for index, link in enumerate(private['links']):
            link['translationSystem'] = 'hidden-model-a' if index < 47 else 'hidden-model-b'
        review = review_for(self.packet)
        failing = private['links'][-1]['id']
        next(row for row in review['rows'] if row['id'] == failing).update(verdict='false_positive', causeCorrect=False)
        result = helper.summarize(private, helper.validate_review(review, self.packet, 'synthetic-packet-hash'))
        self.assertTrue(result['groups'][0]['allV2RedCandidateAssertions']['passed'])
        self.assertFalse(result['allCandidateAssertionGroupsPassed'])

    def test_review_missing_duplicate_unknown_ids_and_extra_keys_rejected(self):
        for mutation in (lambda r: r['rows'].pop(), lambda r: r['rows'].append(r['rows'][0]),
                         lambda r: r['rows'][0].update(id='unknown'),
                         lambda r: r['rows'][0].update(confidence=.95)):
            review = review_for(self.packet)
            mutation(review)
            with self.assertRaises(ValueError):
                helper.validate_review(review, self.packet, 'synthetic-packet-hash')

    def test_invalid_review_types_quotes_and_claims_rejected(self):
        changes = [{'locationCorrect': 1}, {'causeCorrect': 'true'}, {'sourceQuote': 'not in source'},
                   {'translationQuote': '다른 구절'}, {'actualSeverity': 'minor'}, {'extentAppropriate': False}]
        for change in changes:
            review = review_for(self.packet)
            review['rows'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                helper.validate_review(review, self.packet, 'synthetic-packet-hash')

    def test_json_duplicate_keys_and_nonfinite_values_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.json'
            for content in ('{"id":1,"id":2}', '{"value":NaN}'):
                path.write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    helper.read(path)

    def test_prepare_grade_roundtrip_preserves_inputs_and_refuses_reuse_or_tamper(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(helper, 'ROOT', Path(folder)):
            root = Path(folder) / '.training/quality-evaluation'
            root.mkdir(parents=True)
            original = root / 'original.json'
            original.write_text('{"synthetic":true}', encoding='utf-8')
            data = copy.deepcopy(self.data)
            data['hashes'] = helper.snapshot([original])
            bundle = root / 'bundle'
            with patch.object(helper, 'verify_evidence', return_value=data):
                helper.prepare('synthetic', 'synthetic', 'synthetic', bundle)
                with self.assertRaisesRegex(ValueError, 'already_exists'):
                    helper.prepare('synthetic', 'synthetic', 'synthetic', bundle)
                packet = helper.read(bundle / 'reviewer-packet.json')
                review_path = root / 'review.json'
                helper.write_new(review_path, review_for(packet, helper.sha(bundle / 'reviewer-packet.json')))
                result = helper.grade(bundle, review_path, root / 'result.json')
                self.assertFalse(result['inferencePerformed'])
                self.assertFalse(result['substringMatchScoredAsSemanticCorrectness'])
                helper.unchanged(data['hashes'])
                original.write_text('{"changed":true}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'input_changed'):
                    helper.grade(bundle, review_path, root / 'result-2.json')


if __name__ == '__main__':
    unittest.main()
