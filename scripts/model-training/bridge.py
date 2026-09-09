"""Private offline NDJSON bridge; EOF releases the local trained model."""
from __future__ import annotations

import contextlib
import io
import json
import logging
import sys

from runtime import LocalTranslator, disable_network, verify_manifest


def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def validate_request(value):
    if not isinstance(value, dict) or not isinstance(value.get("id"), str) or len(value["id"]) > 200:
        raise ValueError("Invalid request")
    segments, glossary = value.get("segments"), value.get("glossary", [])
    if not isinstance(segments, list) or not segments or len(segments) > 1000:
        raise ValueError("Invalid segments")
    identifiers, chars = set(), 0
    for item in segments:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or len(item["id"]) > 400
                or not isinstance(item.get("text"), str) or item["id"] in identifiers):
            raise ValueError("Invalid segment")
        identifiers.add(item["id"])
        chars += len(item["text"])
    if chars > 150000 or not isinstance(glossary, list) or len(glossary) > 100:
        raise ValueError("Invalid input size")
    for item in glossary:
        if (not isinstance(item, dict) or not isinstance(item.get("source"), str) or not item["source"].strip()
                or not isinstance(item.get("target"), str) or not item["target"].strip()
                or len(item["source"]) > 500 or len(item["target"]) > 500
                or item.get("mode", "exact") not in {"phrase", "exact"}):
            raise ValueError("Invalid glossary term")
        for field in ("aliases", "replacements"):
            values = item.get(field, [])
            if (not isinstance(values, list) or len(values) > 30
                    or any(not isinstance(part, str) or not part.strip() or len(part) > 500 for part in values)):
                raise ValueError("Invalid glossary variants")


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    logging.disable(logging.CRITICAL)
    disable_network()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            manifest = verify_manifest()
            translator = LocalTranslator(manifest)
        emit({"ready": True, "provider": "finetuned", "model": manifest["model"],
              "modelHash": manifest["modelHash"], "runtimeVersion": manifest["runtimeVersion"]})
    except Exception:
        emit({"ready": False, "error": {"code": "LOCAL_SETUP_REQUIRED", "message": "학습 모델 등록 상태와 무결성·평가 기록을 확인해 주세요."}})
        return
    for line in sys.stdin:
        request_id = None
        try:
            if len(line) > 1200000:
                raise ValueError("Request too large")
            value = json.loads(line)
            if isinstance(value, dict) and isinstance(value.get("id"), str) and len(value["id"]) <= 200:
                request_id = value["id"]
            validate_request(value)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                segments = [{"id": item["id"], **translator.translate_segment_result(item["text"], value.get("glossary", []))}
                            for item in value["segments"]]
            emit({"id": request_id, "data": {"segments": segments}, "inputTokens": None, "outputTokens": None, "requestId": None})
        except (ValueError, TypeError, KeyError):
            emit({"id": request_id, "error": {"code": "LOCAL_INVALID_REQUEST", "message": "입력 형식·문장 길이 또는 번역 결과의 잘림을 확인해 주세요."}})
        except Exception:
            emit({"id": request_id, "error": {"code": "LOCAL_TRANSLATION_FAILED", "message": "학습 모델의 로컬 번역에 실패했습니다. 원문과 기존 번역은 유지됩니다."}})


if __name__ == "__main__":
    main()
