"""Validate the fixed two-row recovery as two rows, never as a full dev18 run."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import linguistic_screen as screen
import prepare_finance_answerability as common
import v5_tail2_review_evidence as tail

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / '.training/comparisons/linguistic-dev-20260910/translategemma-27b-v5-tail-retry-20260912-b8'
INPUT = ROOT / 'content/model-comparison/linguistic-dev-20260910.jsonl'
INPUT_SHA = 'b8a2b91802e36d96654631a9965566f774e050ea4139ca7270d9f84d2a916848'
MODEL_SHA = '7f1e67c4ecfec676b38c1ea2ef85c46fafe2f02d3c050fb9540e51787405d8a3'


def verify():
    evidence = common.Evidence()
    evidence.raw(INPUT, INPUT_SHA)
    rows, identity = screen.read_screen(INPUT, tail.TAIL_IDS)
    for name, expected in (identity.get('inputFiles', {}) | identity.get('readerCodeFiles', {})).items():
        evidence.raw(name, expected)
    evidence.raw(identity['manifestPath'], identity['manifestSha256'])
    summary = evidence.read(RUN / 'summary.json')
    tail.require(summary.get('version') == tail.VERSION and summary.get('status') == 'completed'
                 and summary.get('ramBudgetGiB') == 8 and summary.get('input') == identity,
                 'same_fixed_input_complete_tail_required')
    predictions = evidence.read(RUN / 'predictions.jsonl', jsonl=True)
    tail.check_artifacts(RUN, summary, predictions, evidence)
    common.validate_prediction_rows(predictions, rows, summary)
    installed = evidence.read(ROOT / '.training/comparisons/translategemma-27b-q4/installation-manifest.json',
                              summary['installationManifestSha256'])
    tail.require(summary.get('modelSize') == installed.get('modelSize') == '27b'
                 and summary.get('modelSha256') == installed.get('model', {}).get('sha256') == MODEL_SHA
                 and summary.get('runtimeFiles') == installed.get('runtimeFiles')
                 and summary.get('templateSha256') == installed.get('templateSha256'),
                 'fixed_model_runtime_template_required')
    for field in ('postProcessingApplied','translationMemoryApplied','appDeploymentPerformed','humanReviewed'):
        tail.require(summary.get(field) is False, 'unapproved_processing_' + field)
    for name, digest in summary['codeHashes'].items():
        evidence.raw(name, digest)
    for row, pred in zip(rows, predictions, strict=True):
        raw = evidence.read(RUN / pred['rawResponseFile'], pred['rawResponseSha256'])
        tail.require(common.text_sha(raw['prompt']) == pred['promptSha256']
                     and raw['prompt'].count(row['source']) == 1, 'actual_prompt_source_differs')
    for name in ('verify_tg27_tail2.py','v5_tail2_review_evidence.py','v4_review_evidence.py',
                 'v3_review_evidence.py','prepare_finance_answerability.py','linguistic_screen.py'):
        evidence.raw(Path(__file__).with_name(name))
    evidence.unchanged()
    return dict(version=tail.EVIDENCE_CONTRACT, verifiedAtUTC=datetime.now(timezone.utc).isoformat(),
        run=RUN.relative_to(ROOT).as_posix(), selectedIds=tail.TAIL_IDS, completedRows=2,
        originalDevelopmentSourceCount=18, fullDev18RunValidated=False, tail2RunValidated=True,
        meaningReviewed=False, questionAnswersProduced=False, nativeCalls=0,
        humanReviewed=False, independentBlindReview=False, qualityAccepted=False,
        inputIdentity=identity, evidenceFiles={str(p):v for p,v in evidence.files.items()})


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    out = common.safe_path(args.output, ROOT / '.training')
    tail.require(not out.exists(), 'new_verification_output_required')
    record = verify()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x', encoding='utf-8') as f:
        json.dump(record, f, indent=2)
    print(json.dumps({k:record[k] for k in ('selectedIds','completedRows','fullDev18RunValidated','tail2RunValidated','nativeCalls')}))


if __name__ == '__main__':
    main()
