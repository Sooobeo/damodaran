# 금융 읽기6 — Hy30 한국어 전용 질문 평가

2026-09-11. 저장된 Hy30 v4 읽기6 번역을 다시 생성하지 않고, 고정 질문 v2의12문항에 대한 한국어 전용 답변을 평가했다. **전체10/12=83.33%, 핵심4/6=66.67%로 질문 수용 기준을 통과하지 못했다.** 누락·채점 보류·답변 상태 충돌은0이다. 실제 사람의 학습 효과 측정이나 전체 번역 품질 인증은 아니다.

## 기준과 절차

[학습 수용 기준](LEARNING_READINESS_BASELINE_20260911.md)은 읽기6의 전체12/12·핵심6/6을 요구한다. [질문지 v2](../../.training/quality-evaluation/finance-answerability/source-only-v1/assistant-questionnaire-v2.json)는 원문만 본 도우미가 작성하고 root가 원문 기준으로 검토했다. SHA `a946103f3068b64e54107f4b96a01834857b146d2190e7b5f5e009b9cef1d75b`다. **이 질문지는 기존 Hy30 출력보다 나중에 작성됐으므로 사후 개발 평가다.** 새 TG27 생성 전에는 질문을 동결했다.

[실제 Hy30 실행](../../.training/comparisons/real-reading-check-20260910/hymt30-paging-v4/summary.json)의 완료·입력·코드·모델 설치 정체성·원시 응답·guard·소유 종료를 [금융 평가 도구](../../scripts/model-comparison/FINANCE_ANSWERABILITY.md)로 읽기 검증했다. 원문·기존 번역·이전 의미 판정은 바꾸지 않았고 새 모델 호출은0회다.

새 `fork:none` 답변자는 익명 질문 ID, 한국어 질문, 실제 한국어 번역, 빈 허용 한국어 문맥만 읽었다. 영어·정답·모델 정체성·핵심 표시·기존 판정은 전달하지 않았다. [답변](../../.training/quality-evaluation/finance-answerability/reading-candidate-a-v1/answers-a-v1.json) SHA는 `a763a672a5929d44e6ea9490000e4f540c0ac1b5e465f1cb13afa3b439442e0b`다.12개 모두 answered였으며, [동결 기록](../../.training/quality-evaluation/finance-answerability/reading-candidate-a-v1/answers-freeze-v1.json)을 먼저 만든 뒤 root가 원문·고정 기준으로 수동 채점했다.

root는 원문·모델 정체성·과거 비교를 본 coordinator이므로 채점 전체를 맹검이라고 하지 않는다. 두 경계 사례에는 모델·root 제안 판정을 알리지 않고 원문 우선 질문 작성자의 [독립 의견](../../.training/quality-evaluation/finance-answerability/reading-candidate-a-v1/source-question-author-consultation-v1.json)을 받았다. 질문·허용 표현·정답 기준을 수정하지 않았다. 모두 도우미 검토이며 `humanReviewed=false`다.

## 결과와 의미

| 구분 | 결과 | 기준 | 판정 |
|---|---:|---:|---|
| 전체 질문 | 10/12, 83.33% | 12/12 | 미달 |
| 핵심 질문 | 4/6, 66.67% | 6/6 | 미달 |
| 답변·판정 누락 | 0 | 0 | 충족 |
| 채점 보류·상태 충돌 | 0 | 0 | 충족 |

- **REAL26-001-Q1:** 미래 1달러의 현재가치 계산은 답했지만, 현금흐름의 평가 시점을 옮기는 기능은 넓은 ‘시간에 따른 분석’으로만 답했다. 고정된 구체적 필수사실을 넓은 상위 개념에 보충해서 인정하지 않았다.
- **REAL26-003-Q1:** 지급 약속자의 불이행은 답했지만, 수취인이 그때 없을 수 있다는 사유는 일반적인 수령 불가능 상황으로만 답했다. 사망으로 한정할 필요는 없으나 부재라는 의미까지 답변에 있다고 추정하지 않았다.
- 다른10문항은 동의 표현을 허용해 정답으로 판정했다. 국채 비교는 수익률·단기 구분으로 두 대상을 식별했으므로 ‘장기’라는 단어의 문자 일치만을 요구하지 않았다. 72의 법칙의 연산 방향·근사치와 두 단계 성장 전망도 해당 질문에 맞게 답했다.

[수동 판정](../../.training/quality-evaluation/finance-answerability/reading-candidate-a-v1/root-judgments-v1.json) SHA `2fd94ff699d2c76e0708dcd326bda19c069ddd348b84ea55d10d13fbbf16408b`, [집계 결과](../../.training/quality-evaluation/finance-answerability/reading-candidate-a-v1/grade-report-v1.json)에 각 질문의 원문 인용·사유·분모를 보존했다. substring 검사는 근거 구절의 존재만 확인하며, 정답은 단어 일치로 자동 채점하지 않았다.

이 결과는 해당 질문의 답변 가능성을 측정한다. 두 오답을 그대로 새로운 major 오류2건으로 바꾸거나 기존 문단 의미 판정을 덮어쓰지 않는다. 용어 전체 inventory·독립 시험·일반16·개발18 질문 결과로 확대하지 않으며 전체 학습 수용은 미평가 상태다. 합성13개 도구 검사와 이번 실제 보존 출력에 대한 질문 평가도 구분한다.
