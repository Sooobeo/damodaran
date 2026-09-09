"""Downloads one official Argos model during explicit setup, never at runtime."""
from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
import sys
import urllib.request
import zipfile

from runtime import APP_ROOT, RUNTIME_ROOT, MODEL, environment, runtime_version, sha256, verify_manifest, LocalTranslator

MODEL_URL = "https://argos-net.com/v1/translate-en_ko-1_1.argosmodel"
MODEL_ARCHIVE_SHA256 = "e03d8e65e6d44525ec5808c3409fcf8728c76c2c76925372b6d3dc3278de17fc"


def download_model() -> Path:
    downloads = RUNTIME_ROOT / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / "translate-en_ko-1_1.argosmodel"
    if not archive.is_file() or not zipfile.is_zipfile(archive) or sha256(archive) != MODEL_ARCHIVE_SHA256:
        print("영어 → 한국어 모델 약 115 MB를 내려받습니다.", flush=True)
        temporary = archive.with_suffix(".partial")
        request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; DamodaranLocalStudy/1.0)"})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 200 * 1024 * 1024:
                    raise ValueError("Model download exceeds expected size")
                output.write(chunk)
        if sha256(temporary) != MODEL_ARCHIVE_SHA256:
            raise ValueError("Downloaded model hash differs from the pinned release")
        temporary.replace(archive)
    return archive


def install() -> None:
    environment()
    archive = download_model()
    packages = RUNTIME_ROOT / "packages"
    packages.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        total = 0
        for member in source.infolist():
            target = (packages / member.filename).resolve()
            if (not target.is_relative_to(packages.resolve()) or Path(member.filename).is_absolute()
                    or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Unsafe model archive")
            total += member.file_size
            if total > 600 * 1024 * 1024:
                raise ValueError("Model archive too large")
        source.extractall(packages)
    candidates = []
    for metadata_file in packages.glob("*/metadata.json"):
        metadata = json.loads(metadata_file.read_text("utf-8"))
        if metadata.get("from_code") == "en" and metadata.get("to_code") == "ko" and metadata.get("package_version") == "1.1":
            candidates.append(metadata_file.parent)
    if len(candidates) != 1:
        raise ValueError("Expected English Korean model missing")
    model = candidates[0]
    files = [{"path": file.relative_to(APP_ROOT).as_posix(), "size": file.stat().st_size, "sha256": sha256(file)} for file in sorted(model.rglob("*")) if file.is_file()]
    canonical = json.dumps(files, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    manifest = {
        "schemaVersion": 1, "provider": "argos", "model": MODEL,
        "modelHash": hashlib.sha256(canonical).hexdigest(),
        "installedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pythonPath": Path(sys.executable).relative_to(APP_ROOT).as_posix(),
        "pythonVersion": sys.version.split()[0],
        "modelPath": model.relative_to(APP_ROOT).as_posix(),
        "runtimeVersion": runtime_version(),
        "archiveSha256": sha256(archive), "downloadUrl": MODEL_URL,
        "modelFiles": files,
    }
    # Verify actual Korean inference offline before reporting setup success.
    translator = LocalTranslator(manifest)
    translated = translator.translate_plain("The value of money changes over time.")
    if not any("가" <= char <= "힣" for char in translated):
        raise ValueError("Korean inference verification failed")
    target = RUNTIME_ROOT / "manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
    verify_manifest()
    print("모델 해시와 실제 한국어 번역을 확인했습니다.", flush=True)


if __name__ == "__main__":
    if "--download-only" in sys.argv:
        download_model()
    elif "--verify" in sys.argv:
        verify_manifest()
        print("설치 무결성 확인 완료.", flush=True)
    else:
        install()
