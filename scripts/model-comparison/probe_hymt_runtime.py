"""Inspect pinned CPU/SYCL binaries without passing any model or prompt.

Only --version, --list-devices and --help are allowed. Every child uses the
existing private Windows Job owner. This does not establish model compatibility.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import setup_hymt30 as setup

OWNER = setup.ROOT / "scripts/local-hymt/process_owner.py"
OWNER_SHA = "9a00d809466e304359fb47c374c8fc2464ce31cdbc9557fbc9eee089bfe9d8b2"
FLAGS = ("--version", "--list-devices", "--help")


def main():
    setup.require(sys.version_info[:2] == (3, 11), "cpython311_required")
    setup.require(setup.digest(OWNER) == OWNER_SHA, "process_owner_changed")
    inventory = {kind: setup.install_runtime(kind, offline=True) for kind in setup.RUNTIMES}
    sys.path.insert(0, str(OWNER.parent))
    from process_owner import claim_process_owner
    owner = claim_process_owner()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = setup.DEST / ("runtime-probe-" + stamp)
    destination.mkdir()
    environment = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith(("LLAMA_", "GGML_", "AIP_"))}
    environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    records = []
    for kind in setup.RUNTIMES:
        directory = setup.DEST / f"runtime-{kind}"
        for flag in FLAGS:
            argv = [str(directory / "llama-server.exe"), flag]
            started = time.monotonic()
            process = None
            record = {"backend": kind, "argument": flag, "modelLoaded": False}
            try:
                process = owner.spawn(argv, cwd=directory, env=environment,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                try:
                    content, _ = process.communicate(timeout=40)
                except subprocess.TimeoutExpired:
                    process.kill()
                    content, _ = process.communicate(timeout=5)
                    record["timeout"] = True
                setup.require(len(content) <= 2 * 1024 * 1024, "probe_output_too_large")
                log = destination / f"{kind}-{flag[2:]}.txt"
                with log.open("xb") as output:
                    output.write(content)
                record.update(exitCode=process.returncode, log=log.name, logSha256=setup.digest(log))
            except Exception as error:
                record.update(errorType=type(error).__name__)
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                record["seconds"] = round(time.monotonic() - started, 3)
                record["ownedChildExited"] = process is None or process.poll() is not None
            records.append(record)
            print(json.dumps(record), flush=True)
    result = {"createdAt": datetime.now(timezone.utc).isoformat(), "modelLoaded": False,
              "inferencePerformed": False, "runtimeTag": setup.RUNTIME_TAG,
              "runtimeRevision": setup.RUNTIME_REVISION, "runtimeFiles": inventory,
              "processOwnerSha256": OWNER_SHA, "setupScriptSha256": setup.digest(setup.__file__),
              "probeScriptSha256": setup.digest(__file__), "records": records}
    setup.archive_tools.json_once(destination / "verification.json", result)
    print(json.dumps({"receipt": str(destination / "verification.json"), "modelLoaded": False}), flush=True)


if __name__ == "__main__":
    main()
