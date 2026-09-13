"""Exclusive local dataset output; preserve any interrupted publication for review."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile


def write_outputs_once(destination: Path, payloads: dict[str, str]):
    destination.mkdir(parents=True, exist_ok=True)
    if not payloads or any(Path(name).name != name or name in {".", ".."} for name in payloads):
        raise ValueError("Dataset outputs must be nonempty plain filenames")
    # Check every output before creating any of them. os.link below also refuses
    # a concurrent writer's existing destination (unlike os.replace).
    if any((destination / name).exists() for name in payloads):
        raise ValueError("Existing dataset output; preserve and inspect it before a new experiment")
    staging = Path(tempfile.mkdtemp(prefix=".v4-staging-", dir=destination))
    for name, content in payloads.items():
        (staging / name).write_bytes(content.encode("utf-8"))
    try:
        for name in payloads:
            os.link(staging / name, destination / name)
    except OSError as error:
        raise RuntimeError(f"Dataset publication interrupted; preserve staged and published files for review: {staging}") from error
    for name in payloads:
        (staging / name).unlink()
    staging.rmdir()
