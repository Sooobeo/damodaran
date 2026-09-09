"""Export evaluated Marian weights while preserving the learned decoder start.

The frozen training script is unchanged. Old export/parity artifacts remain at
their original paths and are also copied to deployment-attempts/int8-v1 with a
verified source/destination hash map. This v2 artifact uses separate new paths.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

import infer
import runtime
import train


def archive_first_attempt(run):
    archive = run / "deployment-attempts/int8-v1"
    archive.mkdir(parents=True, exist_ok=True)
    mappings = []
    names = ("ctranslate2", "export-manifest.json", "parity-summary.json", "parity-dev-predictions.jsonl", "parity-diagnosis.json")
    for name in names:
        original = run / name
        if not original.exists():
            continue
        sources = sorted(original.rglob("*")) if original.is_dir() else [original]
        for source in sources:
            if source.is_symlink():
                raise ValueError("Linked old artifacts are unsupported")
            if not source.is_file():
                continue
            destination = archive / source.relative_to(run)
            digest = train.sha256(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(source, destination)
            if train.sha256(destination) != digest or destination.stat().st_size != source.stat().st_size:
                raise ValueError("Prior attempt archive differs from the original")
            mappings.append({"originalPath": train.relative(source), "preservedPath": train.relative(destination),
                             "sha256": digest, "size": source.stat().st_size})
    record = {"schemaVersion": 1, "createdAt": train.now(), "originalFilesRetained": True, "files": mappings}
    manifest = archive / "preservation-manifest.json"
    if manifest.exists():
        if json.loads(manifest.read_text("utf-8"))["files"] != mappings:
            raise ValueError("Prior attempt archive inventory changed")
    else:
        train.write_json(manifest, record)
    return manifest


def build_loader(marian_loader):
    class LearnedStartMarianLoader(marian_loader):
        def _remove_pad_weights(self, spec):
            # Full fine-tuning can update the tied pad row via output softmax.
            # Retain this row, rather than replacing its decoder start with 0.
            pass

        def set_decoder(self, spec, decoder):
            super().set_decoder(spec, decoder)
            spec.start_from_zero_embedding = False

        def set_config(self, config, model, tokenizer):
            super().set_config(config, model, tokenizer)
            if model.config.decoder_start_token_id != tokenizer.pad_token_id:
                raise ValueError("Unexpected Marian decoder start ID")
            config.decoder_start_token = tokenizer.pad_token

        def get_vocabulary(self, model, tokenizer):
            if not tokenizer.separate_vocabs:
                raise ValueError("Separate Marian vocabularies are required")
            vocabularies = []
            for mapping in (tokenizer.encoder, tokenizer.target_encoder):
                values = sorted(mapping.items(), key=lambda item: item[1])
                if [index for _, index in values] != list(range(32001)) or values[-1][0] != "<pad>":
                    raise ValueError("Unexpected full vocabulary IDs")
                vocabularies.append([token for token, _ in values])
            return vocabularies

        def set_vocabulary(self, spec, tokens):
            spec.register_source_vocabulary(tokens[0])
            spec.register_target_vocabulary(tokens[1])

    return LearnedStartMarianLoader()


def export(run_id):
    verified = infer.verified_model(run_id)
    if not verified["promotionEligible"]:
        raise ValueError("The selected FP32 model must pass both frozen evaluation gates")
    run = runtime.TRAINING_ROOT / "runs" / run_id
    preservation = archive_first_attempt(run)
    destination = run / runtime.MODEL_DIRECTORY
    report_path = run / runtime.EXPORT_MANIFEST
    if destination.exists():
        report = json.loads(report_path.read_text("utf-8"))
        actual = {path.name: train.sha256(path) for path in destination.iterdir() if path.is_file()}
        if (report.get("files") != actual or report.get("sourceModelSha256") != verified["weightHash"]
                or report.get("conversionVersion") != runtime.EXPORT_VARIANT):
            raise ValueError("Existing repaired export differs")
        train.emit("export-existing", path=train.relative(destination))
        return
    staging = run / (runtime.MODEL_DIRECTORY + ".stage-" + uuid.uuid4().hex)
    runtime.disable_network()
    from ctranslate2.converters import TransformersConverter
    from ctranslate2.converters.transformers import MarianMTLoader, _MODEL_LOADERS
    original = _MODEL_LOADERS["MarianConfig"]
    files = ["source.spm", "target.spm", "vocab.json", "target_vocab.json", "tokenizer_config.json", "special_tokens_map.json", "generation_config.json"]
    try:
        _MODEL_LOADERS["MarianConfig"] = build_loader(MarianMTLoader)
        converter = TransformersConverter(str(verified["path"]), copy_files=files)
        converter.convert(str(staging), quantization="int8")
    finally:
        _MODEL_LOADERS["MarianConfig"] = original
    config = json.loads((staging / "config.json").read_text("utf-8"))
    if config.get("decoder_start_token") != "<pad>":
        raise ValueError("Converted decoder does not start from the retained pad row")
    for filename in ("source_vocabulary.json", "target_vocabulary.json"):
        vocabulary = json.loads((staging / filename).read_text("utf-8"))
        if len(vocabulary) != 32001 or vocabulary[-1] != "<pad>":
            raise ValueError("Converted vocabulary dropped the trained start token")
    os.replace(staging, destination)
    report = {"format": "ctranslate2", "quantization": "int8", "conversionVersion": runtime.EXPORT_VARIANT,
              "sourceModelSha256": verified["weightHash"], "separateVocabularies": True,
              "tokenizerRepair": train.TOKENIZER_REPAIR, "decoderStartEmbeddingPreserved": True,
              "decoderStartToken": "<pad>", "suppressedOutputSequences": [["<pad>"]],
              "needsIndependentInferenceParityCheck": True, "priorAttemptPreservationManifest": train.relative(preservation),
              "exportScriptSha256": train.sha256(Path(__file__)),
              "files": {path.name: train.sha256(path) for path in destination.iterdir() if path.is_file()}}
    train.write_json(report_path, report)
    train.emit("export-complete", path=train.relative(destination), decoderStartEmbeddingPreserved=True, needsInferenceParityCheck=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    runtime.disable_network()
    with train.exclusive_run(args.run_id):
        export(args.run_id)


if __name__ == "__main__":
    main()
