"""Summarize a completed exploratory probe and prepare model-blinded review.

Metrics are lexical diagnostics, not professional semantic quality ratings.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import statistics

from sacrebleu.metrics import BLEU, CHRF

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / ".training/comparisons/finance-probe-20260909"
CASES = ROOT / "content/model-comparison/finance-probe-20260909.jsonl"
MODELS = {
    "argos": "argos-raw.jsonl",
    "marian": "marian-finance-v3-fp32.jsonl",
    "translategemma": "translategemma-q4-cpu-predictions.jsonl",
}


def rows(path):
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    cases = rows(CASES)
    identifiers = [case["id"] for case in cases]
    predictions = {}
    summary = {"purpose": "unreviewed assistant-authored exploratory probe; not independent test", "sourceSha256": sha(CASES), "models": {}}
    chrf, bleu = CHRF(), BLEU(tokenize="none", effective_order=True)
    for name, filename in MODELS.items():
        path = OUTPUT / filename
        values = rows(path)
        if len(values) != len(cases) or [item["id"] for item in values] != identifiers:
            raise ValueError(f"Incomplete or reordered result: {name}")
        for case, item in zip(cases, values):
            expected = hashlib.sha256(case["source"].encode()).hexdigest()
            observed = item.get("sourceSha256", item.get("inputSha256"))
            if observed != expected:
                raise ValueError(f"Source mismatch: {name}/{case['id']}")
        predictions[name] = {item["id"]: item for item in values}
        entry = {"resultSha256": sha(path), "count": len(values), "generationMedianSeconds": statistics.median(item["generationSeconds"] for item in values), "generationTotalSeconds": sum(item["generationSeconds"] for item in values), "numericLexicalMatches": sum(item["numbersPreserved"] for item in values), "empty": sum(not item["translation"].strip() for item in values), "outputLimitReached": sum(item["outputLimitReached"] for item in values)}
        entry["domains"] = {}
        for domain in ("finance", "general", "all"):
            selected = [case for case in cases if domain == "all" or case["domain"] == domain]
            hypotheses = [predictions[name][case["id"]]["translation"] for case in selected]
            refs = [case["reference"] for case in selected]
            terms = [(term, predictions[name][case["id"]]["translation"]) for case in selected for term in case["expectedTerms"]]
            entry["domains"][domain] = {"count": len(selected), "chrf": chrf.corpus_score(hypotheses, [refs]).score, "bleu": bleu.corpus_score(hypotheses, [refs]).score, "termHits": sum(any(target in translation for target in term["acceptedTargets"]) for term, translation in terms), "termTargets": len(terms)}
        summary["models"][name] = entry
    summary["metricSignatures"] = {"chrf": str(chrf.get_signature()), "bleu": str(bleu.get_signature())}
    (OUTPUT / "comparison-metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8")
    generator = random.Random(20260909)
    blind, mapping = [], []
    for case in cases:
        names = list(MODELS)
        generator.shuffle(names)
        blind.append({"id": case["id"], "source": case["source"], "reference": case["reference"], "criticalChecks": case["criticalChecks"], "outputs": {letter: predictions[name][case["id"]]["translation"] for letter, name in zip("ABC", names)}})
        mapping.append({"id": case["id"], "labels": dict(zip("ABC", names))})
    for filename, values in (("blind-review-input.jsonl", blind), ("blind-review-key.jsonl", mapping)):
        target = OUTPUT / filename
        if target.exists():
            raise ValueError("Preserve existing blinded review artifacts")
        target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in values), "utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
