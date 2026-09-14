"""Synthetic v2 lineage and provisional joins; no actual run, QA or grading."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import test_recovery_evaluation_v2 as support
from input_execution_v1 import recovery_evaluation_v2 as r
from input_execution_v1 import recovery_evaluation as old
from input_execution_v1 import recovery_relations_v2 as relations
from input_execution_v1 import recovery_relations as old_relations
from input_execution_v1 import test_recovery_relations_v2 as relation_support

e, tr = r.ev, r.transport


class V2BoundaryTests(unittest.TestCase):
    def setUp(self):
        original = support.fixture
        def fixture():
            bundle, rows, units, annotations, prepared = original()
            for i in range(8, 16):
                before, after = "unit" + str(i), f"IP1-G{i-7:02d}"
                units[after] = units.pop(before); units[after]["id"] = after
                annotations[after] = annotations.pop(before); annotations[after]["id"] = after
                for row in rows + prepared:
                    if row["id"] == before:
                        row["id"] = after
            return bundle, rows, units, annotations, prepared
        self.f = support.RecoveryEvaluationV2Tests("runTest")
        self.addCleanup(self.f.doCleanups)
        with patch.object(support, "fixture", side_effect=fixture):
            self.f.setUp()
        self.root = self.f.root

    def new_provisional(self, unit="IP1-G02"):
        f = self.f
        refs = f.validation["evidence"]["files"]
        for run in (f.prior, f.recovered):
            e.write_new(run / "plan.json", {"syntheticOnly": True, "run": run.name})
            refs.append(tr.reference(f.root, run / "plan.json"))
            matching = next(item for item in f.manifest["sourceRuns"] if item["runPath"] == run.relative_to(f.root).as_posix())
            matching["planSha256"] = e.sha((run / "plan.json").read_bytes())
        for row in f.rows:
            if row["id"] == unit:
                response = f.root / row["rawResponsePath"]
                prefix = response.name.removesuffix(".response.bin")
                for suffix in (".request.json", ".receipt.json"):
                    path = response.with_name(prefix + suffix)
                    e.write_new(path, {"syntheticOnly": True, "configuration": row["configuration"], "kind": suffix})
                    refs.append(tr.reference(f.root, path))
        f.cohort_path.write_bytes(e.packed(f.manifest))
        f.prepare()
        folder = f.root / tr.BASE / "synthetic-mixed-provisional"
        provenance = {(row["id"], row["configuration"]): row for row in f.bundle["outputProvenance"]}
        def reference(path):
            path = f.root / path
            return {**tr.reference(f.root, path), "bytes": len(path.read_bytes())}
        mappings = []
        for mapping in f.bundle["mapping"]:
            if mapping["id"] != unit:
                continue
            rid = mapping["reviewId"]
            packet = next(p for p in f.bundle["sourcePackets"] if p["reviewId"] == rid)
            e.write_new(folder / "source-packets" / (rid + ".json"), packet)
            p = provenance[(unit, mapping["configuration"])]
            response = Path(p["rawResponse"]["path"])
            prefix = response.name.removesuffix(".response.bin")
            mappings.append({"reviewId": rid, "id": unit, "configuration": mapping["configuration"],
                "output": reference(p["outputFile"]["path"]), "rawResponse": reference(response),
                "request": reference(response.with_name(prefix + ".request.json")),
                "receipt": reference(response.with_name(prefix + ".receipt.json")),
                "packetSha256": e.sha(e.packed(packet)), "sourceRunId": p["runId"],
                "sourceRunStatus": "failed" if p["sourceRunStatus"] == "failed" else "not_finalized"})
        manifest = {"version": "input-execution-v1-recovery-provisional-source-v1", "unit": unit,
            "status": "provisional_source_packets_only", "mappings": mappings,
            "sourcePackets": 4, "questionPackets": 0, "seed": e.SEED,
            "plan": reference(f.recovered / "plan.json"), "whole64CohortValidated": False,
            "formalSourceReviewsApproved": False, "sourceRunInProgress": True,
            "candidateDecisions": 0, "generationCalls": 0, "humanReviewed": False,
            "preparationCodeSha256": r.RECOVERY_PROVISIONAL_SHA}
        e.write_new(folder / "private/manifest.json", manifest)
        return folder

    def promote(self, folder):
        return r.verify_recovery_provisional(folder, self.f.folder, self.root / tr.BASE / "promotion.json", self.root)

    def mutate(self, folder, change):
        self.f.rewrite(folder / "private/manifest.json", change)

    def test_old_frozen_lineage_and_all_eleven_bytes_are_mandatory(self):
        result = r.lineage.verify(self.root)
        self.assertEqual(len(result["files"]), 12)
        self.assertFalse(result["criteriaChanged"] or result["priorFrozenFilesModified"])
        path = self.root / result["files"][1]["path"]
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaises(ValueError):
            self.f.prepare()
        self.assertFalse(self.f.folder.exists())

    def test_v1_validator_receipt_is_rejected_without_packet_writes(self):
        self.f.manifest.pop("validationVersion")
        self.f.cohort_path.write_bytes(e.packed(self.f.manifest))
        with self.assertRaisesRegex(ValueError, "validator_v2_required"):
            self.f.prepare()
        self.assertFalse(self.f.folder.exists())

    def test_v2_envelope_preserves_frozen_v1_source_question_and_mapping_bytes(self):
        cohort = r.load_cohort(self.f.cohort_folder, self.root)
        prior, current = old.build_review_packets(cohort), r.build_review_packets(cohort)
        for key in ("sourcePackets", "questionPackets", "mapping", "outputProvenance"):
            self.assertEqual(e.packed(prior[key]), e.packed(current[key]))
        self.assertEqual(current["recovery"]["validationVersion"], r.lineage.VALIDATION_VERSION)
        self.assertEqual(current["recovery"]["evaluationLineage"], r.lineage.verify(self.root))

    def test_relation_v2_preserves_v1_diagnostics_and_original_run_observations(self):
        rows = relation_support.rows()
        prior, current = old_relations.diagnose_rows(rows), relations.diagnose_rows(rows)
        current["version"] = prior["version"]
        self.assertEqual(e.packed(current), e.packed(prior))

    def test_mixed_provisional_joins_all_four_artifacts_without_approval(self):
        receipt = self.promote(self.new_provisional())
        self.assertEqual([p["originalRunStatus"] for p in receipt["rows"]].count("failed"), 2)
        self.assertTrue(receipt["packetPromotionEligible"] and receipt["actualReviewerConfirmationStillRequired"])
        self.assertFalse(receipt["formalSourceReviewsApproved"] or receipt["originalFailedRunMarkedCompleted"])
        self.assertEqual(receipt["scoresProduced"], 0)
        self.assertTrue(all(set(p["originalEvidence"]) == {"output", "rawResponse", "request", "receipt"} for p in receipt["rows"]))

    def test_new_only_provisional_keeps_provisional_and_final_statuses_distinct(self):
        receipt = self.promote(self.new_provisional("IP1-G03"))
        self.assertEqual({p["originalRunStatus"] for p in receipt["rows"]}, {"completed"})
        self.assertEqual({p["provisionalRunStatus"] for p in receipt["rows"]}, {"not_finalized"})

    def test_packet_whitespace_change_is_not_semantic_equivalence(self):
        folder = self.new_provisional()
        path = next((folder / "source-packets").iterdir())
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "not_byte_identical"):
            self.promote(folder)

    def test_valid_but_wrong_request_file_is_not_accepted_as_same_output(self):
        folder = self.new_provisional()
        self.mutate(folder, lambda d: d["mappings"][0].update(request=deepcopy(d["mappings"][1]["request"])))
        with self.assertRaisesRegex(ValueError, "http_join_changed"):
            self.promote(folder)

    def test_manifest_byte_count_is_checked(self):
        folder = self.new_provisional()
        self.mutate(folder, lambda d: d["mappings"][0]["receipt"].update(bytes=0))
        with self.assertRaisesRegex(ValueError, "evidence_not_in_cohort"):
            self.promote(folder)

    def test_changed_original_source_run_status_is_rejected(self):
        folder = self.new_provisional()
        self.mutate(folder, lambda d: d["mappings"][0].update(sourceRunStatus="completed"))
        with self.assertRaisesRegex(ValueError, "original_status_changed"):
            self.promote(folder)

    def test_helper_and_manifest_cannot_be_changed_together(self):
        folder = self.new_provisional()
        code = self.root / "scripts/model-comparison/input_execution_v1/recovery_provisional_reviews.py"
        code.write_bytes(code.read_bytes() + b"changed")
        self.mutate(folder, lambda d: d.update(preparationCodeSha256=e.sha(code.read_bytes())))
        with self.assertRaisesRegex(ValueError, "preparation_code_changed"):
            self.promote(folder)

    def test_prior_plan_does_not_replace_recovery_plan(self):
        folder = self.new_provisional()
        path = self.f.prior / "plan.json"
        self.mutate(folder, lambda d: d.update(plan={**tr.reference(self.root, path), "bytes": len(path.read_bytes())}))
        with self.assertRaisesRegex(ValueError, "plan_changed"):
            self.promote(folder)

    def test_incomplete_cohort_cannot_promote_any_draft(self):
        folder = self.new_provisional()
        with patch.object(r, "validate_cohort", side_effect=ValueError("cohort_incomplete")):
            with self.assertRaisesRegex(ValueError, "cohort_incomplete"):
                self.promote(folder)
        self.assertFalse((self.root / tr.BASE / "promotion.json").exists())


if __name__ == "__main__":
    unittest.main()
