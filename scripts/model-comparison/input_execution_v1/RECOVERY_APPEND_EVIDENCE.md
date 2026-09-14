# 복구 평가·관계 관측의 원장 연결

`recovery_append_evidence.py`는 검증된 논리 cohort64개, 실제 S5 원문 판정64개·독립 답변64개·질문 채점128개, 복구 관계 진단64개를 전부 다시 대조한 뒤128개 관측 사건을 준비한다. 기본 실행은 준비만 수행하며 `--append`를 명시한 경우에만 기존 원장 writer를 호출한다. 실제 모델 호출·새 답변·정답 생성·평가 수정·원래 실패 실행 변경은 없다.

기존 `append_evidence.py`와 그 단일 실행 검증·고정 completed 사건 생성 함수는 변경하거나 사용하지 않는다. `recovery_evaluation.load_formal/read_frozen_answers`로 원 cohort·packet·배정/초안/답변 동결 해시를 재검증한다. 동결 `evaluation.aggregate` 결과와 저장 보고서 전체를 비교하되 새 `recoveryEvidence`의 formal/cohort SHA·원 실패 상태·38+26·65요청/중단1·정확한 근거 참조도 함께 일치해야 한다. 보류·누락이나 줄어든 분모는 준비 단계부터 거부한다.

복구 관계 보고서는 `recovery_relations.diagnose_rows`로 다시 계산하며, `cohortEvidence/cohortManifest`를 같은 cohort validator 결과와 대조한다. 원문/번역 문자열만 관계 검사기로 전달하며 경고 수를 번역 오류 수로 바꾸지 않는다. 원문 판정의 codepoint 근거와 관계 검사의 UTF-16 근거를 각각 보존한다.

동결 평가10개 파일·해시, 기존 원장 코드와 관계 검사기 SHA 검증에는 기존 `verify_evaluation_freeze`를 그대로 사용한다. 기존836개 사건·translation_review146개·ID inventory SHA 기준도 그대로다. 큰 파일 검증은 기존 streaming `EvidenceGraph`와 cohort validator가 맡는다. 원장 스냅샷·기준 검증·바이트 보존·내용 해시 기반 중복 방지 writer는 기존 함수를 재사용하며 기준을 자동 완화하지 않는다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/recovery_append_evidence.py --folder <복구정식S5폴더> --cohort <완료복구cohort폴더>/manifest.json --source-reviews <확정원문검토64.json> --grades <실제질문채점64.json> --report <복구S5보고서.json> --relations <복구관계진단폴더> --destination <새사건준비폴더>
```

`<...>`는 실제 검증된 경로로 바꾸며 destination은 `.training/quality-evaluation/input-preparation-v1/` 아래의 새 폴더여야 한다. `events.json`과 `prepared-manifest.json`을 root가 확인한 다음, 실제 원장 연결에는 같은 근거와 다른 새 destination으로 `--append`를 명시한다. 재실행 때도 모든 근거를 재검증하며 준비 파일만 믿고 쓰는 경로는 없다. 이 안내 자체는 실제 append를 수행하지 않는다.

사건은 `input_preparation_observation`64개와 `relation_observation`64개다. 각 사건에 실제 원본 run 경로·runId·producerVersion·원본 output/raw SHA·원래 실행 상태와 source cohort/stratum/domain/document group을 기록한다. 원 실패 실행에서 보존한38개는 두 종류 사건 모두 `originalRunStatus:failed`이며 개별 출력만 `originalOutputStatus:completed`다. 복구 실행26개는 완료된 실제 복구 run에 연결한다. 따라서 사건128개의 실행 상태 분포는 failed76·completed52이고, 모든 사건의 논리 cohort metadata는 실제 요청65/응답 미확인 중단1을 보존한다.

관계 사건은 확장된 실제 원문 관측 사건의 내용 해시를 `linkedInputPreparationEventIds`에 연결한다. `translation_review`나 `question_judgment`를 추가하지 않는다. 원문 오류·잘못된 독해 답변·관계 경고를 서로 다른 번역 오류3개로 합산하지 않는다. 같은 근거로 다시 준비하면 사건 바이트가 같으며 반복 추가는0개다. 기존 사건의 바이트와 historical translation_review 수가 바뀌거나 알 수 없는 새 사건이 끼면 거부한다. 이미 생긴 사건을 삭제해서 되돌리지 않는다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 -m unittest discover -s scripts/model-comparison/input_execution_v1 -p test_recovery_append_evidence.py -v
```

테스트의 append는 임시 폴더에 만든 합성 원장에만 수행한다. 실제836개 원장을 테스트로 쓰거나 현재 QA·점수를 만들지 않는다. cohort 모듈을 포함한 실제 연결 검증과 최종 근거의 준비/추가는 별도 완료 항목이다. 새 wrapper·테스트·이 문서는 기존 동결에 소급 편입하지 않고 별도 코드 해시로 보존한다.
