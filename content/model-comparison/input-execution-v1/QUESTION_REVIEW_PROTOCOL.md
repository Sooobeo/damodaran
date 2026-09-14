# 독립 한국어 질문 답변 전달 절차

S4의64개 완료·소유 종료·최종 무결성 검증과 정식 S5 packet 생성 후에만 실행한다. S1의 고정 해시 정렬 순서 R001~R064를 사용한다. 각 질문 답변 작업은 `fork_turns="none"`인 새 agent context 하나에 packet 하나만 전달한다. 구성/원문/정답/다른 후보/최소대립쌍/자료 작성 대화를 전달하지 않는다. 이전에 만든 source review agent를 질문 답변자로 사용하지 않는다.

전달하는 내용은 reviewId·한국어 translation·질문q1/q2와 아래 절차 지시뿐이다. 원문 경로·자료 제목·구성명·원래 질문 ID·core 여부도 제외한다. 답변자는 별도 자료를 검색하거나 저장소 문서를 읽지 않으며 번역만으로 답한다. 판단할 수 없다면 판단 불가와 그 이유를 적는다. 답변·이유·번역의 정확한 근거 인용을 기록한다. 질문이 암시하는 내용이나 사전 지식으로 잘못된 번역을 보충하지 않는다.

답변자는 지정된 새 답변 초안 파일 하나만 작성할 수 있다. UTF-8 JSON 구조는 다음과 같다. 인용은 원래 번역과 정확히 같아야 하고, 되풀이되는 짧은 표현이면 고유한 문장 전체를 인용하거나 명시적인 Unicode codepoint 구간을 제공한다.

```json
{
  "reviewId": "R001",
  "answers": [
    {"questionId": "q1", "answerKo": "실제 답변", "reasonKo": "번역에 근거한 이유", "translationEvidence": ["정확한 번역 인용"]},
    {"questionId": "q2", "answerKo": "실제 답변", "reasonKo": "번역에 근거한 이유", "translationEvidence": ["정확한 번역 인용"]}
  ]
}
```

`cannotDetermine:true`인 경우만 빈 근거 목록을 허용한다. root는 내용·정답을 대신 작성하거나 잘못된 답변을 개선하지 않는다. 형식/인용 오류를 수정해야 하면 동일 context에서 해당 형식 문제만 안내하고 이전 답변을 남긴다. 원문과 정답으로 답변을 고치는 재시도는 하지 않는다.

root는 실제 spawn 결과의 actor ID, `fork_turns=none`, 전달 packet의 SHA와 출력 초안 SHA를 배정 기록에 연결한다. 개별 모델의 정확한 runtime 식별자가 도구에서 노출되지 않으면 `Codex (inherited model; exact runtime model ID unavailable)`로 명시하며 추정하지 않는다. 모든 답변의 provenance는 `humanReviewed:false`, 새 context, 사전 노출 없음, 허용 노출 translation/questions 두 개만으로 기록한다.

전체64답변을 검증·동결한 뒤 다른 원문 평가자가 채점한다. 답변자와 채점자 actor가 달라야 한다. 동결 뒤 점수에 맞춰 답변을 바꾸지 않는다. 이 결과는 도우미의 번역 독해 시험이며 사람의 학습 효과나 인간 독립 검수로 해석하지 않는다.
