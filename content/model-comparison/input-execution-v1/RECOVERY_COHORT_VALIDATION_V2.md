# S4 복구 코호트 사후 검증 보완 v2

2026-09-14 복구 실행의 시작 감사에서, 동결된 `recovery_cohort.py`가 `--port`만 정규화하고 해당 포트가 들어간 `--cors-origins`는 그대로 비교하는 결함을 확인했다. 원래 실행과 복구 실행은 서로 다른 임시 로컬 포트를 정상적으로 선택한다. 따라서 서버·모델·입력·제한·샘플링이 같아도 v1 사후 검증기가 이 정상 차이를 `recovery_runtime_command_changed`로 거부한다.

이는 모델 생성이나 원시 응답의 실패가 아니다. 원래 실패 실행과 복구 producer, 실행 plan, 동결된 여섯 실행 파일과 그 manifest는 수정하지 않는다. v1 검증 결과를 성공으로 덮어쓰거나 출력을 재생성하지 않는다. 발견 근거는 별도 `.training/verifications/input-preparation-v1/recovery-start-audit-20260914.json`에 보존한다.

새 `recovery_cohort_v2.py`는 v1의 실패 38개·무응답 요청 1개, 복구 26개, 원시 요청/응답/영수증, 64개 입력 대조, 종료·무결성·입력 정체성, 원래 JSONL 바이트 보존 검사를 그대로 사용한다. 기존 함수나 모듈의 전역 값을 교체하지 않는다. 복구 실행 검증 함수에서 명령행 비교만 명시적으로 보완한다.

두 실행 각각에서 `--host`, `--port`, `--cors-origins`, `--api-key`가 정확히 한 번 있어야 한다. host는 `127.0.0.1`, port는 1~65535의 표준 십진 정수 문자열, origin은 정확히 `http://127.0.0.1:<그 실행의 port>`여야 한다. API 키는 기존 가림 표기여야 한다. 이 조건을 확인한 뒤에만 port와 연결된 origin을 함께 정규화한다. 다른 host·임의 origin·다른 포트·와일드카드·HTTPS·중복 옵션은 허용하지 않으며 나머지 명령행 인자와 샘플링은 기존 실행과 모두 같아야 한다.

코호트의 자료 스키마 `version`은 `input-execution-v1-recovery-cohort-v1`을 유지한다. 사후 검증 정체성은 별도 `validationVersion=input-execution-v1-recovery-cohort-validator-v2`와 `validationLineage`로 표시한다. lineage에는 원래 실행 동결 manifest, v1 검증기/테스트, v2 검증기/테스트, 이 보완 문서의 경로·해시를 넣는다. 원래 출력 행의 `runId`·`producerVersion`·문자열·원시 응답은 변경하지 않는다. v2 검증 결과를 v2 producer가 생성한 것처럼 표시하지 않는다.

S5는 v2 검증기를 명시적으로 불러야 한다. 전체 복구 26개와 종료·무결성 검증을 완료한 뒤 새 코호트 폴더를 만들며, 이 문서나 시작 감사만으로 생성 완료·의미 품질·질문 평가·후보 선택을 승인하지 않는다. 동결된 평가 명제·분모·허용 의미·수용 기준은 그대로다.
