# S3 저장 출력 관계 진단

2026-09-13. [S1 계약 6절](../input-preparation-v1/CONTRACT.md#6-s3-관계-검사의-사전-범위)에 따라 모델 호출 없이 기존 **98개 출력**을 새 관계 검사기로 진단했다. 최종 v2 경고는 TG27 `LDEV26-006`의 상대 %→%p **1개**다. 이 오류는 기존 `finance-meaning-rules-v1`에서도 이미 발견한 사건이므로 새 번역 오류나 기존 의미 검사 대비 검출력 증가로 세지 않는다. 생성 품질 개선은 측정하지 않았다.

## 자료와 보존

| 기존 출력 층 | 실제 출력 | 원래 실행 상태 | 원래 검토 기준 |
|---|---:|---|---|
| Hy7/Hy30 개발18·읽기6 | 48 | 완료 | `assistant-round2-v4` |
| TG27 실패 실행의 완료 개발 문단 | 16 | 실행 실패, 개별 출력 완료 | `tg27-failed-run-16-diagnostic-review-v1` |
| TG27 별도 복구 실행 | 2 | 완료 | `tg27-recovery-run-root-review-v1` |
| 일반16 Hy7 raw/contextual | 32 | 완료 | `general-meaning-assistant-review-v1` |

오류가 있는 출력만 고르지 않았다. 98개 모두 개별 저장 상태는 completed이며 실패 실행의 16개를 성공한 전체 실행으로 바꾸지 않았다. TG27 읽기6은 출력이 없어 편입하지 않았다. Hy7의 과거 `adjusted-hy7-tg12-v1` 24판정도 같은 출력에 별도로 연결하여 **98출력·122 baseline 관측**을 기록했다. 서로 다른 검토회차를 새 출력이나 새 오류로 더하지 않는다.

검사 전에 [v2 입력 manifest](../../../.training/quality-evaluation/input-preparation-v1/s3-relations-20260913-v2/relation-manifest.json)에 실제 원문·출력·원시 자동 검사·판정·실행 상태·930개 근거 파일의 바이트 해시를 고정했다. [사후 검증](../../../.training/quality-evaluation/input-preparation-v1/s3-relations-20260913-v2/relation-verification.json)은 98출력/122판정과 실제 UTF-16 인용 196개, 원장 관측98개, 기존 근거 파일 보존을 확인했다. 원문·번역·자동 숫자 판정·의미 판정은 수정하지 않았다.

## 구현과 반례

[검사기 v2](../../../scripts/model-comparison/input_preparation_v1/relation_checks_v2.py)의 입력은 영어 원문과 한국어 번역 문자열 두 개뿐이다. 평가 ID·참조 번역·명제·질문 정답·원장 판정은 검사기에 전달하지 않으며, 기존 판정은 검사 후 집계에만 연결한다. 일반적인 의미 분석 모델은 사용하지 않는다.

지원 상태는 `supported_match`, `supported_conflict`, `undetermined`, `unsupported`, `source_ambiguous`다. 지원 일치/충돌에는 양쪽 실제 인용과 UTF-16 offset이 있으며, 나머지 세 상태는 통과가 아니다. 값·단위 다중집합 일치도 수량의 대상이나 역할까지 보존됐다는 뜻이 아니다.

검사 전 [최초 합성46개](relation-fixtures-v1.json)와 [해시 영수증](relation-fixtures-freeze-v1.json)을 고정했다. 첫 실제 v1 진단에서는 정상적인 `%포인트` 표기4개와 `67.44%까지 상승`1개를 잘못 경고했다. [v1 실제 결과](../../../.training/quality-evaluation/input-preparation-v1/s3-relations-20260913-v1/relation-report.json)·코드는 그대로 보존했고 원장에는 등록하지 않았다. 이어 [표기 회귀10개](relation-fixtures-v2-extra.json)·[독립 코드 검토 반례8개](relation-fixtures-v2-review.json)를 별도 해시로 동결했다. `months`의 `m` 오인, Unicode 음수 부호, 같은 절의 다른 포함/제외 대상, 이중부정 범위도 수정·보류 처리했다. 독립 도우미가 해당4개 수정을 실행으로 재확인했다.

최종 **합성64개가 모두 기대 상태와 일치**했다. v2 검사기 unittest8개와 집계·원장 중복·불변 파일 검사 unittest5개가 통과했다. 합성 정답 일치는 모델 출력 정확도나 미노출 탐지 성능이 아니다. v1의 실패를 본 뒤 고친 v2이므로 개발 회귀 결과다.

## 실제 지원 범위와 미검사

98출력×6유형=588개 관계 상태다. 적어도 한 유형이 명시적으로 지원된 출력은 36/98이며 문단 전체 의미 검사 coverage가 아니다.

| 유형 | 지원 일치 | 지원 충돌 | 판단 보류 | 미지원 | 원문 범위 불확실 |
|---|---:|---:|---:|---:|---:|
| 값+단위, 자연 표기 | 28 | 1 | 0 | 69 | 0 |
| %/%p와 변화 방향 | 2 | 0 | 6 | 90 | 0 |
| 시작값·증가분·결과값 | 2 | 0 | 6 | 90 | 0 |
| 명시 숫자 나눗셈 방향 | 0 | 0 | 0 | 98 | 0 |
| 포함/제외·제한된 부정 범위 | 1 | 0 | 5 | 92 | 0 |
| 이후/도중·필요조건 | 6 | 0 | 6 | 83 | 3 |

숫자 피연산자끼리의 나눗셈 반례는 지원하지만, 실제 자료의 `72 ÷ 할인율`처럼 일반 명사와 숫자가 결합된 나눗셈은 미지원이다. 시간/조건은 통합·서명·코드 유효성의 명시 사건에, 포함/제외는 단일 절의 지원 대상과 술어에 한정한다. 다중 술어·생략·중첩 부정·암묵적 계산·일반 당사자 역할은 자동 확정하지 않는다. 실제 해당 유형의 정답 표본이 없는 경우 합성 성공으로 실제 coverage를 채우지 않았다.

## 기존 문단 판정 대비 경고

아래 행렬의 양성은 **기존 문단 major/critical**이고 검출은 관계 경고 유무다. 미지원/보류로 경고하지 못한 중요 오류도 운영상 FN에 남긴다. TN은 기존 판정에 중요 오류가 없고 경고하지 않았다는 뜻이며 검사 통과가 아니다. 기존 unresolved는 행 분모에 유지하고 TP/FP/FN/TN에서 따로 분리한다. minor는 문단 중요 오류 기준의 음성이다.

| 층·판정 회차 | N | TP | FP | FN | TN | 기존 보류 |
|---|---:|---:|---:|---:|---:|---:|
| Hy7 개발18 adjusted | 18 | 0 | 0 | 4 | 14 | 0 |
| Hy7 개발18 round2 | 18 | 0 | 0 | 5 | 13 | 0 |
| Hy30 개발18 round2 | 18 | 0 | 0 | 5 | 13 | 0 |
| TG27 실패 실행16 | 16 | 1 | 0 | 4 | 6 | 5 |
| TG27 복구2 | 2 | 0 | 0 | 0 | 1 | 1 |
| 일반 Hy7 raw16 | 16 | 0 | 0 | 3 | 12 | 1 |
| 일반 Hy7 contextual16 | 16 | 0 | 0 | 2 | 13 | 1 |
| Hy7 읽기6 adjusted | 6 | 0 | 0 | 1 | 5 | 0 |
| Hy7 읽기6 round2 | 6 | 0 | 0 | 2 | 4 | 0 |
| Hy30 읽기6 round2 | 6 | 0 | 0 | 1 | 5 | 0 |

[실제 JSON 보고서](../../../.training/quality-evaluation/input-preparation-v1/s3-relations-20260913-v2/relation-report.json)는 각 baseline별로 6유형 각각의 TP/FP/FN/TN·경고·지원범위·미검사 오류를 따로 담는다. **이 유형별 행렬도 각 유형의 경고를 문단 판정에 비교한 결과이며, 관계 유형 자체를 별도로 수동 gold로 주석한 정확도는 아니다.** 별도 관계 gold 주석과 독립 calibration/holdout은 미완료다. 전체 QE95/95, 빨간 구간 정확도, 기지6개, 앱 등록 수용을 통과했다고 주장하지 않는다.

TG27 `LDEV26-006`의 기존 숫자·기호 자동 검사는 통과였고 원문 의미 판정은 major였다. 새 값/단위 검사가 경고한 상대%→%p는 [기존 의미 규칙의 실제 관측](../../../.training/verifications/tg2716-existing-meaning-rules-20260912.json)에서도 경고했다. 따라서 이번 작업은 같은 오류의 관계 관측 연결과 제한된 검사 구현·회귀 검증이며, 기존 검사보다 새 오류를 더 찾았다는 성과는 없다.

## 원장 연결과 재실행

기존 `ledger.append_events`를 그대로 재사용해 `relation_observation`98개만 추가했다. 반복 실행은 **추가0·재사용98**이다. 원장은738→836사건이며 번역 검토146·질문76·기존 QE120·실행6·용어390은 그대로다. 새 관측은 기존 오류의 출력/검토 사건 ID를 참조하고 번역 오류 사건을 추가하지 않는다. [사후 검증](../../../.training/quality-evaluation/input-preparation-v1/s3-relations-20260913-v2/relation-verification.json)에 연결·중복 검사 결과를 남겼다.

```powershell
# 검사기와 집계·불변성 검증
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/input_preparation_v1/test_relation_checks_v2.py
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/input_preparation_v1/test_relation_diagnostic.py

# 기존 동결 진단을 재검증한다. --append-ledger도 동일 관측 추가0이다.
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/input_preparation_v1/run_relation_diagnostic_v2.py run --output .training/quality-evaluation/input-preparation-v1/s3-relations-20260913-v2 --append-ledger
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/input_preparation_v1/verify_relation_artifacts.py --output .training/quality-evaluation/input-preparation-v1/s3-relations-20260913-v2
```

이번 완료 범위는 저장 출력 관계 진단·개발 반례·불변 증거·기존 원장 연결이다. 모델 로딩·번역 생성·가중치 학습·앱/운영 DB·원문 버전·등록 제공자 변경은 없었다. 앱용 독립 의미 검사로 승격하지 않았다.
