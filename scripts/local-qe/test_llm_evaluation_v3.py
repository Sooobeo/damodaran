"""Synthetic native evidence only: no runtime imports, native children or model loads."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import evaluate_llm_review_v3 as e


class NativeFixture:
    def __init__(self, folder, count=1, mismatch=True, failed=False):
        self.dest = folder
        self.run = folder / 'runs/synthetic'
        self.run.mkdir(parents=True)
        self.contract, self.mapper, self.gate = e.load_modules()
        self.rows, self.labels, identity = e.fixed_inputs(e.INPUT, self.gate)
        self.binding = dict(sha256='synthetic-root-binding', rootReviewed=True)
        self.frozen = dict(binding=self.binding, budgetRows={}, profileSha256='synthetic', tokenBudgetAuditSha256='synthetic')
        self.s = dict(version=e.RUNTIME, expectedCount=8, inputSha256=e.INPUT_SHA, resourceProfile=e.PROFILE,
            referencesOrJudgmentsIncluded=False, inputFields=['source', 'translation', 'context'], contextTokens=4096,
            sampling=e.SAMPLING, nativeStartupConfiguration=e.STARTUP, materialWarningMapping=self.mapper.identity(), screenGate=identity,
            thinking={'enable_thinking': False, 'suffixMustBeVerifiedAtRuntime': True},
            timeLimitsSeconds={'startup': 600, 'request': 600, 'total': 14400}, responseByteLimit=2*1024**2,
            fullBaselineAccepted=False, allEightCorrectIsAcceptance=False, automaticRetries=False,
            independentHoldout=False, developmentDiagnosisOnly=True, semanticSpanPrecision='not_evaluated',
            status='failed' if failed else 'stopped_futility' if mismatch else 'completed', completedCount=count,
            generationRequests=count, screenDecisions=[], artifacts={}, ownedPid=12345,
            initialChildTelemetry=self.telemetry(0), modelLoaded=True, runtimeContractValidated=True,
            ggufMetadata={'templateSha256': e.TEMPLATE_SHA}, runtimeEogTokens={'248046': '<|im_end|>'},
            guard=dict(abortReason=None, killError=None, lastDeadlineExceededAtEnd=False),
            finalIntegrityVerified=True, childProcessStopped=True,
            nativeTemplateParity=dict(status='verified', rowsVerified=count, firstRequestVerifiedBeforeGeneration=True))
        ws = dict(pid=12345, creationTicks=123456789, after=dict(hardMaximumEnabled=True, maximumBytes=6*e.GIB-64*1024**2))
        self.s['workingSet'] = ws
        self.s['spawn'] = dict(version='comparison-suspended-owned-spawn-v1', pid=12345,
            atomicJobAssignment=True, createSuspended=True, limitAppliedBeforeResume=True, resumePreviousCount=1, workingSetLimit=ws)
        self.s['memoryPreflight'] = dict(physicalPassed=True, commitPassed=True,
            observed=dict(availablePhysical=9*e.GIB, availableCommit=9*e.GIB),
            maximumChildWorkingSetBytes=6*e.GIB, maximumChildPrivateBytes=6*e.GIB,
            minimumAvailablePhysicalBytes=9*e.GIB, minimumAvailableCommitBytes=9*e.GIB)
        template = (e.DEST / 'token-budget-v1/attempt-001/actual-chat-template.jinja').read_text(encoding='utf-8')
        self.write('runtime-props.raw.json', dict(chat_template=template, total_slots=1,
            default_generation_settings={'n_ctx': 4096}, model_path=str(folder/'Qwen3.5-9B-Q4_K_M.gguf')))
        self.raw('runtime.log', "n_threads = 4 (n_threads_batch = 4)\nn_ctx = 4096\ncontext checkpoints enabled, max = 3\nprinting all EOG tokens:\nlog: - 248046 ('<|im_end|>')\n")
        self.raw('requests.jsonl', ''.join(json.dumps({'id': r['id'], 'request': self.contract.build_request(r)}, ensure_ascii=False)+'\n' for r in self.rows))
        claim = folder/'.dev8-v5-generation.claim.json'
        claim.write_text(json.dumps(dict(version=e.RUNTIME, output=str(self.run), inputSha256=e.INPUT_SHA,
            executionBindingSha256=self.binding['sha256'], singleDiagnosticAttemptOnly=True, automaticRetries=False)), encoding='utf-8')
        self.s['diagnosticClaim'] = dict(path=str(claim), sha256=e.digest(claim))
        events = []
        for index, row in enumerate(self.rows, 1):
            prompt = e.rendered(self.contract.build_request(row))
            tokens = list(range(20))
            self.frozen['budgetRows'][row['id']] = dict(inputTokens=20, fitsStrict=True,
                promptSha256=e.sha(prompt.encode('utf-8')), tokenIdsSha256=e.token_hash(tokens))
            if index > count:
                continue
            truth = self.labels[index-1]['materialError']
            warning = not truth if mismatch and index == count else truth
            content = {'semantic_issues': [], 'uncertainties': []}
            if warning:
                content['uncertainties'] = [dict(source_quote=row['source'], translation_quote=row['translation'], reason='합성 의미 불확실성 표본')]
            raw_content = json.dumps(content, ensure_ascii=False)
            cached = 0 if index == 1 else 18
            timing = dict(cache_n=cached, prompt_n=20-cached, prompt_ms=100., predicted_ms=200.)
            cache = dict(reusedPromptTokens=cached, processedPromptTokens=20-cached,
                promptMilliseconds=100., predictedMilliseconds=200., nativeTimings=timing,
                slotTokensAfterResponse=40, prefixReuseObserved=cached>0, sameSeedGuaranteesSameOutput=False)
            prefix = f'{index:04d}'
            raw_name = prefix+'-completion.raw.json'
            self.write(prefix+'-template.raw.json', {'prompt': prompt})
            self.write(prefix+'-tokenize.raw.json', {'tokens': tokens})
            self.write(prefix+'-native-request.json', e.SAMPLING | dict(prompt=tokens, json_schema=self.contract.load_schema(), message_delimiters=e.DELIMITERS))
            self.write(raw_name, dict(prompt=prompt, content=raw_content, tokens=[1, 2, 3], stop_type='eos', truncated=False,
                stopping_word='', generation_settings=e.SAMPLING, timings=timing, tokens_cached=40))
            self.write(prefix+'-completion-receipt.json', dict(id=row['id'], rawResponseFile=raw_name,
                rawResponseSha256=e.digest(self.run/raw_name), returnedAt='2026-09-11T00:00:01+00:00', lastDeadlineExceededAtEnd=False))
            validated = self.contract.validate_response(row, raw_content)
            mapping = self.mapper.classify(validated)
            record = dict(id=row['id'], status='completed', seconds=1., rawResponseFile=raw_name,
                rawResponseSha256=e.digest(self.run/raw_name), promptSha256=e.sha(prompt.encode('utf-8')), inputTokens=20,
                outputTokens=3, assessment=validated, materialWarningV3=mapping, cache=cache,
                childTelemetry=self.telemetry(index), humanReviewed=False, semanticQualityCertified=False)
            name = prefix+'-assessment.json'
            self.write(name, record)
            decision = self.gate.check(row['id'], mapping) | dict(index=index, assessmentFile=name,
                assessmentSha256=e.digest(self.run/name), rawResponseFile=raw_name, rawResponseSha256=e.digest(self.run/raw_name))
            self.write(prefix+'-gate.json', decision)
            self.s['screenDecisions'].append(decision)
            if not decision['continueRun']:
                self.s['futilityStop'] = decision
            child = self.telemetry(index) | dict(workingSetBytes=5*e.GIB, peakWorkingSetBytes=5*e.GIB, privateBytes=4*e.GIB)
            events.append(dict(at=f'2026-09-11T00:00:{index:02d}+00:00', child=child,
                system=dict(availablePhysical=4*e.GIB, availableCommit=5*e.GIB), abortReason=None,
                phase='request', rowId=row['id'], requestStartUTC='2026-09-11T00:00:00+00:00',
                requestElapsedSeconds=index, heartbeatGapSeconds=1., actualGeneratedTokenProgress='unknown_nonstreaming'))
        self.raw('memory.jsonl', ''.join(json.dumps(event)+'\n' for event in events))
        self.s['itemStatuses'] = [dict(id=row['id'], status='completed' if i<count else 'not_run') for i,row in enumerate(self.rows)]
        self.s['unexecutedIds'] = [r['id'] for r in self.rows[count:]]
        if failed:
            self.s['failure'] = dict(code='synthetic_failure', rowId=None)
        self.save()

    @staticmethod
    def telemetry(seconds):
        return dict(pid=12345, creationTicks=123456789, priorityClass=16384, priorityName='BelowNormal',
                    kernelCpuSeconds=0., userCpuSeconds=float(seconds), totalCpuSeconds=float(seconds))

    def raw(self, name, raw):
        (self.run/name).write_text(raw, encoding='utf-8', newline='\n')
        self.s['artifacts'][name] = e.digest(self.run/name)

    def write(self, name, value):
        self.raw(name, json.dumps(value, ensure_ascii=False, indent=2)+'\n')

    def mutate(self, name, fn):
        value = e.read(self.run/name)
        fn(value)
        self.write(name, value)

    def save(self):
        (self.run/'summary.json').write_text(json.dumps(self.s, ensure_ascii=False), encoding='utf-8')

    def analyze(self):
        self.save()
        with patch.object(e, 'DEST', self.dest), patch.object(e, 'verify_setup', return_value=self.frozen):
            return e.analyze_run(self.run, e.INPUT, self.dest/'synthetic-binding.json')


class EvaluationTests(unittest.TestCase):
    def test_producer_installation_inventory_includes_manifest_and_rejects_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            native, model, manifest = [root / name for name in ('native.exe', 'model.gguf', 'install-manifest.json')]
            for path in (native, model, manifest):
                path.write_bytes(b'synthetic local file')
            identities = {}
            for path in (native, model, manifest):
                st = path.stat()
                identities[str(path)] = [st.st_size, st.st_mtime_ns, st.st_ino]
            installation = {'statIdentities': identities}
            runtime_files = {str(native): e.digest(native)}
            e.verify_installation_stats(installation, runtime_files, model, manifest)
            missing = copy.deepcopy(installation)
            del missing['statIdentities'][str(manifest)]
            with self.assertRaisesRegex(ValueError, 'installation_inventory_differs'):
                e.verify_installation_stats(missing, runtime_files, model, manifest)
            extra = copy.deepcopy(installation)
            extra['statIdentities'][str(root/'unlisted')] = [0, 0, 0]
            with self.assertRaisesRegex(ValueError, 'installation_inventory_differs'):
                e.verify_installation_stats(extra, runtime_files, model, manifest)
            manifest.write_bytes(b'changed manifest bytes')
            with self.assertRaisesRegex(ValueError, 'installation_stat_differs'):
                e.verify_installation_stats(installation, runtime_files, model, manifest)

    def fixture(self, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return NativeFixture(Path(tmp.name), **kwargs)

    def test_first_fn_stops_with_conditional_bound_and_missing_fn(self):
        result = self.fixture().analyze()
        self.assertEqual(result['runStatus'], 'stopped_futility')
        self.assertTrue(result['diagnosticOperationalClosurePassed'])
        self.assertEqual(result['diagnosticMetrics']['falseNegatives'], 4)
        self.assertIsNone(result['diagnosticMetrics']['precision'])
        self.assertEqual(result['conditionalFutility']['bestPossibleFull48RecallAfterThisError'], 13/14)
        self.assertFalse(result['fullBaselineAccepted'])

    def test_all8_matched_is_never_full48_acceptance(self):
        result = self.fixture(count=8, mismatch=False).analyze()
        self.assertTrue(result['diagnosticAll8Matched'])
        self.assertEqual(result['diagnosticMetrics']['precision'], 1.)
        self.assertFalse(result['fullBaselineAccepted'])
        self.assertEqual(result['semanticSpanPrecision'], 'not_evaluated')

    def test_first_fp_bound(self):
        result = self.fixture(count=5).analyze()
        self.assertEqual(result['conditionalFutility']['bestPossibleFull48PrecisionAfterThisError'], 14/15)
        self.assertEqual(result['diagnosticMetrics']['falsePositives'], 1)

    def test_failed_run_keeps_valid_completed_units_separate(self):
        result = self.fixture(count=2, mismatch=False, failed=True).analyze()
        self.assertEqual(result['partialCompletedEvidenceCount'], 2)
        self.assertEqual(result['diagnosticMetrics']['falseNegatives'], 2)
        self.assertFalse(result['diagnosticOperationalClosurePassed'])

    def test_summary_forgery_and_resource_failures_rejected(self):
        changes = [lambda s:s.update(expectedCount=48), lambda s:s.update(generationRequests=2),
            lambda s:s.update(childProcessStopped=False), lambda s:s.update(finalIntegrityVerified=False),
            lambda s:s.update(status='completed'), lambda s:s['guard'].update(lastDeadlineExceededAtEnd=True),
            lambda s:s['initialChildTelemetry'].update(priorityClass=32),
            lambda s:s['memoryPreflight']['observed'].update(availablePhysical=9*e.GIB-1),
            lambda s:s['spawn'].update(limitAppliedBeforeResume=False),
            lambda s:s['screenDecisions'][0].update(continueRun=True),
            lambda s:s['nativeTemplateParity'].update(firstRequestVerifiedBeforeGeneration=False)]
        for mutate in changes:
            with self.subTest(mutation=changes.index(mutate)):
                fixture = self.fixture()
                mutate(fixture.s)
                with self.assertRaises(ValueError): fixture.analyze()

    def test_native_artifact_corruption_and_semantic_mapping_forgery_rejected(self):
        mutations = [('-template.raw.json', lambda x:x.update(prompt=x['prompt']+'x')),
            ('-tokenize.raw.json', lambda x:x.update(tokens=[True])),
            ('-native-request.json', lambda x:x.update(cache_prompt=False)),
            ('-native-request.json', lambda x:x.update(json_schema={})),
            ('-completion.raw.json', lambda x:x.update(truncated=True)),
            ('-completion.raw.json', lambda x:x.update(stop_type='limit')),
            ('-completion.raw.json', lambda x:x['generation_settings'].update(temperature=.3)),
            ('-completion-receipt.json', lambda x:x.update(lastDeadlineExceededAtEnd=True)),
            ('-assessment.json', lambda x:x['materialWarningV3'].update(primaryWarning=True)),
            ('-assessment.json', lambda x:x['assessment'].update(whole_paragraph_review=True)),
            ('-assessment.json', lambda x:x['childTelemetry'].update(creationTicks=1)),
            ('-gate.json', lambda x:x.update(continueRun=True))]
        for suffix, mutate in mutations:
            with self.subTest(suffix=suffix):
                fixture = self.fixture()
                fixture.mutate('0001'+suffix, mutate)
                with self.assertRaises(ValueError): fixture.analyze()

    def test_extra_generation_after_mismatch_and_unlisted_file_rejected(self):
        fixture = self.fixture()
        fixture.write('0002-native-request.json', {})
        with self.assertRaises(ValueError): fixture.analyze()
        fixture = self.fixture()
        fixture.write('0002-completion.raw.json', {})
        with self.assertRaises(ValueError): fixture.analyze()
        fixture = self.fixture()
        (fixture.run/'unlisted.py').write_text('raise RuntimeError("must never import")', encoding='utf-8')
        with self.assertRaises(ValueError): fixture.analyze()

    def test_strict_json_and_fixed_projection(self):
        for raw in ('{"a":1,"a":2}', '{"x":NaN}'):
            with self.assertRaises(ValueError): e.parse(raw)
        with self.assertRaises(ValueError): e.fixed_inputs(e.DATA/'inputs.jsonl', e.load_modules()[2])
        self.assertFalse(e.same(True, 1))

    def test_memory_limit_exact_boundary(self):
        fixture = self.fixture()
        event = e.lines(fixture.run/'memory.jsonl')[0]
        event['child']['peakWorkingSetBytes'] = 6*e.GIB
        fixture.raw('memory.jsonl', json.dumps(event)+'\n')
        self.assertTrue(fixture.analyze()['resourceEvidence']['passed'])
        event['child']['peakWorkingSetBytes'] += 1
        fixture.raw('memory.jsonl', json.dumps(event)+'\n')
        with self.assertRaises(ValueError): fixture.analyze()

    def test_allowlist_is_exact_and_never_imports_receipt_code(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'danger.py'
            path.write_text('raise RuntimeError("not imported")', encoding='utf-8')
            e.verify_hashes({str(path.resolve()): e.digest(path)}, [path])
            with self.assertRaises(ValueError): e.verify_hashes({str(path.resolve()): e.digest(path)}, [])
            with self.assertRaises(ValueError): e.verify_hashes({}, [path])

    def test_unreviewed_binding_and_changed_freeze_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dest = root/'candidate'
            dest.mkdir()
            receipts = root/'.training/verifications'
            receipts.mkdir(parents=True)
            freeze = dest/'freeze-v5-dev8.json'
            freeze.write_text('{}', encoding='utf-8')
            receipt = receipts/'binding.json'
            contract, mapper, _ = e.load_modules()
            with patch.object(e, 'ROOT', root), patch.object(e, 'DEST', dest):
                for values in [dict(rootReviewed=False, freezeSha256=e.digest(freeze)),
                               dict(rootReviewed=True, freezeSha256='0'*64)]:
                    receipt.write_text(json.dumps(dict(version=e.BINDING, freezePath='candidate/freeze-v5-dev8.json', **values)), encoding='utf-8')
                    with self.assertRaises(ValueError): e.verify_setup({}, receipt, [], contract, mapper, {})


if __name__ == '__main__':
    unittest.main()
