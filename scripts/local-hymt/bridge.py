"""Private NDJSON bridge for a registered Hy-MT2 model; no provider selection."""
from __future__ import annotations

import json
import sys

MAX_LINE_BYTES = 1200000


def require(condition):
    if not condition:
        raise ValueError("invalid_request")


def parse_request(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda _value: require(False))


def validate_request(value, identity):
    require(isinstance(value, dict) and set(value) <= {"id", "modelIdentity", "context", "segments", "glossary"})
    require(isinstance(value.get("id"), str) and 1 <= len(value["id"]) <= 200)
    require(value.get("modelIdentity") == identity)
    context = value.get("context")
    require(isinstance(context, str) and len(context) <= 12000)
    segments = value.get("segments")
    require(isinstance(segments, list) and 1 <= len(segments) <= 1000)
    clean, ids, total = [], set(), 0
    for segment in segments:
        require(isinstance(segment, dict) and set(segment) == {"id", "text"})
        identifier, text = segment["id"], segment["text"]
        require(isinstance(identifier, str) and 1 <= len(identifier) <= 400 and identifier not in ids)
        require(isinstance(text, str) and 1 <= len(text) <= 12000 and bool(text.strip()))
        ids.add(identifier)
        total += len(text)
        clean.append({"id": identifier, "text": text})
    require(total <= 150000)
    # The app's generic request includes this field. Hy-MT2 uses only the sealed
    # registered catalog, never caller-supplied target/reference annotations.
    glossary = value.get("glossary", [])
    require(isinstance(glossary, list) and len(glossary) <= 100)
    return {"id": value["id"], "context": context, "segments": clean}


def serve(input_stream, output_stream, *, verifier=None, engine_factory=None):
    if verifier is None:
        from deployment import verify_manifest
        verifier = verify_manifest
    if engine_factory is None:
        from engine import OwnedHymtEngine
        engine_factory = OwnedHymtEngine
    engine, status = None, 0

    def emit(value):
        output_stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
        output_stream.flush()

    def error(code, message, request_id=None):
        value = {"error": {"code": code, "message": message}}
        if request_id is not None:
            value["id"] = request_id
        emit(value)

    try:
        try:
            bundle = verifier()
            engine = engine_factory(bundle.manifest["profile"])
            bundle.assert_unchanged()
        except BaseException:
            emit({"ready": False, "error": {"code": "LOCAL_SETUP_REQUIRED",
                  "message": "로컬 번역 모델의 등록 상태와 파일 무결성을 확인해 주세요."}})
            return 1
        manifest = bundle.manifest
        emit({"ready": True, "provider": "hymt", "model": manifest["model"], "modelHash": manifest["modelHash"],
              "runtimeVersion": manifest["runtimeVersion"], "identity": bundle.identity})
        while True:
            raw = input_stream.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            if len(raw) > MAX_LINE_BYTES:
                error("LOCAL_INVALID_REQUEST", "번역 요청의 크기가 허용 범위를 넘었습니다.")
                status = 1
                break
            request_id = None
            try:
                value = parse_request(raw)
                if isinstance(value, dict) and isinstance(value.get("id"), str) and 1 <= len(value["id"]) <= 200:
                    request_id = value["id"]
                request = validate_request(value, bundle.identity)
            except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                error("LOCAL_INVALID_REQUEST", "번역 요청의 문단·문맥·모델 식별값을 확인해 주세요.", request_id)
                continue
            try:
                bundle.assert_unchanged()
            except Exception:
                error("LOCAL_PROVIDER_CHANGED", "번역 실행 파일이나 등록 구성이 변경되었습니다.", request_id)
                status = 1
                break
            try:
                segments = []
                for segment in request["segments"]:
                    result = engine.translate(segment["text"], request["context"], bundle.terms)
                    require(isinstance(result, dict) and set(result) == {"translatedText", "warnings"}
                            and isinstance(result["translatedText"], str) and bool(result["translatedText"].strip())
                            and isinstance(result["warnings"], list) and all(isinstance(w, str) for w in result["warnings"]))
                    segments.append({"id": segment["id"], **result})
            except (KeyboardInterrupt, SystemExit):
                error("LOCAL_STOPPED", "로컬 번역 실행을 중단했습니다.", request_id)
                status = 1
                break
            except Exception:
                error("LOCAL_TRANSLATION_FAILED", "번역을 완료하지 못했습니다. 문단 길이와 원문을 확인해 주세요.", request_id)
                continue
            try:
                # In-flight file changes must not publish a seemingly valid result.
                bundle.assert_unchanged()
            except Exception:
                error("LOCAL_PROVIDER_CHANGED", "번역 실행 파일이나 등록 구성이 변경되었습니다.", request_id)
                status = 1
                break
            emit({"id": request_id, "data": {"segments": segments},
                  "inputTokens": None, "outputTokens": None, "requestId": None})
    except (KeyboardInterrupt, SystemExit):
        status = 1
    finally:
        if engine is not None:
            try:
                engine.close()
            except BaseException:
                error("LOCAL_CLEANUP_FAILED", "로컬 번역 프로세스 정리를 완료하지 못했습니다.")
                status = 1
    return status


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    return serve(sys.stdin.buffer, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
