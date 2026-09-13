"""Run the unchanged deployed Hy-MT2 7B configuration on new development input."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from linguistic_screen import ROOT, assert_input_unchanged, digest, memory_status, read_screen, write_once

sys.path.insert(0, str(ROOT / "scripts/local-hymt"))
from engine import OwnedHymtEngine
from deployment import verify_manifest
import run_hymt as hy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", nargs="+")
    args = parser.parse_args()
    rows, identity = read_screen(args.input, args.ids)
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / ".training/comparisons").resolve()):
        raise ValueError("output_outside_comparisons")
    output.mkdir(parents=True, exist_ok=False)
    terms, catalog = hy.read_catalog()
    started = time.monotonic()
    record = {"startedAtUtc": datetime.now(timezone.utc).isoformat(), "model": "Hy-MT2-7B-Q8_0",
              "profile": "contextual", "modelSha256": hy.MODEL["sha256"], **identity,
              "catalog": catalog, "memoryBefore": memory_status(),
              "codeHashes": {str(p.relative_to(ROOT)): digest(p) for p in
                             (Path(__file__), ROOT / "scripts/model-comparison/linguistic_screen.py",
                              ROOT / "scripts/local-hymt/engine.py", ROOT / "scripts/local-hymt/process_owner.py",
                              ROOT / "scripts/model-comparison/run_hymt.py")},
              "appModified": False, "paidCalls": 0, "trainingPerformed": False}
    write_once(output / "preflight.json", record)
    engine = None
    bundle = None
    completed = 0
    try:
        bundle = verify_manifest()
        if bundle.manifest["profile"] != "contextual":
            raise ValueError("deployed_baseline_profile_changed")
        record["deployedIdentity"] = bundle.identity
        assert_input_unchanged(identity)
        engine = OwnedHymtEngine("contextual")
        bundle.assert_unchanged()
        record.update(loadSeconds=round(time.monotonic() - started, 3), memoryLoaded=memory_status(),
                      runtimeEog=engine.actual_eog, runtimeTokenEvidence=engine.actual_tokens)
        print(json.dumps({"ready": True, "loadSeconds": record["loadSeconds"]}), flush=True)
        with (output / "predictions.jsonl").open("x", encoding="utf-8", newline="\n") as destination:
            for row in rows:
                bundle.assert_unchanged()
                assert_input_unchanged(identity)
                content, matches = hy.build_user_prompt(row, "contextual", terms)
                prompt = hy.render_prompt(engine.template, content)
                tokens = engine.client.request("/tokenize", {"content": prompt, "add_special": False, "parse_special": True})["tokens"]
                hy.validate_prompt_tokens(tokens)
                before = time.monotonic()
                response = engine.client.request("/completion", hy.SAMPLING | {"prompt": tokens})
                result = hy.prediction(row, prompt, matches, tokens, response, time.monotonic() - before)
                result["memoryAfter"] = memory_status()
                destination.write(json.dumps(result, ensure_ascii=False) + "\n")
                destination.flush()
                completed += 1
                print(json.dumps({"completed": row["id"], "seconds": result["generationSeconds"],
                                  "generatedTokens": result["generatedTokens"]}), flush=True)
        record["status"] = "completed"
    except BaseException as error:
        record.update(status="failed", errorType=type(error).__name__)
        raise
    finally:
        try:
            if engine:
                engine.close()
        except BaseException as error:
            record.update(status="failed", cleanupError=type(error).__name__)
        try:
            assert_input_unchanged(identity)
            if bundle:
                bundle.assert_unchanged()
            for name, value in record["codeHashes"].items():
                if digest(ROOT / name) != value:
                    raise ValueError("runner_code_changed_during_run")
        except BaseException as error:
            record.update(status="failed", finalIntegrityError=type(error).__name__)
        record.update(completed=completed, elapsedSeconds=round(time.monotonic() - started, 3),
                      memoryAfter=memory_status(), childStopped=engine is None or engine.process.poll() is not None,
                      finishedAtUtc=datetime.now(timezone.utc).isoformat())
        write_once(output / "summary.json", record)
    if record["status"] != "completed":
        raise RuntimeError("baseline_failed_see_preserved_summary")


if __name__ == "__main__":
    main()
