# 금융 영한 번역 개선 — 다른 세션 인계

**최신 S5 상태 — 질문9/64개 실제 저장, 남은55개 부분 복구 준비.** 배터리 v4는9개의 단일 호출과 소유 프로세스 종료를 마친 뒤 R010의 시작 메모리 검사에서 중단됐다. 배터리는72%였고, 첫 관측의 여유 메모리가9GiB보다30,420,992바이트 부족했다가 두 번째 관측에서 회복됐다. [부분 복구 v5](content/model-comparison/input-execution-v1/LOCAL_QUESTION_PARTIAL_RECOVERY_V5.md)는 원9개와 원래 failed 실행·claim을 보존하며 R010~R064만 새로 생성한다. 시작 메모리 기준을 유지하고 native 생성 전 최대300초 회복을 기다린다. 현재 복구 구현·검증을 준비 중이며, 전체64개 답변 동결·질문 채점·후보 선택·S6은 아직 미완료다. [실제 중단 감사](.training/verifications/question-battery-partial-stop-audit-20260914.json)를 기준으로 이어간다.

> **최신 작업 기준: 2026-09-14, S4 실제64개 완료, S5 독립 질문9개 보존·남은55개 부분 복구 준비. [28절](#28-공통-입력-처리와-의미-관계-검사-개선-실행-계획)을 먼저 읽는다.** 최초 failed38개와 복구 completed26개를 실제 run ID/응답 그대로 검증했다. 원문64개 검토·관계64개 진단은 완료했다. 질문 시도는 native/호출0회에서 중단됐고 후속 관측에서 AC0을 확인했다. [중단·재개 기록](content/model-comparison/input-execution-v1/QUESTION_POWER_STOP_20260914.md)을 먼저 확인한다. [복구 v3](content/model-comparison/input-execution-v1/LOCAL_QUESTION_ZERO_CALL_RECOVERY_V3.md) 구현·검사·동결 뒤 AC0을 확인했으나, 최신 사용자는 배터리 실행을 명시 승인했다. [v4 전원 정책](content/model-comparison/input-execution-v1/LOCAL_QUESTION_BATTERY_EXECUTION_V4.md)으로 이어가며 잔량20% 이하/미확인 시 중단한다. 질문 답변/채점·후보 선택·S6은 미완료다.
>
> 이전 TG27 엔진 실측은 **2026-09-12의 27절**, 최신 Hy7 S4 실측은 **2026-09-14의 28절**에 있다. TG27 개발 원문18개 대조는 완료했지만 실패 실행의 완료16개와 새17·18번을 단일 성공 실행으로 합치지 않는다. 중요 오류5·경미2·관측 오류 없음5·보류6으로 수용 기준 미충족이다. TG27 읽기6은 저장 결과 없이 중단됐고 재시도 사전검사는 보류됐다. 당시 프로세스 부재·RAM 값은 현재 실행 상태를 뜻하지 않으므로 실행 직전에 다시 확인한다. 1~27절의 시각별 기록은 당시 이력으로 보존한다.
>
> 사용자는 번역과 밑줄 검사 모두95%를 요청한 뒤 언어학·교육학 근거로 수용 기준을 정하도록 위임했다. 최신 기준은 [금융자료 학습 준비 기준](content/model-comparison/LEARNING_READINESS_BASELINE_20260911.md)이다. 후속 요구는 CPU·메모리 최적화, 오류 사례 누적·일반화·주요 원인 분석이다. 22절 작성 당시의 중단 지시 이후 재개와 연결 실패 재시도 승인을 받았다. 1~22절은 해당 시점의 이력으로 보존한다.

작성 시점: **2026-09-11 01:00 KST 전후 / 2026-09-10 16:00 UTC 전후**. 작업 폴더는 `C:\Users\Insun\damodaran`, 셸은 Windows PowerShell이다.

이 문서는 각 시점에 확인한 사실과 다음 작업을 구분한다. **최고 품질 달성, 새 학습 완료, 30B 앱 적용 완료를 뜻하지 않는다.** 최초 인계 작성 때는 새 모델을 실행하지 않았으며 이후 변경은 마지막 절의 상태를 따른다.

## 1. 사용자의 목표와 최신 요구

- 무료 로컬 모델만 사용한다. 유료 API를 사용하지 않는다.
- 번역 속도보다 **번역된 문장의 의미 정확성과 한국어 품질**이 우선이다.
- 금융 용어의 표기 적중 90% 이상은 중간 목표다. 용어만 맞고 문장 뜻이 틀리는 결과는 원하지 않는다.
- 금융 다의어를 조심한다. 금융 자료라는 이유로 모든 `equity`, `interest`, `capital` 등을 금융 의미로 강제하지 않는다.
- 사람이 검수한 별도 번역 자료는 없다. 도우미 작성·대조 자료를 사람 검수본으로 표시하지 않는다.
- 기반 번역 능력을 먼저 확보하고, 그 위에 **문맥에 맞는 용어와 말투를 추가 튜닝**하기를 원한다.
- 이 요구는 반드시 번역을 두 번 호출하라는 뜻이 아니다. 일반 번역 능력과 도메인 적응의 역할을 구분하라는 뜻으로 해석했다.
- 사용자는 필요 없는 메모리를 그때그때 정리하라고 지시했다. 완료·실패한 소유 모델 프로세스를 종료하고 한 번에 무거운 모델 하나만 실행한다. 모델 파일·학습 결과·개인 자료를 삭제하라는 뜻이 아니다.
- 사용자는 메모리 확보를 위해 사용하지 않는 프로그램을 직접 닫을 수 있다고 답했다. 다른 사용자의 앱이나 다른 세션이 띄운 서버를 임의로 종료하지 않는다.

최초 인계 당시 대화(후속 중단·재개 이력은22~27절, 현재 작업 순서는28절):

> 내가 원하는 건 1. 일반적인 번역 2. 1번에 맥락에 맞는 용어 변경, 말투 변경 추가 튜닝인데 왜 계산방향 뒤집는다는 오류가 나오는거야? 모델 설계 점검하고 마저 진행해.
>
> 원시번역에서 왜 오류가 나는거야? 의미상 오류가 나면 안되잖아.
>
> 지금 상황이랑 너가 해야하는 일 전체 정리해서 md로 만들어줘 다른 세션에 투입하게.

**사용자는 생성 문장 수를 품질 성과처럼 보고하는 것을 싫어한다.** 생성 완료, 자동 검사, 원문 의미 대조, 사람 검수, 앱 적용을 구분해서 보고한다. “모델은 원래 틀린다”는 설명으로 요구를 무시하지 말고 실제 입력·실행 설정 문제와 기반 모델의 의미 오류를 구분한다.

## 2. 인계 시점의 실행 상태

| 항목 | 실제 상태 |
|---|---|
| 현재 앱에 등록된 번역 모델 | Hy-MT2 7B Q8, 문맥·조건부 용어 지시 구성. 등록 변경 없음 |
| Hy-MT2 30B v4 개발 자료 | **18/18 생성 완료**, 출력 무결성 검사 통과, 소유 모델 종료 확인. **의미 검수는 아직 안 했다** |
| Hy-MT2 30B 공개 원문 6문단 | **미실행**. 인계 요청 때문에 다음 실행을 시작하지 않았다 |
| TranslateGemma 27B | v3 기능 입력 2건 완료. 개발 18개·공개 원문 6개는 **미실행** |
| TranslateGemma 27B v4 | 시간 한도만 늘린 새 실행기와 37개 검사 준비 완료. **실제 v4 모델 실행은 아직 없다** |
| 무거운 모델 프로세스 | 마지막 Hy30 개발 실행 종료 확인. 인계 시 다음 모델을 시작하지 않음 |
| 절전 방지 보조 프로세스 | phase3 종료 요청 후 `released=true`, 16:00:02 UTC 해제 확인 |
| 운영 DB·기존 번역·검수·메모 | 이번 비교에서 변경하지 않음 |
| 새 가중치 학습·추가 배포 | 이번 대형 모델 비교에서 수행하지 않음 |
| 운영 웹 서버 | 인계 시 3000번 포트의 리스너를 확인하지 못함. 다른 세션의 실행 상태를 먼저 확인하고 중복 시작하지 말 것 |

Hy30 개발 실행은 전체 약 **1,411.937초**, 번역 요청 합계 **1,346.518초**, 모델 로드 **10.875초**였다. 이는 시간 기록이며 품질 점수가 아니다. 종료 후 가용 물리 메모리는 약 15GiB였다. 시작 전에 다시 실측해야 한다.

인계 시 유지할 근거:

- [Hy30 개발 완료 summary](.training/comparisons/linguistic-dev-20260910/hymt30-paging-v4/summary.json)
- summary SHA-256: `220bd71b1370e7de3b432627d87885cb079463bb5b4572dfb0cad59173ffb1aa`
- predictions SHA-256: `3b354d337cff371183f5d8f5a7d5af221407a796e48fa6becb1ec50ee3d421ec`
- `status=completed`, `completed=selectedCount=18`, `outputIntegrityPassed=true`, `invalidOutputIds=[]`, `childProcessStopped=true`.
- 모델 peak 작업 집합 약 7.94GiB. 실행 전체 관측 최저 가용 물리 메모리 601,780,224바이트. 메모리 guard·감시·소유 자식 종료 오류 없음.
- [보조 프로세스 해제 기록](.training/verifications/keep-awake-linguistic-20260911-phase3.json). wrapper PID 36532, 실제 Python PID 44252는 이번 세션의 과거 식별값이다. **다음 세션에서 PID만 보고 종료하지 말 것.**

## 3. “72를 이자율로 나눈다” 오류의 확인된 원인 위치

문제 사례는 `REAL26-005`다. **우리의 v5 미세조정 모델이 아니라, 추가 학습하지 않은 TranslateGemma 12B Q4의 원문 전용 출력**에서 발생했다.

| 원문 관계 | 모델이 실제 출력한 관계 |
|---|---|
| `dividing 72 by the discount or interest rate` | `할인율 또는 이자율을 72로 나누어 계산합니다` |

즉 `72 ÷ 이자율`을 `이자율 ÷ 72`로 바꿨다. 계산기를 실행하다 실패한 것이 아니라, **“A를 B로 나누다”의 두 논항을 바꿔 번역한 기본 의미 오류**다. 숫자와 뒤의 6%→12년, 9%→8년 예시는 그대로 남아 있었다.

실제 기록에서 확인한 내용:

1. 원시 서버 응답의 `content`와 저장한 `translation`이 동일하다.
2. `profile=source-only`, `contextUsed=false`, `glossaryApplied=false`, `postProcessingApplied=false`, `translationMemoryApplied=false`다.
3. 서버 응답에 남은 실제 `prompt`에는 원문 전체가 **정확히 한 번** 포함된다. 이 prompt의 SHA가 prediction의 `promptSha256`와 일치한다.
4. 입력 159토큰, 출력 114토큰, 문맥 한도 2,048, 출력 한도 768이다. `truncated=false`, 원래 종료 토큰 `106`으로 끝났다.
5. `temperature=0`, sampler는 temperature, `ignore_eos=false`, `logit_bias=[]`였다.
6. 숫자 존재 여부 중심의 자동 검사는 통과했다. 이 검사는 피제수·제수의 의미 역할까지 검증하지 못했다.

근거:

- [동결 원문 입력](content/model-comparison/real-reading-check-20260910/sources.jsonl)
- [TG12 원시 응답](.training/comparisons/real-reading-check-20260910/translategemma-12b-v2/REAL26-005-response.json)
- [TG12 저장 출력](.training/comparisons/real-reading-check-20260910/translategemma-12b-v2/predictions.jsonl)
- [TG12 실행 summary](.training/comparisons/real-reading-check-20260910/translategemma-12b-v2/summary.json)
- [코드 줄과 실제 프롬프트 대조를 포함한 설계 감사](.training/verifications/translation-stage-design-audit-20260911.md), SHA `2579e7dc60c4f498da4e8efdcc74bbae2839bd20588d4fd2ffeb3dfcc89f6991`.

따라서 이 사례를 용어 치환, 말투 변경, 우리 가중치 학습, 입력 잘림의 탓으로 설명할 근거는 없다. 다만 **Q4 양자화 영향과 원래 가중치의 오류, 모델 내부 표현 중 무엇이 이 한 건을 유발했는지는 비교 실험하지 않았다.** 특정 내부 원인을 확정하지 않는다. “원시”는 수정 전이라는 뜻이며 정확성 보증이라는 뜻이 아니다.

## 4. 현재 모델 설계에 대한 판단

### 4.1 현재 Hy7·Hy30은 별도의 금융 가중치 튜닝이 아니다

현재 비교의 Hy7·Hy30 `contextual` 구성은 처음 번역할 때 원문, 조건부 용어 정의, 주어진 문맥, 한국어 문체·의미 보존 지시를 **하나의 프롬프트**에 넣는다. 일반 초안을 저장한 뒤 별도 편집 모델이 고치는 구조도 아니며, 이 비교에서 Hy 가중치를 금융 자료로 학습한 것도 아니다.

- [프롬프트 구성](scripts/model-comparison/run_hymt.py): `build_user_prompt`, `raw`와 `contextual`.
- [현재 앱 번역 흐름](lib/translation/index.ts): Hy-MT2는 보호 표식을 삽입하지 않은 원시 문단을 제공자에 전달하고 반환값을 검사한다.
- [앱 Python 엔진](scripts/local-hymt/engine.py).

TG12는 원래 번역 템플릿의 `source-only`다. 따라서 현재 7B·12B 및 대형 후보 비교는 **실사용 구성의 비교**이지, 모델 크기만의 효과나 금융 튜닝만의 효과를 분리한 실험이 아니다. 양자화도 Hy7 Q8 대 다른 후보 Q4로 다르다.

현재 개발 18개와 공개 원문 6개는 `context` 필드가 비어 있다. 조건부 용어 지시의 효과가 포함될 수 있지만 **이웃 문맥 효과를 검증했다고 주장할 수 없다.**

### 4.2 이전 Marian v5는 전체 가중치 미세조정이었다

- [v5 학습 진입점](scripts/model-training/train_v5.py)은 고정된 기존 [trainer](scripts/model-training/train.py)를 재사용한다.
- v4의 선택된 FP32 모델에서 시작한다. LoRA가 아니며 일반적으로 학습 가능한 전체 파라미터를 업데이트한다. Marian의 고정 위치 임베딩은 예외다.
- `model(**batch).loss`를 사용한 문장쌍 지도학습이다. 용어·말투만 고치는 독립된 층이나 원문 명제를 고정하는 제약이 있는 구조가 아니다.
- 당시 선택 기준은 용어·chrF·BLEU·숫자·빈 출력·잘림·금지 오역 표기와 일반 영역 회귀였다. 문장 전체 의미 보존을 보증하지 못했다.
- 1,152 학습쌍, 6 epochs, 1,728 optimizer 갱신, 255개 중 253개 tensor 변화가 기록되어 있다.
- 최종 시험의 기준 용어는 103/110, 93.64%였지만 후속 도우미 의미 검토에서 50/140행에 중요한 오류가 남아 앱 모델로 선택하지 않았다.
- 일반 chrF는 부모 42.070999→44.227848로 상승했다. 이것만으로 일반 의미 능력이 보존됐다고 할 수 없고, 반대로 v5 오류만 보고 “전체 미세조정이 일반 능력을 망가뜨렸다”고 인과적으로 단정할 수도 없다.

근거는 [v5 보고서](content/training/FINANCE_V5_REPORT.md)와 [학습 도구 안내](scripts/model-training/README.md)다. 기존 최종 시험은 이미 소비했으므로 이를 보고 다시 학습해 같은 시험을 통과시키면 안 된다.

### 4.3 다음 작업의 설계 기준

1. **기반 선정:** 일반 번역에서 주체·대상, 부정, 조건, 수량, 지시 대상, 다의어의 문맥상 의미를 먼저 비교한다. 중요한 의미 오류를 용어 점수 상승으로 상쇄하지 않는다.
2. **도메인 적응:** 기반이 보존한 개념의 정규 명칭, 문서 내 일관성, 원하는 문체를 개선한다. `equity`의 뜻 자체를 잘못 고르는 문제는 단순 표기 문제가 아니다.
3. **전후 회귀:** 고친 중요한 오류와 새로 만든 중요한 오류를 따로 센다. 기존에 정확했던 문장을 튜닝 후 틀리게 만든 경우를 평균 점수에 숨기지 않는다.
4. 같은 모델의 raw와 domain 구성을 비교해야 프롬프트 효과를 분리할 수 있다. 필요한 후보에만 이 대조를 추가한다. 모든 실행기를 새 버전으로 무작정 복제하지 않는다.
5. 동결 기반+LoRA, 일반 번역 replay, 보수적 업데이트 등은 검토 가능한 수단이지 의미 보존 보증이 아니다. 설치된 **GGUF의 추론 성공은 해당 모델을 이 PC에서 가중치 학습할 수 있다는 증거가 아니다.** 학습 가능한 체크포인트와 역전파 경로부터 확인한다.
6. 현재 개발 자료에서 일반 영역은 2개뿐이다. 범용 번역 능력의 보존을 주장하려면 별도의 새 일반 개발 자료가 필요하다.

학습 후보 수용 기준은 [TRAINING_QUALITY_FLOOR.md](content/model-comparison/TRAINING_QUALITY_FLOOR.md)에 있다. 이는 **정책 문서이며 자동 의미 검수 기능 구현 완료가 아니다.** 오류·미해결 의견이 있는 번역을 대량으로 학습에 넣지 않는다.

## 5. 이미 끝낸 품질 비교 — 재생성할 필요 없음

두 구성의 실제 개발 18개와 공개 원문 6개, 총 48개의 도우미 익명 판단 및 경계 사례 조정을 완료했다.

| 지표 | Hy-MT2 7B Q8 contextual | TranslateGemma 12B Q4 source-only |
|---|---:|---:|
| 개발 18개 중 중요한 의미 오류 | 4/18 | 6/18 |
| 개발 18개 중 자연스러움 | 17/18 | 16/18 |
| 주석된 용어의 뜻 적중 | 12/12 | 11/12 |
| 주석된 용어의 정규 표기 | 12/12 | 7/12 |
| 뜻과 표기를 모두 만족 | 12/12 | 7/12 |
| 최소대립쌍 양쪽 관계 보존 | 5/6쌍 | 4/6쌍 |
| 공개 원문 6문단 중 중요한 의미 오류 | 1/6 | 3/6 |

주의:

- 12B로 교체할 품질 근거가 부족하다. 현재 7B 등록을 유지했다.
- 7B도 자금 조달 구성의 `equity`를 “균형”으로, 12B는 “공평성”으로 오역했다. 이 출현은 위의 **주석된 용어 12개 분모에 들어 있지 않다**. 12/12를 전체 정확도나 목표 달성으로 표시하지 않는다.
- 실제 읽기의 7B·12B 모두 단기 Treasury bills를 장기 국채와 구분하지 못했다.
- 12B에는 위의 72 나눗셈 반전과 영구 성장 조건 누락도 있었다.
- 개발 18개와 공개 문서 한 곳의 6문단을 합쳐 일반적인 정확도 백분율을 만들지 않는다.
- 전부 도우미 검토이며 사람 검수·새 독립 최종 인증이 아니다.

자세한 원시 경로·판정 조정·집계 명령·해시는 [후속 비교 보고서](content/model-comparison/LINGUISTIC_COMPARISON_REPORT.md)에 있다.

### 완료된 실행 폴더

공통 루트는 `.training/comparisons/`다.

| 폴더 | 상태 |
|---|---|
| `linguistic-dev-20260910/hy7-baseline` | 개발 18개 완료·검수 완료 |
| `linguistic-dev-20260910/translategemma-12b-v2` | 개발 18개 완료·검수 완료 |
| `real-reading-check-20260910/hy7-baseline` | 실제 읽기 6개 완료·검수 완료 |
| `real-reading-check-20260910/translategemma-12b-v2` | 실제 읽기 6개 완료·검수 완료 |
| `linguistic-dev-20260910/translategemma-27b-smoke-paging-v3` | 기능 2개 완료. 금융 품질 비교는 아님 |
| `linguistic-dev-20260910/hymt30-smoke-paging-v3` | 첫 응답의 top_k 보고값 검증 실패. 원시 응답 보존·모델 종료 |
| `linguistic-dev-20260910/hymt30-smoke-paging-v4` | 새 기능 2개 완료·원래 EOS·무결성·종료 확인 |
| `linguistic-dev-20260910/hymt30-paging-v4` | **개발 18개 완료, 의미 검수 미완료** |

## 6. 고정 데이터와 평가 도구

### 입력

- 개발 자료: [linguistic-dev-20260910.jsonl](content/model-comparison/linguistic-dev-20260910.jsonl)
  - SHA `b8a2b91802e36d96654631a9965566f774e050ea4139ca7270d9f84d2a916848`.
  - 도우미 작성 18개, 금융 16·일반 2, 최소대립쌍 6쌍과 문단 6개.
  - [출력 관측 전 원문 감사](.training/verifications/linguistic-dev-source-audit-20260910.json), SHA `73c61fecff05337a20048d9caf8912a5743437611265324bae65a0c7e01675cf`.
- 실제 읽기: [sources.jsonl](content/model-comparison/real-reading-check-20260910/sources.jsonl)
  - SHA `becd55204f1533adf68c0512638f17db676d4848a175367c841ed71fca34f8d0`.
  - 보존된 R02 PVPrimer의 6문단. 출력 전에 선정했다.
  - 같은 폴더의 assistant references·review advisory·manifest를 함께 읽는다.
- 모든 입력과 참조는 동결 상태다. 결과를 보고 원문·정답·기준을 바꾸거나 train에 넣지 않는다.
- R05의 기존 UTF-8 해독 손상은 별도 [조사 메모](content/model-comparison/R05_SOURCE_DECODING_NOTE.md)에 있다. 이번 깨끗한 원문 비교에서 제외했고 운영 원문 재추출·수정은 하지 않았다.

### 검수

- [개발 검수 형식](scripts/model-comparison/LINGUISTIC_REVIEW.md), [읽기 검수 형식](scripts/model-comparison/READING_REVIEW.md).
- 새 v4 결과: [REVIEW_V4.md](scripts/model-comparison/REVIEW_V4.md)의 별도 도구를 쓴다.
- 기존 7B/12B prepared와 집계는 기존 도구를 그대로 사용한다. 새 v4 도구로 기존 prepared를 덮어쓰지 않는다.
- 각 prepared의 manifest, source audit/advisory, 해당 packet을 먼저 읽는다. **검토 전에 review-key.json이나 모델별 점수를 열지 않는다.**
- 검수자는 source/context에서 판단한다. 도우미 참조 번역도 오류 가능성이 있으므로 절대 정답처럼 강제하지 않는다.
- 의미 오류 severity, 자연스러움, 용어의 뜻과 표기를 분리한다. 모든 후보·모든 행 판단을 채우고 원문·번역의 실제 근거 구절을 기록한다.
- 기존 경계 사례 조정은 [사전 조정 기록](.training/verifications/local-review-adjudication-20260910.json)에 있다. 원래 검수 파일을 보존하고, 모델 키를 열기 전에 조정한 별도 파일로 집계했다.

주요 기존 집계:

- [개발 7B/12B 집계](.training/comparisons/linguistic-dev-20260910/assistant-review-hy7-tg12-v1.json), SHA `58d6263620fb5000e187370341cac160ecafe734e14432a35a866b8f309315f2`.
- [읽기 7B/12B 집계](.training/comparisons/real-reading-check-20260910/assistant-review-hy7-tg12-v1.json), SHA `0e88a6312d1ef220bf3ce59bbd6fce05187e62c9129d3f6d8b8357937d830786`.

## 7. 설치된 모델·실행기 정체성

PC: Intel Core Ultra 7 258V / Arc 140V 공유 GPU, 물리 RAM 약 **31.51GiB**. `.venv-training`은 Python 3.11.9, torch 2.8+xpu, transformers 4.57.6이다. 공유 GPU 메모리를 추가 전용 RAM처럼 계산하지 않는다.

| 구성 | 로컬 폴더 / 모델 파일 크기 | 모델 SHA-256 |
|---|---|---|
| Hy7 Q8 | `.training/comparisons/hy-mt2-7b-q8`, 7,981,928,896바이트 | `58b3ad55dd6f6fa08c695cddc34fb5f8f708a844f78ae10508071914b0ed67c0` |
| TG12 Q4 | `.training/comparisons/translategemma-12b-q4`, 7,300,794,112바이트 | `b7aac4b4be7ab0c49b6556c29c4467e74313df7f1e95d9f9676bb2adf0afa528` |
| TG27 Q4 | `.training/comparisons/translategemma-27b-q4`, 16,546,704,480바이트 | `7f1e67c4ecfec676b38c1ea2ef85c46fafe2f02d3c050fb9540e51787405d8a3` |
| Hy30-A3B Q4 | `.training/comparisons/hy-mt2-30b-a3b-q4`, 18,236,702,880바이트 | `bb44b11bb0f7cd3d1321645b41e911cd3de2e473731227fc6bf37aa18b543f88` |

モデル revision:

- Hy7: `tencent/Hy-MT2-7B-GGUF`, `ab8472660ac61fac25f1af43fac2599d52a8a775`.
- TG12: `mradermacher/translategemma-12b-it-GGUF`, `fdf84c9f6fe14e69d58814f14e7b5b63bb6a1b28`.
- TG27: `mradermacher/translategemma-27b-it-GGUF`, `0f2c24b456631d519a919e20429119bbd524d86e`.
- Hy30: `tencent/Hy-MT2-30B-A3B-GGUF`, `fd3dcbb6b31e9e03923ef4f9f42500f74c3851c3`.
- TG 변환본의 정확한 원본 Google 학습 가중치 revision은 입증하지 못했다. 위의 실제 GGUF revision·파일 SHA를 실행 정체성으로 쓴다.

현재 유지할 실행기:

| 파일 | SHA-256 / 주요 조건 |
|---|---|
| `scripts/model-comparison/run_hymt30_v4.py` | `b9cfd5dadaa98283b087488e41c57c46664e7f2298e5f835d313af0c39986edf` |
| `scripts/model-comparison/run_translategemma_large_v4.py` | `6277e73c9ee9f8d35c79fa197028eb8a5345a77078779802d81405845c0a7a2d` |
| `working_set_limit.py` | `4f8a45772a0f3d3f63274c7309bc7e673446eebb47a4a3e71dc558b95a4cbcdb` |
| `suspended_process_owner.py` | `f01ca8a2fd5a75e501c343d2632b2f534b4e5f0397fda502449c69c5d363dfcd` |

Hy30은 원래 30B 템플릿·EOS 120025, CPU b10888, context 8192, F16 KV, no-repack, temperature 0.7/top_p 1/top_k -1/repeat penalty 1/seed 42를 쓴다. 요청 `top_k=-1`을 고정 런타임이 `0`으로 보고한다. 공식 고정 소스에서 둘 다 top-k 비활성 동작임을 확인한 **검증 수정만 v4에 반영**했다. 요청 샘플링은 바꾸지 않았다.

TG27은 원래 TranslateGemma 템플릿, context 2048와 출력 예약 768, source-only, CPU4, no-repack, temperature 0, seed 20260910이다. native EOG 집합 1/106/212를 확인하되 원래 종료 1/106과 추가 212를 구분한다. 212로 종료한 결과를 정상 원래 종료로 인정하지 않는다.

현재 앱 등록 manifest:

- 경로 `.translation/hymt/manifest.json`
- SHA `cb3031d87372fbb1b0c3dcaf2071064bb59a8a21bd96480ef2d5a66752ae52bd`
- 등록 `hymt-contextual-q26-20260910-nativejob`.
- Hy7의 EOS override·템플릿·샘플링을 Hy30에 복사하지 않는다.

## 8. 메모리 정책과 실제 실패 이력

- v3/v4 비교 실행기는 Windows의 소유 Job에 native 프로세스를 중지 상태로 생성하고, 작업 집합 상한을 설정·재조회한 다음 재개한다.
- `--ram-budget-gib`는 8~12 중 명시한다. 기본값 12를 무심코 사용하지 말고 시작 직전 여유를 확인한다.
- 시작에는 선택 예산+3GiB의 가용 물리 메모리와 모델별 commit 여유가 필요하다. 8GiB 실행에는 최소 11GiB 물리 여유가 필요하다.
- API 요청 상한은 관측 예산보다 64MiB 낮다. 실제 관측값이 선택 예산을 넘으면 중단한다.
- 물리·commit 여유 512MiB 미만이 3초 지속되면 소유 모델을 중단한다. 0.5초 관측, 보존 로그는 보통 5초 간격이다.
- 작업 집합 상한은 **소유 모델의 상주 메모리 한도**다. OS 파일 캐시·prefetch·다른 앱·전체 commit의 상한이 아니다.
- 실제 TG27 기능 시험에서는 순간 최저 물리 여유가 약 80.7MiB까지 내려갔다. 이 값은 0.5초 관측의 summary 최솟값이고 5초 로그에는 없어서 정확한 시각·지속시간을 확정할 수 없다. 전역 메모리 안정성을 보증하지 않는다.
- TG27 8GiB 기능 입력 2건은 각각 약 978초·729초 걸렸다. 현재 RAM에서 전체 비교가 오래 걸릴 수 있다. 느리다는 이유로 품질 실패로 분류하거나 후보를 자동 제외하지 않는다.
- TG27 v4는 request 2시간/전체 24시간, startup 30분이다. v3는 request 30분/전체 12시간이었다. **시간 한도만 변경**했고 원래 v3 파일·출력은 보존했다.
- Hy30 v4는 request/startup 각각 30분, 전체 12시간이다.
- 실패한 TG12 초기 EOG 검사, Hy30 v3 top_k 검사, 초기 working-set fixture, GPU/SYCL 메모리 실패를 삭제하거나 성공으로 소급하지 않는다.
- 비교 CLI는 실행 후 모델을 종료한다. **현재 앱 자체의 유휴 모델 자동 반환은 아직 구현하지 않았다.** [앱 유휴 수명 감사](.training/verifications/local-engine-idle-audit-20260911.json)에 현재 무기한 상주 및 종료 대기·동시 요청 문제를 기록했다. 이 두 상태를 혼동하지 않는다.

## 9. 다음 세션의 권장 작업 순서와 명령

### A. 먼저 상태를 확인한다

1. [AGENTS.md](AGENTS.md), [제품 요구사항](DAMODARAN_KO_LEARNING_SPEC.md), [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md), 관련 README를 읽는다.
2. `git status --short`와 관련 diff를 확인한다. **사용자·다른 세션의 미커밋 변경이 많다. reset/clean/덮어쓰기 금지.** UI·브랜딩·원문 가져오기 개선 등은 이번 모델 작업의 소유 변경이 아니다.
3. 완료한 Hy30 개발 summary와 정체성, 현재 가용 RAM, 살아 있는 소유 모델을 확인한다. 이미 완료한 18개를 다시 생성하지 않는다.
4. 한 번에 무거운 모델 하나만 실행한다. `.venv-training` Python의 실제 자식 이름은 `python3.11.exe`일 수 있다. `python.exe`만 검색하면 누락한다.

```powershell
.venv-training/Scripts/python.exe -X utf8 -c "import sys,json;sys.path.insert(0,'scripts/model-comparison');import linguistic_screen as s;print(json.dumps(s.memory_status()))"
Get-Process python*,llama* -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,WorkingSet64
```

다음 세션에서 장시간 계산에 절전 방지 보조 프로세스를 사용한다면 과거 helper를 재사용·중복 실행하지 않는다. 명시적 유한 시간과 종료 sentinel을 둔 새 소유 helper를 만들고 마지막에 해제한다. 다른 앱이나 전원 계획을 임의로 변경하지 않는다.

### B. 완료된 Hy30 개발 출력의 의미 검수를 시작한다

아래 출력 폴더는 인계 시점에 아직 만들지 않았다. 실행 전 존재 여부를 확인하고, 이미 존재하면 덮어쓰지 않는다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/prepare_linguistic_reviews_v4.py --input content/model-comparison/linguistic-dev-20260910.jsonl --source-audit .training/verifications/linguistic-dev-source-audit-20260910.json --results .training/comparisons/linguistic-dev-20260910/hy7-baseline .training/comparisons/linguistic-dev-20260910/hymt30-paging-v4 --output .training/comparisons/linguistic-dev-20260910/prepared-round2-v4
```

세 packet을 별도 도우미가 검토하면 실제 모델 실행과 병행할 수 있다. 새 검수 도우미는 가능하면 **`fork_turns="none"`**으로 만들고 배정 packet·manifest·source audit/advisory·검수 규약만 전달한다. 이 인계 문서 전문, 이전 모델별 오류·점수, 모델별 실행 폴더·원시 summary·review-key는 전달하지 않는다. 위 예시처럼 prepared 폴더 이름도 중립적으로 두면 좋다. 원문·advisory·검수 형식을 먼저 읽고 모델 이름과 label의 대응을 숨긴다. 이미 특정 출력을 본 검토자라면 그 한계를 기록한다. 동일 기준의 중요한 오류와 경미한 문제를 구분하고, 애매한 판단은 원문으로 조정한 다음 집계한다.

```powershell
# 검수 JSONL 경로는 실제로 작성한 모든 파일을 지정한다. 아래 꺾쇠는 대체할 자리다.
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/summarize_linguistic_reviews_v4.py --prepared .training/comparisons/linguistic-dev-20260910/prepared-round2-v4 --reviews <모든_검수_JSONL> --output <새_집계_JSON>
```

### C. Hy30 공개 원문 6개를 실행한다

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_hymt30_v4.py --run --ram-budget-gib 8 --input content/model-comparison/real-reading-check-20260910/sources.jsonl --output .training/comparisons/real-reading-check-20260910/hymt30-paging-v4
```

실행의 `start.json`이 생기면 실제 코드 바이트를 보존한다. 아래 스냅샷 스크립트는 새 `code-snapshot` 폴더만 만든다. 이미 보존했다면 다시 실행하지 않는다.

```powershell
.venv-training/Scripts/python.exe -X utf8 .training/verifications/snapshot-linguistic-code-20260910.py .training/comparisons/real-reading-check-20260910/hymt30-paging-v4
```

종료 후 status만 보지 말고 output integrity·무효 출력 목록·모델 종료·hash를 확인한다. 그 뒤 `prepare_reading_reviews_v4.py`로 Hy7 또는 다른 완료 후보와 비교한다. 읽기 준비 CLI에는 `--source-audit` 인자가 없다. dev 검수 형식과 읽기 검수 형식은 다르다.

### D. TG27 개발 18개·공개 원문 6개를 순차 실행한다

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_translategemma_large_v4.py --model-size 27b --threads 4 --ram-budget-gib 8 --input content/model-comparison/linguistic-dev-20260910.jsonl --output .training/comparisons/linguistic-dev-20260910/translategemma-27b-paging-v4
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_translategemma_large_v4.py --model-size 27b --threads 4 --ram-budget-gib 8 --input content/model-comparison/real-reading-check-20260910/sources.jsonl --output .training/comparisons/real-reading-check-20260910/translategemma-27b-paging-v4
```

위 두 명령은 **동시에 실행하지 않는다.** 매 실행 직전 RAM/commit을 확인하고 첫 실행의 실제 종료를 확인한 다음 두 번째를 시작한다. `start.json`의 코드 해시와 일치하는 스냅샷도 각 폴더에 보존한다. 새 설정 선택이 필요하면 이미 검증한 8~12GiB 범위에서 명시하며, 3GiB 여유나 의미 평가 기준을 조용히 낮추지 않는다.

완료 후 같은 새 v4 검수 도구로 의미를 대조한다. 새 후보까지 포함한 비교는 2~4개 완료 시스템을 지원한다. 일반 번역 실패와 도메인 표기 불일치를 나누어 보고하고, 크기·생성 개수로 우승 모델을 정하지 않는다.

### E. 사용자 요구에 맞게 설계·평가를 정합화한다

현재 비교를 완료한 뒤, 유망한 같은 모델에서 **일반 번역 구성 대 용어·문체 지시 구성**의 차이를 필요 범위에서 확인한다. 프롬프트 변화와 가중치 학습을 구분한다. 새 일반 개발 자료 및 의미 관계 보존 항목을 준비하고, 이미 소비한 test나 현재 dev 출력으로 train을 만들지 않는다.

그다음에만 검토된 train 전용 번역쌍의 작은 묶음으로 도메인 적응을 설계한다. 올바른 번역을 튜닝이 망가뜨리지 않는 전후 대조와, 숫자 존재 검사 외 **행위자·조건·부정·연산 방향·단위·지시 관계**를 확인한다. 이 관계 전체를 단순 문자열 규칙만으로 보증할 수 있다고 주장하지 않는다.

## 10. 새 모델을 앱에 적용하려면 남은 별도 작업

현재 비교 성공만으로 manifest의 파일명만 바꿔서는 안 된다.

- 현재 Python 배포 검증과 TypeScript 런타임은 7B 모델·런타임·템플릿·등록 계약을 고정한다. 새 모델은 별도 엔진·등록 정체성 계약이 필요하다.
- 기존 등록기는 Q26의 4구성×24문단 및 선택 결과 계약을 전제한다. 현재 18+6 평가를 임의로 이 계약의 통과로 취급하지 않는다. 새 근거 계약을 명시적으로 설계한다.
- Hy30은 Hy7과 tokenizer/EOS/템플릿/샘플링/native runtime이 다르다. TG27은 source-only·2K 문맥·768 출력 예약이다. 장문 초과를 조용히 자르거나 다른 제공자로 대체하지 않는다.
- cache identity에는 실제 모델·실행기·프롬프트·문맥/용어·처리 정책을 반영한다.
- [현재 유휴 수명 감사](.training/verifications/local-engine-idle-audit-20260911.json)를 참고해 앱에서도 장기 유휴 모델을 반환할지 구현한다면, ready 대기 중 요청·동시 요청·이전 종료 완료 전 새 spawn·취소를 함께 다룬다. 단순 타이머나 문단마다 강제 종료를 무심코 붙이지 않는다.
- 별도 DATA_DIR에서 실제 새 엔진으로 HTML 3문단·PDF 1페이지의 생성·저장·캐시, 의미 대조, 취소·시간초과·native 종료를 검증한다. 테스트 제공자의 결과를 실제 품질 검증으로 보고하지 않는다.
- 현재 7B의 과거 앱 QA를 새 모델의 앱 QA로 재사용하지 않는다.

원래 등록 계약: [DEPLOYMENT.md](scripts/local-hymt/DEPLOYMENT.md).

## 11. 실제로 실행한 검사와 동결 상태

| 검사 | 결과·범위 |
|---|---|
| 최종 v3 producer | 합성 54개: TG27 36 + Hy30 18 통과 |
| TG27 v4 | 합성 37개 통과. v3에서 version/요청·전체 시간 한도만 변경했음을 원본 문자열 변환 대조로 확인 |
| Hy30 v4 | 합성 20개 통과. 고정 native top_k 정규화만 허용, 다른 설정 유지 |
| 기존 v3 검수 도구 | 합성 53개 통과 |
| 새 v4 검수 도구 | 합성 64개, CLI help 4개 통과. 실제 Hy30 v4 기능 2건의 raw·memory·C++ 근거 해시 대조 통과 |
| 기존 검수 보존 | 기존 검수 도구 6개와 7B/12B prepared 두 폴더의 manifest·packet·key SHA 유지 확인 |
| 이번 실제 대형 모델 실행 | TG27 v3 기능 2개, Hy30 v4 기능 2개·개발 18개. 의미 검수와 구분 |
| 앱 검사 | 이 후속 Python 비교 작업에서 새 앱 build/typecheck/실제 앱 엔진 QA를 수행한 것으로 보고하지 말 것 |

검사 근거:

- [v3 54개](.training/verifications/local-v3-final-tests-20260910.json)
- [TG27 v4 37개](.training/verifications/translategemma-v4-time-budget-20260911.json)
- [Hy30 v4 20개 및 소스 근거](.training/verifications/hymt30-v4-topk-metadata-validation-20260910.json)
- [v4 검수 64개·코드 스냅샷·기존 해시](.training/verifications/review-v4-contracts-20260911.json)

v4 검수 도구 동결 SHA:

| 파일 | SHA-256 |
|---|---|
| `v4_review_evidence.py` | `42adef2739e58b76ebeb6d3ddfe720022466e1fa413d51084d06b03164c6c24b` |
| `prepare_linguistic_reviews_v4.py` | `2c9f20d7611d2205d7d30f29e9cad721445078d777208e982c1b42b3341d7fa1` |
| `summarize_linguistic_reviews_v4.py` | `ee98e1fdcb622b3bb4ccbbad6c657c1679db5ac2d7c9cb9ba761f1e272adb7ea` |
| `reading_review_common_v4.py` | `287492ed753bea438b7010eef53dca7a33dfdd076bd7e8fab6daa7c075eeb491` |
| `prepare_reading_reviews_v4.py` | `143c00b933059eba208965bc076f1786b7af0ef8f84244689bef0fadf0e46d1d` |
| `summarize_reading_reviews_v4.py` | `3449e3c73ba7be9e48bb982a04165a67d3f0653baa031d2448cc7faf52a8d79c` |

이미 생성한 prepared가 참조하는 검수 코드는 수정하지 않는다. 필요한 변경은 새 명시적 버전과 근거로 분리한다. 다만 이름만 바꾼 실행기 복제를 목적 없이 늘리지 말고, 고정 공통 기능의 재사용을 먼저 검토한다.

## 12. 저장소·자료 보존 및 인계 주의

- `git status`에는 많은 미커밋/새 파일이 있다. 사용자 UI·브랜딩 변경, 원문 수집 수정과 이전 번역·학습 작업이 섞여 있다. 이번 작업만의 깨끗한 기준으로 전체 트리를 초기화하지 않는다.
- 운영 DB는 `data/library.sqlite`, 원문·개인 기록·기존 번역을 보존한다. DATA_DIR가 다른 검증과 운영을 섞지 않는다.
- `.training`의 학습 가중치·optimizer·실행 결과는 SQLite 백업에 포함되지 않는다. 재설치 가능한 venv와 대체 불가능한 학습/평가 결과를 구분한다.
- 모델 다운로드는 이미 끝났다. 파일이 일치한다면 다시 내려받을 필요가 없다.
- 완료한 실행·기능 시험을 불필요하게 반복하지 않는다. 변경·새 실패·미해결 문제에 필요한 검사만 추가한다.
- 테스트·실행 중 동결 입력, 공통 프롬프트, registered Python 파일을 수정하면 정체성 검사가 실패한다. 먼저 실행 종료와 코드 소유권을 확인한다.
- Windows 백그라운드 실행은 숨김 창을 사용하고 소유권을 기록한다. broad `python`/`node` 종료, OS 캐시 강제 비우기, 다른 앱 종료는 하지 않는다.
- 다른 세션이 서버를 띄운 이력이 있어 종료 여부 질문이 있었지만 답을 받지 못했다. 예전 root의 앱 종료 기록만 보고 현재 서버 소유권을 가정하지 않는다.
- 이 인계에 API 키·사용자 메모·전체 환경 변수는 포함하지 않았다. 후속 진단에도 노출하지 않는다.

## 13. 다음 세션의 완료 보고 기준

사용자에게는 다음을 짧고 분명하게 보고한다.

1. 어떤 실제 구성에서 중요한 의미 오류가 줄었는지와 남은 대표 오류.
2. 일반 번역 기반과 용어·문체 적응을 어떻게 구분했고, 어떤 전후 회귀를 확인했는지.
3. 생성 완료/자동 검사/도우미 의미 검수/사람 검수/가중치 학습/앱 적용 중 어디까지 실제로 끝났는지.
4. 소유 모델을 종료해 메모리를 반환했는지, 남아 있는 실행이 있다면 정확히 무엇인지.

**다음 즉시 작업은 이미 생성된 Hy30 개발 18개를 의미 검수하고, Hy30 실제 읽기 6개와 TG27의 미완료 비교를 이어가는 것이다. 새 대량 학습을 먼저 시작하는 것이 아니다.**

## 14. 이미 조사한 공식 자료

후보의 공개 벤치마크를 이 PC의 금융 영한 정확도나 Q4 품질로 대신하지 않는다. 다음 자료와 저장된 publisher metadata를 참고하면 같은 조사를 처음부터 반복할 필요가 없다.

- [Google TranslateGemma 27B](https://huggingface.co/google/translategemma-27b-it), [12B](https://huggingface.co/google/translategemma-12b-it), [논문](https://arxiv.org/pdf/2601.09012).
- [Tencent Hy-MT2 30B-A3B](https://huggingface.co/tencent/Hy-MT2-30B-A3B), [공식 GGUF](https://huggingface.co/tencent/Hy-MT2-30B-A3B-GGUF/tree/main), [논문](https://arxiv.org/html/2605.22064v1).
- [MQM 오류 분류](https://www.themqm.org/mqm-pillars/the-mqm-full-typology/).
- [Windows 작업 집합](https://learn.microsoft.com/en-us/windows/win32/memory/working-set), [작업 집합 상한 API](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-setprocessworkingsetsizeex).

Hy30 논문의 여러 언어를 합친 금융 XCOMET/GEMBA 수치를 한국어 금융 용어 적중률로 읽지 않는다. TranslateGemma의 공개 인간 평가도 이번 금융 자료·변환 Q4 가중치의 직접 검증이 아니다. 최종 선택은 위에 남긴 실제 로컬 출력의 원문 대조를 따른다.

## 15. 후속 제안 — 자동 의미 위험 표시와 번역 검토 안내

사용자는 모델 성능을 완벽하게 만드는 데 한계가 있으므로, 번역 시 산출한 품질 수치가 일정 기준보다 낮으면 달모다란 reader에서 해당 영어·한국어 구절을 붉은 밑줄로 표시하고 `번역 오류 주의 요망`을 안내하는 방안을 제안했다.

### 15.1 판단

이 기능은 **도입할 가치가 있는 보조 안전장치**다. 다만 번역 모델 자체의 토큰 확률이나 같은 모델의 자기평가 점수 하나를 임계값으로 사용하면 효율이 낮다. 모델은 자연스럽고 확신도 높은 문장으로도 의미를 틀릴 수 있다. `REAL26-005`의 나눗셈 반전도 숫자와 예시가 보존되고 한국어 문장이 유창해 기존 자동 검사를 통과했다. 따라서 생성 모델의 낮은 확률을 곧바로 의미 오류로 해석하거나, 높은 확률을 정확성 보증으로 표시하지 않는다.

권장 방식은 원문과 번역을 함께 받는 **별도의 번역 품질 추정(Quality Estimation, QE) 단계**를 두어 검토 우선순위를 정하는 것이다. 품질 추정 결과도 오탐과 누락이 있으므로 `번역 오류` 확정 판정이 아니라 `자동 의미 검사: 검토 권장`으로 표시한다. 점수는 내부 근거로 보존하되 사용자에게는 기본적으로 위험 단계와 이유를 보여준다.

| 신호 | 권장 용도 | 한계 |
|---|---|---|
| 번역 모델의 토큰 확률·평균 log probability | 진단 보조 | 유창하고 흔한 오역에 높은 확률을 줄 수 있으며 의미 관계를 직접 검증하지 않음 |
| 같은 생성 모델의 자기평가 | 참고 실험 | 번역과 평가가 같은 맹점을 공유하고 점수가 교정되어 있지 않음 |
| 숫자·수식·부호·URL 규칙 | 즉시 고위험 경고 | 정해 둔 표면 불변량만 검사하며 피제수·제수, 주체·대상 등은 놓침 |
| 독립 QE 모델의 원문-번역 점수 | 문장·문단 검토 우선순위 | 로컬 영한 자료에서 임계값을 별도 교정해야 하며 단일 점수로 오류 위치를 알 수 없음 |
| 독립 오류 구간 탐지 | 밑줄 위치와 심각도 후보 | 구간 오탐과 offset 불일치가 가능하므로 반환 텍스트·경계를 검증해야 함 |
| 사용자 검수 | 현재 표시 번역의 최종 기준 | 자동 평가와 구분하고 기존 기계 결과·평가 이력을 보존해야 함 |

참조 번역 없이 source와 machine translation만 받는 COMETKiwi 계열은 문장 위험 점수 후보가 될 수 있다. xCOMET은 문장 점수와 `minor`·`major`·`critical` 오류 구간을 함께 제공하는 평가 후보지만, 공개 XL 모델은 약 3.5B이고 공식 사용 예시는 참조 번역을 포함한다. 현재처럼 사람 검수 정답이 없는 제품 경로에 바로 적용할 수 있다고 가정하지 말고, 모델 계약·영한 지원·라이선스·실제 메모리와 속도를 별도 확인한다.

- [COMET 공식 저장소 — reference-free 평가와 점수 해석](https://github.com/Unbabel/COMET)
- [COMETKiwi XL 모델 카드](https://huggingface.co/Unbabel/wmt23-cometkiwi-da-xl)
- [xCOMET 논문 — 문장 점수와 오류 구간 탐지](https://aclanthology.org/2024.tacl-1.54/)
- [xCOMET XL 모델 카드](https://huggingface.co/Unbabel/XCOMET-XL)

### 15.2 권장 표시 정책

1. **빨간 물결 밑줄**은 고정 규칙 위반 또는 신뢰 기준을 통과한 `major`·`critical` 오류 구간에만 쓴다.
2. 품질 점수만 낮고 오류 범위를 신뢰성 있게 특정하지 못하면 임의의 단어를 밑줄 긋지 않는다. 해당 문장 전체를 옅은 주황색으로 표시하고 `자동 의미 검사: 검토 권장`을 붙인다.
3. 한국어 오류 구간은 평가기가 반환한 실제 offset과 문자열이 현재 번역에 정확히 일치할 때만 표시한다.
4. 영어와 한국어의 정확한 구절 대응은 별도 source-target 정렬이 필요하다. 초기 버전은 한국어의 검출 구간과 대응하는 **영어 문장 전체**를 연결해 강조하고, 검증된 양언어 정렬이 있을 때만 영어 구절까지 좁힌다. 존재하지 않는 정밀도를 UI로 암시하지 않는다.
5. 색만으로 상태를 전달하지 않는다. 아이콘·텍스트·접근성 설명을 함께 제공하고, 표시를 누르면 `연산 방향`, `주체/대상`, `부정/조건`, `수량/단위`, `용어 의미`, `일반 저신뢰` 등 근거 범주를 보여준다.
6. `의미 검사 대기 중`, `검토 권장`, `자동 검사상 특이점 없음`, `사용자 검수 완료`, `검사 모델 미설치/실패`를 구분한다. `자동 검사상 특이점 없음`을 무오류 또는 사람 검수 완료로 표현하지 않는다.
7. 기존 번역 수정 UI로 바로 이동하게 한다. 사용자가 검수본을 저장하면 현재 읽기 화면에서는 검수본을 우선하되 기계 번역과 자동 평가 이력은 삭제하지 않는다.

### 15.3 권장 처리 흐름과 저장 경계

```text
선택한 원문 번역
  → 기존 형식·숫자·수식 검사
  → 번역 결과 저장
  → 별도 비동기 의미 품질 검사
  → 점수·심각도·검출 구간 저장
  → reader에 검토 상태와 강조 표시
  → 선택적으로 사용자 수정·검수 저장
```

- 의미 검사는 페이지 방문이나 렌더링으로 자동 재실행하지 않고, 사용자가 선택해 생성한 번역에 대해서만 큐에서 실행한다.
- 동일한 `원문 해시 + 번역문 해시 + 문맥 해시 + 평가 모델/가중치/런타임/평가 규칙·임계값 버전`은 캐시한다.
- 현재 `translations.validation_status`는 숫자·수식 등 기존 자동 검사와 연결되어 있으므로 의미 위험 점수를 여기에 덮어쓰지 않는다. 새 마이그레이션의 별도 평가 이력(예: `translation_quality_assessments`)에 평가 모델 정체성, 원시 점수, 교정 버전, 상태, 오류 범주, 양쪽 offset·문자열·심각도·신뢰도와 생성 시각을 저장하는 방향이 적절하다.
- 평가 결과 저장 시 translation이 가리키는 원문과 평가 입력의 원문·번역 해시가 여전히 같은지 확인한다. 평가 중 사용자 검수본이 생기면 늦게 도착한 자동 결과로 검수본을 덮어쓰지 않는다.
- 평가 모델 미설치·실패·시간 초과 시 번역을 폐기하거나 다른 모델로 조용히 대체하지 않는다. 번역은 사용자 미검수 상태로 제공하고 의미 검사를 완료하지 못했다고 명시한다.
- 현재 앱 Hy-MT2 프로세스는 유휴 자동 반환이 구현되지 않았다. 이 PC에서 7B 번역 모델과 3.5B급 평가 모델을 동시에 상주시킨다고 가정하지 않는다. 번역 배치를 마치고 소유 번역 프로세스를 안전하게 반환한 뒤 평가 배치를 실행하거나, 실제 실측을 통과한 더 작은 평가기를 사용한다.

### 15.4 임계값을 정하는 실험

임의로 `0.7` 같은 수치를 정하지 않는다. 먼저 이미 원문 대조를 마친 Hy7·TG12의 개발 18개와 실제 읽기 6개씩, 총 48개 출력을 후보 평가기로 점수화한다. 출력과 기존 판정을 바꾸지 않고 다음을 계산한다.

1. 중요한 의미 오류의 재현율: 알려진 연산 방향·주체/대상·부정·조건·누락 오류를 얼마나 경고했는가.
2. 경고 정밀도와 전체 경고율: 정상 번역을 얼마나 불필요하게 표시해 사용자의 경고 피로를 만드는가.
3. 위험 유형별 성능: 의미 반전, 다의어, 수량·단위, 누락·추가, 자연스러움을 분리한다.
4. 오류 구간 성능: 단순히 문장을 맞혔는지뿐 아니라 밑줄 offset과 실제 문제 구절이 맞는지 확인한다.
5. 모델·길이·문서별 안정성: 특정 번역 모델이나 긴 문단에만 점수 분포가 달라지는지 확인한다.
6. 처리 시간·peak 작업 집합·모델 전환 비용: 품질 탐지율과 함께 기록한다.

현재 48개는 기능 도입 가능성과 초기 임계값을 판단하는 작은 개발 표본일 뿐 독립 최종 인증이 아니다. 모두 도우미 판단이며 사람 검수 자료가 없다는 표시를 유지한다. 최소한 `REAL26-005`의 나눗셈 반전과 기존에 확인한 영구 성장 조건 누락·Treasury bills 만기 구분·`equity` 의미 오류를 재현하지 못하는 평가기는 단독 빨간 경고기로 채택하지 않는다.

### 15.5 구현 우선순위

1. 고정밀 규칙으로 알려진 치명 유형을 보강하고 기존 문단 단위 `needs_review` 안내를 개선한다.
2. 후보 reference-free QE 평가기를 48개 고정 출력에 오프라인 적용하여 도입 가치와 로컬 자원 사용을 측정한다.
3. 검증된 문장 위험 상태를 별도 DB 이력과 API에 추가하고 비동기 작업·캐시·실패 상태를 구현한다.
4. 먼저 문장 단위 강조를 배포하고, 오류 구간 탐지 성능과 offset 계약을 통과한 뒤 한국어 구간 밑줄을 추가한다.
5. 양언어 구절 정렬은 마지막 단계로 둔다. 검증되지 않은 영어 구간을 번역 오류의 정확한 원인처럼 표시하지 않는다.

이 제안은 모델 개선을 중단하거나 대체하는 것이 아니다. 더 나은 번역 기반과 금융 용어·문체 적응을 계속 평가하되, 남는 오류가 사용자 학습 내용을 조용히 왜곡하지 않도록 **생성 품질 개선과 독립적인 위험 표시를 함께 사용하는 방어층**이다. 이 절은 설계 제안이며 자동 의미 검사 모델 설치·임계값 검증·DB/UI 구현이 완료됐다는 뜻이 아니다.

## 16. 전체 진행 승인 이후 — 2026-09-11 후속 상태

최신 사용자는 번역·밑줄 모두 95% 목표를 제시한 뒤 언어학·교육학 근거로 수용 기준을 자율 결정하도록 위임했다. [새 정책](content/model-comparison/LEARNING_READINESS_BASELINE_20260911.md)의 critical/major 의미 오류 0건, 기타 용어·질문·위험 경고별 95%, 핵심 질문·기지 오류·기술 offset 별도 기준을 적용한다. 정책과 자동 점수를 보편적인 학습 안전 확률이나 사람 검수 완료로 표현하지 않는다.

- **Hy30 개발18·읽기6 완료**: 생성·무결성·모델 종료와 새 익명 도우미 검수를 마쳤다. round2 개발 중요 오류는 Hy7 5/18·Hy30 5/18, 읽기는 2/6·1/6이다. 이전 Hy7 조정 검수 4/18·1/6은 보존하며 같은 출력의 검수 회차 차이를 모델 회귀로 해석하지 않는다. 각 비교 폴더의 `assistant-review-round2-v4.json`과 검토 provenance를 따른다.
- **TG27 첫 전체 시도 실패**: `linguistic-dev-20260910/translategemma-27b-paging-v4`에서 첫 입력 0/18, ConnectionResetError, guard의 request_time_limit을 기록했다. 관측 공백 676.937→4664.390초 및 4669.484→21874.093초가 있으며 원인은 확정하지 않는다. childProcessStopped=true다. 실패·미실행 행과 코드 스냅샷을 보존하고 새 시도·읽기6은 남겨 둔다. 생성되지 않은 출력을 의미 오류 0건으로 세지 않는다.
- **general16 동결·도구 준비**: 새 일반12·일반 의미의 이웃 문맥4, train/test 사용 금지. `run_general_context.py`의 Hy7/Hy30 raw/contextual 별도 실행과 짝 정체성 검사를 준비했다. 합성27·help2 통과, 실제 추론은 아직 안 했다. 기존 사전 매칭은0이므로 금융 용어 힌트 삽입의 회귀 검증까지 주장하지 않는다. 실행 계약은 `scripts/model-comparison/GENERAL_CONTEXT.md`다.
- **QE48 실측 완료·미등록**: wmt20 COMET-QE의 최초 로드 실패와 엄격한 position_ids 호환 처리 후48개 성공을 보존했다. 전체141.516초·peak4.89GiB, 기지6개 탐지에45/48경고·정밀도31.11%라 기존85/60·새95/95 모두 실패다. 새 제품정책 재해석은 사후 분석·재추론0으로 표시한다. 기존 정책/점수/판정은 불변이고 향후 등록은 product-qe-95-v2의 전체 및 baseline별 기준을 따른다. 별도 Qwen3.5-9B 검토 후보는 공개 출처·파일·Windows 실행 계약을 확인하며 설치 준비 중이고 실제 탐지 품질은 미검증이다.
- **앱 구현·검사**: migration003 평가 이력, quality 큐/캐시/lease/취소/검수 충돌/복원, 유휴60초 및 모델 전환 종료, 한국어 구간과 영어 문장 연결 UI를 구현했다. 취소 경합·예약 재시도 성공 후 과거 실패 경고 정리·현재 product-qe-95-v2만 허용하는 런타임 검증까지 포함한 최종173개 중171통과·조건부2제외, build·TypeScript 통과다. 초기165개/이후 집중검사 로그도 보존한다. 모의 UI9개·1440/390 화면 통과. 고정 규칙은 기존48개에서 TP1/FP0/FN13이며 광범위 의미 탐지 기준을 충족한 결과가 아니다.
- **실제 앱 QA 완료·의미 수용 보류**: `.training/quality-evaluation/app-hy7-quality-20260911` 새 schema3 DB에 운영 R01 HTML과 기존 격리 QA의 v2 PDF 불변 데이터만 복제했다. 개인기록·기존번역0, 복제시 source DB는 schema2 readonly였다. HTML3문단+PDF 페이지번호3 전체4블록을 `scripts/verify-quality-app.ts --run`으로 실제 생성·저장했다(920.062초). 자동검사7/7, 번역/의미 검사 캐시의 추가 작업·사용량0, 미등록 QE의 unavailable·score=null, 원문/블록 불변과 소유 프로세스 종료를 확인했다. 결과는 derived/quality-app-report.json, 실행 코드7개는 code-at-run/에 보존한다. 별도 quality-app-assistant-meaning-review.json은 비맹검 도우미 대조이며 본문5/제목1/쪽번호1, 확정 critical0/major0/minor3와 수혜주체 보류1건으로 의미 gate를 보류한다. 원문 체크포인트의 인코딩 손상·번역 열람 후 별도 복구본도 provenance에 남겼다. 이후 운영 DB는 `data/backups/2026-09-11T02-07-21-790Z-ad748cd6` 일관백업(원문참조53개) 후 schema3으로 갱신했고 기존14개 테이블의 행해시·개수·FK·무결성을 확인했다. 전후 근거는 `.training/verifications/schema3-before-20260911.json`, `schema3-after-20260911.json`이다. 최종 앱 시작은 아직 하지 않았다.

실행 중 모델 하나만 유지한다. `.training/verifications/run-awake-20260911.py`는 소유 실행 동안 Windows 시스템 유휴 방지 요청을 유지하고 종료 시 반환하며 화면·전역 전원 설정을 바꾸지 않는다. 첫 helper 검사도 긴 시간 공백으로 실패했고 v3 짧은 소유 자식에서 stdout·정상 종료·요청 해제를 확인했다. 사용자 수동 절전 등 모든 중단을 막는다고 보장하지 않는다. 다음 실행 전에 실제 프로세스 종료와 물리/commit 여유를 다시 확인한다.

## 17. CPU·메모리 최적화 추가 지시 이후

사용자는 작업을 계속하면서 메모리와 CPU도 최적화하도록 명시했다. 무거운 모델 한 개, CPU4 스레드와 BelowNormal 우선순위를 우선하고 모델·KV·작업 메모리, 가용 물리/commit, 속도와 원문 의미를 함께 확인한다. 스레드4는 CPU 사용률50%의 하드 상한을 뜻하지 않는다. 더 작은 문맥·캐시 설정도 새 구성으로 기록하며 입력을 자르거나 이전 출력의 정확성을 자동 승계하지 않는다.

- **Qwen3.5-9B 설치·기술3행 완료**: 고정 GGUF Q4_K_M 5,680,522,464 bytes/SHA `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8`와 b10888 CPU를 확인했다. Windows Python alias 문제(v1), EOG 로그 누락(v2), 실제3행 성공(v3)을 별도 보존한다. v3의 입력1153/1151/1159토큰·출력135/148/229토큰, 103.45/174.59/226.33초, peak working5.215GiB/private0.706GiB다. 실제EOS·형식·sampling·소유종료·파일 무결성 검증이며 의미 품질 통과가 아니다. 경로는 `.translation/qe/llm-candidates/qwen35-9b/runs/smoke-first3-v3`와 같은 후보 루트의 `smoke-technical-receipt-v3.json`이다.
- **v1 경고 정책은 3행에서 조기 탈락**: 고정 정답상 중요 오류가 없는 `LDEV26-003`에서 유창성 메모의 인용 불일치만으로 경고했다. 전체48의 기지 양성14개를 전부 찾더라도 이1FP 때문에 정밀도 상한은14/15=93.33%다. `.training/quality-evaluation/qwen35-smoke-primary-gate-v1.json`에 근거를 남기고 불필요한 나머지45행 추론을 하지 않았다. 전체 재현율·모델의 의미 판별 성능은 미측정이다. 기존 정책·출력·판정을 바꾸지 않았다.
- **새 경고 매핑 v2는 아직 미검증**: `scripts/local-qe/qwen-material-warning-policy-v2.json`과 `material_warning.py`는 major/critical 주장 또는 불확실성에 경고한다. 경미한 의미 문제·유창성 메모만은 진단에 보존하며, 그 인용 실패를 중요한 의미 오류로 간주하지 않는다. 중요 오류 주장의 위치를 못 찾으면 문단 경고를 유지한다. 기존 v1 실패를 성공으로 바꾸지 않고 새 구성 전체48 평가를 요구한다. 합성5검사는 실제 탐지 성능이 아니다.
- **Qwen 자원 후보 v4 동결·토큰 검사 완료**: CPU4/BelowNormal, ctx8192→4096, 동일 슬롯의 공통 지시 재사용을 별도 프로필로 고정했다. vocabulary만 읽는 도구로 전체48의 입력 최대1393+출력예약2048=3441<4096, 최소655토큰 여유를 확인했다. 기존 실제3행의 template 바이트·토큰이 정확히 일치했고, 공통 prefix는1064토큰이다. 토큰 검사는 peak104.36MiB·전체가중치로드/생성0, 실행기 합성18개·토큰 도구12개 통과다. `freeze-v4.json` SHA `d57916747df96297c5fe08c8f87118fac2ce67a6e9e04e782af049358324ee67`. context checkpoint3개는 추가 메모리를 쓰므로 **문맥 축소가 전체 메모리 절감이라는 주장은 하지 않는다**. 실제 복원·cached token·시간·품질은 다음 모델 슬롯에서 측정한다. [실행 안내](scripts/local-qe/llm-qwen35/README_V4.md)와 [캐시 조사](scripts/local-qe/llm-qwen35/CACHE_REUSE_REVIEW_V1.md)를 따른다. [고정 서버 문서](https://github.com/ggml-org/llama.cpp/blob/72797e89198ab564fd0e6baa54ab196e8dd1d884/tools/server/README.md)는 prefix 재사용 시 배치에 따른 출력 비동일 가능성을 명시한다.
- **general16 질문지 동결·Hy7 raw 시작**: [질문지](content/model-comparison/general-context-questions-20260911.md)는32문항·핵심16, 원문근거54구절, 공통한국어문맥4개다. 출력 미관측 상태에서 작성했고 답변자에게 정답 키를 주지 않는다. JSON SHA `4faac811b4b2644fe73f2b2adcfa9862207243988e963f4e7d331d627900ed51`. `general-context-dev-20260911/hy7-raw-below-normal-v1`에서 실제 raw16을 시작했다. CPU4·8GiB 관측 상한·실제 native BelowNormal을 확인하고 코드39개를 보존했다. 원래 모델/프롬프트/샘플링은 같고 우선순위만 별도 receipt로 남긴다. 실행/검토·contextual 대조는 아직 미완료다.
- **분석기 실패 거부 보완**: `scripts/local-qe/evaluate_llm_review.py`는 v1 전체48 전용 읽기 분석기다. 15개 합성회귀와 메타데이터 후2개 재검사, 실제v3 첫3개 계약의 읽기 대조를 확인했다. 최종채택은 실행성공/모델로드/계약/48회요청/guard/무결성/종료를 별도로 요구하고, 원시EOS·잘림·프롬프트·토큰·sampling도 재검증한다. 구간 의미 정확성은 별도 미평가이며 현재 모델을 등록하지 않는다.

`.training/verifications/run-awake-below-normal-20260911.py`는 원래 helper를 보존한 별도 실행기다. 새 소유 자식을 BelowNormal로 생성하며, 초기 합성 자식과 실제 Hy7 native의 우선순위를 확인했다. 원문/가중치/운영 전역 전원 설정을 바꾸지 않는다. 우선순위 상속 계약은 [Windows CreateProcessW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw)를 참고한다. 작은 working-set 상한이 반복적인 페이지 교체를 유발하는지도 관측하며 전체 page-fault 카운트를 디스크 hard-fault 수로 단정하지 않는다.

실제 앱 표본의 수혜주체 보류1건은 `derived/quality-app-assistant-beneficiary-adjudication-v2.json`에 후속 판정을 추가했다(SHA `51ad037a0c34a5194419a4eac6bf23a1a7d501842bd76ccdace90db61a9bb1cf`). 원문·이웃의 과거 투자와 미래 창출력 대비가 보존되고 주주/기업별 현금흐름 분배를 가르는 문맥이 아닌 근거로 minor를 확정했다. 최종 선택 표본7개는 critical0/major0/minor4·보류0이며, 본문은5개뿐이다. 원래 보류 판정과 원시 번역은 보존한다. 이는 비맹검 도우미 조정이고 전체 용어·질문·신규 시험·실제 사람 학습 효과는 미평가이므로 전체 학습 수용으로 바꾸지 않는다.

## 18. 자원 최적화 실측과 일반 문맥 평가 진행

사용자의 최신 `메모리, cpu 최적화하면서 진행해` 지시에 따라 CPU4/BelowNormal·한 번에 모델 하나·기존 RAM guard를 유지하며 별도 설정을 측정한다. 과거의 속도 튜닝 중단 기록은 당시 요구의 이력으로 남기고, 새 최적화가 품질이나 기존 증거를 대신하지 않게 한다.

- **Hy7 raw 일반16 완료**: `general-context-dev-20260911/hy7-raw-below-normal-v1`의16개를1513.953초에 생성했다. 자동검사 실패0, 원시 출력 무결성·모델 종료·guard 무오류 확인. peak working set7.94GiB, 최소 여유 물리 메모리4.94GiB. summary SHA `a0752d7448083d6cc3204c6a5bd33407959e40cbb6fe70247595f862a74394af`, 코드39개를 실행 시작 시 보존했고 wrapper도 종료·전원요청 해제를 확인했다.
- **한국어 전용 질문 평가 기준 미달**: 새 `fork:none` 답변자는 익명 질문 ID·질문·한국어 번역·사전 허용 한국어 문맥만 읽었다. 영어·정답·다른 후보를 전달하지 않았다. 32개 답변을 동결한 뒤 원문 근거로 root가 별도 채점했다. **30/32=93.75%, 핵심15/16=93.75%**로 전체95%·핵심100% 모두 미달이다. 냉각 종료의 들어 올리기 조건과 고무발/의자 다리의 부품 대상을 정확히 답하지 못했다. 질문에 답한 결과를 해당 문단의 모든 의미 관계나 실제 사람의 학습 효과로 확장하지 않는다. 근거: `.training/quality-evaluation/general-answerability/raw-v1/answers-freeze.json`, `root-judgments-v1.json`, `grade-report-v1.json`.
- **원문 우선 의미 검토**: 별도 도우미가 후보 번역을 보기 전에16개 원문의61개 명제를 고정했다. 그 뒤 익명 후보 A의 번역을 제공해 의미·유창성을 분리해서 대조한다. 명제 계획 SHA `9f54a2b1ab0f8c62c53eab1eb65a17b40656bb9ba39e1e003d33978594512a19`. 질문 작성·한국어 전용 답변·원문 대조의 역할과 증거를 혼동하지 않는다.
- **Qwen v4 기술3개 완료**: 새 `smoke-first3-v4`에서 모델/계약·EOS·guard·무결성·종료를 확인했고 CPU4/BelowNormal이었다. 뒤 두 입력에서 각각1058토큰을 체크포인트로 복원해 실제 재사용했다. 요청 시간 합계는 기존 v3 약504.375초에서 v4 약427.125초로 줄었지만 첫 요청은250.859초로 더 느렸다. 출력 토큰 수도512→567로 다르고 CPU·문맥·캐시 설정을 함께 바꿨으므로 캐시만의 인과효과 또는 전체48 속도 보장으로 주장하지 않는다. 새 v2 문단 경고는 첫3개 모두false지만 이는 대부분 정상인 기술 표본이며95/95 품질 통과가 아니다. summary SHA `8a0f2699b55e199a410eec18b173e755257074912de06cc1a9e93090a6878d3c`.
- **Hy7 contextual 일반16 완료**: `hy7-contextual-below-normal-v1`의 생성·원시 무결성·소유 종료16/16, 기존 자동 검사 실패0을 확인했다. raw와 같은 모델·샘플러·CPU4·8GiB 및 실제 BelowNormal·PID/생성시각, 코드39개·별도 scheduling receipt를 보존한다. 전체2917.860초에는 아래 Modern Standby 공백이 포함된다. 새 한국어 전용 답변을 동결 후 채점한 결과29/32, 핵심14/16으로 기준 미달이다. 익명 의미 검토는 major2·보류1, 중요 오류 없음 확정13/16이다. [일반 문맥 비교 보고서](content/model-comparison/GENERAL_CONTEXT_REPORT_20260911.md)에 질문·의미·답변자 차이·자원 한계를 함께 기록했다.

질문 준비 도구는 [`prepare_general_answerability.py`](scripts/model-comparison/prepare_general_answerability.py), 답변 동결·주석 집계는 [`grade_general_answerability.py`](scripts/model-comparison/grade_general_answerability.py)다. 각각 합성10개와11개 검사를 통과했으며 재사용 검증기5개도 확인했다. grader는 실제 답변 동결 시 코드 해시를 묶으므로 이후 이 버전을 수정하지 않는다. Qwen v4 전체48의 별도 분석기는 [`evaluate_llm_review_v2.py`](scripts/local-qe/evaluate_llm_review_v2.py)이며 합성23개 통과, 원시 응답에서 v2 매핑을 재산출하고 기존 v1 평가기·결과는 보존한다. Hy7 두 실행 종료를 확인한 뒤 14:02:38 KST에 `runs/dev48-v4` 전체48을 시작했다. 최종 집계·의미 밑줄 품질 통과·앱 등록은 아직 미완료다.

후속 기술 대조 `.translation/qe/llm-candidates/qwen35-9b/smoke-technical-receipt-v4.json`(SHA `9a7d26d106c3517a318922a5efdb978362b5bd94253b6a14cb5e5361f7cf752e`)에서 각 run20개 artifact, 동일 template/token, EOS·sampling·guard·소유 종료, v4의847개 BelowNormal 관측과3개 CPU telemetry를 검증했다. v4 peak WS는 v3보다20.67MiB, 관측 private 최대는22.98MiB 증가했다. 원문·정답은 추가로 모델에 보내지 않았고 전체48 평가기에3개를 위장해 넣지 않았다.

raw 익명 의미 검토는 `general-meaning/candidate-a/assistant-review.json`(SHA `5341210a61be130d895c0ac690bd2733558421c63f450fbb05bf3aec084ce233`)에 완료했다.16개 중 none9/minor3/major3/unresolved1, critical0이며61명제 중 보존52/불보존8/보류1이다. major는 적용 대상, 고무발/의자 다리, 이름/명단의 지시 대상이다. `ambiguity-note-001.json`은 원문 his의 선호 해석과 비유일성, 번역의 소유 대명사 생략을 분리하고 보류를 유지한다. 질문의 정답 기준에 맞게 답한 결과가 해당 문단의 모든 세부 관계를 확정한다는 뜻은 아니다.

contextual 실행 중634.829→2319.110초,1684.281초의 관측 공백이 있었다. Windows Kernel-Power 이벤트는13:22:15→13:50:17 KST의 Modern Standby와 겹쳤고 이후 같은 native PID로 처리를 계속했다. `.training/verifications/general-contextual-standby-observation-20260911.json`에7개 이벤트와 공백을 보존했다(SHA `3232bc19832da76283d5a325a7e9f3a1fece10875a4c1382e3417a4fba401e30`). 해당 지연을 모델 연산·paging 속도로만 해석하거나 대기 시간 전체의 CPU 실행을 추정하지 않는다. 이번 증거를 과거 TG27 실패의 원인으로 소급 확정하지 않는다. 덮개·사용자 수면 등에 대한 전원 요청의 한계는 [Microsoft PowerSetRequest](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powersetrequest)를 따른다. 전역 전원 설정은 변경하지 않았다.

## 19. Qwen 완료12개에서 기준 미달 확정·TG27 v5 준비

Qwen v4의 전체48 실행은 16:03:19 KST에 **실패·소유 프로세스 종료·wrapper 전원요청 해제**로 마쳤다. 생성 요청/완료는12개이며 실패 행13의 생성 결과는 없다. 14:24:01.708~15:18:02.223의3240.5초 관측 공백에서 native CPU·page fault 증가가0이었고 Modern Standby와 겹쳤다. 요청guard와 원래 실패를 보존한다. 별도 관측은 `.training/verifications/qwen-v4-dev48-standby-failure-20260911.json`이다.

**품질상 같은 구성을 재개할 필요도 없다.** 완료12개·65artifact를 재검증하고 실제 중요 오류3개(008 자금조달 equity, 009/010 포함된 수와 구성원 정체)를 모두 경고하지 못했음을 확인했다. 009/010은 원시 이유에서 의미 차이를 언급하면서 fluency로만 분류한 실패다. 남은 것을 전부 맞혀도 전체재현율11/14=78.57%, Hy7 2/5=40%, 필수오류최대5/6으로 수용 불가다. 잔여36개·재개 구현·앱 등록을 하지 않고 CPU를 다음 후보에 사용한다. 원시 결과·기존GT·매핑을 고치지 않는다. [Qwen 상세 보고서](content/model-comparison/QWEN35_V4_REVIEW_20260911.md)와 `.translation/qe/llm-candidates/qwen35-9b/dev48-v4-completed12-futility-receipt-v1.json`(SHA `bef6ebeef392a6a4c2f4f40f7e8dfe25a0a035809048c250e4f8a3a8aadd7155`)에 상한과 기술 증거를 구분했다. 빨간구간후보0이므로 의미 구간 정밀도는 미평가다.

TG27은 새 [v5 실행기](scripts/model-comparison/TRANSLATEGEMMA_V5.md)에서 CPU4/BelowNormal·선택8~12GiB와 원래 생성/시간 계약을 유지하고 phase·요청 경과·관측 공백·소유 PID/생성시각·CPU·메모리를 기록한다. 원래 v4는 불변이다. 독립 검토에서 생성 중 정리 실패를 종료 성공으로 잘못 기록할 수 있는 경로와, 지연 관측 후 요청 종료가 deadline을 지워 초과를 놓치는 경로를 발견해 **새 v5에서만 수정**했다. 반환된 원시 응답은 실패 검사 전에 보존한다. 최종 runner SHA `946119b19e982e311680491cc6cd4b4ab9109336873ae4ffac27fecf0d2c6d54`, 합성53개·help 통과다.

새 [v5 검수 연결](scripts/model-comparison/V5_REVIEW_ADAPTERS.md)은 개발18·읽기6 준비부터 집계까지 별도 파일로 지원한다. 기존 v4 검증에 실제 v5의 원시응답 SHA·소유 종료·우선순위·행별 요청/응답·관측 간격·CPU를 추가 검사한다. 기존 회귀53개+전용12개=65개·help4개 통과다. 실제 모델/검수 packet 생성과 구분하며 다음 TG27 실행은 종료·자원 두 관측·고정 질문지를 확인한 후 새 폴더에서 시작한다. 사용자 앱·전역 메모리/전원 설정을 변경하지 않는다.

## 20. RAM 시작 조건 보류·금융 질문지 동결

2026-09-11 16:30:23/26 KST 두 실제 관측의 가용 물리 메모리는11,036,160,000/11,069,845,504바이트, 최솟값 약10.28GiB였다. commit 여유와 이전 Qwen 종료·전원요청 해제·고정 파일 해시는 통과했고 native 모델은 두 번 모두0개였다. **최소8GiB 모델 상한 + 3GiB 시작 여유 = 11GiB 조건 미달로 TG27 로딩을 보류**했다. 더 낮은 여유 기준이나 사용자 앱 종료로 맞추지 않는다. 당시 실제 관측은 [receipt](.training/verifications/tg27-v5-slot-preflight-20260911-agent-001.json), SHA `8d8f9db9428e2bbd0a92c2d34c73eb675bbca9cf00b241025dc898a500fe46a5`에 남겼다. 그 전에 저장하지 못한 최초 검사 수치를 재구성하지 않는다.

[모델 없는 시작 검사](.training/verifications/PREPARE_TG27_V5_SLOT_20260911.md)는 CPU·RAM을 적게 쓰는 읽기 검사와 새 receipt 생성만 한다. 3초 간격 두 관측의 최솟값으로8~12GiB 중 시작 여유가 맞는 최대 budget을 고르고, PASS여도 모델 자동 실행·slot 예약·반복 재시도를 하지 않는다. 합성7개 통과이며 이 상태를 실제 TG27 추론 검증으로 보고하지 않는다.

금융 개발18·읽기6의48문항(핵심24)을 원문만 본 새 도우미가 작성하고 root가 원문 기준을 검토했다. 두 질문에 기존 채점 기준의 비용 금액을 명시하는 문구 수정만 v2로 추가했다. [질문지 v2](.training/quality-evaluation/finance-answerability/source-only-v1/assistant-questionnaire-v2.json), SHA `a946103f3068b64e54107f4b96a01834857b146d2190e7b5f5e009b9cef1d75b`를 새 TG27 생성 전에 동결했다. v1과 나머지46문항·모든 기준·인용을 보존한다. 기존 Hy7/Hy30 출력보다 먼저 작성됐다고 주장하지 않는다. 새 한국어 전용 답변자는 영어·정답·기존 판정을 보지 않고 답하고, 답변 동결 뒤 별도 원문 대조로 채점한다. 실제 답변·질문 통과 결과는 아직 없다.

여유를 기다리는 동안 평가 도구·문서를 정리한다. Qwen 후속은 문체 노트를 제외한 의미 전용 새 계약을 준비하며 기존 v4의 출력·정답·경고 분류는 변경하지 않는다. 지시가 짧아져 CPU·메모리나 탐지 성능이 개선됐다는 효과는 새 실측 전까지 주장하지 않는다.

16:57:50/53 KST 새 두 관측도 가용 physical 최솟값10,248,232,960바이트(약9.54GiB)로 TG27 시작 조건에 못 미쳤다. commit15,068,934,144바이트와 native0·파일/종료 확인은 통과했다. [두 번째 보류 기록](.training/verifications/tg27-v5-slot-preflight-20260911-root-002.json)을 별도 보존하며 모델 실행은0회다.

Qwen 후속의 [의미 전용 계약](scripts/local-qe/llm-qwen35/CONTRACT_V2.md)은 새 prompt/schema/contract 파일로 구현하고 신규15개·기존24개 형식 검사를 통과했다. 이는 의미 탐지 성능 검증이 아니다. [개발8 중단 규칙](scripts/local-qe/llm-qwen35/SCREEN_GATE_V1.md)은 기존 개발 오류4·정상4를 고정한 뒤 실제 유효 응답의 첫 오탐/미탐에서 남은 generation을 중단하도록 한다. 합성5개 통과이며, root 선택자는 기존 오류·출력·판정을 본 상태라고 명시했다. 모델 요청에는 원문·번역·문맥만 허용하고 정답은 외부 중단 판정에만 사용한다. 8개 모두 맞아도 전체 baseline 통과가 아니다.

[6GiB 자원 가설](.training/verifications/QWEN_V4_6G_RESOURCE_FEASIBILITY_20260911.md)은 이전1,960개 메모리 기록의 peak WS5.236GiB, 관측 private 최대0.730GiB를 근거로 새 Qwen 개발8 한 번을 검토한다. 실제 hard WS5.9375GiB까지 관측 peak 대비0.701GiB 여유가 있으나 scratch·paging·새 prompt 효과는 미확인이다. 별도 새 프로필의 cap6GiB와 시작physical/commit각9GiB를 준비하며 TG27의11GiB 조건은 그대로다. 과거8GiB 실행을6GiB 검증으로 바꾸거나 메모리 절감·품질 동일을 주장하지 않는다. 새 runtime/token 감사/freeze/실제8개 실행은 아직 연결 중이다.

## 21. TG27 v5 개발18 시작·기존 Hy30 읽기 질문 평가 완료

17:11:46/49 KST [세 번째 자원 검사](.training/verifications/tg27-v5-slot-preflight-20260911-root-003.json)는 physical 최솟값11,876,282,368바이트(11.06GiB), commit15,982,518,272바이트·native0·고정 해시/종료 확인을 통과했다. 규칙대로 가능한 최대 budget8GiB를 선택하고 새 개발18 폴더 `.training/comparisons/linguistic-dev-20260910/translategemma-27b-v5-slot-20260911T081146068786Z`에서 실제 v5를 시작했다. wrapper 기록은 `.training/verifications/tg27-v5-dev18-20260911T081146-awake.json`이다. 코드35개를 원래 SHA와 같은 바이트로 보존했다.

첫 문단에서 소유 native PID72044·creationTicks134335879906617313·실제 BelowNormal(16384)을 확인했다. 17:15:59 관측은 첫 요청 경과145.5초, WS7.9374GiB·peak7.9376GiB·가용physical3.82GiB·누적CPU358.72초였다. 요청 안의 생성 토큰 진행률은 알 수 없으며 CPU 증가만으로 번역 완료 비율을 만들지 않는다. 모델 하나 원칙에 따라 Qwen은 코드·합성 준비만 진행한다. **개발18 완료·종료·읽기6·의미/질문 검수는 아직 미완료다.**

[금융24 질문 평가 도구](scripts/model-comparison/FINANCE_ANSWERABILITY.md)는 새4파일·합성13개를 통과했고 답변자의 불확실성과 채점자의 보류를 구분한다. 완료한 Hy30 v4 읽기6의 보존된 실제 증거를 검증해 새 익명12문항을 만들고 `fork:none` 한국어 전용 답변을 동결 후 수동 채점했다. 결과는 **전체10/12=83.33%, 핵심4/6=66.67%**, 누락·판정 보류·충돌0으로 기준 미달이다. 새 엔진 호출0, 질문은 기존 Hy30 출력보다 나중인 사후 평가이며 두 경계 사례는 원문 우선 질문 작성자의 모델 익명 의견도 받았다. [읽기 질문 보고서](content/model-comparison/FINANCE_READING_ANSWERABILITY_REPORT_20260911.md)에 답변·freeze·근거·실패 이유·한계를 보존했다. 기존 의미 판정을 새로 덮어쓰지 않는다.

## 22. 최종 중단 인계 — 완료 내용·주요 원인·다음 작업

### 22.1 최신 사용자 지시와 현재 실행 상태

최신 지시는 순서대로 다음과 같다.

1. 메모리·CPU를 최적화하면서 진행한다.
2. 오류 난 부분을 계속 쌓고 일반화하여 주요 원인을 파악한다.
3. **하던 작업만 정리하고 멈추며 지금까지 한 내용과 앞으로 할 일을 이 문서에 수정한다.**

마지막 지시에 따라 장시간 진행 중이던 TG27의 현재 상태를 보존하고 종료했다. **현재 모델 실행을 계속 기다리거나 새 실험을 예약한 상태가 아니다.** 코드·자료·판정·모델 가중치·개인 기록은 보존한다. 통합 오류 원장은 아직 구현하지 않았으며, 오류 근거는 아래 기존 산출물에 남아 있다. 새 요청으로 오류 원장까지 추가 구현을 시작하지 않고 재개 순서에 기록한다.

TG27 v5 실행 경로는 `.training/comparisons/linguistic-dev-20260910/translategemma-27b-v5-slot-20260911T081146068786Z`다. 17:12:35~17:33:01 KST 실행 후 **완료0/18, 첫 문단 미완료, 읽기6 미실행**으로 마쳤다. 새 출력·의미 점수는 없다. 종료 시 PID72044·creationTicks134335879906617313·실행 파일 경로를 실제 process handle로 대조한 뒤 이 소유 native 하나만 종료했다. 다른 사용자 앱·전역 메모리/전원 설정은 변경하지 않았다.

- [사용자 요청 중단 기록](.training/verifications/tg27-v5-user-stop-20260911.json): SHA `650ecae11ced9ccf0d4bac49350b51dd8406c7620414ccd4f61f7b532103c191`, native exit code3221225786, 실제 종료 확인.
- [원래 실행 summary](.training/comparisons/linguistic-dev-20260910/translategemma-27b-v5-slot-20260911T081146068786Z/summary.json): SHA `5bf03452446a58f232792d4ff23bc2ccf6e6e15a48180de60403b094cdb5889b`. 원시 `status:failed`, `error:ConnectionResetError`, `childProcessStopped:true`를 바꾸지 않는다. **이 오류는 사용자 요청으로 native를 종료한 뒤 발생했으므로 새 자발적 엔진 실패나 품질 실패로 집계하지 않는다.** guard abort·monitor error는null이며 deadline 초과도false다.
- [wrapper 종료 기록](.training/verifications/tg27-v5-dev18-20260911T081146-awake.json): SHA `a0cfe8554a87659fec60c68cdeb874b86609abc35ef63732cadeeab172697c99`, childStopped/released 모두true. 17:35:16 KST 읽기 확인에서 native 및 알려진 PID72044/24196/35592 모두0개였다.
- 실제 peak WS8,522,936,320바이트(약7.94GiB), private 관측 최대1,072,717,824바이트였다. 실행 중 시스템 가용physical 최솟값은842,346,496바이트로 낮아졌으므로 처음의3GiB 여유가 계속 보장됐다고 주장하지 않는다. 최대 heartbeat 공백286.984초는 원인 미확정이다. page fault는 soft/hard 합계이며 디스크 paging 원인의 증거로 단정하지 않는다. 코드35개·원래 실패/미실행18행·메모리 기록을 보존했다.

### 22.2 완료한 것과 실제로 확인한 범위

| 영역 | 완료·실측 결과 | 아직 의미하지 않는 것 |
|---|---|---|
| 학습 수용 기준 | 언어학·교육학 근거를 검토하고 집합별 의미·용어·질문·탐지·구간 기준을 동결 | 보편적인 ‘95%면 안전’ 학술 보장·실제 인간 학습 효과 |
| 앱 의미 위험 표시 | 평가 이력·큐·캐시·lease·취소·검수 충돌·유휴/전환 종료·reader 표시 구현. 최종171개 통과·조건부2개 제외, build/typecheck·모의UI9개 확인 | 의미 검사 모델의95/95 통과 |
| 실제 앱 번역 | 격리 DB에서 Hy7 HTML3문단+PDF1페이지4블록 생성·저장·캐시. 원문/개인 기록 보존, 자동검사7/7 | 제목·쪽번호를 포함한7개를 독립 금융 본문7개로 세는 것, 전체 학습 수용 |
| 운영 데이터 | 일관 백업 뒤 schema3 마이그레이션,14개 테이블 행 해시·개수 보존 | 운영 서버/worker를 새로 기동했다는 것 |
| Hy30 비교 | 개발18·읽기6 생성·익명 의미 검토 완료. 중요 오류 각각5/18·1/6 | 학습 기준 통과·앱 엔진 교체 |
| Hy7 일반16 | raw/contextual 각각16/16 생성. 질문30/32·29/32, 핵심15/16·14/16. 핵심명제는 각각52/61 보존 | 문맥 프로필만으로 의미 오류 해결, 서로 다른 답변자 점수 차이의 인과 효과 |
| Hy30 읽기 질문 | 기존 번역 재사용·새 한국어 전용 답변12개 동결·수동 채점:10/12, 핵심4/6. 새 번역 호출0 | 질문을 기존 출력보다 먼저 고정한 독립 실험, 기존 의미 판정 수정 |
| COMET QE | 전체48 실제 점수화. 필수6개 탐지에45개 경고, 정밀도14/45=31.11% | 기존85/60 또는 새95/95 수용·등록 |
| Qwen v4 QE | 완료12개 원시/기술 증거 재검증. 실제 중요 오류3개 모두 미탐, 잔여36을 모두 맞혀도 재현율 상한11/14 | 전체48 완료·의미 밑줄 정밀도 측정·등록 |
| TG27 v5 | 소유 자원 관측 실행기·v5 검수 연결 구현 및 합성53개/65개. 실제 로드·첫 요청·자원/종료 경로 관측 후 사용자 요청 중단 | 개발18 또는 읽기6 생성·의미 평가 완료 |

세부 원수·근거·판정 provenance는 [일반16 보고서](content/model-comparison/GENERAL_CONTEXT_REPORT_20260911.md), [읽기 질문 보고서](content/model-comparison/FINANCE_READING_ANSWERABILITY_REPORT_20260911.md), [Qwen v4 보고서](content/model-comparison/QWEN35_V4_REVIEW_20260911.md)를 따른다. 현재 **번역·밑줄 두 영역을 각 baseline에서 함께 충족한 구성은 없다.** 등록 Hy7와 기존 자료는 유지하며 새 QE를 등록하지 않았다.

### 22.3 준비까지만 끝낸 Qwen 다음 구성

기존 v4·GT·프로필·출력·판정은 불변이며, 아래는 별도 새 파일이다.

| 새 준비물 | 상태 |
|---|---|
| [의미 전용 contract v2](scripts/local-qe/llm-qwen35/CONTRACT_V2.md) | 문체 노트 없는 두 배열, 엄격 JSON·실제 인용·UTF16 검증. 신규15개·기존24개 통과 |
| `material_warning_v3.py`, `qwen-material-warning-policy-v3.json` | major/critical 또는uncertainty를 경고. minor 이유의 키워드로 사후 승격하지 않음. 신규15개·기존5개 통과 |
| [고정 개발8 및 중단 규칙](scripts/local-qe/llm-qwen35/SCREEN_GATE_V1.md) | 오류4·정상4, 첫 실제 FP/FN에서 이후 generation 중단. 합성5개. 개발 실패 노출·표본 의존성 명시 |
| [runtime v5·token budget v2·profile v4](scripts/local-qe/llm-qwen35/README_V5.md) | CPU4/BelowNormal,6GiB 관측 상한·9GiB 시작physical/commit, 원래EOS/sampling/4096 context/2048 output 유지. 합성16+14개·help2개 |
| [dev8 분석기 v3](scripts/local-qe/LLM_EVALUATION_V3.md) | 원시 응답·새 mapping·gate·실행 정체성 재검증, 합성11개. 준비 정체성 일부는 합성 fixture stub이므로 실제 준비/실행 검증과 구분 |

**새 모델 호출·vocabulary native 실행은0회**다. `resource-profile-v4.json`의 `tokenBudgetAudit:null`이며 freeze·execution binding도 없다. 현재는 실제 실행을 허용하는 상태가 아니다. root의 전체 코드 검토, 실제 vocab 감사, profile 최종화, freeze와 외부 binding 생성이 남았다. 코드 hash를 감사에 묶으므로 **코드 수정·검토를 먼저 끝낸 다음 감사**해야 한다.

현재 핵심 해시:

```text
contract_v2.py       463561009a9eb966b97893d8447315730cf66b454332f7811c48a96b974b8378
material_warning_v3  c1e8209c8ecc5e4c6ffdb3563c27bc2a6d1eb27de08b652fb6362a3e1bfc9cd7
screen_gate_v1.py    a622f193b34403b87199dda0ea2017697f9249464c290e64ec7dbc23c32e283a
runtime_v5.py        0413a501ca8b98f4bc2552f84def727d79668dbac9d5568120d080dfdfe58300
token_budget_v2.py   57b3035f575679584b9602e1be80299dcedf6a44025386e30e62296ecb6c1574
evaluate_llm_review_v3.py c2683be69575ada3336ef0fe5c3565bb79d9ee6d7b7e89ca14d1bdfb4883d6d6
```

준비 상세는 `.training/verifications/qwen-v5-preparation-synthetic-20260911.json`에도 있다. 첫 FP/FN의13/14·14/15 상한은 **관측한 출력을 그대로 유지할 때**의 조건부 상한이다. 다시 추론할 때의 출력 예측이 아니며, 좋은 결과가 나올 때까지 반복하지 않기로 정한 개발 중단 규칙이다. 8/8도 전체48·각 모델/집합/교차집합95/95·필수6개·빨간 구간 정밀도 통과가 아니다. 현재 분석기는dev8 전용이므로 전체48 연결은 별도 후속 구현이다.

중단 전에 진행하던 [금융 원문 용어 inventory](.training/quality-evaluation/finance-terms/source-inventory-v1/README.md)도 저장을 마쳤다. 원문24/24개, 출현149건(개발92·읽기57), 고유개념83개, 제외/경계 출현44개와 한정·모호성8그룹을 기록했다. [최종 JSON](.training/quality-evaluation/finance-terms/source-inventory-v1/source-inventory.json) SHA는 `61d9b4a1535b2bcb0e67e593e975bc6945da987ec277cf4a8bd5ee9e2eff7930`이다. 149개 실제 인용·Unicode code point 위치·원문/문맥 SHA·중첩0·입력 불변 검사를 통과했다. 이는 UI용 UTF16 위치가 아니며 변환 없이 혼용하지 않는다. LDEV26-015는 금융 용어 분모0·미평가다. 현재 후보·GT는 보지 않았으나 허용 용어집의 과거 교정 예시 노출과 TG27 생성 시작 뒤 작성 사실을 공개했다. **root의 전체 의미/선정 검토·실제 후보별 용어 채점·용어95% 판정은 아직 없다.** 원문 우선 도우미 inventory이며 인간 gold나 학습 데이터가 아니다.

### 22.4 오류를 누적하며 검증할 주요 원인

다음은 현재 관측에서 묶은 **오류 유형과 원인 가설**이다. 모델 내부의 학습 원인까지 실험으로 확정했다는 뜻은 아니다.

| 반복 관측 | 현재 확인한 내용 | 다음에 검증할 것 |
|---|---|---|
| 문맥상 개념 선택 | 자금조달 equity가 공평성/균형으로, cost of equity가 주식의 비용으로 바뀌는 실제 오역 | 일반 뜻과 금융 뜻의 최소대립·용어 inventory로 뜻 선택을 대조. 무조건 금융 단어 치환 금지 |
| 관계·수량 대상 손실 | how many→which, 주체/적용 대상, 시간 범위, 연산 방향, 부품 지시 대상 등에서 의미가 바뀜 | 단어·숫자 보존과 명제 관계를 따로 평가. 숫자 검사 통과를 의미 통과로 보지 않음 |
| 구체성이 넓은 표현으로 약화 | 평가 시점 이동→시간 분석, 수취인 부재→수령 불가처럼 답변에 필요한 사실이 흐려짐 | 의미 severity와 핵심 질문 답변을 별도로 측정. 유창한 요약에 누락 사실을 보충해 정답 처리하지 않음 |
| 검사기의 분류 실패 | Qwen v4는009/010의 차이를 원시 이유에 쓰고도 fluency에만 넣어 중요 경고를 만들지 않음 | 문체 노트를 제거한 새 의미 계약을 개발8에서 한 번 진단. 기존 결과의 사후 재분류 금지 |
| 일반 저신뢰 경고의 낮은 정밀도 | COMET은 필수 오류를 모두 찾으려면45/48개를 경고해 오경고가 많음 | 재현율과 정밀도를 함께 보고, 전체 경고로 덮거나 baseline별 실패를 평균으로 가리지 않음 |
| 실행 지연·중단 | CPU/메모리 guard, 일부 Modern Standby 중첩, 관측 공백, 큰 mmap 모델의 긴 요청을 기록함 | 사용자 중단·시간초과·환경 문제·품질 오류를 다른 원인으로 기록. paging/잠금/절전 원인은 필요한 실제 증거가 없으면 미확정으로 남김 |

**통합 오류 원장·자동 누적 도구·원인별 집계 보고서는 아직 미구현**이다. 지금은 개별 원시 출력·JSON 판정·futility/절전/중단 receipt·보고서에 축적되어 있다. 향후 원장은 최소한 원문/번역/문맥 SHA, baseline·모델·실행/판정 버전, 실제 오류/보류/사용자 취소 구분, 심각도·유형, 근거 파일·인용, 자동검사 결과, 원인 가설·확신 수준·반례·다음 검증을 연결해야 한다. 같은 출력의 재검토를 새 모델 오류로 중복 합산하지 않는다. 원본 판정은 덮지 않고 후속 판정과 연결한다. 합성 테스트 오류·실제 추론 오류·실행 취소를 섞지 않는다.

### 22.5 재개 지시 이후의 진행 순서

1. **현재 작업 상태 확인:** 이22절, AGENTS/제품/시스템 문서, git diff와 소유 프로세스·모델 등록 상태를 읽는다. 기존 더러운 작업 트리·원문·DB·검수·실패 산출물을 초기화하지 않는다. 현재 활성 모델과 실행 예약은 없다.
2. **오류 원장과 원인별 분석부터 연결:** 위 기존 산출물을 참조하는 중복 방지·추가 기록 방식으로 누적하고, 관측과 원인 가설을 구분한다. 새 원문 용어149출현의 선정·허용 뜻·제외 범위를 root가 검토한 뒤 후보별 의미 채점에 연결한다. 독립 test 내용을 조정용 원장으로 유출하거나 알려진 개발 정답을 모델 prompt에 넣지 않는다.
3. **Qwen 작은 진단 준비를 완성:** 새 runtime/분석기 검토 → 단일 슬롯에서 vocabulary-only 감사 → profile 최종화 → `freeze-v5-dev8.json` → 외부 execution binding → 시작RAM 두 관측 → 개발8 한 번 순서다. 첫 FP/FN·기술 실패에서 중단하고 원시 기록을 남긴다. 8개가 모두 맞아야 별도 전체48 연결을 검토하며, 빨간 구간 의미 검토는 다시 따로 수행한다.
4. **TG27 비교는 미완료로 유지:** 새 폴더에서 개발18 → 소유 종료·무결성 → 같은 budget으로 읽기6 → v5 검수 연결·금융 질문·용어 평가를 수행한다. 가능한8~12GiB 중 실행 직전 두 관측에 +3GiB 여유를 만족하는 최대값을 선택하고, 읽기6 앞에서도 같은 budget의 여유를 다시 확인한다. 현재0/18을 재개 완료나 의미 오류0으로 세지 않는다. 오래 걸린다는 이유만으로 비교를 없애지 않되, 중단/자원 제약을 숨기거나 사용자 앱을 임의로 종료하지 않는다.
5. **번역 기반·일반 능력·도메인 적응을 분리:** Hy7 raw/contextual 일반16과 Hy30/TG 비교의 의미·질문·용어 결과로 다음 개선을 정한다. 필요한 경우 작은 검토된 train 전용 적응 설계로 이어가되 dev/test로 가중치를 조정하거나 실패 기준을 완화하지 않는다. 현재 가중치 재학습을 새로 완료한 상태는 아니다.
6. **마지막에 앱 적용·운영 기동 판단:** 통과한 모델/실행 프로필만 선택하고 실제 격리 앱 생성·저장·캐시·소유 종료를 확인한다. 운영 앱 재기동은 아직 수행하지 않았다. 승인된 모델이 없으면 기존 등록 상태와 미검증 안내를 유지하며 실패 후보를 등록하지 않는다. 본문·탐지·구간·독립 시험·사용자 검수의 완료를 구분해 최종 보고한다.

새 독립 시험60개, 실제 인간 학습 효과 측정, 전체 baseline95/95 수용은 완료하지 않았다. 기존 사람이 검수하지 않은 자료를 사람 gold로 바꾸지 않으며, 자동 형식/인용 검사나 도우미 합의를 그 대신 사용하지 않는다.

## 23. 사용자 재개 승인 이후 — 2026-09-11 진행 중

사용자가 인수인계 브리핑 뒤 불필요 백그라운드·메모리·캐시를 정리하고 진행하라고 승인했고, `마저 해`로 재차 진행을 지시했다. 이전 중단 지시를 현재 실행 금지로 해석하지 않는다.

- **정리 완료:** 부모가 없는 오래된 Next telemetry PID40996을 handle·생성시각·정확한 명령으로 대조 후 종료. `.next/cache`와 scripts의 재생성 가능한 Python 캐시 149파일,198,388,471바이트 삭제. 모델·원문·DB·학습/검토 증거와 사용자 앱·전역 메모리 정책은 보존했다. 근거 `.training/verifications/cleanup-resume-20260911T0913025820828Z.json`.
- **보조 검토 제한:** 병행 도우미3개가 서비스 사용량 제한으로 중단됐다. 원장/용어 검토 산출물은 만들지 못했고 Qwen의 부분 읽기 의견만 받았다. root가 직접 이어서 검토·구현했으며 독립 검토 완료로 표시하지 않는다.
- **Qwen 준비 수정·검증:** `evaluate_llm_review_v3.py`가 runtime의 `statIdentities`에 포함된 install manifest를 누락하는 연결 오류를 수정했다. 회귀 포함 합성89개 통과, 실제 vocab8개·순차 native10개 종료·입력 최대638+출력2048<4096 확인. pending profile만 최종화하고 이전 바이트를 보존했다. 실제 producer/evaluator 준비 연결도 검증했다. `.training/verifications/qwen-v5-dev8-root-review-resume-20260911.json`과 같은 경로의 binding, `.translation/qe/llm-candidates/qwen35-9b/freeze-v5-dev8.json`을 따른다. 준비 helper 첫 import 경로 오류는 실행 전이었고 수정 후 준비를 완료했다.
- **Qwen 실제 진단 실패:** 새 `runs/semantic-v2-dev8-v5`는 첫 LDEV26-010에서 `semantic_issues:[]`, `uncertainties:[]`를 반환해 실제 FN1,완료1/8 뒤 `stopped_futility`. 생성19토큰, 요청 약43.156초, wrapper66.844초. 기술/EOS/입출력/guard/종료/실제 정체성을 사후 평가기로 검증했다. peak WS5,618,454,528바이트·private 관측780,410,880바이트, 최대 heartbeat0.641초. 원래 판정/출력을 유지할 때 전체48 재현율 상한13/14이며 등록·나머지7개·전체48 재실행은 하지 않는다. 실제 결과 `.training/quality-evaluation/qwen-v5-dev8-evaluation-resume-20260911.json`, 종료 `.training/verifications/qwen-v5-dev8-resume-20260911-awake.json`. 첫 응답에 이유도 없으므로 기존 v4의 fluency 분류 문제만으로 이번 미탐을 설명할 수 없다.
- **통합 오류 기록 완료:** `scripts/model-comparison/error-ledger/ledger.py`에 원문/번역/문맥 해시·원시 판정·근거 인용·가설 분리·추가 기록·중복 방지 구현. Windows 잠금 종료를 수정했고 원장8개+용어4개, 합성12개 통과. 현재 `.training/quality-evaluation/error-ledger/v1`에609사건(번역 검토128·질문76·QE104·실행3·용어298)을 연결했다. 311사건 단계에서 재실행 추가0·재사용311을 확인했고 기존 검토회차·원문 판정은 보존했다. Qwen 미실행36/7개는 실제 FN으로 세지 않는다. [원인별 보고서](content/model-comparison/ERROR_LEDGER_REPORT_20260911.md)에 관측 유형·반례·미확정 원인·다음 검증을 분리했다.
- **원문 용어·기존 후보 채점 완료:** 원문24개·용어149출현·제외44개를 root가 모두 읽고 해시/인용/범위 확인. 분모149 유지, REAL26-004의1년 기간/주기 주석만 별도 정정했다. 원래 inventory와 원문은 불변이며 `.training/quality-evaluation/finance-terms/source-inventory-review-20260911/`에 보존했다. 이후 Hy7/Hy30 실제 번역을 읽어298개 수동 판정: Hy7 개발86/92·읽기55/57·전체141/149=94.63%, Hy30 개발84/92·읽기55/57·전체139/149=93.29%(보류3). 원문·번역 근거와 보류 이유는 [용어 보고서](content/model-comparison/FINANCE_TERM_REVIEW_20260911.md), `candidates-root-v1/root-judgments.json`, `candidates-root-v1-evaluated/summary.json`에 남겼다. 비맹검 도우미 검토이며 사람 gold/독립 시험이 아니다. term_review.py는 실제 packet에 코드 해시가 묶였으므로 이후 이 버전을 수정하지 않는다.
- **TG27 실제 새 개발 실행 중:** 첫 재개 사전검사는 물리11GiB 미달로 보류, 두 번째 검사 PASS/budget8. `.training/verifications/tg27-v5-slot-preflight-resume-20260911-002.json` 확인 후 `.training/comparisons/linguistic-dev-20260910/translategemma-27b-v5-resume-20260911-b8` 시작. wrapper 기록 `.training/verifications/tg27-v5-resume-20260911-dev18-awake.json`, 현재 실행 shell session49311. native 최초 PID53328/creationTicks134335943084635759. PID를 재사용한 종료 금지. 코드35개 원래 해시와 같은 바이트로 보존했다. 이후 진행·완료 여부는 현재 파일과 소유 프로세스로 확인한다. 읽기6은 아직 시작하지 않았다.

TG27 첫 문단 LDEV26-001은19:18:42 KST에 생성됐다(초기 후속 문구의20시는 UTC→KST 변환 오류로 정정). 요청1192.516초, 입력107·출력48토큰·잘림false였다. `$`가 한국어 ‘달러’로 표현되어 `currencySymbolsPreserved:false`, 나머지 숫자/비어있지 않음/제어토큰/잘림/종료 검사는 true였다. 원시 자동 검사 실패를 통과로 고치지 않는다. 이후 둘째 문단에서 실패·종료한 내용과 재시도는24절을 따른다.

원문/운영 DB/앱 등록 변경과 새 가중치 학습은 없으며, 앱 전체 테스트를 새로 실행한 것으로 보고하지 않는다. Qwen 실패는 기준을 낮추거나 새 프로필 반복으로 성공 처리하지 않는다. 계속할 일은 TG2718+6 완료·동일 원문 용어 및 의미 평가·가능한 독립 한국어 전용 질문 평가·근거 기반 다음 개선 판단이다. 보조 도우미 사용량 제한으로 새 독립 답변자 검토를 수행할 수 없으면 root의 원문 열람 상태를 숨겨 한국어 전용 답변자로 대체하지 않는다. 통과 후보가 없으면 기존 Hy7와 의미 검사 미등록 안내를 유지한다.

## 24. 연결 실패 재시도 승인 — 2026-09-12 진행 상태

사용자가 `네트워크 끊겨서 실패한거는 다시 해봐`라고 지시했다. 기록상 TG27의 ConnectionResetError는 외부 인터넷 장애가 아니라 소유 native의 고정 요청 제한 종료에 따른 로컬 연결 종료다. 두 긴 관측 공백4070.984/11499.360초가 Windows Modern Standby(덮개·전원 버튼 포함)와 겹쳤고 그 사이 native CPU 증가는1.0625/2.5625초였다. 두 번째 요청의 최종 경과16529.5초가7200초 제한을 초과했다. 공백 전체를 모델 연산 시간으로 해석하지 않는다.

- **이전 재개 실행 종료 확인:** `translategemma-27b-v5-resume-20260911-b8`는 실제 완료1·요청 실패1·미실행16, 총18행으로 보존했다. wrapper는18:58:06~23:54:15 KST, 경과17768.516초였고 소유 native 종료·전원 요청 해제를 확인했다. 첫 재시도 검사에서 native0을 두 번 관측했다. 부분 raw 응답/EOS/토큰/해시·코드 snapshot35개 등79파일을 확인했으나 전체 완료 v5 검사를 통과했다고 표시하지 않는다. 근거 `.training/verifications/tg27-v5-resume-standby-failure-20260911.json`, `tg27-resume-power-events-20260911.json`.
- **원장 추가:** 위 실제 실행 실패를 별도 사건으로 연결해610사건(번역128·질문76·QE104·실행4·용어298)이 됐다. 합성12개 재통과, 기존609개 재사용·새1개. 원시 번역·의미 판정과 실제 실패 원인을 혼합하지 않는다.
- **9GiB 로딩 전 거부2회:** 두 번의 사전검사는9GiB를 선택했지만 실제 producer 진입 때 RAM이 감소해 `memory-preflight/insufficient_available_physical_memory`로 종료됐다. native 모델 로딩·생성은0회다. `translategemma-27b-v5-user-retry-20260911-b9`와 `...-b9-02`, 각 wrapper/사전검사 기록을 보존했다. 이미 승인된 재시도의 통상적인 자원 조정이며 새 승인을 요구하지 않았다.
- **8GiB 실제 재시작:** 위 두 실제 producer 로딩 직전 관측과 새3초 간격 두 관측의 최솟값12,724,715,520바이트를 사용해 가능한 최대 budget8GiB를 선택했다. 기존 +3GiB physical·commit 조건은 완화하지 않았다. `.training/verifications/tg27-v5-user-retry-slot-effective-20260911.json`에서 확인 후 새 `.training/comparisons/linguistic-dev-20260910/translategemma-27b-v5-user-retry-20260911-b8`를 시작했다. CPU4·BelowNormal·코드35개 snapshot을 유지하고 기존 부분1개를 새 완료18에 끼워 넣지 않는다.
- **현재 실행:** shell session88696. wrapper PID55732, producer PID22900, native 최초 PID24872/creationTicks134336123600195721. 실제 시작23:58:59 KST, 첫 생성 요청23:59:44 KST. wrapper 기록은 `.training/verifications/tg27-v5-user-retry-20260911-dev18-b8-awake.json`이다. 현재 첫 문단 처리 중이며 PID만으로 종료하지 말고 현재 handle/생성시각/실행 경로를 재확인한다. 이후 현재 상태는 새 run 파일과 실제 프로세스로 확인한다.

실행 중 덮개 닫기·전원 버튼의 절전이 같은 제한 종료를 만들 수 있음을 사용자에게 알렸다. 기존 wrapper의 실행 중 전원 유지 요청만 사용하며 전역 전원·덮개·메모리 설정은 바꾸지 않았다. 재실행이 실제로 완료되면 소유 종료·무결성 후 **같은8GiB** 시작 조건으로 읽기6을 진행한다. Qwen v5의 실제 의미 미탐은 재연결로 해결되는 실패가 아니므로 재실행하지 않는다. 운영 앱·DB·등록은 변경하지 않았다.

00:23:15 KST에 재시도의 첫 문단을 실제 생성했다. 요청1411.015초·48토큰이며 번역 텍스트는 이전 실행 첫 문단과 같다. 새 모델이 다시 생성한 것이므로 이전 결과를 캐시 재사용했다고 표시하지 않는다.00:27 관측에서1/18, LDEV26-002 진행·native24872/동일 생성시각·BelowNormal·WS약7.94GiB를 확인했다. 한 문단의 시간 차이를 같은 품질/성능 보장으로 확대하지 않는다.

**순차 연결 실행 중:** `.training/verifications/continue-tg27-user-retry-20260911.py`를 shell session37066에서 시작했다. 새 native를 띄우는 대기 작업이 아니라 현재 dev 종료를5초 간격으로 읽는 가벼운 coordinator다. 개발18 완료·wrapper/소유 종료 → 기존 v5 검증기로 Hy7와 새 검수 packet 준비 → 새 두 관측의8GiB 시작 여유/native0 확인 → 읽기6 → 읽기 종료·v5 검증/packet 준비까지만 수행한다. 의미 판단·한국어 전용 답변·학습·앱 등록은 하지 않는다.13개 종료/자원 조건과3개 파일 기록 중 읽기 조건, 계획 모드의 호출0을 확인했다. 현재 대기 연결만 실제 실행했으며 미래 읽기6까지 검증된 것으로 표시하지 않는다.

- coordinator 상태·이력: `.training/verifications/tg27-user-retry-continuation-20260911.json`, 같은 이름의 `.jsonl`. 시작 단계 `waiting_for_current_development`.
- 예정 읽기 실행: `.training/comparisons/real-reading-check-20260910/translategemma-27b-v5-user-retry-20260911-b8`; wrapper `tg27-v5-user-retry-20260911-reading6-b8-awake.json`.
- 예정 검수 packet: 개발/읽기 각각 `prepared-tg27-user-retry-v5`.
- **중복 기동 금지:** coordinator가 대기/진행 중일 때 root가 읽기 모델을 별도로 시작하지 않는다. 실패/여유 부족/파일 불일치에서는 자동 재시도 없이 종료한다.
- **사용자가 중단을 요구하면:** 먼저 `.training/verifications/tg27-user-retry-continuation-20260911.cancel` 파일을 만들어 이후 기동을 막고, 현재 활성 native를 기존 소유 handle·생성시각 대조 방식으로 종료한다. cancel 파일 자체가 이미 실행 중인 native를 종료하는 것은 아니다. coordinator는 활성 읽기 모델을 과거 PID만 보고 강제 종료하지 않는다.

사용자의 추가 `마저 해` 지시 뒤에도 같은 실행과 순차 coordinator를 유지한다. [부분 원문 대조 도구](scripts/model-comparison/PARTIAL_TG27_REVIEW.md)로 이미 생성된 LDEV26-001의 의미 관계4개·용어7개를 직접 검토하고 raw/선택 prediction/원문/판정/코드 해시와 함께 새 폴더에 보존했다. 원문·이전 출력을 본 root의 비맹검 도우미 판단이며 한국어 전용 답변이나 전체18 수용이 아니다. 이 문단의 지급 의무·당사자·금액·세관 수취 전면 부정은 유지했다. `$`→`달러`에 따른 `currencySymbolsPreserved:false`는 원래 값으로 보존하고, 통화 의미 보존 판단을 별도로 남겼다. 부분 보존을 전체 v5 종료·자원 검증으로 대신하지 않는다.

00:45:42 KST에 둘째 문단도1344.906초·46토큰으로 완료했다. LDEV26-002의 의미 관계4개·용어7출현을 추가 보존했고 현재 부분 대조는2문단·8관계·14용어다. 첫 문단과 바뀐 지급자/수취인 관계를 유지했으며, 둘째의 '지불'은 다음 문장의 '이 환불금'을 함께 읽어 비용 반환 뜻이 유지된 것으로 판단했다. 세 번째 문단 생성이 시작됐고 이후 실제 상태는 현재 run/telemetry를 확인한다. 부분 도구 합성9개는 전체 모델·앱 검사 결과가 아니다.

[첫2문단 자원 분석](content/model-comparison/TG27_RESOURCE_RETRY_20260912.md)에서 모델 파일15.41GiB 대 budget8GiB, 출력 생성0.03597/0.03712토큰/초, native CPU 증가 중 kernel84.51/83.61%와 큰 총 page fault를 연결했다. Hy30의 MoE expert128개 중8개 선택 구조와 TG27의 Gemma3 구조 차이도 실제 GGUF 메타데이터·공식 자료로 확인했다. 반복 페이지 교체의 지연 기여는 가설이며 hard fault 귀속·disk bytes·RAM만 바꾼 대조 실험은 아니다. 실행 중인 프로필이나 기준을 바꾸지 않았다.

**독립 답변 검수의 현재 제약:** 기존 세 검토 도우미는 모두 서비스 사용량 한도 오류 상태다. source-only 원문24·질문48·핵심24와 정책/출처 인용·해시는 [입력 검증 기록](.training/verifications/tg27-pending-answerability-inputs-check-20260912.json)에서 다시 확인했지만, 새 후보 전체 완료 전이므로 Korean-only packet·답변·채점은 생성하지 않았다. 원문을 본 root가 fresh 답변자를 대신하지 않는다. 별도 로컬 모델 답변으로 검수 방법을 바꾸거나 Qwen의 기존 실패를 재시도로 바꾸지도 않았다. 실제 전체 결과가 완료되면 기존 `prepare_finance_answerability.py`에 각 run·`--cohort dev18`/`reading6`·`--producer-version translategemma-large-screen-v5`와 새 output 경로를 명시해 준비한다. 질문지는 `source-only-v1/assistant-questionnaire-v2.json`을 유지하고 답변을 동결한 뒤 별도의 원문 근거 채점을 한다.

**01:17 KST 이후 최신 부분 결과:** LDEV26-003을1871.937초·63토큰으로 생성했고 네 번째 요청으로 넘어갔다. `.training/quality-evaluation/tg27-user-retry-root-review-20260912/partial/LDEV26-003/`에 원문·원시 응답·판정·코드를 보존했다. 시간 조건과 미서명 초안 부정은 유지됐지만 `lender`→'대출자'의 당사자 명확성 및 `waiver`→'면책 동의서'의 범위 한정2출현을 보류했다. 확정적인 차입자 반전으로 단정하지 않는다. '대출자'의 서로 다른 용례에 대한 외부 보조 검토는 공식기관 검색 발췌만 확보했고 전체 페이지/PDF 가져오기는 실패했다는 한계도 판정 파일에 남겼다. 현재 부분 대조3문단·용어24출현 중21보존/3보류이며, 새 부분 결과는 실행이 끝난 전체 검수처럼 기존610사건 원장에 편입하지 않았다. 완료 뒤 전체 v5 검증과 함께 명시적으로 연결해야 한다. 모델·coordinator는 계속 진행 중이며 전체18+6·독립 질문·최종 수용은 미완료다.

**08:41 KST 관측 이후 첫15문단 대조 완료:** 위 01:17 상태는 과거 관측이며 새 실행은15/18을 완료하고 LDEV26-016을 처리 중이다. 원문과 실제 출력의 004~015를 root가 직접 읽어 판단했고,12개 판단의 실제 인용·해시·용어 출현·raw/EOS/settings를 모두 확인한 뒤 새 파일로 보존했다. `.training/verifications/write-tg27-root-reviews-004-015-20260912.py`는 명시적으로 작성한 판단을 기록하는 일회성 보조 파일이며 문자열로 의미를 판정하거나 모델 답변을 만드는 도구가 아니다. 기존001~003 및 실행/평가 코드35개를 바꾸지 않았다.

- [첫15문단 관측 보고서](content/model-comparison/TG27_PARTIAL_REVIEW_20260912.md): 중요5문단·경미2·관측 오류 없음4·보류4. 005의 증가분/결과값,006의 상대2%/2%p,008의 자기자본/형평성,011/012의 통합 이후/도중,012의 간접비/불필요 비용을 구분했다. 현재 중요 의미 오류0 기준을 만족한 것으로 처리할 수 없다.
- 용어는 해당15문단76출현 중66보존·4오역·6보류. 전체 선정149의73출현은 이 후보에서 아직 채점하지 않았다. 015는 분모0이며100%가 아니다. 003/004의 당사자·waiver 범위 및009/010의 exercise period 한정은 별도 보류로 남긴다.
- 013의 숫자/통화 자동 검사 false를 보존하면서 March/April→3월/4월, $540→540달러의 실제 의미 대응을 대조했다. 006/008/011/012의 자동 검사 true도 그대로 보존하며 의미 통과로 바꾸지 않는다.
- `.training/verifications/tg27-first15-root-review-check-20260912.json`에 snapshot90파일·receipt15개 해시/정체성 확인을 기록했다. 부분 판단의 관계70개는 수동 분할한 근거 단위이며 사전 고정된 명제 정확도나 독립 오류 수가 아니다. 한국어 전용 답변·새 모델 호출·학습·등록·운영 DB 변경·앱 전체 테스트·원장 추가는0이다.
- 다음은 현재016~018 완료분의 같은 부분 대조 → coordinator의 종료/v5/읽기6 → 전체 의미·용어 기록과 원장 연결이다. 독립 답변 도우미 사용량 제한은 여전히 남아 있다. 생성이 느리다는 이유로 계약을 변경하거나 검토 결과를 전체 수용으로 올리지 않는다.

**전체 대조 연결 준비:** `scripts/model-comparison/complete_tg27_root_review.py`를 새로 추가했다. 기존 `prepare_finance_answerability.load_verified`의 v5 전체 종료·입력/출력·정체성 증거 읽기와 고정 부분 snapshot을 대조한다. 질문 답변은 생성하지 않는다. `--cohort dev18|reading6 --output <새 폴더> --append-to-ledger` 계약은 [부분 대조 안내의 완료 연결 절](scripts/model-comparison/PARTIAL_TG27_REVIEW.md#전체-실행-종료-후-연결)을 따른다. 모든18/6 원문 판단·용어92/57출현이 있어야 새 보고서/사건을 만들며 원래 부분 receipt, `unresolved`, 자동 검사, 비맹검 이력을 보존한다. 연결 검사6개와 기존 부분 계약9개, 총15개 통과. 실제 진행 중인 run을 전달했을 때 `completed_run_summary_missing`으로 거부돼 전체 output·원장 사건·native 호출은0이었다. 준비 기록은 `.training/verifications/tg27-completed-review-link-preparation-20260912.json`이며 실제 완료 결과 연결은 아직 수행하지 않았다. 실행기·coordinator·기존 ledger.py·부분 검수 코드는 수정하지 않았다.

**사용자 `ㄱㄱ` 후 계속 진행:** LDEV26-016을 새 부분 snapshot으로 보존했다. 수식·변수·금액·운송비 중복 차감 금지·번역 과제를 유지했으나 sale proceeds→판매 수익은 수입/수익·이익 범위를 보류한다. 현재16문단은 중요5·경미2·관측 오류 없음4·보류5, 용어81출현은70보존·4오역·7보류다. `.training/verifications/tg27-first16-root-review-check-20260912.json`에서 snapshot96파일 해시를 확인했다. 첫 보조 기록 시 PowerShell의 기본 파이프 인코딩이 한글 인용을 손상했으나 실제 인용 검증이 쓰기 전에 거부했다. UTF-8을 명시해 다시 검증·보존했고 원시 모델 출력은 변경하지 않았다.

기존 실패한 금융위원회 자료는 이번 재연결에서 전체 HTML158,611바이트를 읽고 과거의 대출자 용례를 확인했다. 한국은행 PDF는 여전히 열리지 않아 검색 발췌 한계를 유지한다. `.training/verifications/tg27-lender-source-retry-20260912/followup.json`에 새 근거를 연결했고003/004의 원래 보류 판정은 덮어쓰지 않았다.

**017 유휴 절전과 임시 전원 요청:** 09:13 KST 시작한017에서 유휴 Modern Standby와 겹친 큰 기록 공백을 관측했다. 15분·18분·27분 공백에서 native CPU 증가가 각각1.671875·8.15625·1.328125초였다. 추가 절전 이벤트도 `.training/verifications/tg27-retry-17-power-events-20260912.json`에 저장했다. 11:02 KST ACLineStatus=1을 실제 확인한 뒤 [Windows 공식 계약](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate)에 따른 일시적 display+system 요청을 추가했다. 유휴 화면 꺼짐을 막는 요청이며 덮개·전원 버튼 등 사용자 수동 절전을 우회하지 않는다. 전역 설정이나 이미 경과한 모델 요청 시간은 변경하지 않았다.

- 새 helper `.training/verifications/hold-display-for-tg27-20260912.py`, 코드 SHA `14ba409f7ca32c62ee15858af032933048fb11329bdc463b2878c5952e19c942`.
- 2초 실측 `.training/verifications/tg27-temporary-display-request-probe-20260912.json`: 설정 반환 성공·해제 true·감시 handle 닫기 true.
- 실제 일시 요청 `.training/verifications/tg27-temporary-display-request-20260912.json`, shell session91890, 최초 helper PID624. coordinator PID46376/creationTicks134336139514933701와 정확한 Python 실행 경로를 대조해 열린 handle을 유지한다. 역사적 PID만으로 종료하지 않는다.
- coordinator 종료·기존 cancel 파일·AC 전원 분리·24시간 상한에서 요청 해제 후 종료한다. native 시작/종료나 재시도를 수행하는 helper가 아니다. 요청이 실행 도중 추가된 사실을 기록했으며 처음부터 같은 전원 조건으로 실행했다고 표시하지 않는다. 기존 coordinator/실행기와 그 고정 hash는 그대로다.

## 25. TG27 완료16 보존과 미완료2 별도 재실행

사용자의 `ㄱㄱ` 지시에 따라 계속 진행했다. 24절의 개발18 재시도는2026-09-12 11:13:57 KST에 **완료16·실패1·미실행1**로 종료됐다. 요청17의 긴 관측 공백 합5415.046초 동안 native CPU는14.28125초 증가했다. 외부 네트워크 장애나90분의 연속 모델 연산으로 해석하지 않는다. 원래7200초 요청 제한은 변경하지 않았다. 실패 증거 `.training/verifications/tg27-retry-17-standby-failure-20260912.json`은58파일·기존16개 snapshot과 raw 일치·소유 종료·wrapper 해제·기존 coordinator 종료·임시 display helper 해제를 확인한다. SHA는 `c9b5e2e7ccabc3dd8e4a676464c24591e52f3c8bb9cd27a65e14e212ed0c9c5e`다.

기존 coordinator 상태는 `stopped_without_retry`, 이유는 `development_not_complete_or_not_closed`다. session37066·88696·91890은 끝났으며24절의 예정 읽기 실행은 시작되지 않았다. 완료16은 중요5·경미2·관측 오류 없음4·보류5, 용어81출현은70보존·4오역·7보류다. snapshot96파일 확인과 개별 판단을 보존하며 전체149출현 점수나 독립 질문 통과로 표시하지 않는다.

### 현재 실행과 다음 순서

[별도 재실행 상세](content/model-comparison/TG27_TAIL_RECOVERY_20260912.md)에 따라 기존 producer의 `--ids LDEV26-017 LDEV26-018`로 미완료2개만 새 폴더에서 실행한다. 11:19 KST 두 관측의 가용 physical 최솟값12,401,569,792바이트와 native0을 확인했다. CPU4·BelowNormal·8GiB·기존 입력/출력/시간 제한과 코드 snapshot35개를 유지한다.

| 항목 | 현재 경로·관측 |
|---|---|
| 새 순차 실행 | `.training/verifications/continue-tg27-tail-recovery-20260912.py`, session89362 |
| coordinator 코드 SHA | `5620b7d9b1227475432b74424d0651ad0cca51426dfb3747f38578f3e76803f8` |
| 상태·이력 | `.training/verifications/tg27-tail-recovery-20260912.json` 및 같은 이름의 JSONL |
| 미완료2 실행 | `.training/comparisons/linguistic-dev-20260910/translategemma-27b-v5-tail-retry-20260912-b8` |
| wrapper 기록 | `.training/verifications/tg27-tail2-recovery-20260912-awake.json` |
| 최초 native | PID75880, creationTicks134336531944836318. 현재 handle/실행 경로와 재대조 없이 종료 금지 |
| 다음 읽기6 | `.training/comparisons/real-reading-check-20260910/translategemma-27b-v5-tail-recovery-20260912-b8` |
| 읽기 검수 준비 | `.training/comparisons/real-reading-check-20260910/prepared-tg27-tail-recovery-v5-20260912` |

시작부터 AC 전원에서 임시 display+system 요청을 유지한다. 전원 분리/취소를 관측하면 요청을 해제하고 다음 모델 기동을 막으며 종료 때도 해제한다. 이미 활성화한 native의 소유권·종료는 기존 wrapper가 유지한다. 전역 전원·덮개 설정이나 사용자 입력을 변경하지 않는다. 중단할 때는 먼저 `.training/verifications/tg27-user-retry-continuation-20260911.cancel`을 만들어 이후 기동을 막고 현재 활성 native의 소유 정체성을 확인한다. flag 자체는 활성 native 종료 명령이 아니다. **진행 중 coordinator와 고정 의존 파일은 수정하지 말고 읽기 모델을 별도 중복 기동하지 않는다.**

순서는2개 종료/소유 해제 → `verify_tg27_tail2.py` → 새8GiB 사전검사 → 읽기6 → 기존 v5 검증/검수 준비다. 자동 재시도·가중치 학습·유료 호출·앱 등록은 없다.2개 전용 증거 consumer는 정확한017/018·원래 v5 기술 검사만 허용하고 기존 전체6/18 consumer는 그대로다. 새2개 증거 검사6개와 새 부분 보존11개, 총17개 합성검사를 통과했다. 실제 완료2개/읽기6의 기술 통과는 그때 별도 확인해야 한다.

### 검토·원장·남은 제약

새017/018·REAL001~006의 수동 판단은 `scripts/model-comparison/partial_tg27_recovery_review.py`, version `tg27-recovery-root-review-v1`로 `.training/quality-evaluation/tg27-recovery-root-review-20260912/partial/`에 보존한다. 원래16개 partial 도구/경로/판정을 고치지 않는다. 인용·raw·원문 전체 용어 출현·비맹검 provenance를 검증한다. 원문을 본 root는 새 한국어 전용 답변자를 대신할 수 없으며 기존 세 도우미의 서비스 사용량 제한은 아직 남아 있다.

기존 `complete_tg27_root_review.py`와 전체 개발18 질문 준비기는 실패한 원래18 실행과 별도2 실행을 성공18개로 합치는 도구가 아니다. 거부 기준을 유지한다. 향후 진단 자료를 함께 집계하려면 원래 실패·각 실행 정체성·부분 검증 범위를 그대로 보존하는 명시 연결이 필요하다. 중요 의미 오류5문단과 보류가 이미 있으므로 생성 완료만으로 수용 판정을 바꾸지 않는다.

실제17번 실행 실패1건만 오류 원장에 추가해 **611사건: 번역128·질문76·QE104·실행5·용어298**이다. 재입력 추가0·재사용1을 확인했다. 최신 원장 보고서는 `.training/quality-evaluation/error-ledger/v1/reports/20260912T022029584075Z.json`이다. 새16개의 의미/용어 판단은 아직 원장에 편입하지 않았다. 기존 Qwen 의미 미탐은 연결 실패로 바꾸지 않으며 재실행하지 않는다. 운영 앱·DB·등록·기존 번역/검수와 모델 가중치는 변경하지 않았다. 이번 CLI 검사와 과거 앱 기능 검증을 구분한다.

**11:37 KST 후속 — 완료16 부분 진단 원장 편입:** 새 `record_tg27_failed_run_reviews.py`가 고정 실패/소유 종료·58파일과 원래16개 snapshot·raw·인용·용어81출현을 다시 검증한다. 실제 문단16·용어81,97사건을 추가했고 반복은 추가0·재사용97이었다. 원장 **708사건: 번역144·질문76·QE104·실행5·용어379**, 최신 보고서 `.training/quality-evaluation/error-ledger/v1/reports/20260912T023713899626Z.json`이다. 모든 새 사건은 실패 실행의 부분 진단임을 명시하며 원래 의미/자동 검사/보류/비맹검 판단을 유지한다. 전체 개발18 검증이나 한국어 전용 질문을 통과시킨 것이 아니다. 합성6개를 통과했고 실제 검증·편입·재입력 기록은 `tg27-user-retry-root-review-20260912/failed-run-diagnostic16*` 세 새 폴더에 남겼다. [명령과 계약](scripts/model-comparison/PARTIAL_TG27_REVIEW.md#실패-실행의-완료16개를-진단-원장에-연결)을 따르며 사용한 코드/기존 의존 파일은 이제 해시에 묶였으므로 수정하지 않는다. 이전611사건·미편입 문구는 역사적 관측이다.

같은 원문의 중요 오류5문단을 Hy7/Hy30의 저장된10개 출력과도 연결해15개 결과·기존 판정·근거47파일을 확인했다. 비교 기록 `.training/verifications/tg27-five-observed-errors-comparison-20260912.json`, 해설은 [오류 분석의 같은 원문 비교](content/model-comparison/ERROR_LEDGER_REPORT_20260911.md#tg27-중요-오류5문단의-같은-원문-비교)다. 005/006 수량 역할과011/012 시점의 TG27 손상은 기존 두 후보와 다르고,008 금융 equity 오역은 세 후보에 공통이다. 자동 검사는15개 모두 통과했다. 오류를 본 뒤 고른5개이므로 모델 순위·전체 정확도·원인 입증으로 확대하지 않는다. 원래 검토를 다시 세거나 수정하지 않았으며 추가 native/원장 사건은0이다.

**새 실행 검토 연결 준비:** `record_tg27_recovery_reviews.py --cohort tail2|reading6 --output <새 폴더> --append-to-ledger`는 각 실행의 종료·기술 검증과 새 수동 판정을 연결한다. [명령](scripts/model-comparison/PARTIAL_TG27_REVIEW.md#새2개와-읽기6의-완료-검토-연결)과 별도2/6 범위를 따른다. 합성6개 통과,11:47 KST 실제 두 미완료 실행은 `completed_recovery_summary_missing`으로 거부돼 output·원장 추가·native 호출0을 확인했다. 준비 증거 `.training/verifications/tg27-recovery-review-link-preparation-20260912.json`, 코드 SHA `cbd5cea23f3a283ea08603a2efaa5ae458989e3455a7badeb101c920c60f6f7c`다. 현재17번 재실행은 진행 중이며 전체24 진단 자료·독립 질문 평가는 아직 완성되지 않았다. 기존 coordinator 고정 파일12개는 모두 일치했다.

**11:54 KST 후속 — 기존 검사 범위 실측:** 숫자 검사 함수의 원래 구현을005/006 원문·번역에 재적용하면 모두 숫자 토큰 `4%:1,2%:1`이다. `%p`의p와`percentage points`의단위 정보를 포함하지 않아 자동 숫자 검사 통과가 재현됐다. 별도 기존 `lib/quality/rules.ts`도16문단에 적용했다. 중요5 중006 한 문단만 경고: TP1·FP0·FN4·TN6·보류5다. 보류를 정답으로 넣지 않으며 1/5 재현은 전체 탐지95% 통과가 아니다. 원래 코드 SHA `75395f6e05b102ac129ba94837c83380dcb04ab980efbfc3d94acef51440f578`를 유지했다. 모델 오역의 내부 원인과 검사 구현의 포착 범위를 구분한다.

근거는 `.training/verifications/tg27-numeric-check-reproduction-20260912.json`, `tg2716-existing-meaning-rules-20260912.json`이며 후자는 실제 원문 문장·한국어2%p의 유효 구간을 보존한다. 검사 관측16개를 원장에 별도 추가하고 반복 추가0·재사용16을 확인했다. **현재724사건: 번역144·질문76·QE120·실행5·용어379**. 최신 보고서는 `.training/quality-evaluation/error-ledger/v1/reports/20260912T025458365421Z.json`이다. 새 importer `.training/verifications/import-tg2716-existing-rules-20260912.py`는 원래 code·관측·번역 사건 해시를 확인하며 보류를 `observed_unadjudicated`/null로 보존한다. 운영 앱 코드·DB·판정·번역·모델 가중치 변경과 추가 native 호출은0이다.

**12:21 KST 후속 — 새017 생성·원문 대조:** 새 실행 첫 문단017은112토큰·정상 EOS106·원래 자동 검사7개true로 완료됐고018 요청으로 넘어갔다. prompt181.711초·출력 생성3450.338초이며 생성 속도는 약0.03217토큰/초다. 실행 조건을 변경하지 않았다. `.training/verifications/tg27-recovery-root-review-LDEV26-017-20260912.json`의 직접 판단을 새 partial 도구로 검증해 `tg27-recovery-root-review-20260912/partial/LDEV26-017/`에 보존했다.

017의 매장 규모/제품 품질 통제 구별·추정 상관·특정 모형·유의성 한계는 유지한다. 다만 인과 효과 입증의 부정이 ‘길어질수록 매출이 증가한다는 입증의 부정’으로 읽힐 수 있어 경계 해석을 보류했다. 앞의 ‘상관관계를 나타낼 뿐’과 입증 부정으로 원래 대비가 회복되는 읽기도 있어 중요 오역으로 단정하지 않는다. 기존 Hy7/Hy30의 상이한 판정과 표현을 함께 보고도 한쪽 판정을 자동 복사하지 않았다. 새 한국어 전용 답변자는 없다. revenue2출현은 매출로 보존한다.

현재 서로 다른2개 실행에서 원문17개를 확보했다. 의미는 중요5·경미2·관측 오류 없음4·보류6, 용어83출현은72보존·4오역·7보류다. 전체149 용어 정확도나 단일 개발18 성공은 아니다. `.training/verifications/tg27-first17-cross-run-root-review-check-20260912.json`에서17개 snapshot의102파일과 기존017 판정 근거를 확인했다. 새017은 부분 보존 단계여서 원장724개에는 아직 추가하지 않았다. 현재018 진행 → 두 개 소유 종료/검증 → 새 자원 검사 → 읽기6 순서와 coordinator를 유지한다.


## 26. TG27 개발18 대조 완료와 읽기6 실행

2026-09-12 13:35 KST 기준이다. 기존 실패 실행의 완료16개는 보존했고, 새017·018 실행은13:22:55 KST에 정상 종료했다. 고정2개 기술 검증은 통과했으며 `tail2RunValidated=true`, `fullDev18RunValidated=false`다. 새 실행의 최대 heartbeat 간격은1.313초로, 이전 실패의 긴 절전 관측 공백과 구분된다. 이번2개의 관측 결과로 전원 설정의 인과 효과나 앞으로의 무중단 실행을 보장하지 않는다.

- [새2개 기술 증거](.training/verifications/tg27-tail2-v5-verification-20260912.json), [소유 종료·해제 기록](.training/verifications/tg27-tail2-recovery-20260912-awake.json).
- 새017은 인과 입증 부정의 명확성을 보류했다. 새018은 감사인의 재고 수량 집계/평가인의 판매가 추정, 미래 수요 인증 제외, 잠정 추정의 지시 대상을 보존했다. 도우미 원문 대조이며 사람 검수나 독립 맹검이 아니다.
- [완료2개 검토 연결](.training/quality-evaluation/tg27-recovery-root-review-20260912/completed-tail2-v1/report.json): 번역2·용어11 사건 추가. 반복 실행은 추가0·재사용13. 현재 원장737개는 번역146·질문76·QE120·실행5·용어390으로 나뉜다.
- [개발18 교차 실행 진단](.training/quality-evaluation/tg27-recovery-root-review-20260912/dev18-cross-run-diagnostic-v1.json): 원문18개 중복/누락 없음, 용어92출현, 증거342파일 확인. 중요5·경미2·관측 오류 없음5·보류6, 용어81보존·4오역·7보류. 전체149 정확도/독립 질문/수용 판정은 산출하지 않는다. 실패16과 성공2를 단일 전체18 기술 성공으로 바꾸지 않는다.

읽기6은 새 자원 검사 두 번을 통과한 뒤 별도 run `real-reading-check-20260910/translategemma-27b-v5-tail-recovery-20260912-b8`에서 실행 중이다. 사전검사의 최소 가용 물리 메모리12,524,666,880바이트와 commit14,886,109,184바이트를 확인했고,13:24:12 KST 첫 요청 `REAL26-001`을 시작했다. CPU4·BelowNormal·8GiB·기존 시간 제한·원문 전용 입력·같은 모델/런타임을 유지한다. 새 native PID73952/creationTicks134336606202373659는 이번 실행의 관측 식별자이며 미래 종료 명령의 근거로 재사용하지 않는다.

다음 작업은 완료되는 읽기 문단의 원문/용어 대조 → 실제6개 종료와 v5 증거 확인 → 새6개 검토 원장 연결이다. 준비 도구는 [새2개와 읽기6 연결 안내](scripts/model-comparison/PARTIAL_TG27_REVIEW.md#새2개와-읽기6의-완료-검토-연결)를 따른다. 독립 한국어 전용 답변자는 서비스 사용량 제한으로 확보되지 않았으며 이미 원문을 본 도우미가 대신 맹검 답변을 만들지 않는다. 새 학습·유료 호출·앱 등록·운영 DB 변경은 없다. 구현 도구 검사, 실제 생성/종료, 원문 의미 판단, 독립 질문 수용을 각각 구분한다.

14:10:58 KST 전원 전환 후 coordinator 상태는 `reading6_running_without_display_request`다. AC 분리·취소 flag 없음과 일시적 display 요청 해제를 [전환 관측](.training/verifications/tg27-reading6-ac-transition-20260912.json)에 보존했다. 이미 시작한 읽기6 producer는 기존 제한 안에서 계속되며 다음 coordinator 실행을 차단한다. 첫 문단만 남기고 나머지5개를 취소하는 기능이 아니다. 전역 전원 설정이나 모델 설정은 변경하지 않았다.


## 27. 읽기6 중단 증거와 재시도 사전검사 보류

2026-09-12 17:09 KST 기준이다.14:16:33 KST 이후 읽기6의 관측 파일이 멈췄고17시 재연결 뒤 native/소유 wrapper/기존 coordinator 프로세스가 없음을 확인했다. `predictions.jsonl`은0바이트이며 summary·최종 종료 코드가 없다. 기존 JSON의 `reading6_running_without_display_request`는 마지막으로 저장된 상태로, 현재 살아 있는 실행을 뜻하지 않는다.

[중단 관측](.training/verifications/tg27-reading6-interruption-20260912.json)은49근거 파일과 native0 사전 관측2개를 보존한다. 전원 전환·Modern Standby·연결 단절과 이후 프로세스 부재가 관측됐으나 최종 종료 주체/코드는 미확정이다. 정상 소유 종료를 새로 써 넣거나 시간 초과로 단정하지 않았다. 원시 run/이전 state/완료 개발18문단은 수정하지 않았다. 이 중단을 실행 사건1개로 추가해 원장은738개(번역146·질문76·QE120·실행6·용어390)이며 반복 추가0·재사용1이다. [새 원장 보고서](.training/quality-evaluation/error-ledger/v1/reports/20260912T080807222617Z.json)를 따른다.

전원 재연결과 프로세스 부재 확인 뒤, 이전 코드와 같은 CPU4·BelowNormal·8GiB·v5 producer·요청7200초 제한으로 읽기6만 재시도하도록 [새 coordinator](.training/verifications/retry-tg27-reading6-after-disconnect-20260912.py)를 준비했다. 새 output은 `real-reading-check-20260910/translategemma-27b-v5-reading-retry-20260912-b8`다.17:05 KST 숨김 launcher가 시작했지만 [실제 사전검사](.training/verifications/tg27-reading6-slot-reading-retry-20260912.json)는 최소 가용 물리11,421,274,112바이트(약10.64GiB)로11GiB 시작 기준을 충족하지 못했다. 첫 native 조회도10초 시간 초과였다. `DEFERRED`, 모델 로딩0회이며 해당 새 run 폴더는 아직 생성하지 않았다. [coordinator 상태](.training/verifications/tg27-reading-retry-20260912.json)는 `stopped_without_retry`, `requestReleased=true`, `ownedWrapperMayStillRun=false`다.

기존 13시 사전검사 PASS를 현재 실행 허가로 재사용하거나 기준을 낮추지 않는다. 재시작에는 현재 AC 전원·조회 성공·native0·가용 물리11GiB/commit기준의 두 관측이 다시 필요하다. 이미 생성된 state/log를 덮어써 재시도하지 않는다. 이번 새 사전검사 보류 뒤 추가 모델을 실행하지 않았다. 메모리를 많이 쓰는 항목은 현재 사용자 앱/시스템/도구 호스트여서 임의로 종료하지 않았다.

완료 범위는 캐시 약189MiB 및 고아 telemetry 정리, 개발18 원문 대조·92용어·오류 원장 연결, 읽기 실행 중단의 증거 보존이다. 미완료는 읽기6 실제 생성·대조·57용어·독립 한국어 질문 평가다. 독립 답변 도우미의 서비스 사용량 제한도 유지된다. 중요 의미 오류5문단 때문에 이 후보의 학습 수용/앱 적용은 통과하지 못했다. 앱·운영 DB·기존 모델 등록·가중치는 변경하지 않았다.

17:11 KST 재확인에서도 [사전검사](.training/verifications/tg27-reading6-post-defer-check-20260912.json)는 DEFERRED였다. 이번 두 조회는 성공했고 native0·오류0이었으나 가용 RAM은7.766/7.512GiB로11GiB 기준보다 부족했다. 앞의10.64GiB는17:05 당시 값이다. 추가 모델은 실행하지 않았으며 전원 요청 해제를 유지한다.8문서의 기존 로컬 링크224개와 diff 공백 검사를 확인했다. 이 메모의 새 링크 대상도 실제 존재한다.

## 28. 공통 입력 처리와 의미 관계 검사 개선 실행 계획

2026-09-13 KST 문서 갱신이다. 사용자는 사전 전처리와 현재 모델 분석을 통해 여러 오류를 함께 줄이는 방안을 요청했고, 그 분석을 바탕으로 이 인수인계 문서를 고쳐 이후 개선의 작업 기준으로 삼도록 지시했다. **앞으로의 우선순위는 입력 준비·검사 개선 → Hy7의 통제된 비교 → 독립 평가 → 통과한 구성의 앱 검증이다.** 9절·22~27절의 TG27 재개 순서를 자동으로 이어 실행하지 않는다. 통상적인 후속 구현 선택은 기존 승인과 이 계획 안에서 진행하며, 이 문서 갱신 자체를 모델 실행 명령으로 해석하지 않는다.

### 28.1 출발점과 이미 확인한 근거

| 구분 | 확인한 사실 | 계획에 반영할 점 |
|---|---|---|
| 기존 품질 상태 | TG27 개발18은 중요5·경미2·관측 오류 없음5·보류6, 용어92출현은81보존·4오역·7보류. 원장738사건은 번역146·질문76·QE120·실행6·용어390 | 완료 자료를 재생성하지 않는다. 읽기6·57용어·독립 질문은 미완료이며 좋은 후속 점수로 중요 오류를 상쇄하지 않는다. |
| 문맥 구성 | [앱 스냅샷](lib/translation/index.ts)은 자료 제목과 앞뒤 한 블록, 대상 블록 자체를 붙여10,000문자로 자른다. 절·부모 목록·표 머리글에 따른 선별과 대상 중복 제거가 없다. | 같은 원문을 유지하고 문맥 선택부터 개선한다. 앱 문맥 문제가 `context=""`인 개발 문장의 실패 원인이라고 단정하지 않는다. |
| 용어 연결 | [Hy 프롬프트](scripts/model-comparison/run_hymt.py)는 긴 구문 우선으로 사전 항목을 찾고 뜻 선택은 모델에 맡긴다. [앱 bridge](scripts/local-hymt/bridge.py)는 요청의 일반 glossary 대신 등록한 고정54개 사전을 사용한다. 이 사전에는 단독 `equity`가 없다. | 앱 사전에 단어만 추가해도 Hy가 사용한다는 전제를 버린다. 새 의미 사전·선택 규칙·실제 입력을 별도 프로필 정체성으로 연결한다. |
| 실제 전처리 선례 | [PDF v2 대조](content/model-comparison/QUALITY_LOCAL_REPORT.md)는 같은 가중치·프롬프트·사전에서 도입문과 종속 목록을 묶은 뒤 해당 내용 추가 오류의 해소를 기록했다. | 이미 있는 보수적 PDF 병합을 재구현하지 않는다. 추가 추출 변경은 필요성이 확인된 경우에만 별도 원문 버전으로 다룬다. |
| 검사 누락 | [오류 분석](content/model-comparison/ERROR_LEDGER_REPORT_20260911.md)에서 기존 비교 숫자 검사가 상대%와%p를 구별하지 못함을 재현했다. 숫자가 같아도 증가분/결과·연산 방향·시점이 바뀔 수 있다. | 번역 생성 개선과 검사 검출력 개선을 별도로 측정한다. 원래 자동 통과·의미 판정을 덮어쓰지 않는다. |
| 문맥 효과의 한계 | [일반16 비교](content/model-comparison/GENERAL_CONTEXT_REPORT_20260911.md)에서 Hy7 원문/문맥 구성의 핵심 명제 보존은 모두52/61이었다. | 문맥을 늘리면 무조건 좋아진다고 가정하지 않는다. 필요한 정보의 선택과 일반 문장 회귀를 함께 검증한다. |

이는 코드와 저장된 결과의 읽기 감사다. 사전 누락·문맥 구성·양자화·메모리가 모델 내부 오역을 일으켰다는 인과 결론은 아니다. 새 구성의 개선 폭은 아직 측정하지 않았다. 마지막 모델 실행 상태·전원·RAM·독립 도우미 사용량 제한은27절의 당시 관측으로만 취급한다.

### 28.2 모델별 역할

| 모델/구성 | 이번 개선에서의 역할 |
|---|---|
| Hy-MT2 7B Q8 | 현재 등록된 contextual 구성을 기준 C0로 삼는 첫 비교 대상. 같은 가중치·런타임·번역 템플릿·생성 설정에서 문맥과 사전 효과를 분리한다. |
| Hy-MT2 30B-A3B Q4 | Hy7에서 유효한 개선이 확인된 뒤의 후속 비교 후보. 모델 크기나 활성 파라미터 수로 정확도·실제 RAM 사용량을 보장하지 않는다. |
| TranslateGemma 12B/27B Q4 | 기존 원문 전용 결과를 보존하는 비교 후보. 현재의 새 입력 실험에 우선 투입하지 않는다. 공식 원문 템플릿에 임의 용어·검토 지시를 끼워 넣지 않는다. |
| Qwen 의미 검사 후보 | 기존 실제 미탐·미등록을 유지한다. 자동 최종 판정자나 검증된 원문 의미 선택기로 사용하지 않는다. 다른 역할을 시험하려면 그 과제의 별도 개발 검증이 먼저다. |
| Argos·학습 Marian v5 | 기존 실행·학습·실패 근거를 보존한다. 이번 입력 개선을 이유로 재학습하거나 앱 제공자를 전환하지 않는다. |

[Tencent 공식 모델 카드](https://huggingface.co/tencent/Hy-MT2-7B#hy-mt2-translation-task-instruction-examples-chinese-english-comparison)는 용어 참조·배경 문맥·문체 지시 예시를 제공한다. [Google 공식 사용 계약](https://huggingface.co/google/translategemma-27b-it#usage)은 번역할 원문을 전용 템플릿에 넣도록 하며, 후편집 등 대체 프롬프트는 공식 지원 밖으로 구분한다. 이 링크는 입력 계약의 근거이지 로컬 금융 품질의 인증이 아니다. 실제 설치 revision·템플릿·생성 설정은 로컬 등록 manifest와 실행 증거로 확인한다. 모델들이 같은 `equity`를 함께 틀린 관측이 있으므로 다수결·자기검수·역번역만으로 수용하지 않는다.

### 28.3 먼저 만들 입력과 검사 계약

**문맥 선택:** 번역 대상의 바이트·원문 버전·블록 소속은 유지한다. 같은 버전의 실제 절 제목, 부모 도입/목록, 필요한 이웃, 표의 행·열 제목과 단위를 구조 근거에 따라 선택한다. 해당 구조가 저장돼 있지 않으면 만들어낸 관계로 채우지 않고 미지원/불확실로 남긴다. 대상 문장을 배경에서 중복하지 않으며, 대상과 참고 문맥을 구분한다. 문맥 조각마다 원문 버전·블록 또는 위치·실제 인용·해시·선택 이유를 보존한다. 문맥 예산은 실제 tokenizer로 계산하고 원문 전체와 출력 여유를 먼저 확보한다. 참고 조각은 문장/구조 경계에서 선택·제외하고 제외 이유를 기록한다. 원문을 몰래 자르거나 수식·부정·한정 표현을 정규화로 바꾸지 않는다.

초기 네 구성 비교에서는 **원문 단위를 동일하게 유지**한다. PDF 재추출·추가 병합까지 동시에 바꾸면 문맥 효과와 섞이므로 별도 실험으로 둔다. 필요 시 새 추출기/설정·새 버전과 이전 평가 단위의 대응을 먼저 정의한다. 표 머리글을 번역 입력에 활용하는 계획은 표 의미 검사 UI가 구현됐다는 뜻이 아니다. 현재 미지원인 표 구조·의미 검사 범위는 실제 구현 전까지 그대로 표시한다.

**문맥별 용어 사전:** 단어별 단일 치환표를 확장하는 방식으로 끝내지 않는다. 출처가 있는 일반적인 금융/비금융 뜻, 허용 표현, 적용 조건, 제외 조건, 긴 복합구 우선순위를 새 사전 버전에 기록한다. 초기 초점은 관측된 금융 다의어와 개념 혼동이며, 사전의 적용 범위를 문서 전체에 무조건 강제하지 않는다. 원문·허용 문맥의 실제 출현과 선택 근거를 기록하고 확신할 수 없는 뜻은 강제 힌트 없이 보류한다. 평가 ID·참조 번역·정답 용어·질문 정답·오류 판정은 사전 선택 입력으로 사용하지 않는다. 일반 사전과 평가 주석을 물리적으로 분리하고, 새 등록 사본이 실제 prompt에 반영됐는지를 검사한다. 기존 등록54개 사전·고정 실행 코드·등록 manifest는 수정하지 않는다.

**수량·논리 관계 검사:** 명확히 지원할 수 있는 값+단위, 상대%/%p, 시작값/증가분/결과값, 나눗셈 피제수/제수, 포함/제외, 이후/도중·조건 관계부터 지원 범위를 정한다. 패턴 이름만으로 의미를 확정하지 않고 양쪽 실제 인용과 관계 근거를 기록한다. 판단 불가·지원 밖·원문 모호성을 정상 통과로 처리하지 않는다. 부정 범위·동의 표현·한국어 어순·기호의 자연스러운 표기 변화·반대 의미의 정상 문장을 반례에 포함한다. 일반적인 의미 구조 추출을 이미 해결했다고 가정하지 않으며, 별도 모델이 만든 의미 메모도 검증 없이 정답 힌트로 전달하지 않는다.

검사기는 동일한 저장 출력 전체를 새 검사 버전으로 읽고 경고와 미검사 범위를 별도 기록한다. 번역을 자동 수정하거나 기존 판정을 갱신하지 않는다. **검출력 증가는 생성 품질 향상과 별도 성과**다. 선택 재번역은 네 구성의 초벌 비교 이후에만 별도 실험으로 설계하며, 원문에서 감지한 위험 유형·최초 출력·추가 호출·수정 결과·채택 근거를 보존한다. 정답을 알고 고른 후보만 최종 출력처럼 제출하지 않는다.

### 28.4 실행 순서와 단계별 완료 조건

아래 항목은 최초 계획 작성 때 모두 미착수였다. 2026-09-13 후속 작업에서 S1~S3을 완료했고, 9월14일에는 **S4까지 완료**했다. S5 질문·채점과 조건부 S6은 미완료다. 입력/검사 구현·실제 토큰화·저장 출력 진단과 번역 생성·의미 개선·앱 등록을 구분한다. 실제 산출물·검사·미완료는28.8에 추가한다.

- [x] **S1 — 입력·평가 계약과 자료 고정.** [완료 자료](content/model-comparison/input-preparation-v1/README.md)와 [계약](content/model-comparison/input-preparation-v1/CONTRACT.md)에 현재 Hy7 등록 정체성·실제 입력 경로·저장 구조와 지원 한계, 사전 출처·문맥 정책·평가/중단 규칙을 고정했다. 금융8·일반8의16단위(실제 공개 회귀4·새 합성12), 핵심 명제48·질문32(핵심16)·선정 용어39출현이다. 최소대립6쌍을 문서 그룹으로 묶고 기존 소비 test·개인 기록을 편입하지 않았다. 후속 독립 금융60·일반20의 선정 절차를 먼저 정했으며 그 내용은 후보 동결 후 별도로 준비한다. S1 완료 당시에는 실제 tokenizer·선택기·관계 검사·번역 생성을 실행하지 않았다.
- [x] **S2 — 모델 없이 입력 준비 구현.** [별도 입력 선택기·연결·토크나이저](content/model-comparison/input-preparation-v1-s2s3/INPUT_PREPARATION.md)를 구현했다. 원문/소속/문맥 출처·예산·금융/일반 반례·정답 격리를 검사하고64prompt와 실제 token IDs를 해시로 고정했다. 전체 prompt281~621/4095토큰이며 어휘 전용 native를 사용했고 tensor/context/번역 생성은0이다. 이번 개발16에서 새 금융 힌트0이라는 제한을 기록했다.
- [x] **S3 — 저장 출력으로 관계 검사 검증.** [98출력·122기존 판정 진단](content/model-comparison/input-preparation-v1-s2s3/RELATION_DIAGNOSTIC.md)에서930파일 해시를 유지하고64합성 반례·유형별 경고행렬·보류/미지원을 기록했다. 경고1개는 이미 알려진 TG27 오류이며 새 검출력 성과가 아니다. 기존 원장 관측98개 추가·반복 추가0/재사용98, 기존 번역 검토146개 유지를 확인했다. 독립 관계gold·QE95/95·앱 등록은 미완료다.
- [x] **S4 — Hy7 네 구성의 실제 생성.** 아래 C0~C3를 동일한 새 입력 집합에서 비교한다.16개로 확정하면 기본 생성 상한은64개 출력이다. 기존 동일 출력 재사용은 원문·문맥·프롬프트·모델/런타임·생성 설정과 처리 정체성이 모두 같은 경우만 허용하며 재사용 여부를 기록한다. 기능/프로토콜 실패는 생성 확대 전에 고치고 새 코드·실행 버전을 남긴다. 이미 완료한 출력의 재생성으로 유리한 결과를 고르지 않는다.
- [ ] **S5 — 원문 대조·질문 평가·후보 선택.** 같은 원문별 네 출력의 중요 의미 오류, 핵심 관계, 뜻과 표현을 함께 만족하는 용어, 자연스러움, 일반 문장 회귀와 보류를 대조한다. 한국어 전용 질문은 원문·정답·다른 후보를 보지 않은 새 답변자에게 제공하고 답변 동결 후 원문 근거로 채점한다. 도우미 유형·이전 노출·답변자 배정 차이를 기록한다. 독립 답변자를 확보하지 못하면 해당 평가만 미완료로 남기고 원문을 본 root가 대신 답하지 않는다. 새 검사기는 네 구성 모두에 같은 버전으로 적용해 생성/탐지 지표를 분리한다.
- [ ] **S6 — 유효한 후보만 확대.** 비교에서 의미 개선과 일반 회귀 없음이 확인된 구성만 필요에 따라 Hy30에 적용해 별도 비교한다. 후보를 선택·동결한 뒤 미노출 독립 평가를 수행한다. 독립 결과로 같은 평가를 통과하도록 재조정하지 않는다. 모든 수용 기준을 충족한 후보만 새 배포 사본·등록 정체성·격리 앱 검증으로 진행한다. 통과 후보가 없으면 기존 등록을 유지하고 남은 오류와 다음 가설을 보고한다. 가중치 학습·선택 재번역은 그때 필요한 별도 실험으로 정한다.

### 28.5 네 구성의 차이와 평가 기준

| 구성 | 문맥 정책 | 사전 정책 | 분리해서 확인할 효과 |
|---|---|---|---|
| C0 | 현재 앱의 제목+이웃 블록 정책을 동일 입력에서 재현 | 현재 등록된 고정54개 | 현재 Hy7 contextual 기준 |
| C1 | 구조·출처·토큰 예산을 반영한 새 선택 | C0와 동일 | 문맥 준비의 효과 |
| C2 | C0와 동일 | 출현별 뜻·적용/제외 조건을 반영한 새 사전과 선택 | 사전 준비의 효과 |
| C3 | C1과 동일 | C2와 동일 | 결합 효과와 상호 간섭 |

모델 가중치·양자화·런타임·번역 템플릿·문체/공통 지시·샘플링·seed·최대 출력·문맥 크기는 네 구성에서 고정한다. 사전 힌트 형식처럼 의도적으로 바꾸는 항목은 C2/C3 변화에 포함해 기록한다. 문맥과 사전이 필요로 하는 토큰 수는 결과로 남긴다. 원문 전용(raw)을 C0로 바꾸거나, 문맥이 비어 있는 예전 개발 출력으로 앱 문맥 기준을 대신하지 않는다. 실제 앱 구조를 가진 입력에 C0 정책을 재현할 수 있는지 확인하고, 합성 자료는 문서 구조와 provenance를 명시해 따로 집계한다.

주된 비교 단위는 같은 원문의 **개선/유지/회귀/보류**다. 총점 상승으로 새 중요 오류나 일반 문장 회귀를 가리지 않는다. 짧은 개발 자료의 결과는 후보 선택 근거이며95% 일반 정확도 인증이 아니다. 최종 수용은 기존 [학습 준비 기준](content/model-comparison/LEARNING_READINESS_BASELINE_20260911.md)을 유지한다: 평가 완결성, critical/major 각각0, 핵심 명제100%, 뜻과 표현을 만족하는 용어95% 이상, 전체 질문95% 이상, 핵심 질문100%. 보류·미평가를 통과로 넣지 않는다. 위험 검사 등록도 같은 문서의 baseline별 정밀도/재현율95/95 및 필수 조건을 따르며 작은 패턴 검사 성공으로 전체 의미 검사 통과를 주장하지 않는다.

기술 실패·잘림·소유 종료 실패·자원 조건 미충족이면 해당 실행을 중지하고 증거를 보존한다. 실패한 평가의 기준을 완화하거나 출력이 좋아질 때까지 설정을 반복하지 않는다. 이 문서의 S1~S3은 모델 RAM이 부족해도 먼저 진행할 수 있으므로 메모리 확보 대기만 반복하지 않는다.

### 28.6 산출물·자원·앱 보존

아래 경로에 S1 정책·자료, 공개 `source-bundles/`, S2의 `s2-prepared/attempt-002/`, S3의 `s3-relations-20260913-v2/`를 생성했다. [S2/S3 안내](content/model-comparison/input-preparation-v1-s2s3/README.md)에 실제 구현/명령과 실패 이력을 연결한다. C0~C3 번역 생성/의미 검토 run은 아직 없다. 기존 hash에 묶인 `run_hymt.py`, 등록 코드·사전, TG27/Qwen 검증 도구와 동결 S1·S2/S3 코드를 직접 수정해서 새 실험을 연결하지 않는다. 후속 변경은 별도 코드·버전·새 산출물로 기록한다.

| 계획 위치 | 남길 내용 |
|---|---|
| `content/model-comparison/input-preparation-v1/` | 정책·일반 의미 사전·개발 입력/출처 manifest·평가 분리 계약. 기존 test와 원문/개인 데이터를 무단 복사하지 않는다. |
| `.training/comparisons/input-preparation-v1/<새 실행 ID>/` | C0~C3별 원시 입력/출력·선택 문맥·용어 근거·정체성/코드 해시·토큰/시간·소유 종료 기록 |
| `.training/quality-evaluation/input-preparation-v1/<새 검토 ID>/` | 별도 원문 명제·익명 검토·질문/답변 동결·수동 채점·새 관계 검사 결과·회귀 집계 |
| 기존 오류 원장과 새 보고서 | 원래 번역/판정에 새 관측을 연결. 실제 오류·보류·실행 실패·검사 실패를 구분하고 중복 추가0 확인 |

한 번에 무거운 로컬 모델은 하나만 실행한다. 모델 시작 직전에 소유 프로세스·현재 AC/전원 상태·가용 physical/commit과 **해당 프로필의 고정 자원 조건**을 확인한다. Hy7에 TG27의 수치만 복사하거나 과거 PASS를 재사용하지 않는다. 기존 운영 worker와 평가 모델도 경합하지 않도록 확인한다. CPU4·BelowNormal 등 기존 실행 정책을 따르되 이를 CPU 사용률의 하드 상한으로 표현하지 않는다. 임시 전원 요청과 소유 native의 수명/종료를 함께 관리하고 사용자 앱·전역 전원 설정을 임의 변경하지 않는다. 디스크 캐시 삭제를 RAM 확보 효과로 보고하지 않는다.

초기 작업은 별도 로컬 비교 CLI 범위이며 페이지/API·운영 SQLite·개인 기록·원문 버전·활성 모델 등록을 변경하지 않는다. 입력 정책의 실제 앱 반영 단계에서는 문맥/사전/선택기/처리 버전과 모델·런타임 정체성을 캐시 및 등록에 연결하고 기존 번역·검수 이력·읽기 위치를 보존한다. 검수 메모리의 정확한 문맥 일치 조건도 유지한다. 등록 변경 전 새 사본과 평가 근거를 검증하며 기존 등록 사본을 덮어쓰지 않는다. 실제 엔진의 HTML3문단·PDF1페이지 생성·저장·캐시·재시작/보존과 관련 UI 동선을 격리 DATA_DIR에서 검증한 뒤 해당 범위만 보고한다. 유료 API·새 학습·전체 자료 자동 번역은 이 비교의 부수 작업으로 실행하지 않는다.

### 28.7 후순위로 보존할 TG27 작업

읽기6 생성·57용어 대조·읽기12질문/핵심6, 개발36질문/핵심18은 완료로 표시하지 않는다. 새 공통 개선의 선행조건에서 제외했으며, 이후 비교 마무리에 필요한 경우 재개한다. 완료 개발18·원문149용어 선정·기존 후보 평가·원장 구현·캐시 정리를 반복하지 않는다. TG27 중요 오류5문단은 읽기·질문 결과가 좋아도 해당 구성의 탈락 근거로 유지한다.

재개할 때는 먼저 [27절 재시도 스크립트](.training/verifications/retry-tg27-reading6-after-disconnect-20260912.py)의 기존 출력 재사용 거부와 계획 출력의 미정의 `TAIL`/개발 ID 잔존을 새 파일에서 보완한다. [부분 검토](scripts/model-comparison/partial_tg27_recovery_review.py)와 [원장 연결](scripts/model-comparison/record_tg27_recovery_reviews.py)은 이전 읽기 run·awake·코드 해시에 고정돼 있으므로 새 run·awake·검토 snapshot·receipt·코드 정체성을 묶는 별도 연결이 필요하다. 옛 파일을 고쳐 과거 증거를 손상시키지 않는다.

기존 [질문 준비 도구](scripts/model-comparison/prepare_finance_answerability.py)는 단일 completed 실행의 전체 집합을 요구한다. 실패16+성공2를 성공18로 우회 전달하지 않으며, 개발 질문까지 마무리할 때는 실행별 이력을 보존하는 별도 진단 계약이 필요하다. TG27을 실제 재시작하려면 AC·조회 성공·native0·physical11GiB 이상 및 기존 commit 조건의 새 두 관측을 충족한다. 중단된 run/state/log와 최종 종료 주체 미확정 기록은 그대로 남긴다.

### 28.8 다음 시작점과 완료 보고

**최신 S5 상태 — 질문9/64개 실제 저장, 남은55개 부분 복구 준비.** 배터리 v4는9개의 단일 호출과 소유 프로세스 종료를 마친 뒤 R010의 시작 메모리 검사에서 중단됐다. 배터리는72%였고, 첫 관측의 여유 메모리가9GiB보다30,420,992바이트 부족했다가 두 번째 관측에서 회복됐다. [부분 복구 v5](content/model-comparison/input-execution-v1/LOCAL_QUESTION_PARTIAL_RECOVERY_V5.md)는 원9개와 원래 failed 실행·claim을 보존하며 R010~R064만 새로 생성한다. 시작 메모리 기준을 유지하고 native 생성 전 최대300초 회복을 기다린다. 현재 복구 구현·검증을 준비 중이며, 전체64개 답변 동결·질문 채점·후보 선택·S6은 아직 미완료다. [실제 중단 감사](.training/verifications/question-battery-partial-stop-audit-20260914.json)를 기준으로 이어간다.

**최신 요청 범위는 S4부터 조건부 S6까지이며 S4를 완료했다.** 최초 `s4-generation/attempt-001`의 failed38개/39회 요청과 응답 미확보1회를 보존했다. [복구 계약](content/model-comparison/input-execution-v1/RECOVERY_V1.md)의 새 실행은 AC1·자원 사전검사·64개 서버 입력 대조 후 남은26개를3,072.766초에 모두 저장했고 소유 native 종료·임시 전원 요청 해제·최종 입력/출력 무결성을 확인했다. [v2 cohort 검증](content/model-comparison/input-execution-v1/RECOVERY_COHORT_VALIDATION_V2.md)과 정식 S5 준비를 마쳤으며 원래 실패를 성공으로 고치지 않는다. 원문64개 검토는16단위의 잠정/정식 packet 바이트 동일성과 실제 작성 판정을 대조해 채택했고 같은 S3 v2의64출력 관계 진단도 저장했다. 새 에이전트 생성 한도로 [로컬 독립 질문 절차](content/model-comparison/input-execution-v1/LOCAL_QUESTION_REVIEW_PROTOCOL_V1.md)를 추가했다. [16파일 실행 동결](content/model-comparison/input-execution-v1/local-question-execution-freeze-v1.json) 후 한국어 packet마다 새 Qwen native 프로세스의 단일 답변을 사용하며 전체64개 답변을 먼저 동결하고 원문 채점한다. 고정 설치의 runtime51개+모델1개 대조를 [교정9파일](content/model-comparison/input-execution-v1/local-question-inventory-correction-freeze-v2.json)로 별도 동결했다. 실제 질문 시도는 claim 작성 후 PowerRequest 진입에서 실패했으며 context/native/completion0이다. 이후 두 관측에서 AC0/ac_power_required를 확인했다. [현재 중단 기록](content/model-comparison/input-execution-v1/QUESTION_POWER_STOP_20260914.md)을 보존하는 [복구 v3](content/model-comparison/input-execution-v1/LOCAL_QUESTION_ZERO_CALL_RECOVERY_V3.md) 코드와 평가 연결 검사를 완료하고 [새9파일](content/model-comparison/input-execution-v1/local-question-zero-call-recovery-freeze-v3.json)을 동결했다. 원래 claim을 그대로 유지하며 사전조건 실패는 새 run/claim을 소비하지 않는다. [새 실제 사전검사](.training/verifications/question-recovery-preflight-20260914.json)도 두 차례 AC0/ac_power_required를 기록했고 native/질문 호출0이다. 사용자는 2026-09-14 후속 지시로 충전기 없이 배터리 전원에서 계속 실행하도록 명시했다. [배터리 실행 v4](content/model-comparison/input-execution-v1/LOCAL_QUESTION_BATTERY_EXECUTION_V4.md)는 AC0/1과 알려진 배터리 잔량20% 초과를 허용하며 CPU4·메모리·소유 프로세스·시간 제한과 질문/평가 기준을 유지한다. 이전 AC 전용 실패·동결을 보존하고 새 실행 정체성으로 진행한다. 실제 후속 실행에는 v4를 사용하며 v3 재실행은 차단 표시로 막는다. 2026-09-14 17:10 KST에 배터리 실행 v4를 실제 시작했다. [39개 구현 검사와 동결 검증](.training/verifications/question-battery-freeze-verification-20260914.json)을 마쳤고 [실제 사전검사](.training/verifications/question-battery-preflight-20260914.json)는 배터리80%·AC0에서 통과했다. 첫 소유 native 프로세스의 입력/토큰 대조 후 질문 요청을 전달했다. 전체64개 답변 저장·종료·무결성 확인과 답변 동결 뒤 채점한다. 아직 완료나 후보 선택을 선언하지 않는다. 질문·채점·후보 선택·S6은 아직 미완료다. 새 금융 힌트0과 기존 평가 기준, 기존 앱 등록·DB·개인 기록을 유지한다. 아래 초기 점검은 당시 이력이다.

각 단계 후 이 절의 체크 상태와 함께 날짜, 변경 파일/정책, 실제 완료 입력·출력 범위, 실행한 검사, 생성/탐지/질문 지표의 분자·분모, 회귀·보류·외부 제약, 산출물 경로, 다음 미완료를 기록한다. 구현 완료·코드 검사 통과·실제 모델 생성·의미 개선·독립 평가 통과·앱 적용을 구분한다. 품질 결과를 기록할 때 [README](README.md)·[구현 상태](IMPLEMENTATION_STATUS.md), 계약 변경 때 [요구사항](DAMODARAN_KO_LEARNING_SPEC.md)·[시스템 설계](SYSTEM_DESIGN.md)를 함께 맞춘다.

2026-09-13 최초 계획 작성 당시의 갱신 범위는 인수인계와 관련 문서의 계획·연결 정리였다. 그때는 앱/도구 구현·모델 추론·가중치 학습·등록 변경·운영 DB 변경을 수행하지 않았다. 아래 초기 문서 검증과 뒤이은 S1/S2/S3 완료 기록을 구분한다.

문서 검증에서는 이번 변경 전 사본과 대조해2~27절 본문 보존, 새28절1개와 미착수 단계6개, 새 로컬 링크/목차 연결20개, 추가 문장의 공백을 확인했다. 별도 도우미가 C0 고정·원문/문맥 구분·평가 정답 분리·생성/검출 분리·독립 평가 조건을 검토해 중요한 모순이나 완료 오표시를 발견하지 않았다. 이 결과는 문서 검증이며 앱/엔진 검사 결과가 아니다.

**2026-09-13 후속 — S1 완료:** [입력 감사](content/model-comparison/input-preparation-v1/CURRENT_INPUT_AUDIT.md)에서 활성 Hy7 등록·가중치 전체와 runtime/code 등104파일을 검증했다. [S1 계약·자료](content/model-comparison/input-preparation-v1/README.md)는16단위·48명제·32질문(핵심16)·39용어를 고정하고, 새 사전8개념·24뜻·11출처와 후속 독립 금융60/일반20 선정 절차를 연결한다. 공개 원문4개는 기존 Marian train 중복 회귀이며 독립 원문이 아니다. 기존14파일·2,345원문/문맥 행의 기계적 대조에서 소비 test 중복0·새 그룹 밖 중복0을 확인했다. 공개 본문·관련 평가 인용은 Git 제외 `source-bundles/`에만 보존했다.

별도 도우미의 금융 원문·계약 대조와 root의 일반 원문 대조 후 부정/가정/상대%의 모호성, 힌트 prompt 필드, 단위×후보별 질문 노출 조건을 동결 전에 보완했다. [S1 검증기](scripts/model-comparison/verify_input_preparation_v1.py)는JSON·소속·정확 인용·용어 위치·분모·중복·해시를 검사하며 앱/모델을 호출하지 않는다. Windows 실행 명령은 완료 자료에 있다. 실제 tokenizer·추론·생성/탐지/질문 품질 점수·앱 build/E2E는 이번에0회이며, 운영 DB·원문 버전·개인 기록·활성 등록·기존 원장을 변경하지 않았다. S2~S6 및 TG27 후순위 미완료는 그대로다.

**2026-09-13 후속 — S2/S3 완료:** 문맥22·사전24·입력 통합13·관계v1 6/v2 8·집계5, 코드 검사78개가 통과했다. S2의64개 고정 입력은 실제 native tokenizer88요청으로281~621토큰을 확인했고 코드9개/산출물5개 manifest SHA는 `b007e567881c1a6dfa556c775eda65916aa49d76559dedc14420c0e127866653`이다. 성공 실행11.750초·peak working set103,772,160바이트·어휘 전용 로드1·소유 exit0이며 가중치 tensor/context/번역 생성0이다. Node C0 문맥16/16·등록 builder/Jinja 동일성을 확인했다. 앞선 smoke와 코드 해시 재검사의 경로 처리 오류가 난 attempt-001을 보존하고 수정 후 새 attempt-002를 완료했다.

새 사전의 실제 금융 힌트는0이며21출현 결정은 미지원11·모호8·중첩 억제1·비금융 억제1이다. C2는 F02/F04의 기존3힌트 제거만으로 C0와 달라졌고 C1/C3 문맥 변경은16개 모두에 적용됐다.64개 논리적 prompt는36개 서로 다른 문자열이다. 입력 토큰 감소나 힌트 제거를 번역 의미 개선으로 표시하지 않는다. S4 출력 재사용도 처리 정체성을 포함한 기존 계약대로 판단한다.

S3는 실제98출력·122원시 판정·930근거 해시를 보존했다. 최종 합성64개, UTF-16 인용196개를 확인했으며 제한 관계 지원은40/588·일부 지원 출력36/98이다. 경고1개는 기존 TG27 상대%→%p 오류 재검출이다. 초기v1 표기 오탐5도 결과/코드와 함께 보존했다. 기존 원장738사건에 relation_observation98개를 추가해836이며 반복 추가0/재사용98, 번역 검토146개와 나머지 원시 판정은 그대로다. 실제 유형별 gold·독립 검출력·QE95/95·생성/질문 평가·앱 등록 통과를 주장하지 않는다.

07:31 UTC의 S4 읽기 점검은 ACLineStatus=0, 가용 physical11.535GiB/commit19.850GiB였다. native 이름 일치0/명시worker0이나 미분류Node가 있어 전체 모델·worker 부재는 확정하지 않았다. 과거 Hy7의8GiB WS 제한 실행과 buffer 기록을 근거로 자원 조건을 제안했으나 아직 동결하지 않았다. 별도 생성기/잠금/자원 감시/임시 전원 요청·서버 endpoint 대조가 남아 있고 실제 생성은 시작하지 않았다. 운영 DB·원문 버전·개인 기록·활성 등록은 유지했다. 앱 build/E2E 및 실제 HTML3문단/PDF1페이지 생성·저장·캐시 검증은 이번 범위에서 실행하지 않았다.
