"""Comparison-only native child: atomic Job membership, limit, then resume.

The existing production ProcessOwner and its private Job remain unchanged.
Only this adapter instance uses a local _Win32 subclass. The production
_OwnedPopen still owns all hProcess/hThread error cleanup and pipe handling.
No model, process, global API patch or Windows privilege change occurs on import.

CREATE_SUSPENDED prevents the primary thread from running until ResumeThread;
exactly one previous suspend count is required. A cap/readback/resume failure
propagates through _OwnedPopen's terminate/wait/handle cleanup before spawn
returns. Working-set policy does not bound system file cache or total commit.

https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags
https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread
"""
from __future__ import annotations

from ctypes import wintypes
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-hymt"))
import process_owner

VERSION = "comparison-suspended-owned-spawn-v1"


class _SuspendedWin32(process_owner._Win32):
    def __init__(self, limiter):
        super().__init__()
        self.limiter, self.last_creation = limiter, None
        self.kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        self.kernel.ResumeThread.restype = wintypes.DWORD

    def create_child(self, job, executable, command, cwd, env, flags, standard_handles,
                     inherited_handles, information):
        super().create_child(job, executable, command, cwd, env,
                             flags | process_owner.CREATE_SUSPENDED, standard_handles,
                             inherited_handles, information)
        # Leave every native handle in `information`: _OwnedPopen alone cleans
        # them on an exception, preventing two independent CloseHandle owners.
        self.last_creation = {"version": VERSION, "pid": int(information.dwProcessId),
                              "atomicJobAssignment": True, "createSuspended": True,
                              "limitAppliedBeforeResume": False, "resumePreviousCount": None}
        self.assert_child(job, information.hProcess)
        receipt = self.limiter.apply_before_resume(information.hProcess, information.dwProcessId)
        self.last_creation.update(limitAppliedBeforeResume=True, workingSetLimit=receipt)
        previous = int(self.kernel.ResumeThread(information.hThread))
        self.last_creation["resumePreviousCount"] = previous
        if previous != 1:
            raise process_owner.ProcessOwnershipError("comparison_child_resume_count_invalid")


class SuspendedProcessOwner(process_owner.ProcessOwner):
    """Borrow the already-owned Job without replacing its API or handle owner."""

    def __init__(self, owner, limiter):
        if not isinstance(owner, process_owner.ProcessOwner) or limiter.owner is not owner:
            raise process_owner.ProcessOwnershipError("comparison_limiter_owner_mismatch")
        owner.assert_owned()
        self.original_owner = owner
        super().__init__(_SuspendedWin32(limiter), owner._handle)
        self._ready = True
        self.assert_owned()

    def assert_owned(self):
        self.original_owner.assert_owned()
        super().assert_owned()

    @property
    def creation_receipt(self):
        return self._api.last_creation
