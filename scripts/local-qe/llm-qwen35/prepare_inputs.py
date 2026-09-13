"""Project only approved input fields from the fixed dev48 input; never read judgments."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import contract
import prepare

INPUT = prepare.ROOT / ".training/quality-evaluation/dev48-v1/inputs.jsonl"


def project(rows):
    return [{key: row.get(key, "") if key == "context" else row[key]
             for key in ("id", "source", "translation", "context")} for row in rows]


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output-name", default="dev48-source-only-v1")
    args = cli.parse_args()
    prepare.require(args.output_name == "dev48-source-only-v1", "fixed_output_name_required")
    raw = INPUT.read_bytes()
    # Do not open sibling labels, named errors, references, judgments, or old model scores.
    rows = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    selected = project(rows)
    target = prepare.DEST / "inputs" / (args.output_name + ".jsonl")
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected).encode("utf-8")
    prepare.once(target, payload)
    checked, checked_sha = contract.read_input(target)
    prepare.require(len(checked) == 48 and prepare.sha_file(INPUT) == prepare.hashlib.sha256(raw).hexdigest(),
                    "input_count_or_identity_changed")
    prepare.json_once(target.with_suffix(".manifest.json"), {
        "version": "dev48-source-only-projection-v1", "createdAt": prepare.utc(),
        "originalInputPath": str(INPUT.relative_to(prepare.ROOT)), "originalInputSha256": prepare.sha_file(INPUT),
        "outputFile": target.name, "outputSha256": checked_sha, "count": len(checked),
        "selectedFields": ["id", "source", "translation", "context"],
        "modelBodyFields": ["source", "translation", "context"],
        "otherFilesRead": False, "judgmentsReferencesChecksIncluded": False,
        "lengths": {field: {"maxChars": max(len(row[field]) for row in checked)}
                    for field in ("source", "translation", "context")},
        "maxCombinedChars": max(sum(len(row[field]) for field in ("source", "translation", "context")) for row in checked),
        "tokenCountsMeasured": False, "notFinalHoldout": True, "humanReviewed": False,
        "codeSha256": prepare.sha_file(__file__)} )
    print(json.dumps({"file": str(target), "count": len(checked), "sha256": checked_sha}))


if __name__ == "__main__":
    main()
