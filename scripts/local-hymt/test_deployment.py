"""Small synthetic deployment/registration fixtures; no real model or review IO."""
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import deployment as dep
import register


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hymt-deployment-fixture-")
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.model = b"small synthetic GGUF stand-in; never loaded"
        self.catalog = dep.canonical({"version": "fixture", "terms": [
            {"id": f"T{i}", "source": f"term {i}", "target": f"target {i}", "definition": "fixture definition", "aliases": []}
            for i in range(54)]})
        self.execution = {"revision": dep.REVISION, "templateSha256": dep.TEMPLATE_SHA256,
                          "runtimeOverrides": copy.deepcopy(dep.OVERRIDES), "sampling": copy.deepcopy(dep.SAMPLING),
                          "contextSize": 8192, "threads": 4, "cpuOnly": True,
                          "contextPolicyVersion": "existing-title-neighbors-v1", "pythonVersion": "fixture", "jinjaVersion": "fixture"}
        for name, value in [("MODEL_SIZE", len(self.model)), ("MODEL_SHA256", dep.sha(self.model)), ("CATALOG_SHA256", dep.sha(self.catalog))]:
            self.stack.enter_context(patch.object(dep, name, value))
        self.stack.enter_context(patch.object(dep, "execution_contract", return_value=self.execution))
        self.revalidate = self.stack.enter_context(patch.object(register, "_revalidate_reviews"))
        self.write(f"{dep.MODEL_DIR}/{dep.MODEL_FILE}", self.model)
        self.write(dep.CATALOG_SOURCE, self.catalog)
        self.write(dep.PYTHON_PATH, b"fixture python metadata only")
        self.write(dep.JINJA_PATH + "/__init__.py", b"fixture Jinja initializer")
        self.write(dep.JINJA_PATH + "/environment.py", b"fixture Jinja environment")
        for name in dep.CODE_PATHS:
            self.write(name, ("fixture code " + name).encode())
        self.runtime = []
        for index in range(52):
            name = "llama-server.exe" if index == 0 else f"fixture-{index:02d}.dll"
            relative = f"{dep.MODEL_DIR}/runtime/{name}"
            self.write(relative, name.encode())
            self.runtime.append(dep.file_entry(self.root, relative))
        self.runtime.sort(key=lambda entry: entry["path"])
        self.stack.enter_context(patch.object(dep, "runtime_inventory", side_effect=lambda root, tracker: copy.deepcopy(self.runtime)))
        self.base = ".training/comparisons/fixture-quality"
        self.prepared = self.base + "/prepared"
        self.reviews_name = self.base + "/reviews-summary.json"
        self.selection_name = self.base + "/selection.json"
        self.source_name = self.base + "/source-fixture.jsonl"
        self.review_source_name = self.base + "/review-fixture.jsonl"
        self.write(self.source_name, b"synthetic source input; never actual data")
        self.write(self.review_source_name, b"synthetic reviewed input; revalidation mocked")
        self.ids = [f"Q26-{index:03d}" for index in range(1, 25)]
        identity = {"systems": {name: {} for name in dep.SYSTEMS}, "datasetSha256": dep.sha(b"fixture dataset"),
                    "inputFiles": {str(self.root / name): dep.sha((self.root / name).read_bytes())
                                   for name in [dep.PYTHON_PATH, dep.JINJA_PATH + "/__init__.py",
                                                dep.JINJA_PATH + "/environment.py", self.source_name]}}
        self.metrics = {"version": dep.PREPARED_VERSION, "rowCount": 24, "humanReviewed": False, "qualityGateApplied": False,
                        "inputIdentitySha256": dep.prepared_identity_sha(identity), "datasetSha256": identity["datasetSha256"],
                        "systems": {name: {"count": 24, "rows": [
                            {"id": rid, "sourceSha256": dep.sha(rid.encode()), "translationSha256": dep.sha((name + rid).encode())}
                            for rid in self.ids]} for name in dep.SYSTEMS}}
        metric_raw = dep.canonical(self.metrics)
        self.preparation = {"version": dep.PREPARED_VERSION, "status": "complete", "rowCount": 24, "humanReviewed": False,
                            "qualityGateApplied": False, "inferencePerformed": False, "identity": identity,
                            "identitySha256": dep.prepared_identity_sha(identity), "files": {
                                "comparison-metrics.json": dep.sha(metric_raw), "anonymous-review.jsonl": dep.sha(b"fixture anonymous"),
                                "review-key.json": dep.sha(b"fixture key")}}
        self.write(self.prepared + "/comparison-metrics.json", metric_raw)
        self.write(self.prepared + "/manifest.json", dep.canonical(self.preparation))
        judgment = {"severity": 3, "evidence": "Synthetic source-grounded fixture explanation.",
                    "meaningPreserved": False, "negationAndConditionsPreserved": False,
                    "contextualWordSensePreserved": False, "omission": True, "unsupportedAddition": True,
                    "quantityOrFormulaError": True, "fluency": False}
        self.review = {"version": dep.REVIEW_VERSION, "completed": True, "reviewerType": "assistant", "humanReviewed": False,
                       "rowCount": 24, "judgedChoices": 96, "unresolvedJudgments": 0, "automaticGateCreated": False,
                       "promotionDecision": None, "inferencePerformed": False, "humanGoldReference": False,
                       "preparedManifestSha256": dep.sha(dep.canonical(self.preparation)),
                       "preparedIdentitySha256": self.preparation["identitySha256"],
                       "anonymousSha256": self.preparation["files"]["anonymous-review.jsonl"],
                       "keySha256": self.preparation["files"]["review-key.json"],
                       "systems": {name: {"judgedChoices": 24} for name in dep.SYSTEMS},
                       "reviews": [{"path": str(self.root / self.review_source_name), "rowCount": 24,
                                    "sha256": dep.sha((self.root / self.review_source_name).read_bytes())}],
                       "evidence": [{"id": rid, "system": name, "sourceSha256": dep.sha(rid.encode()),
                                     "translationSha256": dep.sha((name + rid).encode()), "review": copy.deepcopy(judgment)}
                                    for name in dep.SYSTEMS for rid in self.ids]}
        self.write(self.reviews_name, dep.canonical(self.review))
        self.selection = {"selectedSystem": "hymt-contextual", "preparedManifestSha256": dep.sha(dep.canonical(self.preparation)),
                          "assistantReviewSummarySha256": dep.sha(dep.canonical(self.review)),
                          "rationale": "Explicit fixture choice; no automatic score gate.", "humanReviewed": False}
        self.write(self.selection_name, dep.canonical(self.selection))

    def cleanup(self):
        root = Path(tempfile.gettempdir()).resolve()
        assert self.root.parent == root and self.root.name.startswith("hymt-deployment-fixture-")
        assert Path(self.temp.name).resolve() == self.root
        self.temp.cleanup()

    def write(self, relative, raw):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def activate(self, registration_id="fixture-one"):
        return register.register("contextual", self.prepared, self.reviews_name, self.selection_name, registration_id, self.root)

    def change_manifest(self, mutate):
        active = self.root / dep.ACTIVE_PATH
        value = dep.parse(active.read_bytes())
        mutate(value)
        raw = dep.canonical(value) + b"\n"
        active.write_bytes(raw)
        (self.root / f".translation/hymt/registrations/{value['registrationId']}/manifest.json").write_bytes(raw)

    def test_register_and_verify_full_identity_without_score_gate_or_model_copy(self):
        active = self.activate()
        bundle = dep.verify_manifest(self.root)
        self.assertEqual(bundle.identity, f"hymt:{dep.MODEL_SHA256}:{dep.sha(active.read_bytes())}")
        self.assertEqual(len(bundle.terms), 54)
        self.assertEqual(bundle.manifest["profile"], "contextual")
        self.assertEqual(bundle.manifest["modelFiles"][0]["path"], f"{dep.MODEL_DIR}/{dep.MODEL_FILE}")
        self.assertEqual(len(bundle.manifest["runtimeFiles"]), 52)
        self.assertEqual({item["path"] for item in bundle.manifest["codeFiles"]}, set(dep.CODE_PATHS))
        registration = self.root / ".translation/hymt/registrations/fixture-one"
        self.assertEqual({path.name for path in registration.iterdir()}, {"manifest.json", "catalog.json"})
        self.assertEqual(active.read_bytes(), (registration / "manifest.json").read_bytes())
        self.assertFalse((self.root / ".env.local").exists())
        self.assertFalse((self.root / "data").exists())
        self.revalidate.assert_called_once()
        bundle.assert_unchanged()

    def test_second_registration_preserves_old_immutable_bytes(self):
        self.activate()
        old = self.root / ".translation/hymt/registrations/fixture-one/manifest.json"
        raw = old.read_bytes()
        self.activate("fixture-two")
        self.assertEqual(old.read_bytes(), raw)
        self.assertEqual(dep.verify_manifest(self.root).manifest["registrationId"], "fixture-two")
        with self.assertRaises(dep.DeploymentError):
            self.activate("fixture-two")

    def test_unicode_prepared_identity_matches_producer_contract(self):
        identity = self.preparation["identity"]
        identity.update(inputPath="C:/사용자/금융 학습/자료.jsonl", metadata={"설명": "문맥과 의미 검토"})
        # This is the producer's UTF-8 canonical form, distinct from the runtime
        # contract's ASCII-escaped canonical form.
        identity_sha = dep.sha(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":"), allow_nan=False).encode("utf-8"))
        self.assertNotEqual(identity_sha, dep.sha(dep.canonical(identity)))
        self.metrics["inputIdentitySha256"] = identity_sha
        metrics_raw = dep.canonical(self.metrics)
        self.preparation["identitySha256"] = identity_sha
        self.preparation["files"]["comparison-metrics.json"] = dep.sha(metrics_raw)
        prepared_raw = dep.canonical(self.preparation)
        self.review.update(preparedIdentitySha256=identity_sha, preparedManifestSha256=dep.sha(prepared_raw))
        review_raw = dep.canonical(self.review)
        self.selection.update(preparedManifestSha256=dep.sha(prepared_raw), assistantReviewSummarySha256=dep.sha(review_raw))
        self.write(self.prepared + "/comparison-metrics.json", metrics_raw)
        self.write(self.prepared + "/manifest.json", prepared_raw)
        self.write(self.reviews_name, review_raw)
        self.write(self.selection_name, dep.canonical(self.selection))
        self.activate()
        bundle = dep.verify_manifest(self.root)
        self.assertEqual(bundle.manifest["execution"], self.execution)
        self.assertEqual(bundle.manifest["runtimeVersion"], "hymt-local-v1:" + dep.sha(dep.canonical(self.execution)))
        bundle.assert_unchanged()

    def test_explicit_choice_and_completed_review_are_required(self):
        for field, value in [("selectedSystem", "hymt-raw"), ("rationale", "  "), ("humanReviewed", True),
                             ("preparedManifestSha256", "0" * 64), ("assistantReviewSummarySha256", None)]:
            with self.subTest(field=field):
                changed = self.selection | {field: value}
                self.write(self.selection_name, dep.canonical(changed))
                with self.assertRaises(dep.DeploymentError):
                    self.activate()
        self.assertFalse((self.root / dep.ACTIVE_PATH).exists())
        self.revalidate.assert_not_called()

    def test_duplicate_and_unresolved_review_choices_rejected(self):
        for mutate in [lambda rows: rows.__setitem__(0, copy.deepcopy(rows[1])),
                       lambda rows: rows[0]["review"].__setitem__("fluency", None),
                       lambda rows: rows[0].__setitem__("translationSha256", "0" * 64)]:
            review = copy.deepcopy(self.review)
            mutate(review["evidence"])
            raw = dep.canonical(review)
            self.write(self.reviews_name, raw)
            self.write(self.selection_name, dep.canonical(self.selection | {"assistantReviewSummarySha256": dep.sha(raw)}))
            with self.assertRaises(dep.DeploymentError):
                self.activate()

    def test_revalidation_failure_does_not_publish(self):
        self.revalidate.side_effect = ValueError("fixture validation failure")
        with self.assertRaises(ValueError):
            self.activate()
        self.assertFalse((self.root / dep.ACTIVE_PATH).exists())
        self.assertFalse((self.root / ".translation/hymt/registrations/fixture-one").exists())

    def test_every_manifest_file_category_rejects_changed_bytes(self):
        self.activate()
        manifest = dep.verify_manifest(self.root).manifest
        for entry in [manifest["modelFiles"][0], manifest["runtimeFiles"][0], manifest["codeFiles"][0],
                      manifest["evidenceFiles"][0], manifest["catalog"]]:
            with self.subTest(path=entry["path"]):
                path = self.root / entry["path"]
                raw = path.read_bytes()
                path.write_bytes(raw + b"changed")
                with self.assertRaises(dep.DeploymentError):
                    dep.verify_manifest(self.root)
                path.write_bytes(raw)

    def test_assert_unchanged_detects_active_code_and_new_runtime_file(self):
        self.activate()
        for relative in [dep.ACTIVE_PATH, dep.CODE_PATHS[0], dep.PYTHON_PATH]:
            bundle = dep.verify_manifest(self.root)
            path = self.root / relative
            raw = path.read_bytes()
            path.write_bytes(raw + b" ")
            with self.assertRaises(dep.DeploymentError):
                bundle.assert_unchanged()
            path.write_bytes(raw)
        bundle = dep.verify_manifest(self.root)
        self.write(f"{dep.MODEL_DIR}/runtime/unregistered.dll", b"extra")
        with self.assertRaises(dep.DeploymentError):
            bundle.assert_unchanged()

    def test_assert_unchanged_does_not_rehash_model_or_runtime(self):
        self.activate()
        bundle = dep.verify_manifest(self.root)
        active_raw = (self.root / dep.ACTIVE_PATH).read_bytes()
        with patch.object(Path, "read_bytes", lambda path: active_raw if path == self.root / dep.ACTIVE_PATH else self.fail("Unexpected content read")), \
                patch.object(Path, "open", side_effect=AssertionError("No model rehash after verification")):
            bundle.assert_unchanged()

    def test_fresh_verification_rejects_changed_python_jinja_and_added_python_source(self):
        self.activate()
        for relative in [dep.PYTHON_PATH, dep.JINJA_PATH + "/environment.py"]:
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_bytes()
                path.write_bytes(original + b" changed without changing version")
                with self.assertRaisesRegex(dep.DeploymentError, "file_hash_changed"):
                    dep.verify_manifest(self.root)
                path.write_bytes(original)
        added = self.write(dep.JINJA_PATH + "/new_module.py", b"unrecorded source")
        with self.assertRaisesRegex(dep.DeploymentError, "inventory_changed"):
            dep.verify_manifest(self.root)
        added.unlink()
        bundle = dep.verify_manifest(self.root)
        self.write(dep.JINJA_PATH + "/nested/late_module.py", b"added during use")
        with self.assertRaisesRegex(dep.DeploymentError, "inventory_changed"):
            bundle.assert_unchanged()

    def test_registration_tracks_source_and_review_files_after_revalidation_until_publication(self):
        active = self.activate()
        original_active = active.read_bytes()
        for index, relative in enumerate([self.source_name, self.review_source_name]):
            with self.subTest(relative=relative):
                source = self.root / relative
                original = source.read_bytes()
                self.revalidate.reset_mock()
                def mutate_after_review(root, tracker):
                    self.revalidate.assert_called_once()
                    source.write_bytes(original + b" changed during model verification")
                    return copy.deepcopy(self.runtime)
                dep.runtime_inventory.side_effect = mutate_after_review
                with self.assertRaisesRegex(dep.DeploymentError, "fingerprint_changed"):
                    self.activate(f"interrupted-{index}")
                self.assertEqual(active.read_bytes(), original_active)
                self.assertFalse((self.root / f".translation/hymt/registrations/interrupted-{index}").exists())
                source.write_bytes(original)

    def test_python_dependency_paths_reject_missing_expected_extra_and_outside_records(self):
        original = self.preparation["identity"]["inputFiles"]
        python = str(self.root / dep.PYTHON_PATH)
        environment = str(self.root / dep.JINJA_PATH / "environment.py")
        mutations = [
            lambda values: values.pop(python),
            lambda values: values.pop(environment),
            lambda values: values.__setitem__(python, None),
            lambda values: values.__setitem__(str(self.root.parent / "outside.py"), "0" * 64),
            lambda values: values.__setitem__(str(self.root / dep.JINJA_PATH / "../other.py"), "0" * 64),
            lambda values: values.__setitem__(python.upper(), values[python]),
        ]
        for mutate in mutations:
            prepared = copy.deepcopy(self.preparation)
            values = prepared["identity"]["inputFiles"]
            mutate(values)
            with self.assertRaises(dep.DeploymentError):
                dep.track_python_runtime(dep.FileTracker(self.root), prepared)
        self.assertEqual(self.preparation["identity"]["inputFiles"], original)

    def test_python_dependency_verification_does_not_follow_other_prepared_paths(self):
        prepared = copy.deepcopy(self.preparation)
        prepared["identity"]["inputFiles"][str(self.root / "unrelated-unopened.bin")] = "0" * 64
        # A fresh runtime needs only the exact sealed Python/Jinja dependency
        # paths; metadata cannot make it open arbitrary unrelated file bytes.
        tracker = dep.FileTracker(self.root)
        dep.track_python_runtime(tracker, prepared)
        self.assertNotIn("unrelated-unopened.bin", tracker.hashes)

    def test_runtime_version_profile_and_duplicate_inventory_rejected(self):
        self.activate()
        active = self.root / dep.ACTIVE_PATH
        pristine = active.read_bytes()
        mutations = [lambda m: m.__setitem__("runtimeVersion", "hymt-local-v1:" + "0" * 64),
                     lambda m: m.__setitem__("profile", "raw"),
                     lambda m: m["codeFiles"].__setitem__(0, m["codeFiles"][1]),
                     lambda m: m["execution"].__setitem__("cpuOnly", False),
                     lambda m: m["execution"]["sampling"].__setitem__("temperature", 0.8)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.change_manifest(mutate)
                with self.assertRaises(dep.DeploymentError):
                    dep.verify_manifest(self.root)
                active.write_bytes(pristine)
                (self.root / ".translation/hymt/registrations/fixture-one/manifest.json").write_bytes(pristine)

    def test_immutable_copy_and_strict_json_required(self):
        self.activate()
        immutable = self.root / ".translation/hymt/registrations/fixture-one/manifest.json"
        immutable.write_bytes(immutable.read_bytes() + b" ")
        with self.assertRaises(dep.DeploymentError):
            dep.verify_manifest(self.root)
        for raw in [b'{"a":1,"a":2}', b'{"a":NaN}']:
            with self.assertRaises(dep.DeploymentError):
                dep.parse(raw)

    def test_escaped_paths_rejected(self):
        for name in ["../outside", "scripts/../outside", "C:/outside", "scripts\\file", "/absolute", "scripts/CON.txt"]:
            with self.assertRaises(dep.DeploymentError):
                dep.safe_path(self.root, name, exists=False)

    def test_native_linked_path_rejected(self):
        target = self.write("regular", b"fixture")
        linked = self.root / "linked"
        try:
            linked.symlink_to(target)
        except OSError:
            self.skipTest("Native symlink creation is unavailable on this Windows account")
        with self.assertRaises(dep.DeploymentError):
            dep.safe_path(self.root, "linked")


class ExecutionContractTests(unittest.TestCase):
    def test_official_contract_and_changes_without_loading_models(self):
        hy = SimpleNamespace(REVISION=dep.REVISION, MODEL={"name": dep.MODEL_FILE, "sha256": dep.MODEL_SHA256, "size": dep.MODEL_SIZE},
                             TEMPLATE_SHA=dep.TEMPLATE_SHA256, OVERRIDES=copy.deepcopy(dep.OVERRIDES),
                             SAMPLING=copy.deepcopy(dep.SAMPLING), CONTEXT_SIZE=8192, MAX_NEW_TOKENS=4096)
        with patch.object(dep, "_helpers", return_value=(hy, None)), \
                patch.object(dep.platform, "python_version", return_value="fixture-python"), \
                patch.object(dep.importlib.metadata, "version", return_value="fixture-jinja"):
            execution = dep.execution_contract()
            self.assertEqual(execution["pythonVersion"], "fixture-python")
            self.assertEqual(execution["jinjaVersion"], "fixture-jinja")
            self.assertEqual(execution["sampling"]["temperature"], 0.7)
            self.assertEqual(execution["runtimeOverrides"]["tokenizer.ggml.eos_token_id"], "int:127960")
            hy.SAMPLING["temperature"] = 0.8
            with self.assertRaises(dep.DeploymentError):
                dep.execution_contract()

    def test_canonical_execution_preserves_required_float_spelling(self):
        self.assertEqual(dep.canonical({"b": 0.0, "a": "x"}), b'{"a":"x","b":0.0}')


if __name__ == "__main__":
    unittest.main(verbosity=2)
