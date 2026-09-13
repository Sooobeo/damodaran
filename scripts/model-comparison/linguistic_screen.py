"""Read-only input boundary and telemetry for the new development comparison."""
from __future__ import annotations

import ctypes
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            value.update(block)
    return value.hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_once(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def read_screen(path, ids=None):
    path = Path(path).resolve()
    if not any(path.is_relative_to((ROOT / sub).resolve()) for sub in
               ("content/model-comparison", ".training/comparisons")):
        raise ValueError("input_outside_comparison_directories")
    manifest_path = path.with_name("dataset-manifest.json")
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw.decode("utf-8"))
    if manifest.get("version") == "real-reading-check-20260910-v1":
        from reading_check_input import read_reading_check
        return read_reading_check(path, ids)
    raw = path.read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("input_too_large")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    identity = manifest.get("dataset", {})
    if not (manifest.get("version") == "linguistic-dev-20260910-v1"
            and manifest.get("status") == "frozen" and manifest.get("humanReviewed") is False
            and manifest.get("sourceType") == "assistant_authored_unreviewed"
            and identity.get("file") == path.name and identity.get("count") == len(rows)
            and identity.get("ids") == [r.get("id") for r in rows]
            and identity.get("sha256") == hashlib.sha256(raw).hexdigest()):
        raise ValueError("development_manifest_mismatch")
    if not rows or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("empty_or_duplicate_input")
    clean = []
    for row in rows:
        source, context = row.get("source"), row.get("context", "")
        if not (row.get("split") == "development_screen"
                and isinstance(row.get("id"), str)
                and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", row["id"])
                and isinstance(source, str) and 1 <= len(source.strip()) <= 12000
                and isinstance(context, str) and len(context) <= 12000
                and row.get("sourceSha256") == text_hash(source)
                and row.get("contextSha256") == text_hash(context)):
            raise ValueError("invalid_development_row")
        if any("\x00" in value or re.search(r"<\|[^\n>]*\|>|<(?:bos|eos|start_of_turn|end_of_turn)>", value)
               for value in (source, context)):
            raise ValueError("source_contains_control_token")
        # Reference, required terminology, truth conditions, and review notes
        # cannot cross this allowlist into any model prompt.
        clean.append({key: row.get(key, "") for key in
                      ("id", "source", "context", "domain", "sourceSha256", "contextSha256")})
    if ids:
        requested = list(ids)
        if len(set(requested)) != len(requested) or not set(requested) <= {r["id"] for r in clean}:
            raise ValueError("invalid_requested_subset")
        clean = [r for r in clean if r["id"] in requested]
    return clean, {"inputPath": str(path), "inputSha256": hashlib.sha256(raw).hexdigest(),
                   "manifestPath": str(manifest_path),
                   "manifestSha256": hashlib.sha256(manifest_raw).hexdigest(), "totalRows": len(rows),
                   "selectedIds": [r["id"] for r in clean], "humanReviewed": False,
                   "purpose": "development screen; not independent final certification"}


def assert_input_unchanged(identity):
    if (digest(identity["inputPath"]) != identity["inputSha256"]
            or digest(identity["manifestPath"]) != identity["manifestSha256"]):
        raise ValueError("development_input_changed_during_run")
    for path, expected in (identity.get("inputFiles", {}) | identity.get("readerCodeFiles", {})).items():
        if digest(path) != expected:
            raise ValueError("reading_input_or_reader_changed_during_run")


def memory_status():
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in
            ("totalPhysical", "availablePhysical", "totalPageFile", "availablePageFile",
             "totalVirtual", "availableVirtual", "availableExtendedVirtual")]
    info = Memory()
    info.length = ctypes.sizeof(info)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(info)):
        raise OSError("memory_status_unavailable")
    return {name: getattr(info, name) for name in
            ("load", "totalPhysical", "availablePhysical", "totalPageFile", "availablePageFile")}
