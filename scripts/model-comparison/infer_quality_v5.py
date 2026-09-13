"""Run the verified v5 model locally in FP32 on CPU (default) or Intel XPU.

Example: python scripts/model-comparison/infer_quality_v5.py --run-id
finance-v5-terminology --text "The amount is unchanged." --device cpu

Without --text, read UTF-8 text from stdin. The original frozen infer_v5.py
remains unchanged; this entry point uses the separate serialization-compatible
verifier. No glossary, translation memory, evaluation or app activation occurs.
"""
from __future__ import annotations

import json
import sys

import verify_quality_v5 as verifier

infer = verifier.infer


def main():
    """Reuse the fixed FP32 CLI and restore its module state on every exit."""
    previous_verifier, previous_description = infer.verified_model, infer.__doc__
    infer.verified_model = verifier.verified_model
    infer.__doc__ = __doc__
    try:
        infer.main()
    finally:
        infer.verified_model = previous_verifier
        infer.__doc__ = previous_description


def cli():
    try:
        main()
        return 0
    except Exception as error:
        message = (str(error) if isinstance(error, infer.InferenceError)
                   else "선택된 v5 모델의 로컬 번역에 실패했습니다. 실행 기록과 설치 상태를 확인하세요.")
        print(json.dumps({"error": {"code": "LOCAL_V5_QUALITY_INFERENCE_FAILED",
                                   "type": type(error).__name__, "message": message[:300]}},
                         ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
