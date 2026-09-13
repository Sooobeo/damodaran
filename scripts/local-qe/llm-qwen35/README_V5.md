# Qwen 의미 전용 v5: 고정 개발 8개 진단

이 버전은 `contract_v2.py`의 의미 문제·불확실성만 평가한다. 입력은 고정한 `semantic-v2-dev8-v1.jsonl` 8개이며 모델에 전달하는 필드는 `source`, `translation`, `context`뿐이다. ID·기존 판정·선정 근거·중단 규칙은 모델 입력에 들어가지 않는다. v4·기존 profile·실측·freeze·토큰 감사 파일을 수정하지 않는다.

준비 단계의 `resource-profile-v4.json`은 `tokenBudgetAudit:null`이었으며, 재개 후 실제 감사의 경로·해시를 고정했다. 감사와 외부 실행 binding이 없으면 `--run`을 거부한다. 현재는 아래 결과 절처럼 vocabulary 감사와 실제 진단을 완료했고 첫 미탐으로1/8에서 중단했다. 미실행7개를 포함한 전체8개 품질 검증이나 앱 등록을 완료한 상태는 아니다.

## 자원 가설과 유지 조건

같은 Qwen3.5-9B Q4_K_M·고정 native를 CPU 4개, BelowNormal, poll 0, 단일 슬롯, context 4096, output 2048, checkpoint 3, 기존 sampling/EOS로 실행한다. 새 관측 한도는 자식 WS·peakWS·private 각각 6GiB이며 시작 직전 가용 physical·commit은 각각 최소 9GiB다. 실행 중 시스템 여유 1GiB 미만, 자식 관측 한도 초과, 기존 600초 시작/요청·14400초 전체 시간 제한은 실패로 중단한다.

WS API 설정은 6GiB에서 64MiB를 뺀 **6080MiB**다. 이 설정은 private commit이나 Python·OS·다른 앱을 합한 총메모리 hard cap이 아니다. `mmap`, OS working-set 창, checkpoint, 미측정 scratch·직렬화 비용 때문에 OOM이나 paging을 막는다는 보장은 없다. `cache-ram 0`도 슬롯 checkpoint까지 없애지 않는다. 페이지 fault는 soft/hard를 합한 계수이며 disk hard fault로 해석하지 않는다.

근거는 [v4 자원 연구 기록](../../../.training/verifications/QWEN_V4_6G_RESOURCE_FEASIBILITY_20260911.md)이다. 이전 전체 관측 peakWS는 5,622,362,112바이트(약 5.236GiB), 새 API 한도까지 차이는 약 0.701GiB였다. 이것만으로 새 프롬프트가 안전하거나 품질·속도가 같다고 판단하지 않는다. 프롬프트와 cap을 함께 바꾸므로 각각의 효과를 분리할 수 없다. TG27의 별도 시작 조건을 이 9GiB 기준으로 바꾸지 않는다. 사용자 앱 종료나 OS 전원 정책 변경은 수행하지 않는다.

## 2026-09-11 재개 결과

아래 준비를 완료하고 실제 개발8 진단을 한 번 실행했다. 첫 LDEV26-010에서 의미 오류 없음으로 응답해 FN1·완료1/8 뒤 `stopped_futility`로 종료했다. 실제 응답/EOS/계약·자원·소유 종료는 사후 평가기로 확인했으며 미실행7개·전체48·앱 등록은 진행하지 않는다. [결과 보고서](../../../content/model-comparison/QWEN35_V5_REVIEW_20260911.md)에 준비 수정·합성89개·vocab 감사·실제 자원과 품질 실패를 구분한다. 현재 freeze에 포함된 코드·profile·평가기와 generation claim은 보존한다.

## 감사와 실행 순서

1. 새 코드·합성 검사·평가기 검토를 끝낸다. 토큰 감사는 runtime 파일도 해시로 묶으므로 감사 후 코드를 수정하면 다시 준비가 필요하다.
2. coordinator의 별도 계산 슬롯 승인 후 `token_budget_v2.py --run`을 1회 실행한다. 새 `token-budget-v2/attempt-NNN`만 만들며 고정 8개를 vocabulary-only로 처리한다. help/version 2개를 포함해 자식을 순차 생성하며 generation은 0이다. 기존 공식 `tokenize.cpp` 캐시가 없거나 SHA가 다르면 오류로 거부한다. 설치·다운로드·네트워크 복구는 없다.
3. root가 감사의 실제 결과·종료·무결성을 확인한 뒤 새 profile의 `tokenBudgetAudit.path/sha256`를 최종화한다. 기존 v1 first3 템플릿 일치 필드는 재사용하지 않는다. `nativeTemplateParity`는 `pending_actual_v5_apply_template`다.
4. root가 `DEST/freeze-v5-dev8.json`과 별도 `.training/verifications/` 실행 binding을 생성한다. profile은 외부 receipt 필요만 선언하므로 해시 순환이 없다.
5. 실제 `--run`은 binding, audit, 고정 8개 순서, 설치·모델/native 해시, 시작 메모리를 확인한다. 처음부터 매행 실제 `/apply-template` 결과와 `/tokenize` ID가 Jinja 감사와 같아야 generation을 허용한다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/local-qe/llm-qwen35/runtime_v5.py
.venv-training\Scripts\python.exe -B -X utf8 scripts/local-qe/llm-qwen35/token_budget_v2.py --help
# 아래 두 실행은 coordinator가 별도 승인·검토한 뒤에만 수행한다.
.venv-training\Scripts\python.exe -B -X utf8 scripts/local-qe/llm-qwen35/token_budget_v2.py --run
.venv-training\Scripts\python.exe -B -X utf8 scripts/local-qe/llm-qwen35/runtime_v5.py --run --output .translation/qe/llm-candidates/qwen35-9b/runs/semantic-v2-dev8-v5 --slot-approval <실제슬롯승인기록> --execution-binding .training/verifications/<root실행binding>.json
```

실행 binding의 version은 `qwen-v5-dev8-execution-binding-v1`, `rootReviewed:true`이며 정확한 freeze 경로·SHA를 담는다. freeze version은 `qwen-v5-dev8-freeze-v1`이고 runtimeVersion, inputSha256, codeHashes 전체, resourceProfileSha256, tokenBudgetAuditSha256, materialWarningMapping, screenGate, 고정 경로 `scripts/local-qe/evaluate_llm_review_v3.py`의 SHA를 묶는다. 평가기나 output 폴더의 Python을 import하지 않는다. binding·freeze·평가기 파일은 요청 앞과 종료 경계에서도 해시를 재검증한다.

## 중단·증거의 의미

원시 응답 바이트·SHA를 먼저 저장하고 EOS/잘림·실제 prompt·tokens·sampling·cache·guard·contract 검증을 수행한다. 유효 assessment를 저장한 **뒤에만** 외부 `screen_gate_v1.check`를 호출한다. `material_warning_v3`의 primaryWarning이 고정 개발 판정과 처음 다르면 gate 파일을 저장하고 `stopped_futility`로 종료한다. 그 뒤 다음 generation과 자동 재시도는 없다. 이 중단은 유효한 개발 진단이므로 CLI exit 0이며, 기술 실패는 `failed`/exit 1이다.

`completedCount`에는 유효한 첫 불일치 행도 포함한다. `itemStatuses`는 completed/failed/not_run을 구분하고 `unexecutedIds`·`generationRequests`·`screenDecisions`·`futilityStop`을 별도로 기록한다. `.dev8-v5-generation.claim.json`은 첫 실제 시도 전 exclusive 생성하며 삭제하지 않는다. 동일 진단을 새 폴더에서 몰래 재시도할 수 없다.

요청 반환 시점에도 정확한 deadline을 다시 비교해 monitor가 관측하지 못한 초과를 latch한다. 늦은 raw는 보존하되 정상 assessment로 받아들이지 않는다. 자식 생성·종료 확인 실패는 `childProcessStopped:false`로 남기며 실행 lock을 보존한다. `memory.jsonl`에는 phase/row ID/요청 시작·경과/heartbeat gap, 소유 PID·creation·실제 priority·CPUtimes·메모리만 추가한다. native 로그에 원문·번역·prompt·API key를 추가하지 않는다. 비스트리밍의 실제 생성 토큰 진행은 알 수 없다고 기록한다. Windows 대기나 관측 공백의 원인을 자동 확정하지 않는다.

8개 전부 일치해도 독립 holdout이나 human 학습 검증이 아니며 전체 95/95, 번역 본문 의미·용어 품질, 의미 span precision, 앱 등록은 통과로 표시하지 않는다. 미실행 40개를 이 진단에 합쳐 완료로 계산하지 않는다.

## 합성 검사

```powershell
.venv-training\Scripts\python.exe -B -X utf8 -m unittest discover -s scripts/local-qe/llm-qwen35 -p test_runtime_v5.py -v
.venv-training\Scripts\python.exe -B -X utf8 -m unittest discover -s scripts/local-qe/llm-qwen35 -p test_token_budget_v2.py -v
```

합성 검사는 cap/시작 여유 경계·sampling 보존, 첫 요청 parity 거부, 불일치 이후 generation 0, 무효 구조, 원시 보존·deadline latch, 소유 PID/creation·종료 실패, binding 데이터 검증, 토큰 경계·UTF8·offline 캐시를 확인한다. 실제 모델·vocabulary 실행이나 운영 DB 검증을 대신하지 않는다.
