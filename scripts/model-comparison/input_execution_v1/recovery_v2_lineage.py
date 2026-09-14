"""Verify the unchanged v1 evaluation freeze before explicit v2 validation.

This is a small-file, read-only binding. It does not load models, alter a
validator, create a cohort, or retrospectively change original run identity.
"""
from pathlib import Path
from input_execution_v1 import evaluation as ev
from input_execution_v1 import append_evidence as original

ROOT = Path(__file__).resolve().parents[3]
VERSION = "input-execution-v1-recovery-evaluation-lineage-v2"
FREEZE_PATH = "content/model-comparison/input-execution-v1/recovery-evaluation-freeze-v1.json"
FREEZE_SHA = "37badacdc495e15764949a84d1e07007f2d5ee181e8d9a567a00412e70268e5e"
VALIDATION_VERSION = "input-execution-v1-recovery-cohort-validator-v2"


def verify(root=ROOT):
    graph = original.EvidenceGraph(root)
    freeze = graph.json(FREEZE_PATH, FREEZE_SHA)
    records = ev.unique(freeze["files"], "path", "prior_recovery_evaluation_freeze")
    ev.require(len(records) == 11 and freeze.get("criteriaChanged") is False, "prior_recovery_evaluation_lineage")
    refs = [{"path": FREEZE_PATH, "sha256": FREEZE_SHA}]
    for path, record in records.items():
        graph.verify(path, record["sha256"])
        refs.append({"path": path, "sha256": record["sha256"]})
    graph.verify_unchanged()
    return {"version": VERSION, "priorEvaluationFreeze": refs[0], "files": refs,
            "priorFrozenFilesModified": False, "criteriaChanged": False,
            "outputRecordsRewritten": False, "modelCalls": 0}
