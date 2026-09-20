# S5 질문 부분 복구 — 물리 메모리 시작 정책 v6

2026-09-20 사용자가 “그냥 지금 시작하면 안됨?”이라고 요청했다. 현재 자원으로 남은 작업을 시작하라는 취지로 해석하며, 도우미는 과거 실행 증거를 확인한 뒤 **시작 physical 기준만 9GiB에서 7GiB로 조정**한다. 사용자가 7GiB라는 숫자를 직접 지정한 것은 아니다. 기존 v4/v5 계약과 실패 기록은 보존하고 새로운 실행 정체성으로 진행한다.

## 근거와 변경 범위

원래 R001~R009의 관측 1,975개에서 native peak working set 최대는 5,633,363,968바이트(약5.246GiB), private 최대는 780,705,792바이트(약0.727GiB)였다. 각 실행은 시작 전에 hard working set 6GiB−64MiB를 적용하고 실제 설정값을 재조회했다. 당시 실행 중 최소 가용 physical은 3,677,241,344바이트였다. 뒤의55개 입력에 대한 완료 보장은 아니다.

2026-09-20 08:49:07 KST에는 가용 physical 7,908,782,080바이트(약7.366GiB), commit 13,527,789,568바이트, AC연결·배터리100%, 분류된 모델/worker 경합0개를 관측했다. 9GiB는 모델 자체의 필수 용량보다 여유를 둔 실행 정책이다. 다만 원래 R009의 시스템 전체 가용 physical 감소는 약6.632GiB여서 같은 변동이 반복되면 새 실행이 중단될 수 있다. 모델·다른 프로세스·OS 캐시의 기여를 확정하지 않는다.

| 항목 | v6 정책 |
| --- | --- |
| 시작 가용 physical | **7GiB = 7,516,192,768바이트** |
| 시작 가용 system commit | 기존 **9GiB = 9,663,676,416바이트** |
| native hard working-set 요청 | 기존 **6GiB−64MiB = 6,375,342,080바이트** |
| 관측 working set·peak·private 상한 | 기존 **6GiB = 6,442,450,944바이트** |
| 실행 중 가용 physical/commit | 기존 **각1GiB 이상** |
| 실행·감시 | 기존 CPU4·BelowNormal·한 모델·0.5초 자원 감시·5초 경합 검사 |
| 시간·전원 | 기존 시작/요청 각600초·전체12시간·배터리20% 초과·임시 전원 요청 관리 |
| 생성 전 회복 대기 | 기존 최대300초·관측 쌍 간격3초 이상·재관측5초 |

PROFILE 차이는 `minimumStartPhysicalBytes` 한 필드뿐이어야 한다. 모델·runtime·Python·prompt·sampling·seed·문맥/출력 token·질문·정답 격리·단일 completion은 변경하지 않는다. 새 시작 기준의 완충분은 작으므로 메모리 부족·페이지 교체·시간 초과·중도 종료 가능성이 있다. 실행 중1GiB 여유나 기존 상한/전원/경합 조건을 위반하면 소유 native를 종료한다. 관측 간격 사이의 모든 변동까지 막는다는 보장은 하지 않는다.

## 실행 및 기록

기존 v4/v5 코드·동결·실패9 실행·원시 답변은 불변이다. 새 `local_question_context_memory_v6.py`가 새로운 PROFILE에 묶인 실제 context producer이며, `local_question_runner_memory_v6.py`는 원9개를 v5의 기존 검증기로 확인한 뒤 **R010~R064 정확55개**에만 새 native를 생성한다.

- native producer: `input-execution-v1-local-question-context-memory-v6`
- coordinator: `input-execution-v1-local-question-runner-memory-v6`
- 논리 묶음: `input-execution-v1-local-question-memory-cohort-v6`
- run: `.training/quality-evaluation/input-preparation-v1/s5-local-question-native-memory-v6-20260920/attempt-001`
- claim: `.training/quality-evaluation/input-preparation-v1/.local-question-native-memory-v6-generation.claim.json`
- 동결: `local-question-memory-execution-freeze-v6.json`

새 `partialRecovery`에 기존 v5 전체 정체성을 `previousPartialRecovery`로 연결하고 `memoryPolicy`, `originalProfile`, `profile`을 추가한다. v5 새 실행은 아직 미실행이다. v6 진입에서 기존 v5 claim 위치에 별도 차단 표시를 기록해 옛 진입점의 중복 생성을 막는다. 이 표시는 `representsV5Execution:false`이며 v5 모델 호출로 세지 않는다. 사전검사 실패는 새 run/claim·차단 표시를 만들지 않는다. 실제 run 소비 전 임시 전원 요청과 현재 조건을 다시 확인한다.

원9개의 producer는 v4·시작physical9GiB로 유지하고 새55개만 v6·physical7GiB로 기록한다. `sourceRuns`는 원래 failed9와 새 completed55를 구분한다. 논리 summary의 `contextProducerVersion`은 새55개의 producer를 뜻하며, 개별64개 bindings와 provenance는 각 실제 producer·profile·원시 요청/응답·프로세스 수명·출처를 검증한다. 원9개를 v6 실행으로 바꾸거나 원래 failed run을 completed로 바꾸지 않는다.

## 검증과 후속 평가

새 native·coordinator·transport·evaluation·append 모듈 및 각 검사10개 Python 파일과 이 문서, 총11파일을 실제 생성 전에 동결한다. 메모리 경계·기존 guard 유지·원9/새55 혼합 정체성·잘못된 producer/profile 거부·v5 미실행/차단 표시·원시 증거 변조 거부를 검사한다. 합성 검사 통과와 실제55개 생성 완료는 다르다.

실제 서버 prompt/template/token 대조, 새 context cache0·단일 completion·소유 종료를 확인한다. 각 context 정상 종료 뒤 다음 입력으로 진행한다. 실패·잘림·형식 오류·자원 중단은 원시 결과와 함께 보존하며 자동 재호출하지 않는다. 완료 답변을 다시 생성하지 않는다.

전체64개 증거 검증 → memory-v6 collection → 전체 답변 동결 → 원문 기반128개 질문 채점 → 같은 S5 상대 개선/회귀 판정 → 조건부 S6 순서를 유지한다. 질의 모델·품질 기준은 그대로이며 원9/새55의 메모리 시작 조건이 달랐음을 평가와 원장에 남긴다. 의미·질문 결과를 보고 정책을 조절하거나 실패를 숨기지 않는다. 앱 등록·운영 DB·개인 기록은 이 실행 정책 변경으로 수정하지 않는다.
