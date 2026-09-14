# 실패 보존형 S5 복구 평가 경로

`recovery_evaluation.py`와 `recovery_answer_transport.py`는 원 S4 attempt의 실패를 보존하고 검증된 논리 cohort를 평가하는 새 진입점이다. 기존 `evaluation.py`, `provisional_reviews.py`, `answer_transport.py`, 관계·원장 도구를 수정하거나 실행 중 함수를 교체하지 않는다. 구현·합성 테스트 완료와 실제 복구 생성·평가 완료를 구분한다.

`recovery_cohort.validate_cohort(path, root=ROOT)`가 폴더 또는 `manifest.json`을 받아 `rows`, `evidence`, `manifest`를 반환해야 한다. manifest는 `input-execution-v1-recovery-cohort-v1`/`completed_logical_cohort`, 원 실패 실행 38개 완료·39요청과 새 복구 실행 26개 완료·26요청을 보존한다. 논리 완료 출력은64개지만 실제 요청은65개이며 응답 미확인 중단1개를 없애지 않는다. 이 검증기가 원시 응답·요청·소유 종료·무결성·동결 입력 정체성을 검증한다. 큰 가중치는 검증기에서 streaming SHA로 대조하고, 평가 wrapper가 다시 읽는 `evidence.files`는 작은 실행·출력 증거다.

평가 wrapper는 매 진입 때 이 검증기를 호출하고 고정 S1/S2와 출력64개의 소속·원시 행 해시를 다시 연결한다. 각 행의 원래 `runId`, `producerVersion`, source/prompt/translation/raw 해시와 바이트를 보존한다. 다른 실행을 같은 runId로 바꾸어 기존 단일 실행 검사를 통과시키지 않는다. 원 failed summary는 계속 failed다.

## 명시적 새 명령

아래 `<...>`는 실제 검증된 경로로 바꾸며 새 결과 경로는 모두 Git 제외 `.training/quality-evaluation/input-preparation-v1/` 아래에 둔다. 같은 결과 경로를 재사용하지 않는다. 이 안내 자체는 실행이나 새 QA 배정을 시작하지 않는다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_evaluation.py prepare --cohort <복구cohort폴더>/manifest.json --destination <새정식S5폴더>
```

packet 순서는 S1의 seed와 ID/구성 해시 정렬 R001~R064를 그대로 사용한다. 질문 packet은 reviewId·한국어 translation·q1/q2만 가진다. 원문 packet은 동결 `provisional_reviews.packet_for`를 재사용하므로 같은 원시 출력에서 작성한 잠정 packet과 바이트가 같아야 한다. 복구 정보·구성 매핑·실패 이력은 private bundle/manifest에만 둔다. 새 manifest 상태는 `recovery_packets_prepared`이며 기존 단일 실행 transport에 넣으면 거부된다.

root가 실제 `fork_turns=none` spawn을 수행하고 반환된 canonical actor를 확인한 다음에만 기록한다. 기존 source reviewer를 답변자로 전용하지 않는다. 답변자에게는 단일 inline packet과 [질문 전달 절차](../../../content/model-comparison/input-execution-v1/QUESTION_REVIEW_PROTOCOL.md)의 지시만 제공하며, 답변자는 transport·저장소·다른 packet을 읽지 않는다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_answer_transport.py record --folder <새정식S5폴더> --review-id R001 --actor /root/q_r001
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_answer_transport.py collect --folder <새정식S5폴더> --drafts <실제답변초안64개폴더> --destination <새답변수집폴더>
```

배정은 공통 transport v1 schema를 유지하면서 복구 S5 manifest SHA에 결합한다. 자동 actor 생성·같은 actor 재사용·배정 덮어쓰기는 없다. 정확한 runtime model ID 미노출 표기를 유지한다. collect는64개 실제 배정과64개 초안의 ID/packet/actor/인용을 검사하고 `review_io.question_answer`로 인용 구간만 변환한다. helper가 reviewId를 덮어쓰기 전에 초안 ID 불일치를 거부한다. 원본 배정·초안을 바이트 그대로 복사하고 해시를 연결한다. 답변·이유·내용을 교정하거나 정답 판정을 만들지 않는다. spawn·단일 packet 전달·사전 무노출·파일 작성자의 진실성은 운영자가 보존한 실제 spawn 기록에 의존하며 파일 검증만으로 증명하지 못한다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_evaluation.py freeze-answers --folder <새정식S5폴더> --collection <새답변수집폴더>
```

답변 동결은 수집 receipt, 원래 배정/초안과 보존 사본,64개 actor·128개 질문, packet SHA와 정확 인용을 다시 확인한 뒤 수행한다. 임의 `answers.json`만으로 동결하지 않는다. 원본 초안·수집 결과를 이후 변경하면 거부한다. 모든 답변 동결 후 source를 볼 수 있는 별도 채점자에게 grade packet을 제공하며, 답변자와 채점자 actor는 달라야 한다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_evaluation.py verify-promotion --provisional <원래잠정단위폴더> --folder <새정식S5폴더> --receipt <새승격정체성receipt.json>
```

잠정 source packet4개와 원래 output/raw 파일 SHA를 복구 cohort에서 보존된 원 실패 실행의 해당4행에 연결한다. packet은 byte 동일해야 한다. 결과는 정체성상 승격 가능 영수증일 뿐 원문 검토 승인이나 점수가 아니다. 실제 평가자가 잠정 판정과 정식 packet을 확인한 뒤 명시적으로 확정해야 한다. 검증 중 원 failed attempt를 완료로 선언하거나 이전 판정을 덮어쓰지 않는다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_evaluation.py report --folder <새정식S5폴더> --source-reviews <확정원문검토64.json> --grades <실제질문채점64.json> --output <새복구S5보고서.json>
```

보고서는 동결 `evaluation.aggregate` 및 그 개별 원문/답변/질문 판정 validator와 후보 선택 기준을 그대로 재사용한다. `recoveryEvidence`만 별도 부착해 실패 보존·38+26·65요청/중단1·근거 해시를 명시한다. 명제192·용어156·질문128(핵심64) 분모와 보류 처리, 일반 회귀 거부·중요 오류·핵심 명제 개선·다중 후보 순위는 바뀌지 않는다. 원문·채점 입력이 없으면 생략할 수 있지만 해당 보고서는 미완료이며 자동 점수를 생성하지 않는다. 질문 채점을 주려면 실제 동결 답변이 필요하다. 후보 선택은 등록 승인이 아니다.

## 관계·원장의 별도 복구 경로

기존 `relations.load_outputs/diagnose_rows/write_report`, `append_evidence.validate_final_evidence`도 단일 실행 전제다. 이 복구 평가·답변 wrapper는 관계 진단·원장 append를 하지 않는다. 기존 함수를 monkeypatch하거나 혼합 행의 runId를 덮어써서 호출하지 않는다. 별도 `recovery_relations.py`가 복구 cohort validator와 순수 checker·인용 검증을 연결하며, [복구 원장 연결](RECOVERY_APPEND_EVIDENCE.md)의 `recovery_append_evidence.py`가 실제 최종 원문·동결 답변·질문 채점·관계 근거를 다시 대조한다. 두 도구의 구현과 실제 최종 근거 준비/append 완료는 별개다. 기존 실패·관측 사건은 불변이다. 새 독립 holdout 자료 생성·열람·선정과 모델 호출은 이 도구 범위에 없다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 -m unittest discover -s scripts/model-comparison/input_execution_v1 -p test_recovery_evaluation.py -v
```

테스트는 임시 폴더의 합성64행·원문/질문·배정만 사용하며, cohort/runtime 검증기의 별도 테스트를 대신하지 않는다. 실제 복구·QA·품질 통과로 보고하지 않는다. 새 도구·테스트·이 안내는 기존 평가 동결 파일에 소급 편입하지 않고 사용 전에 별도 해시로 동결한다.
