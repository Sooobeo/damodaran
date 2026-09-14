"""Read-only post-run audit of source, output, baseline, spans, and ledger identity."""
import argparse
import json
from pathlib import Path
import run_relation_diagnostic_v2 as diagnostic


def verify(output):
    ledger=diagnostic.ledger
    ev=ledger.Evidence(diagnostic.ROOT)
    manifest=ev.json(output/"relation-manifest.json")
    for path,digest in manifest["evidenceFiles"].items():ev.read(path,digest)
    inputs=ev.lines(output/"relation-inputs.jsonl",manifest["inputsSha256"])
    assessments=ev.json(output/"relation-diagnostics.json")
    events=ev.json(output/"relation-events.json")
    by_id={r["instanceId"]:r for r in inputs}
    ledger.require(len(inputs)==len(assessments)==len(events)==98,"inventory_mismatch")
    span_count=0
    for assessed in assessments:
        row=by_id[assessed["instanceId"]]
        for kind,text in (("source",row["source"]),("translation",row["translation"]),("context",row["context"])):
            ledger.require(ledger.sha(text.encode("utf-8"))==row[kind+"Sha256"],"text_hash_changed")
        for relation in assessed["diagnostic"]["relations"]:
            ledger.require(relation["warning"] == (relation["status"]=="supported_conflict"),"warning_status_mismatch")
            for name,text in (("sourceEvidence",row["source"]),("translationEvidence",row["translation"])):
                for span in relation[name]:
                    ledger.require(type(span["start"]) is int and type(span["end"]) is int and 0<=span["start"]<span["end"]<=len(text.encode("utf-16-le"))//2,"span_bounds")
                    ledger.require(text.encode("utf-16-le")[2*span["start"]:2*span["end"]].decode("utf-16-le")==span["text"],"span_text_mismatch")
                    span_count+=1
            if relation["status"] in ("supported_match","supported_conflict"):
                ledger.require(relation["sourceEvidence"] and relation["translationEvidence"],"missing_bilingual_span")
    for event in events:
        path=diagnostic.LEDGER/"events"/(ledger.sha(ledger.packed(event))+".json")
        ledger.require(ev.read(path)==ledger.packed(event),"ledger_event_mismatch")
        row=by_id[event["instanceId"]]
        for baseline in row["baselines"]:
            path=diagnostic.LEDGER/"events"/(baseline["eventId"]+".json")
            ledger.require(ev.read(path)==ledger.packed(baseline["event"]),"baseline_event_changed")
    receipts=[ev.json(p) for p in output.glob("relation-ledger-import-*.json")]
    ledger.require(any(r["added"]==98 and r["reused"]==0 for r in receipts),"initial_import_missing")
    ledger.require(any(r["added"]==0 and r["reused"]==98 for r in receipts),"idempotent_import_missing")
    ev.verify_unchanged()
    return {"version":"relation-post-run-verification-v1","outputInstances":98,"baselineObservations":sum(len(r["baselines"]) for r in inputs),
            "originalEvidenceFiles":len(manifest["evidenceFiles"]),"exactUtf16SpansVerified":span_count,
            "ledgerObservationsVerified":len(events),"existingJudgmentsAndOutputsUnchanged":True,
            "initialAdded":98,"repeatAdded":0,"repeatReused":98,"modelCalls":0,"status":"verified"}


if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output",type=Path,required=True)
    args=cli.parse_args()
    result=verify(args.output.resolve())
    path=args.output/"relation-verification.json"
    if not path.exists():diagnostic.write_new(path,result)
    else:diagnostic.ledger.require(path.read_bytes()==diagnostic.ledger.packed(result),"verification_changed")
    print(json.dumps(result,ensure_ascii=False))
