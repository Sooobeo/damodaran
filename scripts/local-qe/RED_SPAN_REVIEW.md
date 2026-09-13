# 빨간 구간 제안의 의미 정밀도 검토

`red_span_review.py`는 기존 출력과 원시 증거를 읽는 별도 prepare/grade CLI다. 모델 로딩·추론·등록·DB 변경을 하지 않는다. 현재 지원하는 입력은 고정된 `evaluate_llm_review_v2.py`가 검증한 **v4 전체 48개 완료 결과**뿐이다. 12개 완료 후 실패한 현재 실행은 거부한다. 부분 실행·재개 실행용 adapter는 구현하지 않았다.

구간 판정·분모 계약은 이후 후보에도 참고할 수 있지만 다른 실행 버전의 결과를 그대로 받아들이지는 않는다. 새 후보는 별도 검증된 version adapter가 필요하다. 이 helper의 합성 검사 통과는 실제 밑줄 품질 실측이 아니다.

## 실행

전체48 완료·무결성·자식 종료를 통과한 기존 v2 분석 파일이 있을 때만 준비한다. `analyze_run()`을 읽기 전용으로 재호출해 저장 결과를 원시 응답·요청·freeze 근거와 재대조하며 새 추론은 없다. 실행 상태가 실패/부분이면 초기 단계에서 거부한다.

```powershell
py -3.11 -B -X utf8 scripts/local-qe/red_span_review.py prepare --analysis .training/quality-evaluation/COMPLETE-ANALYSIS.json --run .translation/qe/llm-candidates/qwen35-9b/runs/COMPLETE-RUN --input .translation/qe/llm-candidates/qwen35-9b/inputs.jsonl --output .training/quality-evaluation/red-span-review-new
py -3.11 -B -X utf8 scripts/local-qe/red_span_review.py grade --bundle .training/quality-evaluation/red-span-review-new --review .training/quality-evaluation/red-span-review-new/assistant-review.json --output .training/quality-evaluation/red-span-review-new/graded.json
py -3.11 -B -X utf8 -m unittest discover -s scripts/local-qe -p test_red_span_review.py
```

위 입력 경로는 명령 형식 예시이며 실제 완료 artifact의 존재를 뜻하지 않는다. 새 출력만 허용하며 기존 폴더·파일을 덮어쓰지 않는다. 원시 evidence, 기존 분석, 입력, 관련 코드의 SHA를 전후 확인하고 grade 때 다시 대조한다.

## 검토자에게 제공하는 자료

`reviewer-packet.json`만 전달한다. 무작위 ID, 원문·문맥·한국어, 실제 제안 구절·UTF-16 offset·제안 사유, 고정 경계 rubric을 담는다. 모델 이름·원본 ID·GT·기존 판정·임계값 통과 여부는 전달하지 않는다. `private-link.json`에는 원본 ID, 모델/집합, 모든 후보 index, 원시 증거 SHA와 기술 분모를 따로 보존한다.

제안 사유는 검토 대상이다. 실제 major/critical 오류가 **해당 위치에 존재하고, 원인이 맞으며, 표시 범위에 불필요한 정상 내용이 지나치게 포함되지 않는지** 원문으로 판단한다. 동의 표현·경미한 문체 차이·다른 곳의 오류만으로 제안 구간을 맞다고 하지 않는다. 문자열 일치나 중첩은 의미 정답으로 채점하지 않는다.

도우미 검토 JSON의 정확한 구조:

```json
{
  "version": "red-span-assistant-review-v1",
  "reviewerType": "assistant",
  "humanReviewed": false,
  "packetSha256": "실제 packet 파일 SHA256",
  "rubricSha256": "packet의 rubricSha256",
  "priorExposureKo": "검토자가 이전에 본 후보·번역·판정의 범위를 사실대로 기록",
  "rows": [{
    "id": "packet의 익명 ID",
    "verdict": "unresolved",
    "actualSeverity": "unresolved",
    "locationCorrect": null,
    "causeCorrect": null,
    "extentAppropriate": null,
    "sourceQuote": "실제 원문 부분문자열",
    "translationQuote": "제안된 한국어 구절 그대로",
    "reasonKo": "원문에 근거한 판단과 미해결 이유"
  }]
}
```

`correct`는 실제 severity가 major/critical이고 위치·원인·범위가 모두 true인 경우만 허용한다. `false_positive`는 셋 중 적어도 하나가 false이거나 실제 오류가 none/minor일 때 사용한다. 애매하면 `unresolved`로 두며 합격 분자에 넣지 않는다. 각 익명 ID는 정확히 한 번 있어야 한다. 누락·중복·존재하지 않는 ID·추가 key·잘못된 bool·인용 불일치를 거부한다. 도우미 검토를 사람 검수로 표시할 수 없다.

## 서로 다른 분모

- **모든 v2 빨간 후보 주장**: `redCandidateIndices`의 각 출력/index를 빠짐없이 분모에 둔다. 같은 위치의 여러 주장도 삭제하지 않고 각각 검토한다.
- **실제 unique 표시 구간**: baseline 계약에 맞춰 같은 출력 ID와 같은 한국어 UTF-16 시작/끝을 묶는다. 같은 구간의 주장 중 하나가 오탐이면 구간도 오탐, 하나가 보류이면 구간도 보류다. 참인 사유 하나만 골라 통과시키지 않는다. 다른 출력의 같은 구절은 별도 구간이다.
- **기술적 위치 검증**: 모든 제출된 비어 있지 않은 원문/한국어 인용을 센다. minor·language note와 원래 not_found/ambiguous로 탈락한 인용도 포함한다. 의도적으로 비운 반대편 anchor는 제출 인용이 아니다. 원래 정직하게 기록된 인용 실패는 기술 분모에 남기고, 저장 offset·상태·구절이 재계산과 불일치하면 변조/계약 오류로 거부한다.

두 의미 단위 모두 전체·각 번역 모델 구성·각 집합·모델×집합을 따로 보고 95%를 적용한다. baseline의 unique 구간 판정과 모든 index 판정을 별도 필드로 보존하며 하나로 섞지 않는다. 분모0은 `not_evaluated`, 보류가 있으면 비율이95% 이상이어도 `hold`다. 기술적 유효성은100%이며 의미 정확성의 증거가 아니다. 정확 이항분포 하한은 iid를 가정한 참고 수치이며 공유 원문·도우미 판정 표본에 iid가 입증됐다고 주장하거나 이를 새 gate로 사용하지 않는다.

문단 탐지 지표·기존 정답·등록 정책은 바꾸지 않는다. 원문 오류 전체 inventory가 없으므로 개별 오류 재현율도 만들지 않는다. `fullLearningReadinessAccepted`는 false이며 앱 등록을 수행하지 않는다. 현재 실제 48개 완료 span 검토는 미실측이다.
