"""Pinned-prior and admission negatives; synthetic objects only, no native/HTTP."""
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
from input_execution_v1 import local_question_runner_recovery_v3 as r
from input_execution_v1 import local_question_runner_inventory_v2 as old


def write(root, path, value):
    path = Path(root) / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else r.packed(value))
    return r.reference(root, path)


@contextmanager
def recovery_fixture(root):
    """Real v3 prior/freeze validation; frozen v2 correction helper is stubbed.

    All pins and files are synthetic and live only in the caller's temp root.
    No frozen module global is changed. Suitable for transport boundary tests.
    """
    root = Path(root).resolve()
    base_ref = write(root, r.FREEZE, b'synthetic core freeze')
    correction_ref = write(root, old.CORRECTION_FREEZE, b'synthetic inventory freeze')
    correction = {'baseExecutionFreeze': base_ref, 'correctionFreeze': correction_ref, 'files': [], 'version': old.CORRECTION_VERSION}
    export_ref = {'path': r.BASE + '/synthetic-export/manifest.json', 'sha256': 'a' * 64}
    template = write(root, r.PRIOR_RUN + '/model-chat-template.jinja', b'synthetic template')
    plan = write(root, r.PRIOR_RUN + '/plan.json', {'version': old.VERSION, 'contextProducerVersion': r.v1.VERSION,
                 'inventoryCorrection': correction, 'exportManifest': export_ref})
    summary = write(root, r.PRIOR_RUN + '/summary.json', {'version': old.VERSION, 'contextProducerVersion': r.v1.VERSION,
                    'inventoryCorrection': correction, 'status': 'failed',
                    'failure': {'type': 'ResourceGuardError', 'code': 'ResourceGuardError'},
                    'powerRequest': None, 'sourceExposure': False, 'expectedContexts': 64,
                    'completedContexts': 0, 'freshNativeProcesses': 0, 'completionRequestsSent': 0,
                    'artifactFiles': [template, plan]})
    claim = write(root, r.PRIOR_CLAIM, {'version': old.VERSION, 'runPath': r.PRIOR_RUN, 'exportManifest': export_ref})
    review = write(root, r.PRIOR_REVIEW, b'synthetic zero-call review')
    pins = {item['path']: item['sha256'] for item in (template, plan, summary, claim, review)}
    fake_old = types.SimpleNamespace(VERSION=old.VERSION, load_correction=lambda root: deepcopy(correction))
    with patch.object(r, 'v2', fake_old), patch.object(r, 'PRIOR_HASHES', pins), patch.object(r, 'INVENTORY_FREEZE_SHA', correction_ref['sha256']):
        prior = r.validate_prior(root)
        files = [write(root, path, b'synthetic recovery code') for path in sorted(r.REQUIRED_RECOVERY_FILES)]
        write(root, r.RECOVERY_FREEZE, {'version': r.RECOVERY_VERSION, **prior, 'actualQuestionCallsAtFreeze': 0, 'files': files})
        yield r.load_recovery(root)


def observations():
    return [{'at': '2026-09-14T00:00:0' + str(i * 3) + '+00:00', 'monotonicSeconds': i * 3,
             'system': {'ACLineStatus': 1, 'availablePhysicalBytes': 10 * 1024 ** 3, 'availableCommitBytes': 10 * 1024 ** 3},
             'processes': {'nativeConflicts': [], 'classifiedConflicts': []}} for i in range(2)]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='question-zero-call-v3-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.identity = self.stack.enter_context(recovery_fixture(self.root))
        self.original_prior = {p: (self.root / p).read_bytes() for p in r.PRIOR_HASHES}

    def put(self, path, value):
        return write(self.root, path, value)

    def test_prior_three_files_and_recovery_nine_bind_without_source_reads(self):
        actual = r.load_recovery(self.root)
        self.assertEqual(actual, self.identity)
        self.assertEqual(len(actual['priorRun']['files']), 3)
        self.assertEqual(len(actual['files']), 9)
        self.assertEqual(len(r.recovery_refs(actual)), 17)
        self.assertFalse((self.root / actual['priorRun']['exportManifest']['path']).exists())
        r.verify_refs(self.root, r.recovery_refs(actual))

    def test_prior_claim_changed_or_missing_rejected(self):
        self.put(r.PRIOR_CLAIM, b'changed')
        with self.assertRaisesRegex(ValueError, 'evidence_hash_changed'):
            r.validate_prior(self.root)
        (self.root / r.PRIOR_CLAIM).unlink()
        with self.assertRaises(FileNotFoundError):
            r.validate_prior(self.root)

    def test_unknown_context_or_http_artifact_rejected(self):
        self.put(r.PRIOR_RUN + '/R001/completion-intent.json', {'sequence': 1})
        with self.assertRaisesRegex(ValueError, 'prior_zero_call_exact_inventory'):
            r.validate_prior(self.root)

    def test_nonzero_or_bool_zero_summary_rejected_after_fixture_repin(self):
        path = r.PRIOR_RUN + '/summary.json'
        value, _ = r.read(self.root / path)
        for count in (1, False):
            value['completionRequestsSent'] = count
            digest = self.put(path, value)['sha256']
            pins = {**r.PRIOR_HASHES, path: digest}
            with patch.object(r, 'PRIOR_HASHES', pins):
                with self.assertRaisesRegex(ValueError, 'prior_failed_zero_call_binding'):
                    r.validate_prior(self.root)

    def test_extra_missing_changed_recovery_files_and_nonzero_freeze_rejected(self):
        value, _ = r.read(self.root / r.RECOVERY_FREEZE)
        value['files'].append(self.put('source.json', b'forbidden source fixture'))
        self.put(r.RECOVERY_FREEZE, value)
        with self.assertRaisesRegex(ValueError, 'zero_call_recovery_exact_code_inventory'):
            r.load_recovery(self.root)
        value['files'].pop()
        value['actualQuestionCallsAtFreeze'] = 1
        self.put(r.RECOVERY_FREEZE, value)
        with self.assertRaisesRegex(ValueError, 'zero_call_recovery_freeze_binding'):
            r.load_recovery(self.root)
        value['actualQuestionCallsAtFreeze'] = 0
        self.put(r.RECOVERY_FREEZE, value)
        self.put(value['files'][0]['path'], b'changed')
        with self.assertRaisesRegex(ValueError, 'evidence_hash_changed'):
            r.load_recovery(self.root)

    def test_recovery_export_must_match_original_claim(self):
        export = {'evidence': [{'path': 'wrong-export', 'sha256': 'a' * 64}], 'packets': []}
        fake = types.SimpleNamespace(load_export=lambda *args: export)
        with patch.object(r, 'v2', fake), patch.object(r, 'load_recovery', return_value=self.identity):
            with self.assertRaisesRegex(ValueError, 'recovery_export_changed'):
                r.load_export('unused', self.root)

    def lifecycle(self, initial_failure=None, power_failure=None, final_failure=None, context_failure=False):
        events, calls = [], []
        identity = deepcopy(self.identity)
        export = {'evidence': [identity['priorRun']['exportManifest']], 'inventoryCorrection': {'fixture': True},
                  'zeroCallRecovery': identity, 'packets': [{'reviewId': rid} for rid in r.contract.IDS]}
        class Lock:
            def __enter__(self):
                events.append('lock')
                return self
            def __exit__(self, *args):
                events.append('unlock')
        class Power:
            def __enter__(self):
                events.append('power')
                if power_failure:
                    raise r.resources.ResourceGuardError(power_failure)
                self.receipt = {'requested': True, 'released': False}
                return self
            def __exit__(self, *args):
                self.receipt['released'] = True
                events.append('power-release')
        counter = [0]
        def observe():
            counter[0] += 1
            label = 'initial' if counter[0] == 1 else 'final'
            events.append(label)
            values = observations()
            code = initial_failure if counter[0] == 1 else final_failure
            if code == 'ac':
                values[0]['system']['ACLineStatus'] = 0
            elif code == 'memory':
                values[0]['system']['availablePhysicalBytes'] = 1
            elif code == 'conflict':
                values[0]['processes']['nativeConflicts'] = [123]
            return values
        def execute(packet, folder, *args):
            calls.append(packet['reviewId'])
            self.assertTrue((self.root / r.RECOVERY_CLAIM).is_file())
            self.assertTrue((folder.parent / 'admission.json').is_file())
            state = {'version': r.v1.VERSION, 'completionRequestsSent': 1, 'nativeStopped': True,
                     'status': 'failed' if context_failure else 'completed'}
            self.put(folder / 'plan.json', {'version': r.v1.VERSION})
            self.put(folder / 'process.json', {'fixture': True})
            self.put(folder / 'summary.json', state)
            if context_failure:
                raise ValueError('question_context_failed')
            return state
        frozen_resources = r.resources
        fake_resources = types.SimpleNamespace(ExperimentLock=Lock, PowerRequest=Power,
                     ResourceGuardError=frozen_resources.ResourceGuardError, require=frozen_resources.require)
        replacements = {'v1': types.SimpleNamespace(VERSION=r.v1.VERSION, execute_context=execute),
                        'load_export': lambda *args: deepcopy(export), 'load_recovery': lambda *args: deepcopy(identity),
                        'observe_preflight': observe, 'resources': fake_resources,
                        'python_identity': lambda: {}, 'installation': lambda root: {},
                        'gguf_template': lambda path: ('template', {}), 'render_prompt': lambda *args: '',
                        'verify_refs': lambda *args: None, 'check_installation_stat': lambda *args: None,
                        'write_json': self.put, 'write_new': self.put, 'snapshot': lambda *args: [],
                        'validate_run': lambda path, export, root: {'summary': r.read(path / 'summary.json')[0]}}
        for key, value in replacements.items():
            self.stack.enter_context(patch.object(r, key, value))
        return events, calls

    def check_unconsumed(self):
        self.assertFalse((self.root / r.DESTINATION).exists())
        self.assertFalse((self.root / r.RECOVERY_CLAIM).exists())
        for path, before in self.original_prior.items():
            self.assertEqual((self.root / path).read_bytes(), before)
        audits = list((self.root / r.AUDIT_BASE).glob('question-recovery-admission-*.json'))
        self.assertEqual(len(audits), 1)
        audit, _ = r.read(audits[0])
        self.assertEqual(audit['completionRequestsSent'], 0)
        self.assertFalse(audit['newClaimCreated'])
        return audit

    def test_ac0_admission_consumes_neither_claim_nor_run(self):
        events, calls = self.lifecycle(initial_failure='ac')
        with self.assertRaisesRegex(r.resources.ResourceGuardError, 'ac_power_required'):
            r.run('export', r.DESTINATION, self.root)
        self.assertEqual(events, ['lock', 'initial', 'unlock'])
        self.assertFalse(calls)
        self.assertEqual(self.check_unconsumed()['failure']['code'], 'ac_power_required')

    def test_power_entry_failure_preserves_rich_code_and_zero_consumption(self):
        events, calls = self.lifecycle(power_failure='temporary_power_request_failed')
        with self.assertRaisesRegex(r.resources.ResourceGuardError, 'temporary_power_request_failed'):
            r.run('export', r.DESTINATION, self.root)
        self.assertEqual(events, ['lock', 'initial', 'power', 'unlock'])
        self.assertFalse(calls)
        self.assertEqual(self.check_unconsumed()['failure']['code'], 'temporary_power_request_failed')

    def test_second_preflight_failure_releases_power_without_consumption(self):
        events, calls = self.lifecycle(final_failure='memory')
        with self.assertRaisesRegex(r.resources.ResourceGuardError, 'insufficient_start_availablephysicalbytes'):
            r.run('export', r.DESTINATION, self.root)
        self.assertEqual(events[-2:], ['power-release', 'unlock'])
        self.assertFalse(calls)
        self.check_unconsumed()

    def test_conflict_preflight_rejects_before_power(self):
        events, calls = self.lifecycle(initial_failure='conflict')
        with self.assertRaisesRegex(r.resources.ResourceGuardError, 'question_preflight_model_or_worker'):
            r.run('export', r.DESTINATION, self.root)
        self.assertNotIn('power', events)
        self.assertFalse(calls)
        self.check_unconsumed()

    def test_successful_synthetic_coordinator_64_claim_and_old_context_identity(self):
        events, calls = self.lifecycle()
        with redirect_stdout(io.StringIO()):
            result = r.run('export', r.DESTINATION, self.root)
        state = result['summary']
        self.assertEqual(calls, list(r.contract.IDS))
        self.assertEqual(state['version'], r.VERSION)
        self.assertEqual(state['contextProducerVersion'], r.v1.VERSION)
        self.assertEqual(state['completionRequestsSent'], 64)
        self.assertEqual(state['zeroCallRecovery'], self.identity)
        self.assertEqual(events, ['lock', 'initial', 'power', 'final', 'power-release', 'unlock'])
        claim, _ = r.read(self.root / r.RECOVERY_CLAIM)
        self.assertEqual(claim['priorClaim'], self.identity['priorClaim'])
        for path, before in self.original_prior.items():
            self.assertEqual((self.root / path).read_bytes(), before)

    def test_failure_after_first_context_keeps_new_claim_and_prevents_next(self):
        events, calls = self.lifecycle(context_failure=True)
        with self.assertRaisesRegex(ValueError, 'question_run_failed'):
            r.run('export', r.DESTINATION, self.root)
        self.assertEqual(calls, ['R001'])
        self.assertTrue((self.root / r.RECOVERY_CLAIM).is_file())
        state, _ = r.read(self.root / r.DESTINATION / 'summary.json')
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['completionRequestsSent'], 1)
        self.assertEqual(events[-2:], ['power-release', 'unlock'])
        with self.assertRaisesRegex(ValueError, 'question_run_must_be_new'):
            r.run('export', r.DESTINATION, self.root)

    def test_existing_new_claim_and_other_destination_rejected_without_context(self):
        events, calls = self.lifecycle()
        self.put(r.RECOVERY_CLAIM, {'existing': True})
        with self.assertRaisesRegex(ValueError, 'recovery_already_consumed'):
            r.run('export', r.DESTINATION, self.root)
        self.assertFalse(calls)
        self.assertNotIn('power', events)
        with self.assertRaisesRegex(ValueError, 'single_recovery_destination_required'):
            r.run('export', r.BASE + '/other-attempt', self.root)

    def test_preflight_audit_has_exact_ac_code_and_never_calls_power(self):
        events, calls = self.lifecycle(initial_failure='ac')
        destination = r.AUDIT_BASE + '/preflight.json'
        result = r.preflight(destination, self.root)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['failure']['code'], 'ac_power_required')
        self.assertFalse(result['powerRequestCalled'])
        self.assertFalse(calls)
        self.assertNotIn('power', events)
        self.assertFalse((self.root / r.DESTINATION).exists())
        self.assertFalse((self.root / r.RECOVERY_CLAIM).exists())

    def test_error_record_avoids_untrusted_detail(self):
        self.assertEqual(r.failure_record(RuntimeError('SECRET prompt!'))['code'], 'RuntimeError')
        self.assertEqual(r.failure_record(ValueError('SECRET prompt!'))['code'], 'ValueError')
        self.assertEqual(r.failure_record(ValueError('private_prompt_content'))['code'], 'ValueError')
        self.assertEqual(r.failure_record(r.resources.ResourceGuardError('private_prompt_content'))['code'], 'ResourceGuardError')
        self.assertEqual(r.failure_record(r.resources.ResourceGuardError('ac_power_required'))['code'], 'ac_power_required')

    def admission_fixture(self):
        folder = self.root / r.DESTINATION
        initial, final = observations(), observations()
        for index, value in enumerate(final):
            value['at'] = '2026-09-14T00:00:0' + str(6 + 3 * index) + '+00:00'
            value['monotonicSeconds'] += 6
        admission = {'version': r.VERSION + '-admission', 'initialPreflight': initial, 'finalPreflight': final,
                     'powerEnteredAt': '2026-09-14T00:00:05+00:00', 'consumedAt': '2026-09-14T00:00:10+00:00',
                     'powerBeforeConsumption': {'requested': True, 'released': False}}
        export = {'zeroCallRecovery': self.identity, 'evidence': [self.identity['priorRun']['exportManifest']]}
        plan = {'zeroCallRecovery': self.identity, 'recoveryClaimPath': r.RECOVERY_CLAIM,
                'startedAt': admission['consumedAt'], 'admission': self.put(folder / 'admission.json', admission)}
        plan_ref = self.put(folder / 'plan.json', plan)
        claim = {'version': r.VERSION, 'runPath': r.DESTINATION, 'priorClaim': self.identity['priorClaim'],
                 'recoveryFreeze': self.identity['recoveryFreeze'], 'exportManifest': export['evidence'][0],
                 'plan': plan_ref, 'admission': plan['admission'], 'at': '2026-09-14T00:00:11+00:00'}
        state = {'zeroCallRecovery': self.identity, 'recoveryClaim': self.put(r.RECOVERY_CLAIM, claim)}
        return folder, plan, state, export, admission, claim

    def test_admission_replay_validates_power_pair_order_and_saved_claim_hash(self):
        folder, plan, state, export, admission, claim = self.admission_fixture()
        self.assertEqual(r.validate_admission(folder, plan, state, export, self.root), claim)
        claim['at'] = '2026-09-14T00:00:12+00:00'
        self.put(r.RECOVERY_CLAIM, claim)
        with self.assertRaisesRegex(ValueError, 'recovery_claim_binding'):
            r.validate_admission(folder, plan, state, export, self.root)

    def test_admission_rejects_early_consumption_even_after_all_hashes_resealed(self):
        folder, plan, state, export, admission, claim = self.admission_fixture()
        admission['powerEnteredAt'] = '2026-09-14T00:00:10+00:00'
        plan['admission'] = self.put(folder / 'admission.json', admission)
        claim['admission'] = plan['admission']
        claim['plan'] = self.put(folder / 'plan.json', plan)
        state['recoveryClaim'] = self.put(r.RECOVERY_CLAIM, claim)
        with self.assertRaisesRegex(ValueError, 'recovery_consumed_before_power_and_preflight'):
            r.validate_admission(folder, plan, state, export, self.root)

    def test_admission_rejects_prior_claim_rebinding_even_after_resealing(self):
        folder, plan, state, export, admission, claim = self.admission_fixture()
        claim['priorClaim'] = {'path': r.PRIOR_CLAIM, 'sha256': '0' * 64}
        state['recoveryClaim'] = self.put(r.RECOVERY_CLAIM, claim)
        with self.assertRaisesRegex(ValueError, 'recovery_claim_binding'):
            r.validate_admission(folder, plan, state, export, self.root)

    def test_replay_ast_preserves_all_frozen_v2_checks(self):
        source = Path(r.__file__).read_text(encoding='utf-8')
        old_tree = ast.parse(Path(old.__file__).read_text(encoding='utf-8'))
        tree = ast.parse(source)
        new_functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        old_validate = next(n for n in old_tree.body if isinstance(n, ast.FunctionDef) and n.name == 'validate_run')
        class Normalize(ast.NodeTransformer):
            def visit_Assign(self, node):
                if isinstance(node.targets[0], ast.Name):
                    if node.targets[0].id == 'recovery_claim':
                        return None
                    if node.targets[0].id == 'previous_finished' and any(isinstance(n, ast.Name) and n.id == 'recovery_claim' for n in ast.walk(node.value)):
                        node.value = ast.Constant(value=None)
                return self.generic_visit(node)
            def visit_Expr(self, node):
                if isinstance(node.value, ast.Call):
                    call = node.value
                    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == 'evidence' and call.func.attr == 'append':
                        return None
                    if isinstance(call.func, ast.Name) and call.func.id == 'require' and isinstance(call.args[-1], ast.Constant) and call.args[-1].value == 'recovery_changed_during_validation':
                        return None
                return self.generic_visit(node)
        actual = Normalize().visit(deepcopy(new_functions['validate_run']))
        self.assertEqual(ast.dump(actual, include_attributes=False), ast.dump(old_validate, include_attributes=False))
        self.assertNotIn('execute_context', new_functions)
        self.assertNotIn('parity', new_functions)
        self.assertIn('v1.execute_context(', source)
        self.assertFalse(any(isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
                             and isinstance(n.value, ast.Name) and n.value.id in ('v1', 'v2') for n in ast.walk(tree)))


if __name__ == '__main__':
    unittest.main()
