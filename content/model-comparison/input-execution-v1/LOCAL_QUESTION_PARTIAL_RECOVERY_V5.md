# 독립 질문 평가 — 완료9개 보존과 나머지55개 복구 v5

사용자의 배터리 실행 승인에 따라 [v4](LOCAL_QUESTION_BATTERY_EXECUTION_V4.md)를 실제 실행했다. `s5-local-question-native-battery-v4-20260914/attempt-001`은 R001~R009의 독립 답변9개를 저장하고, R010의 시작 전 메모리 검사에서 중단됐다. 원래 실행은 **failed, 예정64·완료9·native9·completion9**로 보존한다. 완료된9개를 성공한 전체64개 실행으로 바꾸거나 다시 생성하지 않는다.

[실제 중단 감사](../../../.training/verifications/question-battery-partial-stop-audit-20260914.json)는 원래 실행과 각 닫힌 context의 파일 해시, 실제 소유 프로세스 종료, 전원·자원 관측을 기록한다. R010에는 input-packet·plan·preflight·summary의4파일만 있고 native·HTTP·completion intent·draft는 없다. 이 context의 `nativeStopped:true`는 생성된 프로세스가 없다는 값이며 실제 종료 영수증으로 해석하지 않는다. 원래9개는 각각 retained handle을 통한 종료를 확인했고, 실행 전체의 임시 전원 요청도 해제됐다.

R010의 2026-09-14 08:28:34.940132 UTC 첫 관측에서 availablePhysicalBytes는9,633,255,424였다. 기존9GiB 기준9,663,676,416보다30,420,992바이트 부족했다. 08:28:39.328631 UTC 두 번째 관측은10,260,705,280바이트로 회복됐다. 두 관측의 AC0·배터리72%는 승인된 전원 조건을 충족했다. 이 기록은 당시 메모리 관측이며 특정 사용자 앱이나 OS 내부 원인을 확정하지 않는다.

## 생성 전 메모리 회복 대기

메모리 시작 기준9GiB physical/commit과 실행 중1GiB 여유, CPU4·BelowNormal·단일 소유 native·6GiB 관측 상한, 시간 제한과 배터리20% 초과 조건을 유지한다. 원래9GiB 기준을 낮추지 않는다. 새 coordinator는 **native를 만들기 전에** 메모리만 일시 부족하면 최대300초 동안 회복을 관측한다. 관측 묶음 사이 대기는5초이며 원래 간격3초 이상의 두 사전 관측이 모두 통과해야 실제 context를 시작한다. 모든 관측과 통과·중단 이유를 별도 admission 기록에 보존한다.

대기 허용 사유는 시작 physical/commit 부족 두 코드뿐이다. 배터리20% 이하·미확인, 전원 정보 오류, 다른 모델과의 경합 및 그 밖의 오류를 메모리 대기로 숨기지 않는다. 대기 중에도 전원 조건과 전체 실행 제한을 적용한다. 300초 안에 회복하지 않으면 새 native를 만들지 않고 중단한다. 사용자 앱·프로세스를 종료하거나 전원 설정을 바꾸지 않는다.

coordinator의 대기 후에는 동결된 v4의 실제 context 생성 함수가 원래 사전검사를 다시 수행한다. 이 검사나 이후 실제 실행에서 실패하면 원래 context 파일을 보존하고 중단한다. 자동 모델 재호출이나 답변 형식·품질 재생성은 없다. 메모리 회복을 기다리는 읽기 작업을 completion 재시도로 세거나 실제 모델 호출로 기록하지 않는다.

## 원9개와 새55개의 정체성

새 coordinator는 `input-execution-v1-local-question-runner-partial-recovery-v5`이며, 새 실행 경로는 `.training/quality-evaluation/input-preparation-v1/s5-local-question-native-partial-recovery-v5-20260914/attempt-001/`이다. 별도 `.local-question-native-partial-recovery-v5-generation.claim.json`을 사용한다. 원래 v4 실행·claim, v3 차단 표시, 더 이전0회 호출 실패와 모든 동결 파일을 유지한다.

새 실행의 대상은 **R010~R064 정확55개**다. 원래 한국어 export64개 파일과 모델·runtime·Python·prompt·token·sampling·최대 분량은 그대로다. 각 새 native에는 해당 번역문과 두 질문만 제공하며 이전 질문·답변·원문·정답·구성·다른 후보를 전달하지 않는다. 실제 context 생성 함수와 context producer version은 **v4**이고, 새로운 coordinator version v5와 구분한다. 새 실행을55개 대신64개 생성으로 기록하지 않는다.

복구 검증기는 원래 완료9개의 모든 원시 요청·응답·prompt/token 대조·완료 조건·자원 관측·실제 소유 종료를 기존 v4와 같은 기준으로 재검증한다. 원래 failed run의 닫힌 전체 파일 목록, R010의 정확한0회 호출 상태와4파일, 원래 claim/차단 표시도 검증한다. 과거에 남아 있던 실패 파일을 덮거나0회라는 요약값만으로 재실행을 허용하지 않는다.

새55개가 완료되면 원9개와 새55개를 **별도 논리 묶음64개**로 검증한다. `sourceRuns`와 각 context의 출처는 원래 failed9 실행과 새 completed55 실행의 실제 경로·계획·summary 해시를 유지한다. 논리 묶음은 원래 run summary를 수정하지 않는다. reviewId64개·native UUID64개·PID/생성시각 정체성·completion64회의 중복/누락과 실행 수명 겹침을 거부한다. 완료된9개 중 하나라도 변조되거나 원래 R010에 native/HTTP/호출 증거가 발견되면 이 복구 계약을 적용하지 않는다.

## 동결과 이후 평가

[부분 복구 동결](local-question-partial-recovery-freeze-v5.json)은 원래 [배터리 동결](local-question-battery-execution-freeze-v4.json), 원래 failed9 실행·claim·차단 표시·감사, retained9/pending55 ID, 새 경로, 메모리 대기 정책과 새 코드·검사·이 문서9파일을 묶는다. `actualNewQuestionCallsAtFreeze:0`은 새55개 실행을 아직 시작하지 않았다는 뜻이며, 이미 완료된 원9회의 실제 호출을0으로 바꾸는 값이 아니다.

수집·답변 동결·최종 보고·원장 관측에는 `partialRecovery`와 실제 `sourceRuns`, context별 출처를 연결한다. 이전 `batteryExecution`, `zeroCallRecovery`, `inventoryCorrection`도 유지한다. **9개만으로 질문 채점·후보 선택을 시작하지 않는다.** 실제64개 검증과 전체 답변 동결을 마친 뒤 원문에 근거해128개 질문을 채점한다. 도우미 독해 평가이며 사람 검수·학습 효과로 보고하지 않는다.

S5의 고정 상대 개선·일반 회귀·보류·후보 선택 기준과 조건부 S6 독립80개 및 최종 수용 기준을 유지한다. 앱 등록·운영 DB·개인 기록은 이 실행 복구로 변경하지 않는다. 새55개가 미완료이거나 전체64개 동결에 실패하면 현재9개의 존재를 S5 완료로 보고하지 않는다.
