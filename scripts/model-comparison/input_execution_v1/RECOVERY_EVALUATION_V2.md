# 회복 코호트 검증 v2와 평가 연결

이 경로는 원래 실행의 포트와 연결된 localhost Origin을 함께 검증하는 [사후 검증 보완](../../../content/model-comparison/input-execution-v1/RECOVERY_COHORT_VALIDATION_V2.md)을 명시적으로 사용한다. `recovery_cohort_v2.validate_cohort` 외의 검증기로 조용히 대체하지 않는다. 기존 실행·평가 파일을 수정하거나 실행 중 함수·전역값을 교체하지 않는다.

논리 코호트의 `manifest.version`은 `input-execution-v1-recovery-cohort-v1`을 유지한다. `validationVersion`은 `input-execution-v1-recovery-cohort-validator-v2`이며, `validationLineage`의 여섯 파일 참조는 실행 동결 manifest, 기존 검증기·테스트, 새 검증기·테스트, 보완 문서의 해시와 바이트 수를 보존한다. 기존 38개와 새 26개의 원래 `runId`·`producerVersion`·원시 바이트는 그대로다. 첫 실행은 failed이며 전체 요청 수는 응답 불명확 중단 1개를 포함한 65개다.

`recovery_v2_lineage.verify`는 기존 회복 평가 동결 manifest SHA `37badacdc495e15764949a84d1e07007f2d5ee181e8d9a567a00412e70268e5e`와 그 안의 11개 파일을 다시 검증한다. 그 결과는 `evaluationLineage`로 코호트 검증 이력과 구분해 결합한다. 큰 모델 파일은 코호트 검증기가 streaming SHA로 확인하며 이 보조 모듈은 작은 코드 파일만 읽는다.

## 진입점과 유지되는 평가 계약

`recovery_evaluation_v2.py`는 새 검증기의 `rows`, `evidence`, `manifest`를 받아 S1/S2 입력 및 원래 output/raw 파일과 64개를 대조한다. 준비·읽기·답변 동결·보고·승격의 진입점은 새 파일에 명시되어 있다. 기존 v1의 순수 packet 생성과 manifest 계산을 호출한 뒤 검증 버전과 이력만 새 봉투에 넣는다. source packet, question packet, 구성 매핑, 출력 provenance의 바이트는 기존 v1과 같다.

중요 오류·핵심 명제·일반 의미 회귀·용어·자연스러움·질문 채점·미해결 처리·다중 후보 순위·개선 없는 종료는 동결 `evaluation.py`의 validator와 aggregate를 그대로 사용한다. 도구가 새 답변이나 점수를 작성하지 않는다. 명제 192개, 용어 156개, 질문 128개 중 핵심 질문 64개의 분모를 변경하지 않는다. 개발 결과를 독립 확대 평가나 앱 등록 승인으로 표시하지 않는다.

아래 경로 인자는 실제 준비된 경로로 바꾼다. 모든 평가 결과는 Git 제외 `.training/quality-evaluation/input-preparation-v1/`의 새 폴더나 파일에 저장한다. 완료 64개와 두 실행의 최종 무결성 검증 전에는 첫 명령을 실행할 수 없다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_evaluation_v2.py prepare --cohort <cohort>/manifest.json --destination <formal-folder>
```

`recovery_answer_transport_v2.py`는 새 formal manifest SHA에 공통 배정 schema를 결합한다. 실제 `collaboration.spawn_agent`의 `fork_turns=none` 결과를 받은 운영자만 record를 실행한다. 기존 actor의 재사용·가짜 context 자동 생성·배정 덮어쓰기는 허용하지 않는다. 답변자는 단일 inline question packet만 읽고 transport나 저장소를 읽지 않는다. 질문 packet의 내용은 reviewId·번역·질문 2개뿐이다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_answer_transport_v2.py record --folder <formal-folder> --review-id R001 --actor /root/q_r001
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_answer_transport_v2.py collect --folder <formal-folder> --drafts <actual-drafts64> --destination <new-collection>
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_evaluation_v2.py freeze-answers --folder <formal-folder> --collection <new-collection>
```

collect와 freeze는 64개 실제 배정·초안의 ID/actor/packet/정확 인용 및 원본·보존 사본 해시를 다시 확인한다. helper가 reviewId를 덮어쓰기 전에 초안 ID가 다른 경우 거부한다. 변환은 명시 인용을 Unicode codepoint 구간으로 만드는 것뿐이며 답변 문구나 정답 판단을 바꾸지 않는다. 실제 새 context와 사전 미노출 여부는 운영자가 보존한 spawn 기록에 의존한다. 파일 검증만으로 그 격리를 독립 입증했다고 주장하지 않는다. 정확한 runtime model ID 미노출 표기도 유지한다.

## 잠정 원문 검토의 두 승격 경로

원래 failed run에서 만든 `provisional_reviews.py` packet은 기존과 같은 `verify-promotion` 진입점으로 검증한다. 새 `recovery_provisional_reviews.py`가 생성한 혼합 또는 회복 출력 전용 packet은 별도 `verify-recovery-provisional`을 사용한다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_evaluation_v2.py verify-promotion --provisional <old-provisional-unit> --folder <formal-folder> --receipt <new-old-identity-receipt.json>
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_evaluation_v2.py verify-recovery-provisional --provisional <recovery-provisional-unit> --folder <formal-folder> --receipt <new-recovery-identity-receipt.json>
```

새 잠정 manifest의 버전은 `input-execution-v1-recovery-provisional-source-v1`이다. 단위 IP1-G02~G08의 네 구성만 받고, 생성 도구 SHA `ef62ae93467924548e3966754bfc7a53d3a620fb89bd6b516cfa7f37a8cfebb0`를 고정한다. 회복 plan의 해시와 경로가 최종 코호트의 해당 실행 plan과 같아야 한다. 각 output·rawResponse·request·receipt는 최종 검증 증거의 같은 파일과 연결되고 SHA·바이트 수가 같아야 한다. 요청·영수증을 다른 완료 출력의 정상 파일로 바꾸어도 거부한다. 네 source packet은 최종 packet과 바이트까지 같아야 한다.

잠정 `sourceRunStatus=not_finalized`는 잠정 기록에 보존되고 최종 receipt는 실제 회복 실행의 completed를 별도 기록한다. 원래 실행의 failed는 그대로다. packet 정체성 검증은 잠정 의미 판정의 자동 승인이 아니다. receipt는 `formalSourceReviewsApproved=false`, `actualReviewerConfirmationStillRequired=true`를 유지하며 검토자가 최종 packet과 기존 판단을 확인한 후 명시적으로 확정해야 한다. 새 질문·점수·후보 선택은 이 단계에서 생성하지 않는다.

## 최종 보고와 원장

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_evaluation_v2.py report --folder <formal-folder> --source-reviews <confirmed-source64.json> --grades <actual-question-grades64.json> --output <new-report.json>
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_relations_v2.py --cohort <cohort> --destination <new-relations>
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_append_evidence_v2.py --folder <formal-folder> --cohort <cohort> --source-reviews <confirmed-source64.json> --grades <actual-question-grades64.json> --report <new-report.json> --relations <new-relations> --destination <new-ledger-evidence>
```

관계 wrapper는 새 cohort validator로 전후 재검증하고 기존 `recovery_relations.diagnose_rows`의 실제 행·두 문자열 입력·진단을 재사용한다. 의미 승인으로 해석하지 않는다. 원장 wrapper는 최종 formal/frozen answers/원문/질문 판정으로 aggregate를 다시 계산하고, 관계 진단 및 코호트 전체 증거와 evaluationLineage도 재대조한다. 기존 평가 동결 10개 파일과 원장 836개/translation_review 146개 기준을 계속 적용한다.

원장 명령의 기본값은 준비만 수행한다. 실제 최종 증거가 완성된 뒤 명시 `--append`에서만 기존 보존·멱등 writer를 사용한다. 생성할 관측은 입력 64개와 관계 64개이며 각 관측에 원래 failed 또는 completed 실행을 기록한다. 기존 번역 판정·원문·출력 바이트를 변경하지 않는다. 이 연결 도구의 구현·합성 테스트 완료는 실제 64개 생성, 실제 독립 답변, 평가 통과 또는 실제 원장 추가 완료를 뜻하지 않는다.
