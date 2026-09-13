"""Install pinned official Hy-MT2 Q8 weights and an isolated Windows runtime.

Downloads public files only. Does not change training environments, application
settings, personal data, or any previous comparison. Inference is a later step.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / ".training/comparisons/hy-mt2-7b-q8"
REVISION = "ab8472660ac61fac25f1af43fac2599d52a8a775"
MODEL = {
    "name": "HY-MT2-7B-Q8_0.gguf",
    "url": f"https://huggingface.co/tencent/Hy-MT2-7B-GGUF/resolve/{REVISION}/HY-MT2-7B-Q8_0.gguf",
    "size": 7981928896,
    "sha256": "58b3ad55dd6f6fa08c695cddc34fb5f8f708a844f78ae10508071914b0ed67c0",
}
RUNTIME = {
    "name": "llama-b10874-bin-win-vulkan-x64.zip",
    "url": "https://github.com/ggml-org/llama.cpp/releases/download/b10874/llama-b10874-bin-win-vulkan-x64.zip",
    "size": 35789222,
    "sha256": "0113e9b49a5d979b32805092740ce97b9497494cdb308cf9568577da3e78e3c0",
}
LICENSE_URL = f"https://huggingface.co/tencent/Hy-MT2-7B-GGUF/blob/{REVISION}/LICENSE.txt"
# A completed installation predates the corrected link. These hashes attest
# only that historical installation, not training code or evaluation policy.
HISTORICAL_MANIFEST_SHA256 = "21a78c8b6a55ba862a365c4acea7533eb1a1b8047953734980319b531363fead"
HISTORICAL_SETUP_SHA256 = "7cb24ed858bb42461d6d4cf191514e53915ca1a55f05eb50a7bebc5a460be594"
HISTORICAL_NOTES_SHA256 = "b7aa05556f55f60ddb01f0b164911fc9a4821585681675b183793b786184e904"


def installation_identity():
    return {
        "version": 1,
        "purpose": "free local exploratory comparison; not application deployment or model training",
        "baseModel": "tencent/Hy-MT2-7B", "conversionRepository": "tencent/Hy-MT2-7B-GGUF",
        "conversionRevision": REVISION, "conversion": "publisher-provided Q8_0; not original BF16 precision",
        "upstreamWeightRevision": "not attested by the GGUF publisher; do not infer from repository HEAD",
        "model": MODEL, "runtimeArchive": RUNTIME,
        "runtimeVersion": "llama.cpp b10874 e2d2c0d6aa9b996d5d3a3c1d5e24c8c19728bb3d Windows x64",
        "license": "Apache-2.0", "inferencePerformed": False,
        "tokenizerAndStopTokensValidated": False,
    }


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def json_once(path, value):
    content = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists():
        require(path.read_bytes() == content, "Existing immutable metadata differs")
        return
    staging = path.with_name(path.name + ".pending")
    with staging.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(staging, path)
    staging.unlink()


def download(asset):
    target = DEST / asset["name"]
    if target.exists():
        require(target.stat().st_size == asset["size"] and digest(target) == asset["sha256"],
                "Existing pinned download differs; preserved for inspection")
        return target
    partial = target.with_name(target.name + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    require(offset <= asset["size"], "Partial download exceeds pinned size")
    if offset < asset["size"]:
        headers = {"User-Agent": "MoonModaran-Local-Model-Comparison/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(asset["url"], headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            require(response.status == (206 if offset else 200), "Server did not honor the download range")
            if offset:
                matched = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)",
                                       response.headers.get("Content-Range", ""))
                require(matched is not None and tuple(map(int, matched.groups())) ==
                        (offset, asset["size"] - 1, asset["size"]),
                        "Server did not honor the complete pinned download range")
            length = response.headers.get("Content-Length")
            require(length is None or (re.fullmatch(r"[0-9]+", length) is not None and
                    int(length) == asset["size"] - offset), "Download Content-Length differs")
            size, started, reported = offset, time.monotonic(), time.monotonic()
            # A disconnect before the first body byte leaves a valid empty part.
            with partial.open("ab" if partial.exists() else "xb") as stream:
                for chunk in iter(lambda: response.read(4 * 1024 * 1024), b""):
                    size += len(chunk)
                    require(size <= asset["size"], "Download exceeds pinned size")
                    stream.write(chunk)
                    if time.monotonic() - reported >= 10:
                        print(json.dumps({"event": "download-progress", "file": asset["name"],
                                          "bytes": size, "total": asset["size"],
                                          "seconds": round(time.monotonic() - started, 1)}), flush=True)
                        reported = time.monotonic()
                stream.flush()
                os.fsync(stream.fileno())
    require(partial.stat().st_size == asset["size"] and digest(partial) == asset["sha256"],
            "Downloaded asset integrity check failed")
    os.link(partial, target)
    partial.unlink()
    return target


def runtime_archive(*, allow_download):
    cached = ROOT / ".training/comparisons/translategemma-4b-q4" / RUNTIME["name"]
    local = DEST / RUNTIME["name"]
    archive = cached if cached.exists() else local
    if not archive.exists():
        require(allow_download, "Pinned Windows runtime ZIP is required for offline verification")
        archive = download(RUNTIME)
    require(archive.stat().st_size == RUNTIME["size"] and digest(archive) == RUNTIME["sha256"],
            "Pinned Windows runtime archive differs")
    return archive


def archive_inventory(archive):
    """Derive every path, size and hash independently from the pinned ZIP."""
    inventory = []
    with zipfile.ZipFile(archive) as zipped:
        seen = set()
        for entry in zipped.infolist():
            require((entry.external_attr >> 16) & 0o170000 != 0o120000, "Runtime symlink is prohibited")
            require(entry.orig_filename == entry.filename, "Runtime archive path escaped canonical spelling")
            relative = entry.filename[:-1] if entry.is_dir() else entry.filename
            parts = relative.split("/")
            require(relative and "\\" not in relative and all(part not in ("", ".", "..") and
                    not re.search(r'[<>:"|?*\x00-\x1f]', part) and part == part.rstrip(" .") and
                    not re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                    for part in parts), "Runtime archive path escaped installation or is invalid on Windows")
            require(relative.casefold() not in seen, "Duplicate runtime archive path")
            seen.add(relative.casefold())
            if entry.is_dir():
                continue
            result, size = hashlib.sha256(), 0
            with zipped.open(entry) as stream:
                for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                    result.update(chunk)
                    size += len(chunk)
            require(size == entry.file_size, "Runtime archive member size differs")
            inventory.append({"path": relative, "size": size, "sha256": result.hexdigest()})
    require(any(item["path"] == "llama-server.exe" for item in inventory), "Runtime archive lacks llama-server.exe")
    return sorted(inventory, key=lambda item: item["path"])


def verify_runtime(directory, expected, *, allow_missing=False):
    require(directory.is_dir() and not directory.is_symlink(), "Invalid runtime directory")
    items = {item["path"]: item for item in expected}
    found = set()
    # Validate every existing staging file before writing any missing files.
    for path in directory.rglob("*"):
        require(not path.is_symlink() and path.resolve().is_relative_to(directory.resolve()), "Invalid runtime path")
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            require(relative in items, "Runtime inventory has extra files")
            item = items[relative]
            require(path.stat().st_size == item["size"] and digest(path) == item["sha256"], "Runtime file differs")
            found.add(relative)
    require(allow_missing or found == set(items), "Runtime inventory is incomplete")


def install_runtime():
    archive = runtime_archive(allow_download=True)
    inventory = archive_inventory(archive)
    final, pending = DEST / "runtime", DEST / "runtime.pending"
    if final.exists():
        verify_runtime(final, inventory)
        return inventory
    pending.mkdir(exist_ok=True)
    verify_runtime(pending, inventory, allow_missing=True)
    with zipfile.ZipFile(archive) as zipped:
        for item in inventory:
            path = pending / item["path"]
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(item["path"]) as source, path.open("xb") as output:
                for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
    verify_runtime(pending, inventory)
    pending.rename(final)
    return inventory


def verify_manifest_identity(manifest, path):
    identity = installation_identity()
    require(set(manifest) == set(identity) | {"createdAt", "licenseUrl", "runtimeFiles", "setupScriptSha256"},
            "Installation manifest fields differ")
    require(type(manifest["version"]) is int and all(manifest[key] == value for key, value in identity.items()) and
            manifest["inferencePerformed"] is False and manifest["tokenizerAndStopTokensValidated"] is False,
            "Installation identity differs")
    require(isinstance(manifest["createdAt"], str), "Invalid installation timestamp")
    require(datetime.fromisoformat(manifest["createdAt"]).utcoffset() is not None, "Installation timestamp lacks timezone")
    require(isinstance(manifest["runtimeFiles"], list) and all(isinstance(item, dict) and
            set(item) == {"path", "size", "sha256"} and type(item["size"]) is int for item in manifest["runtimeFiles"]),
            "Invalid runtime inventory")
    if manifest["licenseUrl"] == LICENSE_URL:
        require(manifest["setupScriptSha256"] == digest(Path(__file__)), "Installation setup script differs")
        return
    notes = DEST / "installation-notes.json"
    original = DEST / "setup-original.py"
    require(manifest["licenseUrl"] == LICENSE_URL.removesuffix(".txt") and
            digest(path) == HISTORICAL_MANIFEST_SHA256 and manifest["setupScriptSha256"] == HISTORICAL_SETUP_SHA256 and
            notes.is_file() and digest(notes) == HISTORICAL_NOTES_SHA256 and
            original.is_file() and digest(original) == HISTORICAL_SETUP_SHA256,
            "Historical installation requires its sealed manifest, notes and original setup script")


def verify_installation():
    path = DEST / "installation-manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    require(isinstance(manifest, dict), "Invalid installation manifest")
    verify_manifest_identity(manifest, path)
    # Verification is offline and cannot download or repair an installation.
    inventory = archive_inventory(runtime_archive(allow_download=False))
    require(manifest["runtimeFiles"] == inventory, "Manifest runtime inventory differs from the pinned ZIP")
    verify_runtime(DEST / "runtime", inventory)
    model = DEST / MODEL["name"]
    require(model.stat().st_size == MODEL["size"] and digest(model) == MODEL["sha256"], "Model integrity failed")
    return manifest


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    if (DEST / "installation-manifest.json").exists():
        verify_installation()
        print(json.dumps({"event": "installation-verified", "inferencePerformed": False}), flush=True)
        return
    runtime_files = install_runtime()
    download(MODEL)
    manifest = {
        **installation_identity(), "createdAt": datetime.now(timezone.utc).isoformat(),
        "runtimeFiles": runtime_files, "licenseUrl": LICENSE_URL,
        "setupScriptSha256": digest(Path(__file__)),
    }
    json_once(DEST / "installation-manifest.json", manifest)
    print(json.dumps({"event": "weights-and-runtime-ready", "modelSha256": MODEL["sha256"],
                      "inferencePerformed": False, "tokenizerAndStopTokensValidated": False}), flush=True)


if __name__ == "__main__":
    main()
