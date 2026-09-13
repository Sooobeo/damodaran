"""Pinned, isolated Hy-MT2-30B-A3B Q4 installation; never starts inference.

The default command only reports identities and physical-memory preflight.
--download preserves resumable .part files and verifies publisher hashes.
The fixed 4 GiB headroom is a conservative launch gate, not an OOM guarantee.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import urllib.request
import zipfile

import setup_hymt as archive_tools

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / ".training/comparisons/hy-mt2-30b-a3b-q4"
FROZEN_UTILITY_SHA = "c6f9a33ef0fce4a8eea4d042c233c7d1f89337d0ef128b0d5ca2fe632fa6d8d4"
REVISION = "fd3dcbb6b31e9e03923ef4f9f42500f74c3851c3"
RUNTIME_REVISION = "72797e89198ab564fd0e6baa54ab196e8dd1d884"
RUNTIME_TAG = "b10888"
MODEL = {
    "name": "Hy-MT2-30B-A3B-Q4_K_M.gguf",
    "url": f"https://huggingface.co/tencent/Hy-MT2-30B-A3B-GGUF/resolve/{REVISION}/Hy-MT2-30B-A3B-Q4_K_M.gguf",
    "size": 18236702880,
    "sha256": "bb44b11bb0f7cd3d1321645b41e911cd3de2e473731227fc6bf37aa18b543f88",
}
RUNTIMES = {
    "cpu": {"name": "llama-b10888-bin-win-cpu-x64.zip", "size": 18424677,
            "sha256": "68d0ea47c71a55f6a19219727d39075f08f7da2c9d9073c7b47b3d64a7027284"},
    "sycl": {"name": "llama-b10888-bin-win-sycl-x64.zip", "size": 119782492,
             "sha256": "fa6d763a1299a319b9f8cbece5a8e29d66b6762f4245d9608d4019fb998233e5"},
}
for _asset in RUNTIMES.values():
    _asset["url"] = f"https://github.com/ggml-org/llama.cpp/releases/download/{RUNTIME_TAG}/{_asset['name']}"


def require(condition, code):
    if not condition:
        raise ValueError(code)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def check_utilities():
    require(digest(archive_tools.__file__) == FROZEN_UTILITY_SHA, "frozen_archive_utility_changed")


def memory_preflight(available_bytes=None):
    if available_bytes is None:
        require(os.name == "nt", "windows_memory_preflight_required")
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "totalPhysical", "availablePhysical", "totalPageFile", "availablePageFile",
                    "totalVirtual", "availableVirtual", "availableExtendedVirtual")]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
        kernel.GlobalMemoryStatusEx.restype = ctypes.c_int
        require(kernel.GlobalMemoryStatusEx(ctypes.byref(status)), "physical_memory_query_failed")
        available_bytes = status.availablePhysical
    threshold = MODEL["size"] + 4 * 1024 ** 3
    return {"availablePhysicalBytes": available_bytes, "minimumAvailablePhysicalBytes": threshold,
            "headroomBytes": 4 * 1024 ** 3, "passed": available_bytes >= threshold,
            "policy": "all Q4 weights plus 4 GiB for runtime/KV/headroom; mmap and iGPU shared memory do not add RAM",
            "guaranteesNoOutOfMemory": False}


def validate_asset(path, asset):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == asset["size"],
            "asset_size_or_path_differs")
    require(digest(path) == asset["sha256"], "asset_sha256_differs")


def download(asset):
    target = DEST / asset["name"]
    if target.exists():
        validate_asset(target, asset)
        return target
    partial = target.with_name(target.name + ".part")
    require(not partial.is_symlink(), "partial_symlink_rejected")
    offset = partial.stat().st_size if partial.exists() else 0
    require(offset <= asset["size"], "partial_exceeds_expected_size")
    require(shutil.disk_usage(DEST).free >= asset["size"] - offset + 1024 ** 3,
            "insufficient_disk_space")
    if offset < asset["size"]:
        headers = {"User-Agent": "MoonModaran-HyMT30-Setup/1.0", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(asset["url"], headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            require(response.status == (206 if offset else 200), "range_status_differs")
            if offset:
                match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)",
                                     response.headers.get("Content-Range", ""))
                require(match is not None and tuple(map(int, match.groups())) ==
                        (offset, asset["size"] - 1, asset["size"]), "range_header_differs")
            length = response.headers.get("Content-Length")
            require(length is None or (length.isdigit() and int(length) == asset["size"] - offset),
                    "content_length_differs")
            count, reported = offset, time.monotonic()
            with partial.open("ab" if partial.exists() else "xb") as stream:
                for chunk in iter(lambda: response.read(4 * 1024 * 1024), b""):
                    count += len(chunk)
                    require(count <= asset["size"], "download_too_large")
                    stream.write(chunk)
                    if time.monotonic() - reported >= 10:
                        print(json.dumps({"event": "download-progress", "file": asset["name"],
                                          "bytes": count, "total": asset["size"]}), flush=True)
                        reported = time.monotonic()
                stream.flush()
                os.fsync(stream.fileno())
    validate_asset(partial, asset)
    os.link(partial, target)
    partial.unlink()
    return target


def install_runtime(kind, *, offline=False):
    check_utilities()
    asset = RUNTIMES[kind]
    archive = DEST / asset["name"] if offline else download(asset)
    validate_asset(archive, asset)
    inventory = archive_tools.archive_inventory(archive)
    final = DEST / f"runtime-{kind}"
    if final.exists():
        archive_tools.verify_runtime(final, inventory)
        return inventory
    require(not offline, "runtime_not_extracted")
    pending = DEST / f"runtime-{kind}.pending"
    pending.mkdir(exist_ok=True)
    archive_tools.verify_runtime(pending, inventory, allow_missing=True)
    with zipfile.ZipFile(archive) as zipped:
        for item in inventory:
            path = pending / item["path"]
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(item["path"]) as source, path.open("xb") as output:
                shutil.copyfileobj(source, output, 4 * 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
    archive_tools.verify_runtime(pending, inventory)
    pending.rename(final)
    return inventory


def identity():
    return {"version": "hymt30-installation-v1", "modelRepository": "tencent/Hy-MT2-30B-A3B-GGUF",
            "modelRevision": REVISION, "model": MODEL, "license": "Apache-2.0",
            "licenseUrl": f"https://huggingface.co/tencent/Hy-MT2-30B-A3B-GGUF/blob/{REVISION}/LICENSE.txt",
            "runtimeTag": RUNTIME_TAG, "runtimeRevision": RUNTIME_REVISION, "runtimeArchives": RUNTIMES,
            "upstreamWeightRevision": "not attested by GGUF publisher; separate metadata is not weight provenance",
            "inferencePerformed": False, "tokenizerAndStopTokensValidated": False,
            "applicationDeployment": False, "setupScriptSha256": digest(__file__),
            "archiveUtilitySha256": FROZEN_UTILITY_SHA}


def verify_installation():
    check_utilities()
    manifest = json.loads((DEST / "installation-manifest.json").read_text("utf-8"))
    require(set(manifest) == set(identity()) | {"createdAt", "runtimeFiles"}, "manifest_fields_differ")
    require(all(manifest.get(k) == v for k, v in identity().items()), "manifest_identity_differs")
    validate_asset(DEST / MODEL["name"], MODEL)
    require(manifest["runtimeFiles"] == {kind: install_runtime(kind, offline=True) for kind in RUNTIMES},
            "runtime_inventory_differs")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--download", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    check_utilities()
    if args.verify:
        verify_installation()
        print(json.dumps({"event": "installation-verified", "inferencePerformed": False}))
    elif args.download:
        require(not DEST.is_symlink(), "installation_symlink_rejected")
        DEST.mkdir(parents=True, exist_ok=True)
        if (DEST / "installation-manifest.json").exists():
            verify_installation()
        else:
            inventory = {kind: install_runtime(kind) for kind in RUNTIMES}
            download(MODEL)
            archive_tools.json_once(DEST / "installation-manifest.json", {
                **identity(), "createdAt": datetime.now(timezone.utc).isoformat(), "runtimeFiles": inventory})
        print(json.dumps({"event": "installation-ready", "inferencePerformed": False}), flush=True)
    else:
        print(json.dumps({"identity": identity(), "destination": str(DEST),
                          "memoryPreflight": memory_preflight()}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"event": "setup-failed", "errorType": type(error).__name__,
                          "partialPreserved": True}), file=sys.stderr)
        raise SystemExit(1)
