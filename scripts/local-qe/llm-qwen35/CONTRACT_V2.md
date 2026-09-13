# 의미 전용 contract v2

2026-09-11. [설계 초안](../../../.training/verifications/qwen-next-semantic-only-design-20260911.md)의 짧은 지시와 두 배열 schema를 별도 파일로 구현했다. 기존 v1 파일·v4 실행기·정책·평가·GT·freeze는 변경하지 않았다. 이 단계는 형식/인용 검증이며 품질 개선 실측이 아니다.

설계의 ‘승인 시’는 이미 승인된 후속 작업을 위한 **실행 전 root의 연결·자원 검토**를 뜻한다. 별도 사용자 허가를 다시 요구하는 절차가 아니다. 이번 범위는 모델 설치·실행·런타임 연결·등록을 포함하지 않는다.

| API | 계약 |
|---|---|
| `read_input(path)` | `(rows, original_byte_sha256)`. id/source/translation/context만 허용하며 context 기본값은 빈 문자열이다. BOM·CRLF도 원래 바이트 해시에 포함한다. |
| `load_prompt()`, `load_schema()` | 고정 SHA 확인. schema는 매번 새 객체다. |
| `contract_identity()` | `qwen35-meaning-v2`와 prompt/schema/공통 helper SHA. |
| `build_request(row)` | source/translation/context만 모델에 전달한다. ID·GT·checks·references를 차단한다. JSON schema 이름은 `qwen35_meaning_v2`, `enable_thinking=false`, 기존 sampling·출력2048 예약을 유지한다. 호출 자체는 추론·tokenization을 하지 않는다. |
| `validate_response(row, raw_text)` | `valid/invalid_json/invalid_schema`, 모델 주장에 따른 의미 상태, 원시 assessment와 정확 인용 위치를 반환한다. 런타임의 원시 bytes/EOS/잘림 검사는 별도다. |

출력은 `semantic_issues`와 `uncertainties`만 허용한다. `language_notes`, `dimension`, 점수·확률·추가 키는 거부한다. kind별 빈 인용 조건, critical/major/minor, 공백만 있는 reason/quote 거부는 v1과 같다. 자유 thinking은 현재 grammar/template 계약에서 검증하지 않았으며 활성화하지 않는다.

기존 `contract.py`는 정확한 파일 SHA를 확인한 뒤 고유 module 이름으로 읽기 전용 import한다. 재사용 범위는 입력 검사·엄격 JSON parser·작은 schema 검사·인용 조건·정확 위치 함수와 불변 상수다. v1의 prompt/schema 로더·request builder·response validator는 호출하지 않는다. helper 파일이 바뀌면 새 공개 API 호출도 실패한다.

JSON 중복 키는 거부하고, 입력 중복 ID도 거부한다. 인용 문자열의 겹치는 반복까지 검색하며 유일하지 않으면 offset을 만들지 않는다. 같은 의미를 다른 표현으로 중복 주장했는지는 자동 판정하지 않는다. 완전히 같은 항목도 원래 순서대로 보존하며 `error_count`는 보고된 항목 수다. 임의 병합·삭제·심각도 변경을 하지 않고 독립적으로 판정된 고유 오류 수라고 주장하지 않는다.

인용은 원문/번역의 실제 연속 문자열에서만 찾는다. 문맥의 인용, Unicode/공백 정규화, 임의 일대일 정렬은 허용하지 않는다. 유일 매칭의 `start/end`는 UTF-16 code unit이며 end는 exclusive다. astral 문자도 실제 UTF-16 slice로 왕복 검증한다. 없는/중복 인용은 `not_found/ambiguous`와 null offset을 남기며 의미 주장·심각도를 유지한다.

`whole_paragraph_review`는 v1과 동일한 **진단 신호**라 minor·불확실성·인용 실패도 포함한다. material 경고나 95 gate를 뜻하지 않는다. 새 후처리 연결은 별도 작업이다. `semantic_quality_certified:false`, `semantic_span_precision:not_evaluated`를 반환하고, `span_status:verified`는 인용 위치만 뜻한다. 빈 배열도 검수 완료나 의미 정확성 보증이 아니다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 -m unittest discover -s scripts/local-qe/llm-qwen35 -p test_contract_v2.py
```

합성 15개를 실제 실행해 통과했다. 새 schema와 요청 projection, 고정 파일 변경 거부, 인용 조건·반복·누락·UTF-16 왕복, 기존 전용 함수 미호출을 확인했다. 실제 native grammar 지원·생성·의미 품질은 미실측이다.

root 연결 검토에는 새 API/원시 결과 shape, 의미 경고와 진단의 분리, 새 prompt/schema 실제 template·grammar·token audit, 새 런타임·평가 adapter·freeze가 필요하다. 기존 v2 분석기와 v4 전체48 전용 span helper로 새 응답을 처리하거나 문체 빈 배열을 덧붙여 v1처럼 통과시키지 않는다.
