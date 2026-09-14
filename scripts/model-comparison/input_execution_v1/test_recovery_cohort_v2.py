"""Postvalidator-only tests: synthetic files and recorded argv, no inference."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from input_execution_v1 import recovery_cohort_v2 as c
import test_recovery_cohort as baseline_tests


def command(port="52160"):
    return {"shell": False, "argv": ["llama-server.exe", "--host", "127.0.0.1", "--port", port,
            "--cors-origins", "http://127.0.0.1:" + port, "--api-key", "[EPHEMERAL_REDACTED]",
            "--threads", "4", "--model", "fixture.gguf", "--ctx-size", "8192"]}


def replace(value, flag, replacement):
    value = deepcopy(value)
    value["argv"][value["argv"].index(flag) + 1] = replacement
    return value


class CommandNormalizationTests(unittest.TestCase):
    def test_exact_port_origin_pair_changes_are_allowed_without_mutation(self):
        first, second = command("63989"), command()
        originals = deepcopy([first, second])
        self.assertEqual(c.normalize_runtime_command(first), c.normalize_runtime_command(second))
        self.assertEqual([first, second], originals)

    def test_only_port_normalization_reproduces_frozen_v1_rejection(self):
        first, second = command("63989"), command()
        for item in (first, second):
            item["argv"][item["argv"].index("--port") + 1] = "<LOCAL_PORT>"
        self.assertNotEqual(first, second)
        self.assertNotEqual(first["argv"][6], second["argv"][6])

    def test_other_hosts_are_rejected(self):
        for host in ("localhost", "0.0.0.0", "127.0.0.2", "::1", "example.com"):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "loopback_host"):
                c.normalize_runtime_command(replace(command(), "--host", host))

    def test_unpaired_or_expanded_origins_are_rejected(self):
        for origin in ("http://127.0.0.1:63989", "https://127.0.0.1:52160", "http://localhost:52160",
                       "http://127.0.0.1:52160/", "*", "http://127.0.0.1:52160,http://evil.invalid",
                       "http://127.0.0.1:52160 http://evil.invalid", "http://127.0.0.1:52160@evil.invalid"):
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "origin_must_match"):
                c.normalize_runtime_command(replace(command(), "--cors-origins", origin))

    def test_port_syntax_and_range_are_strict(self):
        for port in ("0", "65536", "999999", "052160", "+52160", "52160.0", " 52160", "٥٢١٦٠", ""):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, "invalid_local_port"):
                c.normalize_runtime_command(command(port))
        for port in ("1", "65535"):
            c.normalize_runtime_command(command(port))

    def test_network_option_duplicates_aliases_and_missing_values_are_rejected(self):
        for flag in ("--host", "--port", "--cors-origins", "--api-key"):
            for extra in ([flag, "override"], [flag + "=override"]):
                changed = command()
                changed["argv"] += extra
                with self.subTest(flag=flag, extra=extra), self.assertRaises(ValueError):
                    c.normalize_runtime_command(changed)
            changed = command()
            index = changed["argv"].index(flag)
            del changed["argv"][index:index + 2]
            with self.subTest(missing=flag), self.assertRaisesRegex(ValueError, "unique_network_option"):
                c.normalize_runtime_command(changed)

    def test_shell_and_secret_contract_are_unchanged(self):
        for changed in (command() | {"shell": True}, command() | {"shell": None},
                        replace(command(), "--api-key", "unredacted-fixture"), {"shell": False, "argv": [1]}):
            with self.assertRaises(ValueError):
                c.normalize_runtime_command(changed)

    def test_all_non_network_arguments_and_metadata_remain_significant(self):
        normal = c.normalize_runtime_command(command())
        for changed in (replace(command(), "--threads", "8"), replace(command(), "--model", "other.gguf"),
                        replace(command(), "--ctx-size", "16384"), command() | {"extra": True}):
            self.assertNotEqual(c.normalize_runtime_command(changed), normal)

    def test_actual_recorded_commands_normalize_only_two_coupled_values(self):
        prior = c.v1.read_json(c.ROOT / c.v1.PRIOR_PATH / "runtime-command.json")
        recovery_path = c.ROOT / c.v1.RECOVERY_BASE / "recovery-attempt-001/runtime-command.json"
        recovery = c.v1.read_json(recovery_path)
        self.assertEqual(len(prior["argv"]), len(recovery["argv"]))
        differences = [i for i, (old, new) in enumerate(zip(prior["argv"], recovery["argv"])) if old != new]
        self.assertEqual(differences, [prior["argv"].index("--port") + 1, prior["argv"].index("--cors-origins") + 1])
        self.assertEqual(c.normalize_runtime_command(prior), c.normalize_runtime_command(recovery))

    def test_current_validation_lineage_keeps_frozen_execution_unchanged(self):
        original = c.v1.validate_recovery_run
        identity = c.validation_identity()
        self.assertEqual(set(identity), {"frozenExecutionManifest", "baselineValidator", "baselineTests", "validator", "tests", "correctionNote"})
        self.assertEqual(identity["baselineValidator"]["sha256"], c.V1_CODE_SHA256)
        self.assertEqual(identity["frozenExecutionManifest"]["sha256"], c.EXECUTION_FREEZE_SHA256)
        self.assertIs(c.v1.validate_recovery_run, original)
        self.assertIsNot(c.validate_recovery_run, original)


class CohortLineageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = baseline_tests.RecoveryCohortTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.prior, self.recovery = self.fixture.cohort_sources()
        self.destination = self.root / c.v1.COHORT_BASE / "explicit-v2-fixture"
        self.lineage = {}
        for name in ("frozenExecutionManifest", "baselineValidator", "baselineTests", "validator", "tests", "correctionNote"):
            path = self.root / "lineage" / (name + ".txt")
            path.parent.mkdir(exist_ok=True)
            path.write_text("synthetic lineage fixture: " + name, encoding="utf-8")
            self.lineage[name] = c.v1.ref(self.root, path)
        for function, result in (("validate_failed_run", self.prior), ("validate_recovery_run", self.recovery), ("validation_identity", self.lineage)):
            active = patch.object(c, function, return_value=result)
            active.start()
            self.addCleanup(active.stop)

    def build(self):
        return c.build(self.fixture.run, "unused", self.destination, self.root)

    def test_v2_cohort_keeps_original_bytes_rows_and_failed_run_history(self):
        original_v1 = c.v1.validate_recovery_run
        built = self.build()
        for target in (self.destination, self.destination / "manifest.json"):
            checked = c.validate_cohort(target, self.root)
            self.assertEqual(checked, built)
        manifest = built["manifest"]
        self.assertEqual(manifest["version"], c.v1.VERSION)
        self.assertEqual(manifest["validationVersion"], c.VALIDATION_VERSION)
        self.assertEqual(manifest["validationLineage"], self.lineage)
        self.assertEqual(manifest["sourceRuns"][0]["status"], "failed")
        self.assertEqual([manifest[k] for k in ("retainedOutputs", "recoveredOutputs", "completionRequestsSent", "interruptedRequests")], [38, 26, 65, 1])
        self.assertFalse(manifest["singleRunCompletion"])
        self.assertEqual(built["rows"], self.prior["rows"] + self.recovery["rows"])
        self.assertTrue(all(row["producerVersion"] == c.v1.PRIOR_VERSION for row in built["rows"][:38]))
        self.assertTrue(all(row["producerVersion"] == c.v1.RECOVERY_VERSION for row in built["rows"][38:]))
        expected = b"".join((self.root / item["evidence"]["predictions"]["path"]).read_bytes() for item in (self.prior, self.recovery))
        self.assertEqual((self.destination / "predictions.jsonl").read_bytes(), expected)
        self.assertIs(c.v1.validate_recovery_run, original_v1)

    def test_v1_or_missing_validation_marker_is_rejected(self):
        original = self.build()["manifest"]
        for marker in (None, "v1", "input-execution-v1-recovery-cohort-validator-v3"):
            changed = deepcopy(original)
            changed["validationVersion"] = marker
            self.fixture.write(self.destination / "manifest.json", changed)
            with self.assertRaisesRegex(ValueError, "v2_cohort_contract"):
                c.validate_cohort(self.destination, self.root)

    def test_changed_lineage_or_lineage_evidence_is_rejected(self):
        original = self.build()["manifest"]
        for field in ("validationLineage", "evidence"):
            changed = deepcopy(original)
            if field == "validationLineage":
                changed[field]["validator"]["sha256"] = "0" * 64
            else:
                changed[field]["validationLineage"] = {}
            self.fixture.write(self.destination / "manifest.json", changed)
            with self.assertRaises(ValueError):
                c.validate_cohort(self.destination, self.root)

    def test_frozen_provenance_counts_cannot_hide_interrupted_request(self):
        original = self.build()["manifest"]
        for field, value in (("completionRequestsSent", 64), ("interruptedRequests", 0), ("retainedOutputs", 39), ("singleRunCompletion", True)):
            changed = deepcopy(original)
            changed[field] = value
            self.fixture.write(self.destination / "manifest.json", changed)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "metadata_changed"):
                c.validate_cohort(self.destination, self.root)

    def test_reformatted_records_with_updated_hash_still_fail(self):
        built = self.build()
        path = self.destination / "predictions.jsonl"
        path.write_bytes(b"".join(c.e.packed(row) for row in built["rows"]))
        manifest = built["manifest"] | {"predictions": c.v1.ref(self.root, path)}
        self.fixture.write(self.destination / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "records_rewritten"):
            c.validate_cohort(self.destination, self.root)

    def test_source_prediction_change_after_validation_blocks_build(self):
        path = self.root / self.recovery["evidence"]["predictions"]["path"]
        path.write_bytes(path.read_bytes() + b"{}\n")
        with self.assertRaisesRegex(ValueError, "predictions_changed_before_build"):
            self.build()
        self.assertFalse(self.destination.exists())

    def test_lineage_file_change_blocks_build(self):
        path = self.root / self.lineage["validator"]["path"]
        path.write_bytes(b"changed fixture verifier")
        with self.assertRaisesRegex(ValueError, "evidence_changed_during_validation"):
            self.build()
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
