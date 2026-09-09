"""Private stdin/stdout NDJSON bridge. EOF releases the process and its model."""
from __future__ import annotations

import contextlib
import io
import json
import logging
import sys

from runtime import LocalTranslator, verify_manifest

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
logging.disable(logging.CRITICAL)


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def validate_request(value) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("id"), str) or len(value["id"]) > 200:
        raise ValueError("Invalid request")
    segments = value.get("segments")
    glossary = value.get("glossary", [])
    if not isinstance(segments, list) or not segments or len(segments) > 1000:
        raise ValueError("Invalid segments")
    identifiers = set()
    chars = 0
    for item in segments:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or len(item["id"]) > 400 or not isinstance(item.get("text"), str)
                or item["id"] in identifiers):
            raise ValueError("Invalid segment")
        identifiers.add(item["id"])
        chars += len(item["text"])
    if chars > 150000:
        raise ValueError("Input too large")
    if not isinstance(glossary, list) or len(glossary) > 100:
        raise ValueError("Invalid glossary")
    for item in glossary:
        if (not isinstance(item, dict) or not isinstance(item.get("source"), str)
                or not isinstance(item.get("target"), str)
                or not item["source"].strip() or not item["target"].strip()
                or len(item["source"]) > 500 or len(item["target"]) > 500):
            raise ValueError("Invalid glossary term")
        if item.get("mode", "exact") not in {"phrase", "exact"}:
            raise ValueError("Invalid glossary mode")
        for field in ("aliases", "replacements"):
            values = item.get(field, [])
            if not isinstance(values, list) or len(values) > 30 or any(not isinstance(value, str) or not value.strip() or len(value) > 500 for value in values):
                raise ValueError("Invalid glossary variants")


def main() -> None:
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            manifest = verify_manifest()
            translator = LocalTranslator(manifest)
        emit({"ready": True, "model": manifest["model"], "modelHash": manifest["modelHash"], "runtimeVersion": manifest["runtimeVersion"]})
    except Exception:
        emit({"ready": False, "error": {"code": "LOCAL_SETUP_REQUIRED", "message": "로컬 번역기 설치 또는 모델 무결성을 확인해 주세요."}})
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
                segments = [{"id": item["id"], **translator.translate_segment_result(item["text"], value.get("glossary", []))} for item in value["segments"]]
            emit({"id": request_id, "data": {"segments": segments}, "inputTokens": None, "outputTokens": None, "requestId": None})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            emit({"id": request_id, "error": {"code": "LOCAL_INVALID_REQUEST", "message": "번역 입력 형식이나 길이를 확인해 주세요."}})
        except Exception:
            emit({"id": request_id, "error": {"code": "LOCAL_TRANSLATION_FAILED", "message": "로컬 번역 처리에 실패했습니다. 원문과 기존 번역은 유지됩니다."}})


if __name__ == "__main__":
    main()
