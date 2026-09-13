"""Small, network-free installer checks. No model load or real asset hashing."""
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import setup_translategemma27 as setup


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b"known fixture bytes"
        self.asset = {"name": "model.gguf", "url": "https://example.invalid/file", "size": len(self.data),
                      "sha256": hashlib.sha256(self.data).hexdigest()}

    def test_new_and_existing_are_verified_without_overwrite(self):
        with patch.object(setup.urllib.request, "urlopen", return_value=Response(self.data)) as request:
            result = setup.download(self.asset, self.root)
            self.assertEqual(result.read_bytes(), self.data)
            setup.download(self.asset, self.root)
            self.assertEqual(request.call_count, 1)
        result.write_bytes(b"mutation")
        with self.assertRaises(ValueError):
            setup.download(self.asset, self.root)
        self.assertEqual(result.read_bytes(), b"mutation")

    def test_empty_part_resumes(self):
        (self.root / "model.gguf.part").touch()
        with patch.object(setup.urllib.request, "urlopen", return_value=Response(self.data)):
            self.assertEqual(setup.download(self.asset, self.root).read_bytes(), self.data)

    def test_exact_range_resumes(self):
        (self.root / "model.gguf.part").write_bytes(self.data[:3])
        response = Response(self.data[3:], 206, {"Content-Range": f"bytes 3-{len(self.data)-1}/{len(self.data)}"})
        with patch.object(setup.urllib.request, "urlopen", return_value=response) as request:
            setup.download(self.asset, self.root)
            self.assertEqual(request.call_args.args[0].get_header("Range"), "bytes=3-")

    def test_invalid_range_does_not_append(self):
        part = self.root / "model.gguf.part"
        part.write_bytes(self.data[:3])
        for value in ("bytes 3-4/19", "bytes 2-18/19", "bytes 3-18/*", "bytes 3-18/20"):
            with self.subTest(value=value), patch.object(setup.urllib.request, "urlopen", return_value=Response(self.data[3:], 206, {"Content-Range": value})):
                with self.assertRaises(ValueError):
                    setup.download(self.asset, self.root)
                self.assertEqual(part.read_bytes(), self.data[:3])

    def test_overflow_does_not_write_excess(self):
        with patch.object(setup.urllib.request, "urlopen", return_value=Response(self.data + b"x")):
            with self.assertRaises(ValueError):
                setup.download(self.asset, self.root)
        self.assertFalse((self.root / "model.gguf").exists())
        self.assertEqual((self.root / "model.gguf.part").read_bytes(), b"")

    def test_wrong_hash_is_preserved_without_publication(self):
        wrong = b"X" * len(self.data)
        with patch.object(setup.urllib.request, "urlopen", return_value=Response(wrong)):
            with self.assertRaises(ValueError):
                setup.download(self.asset, self.root)
        self.assertEqual((self.root / "model.gguf.part").read_bytes(), wrong)
        self.assertFalse((self.root / "model.gguf").exists())

    def test_publication_idempotent_but_immutable(self):
        path = self.root / "manifest.json"
        setup.json_once(path, {"ok": True})
        before = path.read_bytes()
        setup.json_once(path, {"ok": True})
        with self.assertRaises(ValueError):
            setup.json_once(path, {"ok": False})
        self.assertEqual(path.read_bytes(), before)

    def test_archive_traversal_rejected(self):
        archive = self.root / "bad.zip"
        with zipfile.ZipFile(archive, "w") as out:
            out.writestr("../escape", b"bad")
        with self.assertRaises(ValueError):
            setup.archive_tools.archive_inventory(archive)

    def test_valid_runtime_pending_resumes_but_mutation_rejected(self):
        archive = self.root / "runtime.zip"
        with zipfile.ZipFile(archive, "w") as out:
            out.writestr("llama-server.exe", b"synthetic exe")
            out.writestr("library.dll", b"synthetic dll")
        pending = self.root / "runtime.pending"
        pending.mkdir()
        (pending / "library.dll").write_bytes(b"synthetic dll")
        with patch.object(setup, "runtime_archive", return_value=archive):
            setup.install_runtime(self.root)
            setup.install_runtime(self.root)
            (self.root / "runtime/library.dll").write_bytes(b"wrong")
            with self.assertRaises(ValueError):
                setup.install_runtime(self.root)

    def test_bounded_gguf_checks_special_tokens_and_template(self):
        def text(value):
            encoded = value.encode()
            return struct.pack("<Q", len(encoded)) + encoded
        values = {"general.architecture": (8, text("gemma3")),
                  "tokenizer.ggml.bos_token_id": (4, struct.pack("<I", 2)),
                  "tokenizer.ggml.eos_token_id": (4, struct.pack("<I", 106)),
                  "tokenizer.chat_template": (8, text("source_lang_code target_lang_code"))}
        tokens = [setup.TOKEN_STRINGS.get(i, "fixture") for i in range(107)]
        values["tokenizer.ggml.tokens"] = (9, struct.pack("<IQ", 8, len(tokens)) + b"".join(map(text, tokens)))
        raw = b"GGUF" + struct.pack("<IQQ", 3, 1, len(values))
        raw += b"".join(text(k) + struct.pack("<I", kind) + value for k, (kind, value) in values.items())
        path = self.root / "header.gguf"
        path.write_bytes(raw)
        template, contract = setup.gguf_contract(path)
        self.assertFalse(contract["inferencePerformed"])
        self.assertEqual(contract["documentedInputTokenLimit"], 2048)
        self.assertEqual(contract["metadataSha256"], hashlib.sha256(raw).hexdigest())
        path.write_bytes(raw.replace(b"<bos>", b"wrong"))
        with self.assertRaises(ValueError):
            setup.gguf_contract(path)


if __name__ == "__main__":
    unittest.main()
