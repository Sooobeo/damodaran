"""Network-free installer audit with tiny synthetic assets only.

These fixtures are not installation/model/inference success claims. They do
not read or modify real model, test, prediction, or environment files.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import setup_hymt as installer


class Response:
    def __init__(self, chunks, status=200, headers=None):
        self.chunks = iter(chunks)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        chunk = next(self.chunks, b"")
        if isinstance(chunk, Exception):
            raise chunk
        return chunk


class HyMTInstallerAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hymt-install-audit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dest = self.root / ".training/comparisons/hy-mt2-7b-q8"
        self.dest.mkdir(parents=True)
        self.payload = b"tiny-fixture-not-a-model"
        self.asset = {"name": "fixture-model.gguf", "url": "https://example.invalid/fixture-model",
                      "size": len(self.payload), "sha256": hashlib.sha256(self.payload).hexdigest()}
        self.runtime = {"name": "fixture-runtime.zip", "url": "https://example.invalid/fixture-runtime"}
        for field, value in (("ROOT", self.root), ("DEST", self.dest), ("MODEL", self.asset), ("RUNTIME", self.runtime)):
            patcher = patch.object(installer, field, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(installer.urllib.request, "urlopen", side_effect=AssertionError("No live network access"))
        self.network = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(installer, "print", create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.prepare_archive([("llama-server.exe", b"fixture executable bytes, not runnable"),
                              ("lib/ggml.dll", b"fixture runtime library")])

    def prepare_archive(self, entries):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in entries:
                archive.writestr(name, content)
        payload = buffer.getvalue()
        self.runtime.update({"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
        cached = self.root / ".training/comparisons/translategemma-4b-q4" / self.runtime["name"]
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(payload)
        return cached

    def response(self, chunks, offset=0, status=None, content_range=None):
        headers = {"Content-Range": content_range or f"bytes {offset}-{len(self.payload) - 1}/{len(self.payload)}"} if offset else {}
        return patch.object(installer.urllib.request, "urlopen", return_value=Response(chunks, status or (206 if offset else 200), headers))

    def completed_installation(self):
        with self.response([self.payload]):
            installer.main()
        return json.loads((self.dest / "installation-manifest.json").read_text("utf-8"))

    def test_pinned_download_is_published_only_after_full_size_and_hash_match(self):
        with self.response([self.payload]) as request:
            path = installer.download(self.asset)
        self.assertEqual(path.read_bytes(), self.payload)
        self.assertFalse(path.with_name(path.name + ".part").exists())
        self.assertIsNone(request.call_args.args[0].get_header("Range"))
        original_time = path.stat().st_mtime_ns
        self.assertEqual(installer.download(self.asset), path)
        self.assertEqual(path.stat().st_mtime_ns, original_time)

    def test_interrupted_nonempty_download_resumes_with_exact_requested_offset(self):
        prefix = self.payload[:7]
        with self.response([prefix, ConnectionResetError("synthetic disconnect")]):
            with self.assertRaises(ConnectionResetError):
                installer.download(self.asset)
        partial = self.dest / (self.asset["name"] + ".part")
        self.assertEqual(partial.read_bytes(), prefix)
        with self.response([self.payload[7:]], offset=7) as request:
            result = installer.download(self.asset)
        self.assertEqual(request.call_args.args[0].get_header("Range"), "bytes=7-")
        self.assertEqual(result.read_bytes(), self.payload)

    def test_zero_byte_partial_from_early_disconnect_should_resume(self):
        with self.response([ConnectionResetError("disconnect before first byte")]):
            with self.assertRaises(ConnectionResetError):
                installer.download(self.asset)
        partial = self.dest / (self.asset["name"] + ".part")
        self.assertEqual(partial.stat().st_size, 0)
        with self.response([self.payload]):
            result = installer.download(self.asset)
        self.assertEqual(result.read_bytes(), self.payload)

    def test_ignored_or_wrong_start_range_preserves_existing_partial(self):
        partial = self.dest / (self.asset["name"] + ".part")
        partial.write_bytes(self.payload[:4])
        for status, content_range in ((200, "bytes 4-20/21"), (206, "bytes 5-20/21")):
            with self.subTest(status=status, content_range=content_range):
                with self.response([self.payload], offset=4, status=status, content_range=content_range):
                    with self.assertRaisesRegex(ValueError, "honor"):
                        installer.download(self.asset)
                self.assertEqual(partial.read_bytes(), self.payload[:4])

    def test_inconsistent_content_range_end_and_total_should_be_rejected(self):
        partial = self.dest / (self.asset["name"] + ".part")
        partial.write_bytes(self.payload[:4])
        for header in ("bytes 4-4/999999", f"bytes 4-4/{len(self.payload)}",
                       f"bytes 4-{len(self.payload)}/{len(self.payload)}", "bytes 4-20/*",
                       f"bytes 4-{len(self.payload)-1}/{len(self.payload)} trailing"):
            with self.subTest(header=header), self.response([self.payload[4:]], offset=4, content_range=header):
                with self.assertRaises(ValueError):
                    installer.download(self.asset)
                self.assertEqual(partial.read_bytes(), self.payload[:4])

    def test_mismatched_content_length_is_rejected_before_partial_write(self):
        response = Response([self.payload], headers={"Content-Length": str(len(self.payload) + 1)})
        with patch.object(installer.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "Content-Length"):
                installer.download(self.asset)
        self.assertFalse((self.dest / (self.asset["name"] + ".part")).exists())

    def test_short_response_retains_verified_prefix_for_later_resume(self):
        with self.response([self.payload[:4]]):
            with self.assertRaisesRegex(ValueError, "integrity"):
                installer.download(self.asset)
        partial = self.dest / (self.asset["name"] + ".part")
        self.assertEqual(partial.read_bytes(), self.payload[:4])
        with self.response([self.payload[4:]], offset=4):
            self.assertEqual(installer.download(self.asset).read_bytes(), self.payload)

    def test_bad_complete_hash_never_creates_target_and_is_preserved(self):
        wrong = b"x" * self.asset["size"]
        with self.response([wrong]):
            with self.assertRaisesRegex(ValueError, "integrity"):
                installer.download(self.asset)
        target = self.dest / self.asset["name"]
        self.assertFalse(target.exists())
        partial = target.with_name(target.name + ".part")
        self.assertEqual(partial.read_bytes(), wrong)
        with self.assertRaisesRegex(ValueError, "integrity"):
            installer.download(self.asset)
        self.assertEqual(partial.read_bytes(), wrong)

    def test_existing_bad_target_and_oversize_partial_are_not_overwritten(self):
        target = self.dest / self.asset["name"]
        target.write_bytes(b"preserve existing")
        with self.assertRaisesRegex(ValueError, "preserved"):
            installer.download(self.asset)
        self.assertEqual(target.read_bytes(), b"preserve existing")
        other = {**self.asset, "name": "other-fixture.gguf"}
        partial = self.dest / (other["name"] + ".part")
        partial.write_bytes(b"z" * (other["size"] + 1))
        with self.assertRaisesRegex(ValueError, "exceeds"):
            installer.download(other)
        self.assertEqual(partial.stat().st_size, other["size"] + 1)

    def test_runtime_cached_archive_is_checked_and_copied_into_isolated_directory(self):
        archive = self.root / ".training/comparisons/translategemma-4b-q4" / self.runtime["name"]
        before = archive.read_bytes()
        files = installer.install_runtime()
        self.assertEqual({item["path"] for item in files}, {"llama-server.exe", "lib/ggml.dll"})
        self.assertEqual(archive.read_bytes(), before)
        self.assertEqual(installer.install_runtime(), files)
        self.assertFalse((self.dest / "runtime.pending").exists())
        self.network.assert_not_called()

    def test_zip_escape_is_rejected_without_writing_outside_installation(self):
        self.prepare_archive([("../escape.exe", b"no escape")])
        with self.assertRaisesRegex(ValueError, "escaped"):
            installer.install_runtime()
        self.assertFalse((self.dest / "escape.exe").exists())
        self.assertFalse((self.dest / "runtime").exists())

    def test_zip_symlink_is_rejected(self):
        info = zipfile.ZipInfo("linked-library")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        self.prepare_archive([(info, b"../elsewhere")])
        with self.assertRaisesRegex(ValueError, "symlink"):
            installer.install_runtime()
        self.assertFalse((self.dest / "runtime").exists())

    def test_consistent_interrupted_runtime_staging_should_be_resumable(self):
        pending = self.dest / "runtime.pending"
        pending.mkdir()
        (pending / "llama-server.exe").write_bytes(b"fixture executable bytes, not runnable")
        original_time = (pending / "llama-server.exe").stat().st_mtime_ns
        inventory = installer.install_runtime()
        self.assertEqual(len(inventory), 2)
        self.assertTrue((self.dest / "runtime/lib/ggml.dll").is_file())
        self.assertEqual((self.dest / "runtime/llama-server.exe").stat().st_mtime_ns, original_time)

    def test_damaged_or_unexpected_staging_file_is_preserved_without_partial_repair(self):
        pending = self.dest / "runtime.pending"
        pending.mkdir()
        extra = pending / "unexpected.dll"
        extra.write_bytes(b"preserve this unknown file")
        with self.assertRaisesRegex(ValueError, "extra"):
            installer.install_runtime()
        self.assertEqual(list(pending.iterdir()), [extra])
        extra.unlink()
        damaged = pending / "llama-server.exe"
        damaged.write_bytes(b"incomplete executable")
        with self.assertRaisesRegex(ValueError, "differs"):
            installer.install_runtime()
        self.assertEqual(damaged.read_bytes(), b"incomplete executable")
        self.assertEqual(list(pending.iterdir()), [damaged])
        self.assertFalse((self.dest / "runtime").exists())

    def test_windows_ambiguous_and_duplicate_zip_names_are_rejected_before_extraction(self):
        for name in ("C:/outside.exe", "lib\\evil.dll", "lib/../evil.dll", "NUL.dll", "lib./a.dll"):
            with self.subTest(name=name):
                # ZipInfo normally rewrites backslashes on Windows; construct
                # the literal hostile ZIP member to exercise the read boundary.
                member = zipfile.ZipInfo("placeholder")
                member.filename = name
                self.prepare_archive([("llama-server.exe", b"fixture"), (member, b"invalid")])
                with self.assertRaisesRegex(ValueError, "escaped"):
                    installer.install_runtime()
                self.assertFalse((self.dest / "runtime.pending").exists())
        self.prepare_archive([("llama-server.exe", b"one"), ("LLAMA-SERVER.EXE", b"two")])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            installer.install_runtime()
        self.assertFalse((self.dest / "runtime.pending").exists())

    def test_existing_runtime_changes_are_rejected_without_repairing_them(self):
        installer.install_runtime()
        library = self.dest / "runtime/lib/ggml.dll"
        library.write_bytes(b"preserve modified library")
        with self.assertRaisesRegex(ValueError, "differs"):
            installer.install_runtime()
        self.assertEqual(library.read_bytes(), b"preserve modified library")

    def test_complete_setup_and_rerun_do_not_claim_inference_or_modify_files(self):
        manifest = self.completed_installation()
        self.assertFalse(manifest["inferencePerformed"])
        self.assertFalse(manifest["tokenizerAndStopTokensValidated"])
        before = {p.relative_to(self.dest).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.dest.rglob("*") if p.is_file()}
        installer.main()
        self.assertEqual(before, {p.relative_to(self.dest).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                                 for p in self.dest.rglob("*") if p.is_file()})
        self.network.assert_not_called()

    def test_empty_manifest_runtime_inventory_should_not_verify_as_installed(self):
        invalid = self.completed_installation()
        invalid["runtimeFiles"] = []
        for path in (self.dest / "runtime").rglob("*"):
            if path.is_file():
                path.unlink()
        (self.dest / "installation-manifest.json").write_text(json.dumps(invalid), "utf-8")
        with self.assertRaisesRegex(ValueError, "pinned ZIP"):
            installer.verify_installation()

    def test_changed_setup_only_provenance_should_not_verify(self):
        manifest = self.completed_installation()
        for key, value in (("version", 2), ("version", True), ("inferencePerformed", True),
                           ("inferencePerformed", 0), ("tokenizerAndStopTokensValidated", True),
                           ("conversionRevision", "changed revision"), ("conversionRepository", "other/repo"),
                           ("baseModel", "other/model"), ("license", "other license"),
                           ("licenseUrl", "https://example.invalid/license"), ("setupScriptSha256", "0" * 64),
                           ("createdAt", "2026-09-10T01:00:00"), ("runtimeVersion", "later release")):
            with self.subTest(key=key, value=value):
                invalid = {**manifest, key: value}
                (self.dest / "installation-manifest.json").write_text(json.dumps(invalid), "utf-8")
                with self.assertRaises(ValueError):
                    installer.verify_installation()
        for key in manifest:
            with self.subTest(missing=key):
                invalid = dict(manifest)
                del invalid[key]
                (self.dest / "installation-manifest.json").write_text(json.dumps(invalid), "utf-8")
                with self.assertRaisesRegex(ValueError, "fields"):
                    installer.verify_installation()

    def test_partial_or_modified_runtime_inventory_is_rejected_even_if_disk_agrees(self):
        manifest = self.completed_installation()
        manifest["runtimeFiles"] = [item for item in manifest["runtimeFiles"] if item["path"] != "lib/ggml.dll"]
        (self.dest / "runtime/lib/ggml.dll").unlink()
        (self.dest / "installation-manifest.json").write_text(json.dumps(manifest), "utf-8")
        with self.assertRaisesRegex(ValueError, "pinned ZIP"):
            installer.verify_installation()

    def test_verification_requires_existing_pinned_archive_and_never_downloads(self):
        self.completed_installation()
        archive = self.root / ".training/comparisons/translategemma-4b-q4" / self.runtime["name"]
        original = archive.read_bytes()
        archive.unlink()
        with self.assertRaisesRegex(ValueError, "offline verification"):
            installer.verify_installation()
        archive.write_bytes(b"x" * len(original))
        with self.assertRaisesRegex(ValueError, "archive differs"):
            installer.verify_installation()
        self.assertEqual(archive.read_bytes(), b"x" * len(original))
        self.network.assert_not_called()

    def test_legacy_license_requires_all_three_sealed_historical_files(self):
        manifest = self.completed_installation()
        manifest["licenseUrl"] = installer.LICENSE_URL.removesuffix(".txt")
        original = self.dest / "setup-original.py"
        original.write_bytes(b"synthetic historical setup source, never executed")
        manifest["setupScriptSha256"] = installer.digest(original)
        manifest_path = self.dest / "installation-manifest.json"
        manifest_path.write_text(json.dumps(manifest), "utf-8")
        notes = self.dest / "installation-notes.json"
        notes.write_text(json.dumps({"originalManifestSha256": installer.digest(manifest_path),
                                    "correctedLicenseUrl": installer.LICENSE_URL}), "utf-8")
        with self.assertRaisesRegex(ValueError, "Historical"):
            installer.verify_installation()
        with patch.object(installer, "HISTORICAL_MANIFEST_SHA256", installer.digest(manifest_path)), \
             patch.object(installer, "HISTORICAL_SETUP_SHA256", installer.digest(original)), \
             patch.object(installer, "HISTORICAL_NOTES_SHA256", installer.digest(notes)):
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (manifest_path, notes, original)}
            self.assertEqual(installer.verify_installation(), manifest)
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})
            for path in before:
                with self.subTest(changed=path.name):
                    original_bytes = path.read_bytes()
                    path.write_bytes(original_bytes + b" ")
                    with self.assertRaisesRegex(ValueError, "Historical"):
                        installer.verify_installation()
                    path.write_bytes(original_bytes)
            for path in (notes, original):
                with self.subTest(missing=path.name):
                    original_bytes = path.read_bytes()
                    path.unlink()
                    with self.assertRaisesRegex(ValueError, "Historical"):
                        installer.verify_installation()
                    path.write_bytes(original_bytes)

    def test_new_manifest_uses_correct_license_and_current_script(self):
        manifest = self.completed_installation()
        self.assertEqual(manifest["licenseUrl"], installer.LICENSE_URL)
        self.assertTrue(manifest["licenseUrl"].endswith("/LICENSE.txt"))
        self.assertEqual(manifest["setupScriptSha256"], installer.digest(Path(installer.__file__)))
        self.assertFalse((self.dest / "installation-notes.json").exists())


if __name__ == "__main__":
    unittest.main()
