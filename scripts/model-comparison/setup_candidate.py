"""Download a pinned exploratory runtime and public third-party model conversion.

Uses no authentication or paid services. Does not alter either Python environment,
the training runs, the application provider, or personal data.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / ".training" / "comparisons" / "translategemma-4b-q4"
ASSETS = [
    {"name": "llama-b10874-bin-win-vulkan-x64.zip", "url": "https://github.com/ggml-org/llama.cpp/releases/download/b10874/llama-b10874-bin-win-vulkan-x64.zip", "size": 35789222, "sha256": "0113e9b49a5d979b32805092740ce97b9497494cdb308cf9568577da3e78e3c0"},
    {"name": "translategemma-4b-it.Q4_K_M.gguf", "url": "https://huggingface.co/mradermacher/translategemma-4b-it-GGUF/resolve/35a7486e128b19642cdc72d7b91b21ba388aaf42/translategemma-4b-it.Q4_K_M.gguf", "size": 2489909760, "sha256": "81200d03e843d2ec1ece6eeafe7d13cb6e5211e1fcd336ade55790b683a08330"},
]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def download(asset):
    target = DEST / asset["name"]
    if target.exists():
        if target.stat().st_size == asset["size"] and digest(target) == asset["sha256"]:
            return target
        raise ValueError("Existing destination differs from pinned asset")
    partial = target.with_suffix(target.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "Damodaran-Exploratory-Comparison/1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(asset["url"], headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        if offset and (response.status != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-")):
            raise ValueError("Server did not honor resumable download")
        size, started, reported = offset, time.monotonic(), time.monotonic()
        with partial.open("ab" if offset else "xb") as output:
            while chunk := response.read(4 * 1024 * 1024):
                output.write(chunk)
                size += len(chunk)
                if size > asset["size"]:
                    raise ValueError("Asset exceeds pinned size")
                if time.monotonic() - reported >= 5:
                    print(json.dumps({"download": asset["name"], "bytes": size, "total": asset["size"], "seconds": round(time.monotonic() - started, 1)}), flush=True)
                    reported = time.monotonic()
    if partial.stat().st_size != asset["size"] or digest(partial) != asset["sha256"]:
        raise ValueError("Downloaded asset integrity check failed")
    partial.replace(target)
    return target


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    archive = download(ASSETS[0])
    runtime = DEST / "runtime"
    runtime.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            candidate = (runtime / entry.filename).resolve()
            if not candidate.is_relative_to(runtime.resolve()):
                raise ValueError("Unsafe archive member")
        zipped.extractall(runtime)
    print(json.dumps({"runtimeReady": str(runtime)}), flush=True)
    download(ASSETS[1])
    manifest = {
        "purpose": "exploratory local inference, not training or app deployment",
        "baseModel": "google/translategemma-4b-it",
        "baseModelRevisionObserved": "10042cb0e6e7fdce748996a71dc3dc432a4e0c89",
        "baseModelRevisionOfConversion": "not attested by quantizer; not assumed equal to observed upstream head",
        "conversionRepository": "mradermacher/translategemma-4b-it-GGUF",
        "conversionRevision": "35a7486e128b19642cdc72d7b91b21ba388aaf42",
        "conversion": "third-party Q4_K_M GGUF, not Google's original precision weights",
        "license": "gemma", "licenseUrl": "https://ai.google.dev/gemma/terms",
        "runtime": "ggml-org/llama.cpp b10874 Windows Vulkan x64",
        "runtimeCommit": "e2d2c0d6aa9b996d5d3a3c1d5e24c8c19728bb3d",
        "assets": ASSETS,
    }
    (DEST / "installation-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(json.dumps({"verified": True, "manifest": str(DEST / "installation-manifest.json")}), flush=True)


if __name__ == "__main__":
    main()
