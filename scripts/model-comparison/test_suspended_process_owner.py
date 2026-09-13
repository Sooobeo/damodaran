"""Mock and small native fixtures only; no model, dataset, server or app access."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import suspended_process_owner as adapter
import working_set_limit


@contextmanager
def fixture_directory():
    prefix = "moonmodaran-suspended-fixture-"
    directory = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield directory
    finally:
        resolved = directory.resolve()
        if resolved.parent != Path(tempfile.gettempdir()).resolve() or not resolved.name.startswith(prefix):
            raise RuntimeError("fixture_cleanup_path_outside_expected_temp_root")
        shutil.rmtree(resolved)


class ProtocolTests(unittest.TestCase):
    def test_order_and_exact_suspend_count(self):
        for count in (1, 0, 2, 0xFFFFFFFF):
            with self.subTest(count=count):
                api = object.__new__(adapter._SuspendedWin32)
                api.kernel, api.limiter = MagicMock(), MagicMock()
                api.kernel.ResumeThread.return_value = count
                info = adapter.process_owner._ProcessInformation(100, 101, 102, 103)
                calls = []
                api.assert_child = lambda *args: calls.append("owned")
                api.limiter.apply_before_resume.side_effect = lambda *args: (calls.append("cap"), {"flags": 6})[1]
                api.kernel.ResumeThread.side_effect = lambda *args: (calls.append("resume"), count)[1]
                def created(*args):
                    calls.append("suspended")
                    self.assertTrue(args[5] & adapter.process_owner.CREATE_SUSPENDED)
                with patch.object(adapter.process_owner._Win32, "create_child", side_effect=created):
                    if count == 1:
                        api.create_child(1, "test", "test", None, None, 0, (), (), info)
                    else:
                        with self.assertRaises(adapter.process_owner.ProcessOwnershipError):
                            api.create_child(1, "test", "test", None, None, 0, (), (), info)
                self.assertEqual(calls, ["suspended", "owned", "cap", "resume"])

    def test_cap_failure_never_resumes_or_closes_handles_twice(self):
        api = object.__new__(adapter._SuspendedWin32)
        api.kernel, api.limiter = MagicMock(), MagicMock()
        api.assert_child = MagicMock()
        api.limiter.apply_before_resume.side_effect = RuntimeError("fixture-only")
        info = adapter.process_owner._ProcessInformation(100, 101, 102, 103)
        with patch.object(adapter.process_owner._Win32, "create_child"), self.assertRaises(RuntimeError):
            api.create_child(1, "test", "test", None, None, 0, (), (), info)
        api.kernel.ResumeThread.assert_not_called()
        api.kernel.CloseHandle.assert_not_called()
        self.assertEqual((info.hProcess, info.hThread), (100, 101))


def native_fixture(mode):
    """Run in a disposable Python parent so its owned Job ends at process exit."""
    owner = adapter.process_owner.claim_process_owner()
    original_api = owner._api
    with fixture_directory() as directory:
        marker = directory / "started.txt"
        observed = {"callbackBeforeChildCode": False}

        class Limit(working_set_limit.WorkingSetLimit):
            def apply_before_resume(self, handle, pid):
                observed["callbackBeforeChildCode"] = not marker.exists()
                if not observed["callbackBeforeChildCode"]:
                    raise AssertionError("child_code_executed_before_cap")
                receipt = super().apply_before_resume(handle, pid)
                if mode == "failure":
                    raise RuntimeError("injected_after_cap_before_resume")
                return receipt

        class RecordApi(adapter._SuspendedWin32):
            def create_child(self, *args):
                try:
                    return super().create_child(*args)
                finally:
                    info = args[-1]
                    self.recorded_handles = (info.hProcess, info.hThread)

        limiter = Limit(owner, maximum_bytes=127 * 1024**2)
        owned = adapter.SuspendedProcessOwner(owner, limiter)
        owned._api = RecordApi(limiter)  # Only this fixture adapter; production singleton is untouched.
        code = ("import os,sys,time,pathlib,json; "
                "pathlib.Path('started.txt').write_text('started'); "
                "print(json.dumps({'pid':os.getpid(),'env':os.environ['FIXTURE_UNICODE'],"
                "'argv':sys.argv[1],'cwd':str(pathlib.Path.cwd())}),flush=True); time.sleep(20)")
        child = None
        try:
            try:
                child = owned.spawn([sys._base_executable, "-c", code, "fixture-\uac00"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    bufsize=0, cwd=str(directory), env=dict(os.environ, FIXTURE_UNICODE="fixture-\ub098"))
            except RuntimeError as error:
                if mode != "failure" or str(error) != "injected_after_cap_before_resume":
                    raise
                assert not marker.exists()
                kernel = owned._api.kernel
                for handle in owned._api.recorded_handles:
                    flags = wintypes.DWORD()
                    assert not kernel.GetHandleInformation(handle, ctypes.byref(flags))
                    assert ctypes.get_last_error() == 6
                observed.update(status="passed", childCodeDidNotRun=True, bothNativeHandlesClosed=True,
                                productionApiPreserved=owner._api is original_api,
                                creation=owned.creation_receipt)
                return observed
            assert mode == "success"
            line = child.stdout.readline()
            value = json.loads(line)
            assert value == {"pid": child.pid, "env": "fixture-\ub098", "argv": "fixture-\uac00", "cwd": str(directory)}
            limit = limiter.apply_child(child)
            sample = limiter.sample_child(child)
            assert sample["workingSetBytes"] <= 128 * 1024**2
            assert owned.creation_receipt["resumePreviousCount"] == 1
            assert limit["after"]["maximumBytes"] == 127 * 1024**2
            observed.update(status="passed", pipeArgvEnvironmentCwdPreserved=True,
                            productionApiPreserved=owner._api is original_api,
                            creation=owned.creation_receipt, sample=sample)
            return observed
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
                child.stdout.close()
                observed["ownedChildStopped"] = child.poll() is not None


class NativeTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt" and os.environ.get("RUN_COMPARISON_NATIVE") == "1", "explicit source-free native fixture")
    def test_suspended_success_and_failure_cleanup_in_isolated_parent(self):
        for mode in ("success", "failure"):
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, __file__, "--native", mode], capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                value = json.loads(result.stdout)
                self.assertEqual(value["status"], "passed")
                self.assertTrue(value["productionApiPreserved"])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--native":
        print(json.dumps(native_fixture(sys.argv[2])), flush=True)
    else:
        unittest.main()
