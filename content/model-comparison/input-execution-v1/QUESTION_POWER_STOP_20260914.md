# 독립 질문 평가 시작 중단 — 2026-09-14

**최신 S5 상태 — 질문9/64개 실제 저장, 남은55개 부분 복구 준비.** 배터리 v4는9개의 단일 호출과 소유 프로세스 종료를 마친 뒤 R010의 시작 메모리 검사에서 중단됐다. 배터리는72%였고, 첫 관측의 여유 메모리가9GiB보다30,420,992바이트 부족했다가 두 번째 관측에서 회복됐다. [부분 복구 v5](LOCAL_QUESTION_PARTIAL_RECOVERY_V5.md)는 원9개와 원래 failed 실행·claim을 보존하며 R010~R064만 새로 생성한다. 시작 메모리 기준을 유지하고 native 생성 전 최대300초 회복을 기다린다. 현재 복구 구현·검증을 준비 중이며, 전체64개 답변 동결·질문 채점·후보 선택·S6은 아직 미완료다. [실제 중단 감사](../../../.training/verifications/question-battery-partial-stop-audit-20260914.json)를 기준으로 이어간다.

> 최신 사용자 지시: 충전기 없이 배터리로 진행한다. [배터리 실행 v4](LOCAL_QUESTION_BATTERY_EXECUTION_V4.md)로 이어가며 알려진 잔량20% 초과와 기존 메모리/프로세스 보호를 적용한다. 17:10 KST 배터리80% 사전검사 후 v4 실제 질문 실행을 시작했다. 전체64개 완료·답변 동결·채점은 아직 미완료다. 아래 AC 전용 중단과 v3는 이전 이력이다.


S4의 실제 번역 64개, 정식 S5 packet 64개, 원문 검토 64개와 관계 진단 64개는 완료했다. 아래 중단 시점의 독립 질문 답변은0개였다. 후속 v4의 질문 채점·후보 선택·S6은 아직 미완료다. 실패 기록과 기존 앱 등록을 유지한다.

## 설치 목록 교정과 검사

최초 질문 실행은 native 생성 전 `runtime_52_file_inventory`에서 실패했다. 고정 설치 manifest와 실제 runtime 디렉터리는 모두 51파일이고 모델 1파일을 합쳐 총 52파일이다. 실제 51개 runtime 및 모델 전체 해시를 대조했고 설치를 변경하지 않았다. [실패 감사](../../../.training/verifications/question-runtime-inventory-failure-audit-20260914.json)의 SHA256은 `a6a8ac0117da40621fba5a53adf73217d75138af48879eb6ee08e8e6f73e4936`이다.

원래 [질문 실행 동결 16파일](local-question-execution-freeze-v1.json)을 보존하고, [명시적인 설치 목록 교정](LOCAL_QUESTION_INVENTORY_CORRECTION_V2.md)의 코드·검사·설명 9파일을 [별도 동결](local-question-inventory-correction-freeze-v2.json)했다. 교정 동결 SHA256은 `7e9524ac1d1150dece0448b508f6743bf551b9bba8483c62b855152590b4a8a1`이다. 원문·질문 export·모델·생성 설정·격리 방식·평가 기준은 바꾸지 않았다.

교정 검사들은 서로 별도 실행으로 runner 13개, transport 17개, evaluation 24개, append 17개가 통과했다. 평가 테스트의 첫 실행에서 거부 지점이 앞당겨져 기대 오류명 1건이 실패한 이력과 수정 후 전체 24개 통과를 함께 보존했다. 합성 검사는 실제 질문 답변이나 품질 검증을 뜻하지 않는다. [runner 검사 기록](../../../.training/verifications/local-question-inventory-adapter-implementation-20260914.json), [평가 연결 검사 기록](../../../.training/verifications/local-question-inventory-pipeline-implementation-20260914.json), [동결 확인](../../../.training/verifications/question-inventory-correction-freeze-verification-20260914.json)에 해시를 연결했다.

## 실제 두 번째 시작의 상태

실제 시도는 `.training/quality-evaluation/input-preparation-v1/s5-local-question-native-inventory-v2-20260914/attempt-001/`이다. `2026-09-14T02:31:19Z`에 실패했으며 다음 상태를 보존한다.

- 설치·기존 질문 export·코드 검증 후 실행 계획과 generation claim을 저장했다.
- `PowerRequest` 진입이 반환되기 전에 `ResourceGuardError`가 발생했다. 원래 summary에는 상세 오류 코드가 남지 않았으므로 그 기록을 고치지 않는다.
- 실행 폴더는 계획·템플릿·summary 3파일뿐이며 context 디렉터리, native 생성 기록, HTTP 요청, completion intent가 없다. 이 코드 경로에서 native 생성·질문 호출·답변은 각각 0이다.
- summary의 `nativeStopped:true`는 빈 context 집합의 `all()` 결과다. 실제 native를 생성해 종료했다는 증거가 아니다. `powerRequest:null`도 요청 해제 영수증이 아니다.
- 이후 02:32:36 UTC 관측과 02:34:42/46 UTC의 두 번 대조에서 `ACLineStatus=0`, 실제 `PowerRequest`의 `ac_power_required`를 확인했다. 후속 관측은 시작을 막는 현재 전원 조건을 입증하며 이전 실패 순간의 세부 원인을 직접 기록한 것은 아니다.
- 후속 두 관측의 가용 physical 약 12.72 GiB, commit 약 19.17/19.19 GiB였고 native/분류된 모델 경합은 0이었다. 전원 요구는 완화하지 않는다.

[실제 중단 감사](../../../.training/verifications/question-power-stop-audit-20260914.json)의 SHA256은 `c7d2b7f44c7c94a13d31c97f3db1c90bfd809fdea10841a1add69fdb97625108`이다. [별도 도우미의 0회 중단·재개 검토](../../../.training/verifications/question-zero-call-recovery-review-20260914.md)도 파일 목록·실행 경계를 대조했다. 기존 실패 폴더·claim·16+9 동결 파일·질문 export를 변경하지 않았다.

## 재개 순서

1. PC를 AC 전원에 연결한다. 모델 실행 직전에도 전원·메모리·경합을 새로 두 번 검사한다.
2. 원래 generation claim은 영구 재생성 방지 기록이므로 삭제·이동하거나 기존 producer를 우회 재실행하지 않는다. 원래 실패·정확한 0회 호출 근거·동일한 export와 모델을 참조하는 별도 zero-call 재개 계약과 새 코드/run 정체성을 먼저 작성·검사·동결해야 한다. 기존 claim의 존재만으로 모델 요청을 보냈다고 세지는 않는다.
3. 검증된 재개 경로에서 동일한 64 packet을 각각 새 native 프로세스의 단일 completion으로 답한다. 전체 64개 답변을 수집·검증·동결한 뒤 다른 원문 평가자가 128질문을 채점한다. [채점 준비 메모](../../../.training/verifications/question-grading-procedure-20260914.md)는 코드 schema 안내이며 실제 점수가 아니다.
4. 동결한 S5 기준으로 후보를 판정하고 같은 실행·원문·질문·관계 증거를 원장에 연결한다. 적격 후보가 있어야 S6의 독립 80단위 평가로 진행한다. 미평가를 후보 탈락이나 통과로 바꾸지 않는다.

운영 DB·원문 버전·개인 기록·활성 번역 등록을 바꾸지 않았다. 기존 원장 836사건과 번역 검토 146개에 이번 S5 결과를 아직 추가하지 않았다. 실제 앱 build/E2E 또는 HTML 3문단·PDF 1페이지의 생성·저장·캐시 검증은 이번 작업에서 실행하지 않았다.

## 후속 재개 준비 완료

사용자의 후속 지시로 [복구 v3](LOCAL_QUESTION_ZERO_CALL_RECOVERY_V3.md)의 실행·수집·동결·평가·원장 연결 코드를 구현하고 합성 검사를 마쳤다. [새9파일 동결](local-question-zero-call-recovery-freeze-v3.json)은 기존 실패3파일/claim과 원래16+교정9파일을 보존한다. 기존 claim 삭제나 반복 생성은 없다. 전원·자원 조건에 실패한 사전검사는 별도 audit을 만들고 새 run/claim을 소비하지 않는다.

[실행기 검사19개](../../../.training/verifications/local-question-zero-call-recovery-runner-implementation-20260914.json)와 [평가 연결 검사26개](../../../.training/verifications/local-question-zero-call-recovery-pipeline-implementation-20260914.json)를 각각 통과했다. 평가 연결은 transport8+추가1, evaluation9+추가1, append7의 별도 실행이며45개 전체를 한 번에 실행했다고 주장하지 않는다. 처음 AST 비교 테스트의 fixture 결함과 수정 후 실행기19개 통과도 기록했다. 이는 새 코드의 합성 검증이고 실제 QA/앱 검증이 아니다.

[실제 v3 사전검사](../../../.training/verifications/question-recovery-preflight-20260914.json)는 모델·runtime 전체 해시, Python 정체성, GGUF metadata를 확인한 뒤 두 번 모두 AC0/ac_power_required를 기록했다. native·completion·PowerRequest·새 run/claim 생성은0이다. 전원 연결 후 v3의 실제 run 명령으로 이어가되 시작 조건은 그때 다시 검사한다. S5 질문 답변0/64·채점과 S6은 여전히 미완료다.
