"""Source-free fixture V2: request 127 MiB API max, observe within 128 MiB budget.

Uses the installed .NET Framework compiler to produce a disposable PE process;
the child uses a read-only Win32-backed mapping and an 8 MiB managed heap. This
is not a model, minimum-model-RAM experiment or system cache/commit guarantee.
Run with --output <new ignored directory>. All artifacts and failures survive.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from working_set_limit import WorkingSetLimit, WorkingSetLimitError, _validate_sizes, _Native, _MemoryCounters
import process_owner

HERE = Path(__file__).resolve().parent
MAXIMUM = 127 * 1024**2
RESIDENT_BUDGET = 128 * 1024**2
FILE_BYTES = 256 * 1024**2
SOURCE = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Threading;
class MappingFixture {
  static int Main(string[] args) {
    try {
      Console.WriteLine("{\"event\":\"ready\",\"pid\":" + Process.GetCurrentProcess().Id + "}");
      if (Console.ReadLine() != "start") return 2;
      byte[] heap = new byte[8 * 1024 * 1024];
      for (int i = 0; i < heap.Length; i += 4096) heap[i] = 0xA5;
      using (var file = new FileStream(args[0], FileMode.Open, FileAccess.Read, FileShare.Read))
      using (var map = MemoryMappedFile.CreateFromFile(file, null, 0, MemoryMappedFileAccess.Read, null, HandleInheritability.None, false))
      using (var view = map.CreateViewAccessor(0, 0, MemoryMappedFileAccess.Read)) {
        for (int pass = 0; pass < 3; pass++) {
          long checksum = 0;
          for (long offset = 0; offset < view.Capacity; offset += 4096) {
            checksum += view.ReadByte(offset);
            if ((offset % (4 * 1024 * 1024)) == 0) Thread.Sleep(10);
          }
          Console.WriteLine("{\"event\":\"pass\",\"pass\":" + pass + ",\"checksum\":" + checksum + "}");
        }
        GC.KeepAlive(heap);
        Console.WriteLine("{\"event\":\"done\"}");
        if (Console.ReadLine() != "exit") return 3;
      }
      return 0;
    } catch (Exception error) {
      Console.WriteLine("{\"event\":\"failed\",\"type\":\"" + error.GetType().Name + "\"}");
      return 4;
    }
  }
}
'''


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


class _ProcessOnlyDiagnostic:
    """Fixture-only independent API diagnostic, never a WorkingSetLimit fallback.

    The tiny CLR child waits on stdin before mapping/heap allocation. This does
    not establish a model-safe spawn-before-execution boundary.
    """
    def __init__(self, owner):
        self.limiter = WorkingSetLimit(owner, maximum_bytes=MAXIMUM)
        self.job_record = self.limiter.job_record

    def apply_child(self, child):
        self.limiter.apply_before_resume(int(child._handle), child.pid)
        return {**self.limiter.apply_child(child), "suspensionGuaranteed": False,
                "fixtureWaitsForStdinBeforeMapping": True, "processFlagsAppliedAfterSpawn": True}

    def sample_child(self, child):
        return {**self.limiter.sample_child(child), "systemMemory": system_memory()}


def system_memory():
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ("totalPhysical", "availablePhysical",
                "totalPageFile", "availablePageFile", "totalVirtual", "availableVirtual", "extendedVirtual")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(Status)]
    kernel.GlobalMemoryStatusEx.restype = ctypes.c_int
    status = Status()
    status.length = ctypes.sizeof(status)
    if not kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise RuntimeError("fixture_system_memory_query_failed")
    return {"availablePhysical": status.availablePhysical, "availablePageFile": status.availablePageFile}


def fixture(directory, process_only=False):
    summary = {"version": "working-set-native-fixture-v2", "status": "failed",
               "modelLoaded": False, "sourceRead": False, "requestedMaximumBytes": MAXIMUM,
               "mappingBytes": FILE_BYTES, "childHeapBytes": 8 * 1024**2,
               "fixtureRuntime": "installed_dotnet_framework_PE_with_readonly_file_mapping",
               "observationsProve": "bounded_fixture_only_not_model_or_OS_cache_safety",
               "parentPid": os.getpid(), "events": [], "samples": []}
    summary["residentBudgetBytes"] = RESIDENT_BUDGET
    summary["policySha256"] = sha(directory / "predeclared-policy.json")
    summary["processOnlyDiagnostic"] = process_only
    child, reader = None, None
    try:
        summary["memoryBefore"] = system_memory()
        if min(summary["memoryBefore"].values()) < 512 * 1024**2:
            raise RuntimeError("fixture_insufficient_physical_or_commit")
        owner = process_owner.claim_process_owner()
        limiter = _ProcessOnlyDiagnostic(owner) if process_only else WorkingSetLimit(owner, maximum_bytes=MAXIMUM)
        summary["job"] = limiter.job_record
        child = owner.spawn([str(directory / "mapping-fixture.exe"), str(directory / "synthetic.bin")],
                            cwd=str(directory), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", bufsize=1)
        summary["childPid"] = child.pid
        summary["applied"] = limiter.apply_child(child)
        messages = queue.Queue()
        def receive():
            try:
                for line in child.stdout:
                    messages.put(json.loads(line))
            except Exception as error:
                messages.put({"event": "reader_failed", "type": type(error).__name__})
        reader = threading.Thread(target=receive, daemon=True)
        reader.start()
        deadline, started, done = time.monotonic() + 45, False, False
        while time.monotonic() < deadline and not done:
            if child.poll() is not None:
                raise RuntimeError("fixture_child_exited_before_completion")
            sample = limiter.sample_child(child)
            summary["samples"].append(sample)
            if min(sample["systemMemory"].values()) < 512 * 1024**2:
                raise RuntimeError("fixture_physical_or_commit_below_guard")
            while not messages.empty():
                event = messages.get_nowait()
                event["sampleIndex"] = len(summary["samples"]) - 1
                summary["events"].append(event)
                if event["event"] == "ready":
                    if started or event["pid"] != child.pid:
                        raise RuntimeError("fixture_reported_pid_mismatch")
                    started = True
                    child.stdin.write("start\n")
                    child.stdin.flush()
                elif event["event"] == "done":
                    done = True
                elif event["event"] != "pass":
                    raise RuntimeError("fixture_child_reported_failure")
            time.sleep(0.025)
        if not done:
            raise RuntimeError("fixture_timeout")
        summary["samples"].append(limiter.sample_child(child))
        passes = [event for event in summary["events"] if event["event"] == "pass"]
        if [event["pass"] for event in passes] != [0, 1, 2] or any(event["checksum"] != FILE_BYTES // 4096 * 0x5A for event in passes):
            raise RuntimeError("fixture_mapping_read_incomplete")
        faults = [summary["samples"][event["sampleIndex"]]["pageFaultCount"] for event in passes]
        summary["passFaultCounts"] = faults
        summary["repeatedPassFaultIncreases"] = [b - a for a, b in zip(faults, faults[1:])]
        if any(delta <= 0 for delta in summary["repeatedPassFaultIncreases"]):
            raise RuntimeError("fixture_did_not_observe_repeated_page_faults")
        summary["maximumObservedWorkingSetBytes"] = max(row["workingSetBytes"] for row in summary["samples"])
        summary["maximumObservedPeakWorkingSetBytes"] = max(row["peakWorkingSetBytes"] for row in summary["samples"])
        summary["maximumObservedPrivateCommitBytes"] = max(row["privateBytes"] for row in summary["samples"])
        if max(summary["maximumObservedWorkingSetBytes"], summary["maximumObservedPeakWorkingSetBytes"]) > RESIDENT_BUDGET:
            raise RuntimeError("fixture_observed_working_set_above_resident_budget")
        child.stdin.write("exit\n")
        child.stdin.flush()
        if child.wait(timeout=10) != 0:
            raise RuntimeError("fixture_child_exit_failed")
        owner.assert_owned()
        summary["status"] = "completed"
    except BaseException as error:
        summary["error"] = {"type": type(error).__name__, "code": getattr(error, "code", str(error)),
                            "winerror": getattr(error, "winerror", None)}
    finally:
        if child is not None:
            try:
                if child.poll() is None:
                    child.kill()
                summary["childExitCode"] = child.wait(timeout=5)
                summary["childStopped"] = child.poll() is not None
            except BaseException as error:
                summary["status"] = "failed"
                summary["cleanupError"] = type(error).__name__
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream:
                    try:
                        stream.close()
                    except Exception as error:
                        summary["status"] = "failed"
                        summary["streamCleanupError"] = type(error).__name__
        if reader:
            reader.join(timeout=2)
            if reader.is_alive():
                summary["status"] = "failed"
                summary["readerCleanupError"] = "reader_did_not_stop"
        try:
            summary["memoryAfterChildCleanup"] = system_memory()
        except Exception as error:
            summary["status"] = "failed"
            summary["finalMemoryError"] = type(error).__name__
        write_json(directory / "fixture-result.json", summary)
    return 0 if summary["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture-parent", action="store_true")
    parser.add_argument("--process-only", action="store_true")
    args = parser.parse_args()
    directory = args.output.resolve()
    if args.fixture_parent:
        return fixture(directory, args.process_only)
    directory.mkdir(parents=True, exist_ok=False)
    summary = {"version": "working-set-fixture-verification-v2", "status": "failed",
               "modelLoaded": False, "sourceRead": False, "files": {}}
    summary["processOnlyDiagnostic"] = args.process_only
    try:
        if not args.process_only:
            raise RuntimeError("explicit_source_free_process_only_diagnostic_required")
        policy = {"version": "working-set-headroom-fixture-policy-v1",
                  "requestedApiMaximumBytes": MAXIMUM, "measuredResidentBudgetBytes": RESIDENT_BUDGET,
                  "reservedHeadroomBytes": RESIDENT_BUDGET - MAXIMUM,
                  "requiredApiFlags": 6, "requireExactRequestedApiReadback": True,
                  "requireObservedWorkingSetAndPeakWithinResidentBudget": True,
                  "requireThreeExactChecksums": True, "requireRepeatedFaultIncreases": True,
                  "requireOwnedPidMatchAndChildStopped": True, "physicalAndCommitGuardBytes": 512 * 1024**2,
                  "priorApi128MiBObservedMaximumBytes": 134221824, "priorExcessBytes": 4096,
                  "priorStrictFixtureStatusRemains": "failed", "suspensionGuaranteed": False,
                  "sourceFree": True, "modelLoaded": False}
        write_json(directory / "predeclared-policy.json", policy)
        summary["predeclaredPolicySha256"] = sha(directory / "predeclared-policy.json")
        rejected = 0
        for low, high in [(True, MAXIMUM), (1024**2, False), (0, MAXIMUM),
                          (MAXIMUM * 2, MAXIMUM), (1024**2, MAXIMUM + 1)]:
            try:
                _validate_sizes(low, high)
            except WorkingSetLimitError:
                rejected += 1
        if rejected != 5:
            raise RuntimeError("invalid_bounds_were_not_all_rejected")
        summary["invalidBoundsRejected"] = rejected
        for relative in ("working_set_limit.py", "test_working_set_limit_v2.py"):
            summary["files"][relative] = sha(HERE / relative)
        summary["files"]["process_owner.py"] = sha(HERE.parent / "local-hymt/process_owner.py")
        source = directory / "mapping-fixture.cs"
        source.write_text(SOURCE, encoding="utf-8", newline="\n")
        compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        result = subprocess.run([str(compiler), "/nologo", "/platform:x64", "/target:exe",
                                 "/out:" + str(directory / "mapping-fixture.exe"), str(source)],
                                capture_output=True, text=True, timeout=30, creationflags=0x08000000)
        (directory / "compiler-output.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError("fixture_compile_failed")
        with (directory / "synthetic.bin").open("xb") as stream:
            block = b"\x5a" * 1024**2
            for _ in range(256):
                stream.write(block)
        for name in ("mapping-fixture.cs", "mapping-fixture.exe", "synthetic.bin"):
            summary["files"][name] = sha(directory / name)
        started = time.monotonic()
        command = [sys._base_executable, "-X", "utf8", str(Path(__file__).resolve()),
                   "--output", str(directory), "--fixture-parent"]
        if args.process_only:
            command.append("--process-only")
        result = subprocess.run(command,
                                capture_output=True, text=True, timeout=75, creationflags=0x08000000)
        summary["seconds"] = time.monotonic() - started
        summary["parentExitCode"] = result.returncode
        (directory / "parent-output.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
        observed = json.loads((directory / "fixture-result.json").read_text("utf-8"))
        summary["fixtureResultSha256"] = sha(directory / "fixture-result.json")
        if result.returncode or observed["status"] != "completed" or not observed.get("childStopped"):
            raise RuntimeError("native_fixture_failed")
        if observed["policySha256"] != summary["predeclaredPolicySha256"]:
            raise RuntimeError("fixture_policy_identity_changed")
        for relative in ("working_set_limit.py", "test_working_set_limit_v2.py"):
            if summary["files"][relative] != sha(HERE / relative):
                raise RuntimeError("fixture_code_changed")
        if summary["files"]["process_owner.py"] != sha(HERE.parent / "local-hymt/process_owner.py"):
            raise RuntimeError("frozen_process_owner_changed")
        summary["status"] = "completed"
    except BaseException as error:
        summary["error"] = {"type": type(error).__name__, "message": str(error)}
    write_json(directory / "verification.json", summary)
    print(json.dumps(summary))
    return 0 if summary["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
