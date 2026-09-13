"""Pinned TranslateGemma 12B/27B Q4_K_M installation; never runs a model."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import time
import urllib.request
import zipfile

import setup_hymt as archive_tools

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / ".training/comparisons/translategemma-27b-q4"
REVISION = "0f2c24b456631d519a919e20429119bbd524d86e"
REPOSITORY = "mradermacher/translategemma-27b-it-GGUF"
BASE_REVISION_OBSERVED = "7d10f0b72f89a2d0f268cea30727d8b77c0d25c2"
MODEL = {"name": "translategemma-27b-it.Q4_K_M.gguf", "size": 16546704480,
         "sha256": "7f1e67c4ecfec676b38c1ea2ef85c46fafe2f02d3c050fb9540e51787405d8a3"}
MODEL["url"] = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{MODEL['name']}"
README = {"name": "conversion-README.md", "size": 4189,
          "sha256": "fe6c4ba8c228d151c0e21b4edf05acfdab8015fb7ba65c3306e22bf39819a239",
          "url": f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/README.md"}
RUNTIME = dict(archive_tools.RUNTIME)
TOKEN_STRINGS = {0: "<pad>", 1: "<eos>", 2: "<bos>", 105: "<start_of_turn>", 106: "<end_of_turn>"}
PROFILES = {"27b": {"repository": REPOSITORY, "revision": REVISION, "baseRevision": BASE_REVISION_OBSERVED,
                     "model": MODEL, "readme": README, "dest": DEST}}
_repo12 = "mradermacher/translategemma-12b-it-GGUF"
_revision12 = "fdf84c9f6fe14e69d58814f14e7b5b63bb6a1b28"
PROFILES["12b"] = {"repository": _repo12, "revision": _revision12,
    "baseRevision": "d1b225e1caa17f1ddc7e62065d8637d0923f34e2",
    "dest": ROOT / ".training/comparisons/translategemma-12b-q4",
    "model": {"name": "translategemma-12b-it.Q4_K_M.gguf", "size": 7300794112,
              "sha256": "b7aac4b4be7ab0c49b6556c29c4467e74313df7f1e95d9f9676bb2adf0afa528",
              "url": f"https://huggingface.co/{_repo12}/resolve/{_revision12}/translategemma-12b-it.Q4_K_M.gguf"},
    "readme": {"name": "conversion-README.md", "size": 4179,
               "sha256": "b45deaa1fddddab05686d9714e7ce1b7bb7b3e9b210b77d74148361006515458",
               "url": f"https://huggingface.co/{_repo12}/resolve/{_revision12}/README.md"}}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return archive_tools.digest(path)


def plain(path):
    require(path.resolve() == path.absolute(), "Path must not traverse a link or junction")
    if path.exists():
        require(not path.is_symlink() and not getattr(path.stat(), "st_file_attributes", 0) & 0x400,
                "Reparse points are not allowed")


def bytes_once(path, content):
    plain(path)
    if path.exists():
        require(path.read_bytes() == content, "Existing immutable file differs")
        return
    pending = path.with_name(path.name + ".pending")
    plain(pending)
    if pending.exists():
        require(pending.read_bytes() == content, "Existing publication staging differs")
    else:
        with pending.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    os.link(pending, path)
    pending.unlink()


def json_once(path, value):
    bytes_once(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def verified_asset(path, asset):
    plain(path)
    require(path.is_file() and path.stat().st_size == asset["size"] and digest(path) == asset["sha256"],
            "Pinned asset differs; existing bytes were preserved")
    return path


def download(asset, dest=DEST):
    """A failed transfer retains its prefix; wrong headers never append bytes."""
    plain(dest)
    target = dest / asset["name"]
    plain(target)
    if target.exists():
        return verified_asset(target, asset)
    partial = target.with_name(target.name + ".part")
    plain(partial)
    offset = partial.stat().st_size if partial.exists() else 0
    require(offset <= asset["size"], "Partial file exceeds pinned size")
    if offset < asset["size"]:
        headers = {"User-Agent": "MoonModaran-Exploratory-Install/1.0", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        with urllib.request.urlopen(urllib.request.Request(asset["url"], headers=headers), timeout=60) as response:
            require(response.status == (206 if offset else 200), "Unexpected download status")
            require(response.headers.get("Content-Encoding", "identity") == "identity", "Encoded download rejected")
            if offset:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                require(match is not None and tuple(map(int, match.groups())) ==
                        (offset, asset["size"] - 1, asset["size"]), "Incorrect complete Content-Range")
            length = response.headers.get("Content-Length")
            require(length is None or (re.fullmatch(r"\d+", length) and int(length) == asset["size"] - offset),
                    "Incorrect Content-Length")
            size, started, reported = offset, time.monotonic(), time.monotonic()
            with partial.open("ab" if partial.exists() else "xb") as stream:
                while chunk := response.read(4 * 1024 * 1024):
                    require(size + len(chunk) <= asset["size"], "Response exceeds pinned size")
                    stream.write(chunk)
                    size += len(chunk)
                    if time.monotonic() - reported >= 10:
                        print(json.dumps({"event": "download-progress", "file": asset["name"], "bytes": size,
                                          "total": asset["size"], "seconds": round(time.monotonic() - started, 1)}), flush=True)
                        reported = time.monotonic()
                stream.flush()
                os.fsync(stream.fileno())
    verified_asset(partial, asset)
    os.link(partial, target)
    partial.unlink()
    return target


def runtime_archive(dest=DEST, allow_download=False):
    cached = ROOT / ".training/comparisons/translategemma-4b-q4" / RUNTIME["name"]
    local = dest / RUNTIME["name"]
    if local.exists():
        return verified_asset(local, RUNTIME)
    if cached.exists():
        verified_asset(cached, RUNTIME)
        if allow_download:
            bytes_once(local, cached.read_bytes())
            return verified_asset(local, RUNTIME)
        return cached
    require(allow_download, "Pinned runtime archive unavailable offline")
    return download(RUNTIME, dest)


def install_runtime(dest=DEST):
    archive = runtime_archive(dest, allow_download=True)
    inventory = archive_tools.archive_inventory(archive)
    final, pending = dest / "runtime", dest / "runtime.pending"
    plain(final)
    plain(pending)
    if final.exists():
        archive_tools.verify_runtime(final, inventory)
        return inventory
    pending.mkdir(exist_ok=True)
    archive_tools.verify_runtime(pending, inventory, allow_missing=True)
    with zipfile.ZipFile(archive) as zipped:
        for item in inventory:
            path = pending / item["path"]
            plain(path)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                bytes_once(path, zipped.read(item["path"]))
    archive_tools.verify_runtime(pending, inventory)
    pending.rename(final)
    return inventory


def gguf_contract(path):
    """Read only a bounded metadata prefix, never tensors or executable code."""
    with path.open("rb") as stream:
        data = stream.read(32 * 1024 * 1024)
    reader = io.BytesIO(data)
    formats = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}

    def take(n):
        require(0 <= n <= len(data), "Invalid GGUF length")
        b = reader.read(n)
        require(len(b) == n, "Incomplete bounded GGUF metadata")
        return b

    def number(fmt):
        return struct.unpack("<" + fmt, take(struct.calcsize("<" + fmt)))[0]

    def string():
        return take(number("Q")).decode("utf-8")

    def value(kind, key=None):
        if kind == 8:
            return string()
        if kind == 9:
            element, count = number("I"), number("Q")
            require(element != 9 and count <= 1000000, "Invalid GGUF array")
            selected = {}
            for i in range(count):
                item = value(element)
                if key in {"tokenizer.ggml.tokens", "tokenizer.ggml.token_type"} and i in TOKEN_STRINGS:
                    selected[str(i)] = item
            return {"count": count, "selected": selected}
        require(kind in formats, "Invalid GGUF type")
        return number(formats[kind])

    require(take(4) == b"GGUF" and number("I") == 3, "Expected GGUF v3")
    tensor_count, count = number("Q"), number("Q")
    require(0 < count <= 10000, "Invalid metadata count")
    metadata = {}
    for _ in range(count):
        key = string()
        require(key not in metadata, "Duplicate metadata key")
        metadata[key] = value(number("I"), key)
    require(metadata.get("general.architecture") == "gemma3", "Wrong architecture")
    tokens = metadata.get("tokenizer.ggml.tokens", {})
    require(tokens.get("selected") == {str(k): v for k, v in TOKEN_STRINGS.items()}, "Unexpected special token strings")
    require(metadata.get("tokenizer.ggml.bos_token_id") == 2, "Unexpected BOS")
    require(metadata.get("tokenizer.ggml.eos_token_id") in {1, 106}, "Unexpected EOS metadata")
    template = metadata.get("tokenizer.chat_template")
    require(isinstance(template, str) and "source_lang_code" in template and "target_lang_code" in template,
            "Missing direct translation template")
    contract = {"metadataSha256": hashlib.sha256(data[:reader.tell()]).hexdigest(),
                "metadataBytes": reader.tell(), "tensorCount": tensor_count, "metadataCount": count,
                "metadata": {k: v for k, v in metadata.items() if k != "tokenizer.chat_template"},
                "templateSha256": hashlib.sha256(template.encode("utf-8")).hexdigest(),
                "tokenStrings": {str(k): v for k, v in TOKEN_STRINGS.items()},
                "documentedInputTokenLimit": 2048, "inferencePerformed": False,
                "runtimeTokenizationAndEogValidated": False}
    return template, contract


def identity(model_size="27b"):
    profile = PROFILES[model_size]
    return {"version": "translategemma-large-install-v1", "purpose": "isolated exploratory financial meaning comparison",
            "modelSize": model_size,
            "baseModel": f"google/translategemma-{model_size}-it", "baseModelRevisionObserved": profile["baseRevision"],
            "baseModelRevisionOfConversion": "not attested by quantizer; observed upstream HEAD is not a conversion provenance claim",
            "upstreamRawConfigAccess": "unauthenticated HTTP 401 observed; no access bypass or original-byte equivalence claim",
            "conversionRepository": profile["repository"], "conversionRevision": profile["revision"],
            "conversion": "third-party static Q4_K_M; not Google original precision",
            "license": "gemma", "licenseUrl": "https://ai.google.dev/gemma/terms",
            "officialModelCard": f"https://huggingface.co/google/translategemma-{model_size}-it",
            "documentedInputTokenLimit": 2048, "officialInputMode": "direct translation; text contains only source text",
            "runtimeVersion": "llama.cpp b10874 e2d2c0d6aa9b996d5d3a3c1d5e24c8c19728bb3d Windows x64",
            "model": profile["model"], "runtimeArchive": RUNTIME, "conversionReadme": profile["readme"],
            "inferencePerformed": False, "appDeploymentPerformed": False,
            "setupScriptSha256": digest(Path(__file__)), "archiveHelperSha256": digest(Path(archive_tools.__file__))}


def verify_installation(dest=None, model_size="27b"):
    profile = PROFILES[model_size]
    dest = profile["dest"] if dest is None else dest
    plain(dest)
    manifest = json.loads((dest / "installation-manifest.json").read_text("utf-8"))
    expected = identity(model_size)
    require(set(manifest) == set(expected) | {"createdAt", "runtimeFiles", "contractSha256", "templateSha256"},
            "Unexpected manifest fields")
    require(all(manifest.get(k) == v for k, v in expected.items()), "Installation identity changed")
    require(datetime.fromisoformat(manifest["createdAt"]).utcoffset() is not None, "Missing timestamp timezone")
    inventory = archive_tools.archive_inventory(runtime_archive(dest))
    require(manifest["runtimeFiles"] == inventory, "Runtime inventory differs from fixed ZIP")
    archive_tools.verify_runtime(dest / "runtime", inventory)
    verified_asset(dest / profile["readme"]["name"], profile["readme"])
    verified_asset(dest / profile["model"]["name"], profile["model"])
    template, contract = gguf_contract(dest / profile["model"]["name"])
    require((dest / "chat-template.jinja").read_bytes() == template.encode("utf-8") and
            digest(dest / "chat-template.jinja") == manifest["templateSha256"], "Template bytes differ")
    require(json.loads((dest / "gguf-contract.json").read_text("utf-8")) == contract and
            digest(dest / "gguf-contract.json") == manifest["contractSha256"], "GGUF contract differs")
    return manifest


def install(dest=None, model_size="27b"):
    profile = PROFILES[model_size]
    dest = profile["dest"] if dest is None else dest
    plain(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / "installation-manifest.json").exists():
        return verify_installation(dest, model_size)
    runtime = install_runtime(dest)
    download(profile["readme"], dest)
    model = download(profile["model"], dest)
    template, contract = gguf_contract(model)
    bytes_once(dest / "chat-template.jinja", template.encode("utf-8"))
    json_once(dest / "gguf-contract.json", contract)
    manifest = {**identity(model_size), "createdAt": datetime.now(timezone.utc).isoformat(), "runtimeFiles": runtime,
                "templateSha256": digest(dest / "chat-template.jinja"),
                "contractSha256": digest(dest / "gguf-contract.json")}
    json_once(dest / "installation-manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-size", choices=tuple(PROFILES), required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = verify_installation(model_size=args.model_size) if args.verify_only else install(model_size=args.model_size)
    print(json.dumps({"event": "weights-runtime-metadata-verified", "modelSha256": result["model"]["sha256"],
                      "inferencePerformed": False,
                      "manifest": str(PROFILES[args.model_size]["dest"] / "installation-manifest.json")}), flush=True)


if __name__ == "__main__":
    main()
