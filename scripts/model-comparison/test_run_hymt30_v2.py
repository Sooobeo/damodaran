"""Source-free V2 memory guards and unchanged model contract; no model load."""
from __future__ import annotations

from pathlib import Path
import unittest

import run_hymt30 as original
import run_hymt30_v2 as runner


class Hy30V2Tests(unittest.TestCase):
    def test_old_runner_is_frozen(self):
        self.assertEqual(runner.common.digest(original.__file__),
            "0dc794a8dd33f3485a6a13bb2384f4fc8a0cb602dcd964341c3b79ba30ad3d85")
        runner.common.check_identity_unchanged({str(p): sha for p, sha in runner.FROZEN.items()})

    def test_only_command_difference_disables_repacking(self):
        old = original.server_command(43678, "fixture-key")
        new = runner.server_command(43678, "fixture-key")
        self.assertEqual(new.count("--no-repack"), 1)
        self.assertEqual([v for v in new if v != "--no-repack"], old)
        self.assertEqual(runner.SAMPLING, original.SAMPLING)
        self.assertEqual(runner.TEMPLATE_SHA, original.TEMPLATE_SHA)
        self.assertEqual(runner.TOKEN_STRINGS, original.TOKEN_STRINGS)
        self.assertEqual(runner.CONTEXT_SIZE, original.CONTEXT_SIZE)

    def test_physical_and_commit_both_required_separately(self):
        physical = runner.setup.MODEL["size"] + 3 * 1024**3
        commit = 3 * 1024**3
        exact = runner.preflight(physical, commit)
        self.assertTrue(exact["passed"])
        self.assertEqual(exact["f16KvBudgetBytes"], 805306368)
        self.assertFalse(runner.preflight(physical-1, commit)["passed"])
        self.assertFalse(runner.preflight(physical, commit-1)["passed"])
        self.assertFalse(runner.preflight(physical*2, 0)["passed"])
        self.assertFalse(runner.preflight(0, commit*10)["passed"])
        self.assertFalse(exact["commitIsFreePageFileDiskSpace"])
        self.assertFalse(exact["sharedGpuMemoryAddsRam"])

    def test_guard_triggers_for_sustained_commit_or_physical_low(self):
        high = 1024**3
        for field, expected in (("availablePhysical", "physical"), ("availablePageFile", "commit")):
            guard = runner.MemoryGuard()
            state = {"availablePhysical": high, "availablePageFile": high, field: 1}
            self.assertIsNone(guard.observe(state, 0))
            self.assertIsNone(guard.observe(state, 2.99))
            self.assertEqual(guard.observe(state, 3), expected)

    def test_guard_timers_reset_and_do_not_combine_resources(self):
        high = 1024**3
        guard = runner.MemoryGuard()
        self.assertIsNone(guard.observe({"availablePhysical": 1, "availablePageFile": high}, 0))
        self.assertIsNone(guard.observe({"availablePhysical": high, "availablePageFile": 1}, 2))
        self.assertIsNone(guard.observe({"availablePhysical": 1, "availablePageFile": high}, 4))
        self.assertIsNone(guard.observe({"availablePhysical": 1, "availablePageFile": high}, 6))
        self.assertEqual(guard.observe({"availablePhysical": 1, "availablePageFile": high}, 7), "physical")

    def test_guard_threshold_and_missing_sample(self):
        guard = runner.MemoryGuard()
        exact = {"availablePhysical": 512*1024**2, "availablePageFile": 512*1024**2}
        self.assertIsNone(guard.observe(exact, 0))
        self.assertIsNone(guard.observe(exact, 100))
        for state in ({"availablePhysical": 1}, {"availablePhysical": -1, "availablePageFile": 5}):
            with self.assertRaisesRegex(ValueError, "memory_guard_sample"):
                runner.MemoryGuard().observe(state, 0)

    def test_owned_spawn_and_shutdown_record_remain_present(self):
        source = Path(runner.__file__).read_text("utf-8")
        self.assertIn("owner.spawn(command", source)
        self.assertNotIn("subprocess.Popen(", source)
        self.assertIn('summary["memoryAfterChildCleanup"] = screen.memory_status()', source)
        self.assertIn('summary["memoryGuardAbortReason"] = cause', source)
        self.assertEqual(runner.VERSION, "hymt30-development-screen-v2")


if __name__ == "__main__":
    unittest.main()
