"""Partial cohort and memory admission fixtures; no real model/HTTP or power API."""
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
from input_execution_v1 import local_question_runner_partial_recovery_v5 as r
from input_execution_v1 import local_question_runner_battery_v4 as original


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
    """Real prior closure and partial loader; frozen battery/admission boundary stubbed."""
    root = Path(root).resolve()
    folder = root / r.PRIOR_RUN
    battery_ref = write(root, original.BATTERY_FREEZE, b'synthetic battery freeze')
    battery = {'batteryFreeze': battery_ref, 'zeroCallRecovery': {'fixture': True}, 'version': original.BATTERY_VERSION}
    export_ref = {'path': r.BASE + '/export/manifest.json', 'sha256': 'a' * 64}
    for rid in r.RETAINED_IDS:
        write(root, folder / rid / 'raw.fixture', ('original-' + rid).encode())
    failed_plan = {'version': original.VERSION, 'reviewId': 'R010', 'previousModelRequests': 0,
                   'profile': r.PROFILE, 'sampling': r.contract.NATIVE_SAMPLING,
                   'modelSha256': r.contract.MODEL_SHA, 'sourceExposure': False}
    write(root, folder / 'R010/plan.json', failed_plan)
    write(root, folder / 'R010/input-packet.json', b'synthetic question fixture')
    write(root, folder / 'R010/preflight.json', [
        {'monotonicSeconds': 0, 'system': state(r.PROFILE['minimumStartPhysicalBytes'] - 1)},
        {'monotonicSeconds': 3, 'system': state()}])
    write(root, folder / 'R010/summary.json', {**failed_plan, 'status': 'failed',
          'failure': {'type': 'ResourceGuardError', 'code': 'insufficient_start_availablephysicalbytes'},
          'completionRequestsSent': 0, 'shutdown': {'processCreated': False, 'stopped': True},
          'monitorError': None, 'validatedDraft': None, 'artifactFiles': r.snapshot(folder / 'R010', root)})
    write(root, folder / 'plan.json', {'version': original.VERSION, 'batteryExecution': battery, 'exportManifest': export_ref})
    write(root, folder / 'summary.json', {'version': original.VERSION, 'contextProducerVersion': original.VERSION,
          'status': 'failed', 'failure': {'type': 'ValueError', 'code': 'question_context_failed'},
          'expectedContexts': 64, 'completedContexts': 9, 'freshNativeProcesses': 9, 'completionRequestsSent': 9,
          'nativeStopped': True, 'powerRequest': {'released': True}, 'batteryExecution': battery,
          'artifactFiles': r.snapshot(folder, root)})
    write(root, original.RECOVERY_CLAIM, b'original claim')
    write(root, original.SUPERSESSION_PATH, b'original supersession')
    write(root, r.PRIOR_REVIEW, b'prior audit')
    pins = {path: r.file_sha(root / path) for path in r.PRIOR_PINS}
    fake = types.SimpleNamespace(VERSION=original.VERSION, RECOVERY_CLAIM=original.RECOVERY_CLAIM,
             SUPERSESSION_PATH=original.SUPERSESSION_PATH, load_battery=lambda root: deepcopy(battery),
             battery_refs=lambda identity: [identity['batteryFreeze']],
             validate_admission=lambda *args: {'at': '2026-09-14T00:00:00+00:00'},
             validate_preflight=original.validate_preflight)
    with patch.object(r, 'v4', fake), patch.object(r, 'PRIOR_PINS', pins), patch.object(r, 'BATTERY_FREEZE_SHA', battery_ref['sha256']):
        expected = r.partial_freeze_inputs(root)
        files = [write(root, path, b'synthetic partial code') for path in sorted(r.REQUIRED_PARTIAL_FILES)]
        write(root, r.PARTIAL_FREEZE, {'version': r.PARTIAL_VERSION, **expected, 'actualNewQuestionCallsAtFreeze': 0, 'files': files})
        yield r.load_partial_recovery(root)


class PartialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='question-partial-v5-')
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
        fake4 = types.SimpleNamespace(VERSION=original.VERSION, execute_context=context)
        fake_resources = types.SimpleNamespace(ExperimentLock=Scope, require=r.resources.require, ResourceGuardError=r.resources.ResourceGuardError)
        values = {'validate_prior': lambda *args: prior, 'load_export': lambda *args: deepcopy(export),
                  'load_partial_recovery': lambda *args: deepcopy(identity), 'python_identity': lambda: {},
                  'render_prompt': lambda *args: '', 'time': clock, 'resources': fake_resources, 'PowerRequest': Scope,
                  'wait_for_memory': wait, 'check_installation_stat': lambda *args: None, 'verify_refs': lambda *args: None,
                  'installation': lambda *args: {}, 'v4': fake4, 'write_json': self.put, 'write_new': self.put,
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
        self.assertEqual(result['summary']['contextProducerVersion'], original.VERSION)
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
                if isinstance(node.value, ast.Name) and node.value.id == 'v4' and node.attr == 'VERSION':
                    return ast.Name(id='VERSION', ctx=ast.Load())
                return self.generic_visit(node)
        self.assertEqual(ast.dump(Normalize().visit(new_loop), include_attributes=False), ast.dump(old_loop, include_attributes=False))
        self.assertIs(r.parity, original.parity)
        self.assertIs(r.ReplayClient, original.ReplayClient)

    def logical_rows(self):
        return [{'reviewId': rid, 'contextProducerVersion': original.VERSION,
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


if __name__ == '__main__':
    unittest.main()
