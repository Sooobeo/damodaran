"""Contract tests use explicit synthetic scores, never pretend to test QE model quality."""
import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from calibrate import analyze, require_current_registration_policy, slice_gate
from common import (CALIBRATION_POLICY, EXPERIMENT_ROOT, PRODUCT_QE_POLICY, PRODUCT_QE_POLICY_V1, ROOT, read_json, sha_text, validate_request,
                    validate_score, validate_spans, write_json)


def fixture():
    inputs, labels, scores = [], [], []
    for index in range(8):
        source_sha, target_sha = sha_text('source ' + str(index)), sha_text('target ' + str(index))
        base = {'id': str(index), 'sourceSha256': source_sha, 'translationSha256': target_sha}
        inputs.append({**base, 'translationSystem': 'synthetic', 'split': 'test', 'domain': 'test',
                       'documentId': 'd' + str(index), 'sourceLength': index + 10})
        labels.append({**base, 'materialError': index < 3, 'severity': 2 if index < 3 else 0,
                       'namedError': 'known' if index < 2 else None,
                       'errors': [{'severity': 2, 'category': 'quantity_formula'}] if index < 3 else []})
        scores.append({**base, 'modelIdentity': 'synthetic-not-real-model', 'score': index - 4.0,
                       'status': 'completed', 'contextUsed': False, 'truncated': False})
    return inputs, labels, scores


class CalibrationTests(unittest.TestCase):
    def test_separable_fixture_passes_predeclared_gate(self):
        result = analyze(*fixture())
        self.assertTrue(result['accepted'])
        self.assertEqual(result['observed']['recall'], 1)
        self.assertEqual(result['observed']['precision'], 1)
        self.assertEqual(result['observed']['warningRate'], 3 / 8)
        self.assertFalse(result['humanReviewed'])

    def test_equal_scores_cannot_register_by_warning_everything(self):
        inputs, labels, scores = fixture()
        for score in scores:
            score['score'] = 0.5
        result = analyze(inputs, labels, scores)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['observed']['warningRate'], 1)

    def test_missed_named_error_prevents_registration(self):
        inputs, labels, scores = fixture()
        scores[0]['score'] = 50
        self.assertFalse(analyze(inputs, labels, scores)['accepted'])

    def test_duplicate_and_missing_rows_rejected(self):
        inputs, labels, scores = fixture()
        for bad in (scores[:-1], scores + scores[:1]):
            with self.assertRaises(ValueError):
                analyze(inputs, labels, bad)

    def test_hash_mismatch_rejected(self):
        inputs, labels, scores = fixture()
        scores[0]['translationSha256'] = 'changed'
        with self.assertRaises(ValueError):
            analyze(inputs, labels, scores)

    def test_mixed_runtime_rejected(self):
        inputs, labels, scores = fixture()
        scores[0]['modelIdentity'] = 'other'
        with self.assertRaises(ValueError):
            analyze(inputs, labels, scores)

    def test_silent_truncation_rejected(self):
        inputs, labels, scores = fixture()
        scores[0]['truncated'] = True
        with self.assertRaises(ValueError):
            analyze(inputs, labels, scores)

    def test_criteria_cannot_be_relaxed_after_scores(self):
        relaxed = {**CALIBRATION_POLICY, 'minimumPrecision': 0.01}
        with self.assertRaises(ValueError):
            analyze(*fixture(), policy=relaxed)

    def test_explicit_current_policy_rejects_old_precision_pass(self):
        inputs, labels, scores = fixture()
        scores[3]['score'] = scores[2]['score'] - 0.5
        self.assertTrue(analyze(inputs, labels, scores)['accepted'])
        self.assertFalse(analyze(inputs, labels, scores, PRODUCT_QE_POLICY)['accepted'])

    def test_old_policy_cannot_authorize_new_registration(self):
        with self.assertRaisesRegex(ValueError, 'obsolete_or_changed'):
            require_current_registration_policy(analyze(*fixture()))

    def test_named_error_count_is_required_for_current_registration(self):
        result = analyze(*fixture(), policy=PRODUCT_QE_POLICY)
        self.assertTrue(result['accepted'])
        with self.assertRaisesRegex(ValueError, 'current_product_gate_failed'):
            require_current_registration_policy(result)

    def test_current_policy_thresholds_cannot_be_relaxed(self):
        with self.assertRaisesRegex(ValueError, 'calibration_policy_changed'):
            analyze(*fixture(), policy={**PRODUCT_QE_POLICY, 'minimumPrecision': 0.90})

    def test_aggregate_and_marginal_pass_cannot_hide_weak_cross_slice(self):
        inputs, labels, scores = [], [], []
        for system in ('A', 'B'):
            for split in ('dev', 'reading'):
                for index in range(40):
                    item, label, score = [copy.deepcopy(part[0]) for part in fixture()]
                    identifier = f'{system}:{split}:{index}'
                    for row in (item, label, score):
                        row['id'] = identifier
                    item.update(translationSystem=system, split=split)
                    label.update(materialError=index < 20, namedError='known' if system == 'B' and split == 'reading' and index < 6 else None)
                    missed = system == 'A' and split == 'dev' and index < 2
                    score['score'] = 2 if missed else 0 if index < 20 else 1
                    inputs.append(item); labels.append(label); scores.append(score)
        old = analyze(inputs, labels, scores, PRODUCT_QE_POLICY_V1)
        result = analyze(inputs, labels, scores, PRODUCT_QE_POLICY)
        self.assertTrue(old['accepted'])
        self.assertTrue(result['observed']['overallGatePassed'])
        self.assertFalse(result['accepted'])
        slices = result['perBaselineGate']['slices']
        self.assertTrue(all(row['accepted'] for row in slices if row['kind'] != 'translationSystem_x_split'))
        weak = next(row for row in slices if row['key'] == {'translationSystem': 'A', 'split': 'dev'})
        self.assertEqual(weak['recall'], .9)
        self.assertEqual(weak['status'], 'failed')

    def test_zero_positive_slice_is_unassessed_not_perfect(self):
        inputs, labels, scores = fixture()
        for index, item in enumerate(inputs):
            item['translationSystem'] = 'positive' if index < 3 else 'no-positive'
        result = analyze(inputs, labels, scores, PRODUCT_QE_POLICY)
        self.assertFalse(result['accepted'])
        empty_positive = next(row for row in result['perBaselineGate']['slices'] if row['key'] == {'translationSystem': 'no-positive'})
        self.assertIsNone(empty_positive['recall'])
        self.assertIsNone(empty_positive['precision'])
        self.assertEqual(empty_positive['status'], 'unassessed_requires_additional_validation')

    def test_missing_cross_slice_requires_additional_validation(self):
        inputs, labels, scores = fixture()
        rows = [{**item, **label, 'score': score['score']} for item, label, score in zip(inputs, labels, scores)]
        for index, row in enumerate(rows):
            row['translationSystem'] = 'A' if index < 4 else 'B'
            row['split'] = 'dev' if index < 4 else 'reading'
        gate = slice_gate(rows, 100, PRODUCT_QE_POLICY)
        self.assertTrue(gate['additionalValidationRequired'])
        empty = [row for row in gate['slices'] if row['count'] == 0]
        self.assertEqual(len(empty), 2)
        self.assertTrue(all(row['recall'] is None and row['precision'] is None and not row['accepted'] for row in empty))

    def test_paragraph_warning_does_not_certify_all_individual_errors(self):
        result = analyze(*fixture(), policy=PRODUCT_QE_POLICY)
        self.assertFalse(result['individualErrorDetection']['assessed'])
        self.assertEqual(result['metricUnit'], 'translation_output_with_at_least_one_material_error')


class ProtocolTests(unittest.TestCase):
    def test_unbounded_finite_score_is_preserved(self):
        self.assertEqual(validate_score(-3.5), -3.5)
        self.assertEqual(validate_score(5), 5)
        for value in (True, '0.5', math.nan, math.inf, -math.inf):
            with self.assertRaises(ValueError):
                validate_score(value)

    def test_whole_surrogate_pair_span_accepted(self):
        span = {'side': 'target', 'start': 1, 'end': 3, 'text': '😀', 'severity': 'major'}
        self.assertEqual(validate_spans([span], 'source', 'a😀b'), [span])

    def test_split_surrogate_or_wrong_text_rejected(self):
        for span in ({'side': 'target', 'start': 1, 'end': 2, 'text': '😀', 'severity': 'major'},
                     {'side': 'target', 'start': 0, 'end': 1, 'text': 'x', 'severity': 'major'},
                     {'side': 'target', 'start': 0, 'end': 20, 'text': 'x', 'severity': 'major'}):
            with self.assertRaises(ValueError):
                validate_spans([span], 'source', 'a😀b')

    def test_invalid_inputs_rejected(self):
        valid = {'id': 'x', 'source': 'source', 'translation': '번역', 'context': ''}
        validate_request(valid)
        for bad in ({**valid, 'translation': ''}, {**valid, 'source': 'x' * 30001}, {**valid, 'context': []}, {**valid, 'id': 4}):
            with self.assertRaises(ValueError):
                validate_request(bad)

    def test_unregistered_bridge_returns_unavailable_without_model(self):
        if (ROOT / '.translation/qe/manifest.json').exists():
            self.skipTest('a real accepted QE registration exists')
        request = {'id': 'contract-check', 'source': 'source', 'translation': '번역', 'context': ''}
        result = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'scripts/local-qe/bridge.py')],
                                input=json.dumps(request), text=True, encoding='utf-8', capture_output=True, timeout=15, check=True)
        output = json.loads(result.stdout)
        self.assertEqual(output['status'], 'unavailable')
        self.assertEqual(output['errorCode'], 'qe_not_registered')
        self.assertIsNone(output['score'])
        self.assertEqual(output['spans'], [])


class LegacyCheckpointTests(unittest.TestCase):
    def test_only_identical_derived_buffer_is_removed(self):
        import torch
        from backend import checked_legacy_state
        positions = torch.arange(514).unsqueeze(0)
        learned = torch.tensor([3.0, 7.0])
        state = {'encoder.model.embeddings.position_ids': positions.clone(), 'learned.weight': learned}
        result = checked_legacy_state(state, positions)
        self.assertIs(result['learned.weight'], learned)
        self.assertIn('encoder.model.embeddings.position_ids', state)
        self.assertNotIn('encoder.model.embeddings.position_ids', result)

    def test_changed_legacy_buffer_is_rejected(self):
        import torch
        from backend import checked_legacy_state
        with self.assertRaises(RuntimeError):
            checked_legacy_state({'encoder.model.embeddings.position_ids': torch.arange(4)}, torch.arange(4) + 1)
        with self.assertRaises(RuntimeError):
            checked_legacy_state({}, torch.arange(4))


class RuntimeLockTests(unittest.TestCase):
    def test_live_and_reused_pids_are_distinguished(self):
        from runtime_lock import owner_state
        class Existing:
            def __init__(self, created):
                self.created = created
            def create_time(self):
                return self.created
        owner = {'pid': 1234, 'createdAt': 1000.0}
        self.assertEqual(owner_state(owner, lambda pid: Existing(1000.0)), 'live')
        self.assertEqual(owner_state(owner, lambda pid: Existing(1100.0)), 'pid_reused')

    def test_access_denied_is_unknown_not_dead(self):
        import psutil
        from runtime_lock import owner_state
        def denied(pid):
            raise psutil.AccessDenied(pid)
        self.assertEqual(owner_state({'pid': 1234, 'createdAt': 1000.0}, denied), 'unknown')

    def test_only_confirmed_dead_owner_can_be_reclaimed(self):
        import psutil
        from runtime_lock import acquire, release
        EXPERIMENT_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=EXPERIMENT_ROOT, prefix='lock-contract-') as temp:
            path = Path(temp) / 'runtime.lock'
            previous = {'pid': 1234, 'createdAt': 1000.0}
            write_json(path, previous)
            def absent(pid):
                raise psutil.NoSuchProcess(pid)
            owner = acquire(path, process_factory=absent)
            self.assertNotEqual(owner, previous)
            with self.assertRaisesRegex(RuntimeError, 'live_owner'):
                acquire(path)
            self.assertEqual(read_json(path), owner)
            release(path, owner)
            self.assertFalse(path.exists())

    def test_reused_pid_and_invalid_metadata_are_not_removed(self):
        from runtime_lock import acquire
        class Reused:
            def create_time(self):
                return 2000.0
        with tempfile.TemporaryDirectory(dir=EXPERIMENT_ROOT, prefix='lock-contract-') as temp:
            path = Path(temp) / 'runtime.lock'
            previous = {'pid': 1234, 'createdAt': 1000.0}
            write_json(path, previous)
            with self.assertRaisesRegex(RuntimeError, 'pid_reused_owner'):
                acquire(path, process_factory=lambda pid: Reused())
            self.assertEqual(read_json(path), previous)
            path.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'invalid_owner'):
                acquire(path)
            self.assertEqual(read_json(path), {})


if __name__ == '__main__':
    unittest.main()
