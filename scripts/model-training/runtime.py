"""Verified offline CTranslate2 inference for an evaluated Marian checkpoint.

This module intentionally never imports torch or transformers. Raw generation
and application glossary correction are separate entry points.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

APP_ROOT = Path(__file__).resolve().parents[2]
TRAINING_ROOT = APP_ROOT / ".training"
BRIDGE_VERSION = "finetuned-marian-bridge-v2-pad-start"
EXPORT_VARIANT = "marian-pad-start-v2"
MODEL_DIRECTORY = "ctranslate2-pad-v2"
EXPORT_MANIFEST = "export-manifest-v2.json"
PARITY_SUMMARY = "parity-summary-v2.json"
PARITY_PREDICTIONS = "parity-dev-predictions-v2.jsonl"
CODE_FILES = ("scripts/model-training/bridge.py", "scripts/model-training/runtime.py", "scripts/local-translation/runtime.py")
SPM_HASHES = {
    "source.spm": "3d0591e65c49541d82f48df33d7b322c3d4ee7aa0ee8747f9a7f9355dbf22c95",
    "target.spm": "3d2aa641a0890d8966ab8703b109895a4e522713ce99b4a0192bfacb495bc97c",
}
DEFAULT_DECODING = {"beams": 4, "maxInputTokens": 192, "maxNewTokens": 256}
PROCESSING = {"device": "cpu", "computeType": "int8", "intraThreads": 4, "interThreads": 1,
              "tokenizer": "separate-spm-id-vocab-v1-eos-appended", "normalizer": "spm-embedded-only",
              "sentencizer": "english-punctuation-conservative-v1", "lengthPenalty": 1.0,
              "returnEndToken": True, "minDecodingLength": 1, "patience": 1,
              "samplingTopK": 1, "skipSpecialTokens": ["<unk>", "</s>", "<pad>"],
              "decoderStartToken": "<pad>", "preserveLearnedPadEmbedding": True,
              "suppressSequences": [["<pad>"]],
              "protectedValues": "shared-argos-v3-symbol-and-PV-bypass"}


def read_json(path):
    return json.loads(path.read_text("utf-8"))


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def local_path(value, required_root=APP_ROOT):
    if not isinstance(value, str) or Path(value).is_absolute():
        raise ValueError("Expected a project-relative path")
    path = (APP_ROOT / value).resolve()
    if not path.is_relative_to(required_root.resolve()):
        raise ValueError("Path escaped its project directory")
    return path


def relative(path):
    return path.resolve().relative_to(APP_ROOT).as_posix()


def decoding_settings(value):
    if (not isinstance(value, dict) or set(value) != set(DEFAULT_DECODING)
            or any(type(number) is not int for number in value.values())
            or value["beams"] != 4 or not 16 <= value["maxInputTokens"] <= 512
            or not 16 <= value["maxNewTokens"] <= 512):
        raise ValueError("Unsupported inference configuration")
    return dict(value)


def runtime_version(decoding):
    identity = {"versions": {name: importlib.metadata.version(name) for name in ("ctranslate2", "sentencepiece")},
                "python": ".".join(map(str, sys.version_info[:3])), "processing": PROCESSING,
                "decoding": decoding_settings(decoding),
                "codeFiles": {name: sha256(APP_ROOT / name) for name in CODE_FILES}}
    return BRIDGE_VERSION + ":" + canonical_hash(identity)


def model_inventory(directory):
    files = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Linked model files are unsupported")
        if path.is_file():
            files.append({"path": relative(path), "size": path.stat().st_size, "sha256": sha256(path)})
    if not files:
        raise ValueError("Missing exported model files")
    return files


def verify_inventory(manifest):
    directory = local_path(manifest["modelPath"], TRAINING_ROOT)
    files = manifest.get("modelFiles")
    if not directory.is_dir() or not isinstance(files, list) or not files:
        raise ValueError("Missing model inventory")
    if canonical_hash(files) != manifest.get("modelHash") or model_inventory(directory) != files:
        raise ValueError("Exported model inventory or bytes changed")
    return directory


def verify_manifest():
    manifest = read_json(TRAINING_ROOT / "deployed/manifest.json")
    if (manifest.get("schemaVersion") != 1 or manifest.get("provider") != "finetuned"
            or manifest.get("promotionEligible") is not True
            or not isinstance(manifest.get("model"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", manifest.get("runId", ""))):
        raise ValueError("Invalid deployed model identity")
    if manifest.get("runtimeVersion") != runtime_version(manifest.get("decoding")):
        raise ValueError("Inference runtime changed; recheck and register the model")
    python = local_path(manifest["pythonPath"])
    if not python.is_file() or python != Path(sys.executable).resolve():
        raise ValueError("Unexpected Python runtime")
    directory = verify_inventory(manifest)
    run = TRAINING_ROOT / "runs" / manifest["runId"]
    if directory != (run / MODEL_DIRECTORY).resolve():
        raise ValueError("Unexpected exported model directory")
    for filename, key in (("evaluation-summary.json", "evaluationSummarySha256"), (PARITY_SUMMARY, "paritySummarySha256")):
        if sha256(run / filename) != manifest.get(key):
            raise ValueError("Deployment evidence changed")
    evaluation, parity = read_json(run / "evaluation-summary.json"), read_json(run / PARITY_SUMMARY)
    if (not evaluation.get("promotionEligible") or not evaluation.get("devGate", {}).get("passed")
            or not evaluation.get("testGate", {}).get("passed") or not parity.get("passed")
            or parity.get("modelHash") != manifest["modelHash"]
            or parity.get("runtimeVersion") != manifest["runtimeVersion"]
            or parity.get("sourceModelSha256") != evaluation.get("trainedWeightFileSha256")):
        raise ValueError("Deployment quality evidence does not match this model")
    return manifest


def disable_network():
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        os.environ[name] = "1"
    def audit(event, _args):
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.sendto"}:
            raise RuntimeError("Network is disabled for local translation")
    sys.addaudithook(audit)


def glossary_module():
    name = "damodaran_shared_glossary"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, APP_ROOT / "scripts/local-translation/runtime.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class LocalTranslator:
    def __init__(self, manifest):
        disable_network()
        import ctranslate2
        import sentencepiece as spm
        self.settings = decoding_settings(manifest["decoding"])
        directory = local_path(manifest["modelPath"], TRAINING_ROOT)
        self.source = spm.SentencePieceProcessor(model_file=str(directory / "source.spm"))
        self.target = spm.SentencePieceProcessor(model_file=str(directory / "target.spm"))
        config = read_json(directory / "tokenizer_config.json")
        if not config.get("separate_vocabs") or config.get("source_lang") != "en" or config.get("target_lang") != "ko":
            raise ValueError("Expected separate English/Korean tokenization")
        for name, processor, mapping, native_name in (("source.spm", self.source, "vocab.json", "source_vocabulary.json"),
                                                      ("target.spm", self.target, "target_vocab.json", "target_vocabulary.json")):
            if sha256(directory / name) != SPM_HASHES[name] or processor.get_piece_size() != 32000:
                raise ValueError("SentencePiece model differs from the pinned original")
            expected = {processor.id_to_piece(index): index for index in range(32000)}
            expected["<pad>"] = 32000
            if read_json(directory / mapping) != expected:
                raise ValueError("Tokenizer vocabulary IDs changed")
            if read_json(directory / native_name) != [processor.id_to_piece(index) for index in range(32000)] + ["<pad>"]:
                raise ValueError("CTranslate2 source/target vocabulary differs from SentencePiece IDs")
        native_config = read_json(directory / "config.json")
        if (native_config.get("eos_token") != "</s>" or native_config.get("unk_token") != "<unk>"
                or native_config.get("decoder_start_token") != "<pad>"):
            raise ValueError("Unexpected decoder special tokens")
        self.native = ctranslate2.Translator(str(directory), device="cpu", compute_type="int8", inter_threads=1, intra_threads=4)
        if self.native.device != "cpu" or self.native.compute_type != "int8_float32":
            # CT2 reports its effective compute type; int8 on CPU accumulates in
            # FP32. Any backend fallback changes the preregistered parity setup.
            if self.native.device != "cpu" or self.native.compute_type != "int8":
                raise ValueError("Unexpected CTranslate2 compute backend")
        self.glossary = glossary_module()

    def raw_result(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 150000:
            raise ValueError("Invalid translation input")
        if re.search(r"<(?:unk|pad|s)>|</s>", text):
            raise ValueError("Reserved tokenizer literal in input")
        pieces = self.source.encode(text, out_type=str)
        tokens = [piece if self.source.piece_to_id(piece) != 0 else "<unk>" for piece in pieces] + ["</s>"]
        if len(tokens) > self.settings["maxInputTokens"]:
            raise ValueError("Input exceeds the model token limit; no source truncation is allowed")
        result = self.native.translate_batch([tokens], beam_size=self.settings["beams"], patience=1,
                    num_hypotheses=1, length_penalty=1.0, max_input_length=0,
                    max_decoding_length=self.settings["maxNewTokens"], min_decoding_length=1,
                    sampling_topk=1, return_end_token=True, suppress_sequences=[["<pad>"]])[0]
        hypothesis = result.hypotheses[0]
        capped = len(hypothesis) >= self.settings["maxNewTokens"] or not hypothesis or hypothesis[-1] != "</s>"
        clean = [piece for piece in hypothesis if piece not in PROCESSING["skipSpecialTokens"]]
        translated = self.target.decode_pieces(clean).replace("▁", " ").strip()
        return {"translatedText": translated, "generatedTokens": len(hypothesis), "atLengthLimit": capped}

    def translate_plain(self, text):
        if not re.search(r"[A-Za-z]", text):
            return text
        result = self.raw_result(text.strip())
        if not result["translatedText"] or result["atLengthLimit"]:
            raise ValueError("Empty or capped model output")
        return text[:len(text) - len(text.lstrip())] + result["translatedText"] + text[len(text.rstrip()):]

    def source_sentences(self, text):
        # Preserve whitespace and whole sentences. Do not split abbreviations,
        # initials or decimal numbers, and never split just to fit a token cap.
        cuts = []
        for match in re.finditer(r"[.!?][\"')\]]*\s+(?=[A-Z])", text):
            prefix = text[:match.start() + 1]
            if re.search(r"\b(?:Mr|Mrs|Ms|Dr|Prof|vs|etc|Inc|Corp|Ltd|e\.g|i\.e|[A-Z])\.$", prefix, re.IGNORECASE):
                continue
            cuts.append(match.end())
        cursor, pieces = 0, []
        for end in cuts:
            pieces.append(text[cursor:end])
            cursor = end
        if cursor < len(text):
            pieces.append(text[cursor:])
        return pieces or [text]

    def translate_segment_result(self, text, glossary):
        return self.glossary.LocalTranslator.translate_segment_result(self, text, glossary)

    def translate_segment(self, text, glossary):
        return self.translate_segment_result(text, glossary)["translatedText"]
