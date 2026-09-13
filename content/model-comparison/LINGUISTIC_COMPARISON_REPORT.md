# 무료 로컬 금융 영한 번역 비교 — 2026-09-10

완료된 7B·12B 비교에서는 **현재의 Hy-MT2 7B contextual 구성을 유지할 잠정 근거가 있다.** Hy-MT2 7B는 개발 자료에서 중요 의미 오류 4/18개, 실제 공개 원문에서 1/6개였다. TranslateGemma 12B는 각각 6/18개와 3/6개였다. 다만 7B에도 금융 다의어·정보 범위·금융상품 오역이 남아 있어 최고 품질이나 학습용 번역의 하방을 달성했다고 볼 수 없다. 이번 결과로 새 모델을 등록하거나 앱 제공자를 전환하지 않았다.

사용자는 처리 시간보다 번역 결과의 품질을 우선한다. 아래 판단은 원문의 의미·조건·수량·누락과 한국어 자연스러움을 대조한 결과이며, 생성 속도나 모델 크기로 정한 순위가 아니다. 용어 표기 적중률과 번역 전체의 의미 정확도도 구분한다.

| 완료된 평가 | Hy-MT2 7B Q8, contextual | TranslateGemma 12B Q4_K_M, source-only |
|---|---:|---:|
| 개발 자료의 중요 의미 오류 행 (`severity >= 2`, 적을수록 좋음) | 4/18 | 6/18 |
| 개발 자료의 자연스러운 한국어 행 | 17/18 | 16/18 |
| 개발 자료의 주석된 용어 뜻 적중 | 12/12 | 11/12 |
| 개발 자료의 정규 용어 표기 적중 | 12/12 | 7/12 |
| 용어의 뜻과 정규 표기를 함께 만족한 출현 | 12/12 | 7/12 |
| 대립쌍의 양쪽 의미 차이 보존 | 5/6쌍 | 4/6쌍 |
| 공개 원문 6문단의 중요 의미 오류 행 | 1/6 | 3/6 |
| 공개 원문 6문단의 자연스러운 한국어 행 | 5/6 | 6/6 |

집계 원본은 [개발 18행·36판단](../../.training/comparisons/linguistic-dev-20260910/assistant-review-hy7-tg12-v1.json)과 [공개 원문 6문단·12판단](../../.training/comparisons/real-reading-check-20260910/assistant-review-hy7-tg12-v1.json)이다. 두 자료의 용도와 표본 구성이 다르므로 오류 수를 합쳐 전체 번역 정확도로 제시하지 않는다. 자연스러운 한국어 판정은 의미 정확도와 독립적이다. 실제로 12B는 공개 원문 6문단 모두 자연스럽게 읽힌다는 판정을 받았지만, 그중 3문단에 중요한 의미 오류가 있었다.

용어 12/12는 **사전 주석된 12개 출현**에 대한 결과다. 54개 용어 전체를 충분히 검사한 90% 시험이 아니며, 모든 금융 다의어가 이 분모에 들어가지도 않는다. 예를 들어 `LDEV26-008`의 단독어 equity는 고정 표제의 정규 표기 점수에서 제외하고 문맥상 뜻을 따로 검사했다. 자금 조달 구성의 자기자본이라는 뜻을 7B는 “균형”, 12B는 “공평성”으로 옮겼다. 따라서 7B의 용어 12/12와 금융 equity 오역은 동시에 성립한다. 이 점수를 번역 전체가 90% 이상 맞는다는 뜻으로 해석하면 안 된다.

| 관측 사례 | 원문에서 지켜야 하는 내용 | 실제 오류와 의미 |
|---|---|---|
| `LDEV26-008`, 두 구성 | 조달 구성의 equity는 자기자본 | 7B의 균형·12B의 공평성은 금융 자본을 다른 뜻으로 바꾼다. |
| `LDEV26-009/010`, 7B | 포함된 자회사가 몇 개인지 알 수 없음 | 어떤 자회사인지 알 수 없다고 옮겨 개수와 구성원 식별의 차이를 잃는다. 배당에 대한 부분·전면 부정 자체의 보존과는 별개다. |
| `LDEV26-011/012`, 12B | 통합 이후의 비용 감소 예측 | 인수합병 과정의 감소로 옮겨 시간 범위를 바꾼다. may/will의 예측 강도 자체는 보존하므로 이를 별도로 기록했다. |
| `REAL26-004`, 두 구성 | treasury bills와 treasury bonds의 금융상품 구분 | 단기 국채를 7B는 국고채, 12B는 국공채로 옮겨 비교 대상의 만기·상품 구분을 손상했다. |
| `REAL26-005`, 12B | 72를 이자율로 나누는 계산 | “할인율 또는 이자율을 72로 나누어”라고 옮겨 분자와 분모를 뒤집었다. 72·6·12·9·8 등 숫자와 예시가 남아 있어도 계산 지시는 틀릴 수 있다. |
| `REAL26-006`, 12B | 5년 이후의 연 6% 성장률이 영구히 지속됨 | 수치들은 보존했지만 영구 지속 조건을 생략했다. 숫자 문자열 보존만으로 성장 구간의 뜻을 검증할 수 없다. |

학습에 적절한 번역을 고르려면 용어 뜻·표기 외에도 주체와 수취자, 부정의 대상 집합, 조건과 시점, 수식의 피연산자, 지시 대상, 누락·추가를 원문과 대조해야 한다. 이번 오류는 단순 용어 치환만으로 해결되지 않는다. 생성된 번역이나 자동 검사 통과 결과를 그대로 정답 학습쌍으로 승인하지 않았으며, 이번 비교에서 가중치 학습을 실행하지 않았다.

평가 자료와 검토 범위는 다음과 같다.

- [개발 자료](linguistic-dev-20260910.jsonl)는 도우미 작성 미검수 자료 18개로, 금융 16개·일반 2개다. 6개 최소 대립쌍과 6개 문단으로 구성되며 학습에 사용하지 않는다. 대립쌍의 두 행은 서로 의존하는 관측이다.
- [공개 원문 자료](real-reading-check-20260910/sources.jsonl)는 저장된 Damodaran의 R02 PVPrimer 한 문서에서 출력 생성 전에 고른 6문단이다. 원문·버전·블록·파일 해시를 보존했고 운영 DB나 원문을 다시 추출하지 않았다. R05의 문자 손상 진단과 사전 제외 사유는 [별도 기록](R05_SOURCE_DECODING_NOTE.md)에 있다.
- 원문과 참조는 출력 생성 전에 고정했다. 참조 번역과 품질 판단은 모두 도우미 작성이며 **사람 검수는 0건**이다. 참조와 다른 자연스러운 표현을 허용하며 원문의 의미를 우선했다.
- 후보 순서를 원문별로 섞고 모델 이름과 키를 가렸다. 검수 파일의 행·후보·구절·해시 완전성을 확인한 뒤에만 키를 읽고 모델별로 집계했다. [개발 검수 규약](../../scripts/model-comparison/LINGUISTIC_REVIEW.md)과 [공개 원문 검수 규약](../../scripts/model-comparison/READING_REVIEW.md)을 사용했다.
- 경계 사례는 모델 키와 점수를 보기 전에 같은 출력에 대한 추가 도우미 대조로 조정했다. [사전 익명 조정 기록](../../.training/verifications/local-review-adjudication-20260910.json)은 초기 검수·독립 감사·최종 검수를 모두 연결한다. 원문·참조·품질 기준은 바꾸지 않았고 초기 판단 파일도 보존했다. 이는 새 독립 표본이나 사람 검수가 아니다.

사전 조정에서는 `REAL26-001`의 시간 간 환산을 고려·분석으로 넓게 옮긴 두 후보를 동일하게 2에서 1로 낮췄다. 바로 앞에서 미래 금액을 현재 가치로 계산하는 기능을 정확히 전달하므로 문단 전체에서 가치 환산의 핵심이 사라졌다고 보기 어렵다는 판단이다. 개발 자료의 overhead→운영 비용과 수식 설명의 부정확한 연결도, 원문보다 넓은 표면 표현만으로 별도 중요 오류를 추가하지 않도록 조정했다. 해당 행들의 다른 중요 오류는 유지했다.

| 실제 비교 구성 | 고정 실행 정체성과 차이 |
|---|---|
| Hy-MT2 7B | Q8_0 모델 SHA `58b3ad55dd6f6fa08c695cddc34fb5f8f708a844f78ae10508071914b0ed67c0`; 등록 manifest SHA `cb3031d87372fbb1b0c3dcaf2071064bb59a8a21bd96480ef2d5a66752ae52bd`. 기존 등록 contextual 프로필과 조건부 용어 목록을 사용했다. |
| TranslateGemma 12B | Q4_K_M 모델 SHA `b7aac4b4be7ab0c49b6556c29c4467e74313df7f1e95d9f9676bb2adf0afa528`; 설치 manifest SHA `c1bea177a99c4ff788a11551d517727bd3febc7860630d38d0cd827aeff243e7`. 원래 GGUF 번역 템플릿의 source-only 입력, temperature 0, 원문 전체 생성, 사전·번역 메모리·후처리 없이 실행했다. |

이는 모델 가중치만 바꾼 통제 실험이 아니라 **실제로 사용할 두 구성의 비교**다. 양자화·프롬프트·용어 목록·생성 설정이 다르므로 결과를 모델 크기나 가중치 자체의 우열로 일반화할 수 없다. 두 자료의 이웃 문맥 필드는 비어 있다. 7B의 contextual 프로필을 사용했다는 사실을 이번 자료에서 추가 이웃 문맥의 효과를 입증한 것으로 해석하지 않는다.

실제 모델 실행과 도구 검사를 구분했다. 7B·12B는 각각 개발 18개와 공개 원문 6개를 모두 생성했고 각 실행의 소유 native 프로세스 종료가 summary에 기록됐다. 입력·출력·실행 코드 스냅샷·모델 정체성을 보존했다. 12B의 최초 smoke는 실행기의 EOG 검사 불일치로 번역 전 실패했고, 진단과 v2 smoke 성공을 별도 경로에 남겼다. 그 실패를 삭제하거나 정상 완료로 바꾸지 않았다. [실행 도구 안내](../../scripts/model-comparison/README.md)와 [메모리·품질 우선 실행 원칙](LINGUISTIC_OPTIMIZATION_PROTOCOL.md)을 따른다.

| 검증 | 확인한 범위 | 확인하지 않은 것 |
|---|---|---|
| [평가 도구 53개 검사](../../.training/verifications/review-v3-producer-contracts-20260910.json) | 합성 자료의 준비·검수·해시·완전성·v3 producer 완료 증거 계약 | 실제 번역 품질, 모델 추론, 운영 앱 |
| [Hy30 v3 18개 검사와 실행 준비](../../.training/verifications/hymt30-v3-ready-20260910.json) | 모델 없이 정책·프로토콜·실패 주입과 정리 검사, 고정 코드 준비 | Hy30 실제 모델 로드·번역·품질 비교 |
| [최종 v3 실행기 54개 검사](../../.training/verifications/local-v3-final-tests-20260910.json) | TG27 36개·Hy30 18개의 합성 검사. 마지막 실측 예산 위반·정리 실패 전파 포함 | 실제 모델 성능이나 번역 품질 인증 |
| [TG27 v4 시간 한도 검사](../../.training/verifications/translategemma-v4-time-budget-20260911.json), [Hy30 v4 설정 검증](../../.training/verifications/hymt30-v4-topk-metadata-validation-20260910.json) | 각각 37개·20개의 합성 검사. 이전 v3 파일과 실패 기록 보존 | 대형 모델의 금융 의미 품질 |
| [v4 검수 도구 검사](../../.training/verifications/review-v4-contracts-20260911.json) | 합성 64개·CLI help 4개와 실제 Hy30 v4 기능 입력 2건의 원시 응답·메모리 파일 검증 | 개발 자료 의미 검수, 전체 native 서버 독립 감사 |
| 실제 7B·12B 4개 실행 | 두 구성의 18개 개발 입력과 6개 공개 원문 생성, 소유 프로세스 종료 | 자동 검사만으로 의미 정확도 인증 |
| 도우미 익명 검수·집계 | 개발 36판단·공개 원문 12판단, 사전 조정 후 키 공개 | 사람 검수, 독립 최종 시험, 전체 품질 하방 보장 |

완료된 검수의 집계 명령은 다음과 같다. 출력 파일은 불변이므로 재검증할 때는 `--output`을 새로운 경로로 바꾼다. 준비 패킷이나 기존 검수를 덮어쓰지 않는다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/summarize_linguistic_reviews.py --prepared .training/comparisons/linguistic-dev-20260910/prepared-hy7-tg12-v1 --reviews .training/comparisons/linguistic-dev-20260910/review-hy7-tg12-a-v1.jsonl .training/comparisons/linguistic-dev-20260910/review-hy7-tg12-b-v2.jsonl .training/comparisons/linguistic-dev-20260910/review-hy7-tg12-root-v2.jsonl --output .training/comparisons/linguistic-dev-20260910/assistant-review-hy7-tg12-v1.json
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/summarize_reading_reviews.py --prepared .training/comparisons/real-reading-check-20260910/prepared-hy7-tg12-v1 --reviews .training/comparisons/real-reading-check-20260910/review-hy7-tg12-c-v2.jsonl --output .training/comparisons/real-reading-check-20260910/assistant-review-hy7-tg12-v1.json
```

주요 근거 파일의 SHA-256은 아래와 같다. 집계 JSON 안에는 각 검수·원문·패킷·생산자 summary·predictions의 해시와 원시 실행 metadata도 포함된다. Hy7 생산자에는 완료 시점 predictions 해시가 없으므로 출력 바이트의 결속은 준비 시점부터이며, 준비 이전 변조까지 독립 검증했다고 주장하지 않는다.

| 근거 | SHA-256 |
|---|---|
| 개발 18행 집계 | `58d6263620fb5000e187370341cac160ecafe734e14432a35a866b8f309315f2` |
| 공개 원문 6문단 집계 | `0e88a6312d1ef220bf3ce59bbd6fce05187e62c9129d3f6d8b8357937d830786` |
| 키 공개 전 검수 조정 | `ec5837d1ecd22f605514ba2b175c09d9397b5cec5bbfd8f22f12b6c985d35e8c` |
| 개발 입력 JSONL | `b8a2b91802e36d96654631a9965566f774e050ea4139ca7270d9f84d2a916848` |
| 공개 원문 입력 JSONL | `becd55204f1533adf68c0512638f17db676d4848a175367c841ed71fca34f8d0` |

대형 후보의 상태는 단계별 확인 시점을 구분한다. 27B smoke는 **2026-09-10 13:03:42 UTC에 일반 기능 입력 2건으로 완료**됐다. 소유 native·실행 wrapper 종료 후 Hy30을 단독 실행했다. 초기 11GiB 계획은 27B에서 관측한 낮은 시스템 물리 여유를 고려해 8GiB로 낮췄다. Hy30 v3의 설정 보고값 검증 실패를 보존하고, native 소스에 근거한 v4로 새 기능 시험 2건을 완료했다. 후속 결과는 완료된 실행의 summary·품질 검수·해시를 확인한 뒤 이 표에 추가한다. 기능 시험이나 설치 완료를 금융 품질 비교 완료로 바꾸지 않는다.

| 후보 | 확인된 상태 | 실제 번역·품질 결과 | 후속 근거 |
|---|---|---|---|
| TranslateGemma 27B Q4_K_M | 소유 native 작업 세트 예산 8GiB의 실제 smoke 2/2건 완료. 무결성 확인·소유 자식 종료, 메모리 guard·감시·종료 오류 없음 | 일반 기능 입력 2건의 생성만 확인. 금융 개발 18행·공개 원문 6문단 품질 비교 미실행 | [완료 summary](../../.training/comparisons/linguistic-dev-20260910/translategemma-27b-smoke-paging-v3/summary.json), SHA `e005f61940a282a54f262f3f73963313dd29fcb871babe8789186cb3b8b5fb32` |
| Hy-MT2 30B-A3B Q4_K_M | 8GiB 예산 v3 smoke는 첫 응답의 `actual_sampling_mismatch_top_k`로 실패해 보존했다. v4 smoke 2/2건 및 별도 개발 18/18건의 출력 무결성·소유 자식 종료를 확인했다 | 개발 자료 18행 생성 완료. 금융 의미 검수와 공개 원문 6문단 실행은 아직 미완료 | [v3 실패](../../.training/comparisons/linguistic-dev-20260910/hymt30-smoke-paging-v3/summary.json), [v4 기능 완료](../../.training/comparisons/linguistic-dev-20260910/hymt30-smoke-paging-v4/summary.json), [개발 완료](../../.training/comparisons/linguistic-dev-20260910/hymt30-paging-v4/summary.json), 개발 summary SHA `220bd71b1370e7de3b432627d87885cb079463bb5b4572dfb0cad59173ffb1aa` |

27B 완료 summary의 `count=recordedCount=expectedCount=2`, `integrityVerified=true`, `childProcessStopped=true`를 확인했다. 소유 자식의 최대 peak 작업 세트는 8GiB 예산 안에 있었지만, summary의 시스템 물리 여유 최솟값은 약 80.7MiB였다. 5초 간격으로 보존한 원시 샘플에는 이 최솟값이 없어 정확한 시각·지속시간은 확정할 수 없다. 512MiB 미만이 3초 지속될 때의 중단 조건은 발동하지 않았다. 따라서 이번 완료를 시스템 전체 메모리 안정성이나 장문 금융 번역 품질의 인증으로 해석하지 않는다.

8GiB 예산은 소유 native 프로세스의 상주 작업 세트에 대한 관측 한도이며 시스템 전체 RAM·파일 캐시·commit의 상한이 아니다. 요청 API 상한에는 64MiB 여유를 따로 두고, 시작 시 물리 메모리 여유 및 commit 조건과 실행 중 메모리 감시를 적용한다. 한 번에 무거운 모델 하나만 실행하며 완료·실패한 소유 모델 프로세스를 종료한다. 이미 만든 가중치·출력·실패 기록·개인 자료를 메모리 확보를 이유로 삭제하지 않는다.

현재의 실질적 결론은 12B로 교체할 품질 근거가 부족하다는 것이다. 7B를 그대로 유지하면서 남은 오류와 대형 후보의 실제 결과를 확인한다. 이 작은 개발 비교에서 오류가 적었다는 사실만으로 모든 금융 자료에 대한 우수성, 용어 90% 목표의 최종 달성, 새 학습 자료의 자동 승인을 선언하지 않는다.
