"""Small-file integrity/resume checks; no real model or network requests."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import setup_hymt30 as setup


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="damodaran-hymt30-setup-")
        self.directory = Path(self.temporary.name)
        self.destination = patch.object(setup, "DEST", self.directory)
        self.destination.start()
        self.body = b"public-test-file"
        self.asset = {"name": "tiny.gguf", "url": "https://example.invalid/tiny",
                      "size": len(self.body), "sha256": hashlib.sha256(self.body).hexdigest()}

    def tearDown(self):
        self.destination.stop()
        resolved = self.directory.resolve()
        self.assertTrue(resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.assertTrue(resolved.name.startswith("damodaran-hymt30-setup-"))
        self.temporary.cleanup()

    def test_complete_download_and_offline_reuse(self):
        with patch.object(setup.urllib.request, "urlopen", return_value=Response(self.body)) as network:
            result = setup.download(self.asset)
            self.assertEqual(result.read_bytes(), self.body)
            setup.download(self.asset)
            self.assertEqual(network.call_count, 1)
        self.assertFalse(result.with_name(result.name + ".part").exists())

    def test_resume_validates_range(self):
        (self.directory / "tiny.gguf.part").write_bytes(self.body[:4])
        response = Response(self.body[4:], 206, {"Content-Range": f"bytes 4-{len(self.body)-1}/{len(self.body)}"})
        with patch.object(setup.urllib.request, "urlopen", return_value=response) as network:
            setup.download(self.asset)
            self.assertEqual(network.call_args.args[0].get_header("Range"), "bytes=4-")

    def test_ignored_range_preserves_partial(self):
        part = self.directory / "tiny.gguf.part"
        part.write_bytes(self.body[:4])
        with patch.object(setup.urllib.request, "urlopen", return_value=Response(self.body)):
            with self.assertRaisesRegex(ValueError, "range_status_differs"):
                setup.download(self.asset)
        self.assertEqual(part.read_bytes(), self.body[:4])

    def test_wrong_sha_preserves_unpublished_partial(self):
        with patch.object(setup.urllib.request, "urlopen", return_value=Response(b"x" * len(self.body))):
            with self.assertRaisesRegex(ValueError, "asset_sha256_differs"):
                setup.download(self.asset)
        self.assertFalse((self.directory / "tiny.gguf").exists())
        self.assertTrue((self.directory / "tiny.gguf.part").exists())

    def test_existing_changed_file_not_overwritten(self):
        target = self.directory / "tiny.gguf"
        target.write_bytes(b"unchanged-personal-test")
        with patch.object(setup.urllib.request, "urlopen") as network:
            with self.assertRaises(ValueError):
                setup.download(self.asset)
            network.assert_not_called()
        self.assertEqual(target.read_bytes(), b"unchanged-personal-test")

    def test_memory_gate_does_not_count_swap_or_shared_gpu_twice(self):
        self.assertFalse(setup.memory_preflight(12 * 1024 ** 3)["passed"])
        self.assertTrue(setup.memory_preflight(setup.MODEL["size"] + 4 * 1024 ** 3)["passed"])

    def test_frozen_utility_identity(self):
        setup.check_utilities()


if __name__ == "__main__":
    unittest.main()
