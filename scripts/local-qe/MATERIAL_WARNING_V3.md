# 의미 전용 경고 매핑 v3

`material_warning_v3.py`는 `classify(validated)`, `identity()`를 제공한다. 고정 `contract_v2.py`의 두 배열 응답과 검증 metadata만 허용한다. v2 정책의 **major/critical 또는 uncertainty** 경고 의미와 전체/모델/split/교차 95·필수 sentinel6 기준은 유지한다. 이 모듈은 기준을 평가하거나 모델을 실행하지 않는다.

`identity()`는 정책·매핑 코드 SHA와 validator/prompt/schema/공통 helper SHA를 연결한다. 정책과 계약 파일 해시는 매번 대조하고 v2 계약은 고유 module 이름으로 읽기 전용 import한다. 기존 v1/v2 파일·GT·freeze는 수정하지 않는다. 모델 입력에 정책·선택 자료·판정을 보내는 기능은 없다.

| 입력/주장 | 매핑 |
|---|---|
| 유효 응답의 major/critical | `primaryWarning:true`; target quote가 없는/중복된 경우도 문단 위험 경고 유지 |
| uncertainty | `primaryWarning:true`, 구간 후보 생성 없음 |
| minor만 존재 | 경고 false; reason에 major/critical 단어가 있어도 승격하지 않음 |
| 두 빈 배열 | 발견된 material 주장 없음; 의미 정확성 보증 아님 |
| 실제 v2 invalid_json/invalid_schema 결과 | `unassessed`, 두 warning 값 null; 성공·no_findings로 바꾸지 않음 |
| v1 결과, 손상된 metadata/진단/형식 | 고정 코드의 ValueError로 거부; 호출자가 실패로 보존 |

유효 응답은 `language_notes`와 `dimension`을 거부한다. v2 validator가 이들 때문에 이미 invalid_schema로 판정한 원시 assessment는 미평가 결과로 보존한다. unknown status를 정상으로 간주하지 않는다.

진단은 `(collection,index,side)`마다 정확히 하나여야 한다. 없는/추가/중복 index, bool index, 원래 인용과 다른 quote, 누락된 진단, 임의 offset, `unique_spans` 불일치와 위조된 집계·metadata를 거부한다. 유일 인용의 UTF-16 길이·exclusive end를 대조한다. `ambiguous/not_found`는 null 위치만 허용한다.

`redCandidateIndices`는 유일한 target quote 진단을 가진 major/critical semantic issue의 원래 index뿐이다. minor·uncertainty·source-only omission은 포함하지 않는다. 항목 수는 모델이 보고한 항목 수이며, 같은 의미의 주장을 임의 병합하지 않는다.

중요한 한계: `classify(validated)`는 원문을 받지 않으므로 **구조적으로 일관되게 조작된 검증 결과를 원문과 재대조할 수 없다**. 계약 해시와 metadata도 값의 출처를 인증하는 서명이 아니다. 이후 runtime/evaluator는 완료 native 원시 bytes·EOS·잘림·sampling·원문 projection을 검증하고, 같은 validator로 raw를 재검증한 결과와 기록물을 대조해야 한다. 이 모듈은 그 검증을 대신하지 않는다.

`contractDiagnosticWholeParagraphReview`는 기존 진단 신호이며 material 경고와 구분한다. 구간은 후보이며 `semanticSpanPrecision:not_evaluated`다. 유일 인용이나 JSON 통과를 의미 검수·정확한 빨간 밑줄·학습 준비 완료로 표시하지 않는다. invalid/누락 응답의 FN·전체 완료 gate는 이후 평가기가 다룬다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 -m unittest discover -s scripts/local-qe -p test_material_warning_v3.py
```

합성 검사 15개 통과: 고정 실제 v2 validator를 사용해 경고/미평가 분리, reason 키워드 무승격, 진단 위조·중복/누락·UTF-16·hash 변경 거부를 확인했다. 모델 추론과 의미 품질은 측정하지 않았다.

‘실행 전 검토’는 이미 승인된 작업의 root 연결·자원 검토를 뜻하며 추가 사용자 permission을 요구하는 절차가 아니다. 이번 범위에는 runtime/evaluator·token audit·선택8·freeze·모델 실행·등록 구현이 없다.
