"""Metadata-only runner contracts. Never load model weights or spawn a backend."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import probe_hymt_backend as probe


class BackendProbeTests(unittest.TestCase):
    def test_cpu_and_sycl_change_only_backend_threads(self):
        cpu = probe.server_command("cpu", 8, 54321, "test-only-key")
        sycl = probe.server_command("sycl", 4, 54321, "test-only-key")
        for argv in (cpu, sycl):
            self.assertEqual(argv[argv.index("--model") + 1], str(probe.fixed.DEST / probe.fixed.MODEL["name"]))
            self.assertEqual(argv[argv.index("--ctx-size") + 1], "8192")
            self.assertEqual(argv[argv.index("--n-predict") + 1], "4096")
            self.assertIn("--offline", argv)
            self.assertIn("--no-agent", argv)
            self.assertEqual(argv[argv.index("--fit") + 1], "off")
        self.assertEqual(cpu[cpu.index("--device") + 1], "none")
        self.assertEqual(sycl[sycl.index("--device") + 1], "SYCL0")
        with self.assertRaises(probe.fixed.RunError):
            probe.server_command("sycl", 8, 54321, "test-only-key")

    def test_memory_budget_counts_f16_kv_and_scratch(self):
        expected = probe.fixed.MODEL["size"] + 2 * 1024 ** 3
        self.assertFalse(probe.preflight(expected - 1)["passed"])
        self.assertTrue(probe.preflight(expected)["passed"])
        self.assertFalse(probe.preflight(expected)["sharedGpuMemoryAddsRam"])

    def test_default_prepare_never_verifies_or_loads_model(self):
        with tempfile.TemporaryDirectory(prefix="damodaran-hymt-backend-") as temporary:
            folder = Path(temporary)
            self.assertTrue(folder.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()))
            self.assertTrue(folder.name.startswith("damodaran-hymt-backend-"))
            output = folder / "probe"
            with patch.object(probe.fixed, "COMPARISONS", folder), \
                 patch.object(probe.fixed, "verify_installation") as verification, \
                 patch.object(probe.subprocess, "Popen") as spawn:
                result = probe.run(argparse.Namespace(output=output, backend="cpu", threads=4, run=False))
                self.assertEqual(result, 0)
                verification.assert_not_called()
                spawn.assert_not_called()
            summary = json.loads((output / "summary.json").read_text("utf-8"))
            self.assertEqual(summary["status"], "prepared")
            self.assertFalse(summary["modelLoaded"])
            with self.assertRaises(FileExistsError):
                with patch.object(probe.fixed, "COMPARISONS", folder):
                    probe.run(argparse.Namespace(output=output, backend="cpu", threads=4, run=False))

    def test_bounded_client_caps_legacy_timeout(self):
        client = probe.BoundedClient("http://127.0.0.1:54321", "test-key")
        with patch.object(probe.fixed.LocalClient, "request", return_value={}) as request:
            client.request("/completion", {}, timeout=1800)
            self.assertEqual(request.call_args.kwargs["timeout"], 240)


if __name__ == "__main__":
    unittest.main()
