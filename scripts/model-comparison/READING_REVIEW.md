# 실제 공개 원문 6문단의 도우미 검토

이 도구는 `real-reading-check-20260910`의 고정된 6문단을 2~4개 완료 구성으로 비교한다. 한 금융 문서에서 고른 작은 후속 관측이며 독립 최종 시험이나 번역 품질의 하방 보증이 아니다. 원문·도우미 참조·주의사항은 기존 파일을 그대로 검증한다. 사람 검수는 0건이고 모델 실행·학습·원문 재추출·DB 접근은 수행하지 않는다.

기존 18행 개발 집계기와 분리한 신규 파일이다. `reading_check_input.py`의 6행 전체 검증과 `id/source/context/domain/sourceSha256/contextSha256` 허용 필드를 재사용한다. 참조와 주의사항은 검토 자료에만 넣으며 이 CLI는 모델 입력을 만들거나 전송하지 않는다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/prepare_reading_reviews.py --input content/model-comparison/real-reading-check-20260910/sources.jsonl --results .training/comparisons/<run-1> .training/comparisons/<run-2> --output .training/comparisons/<comparison>/prepared
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/summarize_reading_reviews.py --prepared .training/comparisons/<comparison>/prepared --reviews .training/comparisons/<comparison>/review-a.jsonl .training/comparisons/<comparison>/review-b.jsonl --output .training/comparisons/<comparison>/assistant-review-summary.json
```

준비 폴더는 새로 만들어야 한다. `packet.jsonl`은 6원문 각각의 A/B 또는 A/B/C/D 순서를 독립적으로 섞고, 원문·문맥·출처·도우미 참조를 함께 담는다. `review-advisory.json`은 고정 원본 바이트의 사본이다. `review-template.jsonl`의 판단은 `null`과 빈 근거로 시작하므로 그대로는 집계할 수 없다. 검토자는 원문·문맥과 주의사항을 읽고, template의 행을 준비 폴더 밖의 새 검토 파일에 복사해 판단만 채운다. 행을 나눠 검토해도 전체 6행과 각 후보 판단이 한 번씩 모여야 한다.

각 검토 행의 계약은 다음과 같다. ID·SHA·후보 순서는 template에서 그대로 복사한다. `reviewerId`만 선택적으로 추가할 수 있다.

```json
{
  "id": "REAL26-001",
  "sourceSha256": "<source SHA>",
  "contextSha256": "<context SHA>",
  "preparedManifestSha256": "<manifest SHA>",
  "packetSha256": "<packet SHA>",
  "reviewerType": "assistant",
  "humanReviewed": false,
  "judgments": [{
    "id": "REAL26-001",
    "label": "A",
    "targetSha256": "<translation SHA>",
    "severity": 2,
    "fluent": true,
    "errors": [{
      "severity": 2,
      "category": "semantic_role",
      "sourceSpan": "<원문 또는 문맥에 실제 존재하는 연속 구절>",
      "targetSpan": "<해당 번역에 실제 존재하는 연속 구절>",
      "reasonKo": "<주체나 의미 관계가 어떻게 달라졌는지 개별 근거>"
    }],
    "reasonKo": "<해당 원문과 번역을 실제로 대조한 짧은 근거>",
    "humanReviewed": false
  }]
}
```

`severity`는 0(관측 오류 없음), 1(경미), 2(중요한 의미 손상), 3(심각한 의미 손상)이며 오류 목록의 최대값과 같아야 한다. 오류가 없으면 `errors=[]`, `severity=0`이다. 오류가 없는 판단도 개별 한국어 근거가 필요하다. `fluent`는 한국어의 자연스러움으로 별도 판단한다. 유창해도 의미가 틀릴 수 있고, 문체가 조금 어색하다는 이유만으로 중요한 의미 오류라고 판정하지 않는다. 원문을 우선하며 참조와 표기가 다르다는 이유만으로 오류를 만들지 않는다.

허용 오류 범주는 `semantic_role`, `negation_condition`, `quantity_formula`, `word_sense`, `discourse_reference`, `omission`, `unsupported_addition`, `terminology`, `modality`, `fluency`, `other`다. 각 오류는 실제 원문·문맥 구절과 번역 구절을 지목해야 한다. 생략(`omission`)에 한해서만 `targetSpan=""`를 허용한다. 이 자료에는 표준 용어 체크 ID가 없으므로 `termChecks`나 표기 점수를 강제하지 않는다.

집계기는 모든 검토 파일의 6×N 판단·ID·SHA·중복·누락·개별 근거·구절 위치를 검증한 **뒤에 처음으로** `review-key.json`과 생산자 결과를 읽는다. 준비 전에는 출력·summary를 읽어 해시를 결속해야 하므로, 준비 담당자가 모델 정체성을 모른다고 주장하지 않는다. 검토 담당자는 판단 완료 전 키·생산자 결과·점수를 읽지 않는다. 이 순서는 도구의 검증 계약이며, 사람이 별도 파일을 직접 열지 못하게 하는 접근 제어 장치는 아니다.

현재 지원하는 생산자는 버전 없는 Hy-MT2 7B contextual baseline, `translategemma-large-screen-v2`·`translategemma-large-screen-v3`, `hymt30-development-screen-v2`·`hymt30-development-screen-v3`다. 모두 같은 6개 ID·source/context SHA·전체 입력 파일과 reader SHA, 완료 상태와 개별 출력 SHA를 확인한다. TG는 중첩 입력과 `count=recordedCount=expectedCount=6`, 무결성·자식 종료 증거, predictions SHA를 검증한다. Hy30은 최상위 입력과 `completed=6`, 자식 종료·모델 로드, artifact의 predictions SHA를 검증한다. Hy7은 `hymt:<model SHA>:<manifest SHA>` 등록 정체성과 모델 SHA를 맞춘다. 기록된 모델·설치·코드 식별 정보와 원시 summary 전체를 보존하지만 실제 가중치·과거 런타임 바이트를 다시 읽지는 않는다.

v3는 추가로 일시정지 생성·메모리 제한·관측 샘플·오류 없는 감시 종료와 관련 해시의 완료 metadata를 확인한다. 실제 RAM 사용을 다시 측정하거나 의미 품질 점수를 추가하지 않는다. 준비 코드 명세에는 공통 v3 증거 helper도 포함한다. 미지 버전은 거부하며 새 비교는 새 준비 폴더를 사용한다.

Hy7의 생산자 summary에는 완료 시점 predictions SHA가 없으므로, 그 출력의 바이트 결속은 **준비 시점부터** 성립한다. 이 도구가 준비 이전 변조까지 독립 검증했다고 보고하지 않는다. Hy30의 완료는 생성 요청의 완료이며 의미·출력 무결성 통과와 같지 않다. 원시 결과를 보존하고 원래의 자동 검사 상태는 summary metadata에서 구분한다.

결과는 구성별 오류 심각도 행 수·중요 오류 행 수(`severity>=2`)·유창한 행 수·오류 범주별 관측을 기록한다. 새 canonical 점수, 용어 90% gate, 자동 승격 조건은 만들지 않는다. 완료 결과와 준비 파일은 덮어쓰지 않으며 입력·검토·키·생산자·코드 변경을 거부한다.

```powershell
.venv-training/Scripts/python.exe -X utf8 -m unittest discover -s scripts/model-comparison -p test_reading_reviews.py -v
```

위 검사는 합성 원문·출력·키만 사용한다. 합성 검사의 통과는 실제 번역 품질 검증이 아니다.
