"""Fail-closed process ownership for the isolated Windows QE runtime."""
from __future__ import annotations

import ctypes
import math
import os
from contextlib import contextmanager
from pathlib import Path

from common import canonical, read_json, sha_text


def validate_owner(owner):
    pid, created = owner.get('pid'), owner.get('createdAt')
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise RuntimeError('qe_runtime_lock_invalid_owner')
    if isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(created) or created <= 0:
        raise RuntimeError('qe_runtime_lock_invalid_owner')
    return owner


def owner_state(owner, process_factory=None):
    """Reused PIDs and inaccessible owners are not evidence for safe reclamation."""
    import psutil
    validate_owner(owner)
    factory = process_factory or psutil.Process
    try:
        process = factory(owner['pid'])
        created = process.create_time()
    except psutil.NoSuchProcess:
        return 'dead'
    except (psutil.AccessDenied, OSError):
        return 'unknown'
    if abs(created - owner['createdAt']) > 0.001:
        return 'pid_reused'
    return 'live'


@contextmanager
def ownership_mutex(path: Path):
    # This mutex serializes all cooperating creation/reclamation/release actions.
    # Existing old processes lack the mutex but their live-owner lock is retained.
    if os.name != 'nt':
        raise RuntimeError('qe_runtime_lock_windows_required')
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = 'Local\\MoonModaran-QE-' + sha_text(str(path.resolve()).casefold())[:32]
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise RuntimeError('qe_runtime_lock_mutex_creation_failed')
    acquired = False
    try:
        status = kernel.WaitForSingleObject(handle, 0)
        if status not in (0, 0x80):  # Acquired normally or inherited abandoned mutex.
            raise RuntimeError('qe_runtime_lock_mutex_busy')
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def read_owner(path):
    if path.stat().st_size > 2048:
        raise RuntimeError('qe_runtime_lock_invalid_owner')
    try:
        owner = read_json(path)
        if not isinstance(owner, dict):
            raise RuntimeError('qe_runtime_lock_invalid_owner')
        return validate_owner(owner)
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError('qe_runtime_lock_invalid_owner') from exc


def acquire(path, *, process_factory=None):
    import psutil
    current = {'pid': os.getpid(), 'createdAt': psutil.Process().create_time()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with ownership_mutex(path):
        if path.exists():
            previous = read_owner(path)
            state = owner_state(previous, process_factory)
            if state != 'dead':
                raise RuntimeError('qe_runtime_lock_' + state + '_owner')
            # Only a provably absent process may be reclaimed. PID reuse and
            # AccessDenied remain blocked. Re-read inside the exclusive mutex.
            if read_owner(path) != previous:
                raise RuntimeError('qe_runtime_lock_owner_changed')
            path.unlink()
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(canonical(current))
    return current


def release(path, owner):
    with ownership_mutex(path):
        if path.exists():
            if read_owner(path) != owner:
                raise RuntimeError('qe_runtime_lock_owner_changed')
            path.unlink()
