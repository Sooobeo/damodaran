"""Download one pinned Qwen GGUF and attest an existing CPU runtime; no model load."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[3]
DEST = ROOT / ".translation/qe/llm-candidates/qwen35-9b"
QUANT_REPO = "unsloth/Qwen3.5-9B-GGUF"
QUANT_REV = "3885219b6810b007914f3a7950a8d1b469d598a5"
BASE_REPO = "Qwen/Qwen3.5-9B"
BASE_OBSERVED_REV = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
MODEL_NAME = "Qwen3.5-9B-Q4_K_M.gguf"
MODEL_SIZE = 5680522464
MODEL_SHA = "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8"
RUNTIME_COMMIT = "72797e89198ab564fd0e6baa54ab196e8dd1d884"
RUNTIME_PARENT = ROOT / ".training/comparisons/hy-mt2-30b-a3b-q4"
RUNTIME_DIR = RUNTIME_PARENT / "runtime-cpu"
RUNTIME_ARCHIVE = RUNTIME_PARENT / "llama-b10888-bin-win-cpu-x64.zip"
RUNTIME_SIZE = 18424677
RUNTIME_SHA = "68d0ea47c71a55f6a19219727d39075f08f7da2c9d9073c7b47b3d64a7027284"
CURL = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/curl.exe"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def once(path, payload):
    path = Path(path)
    require(path.resolve().is_relative_to(DEST.resolve()), "outside_owned_destination")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def json_once(path, value):
    once(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def fetch_small(url, limit=8 * 1024 * 1024):
    result = subprocess.run([str(CURL), "--ipv4", "--fail", "--silent", "--show-error", "--location",
                             "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "30",
                             "--max-time", "90", "--max-filesize", str(limit), url],
                            capture_output=True, timeout=100, check=True)
    require(len(result.stdout) <= limit, "metadata_too_large")
    return result.stdout


def evidence(name, url):
    path = DEST / "evidence" / name
    if not path.exists():
        once(path, fetch_small(url))
    require(path.is_file() and not path.is_symlink(), "invalid_evidence_path")
    return {"path": str(path.relative_to(DEST)), "url": url,
            "sha256": sha_file(path), "sizeBytes": path.stat().st_size}


def download_model():
    target = DEST / MODEL_NAME
    if target.exists():
        require(target.is_file() and not target.is_symlink() and target.stat().st_size == MODEL_SIZE
                and sha_file(target) == MODEL_SHA, "existing_model_differs_preserved")
        return
    part = target.with_suffix(target.suffix + ".part")
    require(not part.is_symlink(), "partial_symlink_refused")
    offset = part.stat().st_size if part.exists() else 0
    require(offset <= MODEL_SIZE, "oversize_partial_preserved")
    if offset < MODEL_SIZE:
        url = f"https://huggingface.co/{QUANT_REPO}/resolve/{QUANT_REV}/{MODEL_NAME}"
        print(json.dumps({"event": "download-start", "bytes": offset, "totalBytes": MODEL_SIZE}), flush=True)
        # The observed IPv6 route reset TLS; IPv4 retains normal HTTPS certificate validation.
        subprocess.run([str(CURL), "--ipv4", "--fail", "--silent", "--show-error", "--location",
                        "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "30",
                        "--max-time", "3600", "--max-filesize", str(MODEL_SIZE),
                        "--continue-at", "-", "--output", str(part), url], timeout=3610, check=True)
    require(part.stat().st_size == MODEL_SIZE, "download_incomplete_preserved")
    require(sha_file(part) == MODEL_SHA, "download_sha256_differs_preserved")
    os.link(part, target)  # no overwrite; failed/interrupted .part remains recoverable
    part.unlink()


def runtime_attestation():
    require(os.name == "nt", "windows_required")
    require(RUNTIME_ARCHIVE.stat().st_size == RUNTIME_SIZE and sha_file(RUNTIME_ARCHIVE) == RUNTIME_SHA,
            "existing_runtime_archive_differs")
    files = []
    with zipfile.ZipFile(RUNTIME_ARCHIVE) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            relative = Path(entry.filename)
            require(not relative.is_absolute() and ".." not in relative.parts, "archive_entry_path")
            target = RUNTIME_DIR / relative
            require(target.resolve().is_relative_to(RUNTIME_DIR.resolve()) and target.is_file()
                    and not target.is_symlink(), "runtime_file_missing_or_redirected")
            expected = hashlib.sha256(archive.read(entry)).hexdigest()
            require(sha_file(target) == expected, "runtime_file_differs_from_pinned_archive")
            files.append({"path": str(target.relative_to(ROOT)), "sizeBytes": entry.file_size, "sha256": expected})
    server = RUNTIME_DIR / "llama-server.exe"
    # Exactly these diagnostic flags, no -m or model path. No process is left running.
    version = subprocess.run([str(server), "--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30, check=True)
    version_text = version.stdout + version.stderr
    require("build 10888" in version_text and "72797e891" in version_text and "Windows x86_64" in version_text,
            "runtime_version_differs")
    help_run = subprocess.run([str(server), "--help"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30, check=True)
    help_text = help_run.stdout + help_run.stderr
    required_flags = ["--ctx-size", "--parallel", "--jinja", "--reasoning-format", "--no-webui"]
    require(all(flag in help_text for flag in required_flags), "runtime_required_flags_missing")
    for name, content in [("runtime-version.txt", version_text), ("runtime-help.txt", help_text)]:
        path = DEST / "evidence" / name
        if not path.exists():
            once(path, content.encode("utf-8"))
    return {"readOnlyReuse": True, "tag": "b10888", "commit": RUNTIME_COMMIT,
            "archivePath": str(RUNTIME_ARCHIVE.relative_to(ROOT)), "archiveSha256": RUNTIME_SHA,
            "serverPath": str(server.relative_to(ROOT)), "version": version_text.strip(),
            "files": files, "diagnosticArguments": [["--version"], ["--help"]],
            "weightLoadingPerformed": False, "inferencePerformed": False,
            "supportConclusion": "official qwen35 9B loader and JSON/chat contracts present; execution unvalidated"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="download pinned GGUF and metadata; never starts inference")
    args = parser.parse_args()
    if not args.download:
        print(json.dumps({"destination": str(DEST), "model": MODEL_NAME, "bytes": MODEL_SIZE,
                          "sha256": MODEL_SHA, "conversionRevision": QUANT_REV,
                          "willLoadModel": False, "downloadRequiresFlag": True}, indent=2))
        return
    DEST.mkdir(parents=True, exist_ok=True)
    require(not (DEST / "install-manifest.json").exists(), "installation_manifest_already_exists_preserved")
    require(shutil.disk_usage(DEST).free >= MODEL_SIZE + 1024 ** 3, "insufficient_disk_headroom")
    lock_path = DEST / ".prepare.lock"
    lock = lock_path.open("x", encoding="utf-8")
    try:
        lock.write(str(os.getpid()))
        lock.close()
        base_url = f"https://huggingface.co/{BASE_REPO}/resolve/{BASE_OBSERVED_REV}"
        quant_url = f"https://huggingface.co/{QUANT_REPO}/resolve/{QUANT_REV}"
        source_url = f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{RUNTIME_COMMIT}"
        records = [evidence(name, url) for name, url in [
            ("base-api.json", f"https://huggingface.co/api/models/{BASE_REPO}/revision/{BASE_OBSERVED_REV}?blobs=true"),
            ("quant-api.json", f"https://huggingface.co/api/models/{QUANT_REPO}/revision/{QUANT_REV}?blobs=true"),
            ("base-README.md", base_url + "/README.md"),
            ("base-LICENSE.txt", base_url + "/LICENSE"),
            ("base-config.json", base_url + "/config.json"),
            ("quant-README.md", quant_url + "/README.md"),
            ("runtime-qwen35.cpp", source_url + "/src/models/qwen35.cpp"),
            ("runtime-server-README.md", source_url + "/tools/server/README.md"),
            ("runtime-LICENSE.txt", source_url + "/LICENSE"),
            ("runtime-release.json", "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b10888"),
        ]]
        base = json.loads((DEST / "evidence/base-api.json").read_text(encoding="utf-8"))
        quant = json.loads((DEST / "evidence/quant-api.json").read_text(encoding="utf-8"))
        require(base["sha"] == BASE_OBSERVED_REV and quant["sha"] == QUANT_REV, "hub_revision_differs")
        require(base["gated"] is False and quant["gated"] is False, "gated_repository")
        require(base["cardData"]["license"] == "apache-2.0" and quant["cardData"]["license"] == "apache-2.0",
                "license_metadata_differs")
        asset = next(item for item in quant["siblings"] if item["rfilename"] == MODEL_NAME)
        require(asset["size"] == MODEL_SIZE and asset["lfs"]["sha256"] == MODEL_SHA, "pinned_hub_asset_differs")
        release = json.loads((DEST / "evidence/runtime-release.json").read_text(encoding="utf-8"))
        runtime_asset = next(item for item in release["assets"] if item["name"] == RUNTIME_ARCHIVE.name)
        require(runtime_asset["size"] == RUNTIME_SIZE and runtime_asset["digest"] == "sha256:" + RUNTIME_SHA,
                "official_runtime_asset_differs")
        runtime = runtime_attestation()
        print(json.dumps({"event": "runtime-attested", "files": len(runtime["files"]), "modelLoaded": False}), flush=True)
        download_model()
        manifest = {"version": "qwen35-semantic-review-install-v1", "createdAt": utc(),
            "purpose": "assistant experimental independent bilingual semantic review; not application registration",
            "model": {"repository": QUANT_REPO, "revision": QUANT_REV, "file": MODEL_NAME,
                      "sizeBytes": MODEL_SIZE, "sha256": MODEL_SHA, "localHashVerified": True,
                      "quantization": "Q4_K_M", "publisher": "Unsloth (third-party quantizer, not Qwen)",
                      "baseRepository": BASE_REPO, "observedBaseRevision": BASE_OBSERVED_REV,
                      "upstreamWeightRevisionAttestedByQuantizer": False,
                      "upstreamRevisionLimitation": "observed official repository revision is not proof of the exact weights used to create this GGUF",
                      "visionProjectorDownloaded": False},
            "license": {"model": "Apache-2.0", "quantMetadata": "Apache-2.0", "runtime": "MIT",
                        "gated": False, "privateLocalUse": "permitted under the respective licenses"},
            "runtime": runtime, "evidence": records,
            "networkTransport": {"executable": str(CURL), "sha256": sha_file(CURL),
                                 "ipv4": True, "certificateValidationDisabled": False},
            "preparationCode": {"path": str(Path(__file__).relative_to(ROOT)), "sha256": sha_file(__file__)},
            "inferencePerformed": False, "weightLoadingPerformed": False, "trainingPerformed": False,
            "tokenizerChatTemplateValidatedByInference": False, "qualityValidated": False,
            "existingTranslationComparisonsReplaced": False}
        json_once(DEST / "install-manifest.json", manifest)
        print(json.dumps({"event": "installed", "manifest": str(DEST / "install-manifest.json"),
                          "sha256": sha_file(DEST / "install-manifest.json"), "modelLoaded": False}), flush=True)
    finally:
        lock.close()
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if DEST.exists():
            json_once(DEST / f"prepare-failure-{time.time_ns()}.json",
                      {"at": utc(), "type": type(error).__name__, "message": str(error),
                       "inferencePerformed": False, "weightLoadingPerformed": False})
        raise
