"""Explicit cohort-validator-v2 transport for unchanged frozen relation checks."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_execution_v1 import evaluation as e
from input_execution_v1 import recovery_relations as v1
from input_execution_v1 import recovery_v2_lineage as lineage

VERSION = "input-execution-v1-recovery-relations-v2"
checker, CHECKER_SHA, CHECKER_PATH = v1.checker, v1.CHECKER_SHA, v1.CHECKER_PATH
ORIGINAL_VERSION, RECOVERY_VERSION = v1.ORIGINAL_VERSION, v1.RECOVERY_VERSION
SUPPORTED, JOIN_FIELDS, BASE = v1.SUPPORTED, v1.JOIN_FIELDS, v1.BASE
verify_checker, validate_diagnostic = v1.verify_checker, v1.validate_diagnostic


def diagnose_rows(rows):
    """Preserve original producer/run IDs, checks and observations byte for byte."""
    result = v1.diagnose_rows(rows)
    result["version"] = VERSION
    return result


def write_report(cohort, destination, root=ROOT):
    from input_execution_v1 import recovery_cohort_v2
    root = Path(root).resolve(); destination = e.rooted(root, destination)
    boundary = root / BASE
    e.require(destination.is_relative_to(boundary) and destination != boundary and not destination.exists(),
              "fresh_relation_destination_required")
    old_lineage = lineage.verify(root)
    loaded = recovery_cohort_v2.validate_cohort(cohort, root=root)
    e.require(loaded["manifest"].get("validationVersion") == lineage.VALIDATION_VERSION,
              "explicit_relation_cohort_validator_v2_required")
    result = diagnose_rows(loaded["rows"])
    receipt = recovery_cohort_v2.validate_cohort(cohort, root=root)
    e.require(loaded == receipt and lineage.verify(root) == old_lineage, "cohort_or_lineage_changed_during_diagnosis")
    result.update(createdAtUtc=datetime.now(timezone.utc).isoformat(), cohortEvidence=loaded["evidence"],
        cohortManifest=loaded["manifest"], originalAttemptStatus="failed", retainedOutputs=38, recoveredOutputs=26,
        completionRequestsSent=65, interruptedRequests=1, evaluationLineage=old_lineage,
        wrapperCodeSha256=e.sha(Path(__file__).read_bytes()),
        cohortValidatorCodeSha256=e.sha(Path(recovery_cohort_v2.__file__).read_bytes()))
    manifest = {"version": VERSION, "status": "diagnosed", "outputs": 64, "reportFile": "relation-report.json",
                "reportSha256": e.sha(e.packed(result)), "checker": result["checker"],
                "modelCalls": 0, "translationGenerations": 0, "ledgerWrites": 0}
    e.write_new(destination / "relation-report.json", result)
    e.write_new(destination / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_report(args.cohort, args.destination), ensure_ascii=False))


if __name__ == "__main__":
    main()
