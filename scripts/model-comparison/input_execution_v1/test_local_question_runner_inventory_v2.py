"""Small inventory/lineage fixtures; no native process, HTTP or real packets."""
import ast
from copy import deepcopy
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_runner_inventory_v2 as r
from input_execution_v1 import local_question_runner_v1 as original


class InventoryAdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='question-inventory-v2-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else r.packed(value))
        return r.reference(self.root, target)

    def installation(self):
        files = [self.write(r.RUNTIME + '/' + f'file-{i:02}.dll', f'fixture-{i}'.encode()) for i in range(51)]
        self.write(r.MODEL, b'opaque-model-fixture')
        manifest = {'model': {'sha256': r.contract.MODEL_SHA, 'file': r.contract.MODEL_NAME,
                              'revision': '3885219b6810b007914f3a7950a8d1b469d598a5', 'sizeBytes': 5680522464},
                    'runtime': {'commit': '72797e89198ab564fd0e6baa54ab196e8dd1d884',
                                'archiveSha256': '68d0ea47c71a55f6a19219727d39075f08f7da2c9d9073c7b47b3d64a7027284',
                                'files': files}}
        ref = self.write(r.INSTALL, manifest)
        self.stack.enter_context(patch.object(r, 'INSTALL_SHA', ref['sha256']))
        verify = r.verify_refs
        # A model-shaped fixture avoids creating or hashing a synthetic 5.68GB file.
        # Every one of the 51 runtime hashes still uses the production verifier.
        def fixture_verify(root, records):
            model = [item for item in records if item['path'] == r.MODEL]
            self.assertIn(len(model), (0, 1))
            if model:
                self.assertEqual(model[0]['sha256'], r.contract.MODEL_SHA)
            verify(root, [item for item in records if item['path'] != r.MODEL])
        self.stack.enter_context(patch.object(r, 'verify_refs', side_effect=fixture_verify))
        return manifest

    def freeze(self):
        core = [self.write(path, b'core-code-fixture') for path in sorted(r.contract.REQUIRED_FREEZE_FILES)]
        base = self.write(r.FREEZE, {'version': 'input-execution-v1-local-question-execution-freeze-v1',
                                     'actualQuestionCallsAtFreeze': 0, 'files': core})
        self.stack.enter_context(patch.object(r, 'BASE_FREEZE_SHA', base['sha256']))
        files = [self.write(path, b'correction-code-fixture') for path in sorted(r.REQUIRED_CORRECTION_FILES)]
        value = {'version': r.CORRECTION_VERSION, 'baseExecutionFreeze': base,
                 'actualQuestionCallsAtFreeze': 0, 'files': files}
        self.write(r.CORRECTION_FREEZE, value)
        return value

    def test_actual_count_fixture_51_passes_and_retains_model_binding(self):
        self.installation()
        result = r.installation(self.root)
        self.assertEqual(len(result['files']), 52)  # 51 runtime + one model, distinct counts.
        self.assertEqual(result['modelSha256'], r.contract.MODEL_SHA)
        self.assertEqual(len(result['statIdentities']), 52)

    def test_extra_or_missing_runtime_file_rejected(self):
        self.installation()
        self.write(r.RUNTIME + '/extra.dll', b'extra')
        with self.assertRaisesRegex(ValueError, 'runtime_51_file_inventory'):
            r.installation(self.root)
        (self.root / r.RUNTIME / 'extra.dll').unlink()
        (self.root / r.RUNTIME / 'file-00.dll').unlink()
        with self.assertRaisesRegex(ValueError, 'runtime_51_file_inventory'):
            r.installation(self.root)

    def test_runtime_hash_change_rejected(self):
        self.installation()
        self.write(r.RUNTIME + '/file-00.dll', b'changed')
        with self.assertRaisesRegex(ValueError, 'evidence_hash_changed'):
            r.installation(self.root)

    def test_duplicate_manifest_record_rejected_even_with_51_unique_paths(self):
        manifest = self.installation()
        manifest['runtime']['files'].append(manifest['runtime']['files'][0])
        digest = self.write(r.INSTALL, manifest)['sha256']
        with patch.object(r, 'INSTALL_SHA', digest):
            with self.assertRaisesRegex(ValueError, 'runtime_51_file_inventory'):
                r.installation(self.root)

    def test_manifest_edit_without_new_pin_rejected(self):
        manifest = self.installation()
        manifest['model']['revision'] = 'changed'
        self.write(r.INSTALL, manifest)
        with self.assertRaisesRegex(ValueError, 'installed_manifest_changed'):
            r.installation(self.root)

    def test_correct_freeze16_and_correction9_binding(self):
        self.freeze()
        result = r.load_correction(self.root)
        self.assertEqual(result['version'], r.CORRECTION_VERSION)
        self.assertEqual(len(result['files']), 9)
        self.assertEqual(result['baseExecutionFreeze']['sha256'], r.BASE_FREEZE_SHA)
        self.assertEqual(result['correctionFreeze'], r.reference(self.root, r.CORRECTION_FREEZE))

    def test_core_and_correction_hash_changes_rejected(self):
        value = self.freeze()
        self.write(value['files'][0]['path'], b'changed')
        with self.assertRaisesRegex(ValueError, 'evidence_hash_changed'):
            r.load_correction(self.root)
        self.write(value['files'][0]['path'], b'correction-code-fixture')
        self.write(next(iter(r.contract.REQUIRED_FREEZE_FILES)), b'changed')
        with self.assertRaises(ValueError):
            r.load_correction(self.root)

    def test_extra_source_or_omitted_adapter_freeze_record_rejected(self):
        value = self.freeze()
        value['files'].append(self.write('source.json', b'forbidden-source-fixture'))
        self.write(r.CORRECTION_FREEZE, value)
        with self.assertRaisesRegex(ValueError, 'inventory_correction_exact_code_inventory'):
            r.load_correction(self.root)
        value['files'] = value['files'][:-2]
        self.write(r.CORRECTION_FREEZE, value)
        with self.assertRaisesRegex(ValueError, 'inventory_correction_exact_code_inventory'):
            r.load_correction(self.root)

    def test_wrong_base_freeze_or_nonzero_calls_rejected(self):
        value = self.freeze()
        value['baseExecutionFreeze']['sha256'] = '0' * 64
        self.write(r.CORRECTION_FREEZE, value)
        with self.assertRaisesRegex(ValueError, 'inventory_correction_freeze_contract'):
            r.load_correction(self.root)
        value['baseExecutionFreeze'] = r.reference(self.root, r.FREEZE)
        for calls in (1, False):
            value['actualQuestionCallsAtFreeze'] = calls
            self.write(r.CORRECTION_FREEZE, value)
            with self.assertRaisesRegex(ValueError, 'inventory_correction_freeze_contract'):
                r.load_correction(self.root)

    def test_source_free_export_gets_correction_refs_without_changing_packets(self):
        self.freeze()
        packets = [{'reviewId': 'synthetic-only'}]
        initial = {'packets': packets, 'evidence': [{'path': 'export/manifest.json', 'sha256': 'a' * 64}]}
        fake = types.SimpleNamespace(load_export=lambda path, root: deepcopy(initial))
        with patch.object(r, 'v1', fake), patch.object(r, 'verify_refs'):
            export = r.load_export('unused', self.root)
        self.assertEqual(export['packets'], packets)
        self.assertEqual(export['evidence'][0], initial['evidence'][0])
        self.assertEqual(len(export['evidence']), 11)
        self.assertEqual(export['inventoryCorrection']['files'], export['evidence'][2:])

    def test_coordinator_v2_uses_frozen_v1_context_identity_and_same_claim(self):
        destination = self.root / r.BASE / 'run'
        correction = {'version': r.CORRECTION_VERSION, 'fixture': True}
        packets = [{'reviewId': rid} for rid in r.contract.IDS]
        export = {'packets': packets, 'evidence': [], 'inventoryCorrection': correction}
        export['evidence'].append({'path': 'export-manifest-fixture', 'sha256': 'a' * 64})
        calls = []
        def context(packet, folder, *args):
            calls.append(packet['reviewId'])
            state = {'version': original.VERSION, 'completionRequestsSent': 1, 'nativeStopped': True}
            self.write(folder / 'summary.json', state)
            self.write(folder / 'plan.json', state)
            self.write(folder / 'process.json', {'fixture': True})
            return state
        class Scope:
            def __enter__(self):
                self.receipt = {'released': False}
                return self
            def __exit__(self, *args):
                self.receipt['released'] = True
        patches = {'v1': types.SimpleNamespace(VERSION=original.VERSION, execute_context=context),
                   'load_export': lambda *args: deepcopy(export), 'python_identity': lambda: {},
                   'installation': lambda root: {}, 'gguf_template': lambda path: ('template', {}),
                   'render_prompt': lambda *args: '', 'verify_refs': lambda *args: None,
                   'resources': types.SimpleNamespace(ExperimentLock=Scope, PowerRequest=Scope),
                   'write_json': self.write, 'write_new': self.write, 'snapshot': lambda *args: [],
                   'validate_run': lambda folder, export, root: {'summary': r.read(folder / 'summary.json')[0]}}
        with ExitStack() as stack, redirect_stdout(io.StringIO()):
            for name, value in patches.items():
                stack.enter_context(patch.object(r, name, value))
            result = r.run('export', destination, self.root)
        plan, _ = r.read(destination / 'plan.json')
        state = result['summary']
        self.assertEqual(calls, list(r.contract.IDS))
        self.assertEqual(plan['version'], r.VERSION)
        self.assertEqual(state['version'], r.VERSION)
        self.assertEqual(plan['contextProducerVersion'], original.VERSION)
        self.assertEqual(state['contextProducerVersion'], original.VERSION)
        self.assertEqual(plan['inventoryCorrection'], state['inventoryCorrection'])
        self.assertEqual(state['completedContexts'], state['freshNativeProcesses'])
        self.assertEqual(state['completionRequestsSent'], 64)
        self.assertTrue((self.root / r.BASE / '.local-question-native-v1-generation.claim.json').is_file())

    def test_run_and_replay_ast_have_only_explicit_inventory_lineage_differences(self):
        old = ast.parse(Path(original.__file__).read_text(encoding='utf-8'))
        new = ast.parse(Path(r.__file__).read_text(encoding='utf-8'))
        old_functions = {n.name: n for n in old.body if isinstance(n, ast.FunctionDef)}
        new_functions = {n.name: n for n in new.body if isinstance(n, ast.FunctionDef)}
        class Normalize(ast.NodeTransformer):
            def visit_Dict(self, node):
                pairs = [(key, val) for key, val in zip(node.keys, node.values)
                         if not isinstance(key, ast.Constant) or key.value not in ('inventoryCorrection', 'contextProducerVersion')]
                node.keys, node.values = [x[0] for x in pairs], [x[1] for x in pairs]
                return self.generic_visit(node)
            def visit_Attribute(self, node):
                if isinstance(node.value, ast.Name) and node.value.id == 'v1' and node.attr == 'execute_context':
                    return ast.Name(id='execute_context', ctx=ast.Load())
                return self.generic_visit(node)
            def visit_Expr(self, node):
                if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == 'require':
                    args = node.value.args
                    if args and isinstance(args[-1], ast.Constant) and args[-1].value in (
                            'inventory_correction_run_binding', 'frozen_context_producer_identity'):
                        return None
                return self.generic_visit(node)
        for name in ('run', 'validate_run'):
            normalized = Normalize().visit(deepcopy(new_functions[name]))
            self.assertEqual(ast.dump(normalized, include_attributes=False), ast.dump(old_functions[name], include_attributes=False), name)
        self.assertNotIn('execute_context', new_functions)
        self.assertNotIn('parity', new_functions)
        self.assertNotIn('Monitor', {n.name for n in new.body if isinstance(n, ast.ClassDef)})
        # Assigning a frozen v1 module attribute would change its execution globals.
        self.assertFalse(any(isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
                             and isinstance(n.value, ast.Name) and n.value.id == 'v1' for n in ast.walk(new)))

    def test_replay_rejects_wrong_correction_and_relabelled_context(self):
        folder = self.root / r.BASE / 'run'
        for rid in r.contract.IDS:
            (folder / rid).mkdir(parents=True)
        correction = {'fixture': 'bound correction'}
        export = {'evidence': [{'path': 'export-fixture', 'sha256': 'a' * 64}],
                  'packets': [{'reviewId': 'R001'}], 'inventoryCorrection': correction}
        summary = {'version': r.VERSION, 'status': 'completed', 'failure': None,
                   'expectedContexts': 64, 'completedContexts': 64, 'freshNativeProcesses': 64,
                   'completionRequestsSent': 64, 'nativeStopped': True, 'sourceExposure': False,
                   'actualCollaborationSpawns': 0, 'modelSha256': r.contract.MODEL_SHA,
                   'powerRequest': {'released': True}, 'inventoryCorrection': correction,
                   'contextProducerVersion': original.VERSION}
        plan = {'version': r.VERSION, 'pythonIdentity': {}, 'reviewIds': list(r.contract.IDS), 'profile': r.PROFILE,
                'codeAndExportEvidence': export['evidence'], 'exportManifest': export['evidence'][0],
                'modelSha256': r.contract.MODEL_SHA, 'sourceExposure': False,
                'previousModelRequestsPerContext': 0, 'actualCollaborationSpawns': 0,
                'installation': {}, 'gguf': {}, 'inventoryCorrection': correction,
                'contextProducerVersion': original.VERSION}
        self.write(folder / 'model-chat-template.jinja', b'template')
        context_plan = {'version': r.VERSION}  # Forbidden relabelling of frozen v1 execution.
        patches = {'load_export': lambda *args: deepcopy(export), 'python_identity': lambda: {},
                   'installation': lambda root: {}, 'gguf_template': lambda path: ('template', {}),
                   'validated_snapshot': lambda path, root: (summary if path == folder else context_plan, []),
                   'read': lambda path: (plan if path == folder / 'plan.json' else context_plan, b'')}
        with ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(patch.object(r, name, value))
            with self.assertRaisesRegex(ValueError, 'frozen_context_producer_identity'):
                r.validate_run(folder, 'export', self.root)
            plan['inventoryCorrection'] = {'fixture': 'wrong correction'}
            with self.assertRaisesRegex(ValueError, 'inventory_correction_run_binding'):
                r.validate_run(folder, 'export', self.root)


if __name__ == '__main__':
    unittest.main()
