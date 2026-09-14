"""Battery boundaries/owned stop/lineage fixtures; no model or real Windows API."""
import ast
from copy import deepcopy
from contextlib import contextmanager, ExitStack
import io
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_runner_battery_v4 as r
from input_execution_v1 import local_question_runner_v1 as old
from input_execution_v1 import local_question_runner_recovery_v3 as v3


def write(root, path, value):
    target = Path(root) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(value if isinstance(value, bytes) else r.packed(value))
    return r.reference(root, target)


@contextmanager
def battery_fixture(root):
    """Real battery loader, synthetic frozen-v3 loader boundary; source/QA absent."""
    root = Path(root).resolve()
    prior = {'version': v3.RECOVERY_VERSION,
             'priorRun': {'path': v3.PRIOR_RUN, 'files': [write(root, v3.PRIOR_RUN + '/' + name, b'prior fixture') for name in ('plan.json', 'summary.json', 'model-chat-template.jinja')],
                          'exportManifest': {'path': r.BASE + '/export/manifest.json', 'sha256': 'a' * 64}},
             'priorClaim': write(root, v3.PRIOR_CLAIM, b'original claim fixture'),
             'priorReview': write(root, v3.PRIOR_REVIEW, b'prior review fixture'),
             'baseExecutionFreeze': write(root, r.FREEZE, b'core freeze fixture'),
             'inventoryCorrectionFreeze': write(root, v3.v2.CORRECTION_FREEZE, b'inventory freeze fixture'),
             'recoveryFreeze': write(root, v3.RECOVERY_FREEZE, b'recovery freeze fixture'),
             'files': [write(root, path, b'recovery code fixture') for path in sorted(v3.REQUIRED_RECOVERY_FILES)]}
    files = [write(root, path, b'battery code fixture') for path in sorted(r.REQUIRED_BATTERY_FILES)]
    write(root, r.BATTERY_FREEZE, {'version': r.BATTERY_VERSION, 'zeroCallRecovery': prior,
                                'userAuthorization': r.USER_AUTHORIZATION, 'powerPolicy': r.POWER_POLICY,
                                'actualQuestionCallsAtFreeze': 0, 'files': files})
    fake = types.SimpleNamespace(VERSION=v3.VERSION, DESTINATION=v3.DESTINATION,
                                 load_recovery=lambda root: deepcopy(prior), recovery_refs=v3.recovery_refs)
    with patch.object(r, 'v3', fake), patch.object(r, 'PRIOR_RECOVERY_FREEZE_SHA', prior['recoveryFreeze']['sha256']):
        yield r.load_battery(root)


def state(percent=85, ac=0):
    return {'ACLineStatus': ac, 'BatteryLifePercent': percent, 'BatteryFlag': 1,
            'availablePhysicalBytes': 10 * 1024 ** 3, 'availableCommitBytes': 10 * 1024 ** 3}


def observations(percent=85):
    return [{'at': '2026-09-14T00:00:0' + str(i * 3) + '+00:00', 'monotonicSeconds': i * 3,
             'system': state(percent), 'processes': {'nativeConflicts': [], 'classifiedConflicts': []}} for i in range(2)]


class BatteryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='question-battery-v4-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

    def put(self, path, value):
        return write(self.root, path, value)

    def test_battery_boundaries_and_unknowns_on_ac0_and_ac1(self):
        for ac in (0, 1):
            for percent in (0, 1, 20):
                self.assertEqual(r.resource_reason(state(percent, ac)), 'battery_reserve_reached')
            for percent in (21, 85, 100):
                self.assertIsNone(r.resource_reason(state(percent, ac)))
            for percent in (-1, 101, 255, None, True, '85'):
                self.assertEqual(r.resource_reason(state(percent, ac)), 'battery_level_unknown')
        for ac in (255, None, True, -1):
            self.assertEqual(r.resource_reason(state(85, ac)), 'ac_line_status_unknown')

    def test_non_power_limits_and_frozen_native_helpers_unchanged(self):
        old_profile = {k: v for k, v in old.PROFILE.items() if k != 'requiresACLineStatus'}
        actual = {k: r.PROFILE[k] for k in old_profile}
        self.assertEqual(actual, old_profile)
        for name in ('Client', 'spawn', 'parity', 'server_command', 'ReplayClient'):
            self.assertIs(getattr(r, name), getattr(old, name))
        child = {'workingSetBytes': 1, 'peakWorkingSetBytes': 1, 'privateBytes': r.PROFILE['maximumPrivateBytes'] + 1}
        self.assertEqual(r.resource_reason(state(), child), 'private_bytes_observation_exceeded')
        low = state()
        low['availablePhysicalBytes'] = 1
        self.assertEqual(r.resource_reason(low), 'insufficient_running_availablePhysicalBytes')

    def test_system_state_uses_single_power_snapshot_with_percent(self):
        calls = []
        class Function:
            def __init__(self, callback): self.callback = callback
            def __call__(self, pointer): return self.callback(pointer._obj)
        def memory(value):
            value.availablePhysical = value.availableCommit = 10 * 1024 ** 3
            return 1
        def power(value):
            calls.append('GetSystemPowerStatus')
            value.ACLineStatus, value.BatteryLifePercent, value.BatteryFlag = 0, 85, 1
            return 1
        kernel = types.SimpleNamespace(GlobalMemoryStatusEx=Function(memory), GetSystemPowerStatus=Function(power))
        fake = types.SimpleNamespace(_kernel=lambda: kernel, _MemoryStatus=r.resources._MemoryStatus,
                                     _PowerStatus=r.resources._PowerStatus, require=r.resources.require)
        with patch.object(r, 'resources', fake):
            actual = r.system_state()
        self.assertEqual(calls, ['GetSystemPowerStatus'])
        self.assertEqual((actual['ACLineStatus'], actual['BatteryLifePercent']), (0, 85))

    def test_temporary_power_request_on_battery_releases_even_after_body_failure(self):
        calls = []
        class Function:
            def __call__(self, flag):
                calls.append(flag)
                return 1
        kernel = types.SimpleNamespace(SetThreadExecutionState=Function())
        fake = types.SimpleNamespace(_kernel=lambda: kernel, require=r.resources.require)
        with patch.object(r, 'resources', fake), patch.object(r, 'system_state', return_value=state(21)):
            with self.assertRaisesRegex(RuntimeError, 'synthetic'):
                with r.PowerRequest() as power:
                    raise RuntimeError('synthetic')
        self.assertEqual(calls, [0x80000003, 0x80000000])
        self.assertTrue(power.receipt['released'])
        self.assertFalse(power.active)

    def test_power_rejects_twenty_without_calling_set_execution_state(self):
        with patch.object(r, 'system_state', return_value=state(20)), patch.object(r.resources, '_kernel') as kernel:
            with self.assertRaisesRegex(r.resources.ResourceGuardError, 'battery_reserve_reached'):
                with r.PowerRequest():
                    self.fail('must not enter')
        kernel.assert_not_called()

    def test_monitor_drop_kills_only_after_owned_handle_assertions(self):
        folder = self.root / 'monitor'
        folder.mkdir()
        events = []
        process = types.SimpleNamespace(pid=12, _handle=123, poll=lambda: None, kill=lambda: events.append('kill'))
        owner = types.SimpleNamespace(_handle=321, assert_owned=lambda: events.append('owner'),
                    _api=types.SimpleNamespace(assert_child=lambda *args: events.append('child')))
        limiter = types.SimpleNamespace(owner=owner, sample_child=lambda process: {'workingSetBytes': 1, 'peakWorkingSetBytes': 1, 'privateBytes': 1})
        monitor = r.Monitor(process, limiter, folder, time.monotonic())
        with patch.object(r, 'system_state', return_value=state(20)):
            with self.assertRaisesRegex(ValueError, 'battery_reserve_reached'):
                monitor.sample()
        monitor.close()
        self.assertEqual(events, ['owner', 'child', 'kill'])
        self.assertEqual(monitor.error, 'battery_reserve_reached')
        self.assertTrue((folder / 'resource-samples.jsonl').read_bytes())

    def test_known_low_and_unknown_preflight_refused(self):
        for percent, code in ((20, 'battery_reserve_reached'), (255, 'battery_level_unknown')):
            with self.assertRaisesRegex(r.resources.ResourceGuardError, code):
                r.validate_preflight(observations(percent))
        self.assertEqual(r.validate_preflight(observations(21)), observations(21))

    def test_actual_battery_loader_fixture_and_hash_authorization_rejection(self):
        with battery_fixture(self.root) as identity:
            self.assertEqual(r.load_battery(self.root), identity)
            r.verify_refs(self.root, r.battery_refs(identity))
            value, _ = r.read(self.root / r.BATTERY_FREEZE)
            value['userAuthorization'] = {'acOverride': True}
            self.put(r.BATTERY_FREEZE, value)
            with self.assertRaisesRegex(ValueError, 'battery_freeze_binding'):
                r.load_battery(self.root)

    def test_battery_freeze_rejects_extra_source_or_changed_code(self):
        with battery_fixture(self.root):
            value, _ = r.read(self.root / r.BATTERY_FREEZE)
            value['files'].append(self.put('source.json', b'forbidden fixture'))
            self.put(r.BATTERY_FREEZE, value)
            with self.assertRaisesRegex(ValueError, 'battery_exact_code_inventory'):
                r.load_battery(self.root)
            value['files'].pop()
            self.put(r.BATTERY_FREEZE, value)
            self.put(value['files'][0]['path'], b'changed')
            with self.assertRaisesRegex(ValueError, 'evidence_hash_changed'):
                r.load_battery(self.root)

    def test_supersession_is_explicit_block_marker_and_preserves_original_claim(self):
        with battery_fixture(self.root) as identity:
            before = (self.root / v3.PRIOR_CLAIM).read_bytes()
            export = {'batteryExecution': identity, 'zeroCallRecovery': identity['zeroCallRecovery']}
            with patch.object(r, 'write_json', self.put):
                ref = r.write_supersession(self.root, export)
            marker = r.validate_supersession(self.root, ref, export)
            self.assertFalse(marker['representsV3Execution'])
            self.assertEqual(marker['completionRequestsSent'], 0)
            self.assertEqual((self.root / v3.PRIOR_CLAIM).read_bytes(), before)
            self.assertFalse((self.root / v3.DESTINATION).exists())
            with self.assertRaisesRegex(ValueError, 'old_entrypoint_already_consumed'):
                r.write_supersession(self.root, export)
            marker['representsV3Execution'] = True
            ref = self.put(r.SUPERSESSION_PATH, marker)
            with self.assertRaisesRegex(ValueError, 'supersession_marker_binding'):
                r.validate_supersession(self.root, ref, export)

    def test_low_battery_admission_consumes_no_claim_marker_or_run(self):
        with battery_fixture(self.root) as identity:
            export = {'batteryExecution': identity, 'zeroCallRecovery': identity['zeroCallRecovery'],
                      'inventoryCorrection': {}, 'evidence': []}
            class Lock:
                def __enter__(self): return self
                def __exit__(self, *args): pass
            fake = types.SimpleNamespace(ExperimentLock=Lock, require=r.resources.require, ResourceGuardError=r.resources.ResourceGuardError)
            with patch.object(r, 'load_export', return_value=export), patch.object(r, 'resources', fake), patch.object(r, 'observe_preflight', return_value=observations(20)), patch.object(r, 'write_json', self.put), patch.object(r, 'PowerRequest') as power:
                with self.assertRaisesRegex(r.resources.ResourceGuardError, 'battery_reserve_reached'):
                    r.run('export', r.DESTINATION, self.root)
            power.assert_not_called()
            for path in (r.DESTINATION, r.RECOVERY_CLAIM, r.SUPERSESSION_PATH):
                self.assertFalse((self.root / path).exists())
            audit = list((self.root / r.AUDIT_BASE).glob('question-recovery-admission-*.json'))
            self.assertEqual(len(audit), 1)
            self.assertEqual(r.read(audit[0])[0]['failure']['code'], 'battery_reserve_reached')

    def test_completed_admission_replay_binds_battery_freeze_and_supersession(self):
        with battery_fixture(self.root) as identity:
            folder = self.root / r.DESTINATION
            initial, final = observations(21), observations(21)
            for index, item in enumerate(final):
                item['at'] = '2026-09-14T00:00:0' + str(6 + 3 * index) + '+00:00'
                item['monotonicSeconds'] += 6
            admission = {'version': r.VERSION + '-admission', 'initialPreflight': initial, 'finalPreflight': final,
                         'powerEnteredAt': '2026-09-14T00:00:05+00:00', 'consumedAt': '2026-09-14T00:00:10+00:00',
                         'powerBeforeConsumption': {'requested': True, 'released': False}}
            export = {'batteryExecution': identity, 'zeroCallRecovery': identity['zeroCallRecovery'],
                      'evidence': [identity['zeroCallRecovery']['priorRun']['exportManifest']]}
            plan = {'zeroCallRecovery': identity['zeroCallRecovery'], 'batteryExecution': identity,
                    'recoveryClaimPath': r.RECOVERY_CLAIM, 'startedAt': admission['consumedAt'],
                    'admission': self.put(folder / 'admission.json', admission)}
            plan_ref = self.put(folder / 'plan.json', plan)
            with patch.object(r, 'write_json', self.put):
                marker_ref = r.write_supersession(self.root, export)
            claim = {'version': r.VERSION, 'runPath': r.DESTINATION, 'priorClaim': identity['zeroCallRecovery']['priorClaim'],
                     'recoveryFreeze': identity['zeroCallRecovery']['recoveryFreeze'], 'exportManifest': export['evidence'][0],
                     'plan': plan_ref, 'admission': plan['admission'], 'at': r.utc(),
                     'batteryFreeze': identity['batteryFreeze'], 'supersessionMarker': marker_ref}
            summary = {'zeroCallRecovery': identity['zeroCallRecovery'], 'batteryExecution': identity,
                       'supersessionMarker': marker_ref, 'recoveryClaim': self.put(r.RECOVERY_CLAIM, claim)}
            self.assertEqual(r.validate_admission(folder, plan, summary, export, self.root), claim)
            claim['batteryFreeze'] = {**identity['batteryFreeze'], 'sha256': '0' * 64}
            summary['recoveryClaim'] = self.put(r.RECOVERY_CLAIM, claim)
            with self.assertRaisesRegex(ValueError, 'recovery_claim_binding'):
                r.validate_admission(folder, plan, summary, export, self.root)

    def context_fixture(self, percent=85, late=False, cleanup_error=False):
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
                  'observe_preflight': lambda: observations(percent), 'spawn': spawn,
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
        saved = r.read(folder / 'preflight.json')[0]
        self.assertEqual([x['system']['BatteryLifePercent'] for x in saved], [20, 20])
        summary = r.read(folder / 'summary.json')[0]
        self.assertEqual(summary['failure'], {'type': 'ResourceGuardError', 'code': 'battery_reserve_reached'})
        self.assertEqual(summary['completionRequestsSent'], 0)

    def test_late_battery_latch_fails_after_join_and_owned_stop(self):
        folder, packet, export, events = self.context_fixture(late=True)
        with self.assertRaisesRegex(ValueError, 'question_context_failed'):
            r.execute_context(packet, folder, {}, 'template', {}, export, self.root, time.monotonic())
        summary = r.read(folder / 'summary.json')[0]
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
        summary = r.read(folder / 'summary.json')[0]
        self.assertFalse(summary['nativeStopped'])
        self.assertTrue(summary['shutdown']['processCreated'])
        self.assertTrue(summary['shutdown']['cleanupUnconfirmed'])
        self.assertEqual(summary['failure']['type'], 'OSError')

    def test_context_and_monitor_ast_preserve_every_nonpower_statement(self):
        def nodes(module):
            return {n.name: n for n in ast.parse(Path(module.__file__).read_text(encoding='utf-8')).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        actual, prior = nodes(r), nodes(old)
        # Only v4's two diagnostic changes: persist failed battery preflight and
        # keep a trusted failure code. Every native/HTTP/cleanup statement stays.
        class ContextNormalize(ast.NodeTransformer):
            def visit_Assign(self, node):
                if isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'observations':
                    node.value.func.id = 'preflight_pair'
                if isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'failure' and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == 'failure_record':
                    old_failure = next(n for n in ast.walk(prior['execute_context']) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'failure' and isinstance(n.value, ast.Dict) and any(isinstance(x, ast.Name) and x.id == 'error' for x in ast.walk(n.value)))
                    node.value = deepcopy(old_failure.value)
                return self.generic_visit(node)
            def visit_Expr(self, node):
                if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == 'validate_preflight':
                    return None
                return self.generic_visit(node)
        normalized_context = ContextNormalize().visit(deepcopy(actual['execute_context']))
        self.assertEqual(ast.dump(normalized_context, include_attributes=False), ast.dump(prior['execute_context'], include_attributes=False))
        class Normalize(ast.NodeTransformer):
            def visit_Call(self, node):
                if isinstance(node.func, ast.Name) and node.func.id == 'system_state':
                    node.func = ast.Attribute(value=ast.Name(id='resources', ctx=ast.Load()), attr='system_state', ctx=ast.Load())
                return self.generic_visit(node)
        monitor = Normalize().visit(deepcopy(actual['Monitor']))
        self.assertEqual(ast.dump(monitor, include_attributes=False), ast.dump(prior['Monitor'], include_attributes=False))
        self.assertIs(r.execute_context.__globals__['Monitor'], r.Monitor)
        self.assertIs(r.execute_context.__globals__['observe_preflight'], r.observe_preflight)
        self.assertIs(r.execute_context.__globals__['validate_preflight'], r.validate_preflight)
        self.assertIs(r.execute_context.__globals__['PROFILE'], r.PROFILE)
        tree = ast.parse(Path(r.__file__).read_text(encoding='utf-8'))
        self.assertFalse(any(isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
                             and isinstance(n.value, ast.Name) and n.value.id in ('v1', 'v3', 'resources') for n in ast.walk(tree)))

    def test_replay_ast_preserves_v3_validation_except_v4_identity_power_and_marker(self):
        def function(module):
            return next(n for n in ast.parse(Path(module.__file__).read_text(encoding='utf-8')).body if isinstance(n, ast.FunctionDef) and n.name == 'validate_run')
        class Normalize(ast.NodeTransformer):
            def visit_Name(self, node):
                if node.id == 'load_battery': node.id = 'load_recovery'
                return node
            def visit_Constant(self, node):
                if node.value == 'batteryExecution': node.value = 'zeroCallRecovery'
                return node
            def visit_Dict(self, node):
                pairs = [(k, v) for k, v in zip(node.keys, node.values) if not isinstance(k, ast.Constant) or k.value != 'contextProducerVersion']
                node.keys, node.values = [p[0] for p in pairs], [p[1] for p in pairs]
                return self.generic_visit(node)
            def visit_List(self, node):
                if len(node.elts) == 2 and isinstance(node.elts[1], ast.Subscript) and isinstance(node.elts[1].slice, ast.Constant) and node.elts[1].slice.value == 'supersessionMarker':
                    node.elts.pop()
                return self.generic_visit(node)
        left, right = Normalize().visit(function(r)), function(v3)
        # v4 verifies VERSION for actual new contexts where v3 required v1.VERSION.
        class V1Version(ast.NodeTransformer):
            def visit_Attribute(self, node):
                if isinstance(node.value, ast.Name) and node.value.id == 'v1' and node.attr == 'VERSION':
                    return ast.Name(id='VERSION', ctx=ast.Load())
                return self.generic_visit(node)
            def visit_Expr(self, node):
                if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'append' and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == 'evidence':
                    node.value.func.attr = 'extend'
                    node.value.args = [ast.List(elts=node.value.args, ctx=ast.Load())]
                return self.generic_visit(node)
        right = V1Version().visit(right)
        self.assertEqual(ast.dump(left, include_attributes=False), ast.dump(right, include_attributes=False))


if __name__ == '__main__':
    unittest.main()
