"""Keep exact producer source bytes when later app edits change live files.

Only small source files from completed standard comparisons are archived.
Model weights, datasets, databases and translations are not copied or generated.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
VERSION = "quality-producer-code-snapshot-v1"
SYSTEMS = ("argos-app", "marian-v5")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate snapshot JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda value: require(False, "Nonfinite snapshot JSON value"))


def regular(path, boundary):
    path, boundary = Path(path), Path(boundary).resolve()
    require(path.resolve().is_relative_to(boundary) and not any(p.is_symlink() for p in (path, *path.parents)),
            "Snapshot path is linked or escaped its boundary")
    require(path.is_file() and path.stat().st_size <= 32 * 1024 * 1024, "Expected a small regular source file")
    return path


def snapshot_paths(directory, mapping, metadata_sha, summary_sha, inputs=None):
    """Return hash-identical historical paths, or None if no archive exists."""
    directory = Path(directory).resolve()
    destination = directory.parent / "code-snapshots" / directory.name
    if not destination.exists():
        return None
    manifest_path = regular(destination / "manifest.json", directory.parent)
    raw = manifest_path.read_bytes()
    manifest = parse(raw)
    require(manifest.get("version") == VERSION and manifest.get("status") == "complete"
            and manifest.get("system") == directory.name
            and manifest.get("startSha256") == metadata_sha and manifest.get("summarySha256") == summary_sha,
            "Historical producer snapshot is not bound to the completed run")
    entries = manifest.get("files")
    require(isinstance(entries, dict) and set(entries) == set(mapping), "Historical producer inventory differs")
    result, names = {}, {"manifest.json"}
    if inputs is not None:
        inputs.record(manifest_path, sha(raw))
    for key, expected in mapping.items():
        entry = entries[key]
        require(isinstance(entry, dict) and entry.get("sha256") == expected
                and isinstance(entry.get("file"), str)
                and re.fullmatch(r"files/[0-9]{4}-[A-Za-z0-9_.-]+", entry["file"])
                and type(entry.get("size")) is int and 0 <= entry["size"] <= 32 * 1024 * 1024,
                "Malformed historical producer entry")
        path = regular(destination / entry["file"], destination)
        require(path.stat().st_size == entry["size"] and sha(path.read_bytes()) == expected,
                "Historical producer bytes differ from the recorded inference code")
        require(entry["file"] not in names, "Repeated historical producer file")
        names.add(entry["file"])
        if inputs is not None:
            inputs.record(path, expected)
        result[key] = path
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
    require(actual == names and all(not p.is_symlink() for p in destination.rglob("*")),
            "Historical producer snapshot inventory changed")
    return result


def archive(directory, root=ROOT):
    root, directory = Path(root).resolve(), Path(directory).resolve()
    require(directory.name in SYSTEMS and directory.is_relative_to(root / ".training/comparisons"),
            "Expected a standard comparison directory")
    start_path, summary_path = (regular(directory / name, root) for name in ("start.json", "summary.json"))
    start_raw, summary_raw = start_path.read_bytes(), summary_path.read_bytes()
    start, summary = parse(start_raw), parse(summary_raw)
    require(summary.get("status") == "complete" and summary.get("integrityVerified") is True,
            "Archive only an integrity-verified completed comparison")
    mapping = start.get("codeHashes")
    require(isinstance(mapping, dict) and 1 <= len(mapping) <= 100, "Missing producer source inventory")
    for key, expected in mapping.items():
        require(isinstance(key, str) and (key.startswith(("scripts/", "lib/", "content/")) or key == "package-lock.json")
                and re.fullmatch(r"[A-Za-z0-9_./-]+", key) and not {"", ".", ".."}.intersection(key.split("/"))
                and not Path(key).is_absolute() and Path(key).suffix in (".py", ".ts", ".tsx", ".json")
                and isinstance(expected, str) and re.fullmatch(r"[a-f0-9]{64}", expected),
                "Unsupported producer source entry")
    destination = directory.parent / "code-snapshots" / directory.name
    if snapshot_paths(directory, mapping, sha(start_raw), sha(summary_raw)) is not None:
        return destination / "manifest.json"
    payloads, entries = {}, {}
    for index, (key, expected) in enumerate(sorted(mapping.items())):
        original = regular(root / key, root)
        require(re.fullmatch(r"[A-Za-z0-9_.-]+", original.name), "Unsupported source basename")
        raw = original.read_bytes()
        require(sha(raw) == expected, "Producer source changed before archival")
        name = f"files/{index:04d}-{original.name}"
        payloads[name] = raw
        entries[key] = {"file": name, "sha256": expected, "size": len(raw)}
    pending = destination.with_name(destination.name + ".pending")
    require(pending.resolve().is_relative_to(directory.parent) and
            not any(p.is_symlink() for p in (pending, *pending.parents)), "Linked or escaped snapshot output")
    pending.mkdir(parents=True, exist_ok=False)
    (pending / "files").mkdir()
    manifest = {"version": VERSION, "status": "complete", "system": directory.name,
                "createdAt": datetime.now(timezone.utc).isoformat(), "startSha256": sha(start_raw),
                "summarySha256": sha(summary_raw), "files": entries,
                "meaning": "Exact bytes of codeHashes verified during completed inference"}
    payloads["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    for name, raw in payloads.items():
        with (pending / name).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    require(start_path.read_bytes() == start_raw and summary_path.read_bytes() == summary_raw
            and all(sha(regular(root / key, root).read_bytes()) == value for key, value in mapping.items()),
            "Producer evidence changed during archival")
    require(not destination.exists(), "Snapshot appeared concurrently; preserving pending files")
    pending.rename(destination)
    snapshot_paths(directory, mapping, sha(start_raw), sha(summary_raw))
    return destination / "manifest.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-directory", required=True, type=Path)
    result = archive(parser.parse_args().run_directory)
    print(json.dumps({"event": "producer-code-archived", "manifest": str(result), "sha256": sha(result.read_bytes())}))
