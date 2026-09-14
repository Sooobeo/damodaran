"""Freeze then diagnose 98 existing outputs; append only new relation observations."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison/error-ledger"))
import ledger
from relation_checks import inspect_relations, TYPES, VERSION

BASE = ROOT / ".training/quality-evaluation/input-preparation-v1"
LEDGER = ROOT / ".training/quality-evaluation/error-ledger/v1"
FIXTURE = "content/model-comparison/input-preparation-v1-s2s3/relation-fixtures-v1.json"
FIXTURE_FREEZE = "content/model-comparison/input-preparation-v1-s2s3/relation-fixtures-freeze-v1.json"
SELECTED = {
    "assistant-round2-v4":48,
    "general-meaning-assistant-review-v1":32,
    "tg27-failed-run-16-diagnostic-review-v1":16,
    "tg27-recovery-run-root-review-v1":2,
}


def write_new(path, value):
    with path.open("xb") as stream:
        stream.write(ledger.packed(value))


def read_events(ev):
    result = []
    for path in sorted((LEDGER/"events").glob("*.json")):
        raw = ev.read(path, path.stem)
        item = json.loads(raw)
        if item["kind"] == "translation_review":
            result.append((path.stem,item))
    return result


def prepare(output):
    ledger.require(not output.exists(), "prepare_requires_fresh_output")
    ev = ledger.Evidence(ROOT)
    events = read_events(ev)
    selected = [(key,row) for key,row in events if row["reviewVersion"] in SELECTED]
    ledger.require(Counter(row["reviewVersion"] for _,row in selected) == SELECTED,"selected_98_inventory_changed")
    sources = {}
    for path in ("content/model-comparison/linguistic-dev-20260910.jsonl",
                 "content/model-comparison/real-reading-check-20260910/sources.jsonl",
                 "content/model-comparison/general-context-dev-20260911.jsonl"):
        for row in ev.lines(path):
            ledger.require(row["id"] not in sources,"duplicate_source")
            sources[row["id"]] = (row,path)
    fixtures = ev.json(FIXTURE)
    receipt = ev.json(FIXTURE_FREEZE)
    ledger.require(ev.files[FIXTURE] == receipt["fixtureSha256"] and len(fixtures["cases"]) == receipt["caseCount"], "fixture_freeze_changed")
    for name in ("relation_checks.py","test_relation_checks.py","run_relation_diagnostic.py"):
        ev.read(Path(__file__).with_name(name))
    ev.read("scripts/model-comparison/error-ledger/ledger.py")
    # The S1 package and registered identity are read-only evidence, never a model input.
    s1 = ev.json("content/model-comparison/input-preparation-v1/freeze-manifest.json")
    ev.read(".translation/hymt/manifest.json") if (ROOT/".translation/hymt/manifest.json").exists() else None
    rows=[]; run_cache={}
    for key, review in sorted(selected,key=lambda x:(x[1]["cohort"],x[1]["system"],x[1]["sourceId"])):
        source, source_path = sources[review["sourceId"]]
        run = review["run"]
        if run not in run_cache:
            predictions = ev.lines(run+"/predictions.jsonl")
            ledger.require(len({p["id"] for p in predictions}) == len(predictions),"duplicate_prediction")
            run_cache[run]=({p["id"]:p for p in predictions},ev.json(run+"/summary.json"))
        predictions, summary = run_cache[run]
        pred = predictions[review["sourceId"]]
        target = pred["translation"]
        texts={"source":source["source"],"translation":target,"context":source.get("context","")}
        for field,text in texts.items():
            digest=ledger.sha(text.encode("utf-8"))
            ledger.require(digest==review[field+"Sha256"],"original_text_hash_mismatch")
            pred_field="targetSha256" if field=="translation" else field+"Sha256"
            ledger.require(pred.get(pred_field,digest)==digest,"prediction_text_hash_mismatch")
        if pred.get("rawResponseFile") and pred.get("rawResponseSha256"):
            ev.read(Path(run)/pred["rawResponseFile"],pred["rawResponseSha256"])
        baselines=[(key,review)]
        baselines += [(k,r) for k,r in events if r["reviewVersion"]=="adjusted-hy7-tg12-v1"
                      and r["run"]==run and r["sourceId"]==review["sourceId"]
                      and r["outputId"]==review["outputId"]]
        for _,baseline in baselines:
            for ref in baseline["evidence"]:
                ev.read(ref["path"],ref["sha256"])
        rows.append(dict(instanceId=ledger.sha(ledger.packed({"run":run,"id":pred["id"]})),
                         outputId=review["outputId"],sourceId=pred["id"],cohort=review["cohort"],system=review["system"],run=run,
                         **texts,**{field+"Sha256":review[field+"Sha256"] for field in texts},
                         predictionFile=run+"/predictions.jsonl",predictionRowSha256=ledger.sha(ledger.packed(pred)),
                         sourceFile=source_path,sourceRowSha256=ledger.sha(ledger.packed(source)),
                         originalRunStatus=summary.get("status","not_explicit_in_original_summary"),
                         originalRunPhase=summary.get("phase"), originalOutputStatus=pred.get("status"),
                         originalAutomaticChecks={k:pred[k] for k in ("checks","numbersPreserved","automaticChecksPassed","outputIntegrityPassed","truncated","outputLimitReached","stopType") if k in pred},
                         baselines=[{"eventId":k,"event":r} for k,r in baselines]))
    ledger.require(len(rows)==98 and len({r["instanceId"] for r in rows})==98,"saved_instance_inventory_changed")
    ledger.require(sum(len(r["baselines"]) for r in rows)==122,"baseline_inventory_changed")
    ev.verify_unchanged()
    output.mkdir(parents=True)
    with (output/"relation-inputs.jsonl").open("xb") as stream:
        for row in rows: stream.write(ledger.packed(row))
    manifest=dict(version="relation-input-inventory-v1",createdAtUtc=datetime.now(timezone.utc).isoformat(),
                  outputInstances=98,distinctContentIdentities=len({r["outputId"] for r in rows}),baselineObservations=122,
                  selectedBy="all existing output instances in the four prespecified review strata; not error-only",
                  strata=dict(Counter(r["system"]+":"+r["cohort"] for r in rows)),
                  originalRunStatusCounts=dict(Counter(r["originalRunStatus"] for r in rows)),
                  originalOutputStatusCounts=dict(Counter(r["originalOutputStatus"] for r in rows)),
                  evidenceFiles=dict(ev.files),inputsSha256=ledger.sha((output/"relation-inputs.jsonl").read_bytes()),
                  fixtureSha256=ev.files[FIXTURE],checkerExecuted=False,modelCalls=0,
                  readingTG27OutputInvented=False,originalJudgmentsPreserved=True,independentHoldout=False)
    write_new(output/"relation-manifest.json",manifest)
    return {"status":"prepared","outputInstances":98,"baselineObservations":122,"path":str(output)}


def baseline_material(event):
    severity=event["severity"]
    return None if severity=="unresolved" else severity in ("major","critical")


def score(observations, relation_type=None):
    counts=Counter(); statuses=Counter(); covered=0; warnings=0; original_holds=0
    for obs in observations:
        if relation_type:
            assessment=next(r for r in obs["diagnostic"]["relations"] if r["type"]==relation_type)
            warn=assessment["warning"]; statuses[assessment["status"]]+=1
            supported=assessment["status"] in ("supported_match","supported_conflict")
        else:
            warn=obs["diagnostic"]["warning"]
            supported=any(r["status"] in ("supported_match","supported_conflict") for r in obs["diagnostic"]["relations"])
        warnings+=warn; covered+=supported
        material=obs["baselineMaterialError"]
        if material is None:
            original_holds+=1
            counts["baseline_unresolved_warning" if warn else "baseline_unresolved_no_warning"]+=1
        else:
            counts[("TP" if warn else "FN") if material else ("FP" if warn else "TN")]+=1
            if not supported: counts["uncovered_error" if material else "uncovered_no_material_error"]+=1
    return dict(rows=len(observations),TP=counts["TP"],FP=counts["FP"],FN=counts["FN"],TN=counts["TN"],
                baselineUnresolved=original_holds,warnings=warnings,supportedRows=covered,
                supportedCoverage=covered/len(observations) if observations else None,
                statusCounts=dict(statuses),uncoveredMaterialErrors=counts["uncovered_error"],
                uncoveredNoMaterialErrors=counts["uncovered_no_material_error"],
                precision=counts["TP"]/(counts["TP"]+counts["FP"]) if counts["TP"]+counts["FP"] else None,
                recall=counts["TP"]/(counts["TP"]+counts["FN"]) if counts["TP"]+counts["FN"] else None)


def run(output, append=False):
    ledger.require(output.is_dir(),"prepare_first")
    ev=ledger.Evidence(ROOT)
    manifest=ev.json(output/"relation-manifest.json")
    for path,digest in manifest["evidenceFiles"].items(): ev.read(path,digest)
    inputs=ev.lines(output/"relation-inputs.jsonl",manifest["inputsSha256"])
    fixtures=ev.json(FIXTURE,manifest["fixtureSha256"])
    synthetic=[]
    for case in fixtures["cases"]:
        result=inspect_relations(case["source"],case["translation"])
        assessed=next(r for r in result["relations"] if r["type"]==case["type"])
        synthetic.append({"caseId":case["id"],"expected":case["expected"],"actual":assessed["status"],"passed":assessed["status"]==case["expected"],"assessment":assessed})
    ledger.require(all(r["passed"] for r in synthetic),"frozen_synthetic_counterexample_failed")
    diagnostics=[]; baseline_observations=[]
    for row in inputs:
        # This is the only detector boundary. No IDs, context, references, or baseline data enter it.
        diagnostic=inspect_relations(row["source"],row["translation"])
        diagnostics.append({"instanceId":row["instanceId"],"diagnostic":diagnostic})
        for base in row["baselines"]:
            baseline_observations.append({"instanceId":row["instanceId"],"cohort":row["cohort"],"system":row["system"],
                                          "baselineVersion":base["event"]["reviewVersion"],"eventId":base["eventId"],
                                          "baselineMaterialError":baseline_material(base["event"]),"diagnostic":diagnostic})
    groups=defaultdict(list)
    for row in baseline_observations:
        groups[(row["cohort"],row["system"],row["baselineVersion"])].append(row)
    slices=[]
    for (cohort,system,version),rows in sorted(groups.items()):
        slices.append({"cohort":cohort,"system":system,"baselineVersion":version,
                       "paragraphWarningMatrix":score(rows),
                       "eachTypeAsParagraphWarningClassifier":{kind:score(rows,kind) for kind in TYPES}})
    by_instance={d["instanceId"]:d["diagnostic"] for d in diagnostics}
    events=[]
    for row in inputs:
        events.append(dict(version=ledger.VERSION,kind="relation_observation",checker=VERSION,
                           checkerCodeSha256=manifest["evidenceFiles"][Path(__file__).with_name("relation_checks.py").relative_to(ROOT).as_posix()],
                           **{k:row[k] for k in ("instanceId","outputId","sourceId","sourceSha256","translationSha256","contextSha256","cohort","system","run")},
                           baselineReviewEventIds=[b["eventId"] for b in row["baselines"]],
                           originalRunStatus=row["originalRunStatus"],originalOutputStatus=row["originalOutputStatus"],
                           originalAutomaticChecks=row["originalAutomaticChecks"],diagnostic=by_instance[row["instanceId"]],
                           evidence=[ev.ref(output/"relation-manifest.json"),ev.ref(output/"relation-inputs.jsonl",row["instanceId"])],
                           translationErrorAdded=False,originalJudgmentsChanged=False,trainingUseAllowed=False,
                           independentHoldout=False,humanReviewed=False,modelCalls=0))
    report=dict(version="relation-diagnostic-report-v1",checker=VERSION,outputInstances=len(inputs),
                baselineObservations=len(baseline_observations),
                metricMeaning="Each type's TP/FP/FN/TN is that type's warning against the original paragraph major/critical baseline, not manually gold-labeled relation accuracy. Unsupported no-warning rows remain in the operational FN/TN counts and are shown as uncovered; TN is not semantic pass. Unresolved baseline rows are outside confusion counts but remain in rows.",
                duplicateReviewRoundsSummed=False,baselineSlices=slices,
                distinctInstanceWarnings=sum(d["diagnostic"]["warning"] for d in diagnostics),
                relationStatusCounts={kind:dict(Counter(next(r for r in d["diagnostic"]["relations"] if r["type"]==kind)["status"] for d in diagnostics)) for kind in TYPES},
                syntheticFixtureCases=len(synthetic),syntheticFixturePassed=sum(r["passed"] for r in synthetic),
                modelCalls=0,generationQualityImprovementMeasured=False,detectorDiagnosticOnly=True,
                independentHoldout=False,productQE95GatePassed=False,appRegistrationPerformed=False,
                fullSemanticCoverage=False,existingEvidenceVerifiedUnchanged=True,
                inputManifestSha256=ledger.sha((output/"relation-manifest.json").read_bytes()),
                pending=["Independent detector calibration/holdout and app registration remain unperformed.","A relation-specific human gold annotation is not present; type classifier matrices retain the paragraph baseline."])
    ev.verify_unchanged()
    for name,value in (("relation-diagnostics.json",diagnostics),("relation-synthetic-results.json",synthetic),
                       ("relation-report.json",report),("relation-events.json",events)):
        path=output/name
        if path.exists(): ledger.require(path.read_bytes()==ledger.packed(value),"existing_diagnostic_changed:"+name)
        else: write_new(path,value)
    imported=None
    if append:
        before=ledger.summarize(LEDGER)
        imported=ledger.append_events(LEDGER,events)
        after=ledger.summarize(LEDGER)
        ledger.require(before["eventKindCounts"]["translation_review"]==after["eventKindCounts"]["translation_review"],"translation_error_events_changed")
        for path,digest in manifest["evidenceFiles"].items():ev.read(path,digest)
        receipt=dict(version="relation-ledger-import-v1",**imported,beforeEventCount=before["eventCount"],afterEventCount=after["eventCount"],
                     translationReviews=after["eventKindCounts"]["translation_review"],eventKindCounts=after["eventKindCounts"],
                     originalEvidenceVerifiedUnchanged=True,modelCalls=0)
        stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        write_new(output/("relation-ledger-import-"+stamp+".json"),receipt)
    return {"status":"diagnosed","outputInstances":98,"baselineObservations":122,
            "warnings":report["distinctInstanceWarnings"],"syntheticPassed":report["syntheticFixturePassed"],"imported":imported,"path":str(output)}


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action",choices=("prepare","run"))
    cli.add_argument("--output",type=Path,required=True)
    cli.add_argument("--append-ledger",action="store_true")
    args=cli.parse_args(); output=args.output.resolve()
    ledger.require(output.is_relative_to(BASE) and output!=BASE,"owned_new_diagnostic_path_required")
    ledger.require(not any(p.is_symlink() for p in (output,*output.parents)),"output_redirected")
    ledger.require(args.action=="run" or not args.append_ledger,"prepare_does_not_append")
    print(json.dumps(prepare(output) if args.action=="prepare" else run(output,args.append_ledger),ensure_ascii=False))


if __name__=="__main__":main()
