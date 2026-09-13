"""V5 pure helpers and synthetic local fixtures; no actual test/model is read."""
from __future__ import annotations

from difflib import SequenceMatcher
import importlib.util
import json
from pathlib import Path
import random
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("v5_training_under_test", Path(__file__).with_name("train_v5.py"))
v5 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v5)


def sample(identifier, source, split="train"):
    return {"id": identifier, "source": source, "target": source, "split": split, "domain": "finance", "terms": [],
            "provenance": "unit fixture only, not training data"}


def legacy_oracle(rows):
    # Exact original logic, including orientation and issue order. In addition,
    # the filesystem integration below invokes the frozen implementation itself.
    flat = [(split, row, v5.engine.normalized_source(row["source"]), v5.engine.normalized_source(row["source"], True))
            for split, values in rows.items() for row in values]
    issues = []
    for position, (split, row, exact, template) in enumerate(flat):
        for other_split, other, other_exact, other_template in flat[:position]:
            reason = None
            if exact == other_exact:
                reason = "normalized-source-duplicate"
            elif split != other_split and template == other_template:
                reason = "numeric-template-leakage"
            elif split != other_split and min(len(exact), len(other_exact)) >= 35:
                tokens, other_tokens = set(template.split()), set(other_template.split())
                overlap = len(tokens & other_tokens) / max(1, len(tokens | other_tokens))
                ratio = SequenceMatcher(None, template, other_template, autojunk=False).ratio()
                if ratio >= 0.92 or (overlap >= 0.85 and ratio >= 0.82):
                    reason = "near-template-leakage"
            if reason:
                issues.append({"kind": reason, "first": {"split": other_split, "id": other["id"]},
                               "second": {"split": split, "id": row["id"]}})
    return issues


class LeakageBoundTests(unittest.TestCase):
    def test_boundary_scores_templates_and_token_reordering_match_oracle(self):
        sources = ["a" * 100, "a" * 92 + "b" * 8, "a" * 91 + "b" * 9,
                   "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima",
                   "alpha charlie bravo delta echo foxtrot golf hotel india juliet kilo lima",
                   "Revenue after 10 years equals 120 dollars.", "Revenue after 20 years equals 140 dollars.",
                   "Revenue AFTER 10 YEARS equals 120 dollars!", "short", "SHORT!"]
        rows = {split: [sample(f"{split}-{index}", source, split) for index, source in enumerate(sources)]
                for split in ("train", "dev", "test")}
        self.assertEqual(v5.leakage_issues(rows), legacy_oracle(rows))
        self.assertTrue(any(row["kind"] == "near-template-leakage" for row in v5.leakage_issues(rows)))

    def test_seeded_adversarial_pairs_match_oracle_without_relaxing_thresholds(self):
        randomizer = random.Random(834)
        vocabulary = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mango november orange papa queen romeo sierra tango uniform victor whiskey xray yankee zulu".split()
        rows = {"train": [], "dev": [], "test": []}
        for index in range(80):
            words = randomizer.sample(vocabulary, randomizer.randint(4, 20))
            base = " ".join(words)
            candidates = [base, base + " extra", " ".join(words[1:] + words[:1]), base.replace("a", "e"),
                          base[:randomizer.randint(8, len(base))], base + " 12", base + " 24"]
            for split in rows:
                source = randomizer.choice(candidates)
                rows[split].append(sample(f"{split}-{index}", source, split))
        self.assertEqual(v5.leakage_issues(rows), legacy_oracle(rows))

    def test_upper_bounds_prune_disjoint_or_unequal_sequences(self):
        rows = {"train": [sample("base", "alpha " * 30)],
                "dev": [sample("different", "zulu " * 30, "dev"), sample("shorter", "alpha " * 8, "dev")]}
        statistics = {}
        self.assertEqual(v5.leakage_issues(rows, statistics), legacy_oracle(rows))
        self.assertGreater(statistics["lengthBoundPruned"] + statistics["quickRatioPruned"], 0)
        self.assertLess(statistics["fullRatioCalls"], 2)

    def test_actual_frozen_validator_produces_identical_issue_order(self):
        from types import SimpleNamespace
        v5.WORK_ROOT.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="unit-v5-oracle-", dir=v5.WORK_ROOT)
        directory = Path(temporary.name).resolve()
        try:
            rows = {"train": [sample("a", "The project returns 120 dollars after 4 years.")],
                    "dev": [sample("b", "The project returns 150 dollars after 9 years.", "dev")],
                    "test": [sample("c", "The project returns 120 dollars after 4 years.", "test")]}
            paths = {}
            for split, values in rows.items():
                path = directory / f"{split}.jsonl"
                path.write_text("".join(json.dumps(row) + "\n" for row in values), "utf-8")
                paths[split + "_data"] = v5.engine.relative(path)
            with self.assertRaisesRegex(ValueError, "leakage"):
                v5.LEGACY_VALIDATE_DATA(SimpleNamespace(**paths), directory)
            report = json.loads((directory / "data-validation.json").read_text("utf-8"))
            self.assertEqual(v5.leakage_issues(rows), report["issues"])
        finally:
            self.assertTrue(directory.is_relative_to(v5.WORK_ROOT.resolve()))
            self.assertTrue(directory.name.startswith("unit-v5-oracle-"))
            temporary.cleanup()


class ForbiddenAnnotationTests(unittest.TestCase):
    def test_source_grounded_forbidden_sense_is_accepted(self):
        row = {**sample("sense", "Her interest in painting grew."), "target": "그녀의 그림에 대한 관심이 커졌다.",
               "forbiddenTerms": [{"source": "interest", "target": "이자", "reason": "The source describes curiosity, not borrowing."}]}
        self.assertEqual(v5.forbidden_targets(row), ["이자"])

    def test_ungrounded_or_self_contradicting_annotation_is_rejected(self):
        row = {**sample("sense", "Her interest in painting grew."), "target": "그녀의 관심이 커졌다."}
        for forbidden in (["이자"], [{"target": "이자", "reason": " "}],
                          [{"target": "관심", "reason": "Reference cannot contain forbidden output."}],
                          [{"source": "absent word", "target": "이자", "reason": "Missing source anchor."}]):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValueError):
                v5.forbidden_targets({**row, "forbiddenTerms": forbidden})


def score(term_accuracy=0.9, general=False):
    return {"generationProtocol": {"effectivePrecision": "fp32", "numBeams": 4}, "count": 10,
            "chrF": 50.0, "bleu": 30.0, "numericPreservation": 1.0, "termAccuracy": None if general else term_accuracy,
            "termCount": 0 if general else 10, "emptyOutputs": 0, "cappedOutputs": 0,
            "forbiddenTermRows": 2 if general else 0, "forbiddenTermCount": 2 if general else 0,
            "forbiddenTermHits": 0}


def aggregate(term_accuracy=0.9):
    value = score(term_accuracy)
    value["byDomain"] = {"finance": score(term_accuracy), "general": score(general=True)}
    return value


class GateTests(unittest.TestCase):
    def test_absolute_ninety_and_parent_floor_replace_old_relative_gain(self):
        for before, after, passes in ((0.89, 0.90, True), (0.2, 0.899, False),
                                      (0.95, 0.94, False), (0.95, 0.95, True), (0.99, 1.0, True)):
            with self.subTest(before=before, after=after):
                self.assertEqual(v5.gate(aggregate(before), aggregate(after))["passed"], passes)

    def test_each_domain_retains_numbers_quality_empty_and_capped_guards(self):
        for domain in ("finance", "general"):
            for field, bad in (("chrF", 48.9), ("bleu", 28.9), ("numericPreservation", 0.99),
                               ("emptyOutputs", 1), ("cappedOutputs", 1), ("forbiddenTermHits", 1)):
                candidate = aggregate()
                candidate["byDomain"][domain][field] = bad
                with self.subTest(domain=domain, field=field):
                    self.assertFalse(v5.gate(aggregate(), candidate)["passed"])

    def test_missing_general_annotation_and_protocol_mix_are_rejected(self):
        candidate = aggregate()
        candidate["byDomain"]["general"]["forbiddenTermRows"] = 0
        self.assertFalse(v5.gate(aggregate(), candidate)["passed"])
        candidate = aggregate()
        candidate["generationProtocol"] = {"effectivePrecision": "bf16"}
        self.assertFalse(v5.gate(aggregate(), candidate)["passed"])
        candidate = aggregate()
        del candidate["byDomain"]["general"]
        self.assertFalse(v5.gate(aggregate(), candidate)["passed"])

    def test_invalid_nonfinite_scores_cannot_pass(self):
        for field in ("chrF", "bleu", "numericPreservation", "termAccuracy"):
            for bad in (float("nan"), float("inf"), None, True):
                candidate = score()
                candidate[field] = bad
                with self.subTest(field=field, bad=bad):
                    self.assertFalse(v5.gate(score(), candidate, "finance")["passed"])

    def test_actual_metric_domain_recursion_counts_forbidden_sense_separately(self):
        rows = [{**sample("finance", "A bond pays interest."), "target": "A bond pays interest.",
                 "terms": [{"source": "bond", "target": "bond"}]},
                {**sample("general", "A family bond endures."), "domain": "general", "target": "A family bond endures.",
                 "forbiddenTerms": [{"target": "debenture", "reason": "This is an interpersonal relationship."}]}]
        predictions = [{"prediction": rows[0]["target"], "atLengthLimit": False, "generationProtocol": {}},
                       {"prediction": "A family debenture endures.", "atLengthLimit": False, "generationProtocol": {}}]
        result = v5.metrics(rows, predictions)
        self.assertEqual(result["byDomain"]["finance"]["termAccuracy"], 1.0)
        self.assertEqual(result["byDomain"]["general"]["forbiddenTermHits"], 1)
        self.assertEqual(result["forbiddenTermHits"], 1)
        self.assertEqual(result["forbiddenTermCount"], 1)
        with self.assertRaisesRegex(ValueError, "complete"):
            v5.metrics(rows, predictions[:1])


class LocalFixture(unittest.TestCase):
    def setUp(self):
        self.original_root = v5.WORK_ROOT.resolve()
        self.temporary = tempfile.TemporaryDirectory(prefix="unit-v5-integration-", dir=self.original_root)
        self.directory = Path(self.temporary.name).resolve()
        self.patches = [patch.object(v5, "WORK_ROOT", self.directory),
                        patch.object(v5.engine, "WORK_ROOT", self.directory), patch.object(v5.engine, "emit")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.assertTrue(self.directory.is_relative_to(self.original_root))
        self.assertTrue(self.directory.name.startswith("unit-v5-integration-"))
        self.temporary.cleanup()


class IdentityTests(LocalFixture):
    def test_private_hooks_leave_an_independently_imported_frozen_trainer_unchanged(self):
        spec = importlib.util.spec_from_file_location("independent_original", v5.DEPENDENCY_PATH)
        original = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(original)
        self.assertEqual(original.BASE_WEIGHT_HASH, v5.ORIGINAL_WEIGHT_SHA256)
        self.assertEqual(v5.engine.BASE_WEIGHT_HASH, v5.PARENT_WEIGHT_SHA256)
        self.assertIs(v5.engine.metrics, v5.metrics)
        self.assertIs(v5.engine.gate, v5.gate)
        self.assertNotEqual(original.GATE, v5.POLICY)
        self.assertEqual(v5.engine.sha256(v5.DEPENDENCY_PATH), v5.DEPENDENCY_SHA256)

    def test_initial_six_epoch_identity_resumes_only_unchanged(self):
        args = SimpleNamespace(run_id="synthetic-v5", device="xpu", seed=1, threads=4, precision="fp32", epochs=6,
                               batch_size=1, accumulation=4, learning_rate=1e-5, warmup_updates=5,
                               max_length=384, max_new_tokens=384, beams=4, checkpoint_every=50, max_updates=0)
        parent = self.directory / "parent-model"
        parent.mkdir()
        files = {name: "fixture-hash-" + name for name in v5.MODEL_FILES}
        lineage = {"originalBase": {"model": v5.ORIGINAL_MODEL_ID, "revision": v5.ORIGINAL_REVISION,
                                   "weightSha256": v5.ORIGINAL_WEIGHT_SHA256}, "baselineRole": "fixture warm start",
                   "parentModelFiles": files, "parentSelectedStep": 218}
        data = {"files": {split: {"path": "fixture/" + split, "sha256": split + "-hash"} for split in ("train", "dev", "test")},
                "publication": {"manifestSha256": "synthetic-publication-only"}}
        with patch.object(v5, "validate_data", return_value=({}, data)), \
                patch.object(v5, "verify_parent", return_value=(parent, files, lineage)):
            *_, manifest = v5.setup(args)
            self.assertEqual(manifest["config"]["epochs"], 6)
            self.assertEqual(manifest["dependencyFiles"][v5.engine.relative(v5.DEPENDENCY_PATH)], v5.DEPENDENCY_SHA256)
            self.assertEqual(v5.setup(args)[3]["identitySha256"], manifest["identitySha256"])
            args.epochs = 3
            with self.assertRaisesRegex(ValueError, "identity/config/data/parent"):
                v5.setup(args)
            args.epochs = 6
            data["files"]["train"]["sha256"] = "changed"
            with self.assertRaisesRegex(ValueError, "identity/config/data/parent"):
                v5.setup(args)

    def test_parent_lineage_and_inventory_reject_modified_copy(self):
        parent = self.directory / "runs" / v5.PARENT_RUN_ID
        model, selected = parent / "model", parent / "checkpoint-218"
        model.mkdir(parents=True)
        (selected / "model").mkdir(parents=True)
        for name in v5.MODEL_FILES:
            (model / name).write_bytes(("synthetic " + name).encode())
            (selected / "model" / name).write_bytes(("synthetic " + name).encode())
        weight = v5.engine.sha256(model / "model.safetensors")
        v5.engine.write_json(parent / "manifest.json", {"runId": v5.PARENT_RUN_ID, "baseModel": v5.ORIGINAL_MODEL_ID,
            "baseRevision": v5.ORIGINAL_REVISION, "baseFiles": {"model.safetensors": v5.ORIGINAL_WEIGHT_SHA256},
            "selectedCheckpoint": v5.engine.relative(selected)})
        v5.engine.write_json(parent / "training-summary.json", {"runId": v5.PARENT_RUN_ID,
            "baseWeightFileSha256": v5.ORIGINAL_WEIGHT_SHA256, "trainedWeightFileSha256": weight,
            "selectedStep": 218, "completedUpdates": 327, "weightEvidence": {"changedTensorCount": 1},
            "modelPath": v5.engine.relative(model), "selectedDev": {"generationProtocol": {"effectivePrecision": "fp32"}}})
        v5.engine.write_json(selected / "checkpoint.json", {"step": 218, "modelSha256": weight})
        with patch.object(v5, "PARENT_WEIGHT_SHA256", weight), patch.object(v5.engine, "validate_tokenizer_files", return_value={}) as tokenizer:
            path, files, lineage = v5.verify_parent()
            self.assertEqual(path, model)
            self.assertEqual(files["model.safetensors"], weight)
            self.assertFalse(lineage["oldFinalTestRead"])
            tokenizer.assert_called_once_with(model)
            (model / "tokenizer_config.json").write_text("modified", "utf-8")
            with self.assertRaisesRegex(ValueError, "model/tokenizer copy changed"):
                v5.verify_parent()


class DataContractTests(LocalFixture):
    def make_data(self):
        publication = patch.object(v5.dataset_v5, 'verify_published_dataset',
                                   return_value={'manifestSha256': 'synthetic-reviewed-publication'})
        publication.start()
        self.addCleanup(publication.stop)
        data_root = self.directory / "datasets"
        data_root.mkdir()
        sources = {"train": ("A bond provides periodic interest.", "Her interest in acting grew."),
                   "dev": ("Dividend payments reward shareholders.", "Paris is the capital of France."),
                   "test": ("Revenue records the value of sales.", "Equity means fairness in this debate.")}
        rows, paths = {}, {}
        for split, (finance, general) in sources.items():
            term = finance.split()[1] if split == "train" else finance.split()[0]
            rows[split] = [{**sample(split + "-finance", finance, split), "terms": [{"source": term, "target": term}]},
                           {**sample(split + "-general", general, split), "domain": "general",
                            "forbiddenTerms": [{"target": "unrelated-financial-sense", "reason": "Synthetic forbidden sense for fixture."}]}]
            path = data_root / (split + ".jsonl")
            path.write_text("".join(json.dumps(row) + "\n" for row in rows[split]), "utf-8")
            paths[split + "_data"] = v5.engine.relative(path)
        return data_root, rows, SimpleNamespace(**paths)

    def test_missing_publication_rejected_before_any_row_read(self):
        paths = {split + '_data': v5.engine.relative(self.directory / (split + '.jsonl'))
                 for split in ('train', 'dev', 'test')}
        with patch.object(v5, 'DATA_ROOT', self.directory), patch.object(v5.engine, 'read_rows') as reader:
            with self.assertRaisesRegex(ValueError, 'dataset-manifest.json is required'):
                v5.validate_data(SimpleNamespace(**paths), self.directory)
            reader.assert_not_called()

    def test_fresh_split_validation_checks_all_metadata_without_old_data(self):
        data_root, expected, args = self.make_data()
        with patch.object(v5, "DATA_ROOT", data_root):
            rows, report = v5.validate_data(args, self.directory)
            self.assertEqual(rows, expected)
            self.assertTrue(report["passed"])
            self.assertEqual(report["counts"], {"train": 2, "dev": 2, "test": 2})
            self.assertIn("fullRatioCalls", report["optimizedComparisonStatistics"])

    def test_old_or_outside_corpus_path_is_rejected_before_read(self):
        data_root, _, args = self.make_data()
        args.test_data = "content/training/test.jsonl"
        with patch.object(v5, "DATA_ROOT", data_root), patch.object(v5.engine, "read_rows") as reader:
            with self.assertRaisesRegex(ValueError, "required project directory"):
                v5.validate_data(args, self.directory)
            reader.assert_not_called()

    def test_missing_target_annotation_and_missing_general_sense_coverage_fail(self):
        data_root, rows, args = self.make_data()
        with patch.object(v5, "DATA_ROOT", data_root):
            rows["dev"][0]["terms"][0]["target"] = "not-in-reference"
            (data_root / "dev.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows["dev"]), "utf-8")
            with self.assertRaisesRegex(ValueError, "reference does not contain"):
                v5.validate_data(args, self.directory)
            rows["dev"][0]["terms"][0]["target"] = "Dividend"
            rows["dev"][1]["forbiddenTerms"] = []
            (data_root / "dev.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows["dev"]), "utf-8")
            with self.assertRaisesRegex(ValueError, "source-grounded general"):
                v5.validate_data(args, self.directory)


class FinalEvaluationTests(LocalFixture):
    def build(self):
        run = self.directory / "runs" / "synthetic-v5"
        model, parent = run / "model", self.directory / "parent"
        model.mkdir(parents=True)
        parent.mkdir()
        (model / "model.safetensors").write_bytes(b"updated synthetic weight")
        selected = run / 'checkpoints' / 'step-000001'
        (selected / 'model').mkdir(parents=True)
        for name in v5.MODEL_FILES:
            if name != 'model.safetensors':
                (model / name).write_bytes(('synthetic-' + name).encode())
            (selected / 'model' / name).write_bytes((model / name).read_bytes())
        args = SimpleNamespace(run_id="synthetic-v5", max_length=384, max_new_tokens=384, beams=4, precision="fp32")
        summary = {"runId": args.run_id, "modelPath": v5.engine.relative(model), "devGate": {"passed": True},
                   "trainedWeightFileSha256": v5.engine.sha256(model / "model.safetensors"),
                   "weightEvidence": {"changedTensorCount": 1}, 'selectedStep': 1}
        v5.engine.write_json(run / "training-summary.json", summary)
        manifest = {"status": "trained", "runId": args.run_id, "datasets": {"test": {"sha256": "synthetic-test-hash"}},
                    'selectedCheckpoint': v5.engine.relative(selected)}
        v5.engine.write_json(selected / 'checkpoint.json', {'step': 1, 'modelSha256': summary['trainedWeightFileSha256']})
        inventory = v5.model_inventory(run, manifest, summary)
        stamp = {'modelInventory': inventory, 'modelInventorySha256': v5.canonical_hash(inventory)}
        summary.update(stamp)
        manifest.update(stamp)
        v5.engine.write_json(run / 'training-summary.json', summary)
        rows = {"test": [sample("only-fixture", "This temporary fixture is never training data.", "test")]}
        return args, run, parent, rows, manifest

    def test_failing_dev_does_not_consume_final_test(self):
        args, run, parent, rows, manifest = self.build()
        summary = v5.read_json(run / "training-summary.json")
        summary["devGate"]["passed"] = False
        v5.engine.write_json(run / "training-summary.json", summary)
        with patch.object(v5.engine, "evaluate") as evaluate:
            with self.assertRaisesRegex(ValueError, "passing selected dev"):
                v5.evaluate(args, run, parent, rows, manifest)
            evaluate.assert_not_called()
        self.assertFalse(v5.engine.test_ledger(manifest).exists())

    def test_frozen_evaluation_uses_parent_baseline_and_same_consumption_ledger(self):
        args, run, parent, rows, manifest = self.build()
        tokenizer = SimpleNamespace(from_pretrained=Mock(return_value=object()))
        with patch.object(v5.engine, "stack", return_value=(object(), object(), tokenizer, "cpu", {})), \
                patch.object(v5.engine, "encode_rows"), patch.object(v5.engine, "load_model", return_value=object()), \
                patch.object(v5.engine, "predict", return_value=[]) as predict, \
                patch.object(v5.engine, "metrics", return_value=aggregate()):
            v5.evaluate(args, run, parent, rows, manifest)
            self.assertEqual([call.args[-1] for call in predict.call_args_list],
                             [v5.PARENT_WEIGHT_SHA256, v5.engine.sha256(run / "model/model.safetensors")])
            result = v5.read_json(run / "evaluation-summary.json")
            self.assertEqual(result["baseWeightFileSha256"], v5.PARENT_WEIGHT_SHA256)
            self.assertTrue(result["promotionEligible"])
            self.assertEqual(result["gatePolicy"], v5.POLICY)
            self.assertEqual(v5.read_json(v5.engine.test_ledger(manifest))["status"], "complete")
            v5.evaluate(args, run, parent, rows, manifest)
            self.assertEqual(predict.call_count, 2)
        with self.assertRaisesRegex(ValueError, "no further training"):
            v5.engine.train(args, run, parent, rows, manifest)

    def test_conflicting_run_cannot_consume_same_test(self):
        args, run, parent, rows, manifest = self.build()
        ledger = v5.engine.test_ledger(manifest)
        v5.engine.write_json(ledger, {"runId": "other-run", "testDataSha256": "synthetic-test-hash", "modelSha256": "other"})
        with patch.object(v5.engine, "stack") as stack:
            with self.assertRaisesRegex(ValueError, "already been consumed"):
                v5.evaluate(args, run, parent, rows, manifest)
            stack.assert_not_called()

    def test_interrupted_evaluation_resumes_with_same_identity(self):
        args, run, parent, rows, manifest = self.build()
        tokenizer = SimpleNamespace(from_pretrained=Mock(return_value=object()))
        with patch.object(v5.engine, "stack", return_value=(object(), object(), tokenizer, "cpu", {})), \
                patch.object(v5.engine, "encode_rows"), patch.object(v5.engine, "load_model", return_value=object()), \
                patch.object(v5.engine, "predict", side_effect=[[], v5.engine.StopRequested(), [], []]), \
                patch.object(v5.engine, "metrics", return_value=aggregate()):
            v5.evaluate(args, run, parent, rows, manifest)
            self.assertEqual(manifest["status"], "evaluation-paused")
            self.assertFalse((run / "evaluation-summary.json").exists())
            v5.evaluate(args, run, parent, rows, manifest)
            self.assertEqual(manifest["status"], "evaluated")


class FinalInventoryTests(LocalFixture):
    def test_successful_finalization_seals_every_file_once(self):
        args,run,parent,rows,manifest=FinalEvaluationTests.build(self)
        summary=v5.read_json(run/'training-summary.json')
        for value in (summary,manifest):
            value.pop('modelInventory')
            value.pop('modelInventorySha256')
        (run/'training-summary.json').unlink()
        manifest['status']='training'
        manifest['devHistory']=[{'step':1,'checkpoint':manifest['selectedCheckpoint']}]
        def complete(*_):
            v5.engine.write_json(run/'training-summary.json',summary)
            manifest['status']='trained'
            v5.engine.write_json(run/'manifest.json',manifest)
        writer=v5.engine.write_json
        with patch.object(v5.engine,'train',side_effect=complete):
            v5.train(args,run,parent,rows,manifest)
        self.assertIs(v5.engine.write_json,writer)
        inventory=v5.verify_model_inventory(run,manifest,v5.read_json(run/'training-summary.json'))
        self.assertEqual(set(inventory['files']),set(v5.MODEL_FILES))
        selected=v5.engine.local_path(manifest['selectedCheckpoint'],run)
        for folder in (run/'model',selected/'model'):
            (folder/'config.json').write_bytes(b'changed together')
        with patch.object(v5.engine,'train') as trainer:
            with self.assertRaisesRegex(ValueError,'runtime inventory'):
                v5.train(args,run,parent,rows,manifest)
            trainer.assert_not_called()

    def test_resume_uses_existing_seal_and_never_invents_missing_hashes(self):
        args,run,parent,rows,manifest=FinalEvaluationTests.build(self)
        summary=v5.read_json(run/'training-summary.json')
        summary['completedUpdates']=1
        v5.engine.write_json(run/'training-summary.json',summary)
        inventory=manifest.pop('modelInventory')
        manifest.pop('modelInventorySha256')
        manifest['status']='training'
        with patch.object(v5.engine,'train'):
            v5.train(args,run,parent,rows,manifest)
        self.assertEqual(manifest['modelInventory'],inventory)
        self.assertEqual(manifest['status'],'trained')
        summary.pop('modelInventory')
        summary.pop('modelInventorySha256')
        manifest.pop('modelInventory')
        manifest.pop('modelInventorySha256')
        v5.engine.write_json(run/'training-summary.json',summary)
        with patch.object(v5.engine,'train') as trainer:
            with self.assertRaisesRegex(ValueError,'runtime inventory'):
                v5.train(args,run,parent,rows,manifest)
            trainer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
