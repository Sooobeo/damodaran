# 공통 입력 처리 S4~S6 실행

**최신 S5 상태 — 질문9/64개 실제 저장, 남은55개 부분 복구 준비.** 배터리 v4는9개의 단일 호출과 소유 프로세스 종료를 마친 뒤 R010의 시작 메모리 검사에서 중단됐다. 배터리는72%였고, 첫 관측의 여유 메모리가9GiB보다30,420,992바이트 부족했다가 두 번째 관측에서 회복됐다. [부분 복구 v5](LOCAL_QUESTION_PARTIAL_RECOVERY_V5.md)는 원9개와 원래 failed 실행·claim을 보존하며 R010~R064만 새로 생성한다. 시작 메모리 기준을 유지하고 native 생성 전 최대300초 회복을 기다린다. 현재 복구 구현·검증을 준비 중이며, 전체64개 답변 동결·질문 채점·후보 선택·S6은 아직 미완료다. [실제 중단 감사](../../../.training/verifications/question-battery-partial-stop-audit-20260914.json)를 기준으로 이어간다.

> 최신 사용자 지시: 충전기 없이 배터리로 진행한다. [배터리 실행 v4](LOCAL_QUESTION_BATTERY_EXECUTION_V4.md)로 이어가며 알려진 잔량20% 초과와 기존 메모리/프로세스 보호를 적용한다. 17:10 KST 배터리80% 사전검사 후 v4 실제 질문 실행을 시작했다. 전체64개 완료·답변 동결·채점은 아직 미완료다. 아래 AC 전용 중단과 v3는 이전 이력이다.


사용자의 2026-09-13 지시에 따라 [인수인계 28절](../../../TRANSLATION_HANDOFF_20260911.md#28-공통-입력-처리와-의미-관계-검사-개선-실행-계획)의 S4부터 조건부 S6까지 진행한다. [S1 계약](../input-preparation-v1/CONTRACT.md)과 [S2/S3 결과](../input-preparation-v1-s2s3/README.md)는 보존한다.

## 실행 계약

별도 [생성기](../../../scripts/model-comparison/input_execution_v1/producer.py)는 S2 attempt-002의 16단위×4구성 입력을 고정 순서로 한 번씩 생성한다. 원문·문맥·사전·실제 prompt token ID, Hy7 가중치와 native runtime, CPU4와 생성 설정을 실행 계획의 파일 해시에 연결한다. 실제 서버의 `/apply-template`와 `/tokenize`에서 전체64개 입력이 일치해야 첫 `/completion`을 보낸다. 같은 prompt 문자열이라도 구성의 처리 정체성이 다르면 출력을 재사용하지 않는다.

[자원 프로필](../../../scripts/model-comparison/input_execution_v1/PROFILE.md)은 실행 전 AC와 physical/commit 여유를 두 번 검사하고, Windows 소유 Job·suspended spawn·hard working set·BelowNormal을 확인한 뒤 native를 재개한다. 실행 중에는 자원·전원·경합·시간을 감시한다. 소유 native의 종료를 확인한 뒤 임시 절전 방지 요청과 실험 mutex를 해제한다. 운영 worker는 이 실험 mutex에 참여하지 않으므로 경합 검사의 관측 간격 중 다른 실행이 시작될 가능성은 남는다.

요청 전 intent와 원시 HTTP 응답 bytes를 별도 파일에 기록한다. 기술 오류·부분 응답·미완료를 보존하며 자동 재시도하지 않는다. 숫자·통화 검사 경고가 있는 완성 출력도 그대로 평가한다. 원시 생성 문구에 `.strip()`이나 용어 후처리를 적용하지 않는다. 전체 종료·최종 입력 및 출력 무결성 검사가 통과하기 전에는 정식 S5 평가 packet을 만들지 않는다. 생성 중 이미 완료된 출력은 익명 잠정 원문 검토에만 사용할 수 있고, 전체64개 확인 뒤 정식 packet과 해시가 같아야 확정한다. 잠정 초안으로 입력·순서·중단 조건을 바꾸거나 점수·후보를 선택하지 않는다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 -m unittest discover -s scripts/model-comparison/input_execution_v1 -p 'test_*.py' -t scripts/model-comparison -q
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/producer.py --run
```

두 번째 명령은 실제 모델 호출이다. 이미 생성 요청을 보낸 run이 있으면 이 v1 생성기는 다시 실행하지 않는다. 기술 실패를 수정해 이어야 할 때는 원래 실패·미완료·완료 응답을 보존하는 별도 복구 계약과 새 코드/run 정체성을 먼저 작성해야 한다. 임의로 run 폴더를 지우거나 완료 출력을 재생성하지 않는다.

## 전원 중단 후 복구 경로

최초 실행이 실패했으므로 실제 후속 작업은 [복구 계약](RECOVERY_V1.md)의 새 [복구 생성기](../../../scripts/model-comparison/input_execution_v1/recovery_producer.py)를 사용한다. 원래38개는 raw response와 당시 실행 정체성을 그대로 보존한다. 2026-09-14 AC1·메모리·경합 사전검사를 다시 확인했으며, 실제 시작 직전에도 같은 검사를 반복한다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_producer.py --run
```

복구 생성 요청이 이미 존재하면 이 명령도 재실행을 거부한다. 종료 후 완료26개·소유 종료·최종 무결성이 확인된 경우에만 [cohort v2 검증기](../../../scripts/model-comparison/input_execution_v1/recovery_cohort_v2.py)의 `build`로 최초38개와 새26개를 연결한다. [v2 수정 계약](RECOVERY_COHORT_VALIDATION_V2.md)은 실행마다 달라지는 로컬 포트와 그 포트에 정확히 대응하는 CORS origin만 함께 비교 정규화한다. 원래 실행·v1 코드·응답과 나머지 실행 인자는 보존한다. 원래 실패 실행을64개 성공으로 고치지 않으며 총65회 요청 중1회는 원래 응답 미확보 상태로 남긴다.

정식 packet 준비와 잠정 원문 검토의 바이트 대조는 [복구 평가 v2 안내](../../../scripts/model-comparison/input_execution_v1/RECOVERY_EVALUATION_V2.md)의 `recovery_evaluation_v2.py prepare/verify-promotion/verify-recovery-provisional`을 따른다. [복구 관계 진단 v2](../../../scripts/model-comparison/input_execution_v1/recovery_relations_v2.py)는 `--cohort`와 새 `--destination`을 받아 같은 S3 검사기를 실행한다. v2의13개 코드·검사·설명 파일은 [별도 동결](recovery-evaluation-freeze-v2.json)에 기록했다. 최초 단일 실행용 경로에 혼합 실행을 넣지 않는다.

새 질문 에이전트 생성 한도로 인해 질문 평가에는 [로컬 독립 질문 절차 v1](LOCAL_QUESTION_REVIEW_PROTOCOL_V1.md)을 추가했다. 설치된 Qwen3.5-9B Q4_K_M을 packet마다 완전히 새 소유 native 프로세스로 실행하고 단 한 번 답변한 뒤 종료한다. 모든64개에 같은 모델·설정을 적용하고 실제 `cache_n=0`을 확인한다. 컨트롤러가 정식 packet에서 한국어 번역·질문만 내보내며, runner는 원문·정답·평가 bundle을 읽지 않는다. 이 방법을 실제 에이전트 spawn이나 사람 검수로 표시하지 않는다. 기존 `local_question_*_v1.py`와 spawn용 절차를 보존한다. 실제 설치 목록 교정은 [별도 inventory v2 경로](LOCAL_QUESTION_INVENTORY_CORRECTION_V2.md)로 동결했고 질문 export는 그대로다. 실제 질문 시작은 native/호출0회에서 중단됐으며 현재 AC 전원이 필요하다. [중단 기록](QUESTION_POWER_STOP_20260914.md)의 원래 claim을 보존하는 [복구 v3](LOCAL_QUESTION_ZERO_CALL_RECOVERY_V3.md)를 구현·검사·동결했다. 실제 생성에는 이 v3만 사용하며 기존 실행기를 다시 호출하지 않는다. 새 실제 preflight는 AC0으로 실패했고 새 run/claim과 native/질문 호출은0이다. 코드 검사 통과와 실제64개 질문 완료는 별도 상태다.

## 평가 순서

[평가 도구 안내](../../../scripts/model-comparison/input_execution_v1/EVALUATION.md)에 실제 JSON 계약과 명령을 기록한다. 개발 원문64검토, 핵심 명제192개, 용어 출현156개와 질문128개(핵심64개)의 분모를 유지한다. 질문은 단위×구성마다 별도 새 답변자64명에게 한국어 번역과 질문2개만 제공한다. 모든 답변을 먼저 동결하고 다른 평가자가 원문과 대조한다. root의 원문 검토를 독립 질문 답변으로 대체하지 않는다. 모든 검토는 도우미 평가이며 사람 검수·실제 학습 효과를 뜻하지 않는다.

[원래 관계 진단 도구](../../../scripts/model-comparison/input_execution_v1/relations.py)는 단일 run의 완료64개를 요구한다. 현재 실패한 attempt-001에는 아래 원래 명령을 실행하지 않는다. 복구 cohort를 검사하는 새 경로가 두 run의 실제 증거를 검증한 뒤 같은 S3 v2를 적용해야 한다. 구성별16출력×6관계 상태를 유지하며 경고·미지원·보류와 원문 의미 판정은 별도다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/relations.py --outputs .training/comparisons/input-preparation-v1/s4-generation/attempt-001/predictions.jsonl --destination .training/quality-evaluation/input-preparation-v1/s4-relations-20260913-v1
```

S6은 개발 개선과 일반 회귀 없음이 확인된 후보만 선택·동결한다. 적격 후보가 있으면 S1에 고정한 미노출80단위/C0+후보 최대160출력의 독립 평가를 진행한다. 적격 후보가 없으면 기존 등록 유지가 S6의 결론이다. 독립 평가와 모든 수용 기준을 통과한 경우에만 새 등록·격리 앱 HTML3문단/PDF1페이지 생성·저장·캐시 검증으로 진행한다.

## 현재 기록

- 실제 실행: `.training/comparisons/input-preparation-v1/s4-generation/attempt-001/`.
- 최초 실행에서 S2의64개 서버 template/token 대조 후38개를 저장하고39번째 요청에서 응답을 확보하지 못했다. AC 감시 중단으로 status=failed, 미완료26개이며 소유 native 종료·전원 요청 해제·입력 무결성을 확인했다. [복구 계약](RECOVERY_V1.md)과 [원래 실행 정체성](RECOVERY_PRIOR.json)을 보존하고 새 producer로26개만 이어간다. 9월14일 새 실행에서26개를 모두 저장하고 소유 종료·전원 요청 해제·최종 무결성을 확인했다. 복구 elapsed3,072.766초다. `s4-cohort-v2-20260914`의64개 이력 검증·정식 `s5-formal-v2-20260914` 준비·원문 검토64개 채택·관계 진단64개를 완료했다. [독립 질문 실행16파일](local-question-execution-freeze-v1.json)과 [설치 목록 교정9파일](local-question-inventory-correction-freeze-v2.json)을 고정했다. 실제 질문 시작은 PowerRequest 진입에서 실패하여 호출0회이며, 후속 AC0 관측과 [재개 조건](QUESTION_POWER_STOP_20260914.md)을 기록했다. 실제 질문/채점·후보 선택·S6은 미완료다.
- S4 실행은 운영 DB·원문 버전·개인 기록·활성 등록을 변경하지 않는다. 선택형 API와 가중치 학습은 실행하지 않는다.
- 이번 개발16단위의 새 사전 금융 힌트는0개다. C2/C3의 차이를 새 금융 뜻 선택의 품질 입증으로 해석하지 않는다.
