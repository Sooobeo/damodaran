"""Windows working-set limits for a separate, owned comparison process only.

V2 leaves the private lifetime Job unchanged: Job working-set settings failed
with Windows error 1314 in the preserved V1 fixture. The comparison-only
suspended launcher calls apply_before_resume(handle, pid), then resumes only
after successful PID/ownership/limit readback. Suspension is the launcher's
contract and is not inferred from a process handle by this module. apply_child
only verifies the registered identity and does not set the limit again.
Any API failure is fatal; the launcher/caller must stop/wait its owned child.
No retry, elevation,
global process search, environment edit, model load or process spawn occurs here.

These limits are NOT an aggregate Job RAM cap, a commit cap, or an OS/file-cache
cap. WorkingSet/PageFaultCount include shared pages/soft faults respectively.
PrivateUsage measures committed private bytes, not private resident memory.
Paging changes residency, not stored model precision; successful model execution
and system responsiveness still require separate observations and guards.

Official contracts (read 2026-09-10):
https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-setprocessworkingsetsizeex
https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters_ex
https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-prefetchvirtualmemory
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-hymt"))
import process_owner

VERSION = "owned-working-set-limit-v2"
JOB_OBJECT_LIMIT_WORKINGSET = 0x00000001
HARDWS_MIN_DISABLE = 0x00000002
HARDWS_MAX_ENABLE = 0x00000004
HARDWS_FLAGS = HARDWS_MIN_DISABLE | HARDWS_MAX_ENABLE


class WorkingSetLimitError(RuntimeError):
    """Bounded diagnostics without commands, environment values or source text."""

    def __init__(self, code: str, winerror: int | None = None):
        self.code, self.winerror = code, winerror
        super().__init__(code if winerror is None else f"{code} (Windows error {winerror})")


class _MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
            "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


class _Native:
    def __init__(self):
        if os.name != "nt" or sys.version_info[:2] != (3, 11):
            raise WorkingSetLimitError("windows_cpython311_required")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.psapi = ctypes.WinDLL("psapi", use_last_error=True)
        signatures = {
            "GetProcessId": ([wintypes.HANDLE], wintypes.DWORD),
            "GetProcessTimes": ([wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4], wintypes.BOOL),
            "SetProcessWorkingSetSizeEx": ([wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD], wintypes.BOOL),
            "GetProcessWorkingSetSizeEx": ([wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]
        self.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    @staticmethod
    def fail(code):
        raise WorkingSetLimitError(code, ctypes.get_last_error())

    def query_job(self, handle):
        limits = process_owner._ExtendedLimits()
        if not self.kernel.QueryInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits), None):
            self.fail("job_working_set_query_failed")
        return limits

    def query_process(self, handle):
        minimum, maximum, flags = ctypes.c_size_t(), ctypes.c_size_t(), wintypes.DWORD()
        if not self.kernel.GetProcessWorkingSetSizeEx(handle, ctypes.byref(minimum), ctypes.byref(maximum), ctypes.byref(flags)):
            self.fail("process_working_set_query_failed")
        return {"minimumBytes": minimum.value, "maximumBytes": maximum.value, "flags": flags.value,
                "hardMaximumEnabled": bool(flags.value & HARDWS_MAX_ENABLE),
                "hardMinimumDisabled": bool(flags.value & HARDWS_MIN_DISABLE)}


def _job_record(limits):
    basic = limits.BasicLimitInformation
    return {"scope": "per_process_in_private_job", "flags": int(basic.LimitFlags),
            "minimumBytes": int(basic.MinimumWorkingSetSize), "maximumBytes": int(basic.MaximumWorkingSetSize),
            "preservedBasicLimits": {name: int(getattr(basic, name)) for name in (
                "PerProcessUserTimeLimit", "PerJobUserTimeLimit", "ActiveProcessLimit",
                "Affinity", "PriorityClass", "SchedulingClass")},
            "processCommitLimitBytes": int(limits.ProcessMemoryLimit),
            "jobCommitLimitBytes": int(limits.JobMemoryLimit)}


def _validate_sizes(minimum_bytes, maximum_bytes):
    if (type(minimum_bytes) is not int or type(maximum_bytes) is not int
            or minimum_bytes < 1024 * 1024 or maximum_bytes < 16 * 1024 * 1024
            or minimum_bytes > maximum_bytes or minimum_bytes % 4096 or maximum_bytes % 4096):
        raise WorkingSetLimitError("invalid_working_set_bounds")


class WorkingSetLimit:
    """Explicit process cap for a suspended launcher; no spawn or handle closure.

    Requested API max and observed resident budget must be reported separately.
    The first 128 MiB API fixture observed 128 MiB + 4096 bytes despite flags6;
    callers therefore reserve headroom below their separately enforced budget.
    This class does not silently add tolerance to its exact API readback.
    """

    def __init__(self, owner, *, maximum_bytes: int, minimum_bytes: int = 1024**2):
        _validate_sizes(minimum_bytes, maximum_bytes)
        if not isinstance(owner, process_owner.ProcessOwner):
            raise WorkingSetLimitError("verified_process_owner_required")
        owner.assert_owned()
        self.owner, self.minimum_bytes, self.maximum_bytes = owner, minimum_bytes, maximum_bytes
        self._native, self._children = _Native(), {}
        limits = self._native.query_job(owner._handle)
        before = _job_record(limits)
        if before["flags"] & JOB_OBJECT_LIMIT_WORKINGSET:
            raise WorkingSetLimitError("private_job_already_working_set_limited")
        self.job_record = {"version": VERSION, "observed": before, "jobLimitsChanged": False,
                           "workingSetLimitAppliedToJob": False, "aggregateRamCap": False,
                           "commitCap": False, "osFileCacheCap": False}

    def _native_identity(self, handle, expected_pid):
        self.owner.assert_owned()
        if type(handle) is not int or handle <= 0 or type(expected_pid) is not int or expected_pid <= 0:
            raise WorkingSetLimitError("invalid_child_handle_or_pid")
        self.owner._api.assert_child(self.owner._handle, handle)
        pid = self._native.kernel.GetProcessId(handle)
        if not pid:
            self._native.fail("child_process_id_query_failed")
        if pid != expected_pid or pid == os.getpid():
            raise WorkingSetLimitError("child_process_id_mismatch")
        times = [wintypes.FILETIME() for _ in range(4)]
        if not self._native.kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            self._native.fail("child_process_time_query_failed")
        return handle, {"pid": pid, "creationTicks": times[0].dwHighDateTime << 32 | times[0].dwLowDateTime}

    def _identity(self, child):
        if not isinstance(child, subprocess.Popen) or child.poll() is not None:
            raise WorkingSetLimitError("live_owned_child_required")
        return self._native_identity(int(child._handle), child.pid)

    def _checked_limits(self, handle):
        limits = self._native.query_process(handle)
        if (limits["minimumBytes"] != self.minimum_bytes or limits["maximumBytes"] != self.maximum_bytes
                or limits["flags"] != HARDWS_FLAGS):
            raise WorkingSetLimitError("child_hard_working_set_readback_mismatch")
        return limits

    def apply_before_resume(self, process_handle, process_id):
        handle, identity = self._native_identity(process_handle, process_id)
        if handle in self._children:
            raise WorkingSetLimitError("child_limit_already_registered")
        before = self._native.query_process(handle)
        if not self._native.kernel.SetProcessWorkingSetSizeEx(handle, self.minimum_bytes, self.maximum_bytes, HARDWS_FLAGS):
            self._native.fail("child_hard_working_set_set_failed")
        after = self._checked_limits(handle)
        receipt = {"version": VERSION, **identity, "before": before, "after": after,
                   "jobWorkingSetLimitApplied": False, "suspensionMustBeVerifiedByLauncher": True}
        self._children[handle] = (identity, receipt)
        return receipt

    def apply_child(self, child):
        handle, identity = self._identity(child)
        previous = self._children.get(handle)
        if previous is None or previous[0] != identity:
            raise WorkingSetLimitError("child_limit_not_applied_to_identity")
        self._checked_limits(handle)
        return previous[1]

    def sample_child(self, child):
        handle, identity = self._identity(child)
        previous = self._children.get(handle)
        if previous is None or previous[0] != identity:
            raise WorkingSetLimitError("child_limit_not_applied_to_identity")
        limits = self._checked_limits(handle)
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not self._native.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), ctypes.sizeof(counters)):
            self._native.fail("child_memory_query_failed")
        return {**identity, "monotonicSeconds": time.monotonic(),
                "workingSetBytes": int(counters.WorkingSetSize),
                "peakWorkingSetBytes": int(counters.PeakWorkingSetSize),
                "privateBytes": int(counters.PrivateUsage),
                "pageFaultCount": int(counters.PageFaultCount), "workingSetLimits": limits}
