"""Synthetic native/HTTP fixtures only; no model, source packet or external call."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import shutil
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_runner_v1 as r

TEMPLATE = '{% for m in messages %}<|im_start|>{{ m.role }}\n{{ m.content }}<|im_end|>\n{% endfor %}<|im_start|>assistant\n<think>\n\n</think>\n\n'
META = {'templateSha256': r.sha(TEMPLATE), 'metadataSha256': 'a' * 64, 'tensorCount': 1,
        'weightTensorsLoaded': False, 'boundaryTokenIds': {'<|im_start|>': 10, '<|im_end|>': 11, '<|endoftext|>': 12, '<think>': 13, '</think>': 14}, 'eosTokenId': 11}


def packet(rid):
    return {'reviewId': rid, 'translation': '이것은 ' + rid + '의 독립적인 합성 본문입니다.',
            'questions': [{'questionId': 'q1', 'questionKo': '무슨 본문인가요?'}, {'questionId': 'q2', 'questionKo': '확인 가능한가요?'}]}


def memory():
    return {'ACLineStatus': 1, 'availablePhysicalBytes': 10 * r.GIB, 'availableCommitBytes': 10 * r.GIB}


def preflights():
    return [{'at': '2026-09-14T00:00:00+00:00', 'monotonicSeconds': i * 3,
             'system': memory(), 'processes': {'nativeConflicts': [], 'classifiedConflicts': []}} for i in range(2)]


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value, ensure_ascii=False).encode('utf-8'))
        self.headers = {'Content-Length': str(len(self.getvalue()))}
    def getcode(self):
        return 200


class QuestionRunnerTests(unittest.TestCase):
    cached_files = cached_result = None

    @classmethod
    def setUpClass(cls):
        cls.suite_temp = tempfile.TemporaryDirectory(prefix='source-free-question-test-')
        cls.suite_root = Path(cls.suite_temp.name).resolve()

    @classmethod
    def tearDownClass(cls):
        cls.suite_temp.cleanup()

    def setUp(self):
        self.root = type(self).suite_root
        # Delete only this suite's exact TemporaryDirectory, verified above.
        self.assertEqual(self.root, Path(type(self).suite_temp.name).resolve())
        self.assertTrue(self.root.name.startswith('source-free-question-test-'))
        shutil.rmtree(self.root)
        self.root.mkdir()
        self.export = self.root / r.BASE / 'export'
        self.output = self.root / r.BASE / 'run'
        self.events, self.processes, self.delivered = [], [], []
        self.fail_rid = None
        self.late_guard_error = False
        self.cleanup_failure = False
        # Full durability was exercised in the initial 64-context test run.
        # Repeated synthetic corruption fixtures keep actual write ordering but
        # do not benchmark thousands of Windows FlushFileBuffers operations.
        sync_patch = patch.object(r.os, 'fsync', return_value=None)
        sync_patch.start()
        self.addCleanup(sync_patch.stop)
        paths = sorted(r.contract.REQUIRED_FREEZE_FILES)
        for path in paths:
            r.write_new(self.root / path, b'synthetic code fixture')
        r.write_json(self.root / r.FREEZE, {'version': 'input-execution-v1-local-question-execution-freeze-v1', 'actualQuestionCallsAtFreeze': 0,
                                          'files': [r.reference(self.root, p) for p in paths]})
        files = []
        for rid in r.contract.IDS:
            raw = r.packed(packet(rid))
            r.write_new(self.export / (rid + '.json'), raw)
            files.append({'reviewId': rid, 'path': rid + '.json', 'sha256': r.sha(raw)})
        manifest = {'version': 'input-execution-v1-local-question-export-v1', 'questionPackets': 64, 'packetFiles': files,
                    'protocol': r.reference(self.root, r.PROTOCOL), 'executionFreeze': r.reference(self.root, r.FREEZE),
                    'formalManifestSha256': 'a' * 64, 'bundleSha256': 'b' * 64}
        r.write_json(self.export / 'manifest.json', manifest)
        self.install = {'manifest': r.reference(self.root, r.PROTOCOL), 'modelSha256': r.contract.MODEL_SHA, 'files': [], 'statIdentities': {}}
        case = self
        class Opener:
            def __init__(self):
                self.current = None
                self.prompt = None
            def open(self, request, timeout):
                endpoint = request.full_url.split(':', 2)[-1].split('/', 1)[-1]
                body = None if request.data is None else json.loads(request.data)
                if endpoint == 'health':
                    return Response({'status': 'ok'})
                if endpoint == 'props':
                    return Response({'model_path': str(case.root / r.MODEL), 'chat_template': TEMPLATE, 'total_slots': 1, 'default_generation_settings': {'n_ctx': 4096}})
                if endpoint == 'apply-template':
                    self.current = json.loads(body['messages'][1]['content'])
                    self.prompt = r.render_prompt(self.current, TEMPLATE)
                    case.delivered.append(deepcopy(body))
                    return Response({'prompt': self.prompt})
                if endpoint == 'tokenize':
                    return Response({'tokens': [META['boundaryTokenIds'][body['content']]] if body['content'] in META['boundaryTokenIds'] else [1, 2, 3]})
                if endpoint == 'detokenize':
                    return Response({'content': self.prompt})
                if endpoint == 'completion':
                    case.assertTrue(any((case.output / self.current['reviewId'] / 'http').glob('*completion.request.bin')))
                    case.assertTrue((case.output / self.current['reviewId'] / 'completion-intent.json').exists())
                    case.events.append('call:' + self.current['reviewId'])
                    if self.current['reviewId'] == case.fail_rid:
                        raise ConnectionResetError('synthetic interrupted request')
                    answer = {'answerKo': '합성 답변', 'reasonKo': '합성 근거', 'cannotDetermine': False, 'translationEvidence': [self.current['translation']]}
                    content = json.dumps({'reviewId': self.current['reviewId'], 'answers': {'q1': answer, 'q2': answer}}, ensure_ascii=False)
                    return Response({'content': content, 'prompt': self.prompt, 'tokens': [4, 5], 'tokens_evaluated': 3, 'tokens_predicted': 2,
                                     'timings': {'cache_n': 0, 'prompt_n': 3}, 'stop_type': 'eos', 'truncated': False, 'stopping_word': '',
                                     'generation_settings': r.contract.NATIVE_SAMPLING})
                raise AssertionError('unexpected endpoint')
        original_client = r.Client
        class FakeClient(original_client):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.opener = Opener()
        class FakeProcess:
            def __init__(self, pid):
                self.pid, self.alive = pid, True
            def poll(self):
                return None if self.alive else 1
        def spawn(owner, command, log, folder):
            process = FakeProcess(1000 + len(case.processes))
            case.processes.append(process)
            case.events.append('spawn:' + folder.name)
            identity = {'pid': process.pid, 'creationTicks': int((time.time() + 11644473600) * 10000000), 'priorityClass': 0x4000,
                        'workingSetBytes': 100, 'peakWorkingSetBytes': 100, 'privateBytes': 100}
            return process, None, {'pid': process.pid, 'identity': identity, 'atomicJobAssignment': True, 'createSuspended': True,
                                   'limitAppliedBeforeResume': True, 'resumePreviousCount': 1}
        def stop(process, owner):
            if case.cleanup_failure:
                raise OSError('synthetic unexpected cleanup failure')
            process.alive = False
            case.events.append('stop:' + str(process.pid))
            return {'stopped': True, 'pid': process.pid, 'terminationByRetainedHandle': True, 'exitCode': 1}
        class Scope:
            _active = True
            def __enter__(self):
                self.receipt = {'requested': True, 'released': False}
                return self
            def __exit__(self, *args):
                case.events.append('release')
                self.receipt['released'] = True
        class FakeMonitor:
            def __init__(self, process, limiter, folder, started):
                self.deadline, self.error = None, None
                self.rid = folder.name
                r.write_new(folder / 'resource-samples.jsonl', r.packed({'system': memory(), 'child': {'workingSetBytes': 100, 'peakWorkingSetBytes': 100, 'privateBytes': 100}}))
            def start(self): pass
            def sample(self): pass
            def check(self): pass
            def close(self):
                if case.late_guard_error and self.rid == 'R001':
                    self.error = 'other_model_or_worker_started'
        fake_resources = types.SimpleNamespace(ExperimentLock=Scope, PowerRequest=Scope, BELOW_NORMAL=0x4000,
            process_owner=types.SimpleNamespace(claim_process_owner=lambda: object()), stop_owned=stop)
        for name, value in {'resources': fake_resources, 'Client': FakeClient, 'Monitor': FakeMonitor, 'spawn': spawn,
                            'preflight_pair': preflights, 'installation': lambda root: deepcopy(case.install),
                            'python_identity': lambda: {'pythonVersion': 'synthetic CPython3.11', 'jinjaVersion': '3.1.6', 'files': {}},
                            'gguf_template': lambda path: (TEMPLATE, deepcopy(META))}.items():
            active = patch.object(r, name, value)
            active.start()
            self.addCleanup(active.stop)

    def run_fixture(self):
        with redirect_stdout(io.StringIO()):
            result = r.run(self.export, self.output, self.root)
        if type(self).cached_files is None:
            type(self).cached_files = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
            type(self).cached_result = deepcopy(result)
        return result

    def saved_fixture(self):
        if type(self).cached_files is None:
            return self.run_fixture()
        for relative, raw in type(self).cached_files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        return deepcopy(type(self).cached_result)

    def rewrite_json(self, path, value):
        path.write_bytes(r.packed(value))

    def reseal(self, context=None):
        for folder in ([context, self.output] if context is not None else [self.output]):
            summary, _ = r.read(folder / 'summary.json')
            summary['artifactFiles'] = r.snapshot(folder, self.root)
            self.rewrite_json(folder / 'summary.json', summary)

    def test_64_contexts_each_receive_only_their_packet_and_exactly_one_completion(self):
        result = self.run_fixture()
        self.assertEqual(len(result['contexts']), 64)
        self.assertEqual(len(self.processes), 64)
        self.assertTrue(all(not p.alive for p in self.processes))
        self.assertEqual(len([x for x in self.events if x.startswith('call:')]), 64)
        self.assertEqual([json.loads(x['messages'][1]['content']) for x in self.delivered], [packet(rid) for rid in r.contract.IDS])
        self.assertEqual(len({x['contextId'] for x in result['contexts']}), 64)
        for context in result['contexts']:
            self.assertTrue(all(item in result['evidence'] for item in context['evidence']))
        self.assertEqual(r.validate_run(self.output, self.export, self.root), result)

    def test_unknown_first_call_is_preserved_and_stops_before_next_context(self):
        self.fail_rid = 'R001'
        with self.assertRaisesRegex(ValueError, 'question_run_failed'):
            self.run_fixture()
        self.assertEqual(len(self.processes), 1)
        self.assertFalse((self.output / 'R002').exists())
        summary, _ = r.read(self.output / 'summary.json')
        self.assertEqual(summary['status'], 'failed')
        self.assertEqual(summary['completionRequestsSent'], 1)
        self.assertTrue(summary['nativeStopped'])
        request = list((self.output / 'R001/http').glob('*completion.request.bin'))[0]
        receipt, _ = r.read(request.with_name(request.name.replace('.request.bin', '.receipt.json')))
        self.assertEqual(receipt['transportOrProtocolErrorType'], 'ConnectionResetError')
        self.assertIsNone(receipt['rawResponsePath'])
        self.assertTrue((self.output / 'R001/completion-intent.json').exists())
        self.assertLess(self.events.index('stop:1000'), self.events.index('release'))

    def test_guard_latched_on_close_stops_before_a_second_packet(self):
        self.late_guard_error = True
        with self.assertRaisesRegex(ValueError, 'question_run_failed'):
            self.run_fixture()
        self.assertEqual(len(self.processes), 1)
        self.assertFalse((self.output / 'R002').exists())
        summary, _ = r.read(self.output / 'R001/summary.json')
        self.assertEqual(summary['status'], 'failed')
        self.assertEqual(summary['failure']['code'], 'other_model_or_worker_started')
        self.assertEqual(summary['monitorError'], 'other_model_or_worker_started')
        self.assertTrue(summary['nativeStopped'])
        self.assertEqual(summary['completionRequestsSent'], 1)
        self.assertTrue((self.output / 'R001/draft.json').exists())

    def test_unexpected_cleanup_failure_never_claims_native_stopped(self):
        self.cleanup_failure = True
        with self.assertRaisesRegex(ValueError, 'question_run_failed'):
            self.run_fixture()
        self.assertEqual(len(self.processes), 1)
        self.assertFalse((self.output / 'R002').exists())
        context, _ = r.read(self.output / 'R001/summary.json')
        summary, _ = r.read(self.output / 'summary.json')
        self.assertEqual(context['status'], 'failed')
        self.assertEqual(context['failure']['type'], 'OSError')
        self.assertFalse(context['nativeStopped'])
        self.assertTrue(context['shutdown']['processCreated'])
        self.assertTrue(context['shutdown']['cleanupUnconfirmed'])
        self.assertFalse(summary['nativeStopped'])
        self.assertEqual(summary['completionRequestsSent'], 1)

    def test_existing_run_and_global_claim_prevent_silent_regeneration(self):
        self.saved_fixture()
        previous = len(self.processes)
        with self.assertRaisesRegex(ValueError, 'must_be_new'):
            self.run_fixture()
        with self.assertRaisesRegex(ValueError, 'question_run_failed'):
            r.run(self.export, self.output.with_name('another'), self.root)
        self.assertEqual(len(self.processes), previous)

    def test_source_or_extra_file_in_question_export_is_rejected_before_spawn(self):
        r.write_json(self.export / 'source.json', {'source': 'excluded synthetic source'})
        with self.assertRaisesRegex(ValueError, 'inventory'):
            self.run_fixture()
        self.assertFalse(self.processes)

    def test_packet_hash_or_identity_change_is_rejected_before_spawn(self):
        self.rewrite_json(self.export / 'R001.json', packet('R002'))
        with self.assertRaisesRegex(ValueError, 'hash_or_id'):
            self.run_fixture()
        self.assertFalse(self.processes)

    def test_packet_source_field_is_rejected(self):
        self.rewrite_json(self.export / 'R001.json', packet('R001') | {'source': 'not allowed'})
        with self.assertRaisesRegex(ValueError, 'allowlist'):
            self.run_fixture()

    def test_frozen_code_change_is_rejected(self):
        (self.root / r.PROTOCOL).write_bytes(b'changed protocol')
        with self.assertRaisesRegex(ValueError, 'hash_changed'):
            self.run_fixture()

    def test_required_execution_module_cannot_be_omitted_from_freeze(self):
        freeze, _ = r.read(self.root / r.FREEZE)
        freeze['files'] = [item for item in freeze['files'] if not item['path'].endswith('run_io.py')]
        self.rewrite_json(self.root / r.FREEZE, freeze)
        manifest, _ = r.read(self.export / 'manifest.json')
        manifest['executionFreeze'] = r.reference(self.root, r.FREEZE)
        self.rewrite_json(self.export / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'required_question_code_not_frozen'):
            self.run_fixture()

    def test_development_traversal_and_holdout_are_rejected(self):
        for path in (str(self.root / r.BASE / '..' / 'escape'), str(self.root / r.BASE / 'holdout-new')):
            with self.assertRaises(ValueError):
                r.development(self.root, path)

    def test_cross_packet_saved_input_is_rejected_even_when_artifact_hashes_updated(self):
        self.saved_fixture()
        folder = self.output / 'R001'
        self.rewrite_json(folder / 'input-packet.json', packet('R002'))
        self.reseal(folder)
        with self.assertRaisesRegex(ValueError, 'cross_packet'):
            r.validate_run(self.output, self.export, self.root)

    def test_draft_rewrite_is_rejected_by_native_replay(self):
        self.saved_fixture()
        folder = self.output / 'R001'
        draft, _ = r.read(folder / 'draft.json')
        draft['answers'][0]['answerKo'] = 'rewritten answer'
        self.rewrite_json(folder / 'draft.json', draft)
        self.reseal(folder)
        with self.assertRaisesRegex(ValueError, 'draft_rewritten'):
            r.validate_run(self.output, self.export, self.root)

    def test_unconfirmed_native_stop_is_rejected(self):
        self.saved_fixture()
        folder = self.output / 'R001'
        summary, _ = r.read(folder / 'summary.json')
        summary['shutdown']['stopped'] = False
        self.rewrite_json(folder / 'summary.json', summary)
        self.reseal(folder)
        with self.assertRaisesRegex(ValueError, 'completed_one_call'):
            r.validate_run(self.output, self.export, self.root)

    def test_guard_reports_ac_memory_ws_and_private_failures(self):
        self.assertIsNone(r.resource_reason(memory()))
        self.assertEqual(r.resource_reason(memory() | {'ACLineStatus': 0}), 'ac_power_lost_or_unknown')
        self.assertIsNotNone(r.resource_reason(memory() | {'availableCommitBytes': 0}))
        self.assertEqual(r.resource_reason(memory(), {'workingSetBytes': 7 * r.GIB, 'peakWorkingSetBytes': 7 * r.GIB, 'privateBytes': 1}), 'working_set_limit_exceeded')
        self.assertEqual(r.resource_reason(memory(), {'workingSetBytes': 1, 'peakWorkingSetBytes': 1, 'privateBytes': 7 * r.GIB}), 'private_bytes_observation_exceeded')


if __name__ == '__main__':
    unittest.main()
