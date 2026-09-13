"""Windows Job Object fixtures only; never load a model or application data.

Native ownership is claimed inside disposable fixture parents, never in this
test runner. Every process we stop was created by this test and is held by its
own Popen or native process handle. Temporary paths stay below the OS temp root.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, PropertyMock, patch

import process_owner


HERE = Path(__file__).resolve().parent
BASE_PYTHON = sys._base_executable
VENV_PYTHON = str(HERE.parents[1] / ".venv-training/Scripts/python.exe")
WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258


@contextmanager
def fixture_directory():
    temporary = tempfile.TemporaryDirectory(prefix="moonmodaran-job-owner-tests-")
    directory = Path(temporary.name).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if directory.parent != temp_root or not directory.name.startswith("moonmodaran-job-owner-tests-"):
        raise AssertionError("Fixture directory is outside the designated temporary root")
    try:
        yield directory
    finally:
        # TemporaryDirectory performs the only recursive deletion in this test.
        if Path(temporary.name).resolve() != directory or directory.parent != temp_root:
            raise AssertionError("Fixture cleanup target changed")
        temporary.cleanup()


def kernel_api():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateProcess.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.GetProcessTimes.argtypes = [wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4]
    api.GetProcessTimes.restype = wintypes.BOOL
    return api


def creation_ticks(api, handle):
    times = [wintypes.FILETIME() for _ in range(4)]
    if not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        raise AssertionError("Cannot verify the fixture process creation time")
    return times[0].dwHighDateTime << 32 | times[0].dwLowDateTime


def retain_fixture_process(api, pid, expected_creation):
    handle = api.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, False, pid)
    if not handle:
        raise AssertionError("Cannot retain actual fixture process handle")
    try:
        if creation_ticks(api, handle) != expected_creation or api.WaitForSingleObject(handle, 0) != WAIT_TIMEOUT:
            raise AssertionError("Fixture process identity or lifetime changed")
        return handle
    except BaseException:
        # A mismatched/reused PID must never enter the cleanup termination list.
        api.CloseHandle(handle)
        raise


def stop_created_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def read_fixture_state(file, process, timeout=10):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            return json.loads(file.read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        if process.poll() is not None:
            raise AssertionError(f"Fixture parent exited before publishing state: {process.returncode}")
        time.sleep(0.02)
    raise AssertionError("Fixture parent did not publish state within the bounded timeout")


FIXTURE_PARENT = r'''
from contextlib import nullcontext
import ctypes
from ctypes import wintypes
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

helper_dir, directory, mode = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3]
child_python = sys.argv[4]
sys.path.insert(0, str(helper_dir))
import process_owner

def write_state(value):
    (directory / "state.json").write_text(json.dumps(value), encoding="utf-8")

def read_child_state():
    until = time.monotonic() + 10
    while time.monotonic() < until:
        try:
            return json.loads((directory / "child-ready.txt").read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.01)
    raise AssertionError("Fixture child metadata was not published")

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetCurrentProcess.argtypes = []
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
kernel.CreateJobObjectW.restype = wintypes.HANDLE
kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel.AssignProcessToJobObject.restype = wintypes.BOOL
kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel.GetHandleInformation.restype = wintypes.BOOL
kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
kernel.IsProcessInJob.restype = wintypes.BOOL
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4]
kernel.GetProcessTimes.restype = wintypes.BOOL
kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.WaitForSingleObject.restype = wintypes.DWORD
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.CloseHandle.restype = wintypes.BOOL

def created(handle):
    times = [wintypes.FILETIME() for _ in range(4)]
    assert kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times))
    return times[0].dwHighDateTime << 32 | times[0].dwLowDateTime

# The outer fixture job deliberately has NO KILL_ON_JOB_CLOSE limit, so it
# cannot mask a missing kill-on-close flag in the helper's inner job.
outer_job = None
if mode == "nested":
    outer_job = kernel.CreateJobObjectW(None, None)
    if not outer_job or not kernel.AssignProcessToJobObject(outer_job, kernel.GetCurrentProcess()):
        write_state({"fixtureBlocked": "outer_job_assignment", "winError": ctypes.get_last_error()})
        raise SystemExit(3)

try:
    owner = process_owner.claim_process_owner()
except process_owner.ProcessOwnershipError as error:
    write_state({"fixtureBlocked": "helper_claim", "code": error.code})
    raise SystemExit(4)

same = process_owner.claim_process_owner()
owner.assert_owned()
# This private read is solely for native GetHandleInformation/IsProcessInJob
# verification; production callers use the public ownership API.
job_handle = owner._handle
flags = wintypes.DWORD()
if not kernel.GetHandleInformation(job_handle, ctypes.byref(flags)):
    write_state({"fixtureFailure": "GetHandleInformation", "winError": ctypes.get_last_error()})
    raise SystemExit(5)

base = {"parentPid": os.getpid(), "parentParentPid": os.getppid(), "processId": owner.process_id,
        "parentCreated": created(kernel.GetCurrentProcess()),
        "singleton": same is owner, "handleInheritable": bool(flags.value & 1)}
if mode == "guards":
    rejected = []
    with patch.object(process_owner, "_OwnedPopen") as fake, patch.object(owner._api, "assert_child"):
        for label, options in [
            ("shell", {"shell": True}),
            ("close_fds", {"close_fds": False}),
            ("breakaway", {"creationflags": 0x01000000}),
            ("startupinfo", {"startupinfo": object()}),
        ]:
            try:
                owner.spawn([sys.executable, "-c", "raise SystemExit(0)"], **options)
            except process_owner.ProcessOwnershipError as error:
                rejected.append({"case": label, "code": error.code})
        invalid_spawn_calls = fake.call_count
        fake.reset_mock()
        result = owner.spawn([sys.executable, "-c", "raise SystemExit(0)"], creationflags=0x00000200)
        kwargs = fake.call_args.kwargs
        base.update(rejected=rejected, invalidSpawnCalls=invalid_spawn_calls,
                    validSpawnCalls=fake.call_count, returnsPopen=result is fake.return_value,
                    shell=kwargs.get("shell"), closeFds=kwargs.get("close_fds"),
                    creationFlags=kwargs.get("creationflags"))
    write_state(base)
    raise SystemExit(0)

child_code = "from pathlib import Path; import sys,time,os,json; Path(sys.argv[1]).write_text(json.dumps(dict(pid=os.getpid(),ppid=os.getppid())),encoding='utf-8'); time.sleep(300)"
if mode == "io":
    child_directory = directory / "space \uc720\ub2c8\ucf54\ub4dc"
    child_directory.mkdir()
    environment = os.environ.copy()
    environment["OWNER_FIXTURE_VALUE"] = "\uae08\uc735 fixture"
    expected_arguments = ['space value', 'quoted"value', 'trailing\\', '\uc790\uc2dd']
    code = "import json,os,sys; print(json.dumps(dict(arguments=sys.argv[1:],cwd=os.getcwd(),value=os.environ['OWNER_FIXTURE_VALUE'],input=sys.stdin.buffer.read().decode('utf-8')))); print('fixture stderr',file=sys.stderr)"
    child = owner.spawn([child_python, "-B", "-c", code, *expected_arguments],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        cwd=child_directory, env=environment, bufsize=0)
    output, errors = child.communicate(input="\ubb38\uc7a5 input".encode("utf-8"), timeout=10)
    actual = json.loads(output)
    base.update(ioPassed=actual == dict(arguments=expected_arguments, cwd=str(child_directory),
                value=environment["OWNER_FIXTURE_VALUE"], input="\ubb38\uc7a5 input"),
                stderrPassed=errors.strip() == b"fixture stderr", childExitCode=child.returncode)
    merged = owner.spawn([child_python, "-B", "-c", "import sys; print('out',flush=True); print('err',file=sys.stderr,flush=True)"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         cwd=child_directory, env=environment, bufsize=0)
    merged_output, merged_error = merged.communicate(timeout=10)
    base.update(mergedPassed=merged_output.splitlines() == [b"out", b"err"] and merged_error is None,
                mergedExitCode=merged.returncode)
    write_state(base)
    raise SystemExit(0)
if mode == "reject-redirector":
    actual_handle = None
    original_check = owner._api.assert_child
    def delayed_check(job, launched):
        global actual_handle
        state = read_child_state()
        actual_handle = kernel.OpenProcess(0x00100000 | 0x1000, False, state["pid"])
        assert actual_handle
        base.update(childActualPid=state["pid"], childParentPid=state["ppid"])
        # Atomic JOB_LIST now owns the redirector too. Inject a failed final
        # check only after proving membership and holding its actual child.
        # This exercises refusal cleanup, not a promise to support redirectors.
        original_check(job, launched)
        raise process_owner.ProcessOwnershipError("fixture_child_check_failure")
    owner._api.assert_child = delayed_check
    try:
        rejected = owner.spawn([child_python, "-B", "-c", child_code, str(directory / "child-ready.txt")],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except process_owner.ProcessOwnershipError as error:
        base.update(rejected=error.code, redirectedChildStopped=kernel.WaitForSingleObject(actual_handle, 5000) == 0)
        kernel.CloseHandle(actual_handle)
        write_state(base)
        raise SystemExit(0)
    else:
        rejected.kill()
        rejected.wait(timeout=5)
        write_state(base | {"fixtureFailure": "redirector_unexpectedly_accepted"})
        raise SystemExit(9)
native_child = child_python.lower().endswith("ping.exe")
if mode == "before-return":
    original_create = owner._api.create_child
    def pause_after_atomic_creation(*arguments):
        original_create(*arguments)
        information = arguments[-1]
        owner._api.assert_child(job_handle, information.hProcess)
        base.update(childPid=information.dwProcessId, childActualPid=information.dwProcessId,
                    childParentPid=os.getpid(), childCreated=created(information.hProcess),
                    childLauncherInOwnedJob=True, childInOwnedJob=True, beforeSpawnReturn=True)
        write_state(base)
        while True:
            time.sleep(1)
    owner._api.create_child = pause_after_atomic_creation
arguments = ([child_python, "-n", "300", "127.0.0.1"] if native_child else
             [child_python, "-B", "-c", child_code, str(directory / "child-ready.txt")])
child = owner.spawn(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
until = time.monotonic() + 5
while not native_child and not (directory / "child-ready.txt").exists() and child.poll() is None and time.monotonic() < until:
    time.sleep(0.01)
if not native_child and not (directory / "child-ready.txt").exists():
    write_state({"fixtureFailure": "child_ready", "childPid": child.pid})
    raise SystemExit(6)
member = wintypes.BOOL()
if not kernel.IsProcessInJob(int(child._handle), job_handle, ctypes.byref(member)):
    write_state({"fixtureFailure": "IsProcessInJob", "winError": ctypes.get_last_error(), "childPid": child.pid})
    raise SystemExit(7)
launcher_member = bool(member.value)
child_state = {"pid": child.pid, "ppid": os.getpid()} if native_child else read_child_state()
child_handle = kernel.OpenProcess(0x00100000 | 0x1000, False, child_state["pid"])
if not child_handle or not kernel.IsProcessInJob(child_handle, job_handle, ctypes.byref(member)):
    write_state({"fixtureFailure": "actual_child_handle", "winError": ctypes.get_last_error()})
    raise SystemExit(8)
base.update(childPid=child.pid, childActualPid=child_state["pid"], childParentPid=child_state["ppid"],
            childCreated=created(child_handle), childLauncherInOwnedJob=launcher_member,
            childInOwnedJob=bool(member.value))
write_state(base)

if mode in ("normal", "eof"):
    stopped = False
    try:
        # Keep every PID alive until the outer test has opened and checked its
        # real OS handle. EOF then mirrors the app's stdin.end() cleanup path.
        sys.stdin.buffer.read()
        # Discard caller references before cleanup. The job belongs to the
        # process/module lifetime and must not kill this parent on collection.
        del owner, same
        gc.collect()
        assert child.poll() is None
        process_owner.claim_process_owner().assert_owned()
        child.terminate()
        child.wait(timeout=5)
        assert kernel.WaitForSingleObject(child_handle, 5000) == 0
        stopped = True
    finally:
        (directory / "normal-finally.json").write_text(json.dumps({"childStopped": stopped,
            "childExitCode": child.poll(), "parentPid": os.getpid()}), encoding="utf-8")
    raise SystemExit(0)

while True:
    time.sleep(1)
'''


NODE_PARENT = r'''
const fs=require('node:fs'), path=require('node:path'), {spawn}=require('node:child_process');
const [python,fixture,helper,directory,mode,childPython]=process.argv.slice(1);
const child=spawn(python,['-B','-c',fixture,helper,directory,mode,childPython],{
  windowsHide:true,stdio:['pipe','ignore','ignore']});
child.on('error',()=>{process.exitCode=2;process.stdin.destroy();});
child.on('spawn',()=>fs.writeFileSync(path.join(directory,'node-state.json'),JSON.stringify({nodePid:process.pid,launcherPid:child.pid})));
process.stdin.once('data',data=>{if(data.toString().trim()==='kill')child.kill();else child.stdin.end();});
child.on('exit',code=>{process.exitCode=code===0?0:1;process.stdin.destroy();});
'''


@unittest.skipUnless(WINDOWS, "Native Windows Job Object ownership requires Windows")
class NativeOwnershipTests(unittest.TestCase):
    def launch_parent(self, directory, mode, python=BASE_PYTHON, child_python=BASE_PYTHON, node=False):
        arguments = [python, "-B", "-c", FIXTURE_PARENT, str(HERE), str(directory), mode, child_python]
        if node:
            executable = shutil.which("node")
            self.assertTrue(executable, "The application's Node executable is required")
            arguments = [executable, "-e", NODE_PARENT, python, FIXTURE_PARENT, str(HERE), str(directory), mode, child_python]
        return subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            shell=False, close_fds=True, creationflags=CREATE_NO_WINDOW,
        )

    def assert_ready(self, state, nested=False):
        if nested and state.get("fixtureBlocked"):
            self.skipTest("Nested Job Object fixture blocked by this host: " + json.dumps(state, sort_keys=True))
        self.assertNotIn("fixtureBlocked", state, state)
        self.assertNotIn("fixtureFailure", state, state)
        self.assertTrue(state["singleton"])
        self.assertEqual(state["processId"], state["parentPid"])
        self.assertFalse(state["handleInheritable"])

    def force_parent_exit(self, nested=False, python=BASE_PYTHON, node=False, eof=False, actual=False,
                          native=False, before_return=False):
        api = kernel_api()
        with fixture_directory() as directory:
            parent = control = None
            handles = []
            try:
                control = subprocess.Popen([BASE_PYTHON, "-B", "-c", "import time; time.sleep(300)"],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, shell=False, close_fds=True,
                                           creationflags=CREATE_NO_WINDOW)
                mode = "before-return" if before_return else "eof" if eof else "nested" if nested else "forced"
                child_python = str(Path(os.environ["SystemRoot"]) / "System32/ping.exe") if native else BASE_PYTHON
                parent = self.launch_parent(directory, mode, python=python, node=node, child_python=child_python)
                state = read_fixture_state(directory / "state.json", parent)
                self.assert_ready(state, nested=nested)
                self.assertTrue(state["childInOwnedJob"])
                self.assertTrue(state["childLauncherInOwnedJob"])
                if before_return:
                    self.assertTrue(state["beforeSpawnReturn"])
                # The ready bridge is held alive until we retain its actual OS
                # handle. Creation times reject PID reuse before any termination.
                for pid_key, time_key in [("parentPid", "parentCreated"), ("childActualPid", "childCreated")]:
                    self.assertNotEqual(state[pid_key], control.pid)
                    held = retain_fixture_process(api, state[pid_key], state[time_key])
                    handles.append(held)
                if node:
                    node_state = read_fixture_state(directory / "node-state.json", parent)
                    launcher_pid = node_state["launcherPid"]
                    self.assertEqual(node_state["nodePid"], parent.pid)
                    launcher = api.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, launcher_pid)
                    self.assertTrue(launcher)
                    handles.append(launcher)
                else:
                    launcher_pid = parent.pid
                if Path(python) == Path(VENV_PYTHON):
                    self.assertNotEqual(state["parentPid"], launcher_pid)
                    self.assertEqual(state["parentParentPid"], launcher_pid)
                else:
                    self.assertEqual(state["parentPid"], launcher_pid)
                if actual:
                    self.assertTrue(api.TerminateProcess(handles[0], 99))
                elif node:
                    parent.stdin.write(b"eof\n" if eof else b"kill\n")
                    parent.stdin.flush()
                elif eof:
                    parent.stdin.close()
                else:
                    parent.kill()
                parent.wait(timeout=10)
                for held in handles:
                    self.assertEqual(api.WaitForSingleObject(held, 5000), WAIT_OBJECT_0,
                                     "Actual fixture process survived launcher/bridge cleanup")
                if eof:
                    self.assertEqual(parent.returncode, 0)
                    sentinel = json.loads((directory / "normal-finally.json").read_text("utf-8"))
                    self.assertTrue(sentinel["childStopped"])
                    self.assertIsNotNone(sentinel["childExitCode"])
                    self.assertEqual(sentinel["parentPid"], state["parentPid"])
                self.assertIsNone(control.poll(), "An unrelated fixture control process was terminated")
            finally:
                stop_created_process(parent)
                if parent and parent.stdin and not parent.stdin.closed:
                    parent.stdin.close()
                for held in handles:
                    # Only this already-held, test-created child handle may be
                    # terminated if the assertion reveals an ownership failure.
                    if api.WaitForSingleObject(held, 0) == WAIT_TIMEOUT:
                        api.TerminateProcess(held, 99)
                        api.WaitForSingleObject(held, 5000)
                    api.CloseHandle(held)
                stop_created_process(control)

    def test_force_killing_parent_terminates_child_and_preserves_control(self):
        for python in [BASE_PYTHON, VENV_PYTHON]:
            with self.subTest(python=python):
                self.force_parent_exit(python=python)

    def test_nested_outer_job_uses_the_inner_kill_on_close_policy(self):
        for python in [BASE_PYTHON, VENV_PYTHON]:
            with self.subTest(python=python):
                self.force_parent_exit(nested=True, python=python)

    def test_normal_cleanup_reaches_finally_after_child_termination(self):
        for python in [BASE_PYTHON, VENV_PYTHON]:
            with self.subTest(python=python):
                self.force_parent_exit(python=python, eof=True)

    def test_killing_actual_bridge_terminates_native_child(self):
        for python in [BASE_PYTHON, VENV_PYTHON]:
            with self.subTest(python=python):
                self.force_parent_exit(python=python, actual=True)

    def test_node_spawn_kill_and_stdin_eof_stop_actual_processes(self):
        for python in [BASE_PYTHON, VENV_PYTHON]:
            for eof in [False, True]:
                with self.subTest(python=python, eof=eof):
                    self.force_parent_exit(python=python, node=True, eof=eof)

    def test_native_windows_executable_is_owned_and_cleaned_for_base_venv_and_node(self):
        for python in [BASE_PYTHON, VENV_PYTHON]:
            for node in [False, True]:
                for eof in [False, True]:
                    with self.subTest(python=python, node=node, eof=eof):
                        self.force_parent_exit(python=python, node=node, eof=eof, native=True)

    def test_native_child_is_owned_before_spawn_returns_and_survives_no_parent(self):
        for python in [BASE_PYTHON, VENV_PYTHON]:
            with self.subTest(python=python):
                self.force_parent_exit(python=python, native=True, before_return=True, actual=True)

    def test_failed_child_check_refusal_stops_redirected_python(self):
        for python in [BASE_PYTHON, VENV_PYTHON]:
            with self.subTest(python=python), fixture_directory() as directory:
                parent = self.launch_parent(directory, "reject-redirector", python=python, child_python=VENV_PYTHON)
                try:
                    state = read_fixture_state(directory / "state.json", parent)
                    self.assert_ready(state)
                    parent.wait(timeout=10)
                    self.assertEqual(parent.returncode, 0)
                    self.assertEqual(state["rejected"], "child_ownership_not_verified")
                    self.assertTrue(state["redirectedChildStopped"])
                finally:
                    stop_created_process(parent)
                    parent.stdin.close()

    def test_native_creation_preserves_unicode_arguments_environment_cwd_and_pipes(self):
        for python in [BASE_PYTHON, VENV_PYTHON]:
            with self.subTest(python=python), fixture_directory() as directory:
                parent = self.launch_parent(directory, "io", python=python)
                try:
                    state = read_fixture_state(directory / "state.json", parent)
                    self.assert_ready(state)
                    parent.wait(timeout=10)
                    self.assertEqual(parent.returncode, 0)
                    for key in ["ioPassed", "stderrPassed", "mergedPassed"]:
                        self.assertTrue(state[key], key)
                    self.assertEqual(state["childExitCode"], 0)
                    self.assertEqual(state["mergedExitCode"], 0)
                finally:
                    stop_created_process(parent)
                    parent.stdin.close()

    def test_spawn_rejects_escape_options_and_forces_hidden_secure_defaults(self):
        with fixture_directory() as directory:
            parent = self.launch_parent(directory, "guards")
            try:
                state = read_fixture_state(directory / "state.json", parent)
                self.assert_ready(state)
                parent.wait(timeout=10)
                self.assertEqual(parent.returncode, 0)
                self.assertEqual({item["case"] for item in state["rejected"]}, {"shell", "close_fds", "breakaway", "startupinfo"})
                self.assertTrue(all(isinstance(item["code"], str) and item["code"] for item in state["rejected"]))
                self.assertEqual(state["invalidSpawnCalls"], 0)
                self.assertEqual(state["validSpawnCalls"], 1)
                self.assertTrue(state["returnsPopen"])
                self.assertIs(state["shell"], False)
                self.assertIs(state["closeFds"], True)
                self.assertEqual(state["creationFlags"], CREATE_NO_WINDOW | 0x00000200)
            finally:
                stop_created_process(parent)
                parent.stdin.close()


class AtomicCreationContractTests(unittest.TestCase):
    def make_api(self):
        api = object.__new__(process_owner._Win32)
        api.kernel = Mock()
        def initialize(buffer, count, flags, size):
            ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t)).contents.value = 128
            return buffer is not None
        api.kernel.InitializeProcThreadAttributeList.side_effect = initialize
        return api

    def create(self, api, inherited=(44, 48)):
        api.create_child(123456, "fixture.exe", ctypes.create_unicode_buffer("fixture.exe"),
                         None, None, CREATE_NO_WINDOW, (44, 48, 48) if inherited else (),
                         list(inherited), process_owner._ProcessInformation())

    def test_creation_declares_exact_job_and_pipe_handle_lists_before_process_creation(self):
        api = self.make_api()
        observed = []
        def update(buffer, flags, attribute, value, size, previous, returned):
            handles = ctypes.cast(value, ctypes.POINTER(wintypes.HANDLE * (size // ctypes.sizeof(wintypes.HANDLE)))).contents
            observed.append((attribute, list(handles)))
            return True
        api.kernel.UpdateProcThreadAttribute.side_effect = update
        def create(*arguments):
            self.assertEqual(observed, [(process_owner.PROC_THREAD_ATTRIBUTE_JOB_LIST, [123456]),
                                        (process_owner.PROC_THREAD_ATTRIBUTE_HANDLE_LIST, [44, 48])])
            self.assertIs(arguments[4], True)
            self.assertEqual(arguments[5], CREATE_NO_WINDOW | process_owner.EXTENDED_STARTUPINFO_PRESENT
                              | process_owner.CREATE_UNICODE_ENVIRONMENT)
            startup = ctypes.cast(arguments[8], ctypes.POINTER(process_owner._StartupInfoEx)).contents
            self.assertEqual(startup.StartupInfo.cb, ctypes.sizeof(process_owner._StartupInfoEx))
            self.assertEqual(startup.StartupInfo.dwFlags, process_owner.STARTF_USESTDHANDLES)
            self.assertEqual(startup.StartupInfo.hStdError, 48)
            return True
        api.kernel.CreateProcessW.side_effect = create
        self.create(api)
        api.kernel.DeleteProcThreadAttributeList.assert_called_once()

    def test_no_pipes_does_not_enable_general_handle_inheritance(self):
        api = self.make_api()
        self.create(api, inherited=())
        self.assertEqual(api.kernel.UpdateProcThreadAttribute.call_count, 1)
        self.assertIs(api.kernel.CreateProcessW.call_args.args[4], False)
        api.kernel.AssignProcessToJobObject.assert_not_called()

    def test_attribute_failure_never_falls_back_to_unowned_creation(self):
        api = self.make_api()
        api.kernel.UpdateProcThreadAttribute.return_value = False
        with self.assertRaises(process_owner.ProcessOwnershipError) as caught:
            self.create(api)
        self.assertEqual(caught.exception.code, "child_attributes_update_failed")
        api.kernel.CreateProcessW.assert_not_called()
        api.kernel.DeleteProcThreadAttributeList.assert_called_once()

    def test_creation_failure_releases_attribute_list_and_reports_only_a_bounded_code(self):
        api = self.make_api()
        api.kernel.CreateProcessW.return_value = False
        with self.assertRaises(process_owner.ProcessOwnershipError) as caught:
            self.create(api)
        self.assertEqual(caught.exception.code, "owned_child_create_failed")
        self.assertNotIn("fixture.exe", str(caught.exception))
        api.kernel.CreateProcessW.assert_called_once()
        api.kernel.DeleteProcThreadAttributeList.assert_called_once()

    def test_interrupt_after_handle_transfer_closes_the_process_handle_exactly_once(self):
        child = object.__new__(process_owner._OwnedPopen)
        child._owner_api = Mock()
        child._owner_api.kernel.WaitForSingleObject.return_value = 0
        child._job_handle = 123456
        child._closed_child_pipe_fds = False
        child._filter_handle_list = Mock(return_value=[])
        child._close_pipe_fds = Mock(side_effect=lambda *args: setattr(child, "_closed_child_pipe_fds", True))
        information = Mock(hProcess=1024, hThread=2048)
        type(information).dwProcessId = PropertyMock(side_effect=KeyboardInterrupt)
        handle = Mock()
        with patch.object(process_owner, "_ProcessInformation", return_value=information), \
                patch.object(process_owner.subprocess, "Handle", return_value=handle):
            with self.assertRaises(KeyboardInterrupt):
                child._execute_child(["fixture.exe"], None, None, True, (), None, None,
                    None, CREATE_NO_WINDOW, False, -1, -1, -1, -1, -1, -1,
                    True, None, None, None, -1, False, -1)
        handle.Close.assert_called_once_with()
        child._owner_api.kernel.CloseHandle.assert_called_once_with(2048)
        self.assertFalse(child._child_created)
        child._close_pipe_fds.assert_called_once()


class FakeApi:
    """No Win32 calls: failed ownership must be testable without owning pytest/Python."""

    handle = 123456789

    def __init__(self, fail=None, interrupt_after_assignment=False, membership_unknown=False):
        self.fail = fail
        self.interrupt_after_assignment = interrupt_after_assignment
        self.membership_unknown = membership_unknown
        self.assigned = False
        self.calls = []

    def record(self, name, handle=None):
        self.calls.append(name)
        if handle is not None and handle != self.handle:
            raise AssertionError("The helper used an unexpected fake handle")
        if self.fail == name:
            raise process_owner.ProcessOwnershipError("fixture_" + name + "_failed", 5)

    def create_job(self):
        self.record("create_job")
        return self.handle

    def set_limits(self, handle):
        self.record("set_limits", handle)

    def assert_handle(self, handle):
        self.record("assert_handle", handle)

    def assign_current(self, handle):
        self.record("assign_current", handle)
        self.assigned = True
        if self.interrupt_after_assignment:
            raise KeyboardInterrupt()

    def is_current_member(self, handle):
        self.record("is_current_member", handle)
        if self.membership_unknown:
            raise process_owner.ProcessOwnershipError("fixture_membership_unknown", 5)
        return self.assigned

    def assert_owned(self, handle):
        self.record("assert_owned", handle)
        if not self.assigned:
            raise AssertionError("Ownership asserted before native assignment")

    def assert_child(self, handle, process_handle):
        self.record("assert_child", handle)
        if process_handle != 1234:
            raise AssertionError("Unexpected fake child handle")

    def close_unassigned(self, handle):
        self.record("close_unassigned", handle)
        if self.assigned:
            raise AssertionError("An assigned lifetime handle must never be explicitly closed")


class OwnershipContractTests(unittest.TestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(process_owner, "_owner", None))
        self.patches.enter_context(patch.object(process_owner, "_claim_error", None))
        self.popen = self.patches.enter_context(patch.object(process_owner, "_OwnedPopen"))
        self.popen.return_value._handle = 1234
        self.addCleanup(self.patches.close)

    def use_api(self, api):
        return self.patches.enter_context(patch.object(process_owner, "_new_api", return_value=api))

    def test_claim_is_singleton_and_assignment_precedes_every_spawn(self):
        api = FakeApi()
        factory = self.use_api(api)
        owner = process_owner.claim_process_owner()
        self.assertIs(process_owner.claim_process_owner(), owner)
        self.assertEqual(owner.process_id, os.getpid())
        factory.assert_called_once_with()
        self.assertEqual(api.calls[:5], ["create_job", "set_limits", "assert_handle", "assign_current", "assert_owned"])
        self.popen.side_effect = lambda *args, **kwargs: self.assertTrue(api.assigned) or self.popen.return_value
        self.assertIs(owner.spawn(["fixture-child"]), self.popen.return_value)
        self.assertIn("assert_child", api.calls)
        args, kwargs = self.popen.call_args
        self.assertEqual(args, (["fixture-child"],))
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["close_fds"], True)
        self.assertIs(kwargs["owner"], owner)
        self.assertEqual(kwargs["creationflags"], CREATE_NO_WINDOW)
        self.assertNotIn("close_unassigned", api.calls)

    def test_factory_failure_is_latched_without_a_handle_or_child(self):
        failure = process_owner.ProcessOwnershipError("fixture_factory_failed", 5)
        factory = self.patches.enter_context(patch.object(process_owner, "_new_api", side_effect=failure))
        for _ in range(2):
            with self.assertRaises(process_owner.ProcessOwnershipError) as caught:
                process_owner.claim_process_owner()
            self.assertIs(caught.exception, failure)
        self.assertIsNone(process_owner._owner)
        factory.assert_called_once_with()
        self.popen.assert_not_called()

    def test_failed_claim_closes_only_a_confirmed_unassigned_handle(self):
        for stage in ["create_job", "set_limits", "assert_handle", "assign_current"]:
            with self.subTest(stage=stage), patch.object(process_owner, "_owner", None), patch.object(process_owner, "_claim_error", None):
                api = FakeApi(fail=stage)
                with patch.object(process_owner, "_new_api", return_value=api) as factory:
                    for _ in range(2):
                        with self.assertRaises(process_owner.ProcessOwnershipError):
                            process_owner.claim_process_owner()
                    self.assertIsNone(process_owner._owner)
                    factory.assert_called_once_with()
                if stage == "create_job":
                    self.assertNotIn("close_unassigned", api.calls)
                else:
                    self.assertEqual(api.calls[-2:], ["is_current_member", "close_unassigned"])
                    self.assertEqual(api.calls.count("close_unassigned"), 1)
        self.popen.assert_not_called()

    def test_post_assignment_failure_retains_handle_but_cannot_spawn(self):
        api = FakeApi(fail="assert_owned")
        self.use_api(api)
        with self.assertRaises(process_owner.ProcessOwnershipError):
            process_owner.claim_process_owner()
        retained = process_owner._owner
        self.assertIsNotNone(retained)
        self.assertFalse(retained._ready)
        with self.assertRaises(process_owner.ProcessOwnershipError):
            retained.spawn(["must-not-launch"])
        with self.assertRaises(process_owner.ProcessOwnershipError):
            process_owner.claim_process_owner()
        self.assertNotIn("close_unassigned", api.calls)
        self.popen.assert_not_called()

    def test_interrupt_immediately_after_assignment_never_closes_the_job(self):
        api = FakeApi(interrupt_after_assignment=True)
        self.use_api(api)
        with self.assertRaises(KeyboardInterrupt):
            process_owner.claim_process_owner()
        self.assertTrue(api.assigned)
        self.assertIsNotNone(process_owner._owner)
        self.assertFalse(process_owner._owner._ready)
        self.assertIn("is_current_member", api.calls)
        self.assertNotIn("close_unassigned", api.calls)
        with self.assertRaises(process_owner.ProcessOwnershipError):
            process_owner.claim_process_owner()
        self.popen.assert_not_called()

    def test_unknown_membership_retains_handle_and_fails_closed(self):
        api = FakeApi(fail="set_limits", membership_unknown=True)
        self.use_api(api)
        with self.assertRaises(process_owner.ProcessOwnershipError) as caught:
            process_owner.claim_process_owner()
        self.assertEqual(caught.exception.code, "fixture_membership_unknown")
        self.assertIsNotNone(process_owner._owner)
        self.assertFalse(process_owner._owner._ready)
        self.assertNotIn("close_unassigned", api.calls)
        self.popen.assert_not_called()

    def test_owner_rechecks_membership_and_pid_before_spawning(self):
        api = FakeApi()
        self.use_api(api)
        owner = process_owner.claim_process_owner()
        api.fail = "assert_owned"
        with self.assertRaises(process_owner.ProcessOwnershipError):
            owner.spawn(["must-not-launch"])
        api.fail = None
        with patch.object(owner, "_process_id", owner.process_id + 1):
            with self.assertRaises(process_owner.ProcessOwnershipError):
                owner.assert_owned()
            with self.assertRaises(process_owner.ProcessOwnershipError):
                owner.spawn(["must-not-launch"])
        self.popen.assert_not_called()

    def test_spawn_guards_never_reach_popen_and_codes_do_not_echo_arguments(self):
        api = FakeApi()
        self.use_api(api)
        owner = process_owner.claim_process_owner()
        for arguments, options in [
            ([], {}), ("SECRET_ARGUMENT_TEXT", {}),
            (["SECRET_ARGUMENT_TEXT"], {"shell": True}),
            (["SECRET_ARGUMENT_TEXT"], {"close_fds": False}),
            (["SECRET_ARGUMENT_TEXT"], {"startupinfo": object()}),
            (["SECRET_ARGUMENT_TEXT"], {"creationflags": 0x01000000}),
            (["SECRET_ARGUMENT_TEXT"], {"creationflags": -1}),
            (["SECRET_ARGUMENT_TEXT"], {"creationflags": "0"}),
            (["SECRET_ARGUMENT_TEXT"], {"creationflags": process_owner.CREATE_SUSPENDED}),
            (["SECRET_ARGUMENT_TEXT"], {"creationflags": process_owner.EXTENDED_STARTUPINFO_PRESENT}),
        ]:
            with self.subTest(options=list(options)), self.assertRaises(process_owner.ProcessOwnershipError) as caught:
                owner.spawn(arguments, **options)
            self.assertIsInstance(caught.exception.code, str)
            self.assertNotIn("SECRET_ARGUMENT_TEXT", str(caught.exception))
        self.popen.assert_not_called()

    def test_lifetime_owner_has_no_early_close_protocol(self):
        for name in ["close", "__enter__", "__exit__", "__del__"]:
            self.assertFalse(hasattr(process_owner.ProcessOwner, name), name)

    def test_failed_child_membership_kills_only_created_handle_and_closes_pipes(self):
        api = FakeApi(fail="assert_child")
        self.use_api(api)
        owner = process_owner.claim_process_owner()
        child = self.popen.return_value
        child.poll.return_value = None
        with self.assertRaises(process_owner.ProcessOwnershipError) as caught:
            owner.spawn(["fixture-child"])
        self.assertEqual(caught.exception.code, "child_ownership_not_verified")
        child.kill.assert_called_once_with()
        child.wait.assert_called_once_with(timeout=5)
        for stream in [child.stdin, child.stdout, child.stderr]:
            stream.close.assert_called_once_with()
        self.assertNotIn("close_unassigned", api.calls)

    def test_failed_child_cleanup_preserves_fixed_error_and_attempts_each_pipe(self):
        api = FakeApi(fail="assert_child")
        self.use_api(api)
        owner = process_owner.claim_process_owner()
        child = self.popen.return_value
        child.poll.return_value = None
        child.stdin.close.side_effect = OSError("PRIVATE_FIXTURE_DETAIL")
        with self.assertRaises(process_owner.ProcessOwnershipError) as caught:
            owner.spawn(["fixture-child"])
        self.assertEqual(caught.exception.code, "unowned_child_cleanup_failed")
        self.assertNotIn("PRIVATE_FIXTURE_DETAIL", str(caught.exception))
        child.stdout.close.assert_called_once_with()
        child.stderr.close.assert_called_once_with()

    def test_fixture_pid_reuse_refuses_termination_of_mismatched_process(self):
        api = Mock()
        api.OpenProcess.return_value = 123
        with patch(__name__ + ".creation_ticks", return_value=2):
            with self.assertRaisesRegex(AssertionError, "identity or lifetime changed"):
                retain_fixture_process(api, 456, 1)
        api.CloseHandle.assert_called_once_with(123)
        api.TerminateProcess.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
