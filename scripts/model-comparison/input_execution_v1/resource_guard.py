"""Hy7 S4 resource policy; no model, process, lock or power request on import.

The named mutex excludes cooperating experiment producers. Unmodified app
workers do not participate: process snapshots detect that race, never prove an
OS-wide prohibition on other model starts. Only the owned native child is killed.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
sys.path.insert(0, str(ROOT / "scripts/local-hymt"))
import process_owner
from working_set_limit import WorkingSetLimit
from suspended_process_owner import SuspendedProcessOwner

VERSION = "hy7-input-execution-resource-guard-v1"
GIB = 1024 ** 3
BELOW_NORMAL = 0x4000
CREATE_NO_WINDOW = 0x08000000
PROFILE = {
    "profileId": "hy7-cpu4-context8192-observed-ws8-v1",
    "parallelModelSlots": 1, "threads": 4, "contextSize": 8192,
    "maxOutputTokens": 4096, "maxPromptTokens": 4095, "cpuOnly": True,
    "minimumAvailablePhysicalBytes": 11 * GIB,
    "minimumAvailableCommitBytes": 3 * GIB,
    "requestedNativeMaximumWorkingSetBytes": 8 * GIB - 64 * 1024 ** 2,
    "maximumObservedNativeWorkingSetBytes": 8 * GIB,
    "priorityClass": "BelowNormal", "requiresACLineStatus": 1,
    "preflightObservations": 2, "preflightSeparationSeconds": 3,
    "observeSeconds": 0.5, "recordSeconds": 5, "processScanSeconds": 5,
    "lowPhysicalBytes": 512 * 1024 ** 2, "lowCommitBytes": 512 * 1024 ** 2,
    "sustainedLowSeconds": 3, "startupSeconds": 240,
    "requestSeconds": 1800, "totalSeconds": 8 * 60 * 60,
    "independentLowTimers": True, "aggregateRamCap": False,
    "commitCap": False, "osFileCacheCap": False,
    "operationalWorkerParticipatesInMutex": False,
    "allModelStartsPreventedByMutex": False,
}
MUTEX_NAME = "Local\\MoonModaran.ExclusiveLocalModelSlot.V1"
NATIVE_NAMES = frozenset({"llama-server.exe", "llama-cli.exe", "llama-tokenize.exe",
                          "llamafile.exe", "ollama.exe", "ollama_llama_server.exe"})


class ResourceGuardError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise ResourceGuardError(code)


def _kernel():
    require(os.name == "nt", "windows_required")
    return ctypes.WinDLL("kernel32", use_last_error=True)


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
        (name, ctypes.c_ulonglong) for name in (
            "totalPhysical", "availablePhysical", "totalCommit", "availableCommit",
            "totalVirtual", "availableVirtual", "availableExtendedVirtual")]


class _PowerStatus(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ubyte) for name in
                ("ACLineStatus", "BatteryFlag", "BatteryLifePercent", "SystemStatusFlag")] + [
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


def system_state():
    kernel = _kernel()
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatus)]
    kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
    kernel.GetSystemPowerStatus.argtypes = [ctypes.POINTER(_PowerStatus)]
    kernel.GetSystemPowerStatus.restype = wintypes.BOOL
    memory = _MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    power = _PowerStatus()
    require(kernel.GlobalMemoryStatusEx(ctypes.byref(memory)), "memory_query_failed")
    require(kernel.GetSystemPowerStatus(ctypes.byref(power)), "power_query_failed")
    return {"availablePhysicalBytes": int(memory.availablePhysical),
            "availableCommitBytes": int(memory.availableCommit),
            "totalPhysicalBytes": int(memory.totalPhysical),
            "totalCommitBytes": int(memory.totalCommit),
            "commitSource": "GlobalMemoryStatusEx.ullAvailPageFile",
            "ACLineStatus": int(power.ACLineStatus), "BatteryFlag": int(power.BatteryFlag)}


def native_processes(exclude_pids=()):
    kernel = _kernel()
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        getattr(kernel, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
        getattr(kernel, name).restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    require(handle not in (None, ctypes.c_void_p(-1).value), "process_snapshot_failed")
    excluded, matches, count = set(exclude_pids), [], 0
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        require(kernel.Process32FirstW(handle, ctypes.byref(entry)), "process_snapshot_first_failed")
        while True:
            count += 1
            name = entry.szExeFile.lower()
            if name in NATIVE_NAMES and entry.th32ProcessID not in excluded:
                matches.append({"pid": int(entry.th32ProcessID), "name": name})
            if not kernel.Process32NextW(handle, ctypes.byref(entry)):
                require(ctypes.get_last_error() == 18, "process_snapshot_next_failed")
                break
    finally:
        kernel.CloseHandle(handle)
    return {"snapshotProcessCount": count, "nativeConflicts": matches}


def classify_process_rows(rows, exclude_pids=()):
    """Pure classifier; command lines are consumed transiently, never returned."""
    require(isinstance(rows, list), "process_query_invalid_rows")
    excluded = {os.getpid(), *exclude_pids}
    conflicts, unrelated, unknown = [], 0, 0
    root = str(ROOT).replace("/", "\\").lower()
    for row in rows:
        require(isinstance(row, dict) and type(row.get("pid")) is int
                and isinstance(row.get("name"), str), "process_query_invalid_row")
        if row["pid"] in excluded:
            continue
        name = row["name"].lower()
        command = row.get("command")
        require(command is None or isinstance(command, str), "process_query_invalid_command_type")
        command = (command or "").replace("/", "\\").lower()
        project = root in command
        worker = bool(re.search(r"(?:^|[\\\s\"'])worker\\(?:index|main)\.(?:ts|js)(?:[\s\"']|$)", command))
        native = name in NATIVE_NAMES
        model = bool(re.search(r"(?:scripts\\local-hymt\\bridge\.py(?:[\s\"']|$)|"
                               r"scripts\\model-training\\(?:train(?:_v5)?|infer(?:_v5)?|bridge|evaluate_v4)\.py(?:[\s\"']|$)|"
                               r"scripts\\local-qe\\(?:evaluate_llm_review(?:_v[23])?|llm-qwen35\\runtime(?:_v[2-5])?)\.py(?:[\s\"']|$)|"
                               r"(?:^|[\\\s\"'])run_(?:hymt|translategemma|general_context)[^\s\"']*\.py|"
                               r"(?:^|[\s\"'])[^\s\"']+\.gguf(?:[\s\"']|$)|"
                               r"scripts\\model-comparison\\input_preparation_v1\\vocab_worker\.py(?:[\s\"']|$))", command))
        if native or worker or model:
            conflicts.append({"pid": row["pid"], "name": name, "projectRelated": project,
                              "productionWorker": worker, "modelProcess": model,
                              "nativeProcess": native, "commandLineAvailable": bool(command)})
        elif command:
            unrelated += 1
        else:
            unknown += 1
    return {"classifiedConflicts": conflicts, "unrelatedCandidateCount": unrelated,
            "unclassifiedCandidateCount": unknown,
            "unclassifiedNodeIsAutomaticConflict": False,
            "completeAllModelAbsenceProved": False}


def process_state(exclude_pids=()):
    native = native_processes(exclude_pids)
    # Bound the read-only helper lifetime. It prints only selected process data,
    # consumed here and then dropped; neither command lines nor executable paths
    # are included in any receipt. No native/model launch is performed.
    script = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
              "$r=@(Get-CimInstance Win32_Process | Where-Object { "
              "$_.Name -match '^(python(?:w|[0-9.]*)?|node|tsx|llama[^.]*|llamafile|ollama(?:_llama_server)?)\\.exe$' "
              "} | ForEach-Object { @{pid=[int]$_.ProcessId;name=$_.Name;command=$_.CommandLine} }); "
              "ConvertTo-Json -InputObject $r -Compress -Depth 4")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                                 "-Command", script], capture_output=True, timeout=10,
                                creationflags=CREATE_NO_WINDOW, check=False)
        require(result.returncode == 0, "cim_process_query_failed")
        require(len(result.stdout) <= 4 * 1024 ** 2, "cim_process_query_too_large")
        rows = json.loads(result.stdout.decode("utf-8-sig"))
    except ResourceGuardError:
        raise
    except Exception:
        raise ResourceGuardError("cim_process_query_failed") from None
    return {"querySucceeded": True, **native, **classify_process_rows(rows, exclude_pids)}


def preflight(exclude_pids=()):
    memory, processes = system_state(), process_state(exclude_pids)
    failures = []
    if memory["ACLineStatus"] != 1:
        failures.append("ac_power_required")
    if memory["availablePhysicalBytes"] < PROFILE["minimumAvailablePhysicalBytes"]:
        failures.append("insufficient_available_physical_memory")
    if memory["availableCommitBytes"] < PROFILE["minimumAvailableCommitBytes"]:
        failures.append("insufficient_available_commit_memory")
    if processes["nativeConflicts"] or processes["classifiedConflicts"]:
        failures.append("model_or_worker_conflict")
    return {"version": VERSION, "profileId": PROFILE["profileId"],
            "checkedAtUtc": datetime.now(timezone.utc).isoformat(), "monotonicSeconds": time.monotonic(),
            "system": memory, "processes": processes, "passed": not failures, "failures": failures}


def require_preflight(receipt):
    require(receipt.get("profileId") == PROFILE["profileId"] and receipt.get("passed") is True,
            (receipt.get("failures") or ["resource_preflight_invalid"])[0])


def preflight_pair(exclude_pids=()):
    first = preflight(exclude_pids)
    # Return both fresh observations even when the first one fails. No model is
    # started here and no old passing receipt can satisfy this call.
    time.sleep(PROFILE["preflightSeparationSeconds"])
    second = preflight(exclude_pids)
    return {"observations": [first, second], "passed": first["passed"] and second["passed"]}


class ExperimentLock:
    """Non-inheritable named Windows mutex, released on scope exit/crash."""
    _active = False

    def __init__(self):
        self.handle = None
        self.thread_id = None
        self.receipt = None

    def __enter__(self):
        require(not ExperimentLock._active, "experiment_slot_already_held_in_process")
        kernel = self.kernel = _kernel()
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.ReleaseMutex.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
        require(bool(handle), "experiment_mutex_create_failed")
        result = kernel.WaitForSingleObject(handle, 0)
        if result not in (0, 0x80):
            kernel.CloseHandle(handle)
            raise ResourceGuardError("experiment_slot_busy" if result == 0x102 else "experiment_mutex_wait_failed")
        self.handle, self.thread_id = handle, threading.get_ident()
        ExperimentLock._active = True
        self.receipt = {"name": MUTEX_NAME, "exclusiveExperimentSlot": True,
                        "abandonedPreviousOwner": result == 0x80, "handleInheritable": False,
                        "operationalWorkerParticipates": False, "globalStartPrevention": False}
        return self

    def __exit__(self, *args):
        if self.handle is not None:
            require(threading.get_ident() == self.thread_id, "experiment_mutex_wrong_thread_release")
            try:
                require(self.kernel.ReleaseMutex(self.handle), "experiment_mutex_release_failed")
            finally:
                self.kernel.CloseHandle(self.handle)
                self.handle = None
                ExperimentLock._active = False


class PowerRequest:
    """Temporary display+system request, cleared on its owning thread; no settings."""
    def __init__(self):
        self.active, self.thread_id = False, None
        self.receipt = {"requested": False, "released": False, "globalSettingsChanged": False,
                        "manualSleepPrevented": False, "displayAndSystem": True}

    def __enter__(self):
        require(system_state()["ACLineStatus"] == 1, "ac_power_required")
        self.kernel = _kernel()
        self.kernel.SetThreadExecutionState.argtypes = [wintypes.DWORD]
        self.kernel.SetThreadExecutionState.restype = wintypes.DWORD
        require(self.kernel.SetThreadExecutionState(0x80000003), "temporary_power_request_failed")
        self.active, self.thread_id = True, threading.get_ident()
        self.receipt["requested"] = True
        return self

    def __exit__(self, *args):
        if self.active:
            require(threading.get_ident() == self.thread_id, "power_request_wrong_thread_release")
            require(self.kernel.SetThreadExecutionState(0x80000000), "temporary_power_release_failed")
            self.active = False
            self.receipt["released"] = True


class _PriorityCheckedLimiter(WorkingSetLimit):
    def apply_before_resume(self, process_handle, process_id):
        result = super().apply_before_resume(process_handle, process_id)
        kernel = self._native.kernel
        kernel.GetPriorityClass.argtypes = [wintypes.HANDLE]
        kernel.GetPriorityClass.restype = wintypes.DWORD
        priority = int(kernel.GetPriorityClass(process_handle))
        require(priority == BELOW_NORMAL, "suspended_child_priority_not_below_normal")
        result["priorityClassBeforeResume"] = priority
        return result

    def sample_child(self, child):
        result = super().sample_child(child)
        priority = int(self._native.kernel.GetPriorityClass(int(child._handle)))
        require(priority == BELOW_NORMAL, "child_priority_not_below_normal")
        result["priorityClass"] = priority
        return result


def spawn_guarded(owner, command, log_handle, env, cwd):
    require(ExperimentLock._active, "experiment_slot_required_before_spawn")
    owner.assert_owned()
    limiter = _PriorityCheckedLimiter(owner, maximum_bytes=PROFILE["requestedNativeMaximumWorkingSetBytes"])
    suspended = SuspendedProcessOwner(owner, limiter)
    process = None
    try:
        process = suspended.spawn(command, stdin=subprocess.DEVNULL, stdout=log_handle,
                                  stderr=subprocess.STDOUT, cwd=cwd, env=env,
                                  creationflags=BELOW_NORMAL | CREATE_NO_WINDOW)
        receipt = dict(suspended.creation_receipt)
        require(receipt.get("resumePreviousCount") == 1 and receipt.get("limitAppliedBeforeResume") is True,
                "suspended_spawn_receipt_invalid")
        receipt["afterResume"] = limiter.sample_child(process)
        receipt["childOwnedJobVerified"] = True
        return process, limiter, receipt
    except BaseException:
        if process is not None:
            stop_owned(process, owner)
        raise


def stop_owned(process, owner):
    """Terminate only retained hProcess after Job/PID verification; never PID kill."""
    if process is None:
        return {"stopped": True, "processCreated": False}
    owner.assert_owned()
    owner._api.assert_child(owner._handle, int(process._handle))
    if process.poll() is None:
        process.kill()
    try:
        code = process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        raise ResourceGuardError("owned_child_did_not_stop") from None
    return {"stopped": process.poll() is not None, "pid": process.pid,
            "exitCode": code, "terminationByRetainedHandle": True}


class LowResourceTimers:
    def __init__(self):
        self.low_since = {"physical": None, "commit": None}
        self.last_time = None

    def observe(self, state, now):
        require(type(now) in (int, float) and (self.last_time is None or now >= self.last_time),
                "memory_observation_time_invalid")
        self.last_time = now
        for kind, field, threshold in (("physical", "availablePhysicalBytes", PROFILE["lowPhysicalBytes"]),
                                       ("commit", "availableCommitBytes", PROFILE["lowCommitBytes"])):
            value = state.get(field)
            require(type(value) is int and value >= 0, "memory_observation_invalid")
            if value < threshold:
                if self.low_since[kind] is None:
                    self.low_since[kind] = now
                if now - self.low_since[kind] >= PROFILE["sustainedLowSeconds"]:
                    return "sustained_low_" + kind
            else:
                self.low_since[kind] = None
        return None


class ResourceMonitor:
    def __init__(self, process, limiter, path, exclude_pids=(), run_started=None):
        self.process, self.limiter = process, limiter
        self.exclude_pids = {os.getpid(), process.pid, *exclude_pids}
        self.path = Path(path)
        self.stream = self.path.open("x", encoding="utf-8", newline="\n")
        self.run_started = time.monotonic() if run_started is None else run_started
        self.startup_deadline = time.monotonic() + PROFILE["startupSeconds"]
        self.request_deadline = None
        self.stop_event, self.lock, self.observation_lock = threading.Event(), threading.Lock(), threading.Lock()
        self.thread = threading.Thread(target=self._run, name="hy7-s4-resource-guard", daemon=True)
        self.process_thread = threading.Thread(target=self._scan_processes, name="hy7-s4-process-guard", daemon=True)
        self.abort_reason = self.monitor_error = self.kill_error = None
        self.timers = LowResourceTimers()
        self.observations = self.records = self.process_scans = 0
        self.last_recorded = None
        self.extrema = {"minimumAvailablePhysicalBytes": None, "minimumAvailableCommitBytes": None,
                        "maximumChildWorkingSetBytes": None, "maximumChildPrivateBytes": None}

    def _abort(self, reason):
        with self.lock:
            self.abort_reason = self.abort_reason or reason
        if self.process.poll() is None:
            try:
                self.limiter.owner.assert_owned()
                self.limiter.owner._api.assert_child(self.limiter.owner._handle, int(self.process._handle))
                self.process.kill()
            except Exception as error:
                self.kill_error = type(error).__name__

    def _observe(self, state, now):
        child = state.get("child", {})
        require(all(type(child.get(k)) is int and child[k] >= 0 for k in
                    ("workingSetBytes", "peakWorkingSetBytes", "privateBytes", "pageFaultCount")),
                "child_memory_sample_invalid")
        with self.lock:
            startup, request = self.startup_deadline, self.request_deadline
        reason = ("ac_power_lost_or_unknown" if state.get("ACLineStatus") != 1 else
                  "total_time_limit" if now - self.run_started >= PROFILE["totalSeconds"] else
                  "startup_time_limit" if startup is not None and now >= startup else
                  "request_time_limit" if request is not None and now >= request else
                  "working_set_budget_exceeded" if max(child["workingSetBytes"], child["peakWorkingSetBytes"])
                  > PROFILE["maximumObservedNativeWorkingSetBytes"] else self.timers.observe(state, now))
        self.observations += 1
        for key, value, choose in (
            ("minimumAvailablePhysicalBytes", state["availablePhysicalBytes"], min),
            ("minimumAvailableCommitBytes", state["availableCommitBytes"], min),
            ("maximumChildWorkingSetBytes", child["workingSetBytes"], max),
            ("maximumChildPrivateBytes", child["privateBytes"], max)):
            old = self.extrema[key]
            self.extrema[key] = value if old is None else choose(old, value)
        if reason or self.last_recorded is None or now - self.last_recorded >= PROFILE["recordSeconds"]:
            self.stream.write(json.dumps({"seconds": round(now - self.run_started, 3), **state}) + "\n")
            self.stream.flush()
            self.records += 1
            self.last_recorded = now
        if reason:
            self._abort(reason)

    def sample_and_check(self):
        with self.observation_lock:
            state = {**system_state(), "child": self.limiter.sample_child(self.process)}
            self._observe(state, time.monotonic())
        self.check()
        return state

    def _run(self):
        while not self.stop_event.wait(PROFILE["observeSeconds"]):
            if self.process.poll() is not None:
                return
            try:
                self.sample_and_check()
            except Exception as error:
                if self.abort_reason is None:
                    self.monitor_error = type(error).__name__
                self._abort("resource_monitor_failed")
                return

    def _scan_processes(self):
        while not self.stop_event.wait(PROFILE["processScanSeconds"]):
            if self.process.poll() is not None:
                return
            try:
                result = process_state(self.exclude_pids)
                self.process_scans += 1
                if result["nativeConflicts"] or result["classifiedConflicts"]:
                    self._abort("model_or_worker_started_during_run")
                    return
            except Exception as error:
                self.monitor_error = type(error).__name__
                self._abort("process_monitor_failed")
                return

    def start(self):
        self.sample_and_check()
        self.thread.start()
        self.process_thread.start()
        return self

    def ready(self):
        self.check()
        with self.lock:
            self.startup_deadline = None

    def begin_request(self):
        self.sample_and_check()
        with self.lock:
            self.request_deadline = time.monotonic() + PROFILE["requestSeconds"]

    def end_request(self):
        with self.lock:
            self.request_deadline = None
        self.check()

    def check(self):
        require(self.abort_reason is None, self.abort_reason or "resource_monitor_failed")
        require(self.process.poll() is None, "runtime_exited")

    def stop(self):
        self.stop_event.set()
        for thread in (self.thread, self.process_thread):
            if thread.ident is not None:
                thread.join(timeout=12)
            require(not thread.is_alive(), "resource_monitor_did_not_stop")
        if not self.stream.closed:
            try:
                self.stream.flush()
                os.fsync(self.stream.fileno())
            finally:
                self.stream.close()

    close = stop

    def summary(self):
        return {"version": VERSION, "abortReason": self.abort_reason, "monitorError": self.monitor_error,
                "ownedChildKillError": self.kill_error, "observations": self.observations,
                "recordedSamples": self.records, "processScans": self.process_scans, **self.extrema,
                "sampleFile": self.path.name, "profile": PROFILE,
                "pageFaultCounterScope": "raw DWORD counter; soft and hard faults combined; may wrap",
                "mutexDoesNotBlockUnmodifiedWorker": True,
                "processRaceWindowSeconds": "up to scan interval plus bounded query latency; no absolute guarantee"}

    receipt = summary
