"""7 GiB physical boundary and unchanged context cleanup; synthetic native only."""
import ast
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_context_memory_v6 as r
from input_execution_v1 import local_question_runner_battery_v4 as v4


def observations(percent=85, physical=7 * 1024 ** 3, commit=9 * 1024 ** 3):
    return [{'at': '2026-09-20T00:00:0' + str(i * 3) + '+00:00', 'monotonicSeconds': i * 3,
             'system': {'ACLineStatus': 1, 'BatteryLifePercent': percent, 'availablePhysicalBytes': physical,
                        'availableCommitBytes': commit},
             'processes': {'nativeConflicts': [], 'classifiedConflicts': []}} for i in range(2)]


class MemoryContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='question-memory-context-v6-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

    def put(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else r.packed(value))
        return v4.reference(self.root, target)

    def test_exactly_one_profile_field_changes_and_runtime_helpers_identical(self):
        self.assertEqual(set(r.PROFILE), set(v4.PROFILE))
        self.assertEqual({k for k in r.PROFILE if r.PROFILE[k] != v4.PROFILE[k]}, {'minimumStartPhysicalBytes'})
        self.assertEqual(r.PROFILE['minimumStartPhysicalBytes'], 7 * 1024 ** 3)
        for name in ('Monitor', 'Client', 'spawn', 'parity', 'server_command', 'resource_reason', 'PowerRequest'):
            self.assertIs(getattr(r, name), getattr(v4, name))

    def test_physical7_boundary_commit9_and_two_samples(self):
        pair = observations()
        self.assertEqual(r.validate_preflight(pair), pair)
        with self.assertRaisesRegex(v4.resources.ResourceGuardError, 'insufficient_start_availablephysicalbytes'):
            v4.validate_preflight(pair)
        for index in (0, 1):
            low = observations(); low[index]['system']['availablePhysicalBytes'] -= 1
            with self.assertRaisesRegex(v4.resources.ResourceGuardError, 'insufficient_start_availablephysicalbytes'):
                r.validate_preflight(low)
            low = observations(); low[index]['system']['availableCommitBytes'] -= 1
            with self.assertRaisesRegex(v4.resources.ResourceGuardError, 'insufficient_start_availablecommitbytes'):
                r.validate_preflight(low)

    def test_runtime1_gib_and6_gib_observed_limits_remain(self):
        memory = observations()[0]['system']
        for key in ('availablePhysicalBytes', 'availableCommitBytes'):
            low = dict(memory); low[key] = 1024 ** 3 - 1
            self.assertEqual(r.resource_reason(low), 'insufficient_running_' + key)
        child = {'workingSetBytes': 6 * 1024 ** 3, 'peakWorkingSetBytes': 6 * 1024 ** 3, 'privateBytes': 6 * 1024 ** 3}
        self.assertIsNone(r.resource_reason(memory, child))
        self.assertEqual(r.resource_reason(memory, {**child, 'peakWorkingSetBytes': child['peakWorkingSetBytes'] + 1}), 'working_set_limit_exceeded')
        self.assertEqual(r.resource_reason(memory, {**child, 'privateBytes': child['privateBytes'] + 1}), 'private_bytes_observation_exceeded')
        self.assertEqual(r.PROFILE['workingSetApiMaximumBytes'], 6 * 1024 ** 3 - 64 * 1024 ** 2)

    def test_context_and_preflight_ast_match_v4_without_monkeypatch(self):
        def nodes(module):
            return {n.name: n for n in ast.parse(Path(module.__file__).read_text(encoding='utf-8')).body if isinstance(n, ast.FunctionDef)}
        left, right = nodes(r), nodes(v4)
        for name in ('observe_preflight', 'validate_preflight', 'preflight_pair', 'execute_context'):
            self.assertEqual(ast.dump(left[name], include_attributes=False), ast.dump(right[name], include_attributes=False))
        self.assertIs(r.execute_context.__globals__['PROFILE'], r.PROFILE)
        self.assertIs(r.execute_context.__globals__['validate_preflight'], r.validate_preflight)
        tree = ast.parse(Path(r.__file__).read_text(encoding='utf-8'))
        self.assertFalse(any(isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store) and
                             isinstance(n.value, ast.Name) and n.value.id in ('v4', 'resources') for n in ast.walk(tree)))

    def context_fixture(self, percent=85, late=False, cleanup_error=False, physical=7 * 1024 ** 3):
        folder = self.root / 'context'
        packet = {'reviewId': 'R001', 'translation': 'synthetic Korean-only input', 'questions': []}
        export = {'evidence': [{'path': 'synthetic-export', 'sha256': 'a' * 64}]}
        events = []
        process = types.SimpleNamespace(pid=123, poll=lambda: None)
        def spawn(owner, argv, log, path):
            events.append('spawn')
            return process, None, {'pid': 123}
        def stop(process, owner):
            events.append('stop')
            if cleanup_error:
                raise OSError('synthetic cleanup failure')
            return {'stopped': True, 'pid': 123, 'terminationByRetainedHandle': True}
        case = self
        class Client:
            def __init__(self, *args): self.completions = 0
            def request(self, endpoint, *args, **kwargs):
                if endpoint == '/health': return {'status': 'ok'}
                case.assertEqual(endpoint, '/completion')
                events.append('completion')
                self.completions += 1
                case.put(folder / 'raw-response.bin', b'{"synthetic":true}')
                return {'synthetic': True}
        class Monitor:
            def __init__(self, *args): self.error, self.deadline = None, None
            def start(self): pass
            def sample(self): pass
            def check(self): pass
            def close(self):
                events.append('monitor-close')
                if late: self.error = 'battery_reserve_reached'
        fake_resources = types.SimpleNamespace(ResourceGuardError=r.resources.ResourceGuardError,
                require=r.resources.require, process_owner=types.SimpleNamespace(claim_process_owner=lambda: object()), stop_owned=stop)
        fake_contract = types.SimpleNamespace(MODEL_SHA=r.contract.MODEL_SHA, NATIVE_SAMPLING=r.contract.NATIVE_SAMPLING,
                completion_payload=lambda *args: {'synthetic': True}, validate_native=lambda *args: {'reviewId': 'R001', 'answers': []})
        values = {'resources': fake_resources, 'contract': fake_contract, 'Client': Client, 'Monitor': Monitor,
                  'observe_preflight': lambda: observations(percent, physical), 'spawn': spawn,
                  'check_installation_stat': lambda *args: None, 'verify_refs': lambda *args: None,
                  'parity': lambda *args: {'completionRequestsSent': 0, 'prompt': 'synthetic prompt', 'tokenIds': [1]},
                  'write_json': self.put, 'write_new': self.put, 'snapshot': lambda *args: []}
        for name, value in values.items():
            self.stack.enter_context(patch.object(r, name, value))
        return folder, packet, export, events

    def test_context_low_battery_keeps_preflight_and_exact_code_without_spawn(self):
        folder, packet, export, events = self.context_fixture(percent=20)
        with self.assertRaisesRegex(ValueError, 'question_context_failed'):
            r.execute_context(packet, folder, {}, 'template', {}, export, self.root, time.monotonic())
        self.assertFalse(events)
        saved = v4.read(folder / 'preflight.json')[0]
        self.assertEqual([x['system']['BatteryLifePercent'] for x in saved], [20, 20])
        summary = v4.read(folder / 'summary.json')[0]
        self.assertEqual(summary['failure'], {'type': 'ResourceGuardError', 'code': 'battery_reserve_reached'})
        self.assertEqual(summary['completionRequestsSent'], 0)

    def test_late_battery_latch_fails_after_join_and_owned_stop(self):
        folder, packet, export, events = self.context_fixture(late=True)
        with self.assertRaisesRegex(ValueError, 'question_context_failed'):
            r.execute_context(packet, folder, {}, 'template', {}, export, self.root, time.monotonic())
        summary = v4.read(folder / 'summary.json')[0]
        self.assertEqual(events, ['spawn', 'completion', 'monitor-close', 'stop'])
        self.assertEqual(summary['version'], r.VERSION)
        self.assertEqual(summary['failure'], {'type': 'ResourceMonitorError', 'code': 'battery_reserve_reached'})
        self.assertTrue(summary['nativeStopped'])
        self.assertEqual(summary['completionRequestsSent'], 1)
        self.assertTrue((folder / 'raw-response.bin').is_file())
        self.assertTrue((folder / 'draft.json').is_file())

    def test_unexpected_cleanup_does_not_claim_native_stopped(self):
        folder, packet, export, events = self.context_fixture(cleanup_error=True)
        with self.assertRaisesRegex(ValueError, 'question_context_failed'):
            r.execute_context(packet, folder, {}, 'template', {}, export, self.root, time.monotonic())
        summary = v4.read(folder / 'summary.json')[0]
        self.assertFalse(summary['nativeStopped'])
        self.assertTrue(summary['shutdown']['processCreated'])
        self.assertTrue(summary['shutdown']['cleanupUnconfirmed'])
        self.assertEqual(summary['failure']['type'], 'OSError')


    def test_physical7_context_uses_new_profile_and_exactly_one_native_call(self):
        folder, packet, export, events = self.context_fixture()
        result = r.execute_context(packet, folder, {}, 'template', {}, export, self.root, time.monotonic())
        self.assertEqual(events, ['spawn', 'completion', 'monitor-close', 'stop'])
        self.assertEqual(result['version'], r.VERSION)
        self.assertEqual(result['profile'], r.PROFILE)
        self.assertEqual(result['completionRequestsSent'], 1)
        self.assertTrue(result['nativeStopped'])

    def test_below_physical7_never_spawns_and_keeps_failed_observations(self):
        folder, packet, export, events = self.context_fixture(physical=7 * 1024 ** 3 - 1)
        with self.assertRaisesRegex(ValueError, 'question_context_failed'):
            r.execute_context(packet, folder, {}, 'template', {}, export, self.root, time.monotonic())
        self.assertEqual(events, [])
        summary = v4.read(folder / 'summary.json')[0]
        self.assertEqual(summary['completionRequestsSent'], 0)
        self.assertEqual(summary['failure']['code'], 'insufficient_start_availablephysicalbytes')
        self.assertTrue((folder / 'preflight.json').exists())


if __name__ == '__main__':
    unittest.main()

