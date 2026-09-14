# 질문 생성 0회 중단의 복구 계약 v3

사용자의 2026-09-14 후속 지시에 따라 S5 독립 질문 평가를 이어가기 위한 별도 실행 경로다. [현재 중단 기록](QUESTION_POWER_STOP_20260914.md), [0회 중단의 별도 검토](../../../.training/verifications/question-zero-call-recovery-review-20260914.md), 원래 [질문 평가 절차 v1](LOCAL_QUESTION_REVIEW_PROTOCOL_V1.md) 및 [설치 목록 교정 v2](LOCAL_QUESTION_INVENTORY_CORRECTION_V2.md)를 보존한다. 이 문서 작성과 코드 검사는 실제 질문 평가 완료를 뜻하지 않는다.

## 범위와 이전 실패

허용하는 이전 시도는 정확히 `.training/quality-evaluation/input-preparation-v1/s5-local-question-native-inventory-v2-20260914/attempt-001/`이며 상태는 `failed`로 남긴다. 실제 폴더의 전체 목록은 `plan.json`, `model-chat-template.jinja`, `summary.json` 3파일이어야 한다. context·process·HTTP·completion intent·답변 파일이 있거나 요청 여부가 불명확하면 이 경로는 사용할 수 없다.

- summary SHA256: `fc1978fdd1bd7935314ae0c01f259c610446a00b9712a6f664b6f098bd713348`
- plan SHA256: `a6a9cbb6d6d8bded97b486e609d5f9244a293f37839dee7bd8c5b46828ab407f`
- 템플릿 SHA256: `7f0e529032c25183bcd66c7f238da2d377f43be754a94e2725a58c4e16d2ed67`
- 원래 `.local-question-native-v1-generation.claim.json` SHA256: `2eb1e66d411970a9a55cc52344261dc51bffa9c50851ed51606b1b830d9ec019`
- 별도 0회 검토 SHA256: `77c9c6573920528c093df95272e1f960875e647c303bf84151f78094af5e019f`
- 원래 실행16파일 동결 SHA256: `fe4f62dd806d698450373bd9bc159e94b361004066d02af08af9031b3ef7ba68`
- 설치 교정9파일 동결 SHA256: `7e9524ac1d1150dece0448b508f6743bf551b9bba8483c62b855152590b4a8a1`

이전 완료 context·native 생성·completion 요청은 각각 정수 0이다. 원래 claim은 실행 예약이 존재했다는 기록이며 모델 요청의 증거로 세지 않는다. 원래 summary는 `ResourceGuardError`까지만 기록했으므로 이후 AC0 관측을 이전 실패 순간의 상세 오류로 소급해 쓰지 않는다. 이전 native 종료 여부의 빈 집합 표현도 실제 프로세스 종료 증거로 해석하지 않는다.

## 정체성·재개 제한

새 실행기는 `local_question_runner_recovery_v3.py`이고 실행 버전은 `input-execution-v1-local-question-runner-zero-call-recovery-v3`이다. 유일한 실제 출력 경로는 `.training/quality-evaluation/input-preparation-v1/s5-local-question-native-recovery-v3-20260914/attempt-001/`이다. 기존 run과 claim을 삭제·이동·수정하지 않으며 별도의 영구 v3 claim으로 중복 실행을 막는다. 일반적인 `ignore-claim`·재시도 옵션이나 품질 개선을 위한 재생성은 제공하지 않는다.

이 문서와 runner/transport/evaluation/append 코드 및 각각의 테스트, 총 9파일을 [재개 동결](local-question-zero-call-recovery-freeze-v3.json)에 기록한다. 동결은 이전 run3파일·원래 claim·검토·16파일 및 교정9파일 동결과 실제 질문 호출0을 함께 바인딩한다. `validate_prior(root)`와 `load_recovery(root)`는 정확한 파일 목록과 해시를 재검증한다. 이전 실패나 동결 파일이 바뀌면 실행과 결과 수집을 거부한다.

`zeroCallRecovery`는 이전 실패 참조·새 동결·9파일 정체성을 plan/summary, collection, 답변 동결, 평가 보고서와 원장 관측까지 연결한다. `inventoryCorrection`도 유지한다. 새 coordinator의 버전과 실제 개별 context를 생성하는 frozen v1의 `contextProducerVersion`을 구분한다. 동결 모듈의 전역 변수나 함수를 monkeypatch하지 않는다. 원래 순수 판정·변환·집계·단일 native 실행 함수는 그대로 재사용한다.

## 전원·실행 순서

전원 연결 여부와 자원 관측은 현재 상태를 새로 확인해야 한다. 공유 mutex를 획득하고 AC1·충분한 physical/commit·분류된 모델 경합 없음의 사전검사와 임시 전원 요청을 통과한 뒤 새 실행을 시작한다. 사전조건을 충족하지 못한 시도는 0회 관측으로 기록하고 새 run/영구 claim을 소비하지 않는다. 실제 생성 시작 뒤에는 실패·미확인 요청을 남기며 자동 재시도하지 않는다.

원래 CPU4·BelowNormal·메모리 상한·단일 소유 native·시작/요청/전체 시간 제한, 실행 중 전원 및 자원 감시, retained handle에 의한 종료를 유지한다. context마다 다시 두 차례 사전검사를 수행한다. 읽기 전용 사전검사의 통과만으로 이후 실제 실행의 전원 상태를 보장하지 않는다. 오류 코드는 알려진 기술적 식별값을 보존하며 프롬프트·개인 내용·비밀값을 오류 메시지로 복사하지 않는다.

기존 `.training/quality-evaluation/input-preparation-v1/s5-local-question-export-20260914/`의 64개 packet과 manifest를 그대로 사용한다. 모델·runtime·Python·템플릿·sampling·JSON 문법·질문·증거 인용·입출력 토큰 규칙은 바꾸지 않는다. 각 packet의 한국어 번역과 질문만 새 Qwen native 프로세스에 전달하고 단 한 번 completion 후 종료한다. 실제 template/token 대조가 완료되기 전 completion을 보내지 않으며 `cache_n=0`과 원시 요청/응답을 검증한다. 이전 답변 재사용은 0개다.

## 검증·평가 순서

합성 검사는 정확한 이전0회 상태, 추가/변조 파일과 기존 요청 거부, 두 claim의 소속·중복, AC/자원 실패 시 생성0, 새 lineage 변조 거부, 원래 실행·재생 검증의 유지, 답변 수집/동결/원장 중복 보존을 확인한다. 합성 context를 실제 모델 실행이나 실제 질문 답변으로 기록하지 않는다.

실제 실행이 끝나면 새 프로세스64개·각 completion1회·원시 결과·모델/코드/자료 최종 해시·모든 소유 프로세스 종료와 전원 요청 해제를 확인한다. 그 후 `local_question_transport_recovery_v3.py`가 동일64개를 수집하고, `local_question_evaluation_recovery_v3.py`가 **전체 답변을 먼저 동결**한다. 원문 평가자는 동결 뒤128질문을 채점하며 root/원문 검토자가 독립 답변을 대신 작성하지 않는다. 사람 검수·일반 정확도 인증으로 표시하지 않는다.

S5의 원래 상대 개선·회귀·보류·후보 선택 기준과 S6의 조건부 독립80단위 평가는 유지한다. `local_question_append_evidence_recovery_v3.py`는 실제 최종 증거를 모두 검증하고 별도 `--append` 요청이 있어야 원장을 갱신한다. 기존836사건/번역검토146개를 보존하며 반복 추가는0이어야 한다. 이 복구는 운영 DB·개인 기록·웹 worker·원문 버전·앱 제공자 등록을 바꾸지 않는다.

아래 명령은 새 재개9파일 동결 및 현재 사전조건 통과 후 root가 실행한다. 기존 inventory-v2 실행기를 다시 호출하지 않는다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_runner_recovery_v3.py preflight --destination .training/verifications/question-recovery-preflight-20260914.json
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_runner_recovery_v3.py run --export .training/quality-evaluation/input-preparation-v1/s5-local-question-export-20260914 --destination .training/quality-evaluation/input-preparation-v1/s5-local-question-native-recovery-v3-20260914/attempt-001
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_transport_recovery_v3.py collect --formal <formal-S5> --export <same-question-export> --run <completed-v3-run> --destination <new-collection>
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_evaluation_recovery_v3.py freeze-answers --folder <formal-S5> --collection <new-collection>
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_evaluation_recovery_v3.py report --folder <formal-S5> --source-reviews <confirmed-source-reviews> --grades <separate-grades-after-answer-freeze> --output <new-report>
```

`preflight`는 질문 export의 내용을 읽지 않고 이전 실패·코드·설치·Python·GGUF metadata와 현재 자원 관측을 확인한다. native·completion·임시 전원 요청을 만들지 않으며 새 audit 파일에 결과를 저장한다. 실패한 관측은 보존하고 같은 파일을 덮어쓰지 않는다. `run`은 이 audit을 실행 허가로 재사용하지 않고 실제 시작 조건을 다시 검사한다.
