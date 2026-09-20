"""Synthetic v6 memory admission, old-nine preservation and mixed-context provenance."""
import ast
from copy import deepcopy
from contextlib import contextmanager, ExitStack, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_runner_memory_v6 as r
from input_execution_v1 import local_question_runner_battery_v4 as original
from input_execution_v1 import local_question_context_memory_v6 as native
from input_execution_v1 import test_local_question_runner_partial_recovery_v5 as prior_support


def write(root, path, value):
    path = Path(root) / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else r.packed(value))
    return r.reference(root, path)


def state(physical=10 * 1024 ** 3, percent=70):
    return {'ACLineStatus': 0, 'BatteryLifePercent': percent, 'availablePhysicalBytes': physical,
            'availableCommitBytes': 10 * 1024 ** 3}


@contextmanager
def partial_fixture(root):
    """Real v5/v6 identity loaders; synthetic frozen-v4/prior native boundary."""
    root = Path(root).resolve()
    with prior_support.partial_fixture(root) as previous:
        fake4 = types.SimpleNamespace(VERSION=original.VERSION, PROFILE=original.PROFILE,
                battery_refs=lambda identity: [identity['batteryFreeze']])
        with patch.object(r, 'v4', fake4):
            expected = r.partial_freeze_inputs(root)
            files = [write(root, path, b'synthetic v6 code') for path in sorted(r.REQUIRED_PARTIAL_FILES)]
            write(root, r.PARTIAL_FREEZE, {'version': r.PARTIAL_VERSION, **expected,
                    'actualNewQuestionCallsAtFreeze': 0, 'files': files})
            yield r.load_partial_recovery(root)


class MemoryRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='question-memory-v6-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

    def put(self, path, value): return write(self.root, path, value)

    def wait_fixture(self, states, conflicts=None):
        class Clock:
            now = 0.0
            def monotonic(self): return self.now
            def sleep(self, seconds): self.now += seconds
        clock = Clock()
        values = iter(states)
        last = [states[-1]]
        count = [0]
        def sample():
            count[0] += 1
            last[0] = next(values, last[0])
            return deepcopy(last[0])
        conflict = iter(conflicts or [])
        fake = types.SimpleNamespace(require=r.resources.require, ResourceGuardError=r.resources.ResourceGuardError,
              process_state=lambda: {'nativeConflicts': next(conflict, []), 'classifiedConflicts': []})
        for name, value in {'time': clock, 'system_state': sample, 'resources': fake, 'write_new': self.put}.items():
            self.stack.enter_context(patch.object(r, name, value))
        self.stack.enter_context(patch.object(r.os, 'fsync', return_value=None))
        return clock, count, '.training/verifications/wait.jsonl'

    def test_memory_wait_then_two_good_samples_and_full_replay(self):
        clock, count, path = self.wait_fixture([state(r.PROFILE['minimumStartPhysicalBytes'] - 1), state(), state(), state()])
        receipt = r.wait_for_memory(path, self.root)
        self.assertEqual(count[0], 4)
        self.assertEqual(receipt['attempts'], 2)
        self.assertEqual(receipt['elapsedSeconds'], 11)
        self.assertEqual(r.validate_wait(receipt['file'], self.root)['attempts'], 2)

    def test_memory_first_battery_low_second_does_not_wait_again(self):
        clock, count, path = self.wait_fixture([state(1), state(percent=20)])
        with self.assertRaisesRegex(r.resources.ResourceGuardError, 'battery_reserve_reached'):
            r.wait_for_memory(path, self.root)
        self.assertEqual(count[0], 2)
        self.assertEqual(clock.now, 3)
        self.assertEqual(r.read_jsonl(self.root / path)[0][-1]['failure']['code'], 'battery_reserve_reached')

    def test_memory_first_unknown_battery_or_conflict_second_aborts(self):
        clock, count, path = self.wait_fixture([state(1), state(percent=255)])
        with self.assertRaisesRegex(r.resources.ResourceGuardError, 'battery_level_unknown'):
            r.wait_for_memory(path, self.root)
        self.assertEqual(count[0], 2)

    def test_memory_first_second_native_conflict_aborts(self):
        clock, count, path = self.wait_fixture([state(1), state()], [[], [123]])
        with self.assertRaisesRegex(r.resources.ResourceGuardError, 'question_preflight_model_or_worker'):
            r.wait_for_memory(path, self.root)
        self.assertEqual(count[0], 2)

    def test_missing_or_noninteger_memory_is_not_waitable(self):
        invalid = state()
        invalid['availablePhysicalBytes'] = None
        clock, count, path = self.wait_fixture([invalid])
        with self.assertRaisesRegex(ValueError, 'invalid_memory_observation'):
            r.wait_for_memory(path, self.root)
        self.assertEqual(count[0], 1)
        self.assertEqual(clock.now, 0)

    def test_memory_wait_is_bounded_300_without_native(self):
        clock, count, path = self.wait_fixture([state(1)])
        with self.assertRaisesRegex(r.resources.ResourceGuardError, 'memory_admission_timeout'):
            r.wait_for_memory(path, self.root)
        self.assertLessEqual(clock.now, 300)
        self.assertGreater(clock.now, 290)
        self.assertEqual(r.read_jsonl(self.root / path)[0][-1]['completionRequestsSent'], 0)

    def test_wait_replay_rejects_resealed_attempt_spacing_and_false_shortage(self):
        clock, count, path = self.wait_fixture([state(1), state(), state(), state()])
        receipt = r.wait_for_memory(path, self.root)
        original_rows = r.read_jsonl(self.root / path)[0]
        mutations = []
        rows = deepcopy(original_rows); rows[1]['index'] = 1; mutations.append(rows)
        rows = deepcopy(original_rows); rows[4]['monotonicSeconds'] = 4; mutations.append(rows)
        rows = deepcopy(original_rows); rows[1]['system'] = state(); mutations.append(rows)
        rows = deepcopy(original_rows); rows[-1]['elapsedSeconds'] = 1; mutations.append(rows)
        for rows in mutations:
            self.put(path, b''.join(r.packed(row) for row in rows))
            with self.assertRaises(ValueError):
                r.validate_wait(r.reference(self.root, path), self.root)

    def test_partial_identity_binds_nine_retained_and_55_pending(self):
        with partial_fixture(self.root) as identity:
            self.assertEqual(r.load_partial_recovery(self.root), identity)
            self.assertEqual(identity['retainedReviewIds'], list(r.RETAINED_IDS))
            self.assertEqual(identity['pendingReviewIds'], list(r.PENDING_IDS))
            self.assertEqual(identity['priorRun']['failedContext']['nativeStoppedMeaning'], 'no_process_was_created')
            r.verify_refs(self.root, r.partial_recovery_refs(identity))

    def test_prior_nine_raw_change_rejected(self):
        with partial_fixture(self.root):
            self.put(r.PRIOR_RUN + '/R001/raw.fixture', b'changed')
            with self.assertRaises(ValueError):
                r.load_partial_recovery(self.root)

    def test_prior_failed_R010_call_evidence_rejected(self):
        with partial_fixture(self.root):
            self.put(r.PRIOR_RUN + '/R010/completion-intent.json', {'sequence': 1})
            with self.assertRaises(ValueError):
                r.prior_snapshot(self.root)

    def test_new_freeze_extra_source_and_pending_order_change_rejected(self):
        with partial_fixture(self.root):
            value, _ = r.read(self.root / r.PARTIAL_FREEZE)
            value['pendingReviewIds'][0] = 'R001'
            self.put(r.PARTIAL_FREEZE, value)
            with self.assertRaisesRegex(ValueError, 'partial_recovery_freeze_binding'):
                r.load_partial_recovery(self.root)

    def coordinator_fixture(self, fail=False, deadline=False):
        identity = self.stack.enter_context(partial_fixture(self.root))
        export = {'packets': [{'reviewId': rid} for rid in r.contract.IDS],
                  'evidence': [{'path': 'synthetic-export', 'sha256': 'a' * 64}],
                  'inventoryCorrection': {}, 'zeroCallRecovery': identity['batteryExecution']['zeroCallRecovery'],
                  'batteryExecution': identity['batteryExecution'], 'partialRecovery': identity}
        prior = {'export': deepcopy(export), 'installation': {}, 'template': 'template', 'metadata': {}}
        events, calls = [], []
        class Scope:
            def __enter__(self):
                self.receipt = {'requested': True, 'released': False}
                return self
            def __exit__(self, *args):
                self.receipt['released'] = True
        class Clock:
            now = 0
            def monotonic(self): return self.now
        clock = Clock()
        def wait(path, root):
            events.append(str(path))
            ref = self.put(path, b'synthetic passed wait')
            if deadline and '/waits/' in str(path).replace('\\', '/'):
                clock.now = r.PROFILE['totalSeconds']
            return {'file': ref}
        def context(packet, folder, *args):
            calls.append(packet['reviewId'])
            self.assertTrue((self.root / r.PARTIAL_CLAIM).exists())
            self.put(folder / 'plan.json', {'version': original.VERSION})
            self.put(folder / 'process.json', {'fixture': True})
            self.put(folder / 'summary.json', {'version': original.VERSION, 'completionRequestsSent': 1, 'nativeStopped': True})
            if fail: raise ValueError('question_context_failed')
            return {'version': original.VERSION}
        fake_native = types.SimpleNamespace(VERSION=native.VERSION, execute_context=context)
        fake_resources = types.SimpleNamespace(ExperimentLock=Scope, require=r.resources.require, ResourceGuardError=r.resources.ResourceGuardError)
        values = {'validate_prior': lambda *args: prior, 'load_export': lambda *args: deepcopy(export),
                  'load_partial_recovery': lambda *args: deepcopy(identity), 'python_identity': lambda: {},
                  'render_prompt': lambda *args: '', 'time': clock, 'resources': fake_resources, 'PowerRequest': Scope,
                  'wait_for_memory': wait, 'check_installation_stat': lambda *args: None, 'verify_refs': lambda *args: None,
                  'installation': lambda *args: {}, 'native': fake_native, 'write_json': self.put, 'write_new': self.put,
                  'snapshot': lambda *args: [], 'validate_run': lambda path, *args: {'summary': r.read(path / 'summary.json')[0]}}
        for key, value in values.items(): self.stack.enter_context(patch.object(r, key, value))
        return identity, export, events, calls

    def test_only_pending_55_are_dispatched_original_nine_unchanged(self):
        identity, export, events, calls = self.coordinator_fixture()
        before = {rid: (self.root / r.PRIOR_RUN / rid / 'raw.fixture').read_bytes() for rid in r.RETAINED_IDS}
        with redirect_stdout(io.StringIO()):
            result = r.run('export', r.DESTINATION, self.root)
        self.assertEqual(calls, list(r.PENDING_IDS))
        self.assertEqual(result['summary']['expectedContexts'], 55)
        self.assertEqual(result['summary']['completedContexts'], 55)
        self.assertEqual(result['summary']['completionRequestsSent'], 55)
        self.assertEqual(result['summary']['contextProducerVersion'], native.VERSION)
        for rid, raw in before.items(): self.assertEqual((self.root / r.PRIOR_RUN / rid / 'raw.fixture').read_bytes(), raw)

    def test_native_failure_is_not_retried_and_claim_is_preserved(self):
        identity, export, events, calls = self.coordinator_fixture(fail=True)
        with self.assertRaisesRegex(ValueError, 'question_run_failed'):
            r.run('export', r.DESTINATION, self.root)
        self.assertEqual(calls, ['R010'])
        self.assertTrue((self.root / r.PARTIAL_CLAIM).exists())
        summary = r.read(self.root / r.DESTINATION / 'summary.json')[0]
        self.assertEqual(summary['status'], 'failed')
        self.assertEqual(summary['completionRequestsSent'], 1)
        self.assertTrue(summary['powerRequest']['released'])

    def test_twelve_hour_deadline_after_wait_prevents_native_start(self):
        identity, export, events, calls = self.coordinator_fixture(deadline=True)
        with self.assertRaisesRegex(ValueError, 'question_run_failed'):
            r.run('export', r.DESTINATION, self.root)
        self.assertEqual(calls, [])
        self.assertEqual(r.read(self.root / r.DESTINATION / 'summary.json')[0]['completionRequestsSent'], 0)

    def test_static_replay_loop_retains_every_frozen_v4_context_check(self):
        source = Path(original.__file__).read_text(encoding='utf-8')
        old_fn = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'validate_run')
        old_loop = next(n for n in old_fn.body if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == 'packet')
        new_fn = next(n for n in ast.parse(Path(r.__file__).read_text(encoding='utf-8')).body if isinstance(n, ast.FunctionDef) and n.name == 'replay_contexts')
        new_loop = next(n for n in new_fn.body if isinstance(n, ast.For))
        new_loop = deepcopy(new_loop)
        new_loop.iter = deepcopy(old_loop.iter)
        class Normalize(ast.NodeTransformer):
            def visit_Attribute(self, node):
                if isinstance(node.value, ast.Name) and node.value.id == 'native' and node.attr == 'VERSION':
                    return ast.Name(id='VERSION', ctx=ast.Load())
                return self.generic_visit(node)
        self.assertEqual(ast.dump(Normalize().visit(new_loop), include_attributes=False), ast.dump(old_loop, include_attributes=False))
        self.assertIs(r.parity, original.parity)
        self.assertIs(r.ReplayClient, original.ReplayClient)

    def logical_rows(self):
        return [{'reviewId': rid, 'contextProducerVersion': original.VERSION if index < 9 else native.VERSION,
                 'contextId': f'context-{index}', 'process': {'pid': 1000 + index, 'creationTicks': 10000 + index},
                 'draft': {'reviewId': rid, 'answers': ['immutable-synthetic-answer']}}
                for index, rid in enumerate(r.contract.IDS)]

    def test_logical64_keeps_nine_prior_and_55_new_provenance_without_rewriting_drafts(self):
        rows = self.logical_rows()
        before = deepcopy(rows)
        result = r.combine_contexts(rows[:9], rows[9:])
        self.assertEqual(rows, before)
        self.assertEqual([row['reviewId'] for row in result], list(r.contract.IDS))
        self.assertTrue(all(row['sourceRun'] == {'runPath': r.PRIOR_RUN, 'coordinatorVersion': original.VERSION, 'status': 'failed', 'reused': True} for row in result[:9]))
        self.assertTrue(all(row['sourceRun'] == {'runPath': r.DESTINATION, 'coordinatorVersion': r.VERSION, 'status': 'completed', 'reused': False} for row in result[9:]))
        for original_row, final_row in zip(rows, result): self.assertIs(final_row['draft'], original_row['draft'])

    def test_logical64_rejects_missing_duplicate_crosspartition_and_relabelled_rows(self):
        rows = self.logical_rows()
        variants = []
        variants.append((rows[:9], rows[10:]))
        changed = deepcopy(rows); changed[9]['reviewId'] = 'R001'; variants.append((changed[:9], changed[9:]))
        changed = deepcopy(rows); changed[10]['contextId'] = changed[0]['contextId']; variants.append((changed[:9], changed[9:]))
        changed = deepcopy(rows); changed[10]['process'] = changed[0]['process']; variants.append((changed[:9], changed[9:]))
        changed = deepcopy(rows); changed[0]['contextProducerVersion'] = r.VERSION; variants.append((changed[:9], changed[9:]))
        for retained, fresh in variants:
            with self.assertRaises(ValueError): r.combine_contexts(retained, fresh)


    def test_exact_memory_policy_changes_only_physical_start_and_preserves_v5(self):
        with partial_fixture(self.root) as identity:
            self.assertEqual(identity['originalProfile'], original.PROFILE)
            self.assertEqual(identity['profile'], native.PROFILE)
            self.assertEqual(identity['profile']['minimumStartPhysicalBytes'], 7 * 1024 ** 3)
            self.assertEqual(identity['profile']['minimumStartCommitBytes'], 9 * 1024 ** 3)
            self.assertEqual({k for k in identity['profile'] if identity['profile'][k] != identity['originalProfile'][k]},
                             {'minimumStartPhysicalBytes'})
            self.assertEqual(identity['previousPartialRecovery']['version'], r.v5.PARTIAL_VERSION)
            self.assertEqual(len(identity['files']), 11)
            value, _ = r.read(self.root / r.PARTIAL_FREEZE)
            value['memoryPolicy']['minimumStartCommitBytes'] = 7 * 1024 ** 3
            self.put(r.PARTIAL_FREEZE, value)
            with self.assertRaisesRegex(ValueError, 'partial_recovery_freeze_binding'):
                r.load_partial_recovery(self.root)

    def test_unconsumed_v5_entrypoint_block_marker_is_explicit_not_execution(self):
        with partial_fixture(self.root) as identity:
            export = {'partialRecovery': identity}
            frozen = (self.root / r.v5.PARTIAL_FREEZE).read_bytes()
            ref = r.write_supersession(self.root, export)
            value = r.validate_supersession(ref, export, self.root)
            self.assertIs(value['representsV5Execution'], False)
            self.assertEqual(value['completionRequestsSent'], 0)
            self.assertFalse((self.root / r.v5.DESTINATION).exists())
            self.assertEqual((self.root / r.v5.PARTIAL_FREEZE).read_bytes(), frozen)
            self.assertEqual(r.load_partial_recovery(self.root), identity)
            with self.assertRaisesRegex(ValueError, 'old_entrypoint_already_consumed'):
                r.require_old_unconsumed(self.root)
            value['representsV5Execution'] = True
            self.put(r.SUPERSESSION_PATH, value)
            with self.assertRaisesRegex(ValueError, 'memory_supersession_binding'):
                r.validate_supersession(r.reference(self.root, r.SUPERSESSION_PATH), export, self.root)

    def test_v5_existing_claim_or_run_cannot_be_replaced_by_v6(self):
        with partial_fixture(self.root):
            self.put(r.v5.PARTIAL_CLAIM, b'preexisting claim')
            with self.assertRaisesRegex(ValueError, 'old_entrypoint_already_consumed'):
                r.require_old_unconsumed(self.root)
            self.assertEqual((self.root / r.v5.PARTIAL_CLAIM).read_bytes(), b'preexisting claim')

    def test_original_prior_validator_is_reused_without_profile_override(self):
        self.assertIs(r.validate_prior, r.v5.validate_prior)
        self.assertIs(r.prior_snapshot, r.v5.prior_snapshot)
        self.assertEqual(r.v5.PROFILE['minimumStartPhysicalBytes'], 9 * 1024 ** 3)
        self.assertEqual(original.PROFILE['minimumStartPhysicalBytes'], 9 * 1024 ** 3)

    def test_actual_admission_replay_binds_marker_claim_power_and_memory_receipts(self):
        with partial_fixture(self.root) as identity:
            self.wait_fixture([state()])
            initial = r.wait_for_memory('.training/verifications/initial.jsonl', self.root)
            entered = r.utc()
            powered = r.wait_for_memory('.training/verifications/powered.jsonl', self.root)
            consumed = r.utc()
            folder = self.root / r.DESTINATION
            admission = {'version': r.VERSION + '-admission', 'initialWait': initial['file'],
                         'poweredWait': powered['file'], 'powerEnteredAt': entered, 'consumedAt': consumed,
                         'powerBeforeConsumption': {'requested': True, 'released': False}}
            admission_ref = self.put(folder / 'admission.json', admission)
            export = {'partialRecovery': identity,
                      'evidence': [self.put(r.BASE + '/export-manifest.json', {'fixture': True})]}
            plan = {'partialClaimPath': r.PARTIAL_CLAIM, 'oldEntrypointSupersessionPath': r.SUPERSESSION_PATH,
                    'partialRecovery': identity, 'cohortExpectedContexts': 64, 'retainedContexts': 9,
                    'startedAt': consumed, 'admission': admission_ref}
            self.put(folder / 'plan.json', plan)
            marker = r.write_supersession(self.root, export)
            claim = {'version': r.VERSION, 'runPath': r.DESTINATION,
                     'partialRecoveryFreeze': identity['partialRecoveryFreeze'],
                     'priorRunSummary': identity['priorRun']['summary'], 'priorClaim': identity['priorClaim'],
                     'exportManifest': export['evidence'][0], 'plan': r.reference(self.root, folder / 'plan.json'),
                     'admission': admission_ref, 'oldEntrypointSupersession': marker, 'at': r.utc()}
            claim_ref = self.put(r.PARTIAL_CLAIM, claim)
            summary = {'partialRecovery': identity, 'cohortExpectedContexts': 64, 'retainedContexts': 9,
                       'partialClaim': claim_ref, 'oldEntrypointSupersession': marker}
            actual, refs = r.validate_new_admission(folder, plan, summary, export, self.root)
            self.assertEqual(actual, claim)
            self.assertEqual(refs, [initial['file'], powered['file'], marker])
            changed = deepcopy(claim)
            changed['oldEntrypointSupersession']['sha256'] = '0' * 64
            summary['partialClaim'] = self.put(r.PARTIAL_CLAIM, changed)
            with self.assertRaisesRegex(ValueError, 'partial_claim_binding'):
                r.validate_new_admission(folder, plan, summary, export, self.root)


if __name__ == '__main__':
    unittest.main()

