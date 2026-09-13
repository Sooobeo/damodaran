"""Own a Windows bridge and its descendants for the bridge's entire lifetime.

Call claim_process_owner() before creating a native server, then use owner.spawn().
The bridge first joins a new unnamed Job Object whose handle is not inherited.
Children are explicitly assigned job membership during CreateProcessW, not
after starting or by relying on inheritance. If the bridge is forcibly
terminated, Windows closes the sole job handle and terminates its descendants.
The returned native child handle must be a verified member. This is a bounded
launcher for the registered native server, not a general redirector contract.
An unverified returned process is terminated through its own Popen handle and refused.
CPython 3.11's venv parent has its own kill-on-close job: launcher and actual
bridge PIDs differ, so callers must not use Popen.pid as the bridge's identity.

There is deliberately no close(), context manager, __del__, or atexit callback:
closing the job while the bridge is alive would also terminate the bridge and
skip the remainder of its finally blocks. Normal shutdown must terminate/wait
for owned children, finish cleanup, then let Python exit. Windows releases the
handle at process exit. Do not duplicate or manually close this private handle.

No server, model, network connection, or application state is created on import.
Official contracts:
https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs
https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-createjobobjectw
https://learn.microsoft.com/en-us/windows/win32/procthread/terminating-a-process
https://github.com/python/cpython/blob/v3.11.9/PC/launcher.c
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import threading


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
HANDLE_FLAG_INHERIT = 0x00000001
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
STARTF_USESTDHANDLES = 0x00000100


class ProcessOwnershipError(RuntimeError):
    """A bounded error code; messages never contain commands or environment data."""

    def __init__(self, code: str, winerror: int | None = None):
        self.code, self.winerror = code, winerror
        super().__init__(code if winerror is None else f"{code} (Windows error {winerror})")


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        *[(name, wintypes.DWORD) for name in (
            "dwX", "dwY", "dwXSize", "dwYSize", "dwXCountChars", "dwYCountChars",
            "dwFillAttribute", "dwFlags")],
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class _Win32:
    def __init__(self):
        if os.name != "nt":
            raise ProcessOwnershipError("windows_process_ownership_required")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "IsProcessInJob": ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
            "GetHandleInformation": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "GetCurrentProcess": ([], wintypes.HANDLE),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "InitializeProcThreadAttributeList": ([ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                                   ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
            "UpdateProcThreadAttribute": ([ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
                                           ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                           ctypes.c_void_p], wintypes.BOOL),
            "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
            "CreateProcessW": ([wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
                                 ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(_StartupInfoEx),
                                 ctypes.POINTER(_ProcessInformation)], wintypes.BOOL),
            "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def error(code):
        return ProcessOwnershipError(code, ctypes.get_last_error())

    def create_job(self):
        # NULL security attributes => non-inheritable; NULL name => unnamed.
        handle = self.kernel.CreateJobObjectW(None, None)
        if not handle:
            raise self.error("job_create_failed")
        return handle

    def set_limits(self, handle):
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                                   ctypes.byref(limits), ctypes.sizeof(limits)):
            raise self.error("job_limits_failed")

    def assert_handle(self, handle):
        flags = wintypes.DWORD()
        if not self.kernel.GetHandleInformation(handle, ctypes.byref(flags)):
            raise self.error("job_handle_check_failed")
        if flags.value & HANDLE_FLAG_INHERIT:
            raise ProcessOwnershipError("job_handle_must_not_be_inherited")

    def assign_current(self, handle):
        if not self.kernel.AssignProcessToJobObject(handle, self.kernel.GetCurrentProcess()):
            # Do not use BREAKAWAY to evade an enclosing job's restrictions.
            raise self.error("job_assignment_failed")

    def is_current_member(self, handle):
        member = wintypes.BOOL()
        if not self.kernel.IsProcessInJob(self.kernel.GetCurrentProcess(), handle, ctypes.byref(member)):
            raise self.error("job_membership_check_failed")
        return bool(member.value)

    def assert_owned(self, handle):
        self.assert_handle(handle)
        if not self.is_current_member(handle):
            raise ProcessOwnershipError("bridge_not_in_owned_job")
        limits = _ExtendedLimits()
        if not self.kernel.QueryInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                                     ctypes.byref(limits), ctypes.sizeof(limits), None):
            raise self.error("job_limits_check_failed")
        flags = limits.BasicLimitInformation.LimitFlags
        if not flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE or flags & (
                JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK):
            raise ProcessOwnershipError("job_lifetime_limits_changed")

    def assert_child(self, job_handle, process_handle):
        member = wintypes.BOOL()
        if not self.kernel.IsProcessInJob(process_handle, job_handle, ctypes.byref(member)):
            raise self.error("child_job_check_failed")
        if not member.value:
            raise ProcessOwnershipError("child_not_in_owned_job")

    def close_unassigned(self, handle):
        if not self.kernel.CloseHandle(handle):
            raise self.error("unassigned_job_close_failed")


    def create_child(self, job, executable, command, cwd, env, flags, standard_handles,
                     inherited_handles, information):
        """Windows 10+ atomic job assignment; failure never retries without it.

        JOB_LIST references the non-inherited job handle. HANDLE_LIST contains
        only Popen's inheritable child pipe handles, never the job handle.
        Attribute buffers and their values must live through CreateProcessW.
        https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
        """
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        if standard_handles:
            startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            (startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput,
             startup.StartupInfo.hStdError) = standard_handles
        values = [(PROC_THREAD_ATTRIBUTE_JOB_LIST, (wintypes.HANDLE * 1)(job))]
        if inherited_handles:
            values.append((PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                           (wintypes.HANDLE * len(inherited_handles))(*inherited_handles)))
        size = ctypes.c_size_t()
        self.kernel.InitializeProcThreadAttributeList(None, len(values), 0, ctypes.byref(size))
        if not size.value:
            raise self.error("child_attributes_size_failed")
        buffer = ctypes.create_string_buffer(size.value)
        if not self.kernel.InitializeProcThreadAttributeList(buffer, len(values), 0, ctypes.byref(size)):
            raise self.error("child_attributes_init_failed")
        try:
            for attribute, value in values:
                if not self.kernel.UpdateProcThreadAttribute(buffer, 0, attribute, ctypes.byref(value),
                                                              ctypes.sizeof(value), None, None):
                    raise self.error("child_attributes_update_failed")
            startup.lpAttributeList = ctypes.addressof(buffer)
            if not self.kernel.CreateProcessW(executable, command, None, None, bool(inherited_handles),
                                               flags | EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
                                               env, cwd, ctypes.byref(startup), ctypes.byref(information)):
                raise self.error("owned_child_create_failed")
        finally:
            self.kernel.DeleteProcThreadAttributeList(buffer)


class _OwnedPopen(subprocess.Popen):
    """Pinned CPython 3.11 pipe/lifecycle machinery with explicit Windows Job creation.

    Only the private creation hook is replaced, locally to this instance. The
    application's registered native executable, argv, cwd and environment are
    passed directly; shell, custom startup attributes and breakaway are refused.
    """

    def __init__(self, argv, *, owner, **kwargs):
        if os.name != "nt" or sys.version_info[:2] != (3, 11):
            raise ProcessOwnershipError("owned_child_python_runtime_unsupported")
        self._owner_api, self._job_handle = owner._api, owner._handle
        super().__init__(argv, **kwargs)

    def _execute_child(self, args, executable, preexec_fn, close_fds, pass_fds, cwd, env,
                       startupinfo, creationflags, shell, p2cread, p2cwrite, c2pread,
                       c2pwrite, errread, errwrite, unused_restore_signals, unused_gid,
                       unused_gids, unused_uid, unused_umask, unused_start_new_session,
                       unused_process_group):
        information = _ProcessInformation()
        api = self._owner_api
        process_handle = None
        try:
            if shell or not close_fds or startupinfo is not None or pass_fds or preexec_fn is not None:
                raise ProcessOwnershipError("unsafe_child_handle_inheritance")
            arguments = [os.fsdecode(argument) for argument in args]
            if any("\0" in argument for argument in arguments):
                raise ProcessOwnershipError("invalid_child_arguments")
            executable = os.fsdecode(executable) if executable is not None else arguments[0]
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
            cwd = os.fsdecode(cwd) if cwd is not None else None
            if "\0" in executable or (cwd is not None and "\0" in cwd):
                raise ProcessOwnershipError("invalid_child_path")
            environment = None
            if env is not None:
                pairs = []
                for key, value in env.items():
                    if (not isinstance(key, str) or not isinstance(value, str) or not key
                            or "\0" in key or "\0" in value or "=" in key[1:]):
                        raise ProcessOwnershipError("invalid_child_environment")
                    pairs.append((key, value))
                environment = ctypes.create_unicode_buffer("".join(
                    key + "=" + value + "\0" for key, value in sorted(pairs, key=lambda pair: pair[0].upper())) + "\0")
            standard_handles = tuple(int(handle) for handle in (p2cread, c2pwrite, errwrite))
            if -1 in standard_handles:
                standard_handles = ()
            inherited_handles = self._filter_handle_list(standard_handles)
            sys.audit("subprocess.Popen", executable, subprocess.list2cmdline(arguments), cwd, env)
            api.create_child(self._job_handle, executable, command, cwd, environment,
                             creationflags, standard_handles, inherited_handles, information)
            self._close_pipe_fds(p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite)
            process_handle = subprocess.Handle(information.hProcess)
            self._handle = process_handle
            self.pid = information.dwProcessId
            self._child_created = True
        except BaseException:
            if information.hProcess:
                # Retain/stop only the handle returned by this creation call.
                if api.kernel.WaitForSingleObject(information.hProcess, 0) == 258:
                    api.kernel.TerminateProcess(information.hProcess, 1)
                if api.kernel.WaitForSingleObject(information.hProcess, 5000) != 0:
                    raise ProcessOwnershipError("owned_child_creation_cleanup_failed") from None
                # After transfer the Handle owns closure, including __del__.
                # Never close its raw value and leave a second owner behind.
                if process_handle is not None:
                    process_handle.Close()
                else:
                    api.kernel.CloseHandle(information.hProcess)
                self._child_created = False
            raise
        finally:
            if information.hThread:
                api.kernel.CloseHandle(information.hThread)
            if not self._closed_child_pipe_fds:
                self._close_pipe_fds(p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite)


class ProcessOwner:
    """The module singleton owns its private handle until OS process teardown."""

    def __init__(self, api, handle):
        self._api, self._handle = api, handle
        self._process_id, self._ready = os.getpid(), False

    @property
    def process_id(self):
        return self._process_id

    def assert_owned(self):
        if self._process_id != os.getpid() or not self._ready:
            raise ProcessOwnershipError("process_owner_not_ready")
        self._api.assert_owned(self._handle)

    def spawn(self, argv, **kwargs):
        """Return a verified native server Popen; kill/refuse an unverified handle."""
        self.assert_owned()
        if not isinstance(argv, (list, tuple)) or not argv:
            raise ProcessOwnershipError("explicit_child_arguments_required")
        if kwargs.get("shell", False) is not False or kwargs.get("close_fds", True) is not True:
            raise ProcessOwnershipError("unsafe_child_handle_inheritance")
        if kwargs.get("startupinfo") is not None:
            raise ProcessOwnershipError("custom_child_handle_list_prohibited")
        flags = kwargs.get("creationflags", 0)
        if type(flags) is not int or flags < 0 or flags & CREATE_BREAKAWAY_FROM_JOB:
            raise ProcessOwnershipError("child_breakaway_prohibited")
        if flags & (CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT):
            raise ProcessOwnershipError("custom_child_startup_flags_prohibited")
        kwargs.update(shell=False, close_fds=True, creationflags=flags | CREATE_NO_WINDOW)
        child = _OwnedPopen(argv, owner=self, **kwargs)
        try:
            # A venv redirector can report a PID outside this Job. Never assume
            # inheritance succeeded just because the bridge owns its own Job.
            self._api.assert_child(self._handle, int(child._handle))
        except BaseException as error:
            cleanup_failed = False
            try:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
            except BaseException:
                cleanup_failed = True
            finally:
                for stream in (child.stdin, child.stdout, child.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            cleanup_failed = True
            if cleanup_failed:
                raise ProcessOwnershipError("unowned_child_cleanup_failed") from None
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise ProcessOwnershipError("child_ownership_not_verified") from None
        return child


_lock = threading.Lock()
_owner: ProcessOwner | None = None
_claim_error: ProcessOwnershipError | None = None


def _new_api():
    return _Win32()


def claim_process_owner() -> ProcessOwner:
    """Join the lifetime job before any child creation; fail closed on any error.

    Windows 8+ permits an existing enclosing job only when a valid nested
    hierarchy can be formed without incompatible UI limits. An assignment
    failure is a readiness failure, never a reason to spawn an unowned child.
    """
    global _owner, _claim_error
    with _lock:
        if _claim_error is not None:
            raise _claim_error
        if _owner is not None:
            _owner.assert_owned()
            return _owner
        api, handle = None, None
        try:
            api = _new_api()
            handle = api.create_job()
            # Retain the raw handle before assignment. Python interrupts can land
            # immediately after the native call has associated this process.
            _owner = ProcessOwner(api, handle)
            api.set_limits(handle)
            api.assert_handle(handle)
            api.assign_current(handle)
            # Retain the handle even if a check after assignment fails: closing
            # an assigned job here would kill this bridge before error cleanup.
            _owner._ready = True
            _owner.assert_owned()
            return _owner
        except BaseException as error:
            _claim_error = error if isinstance(error, ProcessOwnershipError) else ProcessOwnershipError("job_claim_failed")
            if _owner is not None:
                _owner._ready = False
            if api is not None and handle is not None:
                try:
                    # Only a confirmed non-member may release the job here.
                    # If membership cannot be queried, retain it until OS exit.
                    if not api.is_current_member(handle):
                        api.close_unassigned(handle)
                        _owner = None
                except ProcessOwnershipError as cleanup_error:
                    _claim_error = cleanup_error
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise _claim_error from None
