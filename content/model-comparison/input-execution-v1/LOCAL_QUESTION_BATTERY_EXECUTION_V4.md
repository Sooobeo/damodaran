# 독립 질문 평가의 배터리 실행 v4

사용자는 2026-09-14 현재 세션에서 “충전기 연결은 안 됐는데 배터리 잔량은 충분함. 진행해봐”라고 명시했다. 이 최신 지시에 따라 **남은 S5 질문 평가의 AC 필수 조건을 변경**한다. 이전 AC 전용 실패와 [복구 v3](LOCAL_QUESTION_ZERO_CALL_RECOVERY_V3.md)는 당시 조건과 상태 그대로 보존한다. 배터리 승인은 번역·질문 품질 기준이나 모델 설정을 변경하지 않는다.

## 전원 정책

Windows `GetSystemPowerStatus`의 같은 power snapshot에서 ACLineStatus와 BatteryLifePercent를 기록한다. ACLineStatus는 0 또는 1을 허용한다. 배터리 잔량은 알려진 정수 0~100이어야 하며, **시작과 실행 중 모두 20% 초과**를 요구한다. 20% 이하, 255 등 잔량 미확인, AC 상태 미확인 또는 조회 실패에서는 실행을 중지한다. 잔량 추정치로 남은 전체 실행 시간이나 완료를 보장하지 않는다.

CPU4·BelowNormal·단일 소유 native·6 GiB working set/private 관측 상한·9 GiB 시작 physical/commit·실행 중 여유 메모리·시간 제한·프로세스 경합 감시를 유지한다. 모델·runtime·Python·질문·sampling·context/output token 한도는 이전과 같다. 임시 `SetThreadExecutionState` 요청도 AC 검사를 배터리 정책으로 바꾼 별도 경로에서 만들고 같은 소유 thread에서 해제한다. 전원 설정 자체는 변경하지 않는다.

배터리·메모리 조건을 만족하지 못한 사전검사는 새 audit에 남기고 새 실행/claim을 만들지 않는다. 실제 실행에 들어간 뒤의 실패·부분 응답·불명확한 요청은 그대로 보존하며 자동 재시도나 답변 내용의 보완은 하지 않는다. 실행 중 임계값에 도달하면 소유 native만 종료하고 다음 질문을 시작하지 않는다. 이미 유효하게 저장한 답변도 지우거나 좋은 결과로 교체하지 않는다.

## 새 실행과 이전 기록

실행 버전은 `input-execution-v1-local-question-runner-battery-v4`다. `local_question_runner_battery_v4.py`의 context producer도 v4이며, 실제 배터리 관측·감시 함수와 새 PROFILE을 사용한다. 이전 frozen context가 배터리로 실행됐다고 잘못 기록하지 않는다. 기존 HTTP·prompt/token parity·sampling/JSON 응답 검사·소유 종료·늦은 monitor 오류 검사를 재사용하거나 의미를 유지한 명시적 코드로 연결한다. frozen 모듈의 전역 변수/함수를 monkeypatch하지 않는다.

실제 새 출력 폴더는 `.training/quality-evaluation/input-preparation-v1/s5-local-question-native-battery-v4-20260914/attempt-001/`이다. 이전 inventory-v2의 failed 3파일과 원래 영구 claim은 변경하지 않는다. 기존 [16파일 실행 동결](local-question-execution-freeze-v1.json), [9파일 설치 교정](local-question-inventory-correction-freeze-v2.json), [9파일 복구 v3 동결](local-question-zero-call-recovery-freeze-v3.json)을 검증한다. v3 실제 run은 시작하지 않았으며 그 사실을 v4 시작 전에 확인한다.

v4 실행을 시작할 때 별도 v4 claim을 만들고, 사용하지 않은 v3 claim 경로에는 **v3 실행이 아닌 옛 진입점 차단용 표시**를 남긴다. 이 표시는 v4가 같은 질문을 실행하므로 v3가 나중에 재생성하지 못하게 한다. 표시를 v3 모델 요청·실행 성공·실제 답변으로 세지 않는다. 표시와 새 claim은 생성 직후 해시를 고정하여 각 context와 최종 검증에 연결한다. 기존 원래 claim을 삭제하거나 임의로 무시하는 옵션은 만들지 않는다.

이 문서와 runner/transport/evaluation/append 코드 및 각각의 테스트, 총 9파일을 [배터리 실행 동결](local-question-battery-execution-freeze-v4.json)에 기록한다. 동결은 기존 `zeroCallRecovery`, 최신 사용자 전원 승인, 정확한 전원 정책과 실제 질문 호출0을 함께 보존한다. `batteryExecution` 정체성을 실행 계획·summary·수집·답변 동결·평가 보고서·원장 관측에 유지하고 `zeroCallRecovery` 및 `inventoryCorrection`도 그대로 연결한다.

## 질문·평가 보존

기존 `.training/quality-evaluation/input-preparation-v1/s5-local-question-export-20260914/`의 64개 packet과 manifest를 그대로 사용한다. 같은 Qwen3.5-9B Q4_K_M 모델과 기존 설정으로 packet마다 완전히 새 native 프로세스를 실행한다. 번역문과 두 질문만 제공하며 source·정답·구성 이름·다른 출력을 전달하지 않는다. 각 프로세스에서 completion은 단 한 번이다. 실제 `cache_n=0`, prompt/token 대조, 원시 요청·응답과 실제 프로세스 정체성을 검증한다. 배터리/AC 실행의 속도 차이를 번역 품질 개선이나 인과 효과로 해석하지 않는다.

전체64개 유효 답변의 생성·소유 종료·최종 무결성 검증을 마친 뒤 수집하고 **모든 답변을 먼저 동결**한다. 이후 별도 원문 평가자가128질문을 채점한다. source를 본 root가 질문 답변을 대신 작성하지 않는다. 실제 결과 없는 합성 검사를 QA 완료나 사람 검수로 기록하지 않는다.

S5의 의미 개선·일반 회귀·보류·후보 선택 기준, 조건부 S6 독립80단위와 최종 수용 기준을 유지한다. 승인받은 배터리 실행을 이유로 분모·정답·핵심 명제 기준을 변경하지 않는다. 원장 연결은 실제 최종 증거를 모두 재검증하며 기존836사건/번역검토146개와 반복 추가0 조건을 보존한다. 앱 등록·운영 DB·개인 기록·웹 worker에는 이 비교용 전원 변경을 적용하지 않는다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_runner_battery_v4.py preflight --destination .training/verifications/question-battery-preflight-20260914.json
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_runner_battery_v4.py run --export .training/quality-evaluation/input-preparation-v1/s5-local-question-export-20260914 --destination .training/quality-evaluation/input-preparation-v1/s5-local-question-native-battery-v4-20260914/attempt-001
```

실제 실행에는 v4 진입점을 사용한다. 새 `preflight`는 원문/정답을 읽지 않고 현재 상태를 관측하며 native·completion·임시 전원 요청을 만들지 않는다. 실제 `run`은 공유 mutex와 전원 요청 안에서 현재 시작 조건을 다시 확인한다. 검사는 배터리 20/21 경계·미확인/전원 전환·배터리 저하 중단·소유 종료·이전 claim 보존·표시 해시·배터리 lineage 변조 거부와 기존 실제 native 검사의 유지를 포함한다.
