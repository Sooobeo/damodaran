# 금융24 한국어 전용 질문 평가 준비·동결·집계

이 도구는 고정 질문 v2와 **완료한 한 모델·한 구성·한 집합**의 실제 생성 증거를 연결한다. dev18은 36질문/핵심18, reading6은 12질문/핵심6이며 따로 준비하고 따로 판정한다. 전체 48질문을 합쳐 어느 집합의 실패를 가리지 않는다. 이름의 금융24에는 원래 dev18의 일반 문장도 포함하며 표본을 새로 선별하지 않는다.

이번 작업은 helper와 합성 테스트만 구현했다. 실제 새 답변·판정·점수는 작성하지 않았으며 TG27 v5의 실제 18+6 실행도 아직 남아 있다. 과거 Hy7 번역은 질문 v2보다 먼저 생성되어 질문 사전 등록 실험이 아니다. 무버전 Hy7 결과는 이 helper의 지원 밖이며, 기존 결과·의미판정·general16 helper는 변경하지 않는다.

## 고정 입력과 수용 조건

| 입력 | 경로 / SHA-256 |
|---|---|
| 원문24 | `.training/quality-evaluation/finance-answerability/source-only-v1/source-only.jsonl` / `2ed90d5a9f6250020ba0e73c1fae5a77ecd9f63032badf9ddee7b9a1668432bb` |
| 질문 v2 | 같은 폴더의 `assistant-questionnaire-v2.json` / `a946103f3068b64e54107f4b96a01834857b146d2190e7b5f5e009b9cef1d75b` |
| 제품 정책 | `content/model-comparison/LEARNING_READINESS_BASELINE_20260911.json` / `18aca6dd384b02a0c83e81b1d644961422ed7ec034abdb1b8c92394e0e2c8928` |

[학습 준비 정책](../../content/model-comparison/LEARNING_READINESS_BASELINE_20260911.md)에 따라 dev18은 **35/36 이상·핵심18/18**, reading6은 **12/12·핵심6/6**을 모두 충족해야 한다. 누락 답변은 분모에 남고 오답이며, 누락 판정 파일은 집계를 거부한다. 판정자의 미확정과 답변 상태·정답 판정 충돌은 통과할 수 없다. 답변자의 불확실한 상태는 원래대로 별도 집계하며, 원문 대조 판정자가 명확한 오답/답변 불가로 확정하면 `incorrect` 0점으로 판정할 수 있다. 0분모는 `not_evaluated`이다. 질문 결함을 찾으면 기존 질문과 분모를 유지한 보류 판정을 남기고 별도 새 질문 버전을 설계해야 한다.

## 생성 증거 검증

`--producer-version`은 생략할 수 없고 다음 세 스키마만 허용한다.

- `translategemma-large-screen-v5`: [v5 증거 검사](V5_REVIEW_ADAPTERS.md)의 guard·CPU/메모리 telemetry·소유 종료·원시 응답 해시·토큰/EOG·고정 sampling 전체 계약을 그대로 호출한다.
- `translategemma-large-screen-v4`: 기존 v4 검사를 그대로 적용한 뒤 완전한 EOG·잘림 없음·출력 토큰 SHA·모델/입력/코드/원시 prompt 정체성을 확인한다.
- `hymt30-development-screen-v4`: 기존 v4의 sampling normalization·원시 출력/메모리 SHA 계약을 유지한다. 이 스키마의 `applicationChanged:false`, 각 결과의 `postProcessingApplied:false`·무결성·미검수 상태를 확인한다. TG의 나중에 추가된 summary 필드를 과거 Hy30에 만들어 넣지 않는다.

각 집합의 원래 입력·manifest와 원문24 projection의 ID·원문·문맥 SHA가 같아야 한다. 완료 개수·중복/누락·target SHA·원시 응답·실행 코드 해시, 고정 모델 SHA와 설치 manifest·runtime/template 정체성도 확인한다. 실제 보존한 모델 크기·revision 한계를 private metadata에 그대로 남긴다. 임의 producer 폴더 Python을 import/실행하거나 generic `status:completed`를 우회 허용하지 않는다. 코드가 바뀌어 과거 증거와 달라지면 거부하며 새 adapter 검토가 필요하다.

모델 가중치와 native runtime을 다시 읽거나 기동하지 않는다. 확인 대상은 보존한 실행 증거와 작은 설치/코드 파일이며, 과거 물리 실행을 재측정하거나 서명된 hardware 증거로 보장하지 않는다. 생성시 숫자·용어 자동검사 실패는 표본 제외 조건이 아니다. 생성 완료/무결성 실패는 준비를 거부한다. 기록된 시작시각과 질문 작성시각으로 `questionChronology`를 보존하며, 과거 실행을 새 독립 시험으로 표시하지 않는다.

## 준비와 답변자 분리

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/prepare_finance_answerability.py --run $tg27V5Dev --cohort dev18 --producer-version translategemma-large-screen-v5 --output $newDevPacket
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/prepare_finance_answerability.py --run $tg27V5Reading --cohort reading6 --producer-version translategemma-large-screen-v5 --output $newReadingPacket
```

위 변수는 실제 완료한 run과 `.training/quality-evaluation/finance-answerability/` 아래 새 출력 폴더로 지정한다. 같은 출력 폴더를 재사용하지 않는다. 준비 결과는 다음 세 파일이다.

| 파일 | 받을 사람/세션 |
|---|---|
| `answerer/packet.jsonl` | 해당 packet만 읽는 새 답변자 |
| `private/private-key.json` | 답변 동결 뒤 원문 근거로 채점하는 독립 판정자 |
| `private/manifest.json` | coordinator의 원시 run·모델/코드/설치·packet 해시 관리 |

답변자는 **`answerer/packet.jsonl` 하나만** 받는다. 그 파일의 각 행은 무작위 `questionId`, `questionKo`, `candidateTranslationKo`, `learningContextKo` 네 문자열만 가진다. 원문 ID·영어 원문/영어 문맥·참조·모델 정체성·핵심 표시·모범답안·채점 기준은 포함하지 않는다. 질문 v2의 `learningContextKo`는 24행 모두 빈 문자열이므로 임의 설명·번역한 영어 문맥을 추가하지 않는다. 생성된 한국어에 남은 원래 약어·수식은 원문 번역 결과 그대로이며 새 정답 힌트를 덧붙이지 않는다.

답변에는 실제 읽은 한국어에서의 근거 구절을 요구하고 선행 금융 지식으로 빈 내용을 보충하지 않는다. `private/`라는 이름은 OS 접근권한 경계가 아니다. coordinator가 파일 하나만 전달하고 원문/키/기존 판단을 본 질문 작성자·준비자가 답변자가 되지 않도록 관리한다. 각 packet에 새 `forkTurns:none` 세션을 쓰며 세션 사이 다른 후보 출력과 판정을 공유하지 않는다. 이 도구는 답변자나 모델 호출을 자동으로 시작하지 않는다.

답변 JSON은 `version:finance24-korean-answers-v1`, 읽은 파일의 `packetSha256`, `provenance`, `answers`를 가진다. `provenance`에는 실제 `answererId`, `sessionId`와 `newSession:true`, `forkTurns:none`, `inputScope:packet_only`, `sourceOrKeyPreviouslyViewed:false`, `otherCandidateOutputsViewed:false`, `priorJudgmentsViewed:false`, `isQuestionAuthorOrPacketPreparer:false`가 필요하다. 이는 실제 이력을 coordinator가 확인해 기록하는 진술이지 세션 이력의 독립 증명은 아니다.

각 답변 행의 필드는 `questionId`, 한국어 `answerKo`, 번역에서 그대로 인용한 `targetEvidenceQuotes` 배열, 허용 한국어 문맥의 `contextEvidenceQuotes` 배열, `status`, 한국어 `uncertaintyKo`이다. `status`는 `answered`, `insufficient_information`, `ambiguous`, `unresolved` 중 하나다. `answered`에는 비어 있지 않은 한국어 답과 target 인용이 필요하고 나머지 상태에는 보류/답변 불가 이유가 필요하다. 빈 한국어 문맥에 대한 인용은 항상 빈 배열이다. 준비 도구는 답변 또는 작성된 provenance를 미리 채우지 않는다.

## 답변 동결 뒤 별도 수동 판정

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/grade_finance_answerability.py freeze-answers --packet-directory $newDevPacket --answers $devAnswers --output $newDevFreeze
```

coordinator는 위 동결 영수증이 생성된 뒤에만 새 판정 파일을 작성하도록 한다. 영수증에는 실제 답변 바이트 SHA, packet/키/manifest/정책 해시, 시각, 무작위 nonce가 담긴다. 답변과 영수증은 답변자에게 새 읽기 자료로 되돌려 주지 않는다.

독립 판정자는 답변자와 다른 ID·세션이어야 한다. 원문·문맥의 명제를 먼저 읽고 private key의 허용 변형/필수 사실에 근거해 답변을 `correct`, `incorrect`, `unresolved`로 수동 판정한다. 동의 표현을 허용하고 단어 일치나 모범답안 exact match로 채점하지 않는다. 질문 결함·해석 불일치는 `unresolved`와 사유로 남긴다.

판정 JSON은 `version:finance24-answerability-judgments-v1`, `packetSha256`, `privateKeySha256`, `answersSha256`, **실제 동결 파일의** `freezeReceiptSha256`, UTC `createdAtUtc`, `judge`, `judgments`를 가진다. `judge`는 실제 `id`, `sessionId`, `kind:assistant|human`, `sourceGroundedReview:true`, `automaticExactMatch:false`, `independentOfAnswerer:true`를 기록한다. 각 판정은 `questionId`, `verdict`, 한국어 `reasonKo`, 원문 `sourceEvidenceQuotes`와 문맥 `contextEvidenceQuotes` 배열을 포함하며 모든 질문을 한 번씩 판정해야 한다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/grade_finance_answerability.py grade --packet-directory $newDevPacket --answers $devAnswers --freeze-receipt $newDevFreeze --judgments $devJudgments --output $newDevGrade
```

모든 답변·영수증·판정·결과 경로는 같은 finance-answerability 경계 안의 파일을 쓴다. 채점 때 원래 producer 증거 전체를 다시 확인한다. 답변을 동결 뒤 수정하거나, 영수증과 다른 해시를 판정에 연결하거나, 판정 파일의 수정시각/작성시각이 동결보다 앞서면 거부한다. 로컬 파일 순서와 nonce 연결은 독립 timestamp 공증이 아니다. 근거 인용 검사는 실제 부분문자열 존재만 검증하며 의미 판정의 진실성을 자동 보장하지 않는다.

채점 결과는 해당 cohort 질문 게이트만 계산한다. 의미0오류·용어95%·전체 학습 준비·새 독립 시험·인간 학습 효과는 `not_evaluated` 또는 `false`로 유지한다. 모델 등록이나 앱 설정 변경은 없다.

## 합성 검증

```powershell
.venv-training\Scripts\python.exe -B -X utf8 -m unittest discover -s scripts/model-comparison -p test_finance_answerability.py
```

임시 폴더의 합성 dev18/reading6, 가상 numeric telemetry·원시 응답·수동 판정만 사용한다. 실제 adapter 연결, 공개 packet의 필드/정보 유출, 완료/모델/입력/원시 해시 거부, 새 답변자·독립 판정자, 동결 이후 순서, 누락·미확정·35/36·12/12·핵심100%, 변조/덮어쓰기 거부를 확인한다. 2026-09-11 실행에서 **13개 통과(61.783초)**, CLI help 두 개와 고정 원문24/질문48/핵심24/빈 한국어 문맥24행의 해시·구조를 확인했다. 모델 실행·실제 번역 품질 검증과 구분한다.
