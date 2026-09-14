# S5 평가 입력과 S6 개발 선택

`evaluation.py`는 S4의 원시 출력64개가 완료된 뒤 사용하는 별도 도구다. 모델을 호출하거나 번역을 고치지 않는다. S1 원문·평가 동결, S2 입력64개와 코드/산출물 해시, S4 원문/프롬프트/번역/원시 응답 해시·실제 토큰 목록·기술 검사를 검증한다. `predictions.jsonl`이 같은 run의 출력인지 확인하고, 종료된 `summary.json`의64개 완료·누락0·소유 프로세스 종료·최종 무결성과 전체 산출물 해시가 일치해야 한다. 기술 실패나 미완료가 있으면 평가 packet을 만들지 않는다. 생성기와 같은 프로세스에서 평가 로더를 import해 사용하지 않는다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/input_execution_v1/evaluation.py prepare --outputs <새S4출력.jsonl> --destination .training/quality-evaluation/input-preparation-v1/s5-dev-20260913
```

평가 폴더는 새 경로여야 한다. 원문이 들어간 산출물은 `.training/`에만 보관한다. 기존 산출물을 덮어쓰지 않는다.

## 질문 답변 격리

`question-packets/R001.json`부터64개를 각각 새 답변 context에 하나씩 전달한다. 순서는 S1의 `input-preparation-v1-review-20260913`과 원문 ID/구성의 SHA-256 정렬로 고정한다. 익명 ID는 평가 내용이나 구성에 대한 힌트가 아니다.

각 packet은 **reviewId, 한국어 translation, q1/q2의 questionKo**만 가진다. 답변자에게 `private/`, `source-packets/`, 다른 packet, 영어 원문, 정답, 자료 작성 대화, 인접 문단이나 최소대립쌍을 주지 않는다. `fork_turns=none`인 별도 agent를 한 packet에만 쓰며 같은 agent를 다른 단위/후보에 재사용하지 않는다. 원래 평가 질문 ID·core 여부·domain·구성·출처·제목도 전달하지 않는다.

답변자는 한국어 번역의 근거를 인용해 질문2개에 답한다. 판단할 수 없으면 그 사실과 이유를 적는다. root가 정답을 대신 쓰지 않는다. 다음 필드는 실제 호출/배정 기록으로 transport에서 채울 수 있지만, 독립성이나 모델명을 추정해 채우면 안 된다.

```json
{
  "reviewId": "R001",
  "packetSha256": "packet 파일의 SHA-256",
  "humanReviewed": false,
  "reviewer": {
    "actorId": "실제 독립 context ID",
    "model": "실제 모델 또는 알려진 모델 식별값",
    "freshContext": true,
    "priorTaskExposure": false,
    "allowedExposures": ["packet_translation", "packet_questions"]
  },
  "answers": [
    {"questionId": "q1", "answerKo": "실제 답변", "reasonKo": "답변 근거",
     "translationEvidence": [{"start": 0, "end": 2, "text": "정확"}]},
    {"questionId": "q2", "answerKo": "실제 답변", "reasonKo": "답변 근거",
     "translationEvidence": [{"start": 0, "end": 2, "text": "정확"}]}
  ]
}
```

모든 evidence offset은 **Unicode codepoint의 반개구간 [start,end)**이다. 브라우저 UTF-16 offset과 다르다. `cannotDetermine=true`인 답변만 빈 번역 근거 목록을 허용한다. 빈 답변이나 이유는 허용하지 않는다. 모르는 답변을 정답으로 만들지 않는다.

64개 응답을 JSON 배열 또는 `{"rows":[...]}`에 모아 다음을 실행한다. 응답자64개·질문128개를 검증하고 답변 내용/해시를 동결한 뒤에만 질문 채점용 packet을 만든다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/input_execution_v1/evaluation.py freeze-answers --folder .training/quality-evaluation/input-preparation-v1/s5-dev-20260913 --answers <실제응답64.json>
```

## 원문 대조와 질문 채점

별도 원문 평가자는 `source-packets/RNNN.json`의 전체 원문·번역·S1 명제/용어 주석을 대조한다. 각 후보를 따로 읽거나 원문별4후보를 함께 비교할 수 있다. 구조화된 입력은 다음과 같다.

- 공통: `reviewId`, `sourceSha256`, `translationSha256`, `humanReviewed:false`, `reviewer:{actorId,model}`.
- `fullText`: `reviewedAllText:true`, `severity`는 neutral/minor/major/critical/unresolved, `reasonKo`, `sourceEvidence`, `translationEvidence`, `issues`.
- 오류별 `issues`: 고유 `issueId`, 명시적인 `sourceErrorId`, `severity`는 minor/major/critical, 일반 오류 `category`, `reasonKo`, 양쪽 evidence. 누락 오류는 `omission:true`와 빈 `translationEvidence`를 허용한다. 전체 severity는 실제 오류 중 최고 심각도와 일치해야 한다. 보류일 때만 unresolved를 사용한다.
- `propositions`: 고정 명제 ID마다 `id`, `verdict` preserved/damaged/unresolved, `reasonKo`, 양쪽 evidence. 누락은 위와 같이 명시한다.
- `terms`: 고정 출현 ID마다 `id`, `meaningCorrect` true/false/null, `renderingAcceptable` true/false/null, `reasonKo`, 양쪽 evidence. 원문 근거는 해당 출현의 위치를 포함해야 한다. 동의어를 정확 문자열 일치로 대신 판정하지 않는다.
- `naturalness`: `score` 1/2/3/null, `reasonKo`, `translationEvidence`. 의미 점수와 합치지 않는다.

모든 명제/용어 항목을 실제로 판정한다. 판단 보류는 null/unresolved로 남긴다. 명제3개가 맞아도 전체 문단의 다른 중요 오류를 면제하지 않는다. `source-packets`의 표현과 오류 없는 템플릿을 자동 복사해 성공 판정을 만들지 않는다.

`sourceErrorId`는 원문 평가자가 같은 원문의 후보4개를 실제 의미 대조한 뒤 **동일한 거짓 명제·관계 변화·누락 오류라고 판정한 경우에만** 공유하는 ID다. 출력별로 다른 ID를 억지로 붙이는 용도도, 문장이 비슷하다는 이유로 같은 ID를 만드는 용도도 아니다. `reasonKo`에 어떤 원문 의미가 어떻게 틀렸는지 쓴다. 한국어 문자열 일치·category 일치·근거 구간 겹침으로 동일 오류를 자동 추정하지 않는다. 다른 오류에는 다른 ID를 부여한다. 같은 sourceErrorId라도 category/원문 근거가 연결되지 않거나 심각도가 올라가면 기존 오류 유지로 인정하지 않는다. 일반 문장에서는 최고 severity가 minor로 같아도 새로운 minor 의미 오류가 생기면 회귀를 거부한다. 중요 오류도 major/critical 최고 등급과 오류 수가 같다는 이유로 다른 새 오류를 면제하지 않는다.

질문 채점자는 `grade-packets/RNNN.json`에 있는 **이미 동결된 답변**을 원문/S1 정답과 대조한다. 답변자와 다른 actor여야 한다. 채점 입력은 공통 식별/해시/검토자 필드와 `answerSha256`, `questions:[{questionId,verdict,reasonKo,sourceEvidence,answerEvidence}]`다. verdict는 correct/incorrect/unresolved이며, answerEvidence는 답변 자체에서 인용한다. 원문 근거는 해당 질문의 고정 근거 구간과 연결해야 한다. `humanReviewed:false`를 유지한다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/input_execution_v1/evaluation.py report --folder .training/quality-evaluation/input-preparation-v1/s5-dev-20260913 --source-reviews <실제원문검토64.json> --grades <실제질문채점64.json> --output <새보고서.json>
```

원문 검토나 질문 채점이 없으면 생략할 수 있으나 미완료 보고서만 나온다. 빈/보류 항목은 정답 분자에 포함하지 않으며 전체 분모192명제·156용어·128질문(핵심64)을 유지한다. 0분모는100%가 아니다. 실제 학습 효과나 사람 검수로 표시하지 않는다.

## 생성 중 잠정 원문 검토

S4 생성 동안 이미 완료된 한 단위의 C0~C3 네 출력을 **잠정 원문 검토**로 먼저 읽을 수 있다. 이 단계는 전체 실행 성공이나 정식 S5 완료가 아니다. `evaluation.py prepare`의 전체64·종료·무결성 조건을 우회하지 않는다. 점수 집계·질문 답변·후보 선택을 먼저 하지 않으며 초안 결과를 생성기나 입력 변경에 전달하지 않는다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/input_execution_v1/provisional_reviews.py --run .training/comparisons/input-preparation-v1/s4-generation/attempt-001 --unit IP1-F01 --destination .training/quality-evaluation/input-preparation-v1/provisional-source-20260913/IP1-F01
```

별도 프로세스가 완료된 `outputs/<id>-<config>.json` 네 개, 고정 S1 원문/평가, S2 입력, 실행 plan, 원시 응답·요청·HTTP receipt를 읽기 전용으로 확인한다. 같은 runtime 검증 함수를 다시 적용하며 모델/서버에 요청하지 않는다. 전체64의 고정 seed 정렬에서 계산한 익명 `RNNN`으로 source packet만 만든다. packet 바이트는 향후 정식 source packet과 같아야 하므로 잠정 표시·구성 이름을 packet에 덧붙이지 않는다.

검토자에게는 `source-packets/`의 해당 네 packet만 제공하고 구성 매핑이 있는 `private/manifest.json`을 주지 않는다. 초안 답변 파일은 잠정 폴더에 두고 정식 review 입력으로 자동 사용하지 않는다. private manifest에는 provisional=true, 당시 output/원시 응답/plan 해시, 전체64 검증 전 확정 금지와 모델 호출0을 기록한다. 활성 S4 실행 폴더·프로그램·고정 입력을 쓰거나 수정하지 않는다. 공개 원문과 인용을 포함한 파일은 Git 제외 `.training/quality-evaluation/input-preparation-v1/` 아래에만 쓴다.

전체64 생성과 종료가 완료되고 기존 `evaluation.py prepare`가 성공한 뒤 다음 대조를 수행한다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/input_execution_v1/provisional_reviews.py --verify-promotion .training/quality-evaluation/input-preparation-v1/provisional-source-20260913/IP1-F01 --final-folder .training/quality-evaluation/input-preparation-v1/s5-dev-20260913 --receipt .training/quality-evaluation/input-preparation-v1/s5-dev-20260913/provisional-IP1-F01-identity.json
```

이 검사는 최종64 gate와 당시 근거 파일 해시를 다시 확인하고 잠정/정식 packet 바이트가 모두 같을 때만 승격 가능한 정체성 영수증을 만든다. source 검토 자체를 자동 승인하지 않는다. 실제 평가자가 초안과 정식 packet을 대조해 확인한 뒤에만 정식 source review로 사용한다. 실행 실패·미완료 또는 해시 불일치이면 초안은 잠정 기록으로 남기며 정식 집계에 포함하지 않는다. 질문 packet/새64 context 답변/동결/원문 채점 순서는 변경하지 않는다.

## 선택·등록·원장 경계

S6 개발 선택은 [S1 계약5절](../../../content/model-comparison/input-preparation-v1/CONTRACT.md#5-추론-전에-고정하는-평가)을 그대로 집계한다. 새 중요 오류, 새 핵심 명제 손상, 일반 단위의 의미/질문/용어 회귀를 거부한다. 금융 minor/질문/용어 하락도 비교표에 남기되 계약에 없는 확대 거부 조건을 추가하지 않는다. 보류가 하나라도 남으면 후보 선택을 보류한다.

개선 조건은 중요 오류 단위 수 감소 또는 중요 오류 단위 증가 없이 핵심 명제 순증이다. 복수 후보는 중요 오류 단위 수→핵심 명제→핵심 질문→전체 질문→용어→자연스러움→총 입력 토큰→C1,C2,C3 순서다. 개선이 없으면 현재 등록을 유지한다. 개발 선택만으로 등록을 승인하거나80개 독립 평가를 완료했다고 하지 않는다. 독립 holdout 원문/질문은 이 도구가 만들거나 읽지 않는다.

`ledger_observations`는 기존 `append_events`에 전달 가능한 별도 `input_preparation_observation` 사건을 준비할 뿐 쓰지 않는다. 같은 출력의 원문 오류·질문 오답·검사 경고를 새 오류3개로 합산하지 않도록 기존 translation_review와 구분한다. 실제 추가는 원문/보고서 파일 해시를 확인한 root가 수행하며 반복 사건의 내용 해시는 같아야 한다. 모델 등록·운영 DB·개인 기록 변경은 없다.
