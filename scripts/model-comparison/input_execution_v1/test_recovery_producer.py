"""Recovery lifecycle tests with synthetic responses only; no native/model calls."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io,json
from pathlib import Path
import sys,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent));sys.path.insert(0,str(HERE))
from input_execution_v1 import recovery_producer as p,run_io as r
from test_runtime_contract import row as fixture_row,response as fixture_response

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        target=p.ROOT/'.training/verifications'
        self.temporary=tempfile.TemporaryDirectory(prefix='s4-recovery-test-',dir=target)
        self.root=Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(target.resolve()))
        self.output=self.root/'recovery-attempt-001';self.output.mkdir()
        self.patches=ExitStack()
        self.patches.enter_context(patch.object(p,'ROOT',self.root))
        self.patches.enter_context(patch.object(p,'BASE',self.root))
        self.patches.enter_context(patch.object(r,'ROOT',self.root))
    def tearDown(self):
        self.patches.close();self.temporary.cleanup()
    def summary(self):return json.loads((self.output/'summary.json').read_bytes())

    def execute_fake(self, *, parity_failure=False, completion_failure=False, corrupt_numeric=False, ac_failure=False, prior_failure=False):
        events = []
        rows = []
        for number in range(1, 17):
            for config in ("C0", "C1", "C2", "C3"):
                value = fixture_row()
                value.update(id=(f"IP1-F{number:02d}" if number<=8 else f"IP1-G{number-8:02d}"), configuration=config)
                value["context"]["policyVersion"] = "fixture-context"
                value["terminology"]["selectorVersion"] = "fixture-terms"
                rows.append(value)

        class Scope:
            def __init__(self, name):
                self.name, self.receipt = name, {"fixture": name}

            def __enter__(self):
                events.append(self.name + "-enter")
                return self

            def __exit__(self, *args):
                events.append(self.name + "-exit")

        class Process:
            pid = 987654
            stopped = False

            def poll(self):
                return 0 if self.stopped else None

        process = Process()

        class Monitor:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                events.append("monitor-start")

            def ready(self):
                pass

            def check(self):
                pass

            def begin_request(self):
                events.append("begin-request")

            def end_request(self):
                events.append("end-request")

            def stop(self):
                events.append("monitor-stop")

            def summary(self):
                return {"fixture": True}

        def stop_owned(child, owner):
            if not child.stopped:
                events.append("native-stop")
                child.stopped = True
            return {"stopped": True}

        def require_preflight(value):
            if ac_failure:raise p.guard.ResourceGuardError('ac_power_required')
        def validate_prior(plan):
            if prior_failure:raise r.RunError('original_evidence_changed')
        fake_guard = SimpleNamespace(
            PROFILE={"preflightSeparationSeconds": 0}, ResourceGuardError=p.guard.ResourceGuardError,
            ExperimentLock=lambda: Scope("lock"), PowerRequest=lambda: Scope("power"),
            preflight=lambda **kwargs: {"passed": True}, require_preflight=require_preflight,
            spawn_guarded=lambda *args: (process, object(), {"fixture": True}),
            ResourceMonitor=Monitor, stop_owned=stop_owned)
        state = {"templates": 0, "tokenizes": 0, "completions": 0}

        class Client:
            def __init__(self, base, key, output):
                self.last, self.completions = None, 0

            def request(self, endpoint, payload=None, timeout=15):
                events.append(endpoint)
                if endpoint == "/health":
                    return {"status": "ok"}
                if endpoint == "/props":
                    return {}
                if endpoint == "/apply-template":
                    state["templates"] += 1
                    if parity_failure and state["templates"] == 64:
                        return {"prompt": "mismatch"}
                    return {"prompt": "<|startoftext|>" + payload["messages"][0]["content"] + "<|extra_0|>"}
                if endpoint == "/tokenize":
                    state["tokenizes"] += 1
                    return {"tokens": fixture_row()["tokenIds"]}
                if endpoint == "/completion":
                    self.assert_parity()
                    state["completions"] += 1
                    self.completions += 1
                    self.last = {"rawResponsePath": "fixture.raw", "rawResponseSha256": "f" * 64}
                    if completion_failure:
                        raise r.RunError("fixture_completion_failed")
                    value = fixture_response()
                    if corrupt_numeric:
                        value["content"] = "수수료는 25유로이다."
                    return value
                raise AssertionError(endpoint)

            @staticmethod
            def assert_parity():
                if state["templates"] != 64 or state["tokenizes"] != 64:
                    raise AssertionError("completion before all 64 input parity checks")

        plan = {"inputFiles": [],"outputOrder":[[v['id'],v['configuration']] for v in rows],
                "pendingOutputOrder":[[v['id'],v['configuration']] for v in rows[38:]]}
        with ExitStack() as stack:
            stack.enter_context(patch.object(p, "guard", fake_guard))
            stack.enter_context(patch.object(p, "validate_prior_binding", side_effect=validate_prior))
            stack.enter_context(patch.object(p, "LocalClient", Client))
            stack.enter_context(patch.object(p, "claim_process_owner", return_value=SimpleNamespace(assert_owned=lambda: None)))
            stack.enter_context(patch.object(p, "check_files", side_effect=lambda *args, **kwargs: events.append("check-files")))
            stack.enter_context(patch.object(p.common, "gguf_contract", return_value=("template-fixture", {})))
            stack.enter_context(patch.object(p.common, "server_command", side_effect=lambda port, key: ["fixture.exe", "--api-key", key]))
            stack.enter_context(patch.object(p.common, "validate_runtime_tokens", return_value={"fixture": True}))
            stack.enter_context(patch.object(p.common, "eog_from_log", return_value={"fixture": True}))
            stack.enter_context(patch.object(p.runtime, "validate_props", return_value={"fixture": True}))
            stack.enter_context(redirect_stdout(io.StringIO()))
            result = p.execute(rows, plan, self.output)
        return result, self.summary(), events, state


    def test_only_tail26_called_after_all64_parity(self):
        code,summary,events,state=self.execute_fake()
        self.assertEqual(code,0);self.assertEqual(state['completions'],26)
        self.assertEqual(state['templates'],64);self.assertEqual(state['tokenizes'],64)
        self.assertEqual(summary['completedOutputs'],26);self.assertEqual(summary['missingOutputs'],0)
        self.assertEqual(summary['retainedOutputs'],38);self.assertTrue(summary['outputIntegrityPassed'])
        rows=[json.loads(line) for line in (self.output/'predictions.jsonl').read_text('utf-8').splitlines()]
        self.assertEqual((rows[0]['id'],rows[0]['configuration']),('IP1-G02','C2'))
        self.assertEqual([v['preparedSequence'] for v in rows],list(range(39,65)))
        self.assertEqual(sum(v['technicalRecoveryRetry'] for v in rows),1)
        self.assertTrue(all(v['producerVersion']==p.VERSION and v['runId']=='recovery-attempt-001' for v in rows))
        self.assertLess(events.index('native-stop'),events.index('power-exit'))
        self.assertLess(events.index('power-exit'),events.index('lock-exit'))

    def test_ac_failure_never_starts_native_or_sends_completion(self):
        code,summary,events,state=self.execute_fake(ac_failure=True)
        self.assertEqual(code,1);self.assertEqual(state['completions'],0)
        self.assertNotIn('monitor-start',events);self.assertNotIn('/health',events)
        self.assertEqual(summary['missingOutputs'],26);self.assertTrue(summary['childProcessStopped'])
        self.assertTrue((self.output/'preflight-before-integrity.json').is_file())

    def test_changed_prior_fails_before_native(self):
        code,summary,events,state=self.execute_fake(prior_failure=True)
        self.assertEqual(code,1);self.assertEqual(state['completions'],0)
        self.assertNotIn('monitor-start',events)
        self.assertEqual(summary['failure']['code'],'original_evidence_changed')

    def test_last_parity_failure_preserves26missing_zero_calls(self):
        code,summary,events,state=self.execute_fake(parity_failure=True)
        self.assertEqual(code,1);self.assertEqual(state['completions'],0)
        self.assertEqual(summary['missingOutputs'],26)
        self.assertLess(events.index('native-stop'),events.index('power-exit'))

    def test_interrupted_retry_failure_has_intent_no_automatic_retry(self):
        code,summary,events,state=self.execute_fake(completion_failure=True)
        self.assertEqual(code,1);self.assertEqual(state['completions'],1)
        self.assertEqual(summary['completionRequestsSent'],1);self.assertEqual(summary['completedOutputs'],0)
        self.assertEqual(summary['missingOutputs'],26)
        self.assertEqual(summary['failure']['id'],'IP1-G02');self.assertEqual(summary['failure']['configuration'],'C2')
        attempt=json.loads((self.output/'attempt-events.jsonl').read_text('utf-8').splitlines()[0])
        self.assertTrue(attempt['technicalRecoveryRetry']);self.assertFalse(attempt['automaticRetry'])
        self.assertEqual(attempt['preparedSequence'],39)
        self.assertLess(events.index('native-stop'),events.index('power-exit'))

    def test_numeric_warning_keeps_output_without_regeneration(self):
        code,summary,events,state=self.execute_fake(corrupt_numeric=True)
        self.assertEqual(code,0);self.assertEqual(state['completions'],26)
        self.assertEqual(len(summary['automaticCheckFailureRows']),26)

    def test_second_recovery_after_any_request_intent_is_rejected(self):
        r.write_json(self.output/'http/00001-completion.request.json',{'fixture':'intent'})
        with self.assertRaisesRegex(r.RunError,'new_contract'):p.existing_recovery_generations()

    def test_preflight_only_attempt_allows_later_real_execution(self):
        r.write_json(self.output/'http/00001-health.request.json',{'fixture':'health'})
        p.existing_recovery_generations()

    def test_unknown_original_run_completion_is_not_silently_ignored(self):
        base=self.root/'original-runs';prior=base/'attempt-001'
        r.write_json(prior/'http/00001-completion.request.json',{'fixture':'known original'})
        with patch.object(p.original,'BASE',base),patch.object(p,'PRIOR',prior):
            p.existing_recovery_generations()
            r.write_json(base/'attempt-002/http/00001-completion.request.json',{'fixture':'unknown original'})
            with self.assertRaisesRegex(r.RunError,'other_original'):p.existing_recovery_generations()

    def test_recovery_writer_refuses_other_root_and_other_file(self):
        for path in (self.root.parent/'predictions.jsonl',self.output/'summary.json'):
            with self.subTest(path=str(path)),self.assertRaises(r.RunError):
                p.append_event(path,{'fixture':True})
        p.append_event(self.output/'predictions.jsonl',{'fixture':'retained'})
        before=(self.output/'predictions.jsonl').read_bytes()
        p.append_event(self.output/'predictions.jsonl',{'fixture':'next'})
        self.assertTrue((self.output/'predictions.jsonl').read_bytes().startswith(before))

    def test_original_runtime_and_limits_are_reused(self):
        self.assertIs(p.guard,p.original.guard)
        self.assertIs(p.runtime,p.original.runtime)
        self.assertIs(p.common,p.original.common)
        self.assertEqual((p.STARTUP_SECONDS,p.REQUEST_SECONDS,p.TOTAL_SECONDS),(240,1800,28800))

    def test_missing_order_cannot_include_retained_or_omit_tail(self):
        rows=[{'id':f'IP1-{domain}{i:02d}','configuration':c} for domain in ('F','G') for i in range(1,9) for c in ('C0','C1','C2','C3')]
        order=[[v['id'],v['configuration']] for v in rows]
        plan={'outputOrder':order,'pendingOutputOrder':order[38:]}
        self.assertEqual(p.select_pending(rows,plan),rows[38:])
        for changed in (order[37:63],order[39:],list(reversed(order[38:]))):
            with self.subTest(changed=changed),self.assertRaises(r.RunError):
                p.select_pending(rows,dict(plan,pendingOutputOrder=changed))

if __name__=='__main__':unittest.main()
