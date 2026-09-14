# 로컬 독립 한국어 질문 답변 절차 v1

정책 ID `input-execution-v1-local-question-review-protocol-v1`. S1의 격리 가능한 답변자 및 모델·배정 차이 기록 조건을 실제 로컬 native context로 충족하기 위한 명시 경로다. 기존 QUESTION_REVIEW_PROTOCOL과 spawn용 도구는 보존한다. agent thread limit으로 새 context 생성이 거부된 사실을 실제 spawn 성공으로 표시하지 않는다.

S4의 원래 실패와 회복 실행, 전체 64개 완료, 소유 종료, 최종 무결성 및 정식 S5 packet 검증이 먼저 끝나야 한다. 활성 Hy7 또는 다른 무거운 로컬 모델이 있으면 Qwen을 시작하지 않는다. 실제 실행은 별도 동결한 local runner/contract의 자원·소유권·전원·종료 조건을 따른다.

## 고정 배정과 입력 격리

R001~R064의 기존 고정 해시 순서를 그대로 사용한다. 모든 64개 질문 답변에는 같은 Qwen3.5-9B Q4_K_M 모델과 같은 동결된 QA 설정을 사용한다. 모델은 Unsloth `Qwen3.5-9B-GGUF`, revision `3885219b6810b007914f3a7950a8d1b469d598a5`, GGUF SHA `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8`이며 실제 runtime은 llama.cpp b10888 / commit `72797e89198ab564fd0e6baa54ab196e8dd1d884`다. 실제 파일 해시와 양자화판의 provenance 한계를 기록한다. 이 답변자를 Codex 또는 사람 검수자로 기록하지 않는다.

컨트롤러는 정식 S5를 검증한 뒤 question-only export를 만든다. 모델 runner는 그 export와 해시 manifest만 읽는다. 모델에 전달하는 내용은 reviewId·대상 한국어 translation·q1/q2의 questionId/questionKo와 아래 고정 절차·출력 schema뿐이다. 원문·정답·구성·domain·자료 제목·core 여부·원래 ID·경로·이웃/최소대립쌍·다른 후보·자료 작성 대화·과거 답변을 넣지 않는다. retrieval·도구·파일 접근·이전 대화 message를 모델에 제공하지 않는다. source용 private bundle을 native runner가 읽지 않는다.

각 packet은 **새로 시작한 소유 native 프로세스의 단 한 번 completion**에서만 답한다. 완료 응답을 보존하고 해당 프로세스의 종료를 확인한 뒤 다음 프로세스를 시작한다. 모델 가중치를 공유한다는 사실과 요청 context를 공유한다는 사실을 구분한다. PID는 재사용될 수 있으므로 실제 PID와 process creation FILETIME, UUID contextId, packet/request/raw-response 해시를 함께 결합한다. 존재하지 않은 프로세스나 미실행 packet에 fresh context 완료 기록을 만들지 않는다.

모든 요청은 기존 Qwen runtime 호환 설정인 `cache_prompt=true`와 명시 `n_keep=0`을 사용한다. 이는 같은 프로세스에서 이전 prompt가 있으면 재사용할 수 있는 설정이다. 이번 경로의 격리 증거는 **완전히 새 소유 프로세스에 단 한 번 completion**을 수행한다는 사실과, 그 첫 completion의 실제 `timings.cache_n=0`, `timings.prompt_n=현재 검증된 prompt token 수`다. 이전 process의 slot/cache/state를 전달하지 않는다. `n_keep=0`만으로 초기화를 입증했다고 표시하지 않으며, 응답 뒤 slot의 token 수인 `tokens_cached`를 재사용 0 검사에 쓰지 않는다. 실제 prompt·template·tokenize 일치와 context/output 예산을 확인하며 초과 입력을 자동으로 잘라 통과시키지 않는다.

## 모델에 전달하는 답변 절차

제공된 한국어 번역만 읽고 질문 두 개에 각각 답한다. 질문이 암시하는 내용이나 사전 지식으로 번역의 빈 부분을 보충하지 않는다. 번역만으로 판단할 수 없으면 판단 불가와 그 이유를 적는다. 각 답변에는 번역에 근거한 이유와 번역의 정확한 인용을 넣는다. 두 질문 외에 새 질문·해설·원문 추정·번역 교정을 작성하지 않는다.

native 출력은 reviewId와 answers.q1/answers.q2 두 객체의 UTF-8 JSON이다. 질문 ID는 q1/q2 객체 키로 표현하며 각 답변 객체에는 answerKo, reasonKo, translationEvidence와 명시 cannotDetermine 상태가 있다. q1/q2와 reviewId는 받은 packet과 정확히 같아야 한다. 수집기는 q1/q2 키를 동일한 questionId의 두 행으로 구조만 변환하고 값·인용·답변을 수정하지 않는다. `cannotDetermine=true`인 경우만 빈 인용 목록을 허용한다. 값이 false이면 하나 이상의 실제 번역 인용이 필요하다.

JSON grammar의 인용 선택지는 해당 packet 번역에서 정확히 추출한 문장 또는 전체 번역 문자열만으로 구성할 수 있다. 정답·원문·다른 packet의 내용이나 평가자가 만든 근거를 선택지에 넣지 않는다. 어떤 인용을 쓸지는 모델이 선택한다. grammar는 출력 형식과 정확 인용 범위만 제한하며 정답·의미 판단을 고정하지 않는다.

이번 v1은 형식 재요청을 하지 않는다. 빈 응답·잘림·잘못된 schema/ID·근거 인용 불일치·격리/자원 실패가 있으면 원시 출력과 실패·시도 수를 보존하고 중단한다. 자동 재시도·best-of 선택·root 답변 교정·정답을 넣은 재생성을 하지 않는다. 판단 불가를 문법 실패로 취급하여 강제로 내용 있는 답변을 만들지 않는다.

## 수집·동결·채점

컨트롤러의 local transport는 64개 원본 packet·실제 독립 프로세스·intent/request/response·최종 종료·설정·context ID·모델/runtime 해시를 재검증하고 정확 인용을 Unicode codepoint 구간으로 변환한다. transport는 답변/이유/정답 여부를 수정하지 않는다. 원본 raw 응답과 추출 답변·영수증의 해시를 모두 보존한다. 별도 명시 local 평가 경로가 이를 다시 검증하여 전체 64개 답변을 먼저 동결한다.

동결 뒤 별도 원문 채점자가 답변·근거를 원문/정답과 대조한다. 답변 native context와 원문 채점자는 서로 다른 정체성이다. 채점 결과에 맞춰 답변을 변경하거나 QA 모델·질문 지시를 조정하지 않는다. 기존 S1 핵심/전체 질문 분모와 unresolved 처리 및 평가 기준을 그대로 적용한다.

모델 변경은 평가 방법의 차이로 보고하며 같은 64개 내에서는 동일 조건을 유지한다. Qwen의 실제 한국어 독해 능력은 이번 답변을 생성했다는 이유만으로 검증됐다고 주장하지 않는다. 결과는 도우미 모델의 번역 독해 시험이며 사람의 학습 효과, 인간 독립 검수, 사전학습 데이터 미노출 또는 일반적 정확도 인증이 아니다. 실제 64개 격리·답변·최종 무결성을 검증하지 못하면 질문 평가는 미완료다. 원문 검토나 root 대체 답변으로 보충하지 않는다.

## 컨트롤러의 평가·원장 명령

아래 명령은 모델에게 전달하는 답변 절차가 아니다. 검증된 실제 경로로 인자를 바꾸며 결과는 Git 제외 `.training/quality-evaluation/input-preparation-v1/`의 새 파일·폴더에 저장한다. runner/transport의 실제 64개 원본 검증이 먼저 완료되어야 한다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_evaluation_v1.py freeze-answers --folder <formal-s5> --collection <local-collection>
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_evaluation_v1.py report --folder <formal-s5> --source-reviews <confirmed-source64.json> --grades <actual-grades64.json> --output <new-local-report.json>
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_append_evidence_v1.py --folder <formal-s5> --cohort <recovery-cohort> --source-reviews <confirmed-source64.json> --grades <actual-grades64.json> --report <new-local-report.json> --relations <validated-recovery-relations-v2> --destination <new-ledger-evidence>
```

기존 정식 packet과 recovery-v2 bundle은 유지한다. 답변 동결 영수증은 별도 local version과 protocol/code/collection 해시를 기록하며 spawn용 동결 경로로 읽지 않는다. 원장 연결은 기본 준비만 수행하고 명시 `--append`에서만 기존 검증·보존·멱등 writer를 사용한다. 입력 관측 64개와 관계 관측 64개는 기존 failed 실행 38개와 회복 실행 26개의 실제 provenance를 유지한다. 자동 코드 검증·실제 모델 실행·질문 답변 완료·원문 채점 완료·품질 통과·실제 원장 추가를 구분해서 보고한다.
