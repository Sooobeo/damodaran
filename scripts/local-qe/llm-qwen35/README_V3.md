# 실제 실행기 v3

현재 실행 파일은 `runtime_v3.py`다. 기본 설명과 입력·품질 한계는 [README](README.md)를 따른다. v1/v2 코드와 해당 freeze/실패 기록은 보존했다.

- v1: Windows Store의 `sys._base_executable` alias를 파일로 해시하다 실패했다. 모델 시작·내용 생성은 없었다.
- v2: Windows `GetModuleFileNameW`로 실제 Python 이미지를 해시했다. 모델을 로드했지만 verbosity3 로그에 필수 EOG 근거가 없어 생성 전 중단했다. 물리적 로드는 runtime.log에서 확인되며 v2의 `modelLoaded:false`는 계약 검사 뒤에만 값을 갱신했던 구현상의 한계다.
- v3: `modelLoaded`와 `runtimeContractValidated`를 분리했다. 로그 수준4, offline, no-agent, no-warmup, mmap, no-repack, cache RAM0을 명시했다. 프롬프트·스키마·입력·sampling·8GiB 상한은 유지했다. 실행 전 `freeze-v3.json`을 남겼다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/local-qe/llm-qwen35/test_runtime_v3.py
.venv-training/Scripts/python.exe -B -X utf8 scripts/local-qe/llm-qwen35/runtime_v3.py --input .translation/qe/llm-candidates/qwen35-9b/inputs/smoke-first3-v1.jsonl --output .translation/qe/llm-candidates/qwen35-9b/runs/smoke-first3-v3 --run --slot-approval root-approved-qwen-first3-after-build-20260911
```

위 output 이름이 이미 있으면 새 실행 이름을 사용해야 한다. 단일 모델 슬롯·physical/commit 여유 각각11GiB를 시작 직전에 다시 확인한다. 슬롯 승인 없이 실행하지 않는다. 실제 종료 상태·원시 응답·행별 상태는 해당 실행의 `summary.json`과 artifact 해시로 확인한다.

[평가정책 v1](evaluation-policy-v1.json)은 최초 내용 출력 전에 고정했다. 주 판정은 유효 완료 응답의 `whole_paragraph_review`이며 minor·불확실성·위치 미확인을 사후에 빼지 않는다. major/critical 주장만의 판별은 보조로 보고하며 주 기준을 대체하지 않는다. 개별 의미 근거·구간 의미 정밀도는 독립 검토가 필요하다.
