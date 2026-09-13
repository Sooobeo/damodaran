"""Synthetic tail-only evidence tests; no model or real run writes."""
import copy
import unittest

import v5_review_evidence as full
import v5_tail2_review_evidence as tail
import test_v5_review_evidence as fixture
import reading_review_common_v5 as common


class TailEvidenceTests(unittest.TestCase):
    def setUp(self):
        f = fixture.reading_fixture.ReadingReviewTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.root, self.directory = f.root, f.directories[1]
        clean = [dict(r, id=sid) for r, sid in zip(f.clean[:2], tail.TAIL_IDS)]
        identity = dict(f.identity, selectedIds=tail.TAIL_IDS, totalRows=18)
        self.summary = fixture.upgrade(self.directory, clean, identity, self.root)

    def check(self, summary=None):
        tail.check_artifacts(self.directory, summary or self.summary,
            fixture.get(self.directory / 'predictions.jsonl', True), common.Evidence(self.root))

    def test_two_fixed_rows_keep_original_v5_metadata(self):
        original = copy.deepcopy(self.summary)
        self.check()
        self.assertEqual(self.summary, original)
        self.assertEqual(self.summary['expectedCount'], 2)

    def test_original_full_consumer_still_rejects_subset(self):
        with self.assertRaisesRegex(ValueError, 'v5_full_six_or_eighteen_required'):
            full.check_artifacts(self.directory, self.summary,
                fixture.get(self.directory / 'predictions.jsonl', True), common.Evidence(self.root))

    def test_arbitrary_or_reordered_subset_is_refused(self):
        for ids in [list(reversed(tail.TAIL_IDS)), ['LDEV26-001','LDEV26-002']]:
            changed = copy.deepcopy(self.summary)
            changed['input']['selectedIds'] = ids
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, 'only_fixed_unfinished_tail_ids_allowed'):
                self.check(changed)

    def test_missing_row_or_failed_run_cannot_pass(self):
        for mutation in [{'count':1},{'recordedCount':1},{'expectedCount':18},{'status':'failed'}, {'childProcessStopped':False}]:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.check(self.summary | mutation)

    def test_time_and_memory_guards_remain_strict(self):
        changed = copy.deepcopy(self.summary)
        changed['memoryMonitoring']['lastRequestElapsedSeconds'] = 7200
        with self.assertRaises(ValueError): self.check(changed)
        changed = copy.deepcopy(self.summary)
        changed['memoryBefore']['availablePhysical'] = 11 * tail.GIB - 1
        with self.assertRaisesRegex(ValueError, 'headroom'): self.check(changed)

    def test_native_identity_and_raw_output_are_still_checked(self):
        changed = copy.deepcopy(self.summary)
        changed['memoryMonitoring']['ownedIdentity']['creationTicks'] += 1
        with self.assertRaises(ValueError): self.check(changed)
        path = self.directory / (tail.TAIL_IDS[0] + '-response.json')
        raw = fixture.get(path); raw['content'] = 'changed'
        fixture.put(path, raw)
        with self.assertRaises(ValueError): self.check()


if __name__ == '__main__':
    unittest.main()
