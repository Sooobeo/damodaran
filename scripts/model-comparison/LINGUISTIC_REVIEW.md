# 언어학적 개발 자료의 익명 검토 계약

이 도구는 새 개발 자료 18개와 모델 2~4개의 출력 36·54·72개를 도우미가 원문과 대조한 결과를 집계한다. 준비 manifest의 `judgmentCount`와 모든 packet에 같은 모델 수가 있어야 한다. 사람이 검수한 정답, 독립 최종 시험, 54개 용어 90% 시험 또는 앱 승격 기준이 아니다. 실제 모델 출력에 맞춰 참조·체크·기준을 변경하지 않는다.

검토자는 자신에게 배정된 `packet-N.jsonl`과 준비 `manifest.json`을 읽고, 준비 폴더 밖의 별도 JSONL에 아래 행을 작성한다. `review-key.json`, 다른 모델 결과, 집계 점수는 판단을 끝내기 전에 열지 않는다. `sourceSha256`은 `packet.input.sourceSha256`, `packetSha256`은 해당 packet 파일의 실제 SHA-256, `preparedManifestSha256`은 준비 manifest의 실제 SHA-256이다. ID와 번역 해시는 packet에서 그대로 복사한다.

준비 manifest에 `sourceAudit`가 있으면 같은 폴더의 `source-audit.json`도 판단 전에 읽는다. 이 자료는 모델 출력을 보기 전에 작성한 원문·참조 해석의 주의사항이며 정답 기준이나 점수를 바꾸는 문서가 아니다. 집계기는 정확한 파일명·바이트 해시·감사 버전·모델 출력 미관측·사람 검수 아님·원본 데이터 SHA를 확인하고 `sourceAuditSha256`을 남긴다. 별도 검토 행 필드는 필요하지 않으며 기존 `preparedManifestSha256`에 이미 결속된다. 감사 없는 synthetic fixture도 지원한다.

```json
{
  "id": "LDEV26-001",
  "sourceSha256": "<packet.input.sourceSha256>",
  "reviewerType": "assistant",
  "reviewerId": "<담당 도우미>",
  "humanReviewed": false,
  "preparedManifestSha256": "<manifest.json SHA-256>",
  "packetSha256": "<담당 packet SHA-256>",
  "judgments": [
    {
      "id": "LDEV26-001",
      "label": "A",
      "targetSha256": "<후보 A의 targetSha256>",
      "severity": 0,
      "fluent": true,
      "contrastPreserved": true,
      "errors": [],
      "termChecks": [],
      "reasonKo": "원문의 구체적인 관계와 번역을 대조한 개별 근거를 적는다.",
      "humanReviewed": false
    }
  ]
}
```

위 예시는 필드 설명용이며 실제 `judgments`에는 packet에 있는 A, B(3개 모델이면 C, 4개면 D까지)를 그 순서로 모두 작성한다. 정확한 오류 목록 없이 예시의 0·true 값을 복제하지 않는다. `reasonKo`는 오류가 없는 후보에도 필요하며 관찰 없는 정형 칭찬을 근거로 쓰지 않는다. 참조와 다른 자연스러운 한국어 표현도 원문에 충실하면 허용한다.

| 필드 | 판정 계약 |
|---|---|
| `severity` | 0: 관측 오류 없음, 1: 경미한 정밀도·표기·문체 문제, 2: 학습 내용의 관계·조건·수량·뜻을 바꾸는 중요한 오류, 3: 핵심 내용의 심각한 반전·대폭 누락·허구 추가. 반드시 오류 목록 severity의 최댓값, 빈 목록이면 0 |
| `fluent` | 자연스러운 한국어인지 독립 판단한다. 유창한 오역도 true일 수 있다. 단순 다/습니다 차이를 문법 오류로 취급하지 않는다 |
| `contrastPreserved` | `input.pair`가 있으면 해당 변형의 의미 차이를 번역이 보존했는지 boolean. 일반 문단은 null. 같은 모델의 대립쌍 양쪽이 true인 경우를 집계에서 별도로 센다 |
| `errors` | `{severity:1\|2\|3, category, sourceSpan, targetSpan, reasonKo}`. sourceSpan은 원문/문맥의 실제 연속 구절, targetSpan은 출력의 실제 구절. 생략(omission)만 targetSpan `""` 허용 |
| `termChecks` | `input.canonicalTermChecks`의 ID를 같은 순서로 빠짐없이 한 번씩 `{id,correctSense:boolean,canonicalForm:boolean,reasonKo}`로 작성. 목록이 없으면 `[]`. 의미와 정규 표기는 독립 판단 |

오류 category는 `semantic_role`, `negation_condition`, `quantity_formula`, `word_sense`, `discourse_reference`, `omission`, `unsupported_addition`, `terminology`, `modality`, `fluency`, `other` 중 하나다. 오류 범위는 관찰 근거이며 원문과 출력의 논리적 대응이 맞는지는 도우미가 직접 판단한다. 코드의 부분 문자열 검사가 그 판단을 보증하지 않는다. `canonicalForm`은 고정 정규 용어의 공백·문장부호 차이만 정규화해 비교하고, `correctSense`는 해당 원문 출현의 뜻과 대상이 맞는지 판단한다. 두 값의 AND를 joint로 별도 집계한다. 행 전체에 맞는 용어가 등장했다는 이유로 다른 출현의 오용까지 정답으로 처리하지 않는다.

013의 March/April→3월/4월은 자연스러운 날짜 표기다. 기존 숫자 토큰 경고를 유지하되 새로운 수량을 추가한 오류로 자동 판정하지 않는다. %, 퍼센트포인트, 기준 값·증가분, 수식 피연산자의 관계는 문자열 보존과 따로 대조한다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/summarize_linguistic_reviews.py --prepared .training/comparisons/<run>/prepared-review --reviews .training/comparisons/<run>/review-a.jsonl .training/comparisons/<run>/review-b.jsonl .training/comparisons/<run>/review-c.jsonl --output .training/comparisons/<run>/assistant-review-summary.json
```

집계기는 18개 원문·모든 후보의 36/54/72개 판단·출력 해시·모든 용어 판단의 완전성을 먼저 확인한 뒤에만 키를 읽는다. 모델별 중요 오류 행 수(severity ≥ 2), 영역별/원문 범주별 수치, 오류 범주별 행 수·최대 심각도, 대립쌍 양쪽 보존, 용어의 의미/표기/joint를 각각 남긴다. 최악 오류 범주는 중요 오류 행 수 → 최대 심각도 → 전체 오류 행 수 순으로 비교하며 동률을 모두 표시한다. 통과/실패나 모델 승격 결정을 새로 만들지 않는다.

입력·준비 packet·검토·키·생산자의 predictions/summary 바이트를 해시로 결속하고 마지막에 다시 확인한다. 모델 원시 실행 metadata와 그 canonical SHA를 보존하지만 모델 가중치를 다시 읽거나 추론하지 않는다. 결과는 새 파일에만 기록한다. 대립쌍은 서로 의존하는 관측이며 일반 영역도 2개뿐이므로 이 작은 개발 자료에서 번역의 최저 품질이나 일반 번역 회귀 부재를 보증할 수 없다.

생산자 완료 기록은 형식을 명시적으로 구분한다. 버전 없는 기존 Hy baseline과 `version=hymt30-development-screen-v1`, `hymt30-development-screen-v2`, `hymt30-development-screen-v3`는 최상위 `completed=18`과 입력 정체성을 사용한다. `version=translategemma-large-screen-v1`, `translategemma-large-screen-v2`, `translategemma-large-screen-v3`는 `count=recordedCount=expectedCount=18`, 중첩 `input`의 동일한 입력 정체성, `integrityVerified=true`, `childProcessStopped=true`, predictions SHA를 모두 확인한다. 준비 단계에서도 같은 정규화·입력·개별 완료 검사를 먼저 수행한다. 각 v2는 해당 v1과 같은 검증 경로를 사용하며, 버전 허용으로 점수나 품질 기준을 바꾸지 않는다. 원시 summary를 고치지 않으며 정규화한 schema 이름과 원본 전체 metadata를 결과에 남긴다. 알 수 없는 version이나 섞인 형식을 기존 baseline으로 조용히 간주하지 않는다.

v3는 추가로 소유 자식 종료·일시정지 상태 생성/메모리 제한 기록·관측 샘플·오류 없는 메모리 감시·모델/설치/코드/출력/샘플 해시의 완료 metadata를 확인한다. 이 검사는 기록의 계약 검사이며 실제 RAM 사용을 다시 측정하거나 품질을 인증하지 않는다. Hy30의 `outputIntegrityPassed`가 false인 경우도 원시 진단을 보존하며 의미 판단으로 바꾸지 않는다. 새 준비 manifest의 `reviewCodeFiles`는 준비기·집계기·v3 증거 helper의 바이트에 결속되고, 집계 전후 변경을 거부한다. 이미 검토한 번역을 추가 모델과 다시 비교할 때는 새 패킷을 만들며, 같은 검토자가 문장을 기억할 수 있다는 맹검 한계를 별도로 밝힌다.
