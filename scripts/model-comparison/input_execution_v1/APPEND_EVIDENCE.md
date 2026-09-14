# S5·관계 관측의 원장 연결

`append_evidence.py`는 완료된 S4의64개 출력과 최종 S5 판정, S4 관계 진단64개를 기존 원장에 연결하는 별도 도구다. 기본 실행은128개 사건을 **준비만** 한다. `--append`를 명시한 실행에서만 기존 `ledger.append_events()`를 호출한다. 모델·서버·앱 DB·등록·번역·과거 판정을 수정하지 않는다.

아래 경로는 실제 최종 파일로 바꿔야 한다. 준비 폴더는 항상 새로운 경로여야 한다. 같은 입력으로 다시 준비하거나 추가할 때도 새 receipt 폴더를 사용한다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/append_evidence.py --folder <최종S5폴더> --outputs <완료한S4폴더>/predictions.jsonl --source-reviews <최종원문판정.json> --grades <최종질문채점.json> --report <최종S5보고서.json> --relations <완료한관계진단폴더> --destination .training/quality-evaluation/input-preparation-v1/s5-ledger-prepared-001
```

생성된 `events.json`과 `prepared-manifest.json`을 root가 확인한 다음, 같은 근거 인수와 다른 새 destination에 `--append`를 붙인다. 이때도 근거를 다시 전부 검증하며, 준비 파일만 믿고 원장에 쓰는 우회 명령은 없다. 성공하면 `append-receipt.json`에 실제 추가·재사용 수와 이전 사건 보존을 기록한다.

준비 전에 다음 경계를 검증한다.

- S5 평가 동결 SHA `e1bc158a004e313f136b36c584f9a1d6a75f8afec43451c8d5e2c3b289e3d64b`와 정확한10개 코드/문서·검토 지침, 기존 원장 코드와 S3 v2 관계 검사기 해시.
- S4 종료된 summary의64개 완료·누락0·소유 종료·최종 무결성, 모든 산출물 해시, 동결 plan과 모델·런타임·입력 파일 해시. 큰 GGUF는1MiB씩 읽어 메모리 전체 로드를 피한다.
- S1/S2 자료를 통해 다시 만든 익명 packet과 최종 bundle/packet의 동일성, 독립 답변64개·128질문 동결·grade packet 동일성, 원문 판정64개와 질문 채점64행의 실제 인용/형식 및 보고서 재집계 일치. 보류/누락 판정은 완료 gate를 통과하지 않는다.
- 관계 보고서·manifest·완료 S4와의 연결, 검사기/연결 코드 SHA, 같은 원문·번역에 대한 관계 결과 재계산 일치. 관계 경고를 정답이나 의미 오류 수로 승격하지 않는다.

`input_preparation_observation`64개는 동결 `evaluation.ledger_observations()`가 생성한다. `relation_observation`64개는 동일 출력 해시와 이 새 원문 관측 eventId를 연결한다. 기존 `translation_review`·`question_judgment`를 추가하지 않으며 같은 출력의 원문 오류·질문 오답·관계 경고를 서로 다른 번역 오류3개로 세지 않는다. 관계 근거는 UTF-16, 원문 판정 근거는 Unicode codepoint 구간이라는 차이를 보존한다.

원장 기준은 구현 전 실제 확인한836개 사건·기존 `translation_review`146개다. 정렬된836개 사건 ID 목록의 SHA는 `6454dde292fa30469e44d734889923ee3692c0140c8a85452e896fe590d81edb`다. 매 실행에서 모든 파일명·내용 SHA를 검증하며, 기준 사건과 이번128개 외의 새로운 사건이 있으면 자동으로 제외하거나 기준을 바꾸지 않고 거부한다. 이번128개가 일부 또는 모두 존재하면 같은 바이트만 재사용한다. 따라서 같은 근거의 반복 추가는0개다. 기존 API가 쓰기 잠금을 소유한다. 전후 검사 사이의 외부 추가가 발견되면 성공으로 보고하지 않으며, 이미 추가한 사건을 삭제해서 복구하지 않는다.

검증은 격리된 임시 원장에서만 수행한다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 -m unittest discover -s scripts/model-comparison/input_execution_v1 -p test_append_evidence.py -v
```

이 도구·테스트·안내는 기존 S5 평가 동결10개에 소급 편입하지 않는다. 사용 전에 별도 새 해시 동결 기록으로 보존한다. 구현 완료와 실제 최종 근거의 준비/추가 완료는 별도로 보고한다.
