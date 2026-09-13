# Hy7 일반 문맥 비교 — 실제 실행과 학습용 평가

2026-09-11. 동결된 일반 12개·문맥 다의어 4개에서 Hy7 원문 전용(raw)과 문맥 프로필(contextual)을 각각 실행했다. **두 구성 모두 학습용 수용 기준을 충족하지 못했다.** 모델 가중치 학습·앱 재등록은 하지 않았다.

## 같은 조건과 비교 범위

동일 Hy7 Q8 가중치, CPU 4스레드, 실제 native BelowNormal, 문맥 길이 8192, 관측 작업 집합 상한 8GiB, 동일 샘플링을 사용했다. 각 실행마다 새 소유 프로세스를 만들고 앞선 모델의 종료를 확인했다. 두 실행의 생성·EOS·원시 응답·프롬프트·모델·코드·무결성·종료 관계를 [짝 검증 기록](../../.training/comparisons/general-context-dev-20260911/profile-pair-below-normal-v1.json)에 보존했다. 각 시작의 코드 39개도 별도 보관했다.

차이는 기존 일반 지시·문체와 네 항목의 이웃 문맥이다. 조건부 금융 용어 힌트 삽입은 0건이다. 따라서 아래 결과는 **프로필 전체의 비교**이며 이웃 문맥만의 효과, 금융 용어 삽입의 회귀, 가중치 적응 효과로 해석하지 않는다. 자세한 실행 계약은 [GENERAL_CONTEXT.md](../../scripts/model-comparison/GENERAL_CONTEXT.md)를 따른다.

## 결과

| 관측 항목 | 원문 전용 | 문맥 프로필 |
|---|---:|---:|
| 생성·원시 무결성·종료 | 16/16 | 16/16 |
| 기존 자동 검사 실패 | 0 | 0 |
| 확정 critical / major 오류 문단 | 0 / 3 | 0 / 2 |
| 의미 판정 보류 문단 | 1 | 1 |
| 중요 오류 없음으로 확정한 문단, 보류 포함 전체 분모 | 12/16 (75%) | 13/16 (81.25%) |
| 사전 핵심 명제 보존 / 불보존 / 보류, 총61개 | 52 / 8 / 1 | 52 / 8 / 1 |
| 유창한 문단 | 16/16 | 16/16 |
| 한국어 전용 질문 정답 | 30/32 (93.75%) | 29/32 (90.625%) |
| 핵심 질문 정답 | 15/16 (93.75%) | 14/16 (87.5%) |
| 질문 답변 누락 / 채점 보류 | 0 / 0 | 0 / 0 |
| 학습용 수용 | 미달 | 미달 |

명제 61개는 오류 사건 수와 다르다. 고무 받침 하나의 오역이 반환·점검·교체 대상이라는 여러 명제를 함께 손상할 수 있다. 사전 계획의61개 모두 core=true이며 [후속 읽기 감사](../../.training/quality-evaluation/general-meaning/pair-audit-v1.json)에서 ID·해시·명제키·실제 인용·집계를 재검증했다(SHA `f374b513b3fcab8901fd31b4e9431becef9c6e4cf1a21e88c1d16c644d09c63b`). 두 후보의 핵심 명제 보존은52/61로100% 기준 미달이다. 보류를 분모에서 빼거나 경미한 표현의 유창성을 의미 정확성으로 바꾸지 않는다. 전체 문맥 용어 출현을 사전에 열거한 별도 분모는 없으므로 **전체 용어 95% gate는 미평가**다. 자동 검사 16/16은 의미 수용 기준을 대신하지 않는다.

문맥 프로필은 `GCTX26-009`의 이동 대상을 명단에서 **이름**으로 바로잡았지만, 두 구성 모두 `Members`를 작업자/직원으로 옮기고 `rubber foot`를 의자 다리로 옮겼다. 문맥 프로필에는 `plant swap`의 식물을 공장으로, `paint the scenery`를 풍경 그리기로 옮긴 배경·용도 오류도 있다. 이 두 오류는 검토에서 minor로 분류했으며 원시 판정과 인용을 보존했다. 특정 한 사례가 나아졌다는 이유로 프로필을 승격하지 않는다.

두 구성 모두 반죽의 냉각 종료 기준에서 **찢어지지 않게 들어 올릴 수 있는 상태** 중 들어 올리는 조건을 빠뜨렸다. 핵심 조리 순서는 남아 의미 검토의 심각도는 minor지만, 사전 핵심 명제와 해당 질문의 필수 사실은 충족하지 못했다. 심각도·명제·질문 지표가 서로 다른 이유다.

## 검토와 채점의 분리

질문 32개·핵심 16개는 실제 번역을 보기 전에 동결했다. [질문지](general-context-questions-20260911.md)의 JSON SHA는 `4faac811b4b2644fe73f2b2adcfa9862207243988e963f4e7d331d627900ed51`이다. 서로 다른 새 `fork:none` 답변자는 각각 질문·한국어 번역·사전 허용 한국어 문맥만 읽었다. 원문·정답 키·다른 번역은 제공하지 않았다. 답변 파일을 동결한 다음 root가 영어 원문 근거로 32개를 각각 수동 채점했으며 문자열 일치로 의미를 판정하지 않았다. 동결된 채점기 버전은 수정하지 않았다.

- 원문 전용: [답변 동결](../../.training/quality-evaluation/general-answerability/raw-v1/answers-freeze.json), [원문 근거 채점](../../.training/quality-evaluation/general-answerability/raw-v1/root-judgments-v1.json), [집계](../../.training/quality-evaluation/general-answerability/raw-v1/grade-report-v1.json). `004-Q1`, `005-Q2`가 미통과다.
- 문맥 프로필: [답변 동결](../../.training/quality-evaluation/general-answerability/contextual-v1/answers-freeze.json), [원문 근거 채점](../../.training/quality-evaluation/general-answerability/contextual-v1/root-judgments-v1.json), [집계](../../.training/quality-evaluation/general-answerability/contextual-v1/grade-report-v1.json). 위 두 문항과 `002-Q1`이 미통과다.

`002-Q1`에서 문맥 프로필 답변자는 번역의 직원과 질문의 회원이 같은 대상인지 알 수 없다고 적절하게 보고했다. 원문은 Members로 명확하므로 질문에 완전히 답하지 못한 것으로 채점했다. 원문 전용 답변자는 대상 차이를 문제 삼지 않고 출입 조건에 답했다. 이 **답변자 차이** 때문에 한 문항의 점수 차이를 문맥 적용의 확정적 악화로 해석하지 않는다. 질문에 답할 수 있는지와 문단 전체의 의미 오류를 별도로 확인해야 하는 실제 사례다.

의미 검토자는 후보를 보기 전에 [원문 명제 계획](../../.training/quality-evaluation/general-meaning/source-plan-v1/assistant-propositions.json)의 16개·61명제를 고정했다(SHA `9f54a2b1ab0f8c62c53eab1eb65a17b40656bb9ba39e1e003d33978594512a19`). 그 계획은 검토자의 후보 열람보다 앞서며 모델 생성 전체보다 먼저 작성된 것은 아니다. 모델·프로필을 가린 [후보 A 검토](../../.training/quality-evaluation/general-meaning/candidate-a/assistant-review.json)와 [후보 B 검토](../../.training/quality-evaluation/general-meaning/candidate-b/assistant-review.json)를 순차 수행했고, B에서는 A를 이전에 검토한 사실을 공개했다. 독립 인간 검수나 실제 학습 실험은 아니다.

`GCTX26-001`의 영어 his는 루이스를 선호하지만 유일한 소유자로 확정할 근거가 충분하지 않다. 번역의 소유자 생략과 다른 소유자로 바꾼 오류를 구분해 [별도 모호성 기록](../../.training/quality-evaluation/general-meaning/candidate-a/ambiguity-note-001.json)에 보류했다. 기존 원문·질문·정답·판정을 사후 수정하지 않았고 보류 행도 16개 분모에 유지했다. 향후 이 기준을 고치려면 새 평가 버전이 필요하다.

## CPU·메모리와 시간

| 실제 자원 관측 | 원문 전용 | 문맥 프로필 |
|---|---:|---:|
| 전체 경과 시간 | 1513.953초 | 2917.860초 |
| peak working set | 약 7.94GiB | 약 7.94GiB |
| 관측 최소 가용 물리 메모리 | 약 4.94GiB | 약 3.25GiB |
| native 우선순위 | BelowNormal | BelowNormal |
| CPU 스레드 | 4 | 4 |
| guard 오류·소유 child 종료 실패 | 0 | 0 |

문맥 프로필의 총 경과 시간에는 **1684.281초의 관측 공백**이 포함돼 있다. Windows Modern Standby의 13:22:15~13:50:17 KST 이벤트와 겹쳤고 같은 native 프로세스가 이후 처리를 계속했다. [관측 기록](../../.training/verifications/general-contextual-standby-observation-20260911.json)을 보존했다. 이 전체 시간을 모델 속도와 직접 비교하거나 공백을 단순히 빼서 정확한 연산 시간이라고 주장하지 않는다. 전역 전원·덮개 설정, 사용자 앱은 변경하지 않았다. CPU 4스레드와 BelowNormal은 CPU 사용률의 하드 상한이 아니다.

실행 summary SHA는 원문 전용 `a0752d7448083d6cc3204c6a5bd33407959e40cbb6fe70247595f862a74394af`, 문맥 프로필 `ad68792d47e51701440c4ca9462c3155add8f5820dc25c66f99bd164c223f77a`다. 이 자료는 개발용이며 학습 데이터나 새 독립 최종 시험으로 재사용하지 않는다. 다음은 별도 검사 모델 48개 평가와 TG27 개발18·읽기6을 순차 완료해 후보 판단을 마치는 단계다.
