"""Protect existing corpora, including partially written and concurrent outputs."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dataset_io import write_outputs_once


class DatasetPublicationTests(unittest.TestCase):
    def test_existing_later_output_prevents_earlier_file_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dev.jsonl").write_text("personal", "utf-8")
            with self.assertRaises(ValueError):
                write_outputs_once(root, {"train.jsonl": "train", "dev.jsonl": "dev"})
            self.assertFalse((root / "train.jsonl").exists())
            self.assertEqual((root / "dev.jsonl").read_text("utf-8"), "personal")

    def test_success_preserves_exact_utf8_and_leaves_no_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = {"train.jsonl": "한국어\n", "manifest.json": "{}\n"}
            write_outputs_once(root, payloads)
            self.assertEqual({p.name for p in root.iterdir()}, set(payloads))
            for name, value in payloads.items():
                self.assertEqual((root / name).read_bytes(), value.encode("utf-8"))

    def test_concurrent_existing_file_is_preserved(self):
        import os
        original_link = os.link
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def concurrent_link(source, target):
                Path(target).write_text("concurrent", "utf-8")
                original_link(source, target)
            with patch("dataset_io.os.link", side_effect=concurrent_link):
                with self.assertRaises(RuntimeError):
                    write_outputs_once(root, {"train.jsonl": "new"})
            self.assertEqual((root / "train.jsonl").read_text("utf-8"), "concurrent")
            staged = list(root.glob(".v4-staging-*/train.jsonl"))
            self.assertEqual(len(staged), 1)
            self.assertEqual(staged[0].read_text("utf-8"), "new")


if __name__ == "__main__":
    unittest.main()
