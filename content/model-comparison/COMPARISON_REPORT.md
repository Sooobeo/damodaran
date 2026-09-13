# 번역 모델 실제 비교 — 2026-09-09

## 판단

**이번에 확인한 TranslateGemma 4B Q4_K_M을 현재 앱의 대체 모델로 추천하지 않는다.** 문장이 자연스러워진 사례는 있지만 금융 개념·부정·단위·수식 오류가 남았고, 이 PC의 CPU에서는 기존 학습 모델보다 느렸다. 기존 Marian FP32도 핵심 절 누락과 용어 오역이 있어 그대로 전문 번역 품질로 간주할 수 없다. 이후 개선의 우선순위는 실제 금융 설명문에 가까운 검수 문장·문단과 새 평가 자료를 확보하고, 그 자료로 추가 학습 또는 다른 기반 모델을 비교하는 것이다.

이 판단은 **12개 도우미 작성 미검수 탐색 자료**에 대한 결과다. TranslateGemma의 원본 정밀도 모델이나 다른 크기 모델 전체에 대한 결론이 아니다. 모델을 학습하거나 앱 제공자를 바꾸지 않았다.

## 실제 측정

금융 10개와 일반 2개이며, 긴 금융 문단 6개는 57~68개 영어 단어와 각 3개 문장으로 구성했다. [자료 출처와 검사 기준](README.md)에 명시한 신규 사례만 사용했다. 기존 train/dev/test 원문·예측은 재사용하지 않았다. 모델에는 source만 제공했고 한국어 참조·용어 정답·검토 포인트는 전달하지 않았다.

| 항목 | Argos 원시 출력 | 기존 finance-v3 Marian FP32 | TranslateGemma 4B Q4_K_M |
|---|---:|---:|---:|
| 실제 완료 | 12/12 | 12/12 | 12/12 |
| 실행 장치 | CPU | CPU | CPU |
| 문단 생성 시간 중간값 | 0.422초 | 4.281초 | 21.125초 |
| 12개 생성 합계 | 5.172초 | 49.186초 | 251.828초 |
| 세션당 모델 로드 | 6.844초 | 32.672초 | 5.109초 |
| 지정 금융 용어 표기 적중 | 6/24 | 13/24 | 11/24 |
| 금융 chrF | 20.380 | 39.614 | 33.902 |
| 전체 chrF | 21.043 | 38.635 | 33.711 |
| 숫자 토큰 일치 | 9/12 | 12/12 | 12/12 |
| 빈 결과 / 출력 상한 도달 | 0 / 0 | 0 / 0 | 0 / 0 |

시간은 한 번 실행한 관측값이며 반복 벤치마크의 분포가 아니다. 각 모델을 한 번 로드해 12개를 순서대로 번역했고 모델 로드·무결성 검사는 생성 시간에서 제외했다. 후보의 시간은 로컬 토큰화 요청과 completion 왕복을 포함한다. 모델별 라이브러리 초기화·캐시·PC 부하가 달라 로드 시간의 우열을 모델 자체의 성능으로 일반화하지 않는다.

기준모델은 각 CPU/INT8 beam 4와 CPU/FP32 beam 4, 후보는 CPU/Q4_K_M greedy다. Argos는 기존 문장 분할과 문장별 상한 1,024토큰, Marian은 원문 입력 192/생성 256토큰, 후보는 번역 템플릿 포함 문맥 2,048/생성 512토큰을 사용했다. 입력 잘림·출력 상한 도달은 없었다. **같은 모델의 순수 미세조정 효과 측정이 아니라 실행 방식까지 포함한 후보 비교**다.

모든 모델에서 용어 후처리·검수 메모리를 끄고 원시 출력을 비교했다. Argos의 앱용 숫자·수식 보호도 적용하지 않았으므로 이 출력이 현재 앱 처리 후 결과와 항상 같다는 뜻은 아니다.

## 수치만으로 잡히지 않은 오류

모델 이름을 가리고 문단마다 A/B/C 대응을 바꾼 뒤, 자료 작성과 별도의 도우미가 원문·핵심 확인 포인트에 따라 36개 출력을 대조했다. 0은 핵심 의미 보존, 1은 국소적 용어·표현 문제, 2는 주요 누락·개념/관계/조건/부정/수식 오류, 3은 무관한 생성 또는 대부분 이용 불가다. **독립 전문가 검수나 정답률이 아닌 도우미의 탐색적 판단**이다.

| 도우미 대조 분류 | Argos | Marian FP32 | TranslateGemma Q4 |
|---|---:|---:|---:|
| 0: 핵심 의미 보존 | 0 | 0 | 4 |
| 1: 국소 문제 | 1 | 5 | 0 |
| 2: 주요 오류 | 9 | 7 | 8 |
| 3: 대부분 이용 불가 | 2 | 0 | 0 |

TranslateGemma가 명확하게 보존한 문단도 있지만 오류가 있는 문단에서는 중요한 정보가 바뀌었다. 이 작은 표본의 분류 수로 모델 전체의 우열이나 통계적 유의성을 주장하지 않는다. [모델 이름을 숨긴 검토와 원문 근거](../../.training/comparisons/finance-probe-20260909/blind-review-result.json)는 개별 오류를 확인하는 용도다.

| 모델·사례 | 실제 출력 | 원문과의 차이 |
|---|---|---|
| Argos f05 | 운전자본 설명에 `4AppsApk`와 무료 APK 다운로드 내용 생성 | 원문에 없는 무관한 내용을 생성 |
| Argos f10 | bond를 `노예`로 번역 | 채권 개념을 다른 의미로 변경 |
| Marian f07 | 공장 확장 선택권의 가치만 번역 | 시장 상황과 무관하게 투자할 의무가 아니라는 뒤의 부정절 전체 누락 |
| TranslateGemma f03 | cost of equity를 `자본 비용`으로 번역 | 자기자본비용과 전체 자본비용의 구분을 잃음 |
| TranslateGemma f04 | `TV = CF_next / (r - g)`를 `TV = CF_다음 / (r - g)`으로 번역 | 수식 변수 이름을 변경 |
| TranslateGemma f05 | `현금 증가를 의미하지 않는다고 가정해서는 안 됩니다` | 현금 증가를 의미한다고 가정하지 말라는 원문의 부정을 반대로 전달 |
| TranslateGemma f08 | `-20`과 `5`에 각각 `억 원` 추가 | 원문에 없는 통화·규모 단위를 임의 추가. 숫자 토큰 검사에서는 통과 |
| TranslateGemma f10 | modified duration을 `수정된 만기`로 번역 | 금리 민감도 지표와 만기를 혼동 |

반면 TranslateGemma f07은 Marian이 빠뜨린 투자 의무 부정절을 보존했고, f06의 차입·주주수익 위험 설명과 g12의 도서관 이용 조건은 비교적 자연스럽게 전달했다. 장점과 실패를 함께 보존하며 좋은 사례만 골라 전체 품질로 주장하지 않는다.

숫자 불일치도 모두 오역은 아니다. Argos f03/f10은 영어 `one`을 숫자 `1`로 표현한 영향이 포함된다. 반대로 숫자 토큰이 같아도 단위 추가·수식 변경·부정 반전은 통과할 수 있다. 용어 표기 적중은 참조에 있는 허용 표기의 문자열 포함 여부이며 의미 정확도 비율이 아니다. 한국어 단일 참조·띄어쓰기·문체에 따라 chrF/BLEU도 달라진다. 일반 사례 2개로 일반 번역 능력을 보증하지 않는다.

## Windows·모델 실행 확인

PC는 Intel Arc 140V, RAM 약 31.5GB다. 시작 당시 남은 물리 메모리는 약 5.5GB여서 공개 제3자 Q4_K_M GGUF(2,489,909,760바이트)를 먼저 비교했다. [Google 기반 모델](https://huggingface.co/google/translategemma-4b-it)과 [mradermacher 변환본](https://huggingface.co/mradermacher/translategemma-4b-it-GGUF/tree/35a7486e128b19642cdc72d7b91b21ba388aaf42)을 구분한다. 변환본의 정확한 기반 revision은 제공자가 확인해 주지 않아 조회한 upstream 최신 revision과 같다고 가정하지 않는다. 이용 조건은 [Gemma](https://ai.google.dev/gemma/terms)다.

[llama.cpp b10874 공식 Windows 배포](https://github.com/ggml-org/llama.cpp/releases/tag/b10874)가 Intel GPU를 인식했다. 그러나 첫 번역의 Vulkan 연산은 `ErrorDeviceLost`로 실패했다. GPU 역전파·LoRA 가능성을 확인한 결과가 아니며, 다른 PyTorch XPU 경로까지 실패한다고 일반화하지 않는다. 실패 뒤 동일 GGUF를 CPU로 명시 실행해 12개를 완료했다.

첫 서버 시작에서는 TranslateGemma의 언어 필드가 있는 chat 템플릿을 llama.cpp가 자동 해석하지 못했다. GGUF에 포함된 원래 템플릿을 로컬 Jinja로 렌더링하고, 정확한 BOS를 확인한 토큰 배열을 `/completion`에 전달했다. 번역 프롬프트나 가중치를 품질 점수에 맞춰 조정하지 않았다. 템플릿 시작 실패·Vulkan 실패·CPU 성공을 별도 로그로 남겼다. 후보 서버는 오프라인 옵션·loopback 바인딩·웹 UI 및 도구 비활성화로 실행하고 종료했다.

## 검증과 보관

- 새 비교용 CLI 4개 Python 구문 검사 및 실제 세 모델 추론 완료.
- 36개 출력의 고유 ID·원문 SHA 대응, 결과 파일 해시·빈 출력·상한 상태 확인.
- 후보 모델과 Windows 배포 ZIP을 게시된 SHA-256에 대조.
- 모델·런타임·템플릿·실행 스크립트·입력·출력 해시와 생성 설정 보존.
- 모델 이름과 A/B/C의 대응을 문단마다 무작위로 바꾼 별도 도우미 의미 대조. 전문가·사용자 검수로 표시하지 않음.
- 운영 DB·원문·메모·검수 번역·기존 학습 가중치·앱 제공자 변경 없음. 앱 테스트나 HTML/PDF 저장·캐시 검증을 새로 수행한 것으로 보고하지 않음.

[실행 명령](../../scripts/model-comparison/README.md), [원시 결과 폴더](../../.training/comparisons/finance-probe-20260909/), [자동 지표](../../.training/comparisons/finance-probe-20260909/comparison-metrics.json), [설치 manifest](../../.training/comparisons/translategemma-4b-q4/installation-manifest.json)를 함께 보관한다. `.training/comparisons/`는 SQLite 백업에 포함되지 않는다.

입력 SHA-256: `7ccafce31068b98f5f4bcc32ead765da74e5947fbc730c39cc4e6bb19e35a76d`.

후보 GGUF SHA-256: `81200d03e843d2ec1ece6eeafe7d13cb6e5211e1fcd336ade55790b683a08330`.

## 전체 원문과 실제 출력

아래는 모델 출력 그대로이며 편집·교정하지 않았다. 무관한 내용이나 잘못된 금융 표현도 관측 기록으로 보존한다.


### probe-20260909-f01 — 독립 작성 예시 — 가치와 가격

**영어 원문**

An analyst estimates that a share is worth $48, while its market price is $42. The difference does not prove that the market is wrong, because her estimate depends on assumptions about future cash flows and risk. Before treating the gap as an opportunity, she should examine which assumptions would have to change for it to disappear.

**도우미 작성 미검수 참조**

한 분석가는 주식의 가치를 주당 $48로 추정하지만 시장가격은 $42다. 이 차이가 시장이 틀렸다는 증거는 아니다. 분석가의 추정치가 미래 현금흐름과 위험에 관한 가정에 의존하기 때문이다. 이 차이를 기회로 받아들이기 전에, 어떤 가정이 바뀌면 그 차이가 사라지는지 검토해야 한다.


**Argos raw** (0.422초)

분석가 추정은 $ 48 가치가있는 것으로 추정되며 시장 가격은 $ 42입니다. 차이는 시장이 잘못되었는지 증명하지 않습니다. 그녀의 견적은 미래의 현금 흐름과 위험에 대한 가정에 따라 달라집니다. 기회로 격차를 대우하기 전에, 그녀는 그것이 사라질 것을 위해 그것을 변화해야 한다는 것을 검사해야 합니다.


**Marian finance-v3 FP32** (4.172초)

분석가는 주가가 48달러인 반면 시장가격은 42달러라고 추정한다. 그 차이는 추정치가 미래 현금흐름과 위험에 대한 가정에 달려 있기 때문에 시장이 잘못되었다는 것을 증명하지 못한다. 격차를 기회로 취급하기 전에 어떤 가정이 사라지기 위해 변화해야 하는지 조사해야 한다.


**TranslateGemma 4B Q4_K_M CPU** (23.844초)

한 분석가는 해당 주식의 가치를 48달러로 추정했지만, 시장 가격은 42달러입니다. 이러한 차이는 시장이 틀렸다는 것을 증명하지 않으며, 이는 분석가의 추정치가 미래 현금 흐름과 위험에 대한 가정에 의존하기 때문입니다. 이 차이를 기회로 활용하기 전에, 이 차이가 사라지기 위해서는 어떤 가정이 변경되어야 하는지 확인해야 합니다.


### probe-20260909-f02 — 독립 작성 예시 — 성장과 재투자 수익률

**영어 원문**

A business can report faster earnings growth without becoming more valuable. If its new investments earn a return on invested capital of 12% while the cost of capital is 9%, growth can create value. However, when additional investment earns less than that cost, retaining more earnings may increase the size of the business while reducing the value available to its owners.

**도우미 작성 미검수 참조**

기업은 이익 성장 속도가 빨라져도 가치가 높아지지 않을 수 있다. 신규 투자에서 얻는 투하자본수익률이 12%이고 자본비용이 9%라면 성장은 가치를 창출할 수 있다. 그러나 추가 투자의 수익률이 그 자본비용보다 낮으면 더 많은 이익을 유보하는 것이 기업의 규모를 키우는 동시에 소유주에게 귀속되는 가치를 줄일 수 있다.


**Argos raw** (0.375초)

사업은 더 가치있지 않고 더 빠른 수입 성장을 보고할 수 있습니다. 새로운 투자가가 12%의 투자 자본을 얻은 경우 자본의 9%, 성장은 가치를 창출할 수 있습니다. 그러나, 추가 투자가 그 비용보다 적은을 적립 할 때, 더 많은 수입을 유지하면서 사업의 크기를 증가시킬 수 있습니다.


**Marian finance-v3 FP32** (4.75초)

기업은 가치를 높이지 않으면서 더 빠른 수익 성장을 기록할 수 있다. 신규 투자가 투자 자본수익률이 12%인 반면 자본비용이 9%라면 성장은 가치를 창출할 수 있다. 그러나 추가투자가 그 비용보다 적으면 더 많은 수익을 유지하면 사업 규모가 커지면서 소유주가 사용할 수 있는 가치는 줄어들 수 있다.


**TranslateGemma 4B Q4_K_M CPU** (30.766초)

기업은 더 많은 가치를 창출하지 않고도 더 빠른 수익 성장을 보고할 수 있습니다. 새로운 투자가 투자 자본에 대해 12%의 수익을 올리는 반면, 자본 비용이 9%인 경우, 성장은 가치를 창출할 수 있습니다. 그러나 추가 투자가 그 비용보다 낮은 수익을 낼 경우, 기업의 규모는 커질 수 있지만, 소유주에게 돌아가는 가치는 감소할 수 있습니다.


### probe-20260909-f03 — 독립 작성 예시 — 현금흐름과 청구권의 일치

**영어 원문**

Free cash flow to the firm is measured before cash payments to lenders are deducted, so it belongs to both lenders and shareholders. Discounting it at the cost of equity would mix a cash flow for all capital providers with a required return for only one group. To estimate equity value from operating asset value, account separately for debt and nonoperating assets.

**도우미 작성 미검수 참조**

기업잉여현금흐름은 채권자에게 지급하는 현금을 차감하기 전에 측정하므로 채권자와 주주 모두에게 귀속된다. 이를 자기자본비용으로 할인하면 모든 자본 제공자에게 귀속되는 현금흐름에 그중 한 집단에만 해당하는 요구수익률을 섞어 적용하게 된다. 영업자산 가치에서 자기자본가치를 추정하려면 부채와 비영업자산을 별도로 반영해야 한다.


**Argos raw** (0.422초)

기업에 무료 현금 흐름은 대출에 현금 지불 전에 측정됩니다, 그래서 그것은 대출과 주주 모두에 속한다. 주식의 비용에 그것을 할인하는 것은 1개의 그룹을 위한 필요한 반환을 가진 모든 자본 공급자를 위한 현금 교류를 섞을 것입니다. 운영 자산 가치에서 주식 가치를 추정하기 위해, 부채 및 비동작 자산에 대해 별도로 계정.


**Marian finance-v3 FP32** (5.797초)

대출기관에 대한 현금 지급액이 차감되기 전에 기업에 대한 현금흐름이 측정되므로 대출기관과 주주 모두에게 귀속된다. 주식비용으로 현금흐름을 할인하면 모든 자본 제공자가 현금흐름을 혼합하여 한 그룹에만 필요한 수익률을 얻게 된다. 자산가치에서 자기자본가치를 추정하려면 부채와 영업외자산을 따로 계산한다.


**TranslateGemma 4B Q4_K_M CPU** (28.359초)

기업에 유입되는 순현금은 대출 기관에 대한 현금 지급액을 차감하기 전의 수치를 기준으로 측정되므로, 이는 대출 기관과 주주 모두에게 속합니다. 자본 비용으로 할인하는 것은 모든 자본 제공자에게 속하는 현금 흐름과 단일 그룹에만 적용되는 필요한 수익을 혼합하는 것입니다. 운영 자산의 가치로부터 자본 가치를 추정할 때, 부채와 비운영 자산을 별도로 고려해야 합니다.


### probe-20260909-f04 — 독립 작성 예시 — 계속가치 공식의 조건

**영어 원문**

In a stable growth model, terminal value is written as TV = CF_next / (r - g), where CF_next is the cash flow in the first year after the explicit forecast period. This expression requires r to exceed g. A very high estimate produced by bringing g close to r should prompt a review of the growth assumption, rather than confidence in the apparent precision of the spreadsheet.

**도우미 작성 미검수 참조**

안정성장 모형에서 계속가치는 TV = CF_next / (r - g)로 나타내며, CF_next는 명시적 예측 기간이 끝난 다음 첫해의 현금흐름이다. 이 식을 적용하려면 r이 g보다 커야 한다. g를 r에 가깝게 놓아 추정치가 매우 높아졌다면, 스프레드시트가 겉으로 보여 주는 정밀성을 신뢰하기보다 성장률 가정을 재검토해야 한다.


**Argos raw** (0.531초)

안정적인 성장 모델에서, 터미널 값은 TV = CF next / (r - g)로 작성되며 CF next는 명시적인 예측 기간 후 첫 해에 현금 흐름입니다. 이 표현은 g를 초과하는 r을 요구합니다. r에 가까운 g를 가져오기에 의해 생성 된 매우 높은 견적은 스프레드 시트의 명백한 정밀도에서 신뢰보다 오히려 성장 가정의 검토를 프롬프트해야합니다.


**Marian finance-v3 FP32** (5.687초)

안정적인 성장 모델에서 종단가치는 TV = CF_next / (r - g)로 작성되며, CF_next는 명시적 예측 기간 후 첫해의 현금흐름이다. 이 표현식은 r이 g를 초과해야 한다. g를 r에 가깝게 함으로써 산출되는 매우 높은 추정치는 스프레드시트의 겉보기 정밀도에 대한 확신이 아니라 성장 가정의 검토를 촉구해야 한다.


**TranslateGemma 4B Q4_K_M CPU** (26.266초)

안정적인 성장 모델에서, 최종 가치는 TV = CF_다음 / (r - g)로 표현되며, 여기서 CF_다음은 명시적인 예측 기간 이후 첫 번째 연도의 현금 흐름을 의미합니다. 이 식은 r이 g보다 커야 합니다. g를 r에 매우 가까운 값으로 추정하는 것은 스프레드시트의 명확한 정확성에 대한 자신감을 갖는 것보다, 성장 가정에 대한 검토를 촉구해야 합니다.


### probe-20260909-f05 — 독립 작성 예시 — 운전자본과 현금 회수

**영어 원문**

Sales rose by 15%, but customers took longer to pay and the company built up inventory before opening new stores. These changes tied up cash in working capital even though reported operating income increased. Unless suppliers finance the expansion through a comparable increase in accounts payable, the analyst should not assume that the growth in accounting profit represents an equal increase in cash available for distribution.

**도우미 작성 미검수 참조**

매출은 15% 늘었지만 고객의 대금 지급이 늦어졌고 회사는 신규 매장을 열기 전에 재고를 쌓았다. 이러한 변화로 보고된 영업이익이 증가했음에도 운전자본에 현금이 묶였다. 매입채무가 그에 상응하는 규모로 늘어 공급업체가 확장에 필요한 자금을 제공하는 경우가 아니라면, 분석가는 회계상 이익의 증가가 분배 가능한 현금의 동일한 증가를 뜻한다고 가정해서는 안 된다.


**Argos raw** (0.547초)

판매는 15%에 의하여, 그러나 고객은 더 길게 지불하고 새로운 상점을 열기 전에 회사를 건설했습니다. 이 변경은 작업 자본에 현금을 묶어도 작동 소득이 증가했다. 이 웹 사이트는 애플 리케이션에 전념. 우리는 정품 앱과 게임을 제공 할 목적으로이 사이트를 만들었습니다. 4AppsApk 최고의 안드로이드 애플 리케이션을위한 무료 APK 파일 다운로드 서비스, 계략.


**Marian finance-v3 FP32** (5.015초)

매출은 15% 증가했지만 고객이 지불하는 데 시간이 더 걸렸고 회사는 신규 매장을 열기 전에 재고를 구축했다. 영업이익이 증가했다고 보고해도 이러한 변화는 영업자본에 현금을 연결했다. 지급 계정의 비슷한 증가를 통해 공급 업체가 확장에 자금을 조달하지 않는 한 분석가는 회계 이익의 증가가 분배 가능한 현금의 동일한 증가를 나타낸다고 가정해서는 안 된다.


**TranslateGemma 4B Q4_K_M CPU** (30.093초)

매출이 15% 증가했지만, 고객들이 결제하는 데 더 오래 걸렸고, 회사는 새로운 매장을 개장하기 전에 재고를 축적했습니다. 이러한 변화로 인해 영업이익이 증가했음에도 불구하고 현금 유동 자금이 부족해졌습니다. 공급업체가 미지급금 증가와 유사한 방식으로 사업 확장에 자금을 지원하지 않는 한, 분석가는 회계 이익 증가가 실제로 사용할 수 있는 현금 증가를 의미하지 않는다고 가정해서는 안 됩니다.


### probe-20260909-f06 — 독립 작성 예시 — 레버리지와 주주 위험

**영어 원문**

Borrowing can lower the initial amount of equity needed to buy an asset, but it does not remove the uncertainty in the asset's cash flows. Because interest must be paid before shareholders receive the remainder, debt can make their returns more sensitive to weak operating results. An analyst comparing equity betas across companies should therefore consider differences in financial leverage as well as differences in their underlying businesses.

**도우미 작성 미검수 참조**

차입은 자산을 매입하는 데 처음 필요한 자기자본을 줄일 수 있지만, 그 자산의 현금흐름에 존재하는 불확실성을 없애지는 않는다. 주주가 잔여분을 받기 전에 이자를 지급해야 하므로 부채는 주주 수익률이 부진한 영업실적에 더 민감하게 반응하도록 만들 수 있다. 따라서 기업 간 자기자본 베타를 비교하는 분석가는 기초 사업의 차이뿐 아니라 재무 레버리지의 차이도 고려해야 한다.


**Argos raw** (0.672초)

Borrowing은 자산을 구입하는 데 필요한 주식의 초기 금액을 낮출 수 있지만 자산의 현금 흐름에 불확실성을 제거하지 않습니다. 주주가 계속되기 전에 이자율이 지급되기 때문에, 부채는 약한 운영 결과에 더 민감하게 수익을 올릴 수 있습니다. 기업 전체에서 주식을 비교하는 분석가들은 금융 레버리지의 차이를 고려해야 하며 그 결과 기업의 차이를 고려해야 합니다.


**Marian finance-v3 FP32** (5.938초)

차입은 자산 매입에 필요한 초기 자본금을 낮출 수 있지만 자산의 현금흐름의 불확실성을 제거하지는 않는다. 주주가 나머지를 받기 전에 이자를 지급해야 하기 때문에 부채는 부실한 영업 실적에 대한 수익률을 더욱 민감하게 만들 수 있다. 따라서 기업 간 주식 베타를 비교하는 분석가는 재무 레버리지의 차이와 기본 사업의 차이를 고려해야 한다.


**TranslateGemma 4B Q4_K_M CPU** (29.969초)

대출은 자산 구매에 필요한 초기 자기 자본의 금액을 줄일 수 있지만, 자산의 현금 흐름에 대한 불확실성을 제거하지는 않습니다. 주주들이 잔여 이익을 받기 전에 이자를 지급해야 하기 때문에, 부채는 회사의 수익성이 약화될 경우 주주의 수익에 더 큰 영향을 미칠 수 있습니다. 따라서 기업 간 자기 자본 베타를 비교하는 분석가는 재무적 레버리지뿐만 아니라, 각 회사의 근본적인 사업 측면의 차이도 고려해야 합니다.


### probe-20260909-f07 — 독립 작성 예시 — 실물옵션과 의무의 구분

**영어 원문**

The option to expand a factory has value because management may wait for stronger demand; it is not an obligation to invest regardless of market conditions.

**도우미 작성 미검수 참조**

공장 확장 옵션은 경영진이 수요가 더 강해질 때까지 기다릴 수 있다는 점에서 가치가 있다. 시장 상황과 관계없이 투자해야 하는 의무는 아니다.


**Argos raw** (0.203초)

공장 확장 옵션은 관리가 더 강한 수요를 기다릴 수 있기 때문에 가치가 있습니다. 시장 조건에 관계없이 투자 할 의무가 없습니다.


**Marian finance-v3 FP32** (1.671초)

경영진이 더 강한 수요를 기다릴 수 있기 때문에 공장을 확장하는 옵션은 가치가 있다.


**TranslateGemma 4B Q4_K_M CPU** (11.781초)

공장을 확장하는 옵션은 가치가 있는 이유는, 경영진이 더 강한 수요를 기다릴 수 있기 때문입니다. 시장 상황에 관계없이 투자를 해야 한다는 의무는 없습니다.


### probe-20260909-f08 — 독립 작성 예시 — 음수 이자보상배율

**영어 원문**

With operating income of -20 and interest expense of 5, interest coverage is -4, not 4; the negative sign indicates that operations did not generate enough earnings to cover interest.

**도우미 작성 미검수 참조**

영업이익이 -20이고 이자비용이 5이면 이자보상배율은 4가 아니라 -4다. 음수 부호는 영업활동에서 이자를 감당할 만큼의 이익을 창출하지 못했음을 나타낸다.


**Argos raw** (0.25초)

-20의 운영 소득과 5의 관심 비용으로, 관심 적용은 -4, 아니 4; 부정적인 표시는 해당 작업을 덮는 충분한 수익을 생성하지 않았다는 것을 나타냅니다.


**Marian finance-v3 FP32** (3.391초)

영업이익이 -20이고 이자비용이 5인 경우 이자보상은 4가 아니라 -4이며, 영업이익이 이자를 충당하기에 충분한 수입을 창출하지 못했다는 표시가 음수이다.


**TranslateGemma 4B Q4_K_M CPU** (13.469초)

운영 이익이 -20억 원이고 이자 비용이 5억 원인 경우, 이자 보수는 -4, 4가 아닌, 즉, 운영 수익이 이자를 감당할 만큼 충분하지 않다는 것을 의미합니다.


### probe-20260909-f09 — 독립 작성 예시 — 마진과 자본수익률

**영어 원문**

A retailer with a thin operating margin can still earn a high return on capital if it generates substantial sales from a relatively small investment in stores and inventory.

**도우미 작성 미검수 참조**

영업이익률이 낮은 소매업체도 매장과 재고에 대한 비교적 적은 투자로 많은 매출을 창출한다면 높은 자본수익률을 올릴 수 있다.


**Argos raw** (0.313초)

얇은 운영 한계를 가진 소매상인은 아직도 상점과 재고에 있는 상대적으로 작은 투자에서 실질적 판매를 생성하는 경우에 자본에 높은 반환을 얻을 수 있습니다.


**Marian finance-v3 FP32** (2.172초)

영업이익이 희박한 소매업체는 상점과 재고에 상대적으로 적은 투자로 상당한 매출을 올리면 여전히 높은 자본수익률을 얻을 수 있다.


**TranslateGemma 4B Q4_K_M CPU** (12.5초)

낮은 운영 마진을 가진 소매업체라도, 매장 및 재고에 대한 비교적 적은 투자를 통해 상당한 매출을 창출한다면 높은 자본 회수율을 달성할 수 있습니다.


### probe-20260909-f10 — 독립 작성 예시 — 듀레이션과 채권 가격

**영어 원문**

For a small increase in yield, a bond with a modified duration of 6 will generally lose more value than one with a duration of 2, other things being equal.

**도우미 작성 미검수 참조**

다른 조건이 같을 때 수익률이 소폭 상승하면 수정듀레이션이 6인 채권은 듀레이션이 2인 채권보다 일반적으로 가치가 더 많이 하락한다.


**Argos raw** (0.437초)

수율의 작은 증가를 위해, 6의 변경된 내구를 가진 노예는 일반적으로 2의 기간, 다른 것 동등한 것의 기간으로 1개 보다는 더 많은 가치를 잃습니다.


**Marian finance-v3 FP32** (2.156초)

수익률이 약간 증가하면 수정된 지속 시간이 6인 채권은 일반적으로 지속 시간이 2인 채권보다 더 많은 가치를 잃고 다른 채권도 같다.


**TranslateGemma 4B Q4_K_M CPU** (11.484초)

수익률이 약간 증가하는 경우, 수정된 만기가 6년인 채권은 일반적으로 만기가 2년인 채권보다 더 큰 손실을 보일 것입니다.


### probe-20260909-g11 — 독립 작성 예시 — 문서 교체와 지시 대상

**영어 원문**

Mina sent Joon a revised guide after he found that the old one skipped a setup step. It contains 12 pages instead of 8, but he should not discard his notes until he has checked whether they refer to instructions that still apply.

**도우미 작성 미검수 참조**

준이 기존 안내서에서 설정 단계 하나가 빠졌다는 것을 발견하자 미나는 수정한 안내서를 준에게 보냈다. 수정본은 8페이지가 아니라 12페이지지만, 준은 자신의 메모가 여전히 유효한 지침을 가리키는지 확인하기 전까지 그 메모를 버려서는 안 된다.


**Argos raw** (0.594초)

Mina는 Joon에게 이전 단계가 설정 단계를 건너 뛰는 것을 발견 한 후 개정 된 가이드를 보냈습니다. 8 대신 12 페이지가 포함되어 있지만 여전히 적용하는 지시를 참조 할 수 있는지 확인 할 때까지 자신의 노트를 쫓아서는 안됩니다.


**Marian finance-v3 FP32** (4.047초)

Mina는 이전 가이드가 설정 단계를 건너 뛰었다는 것을 알게 된 후 수정 된 가이드를 보냈습니다. 여기에는 8 페이지가 아니라 12 페이지가 포함되어 있지만 여전히 적용되는 지침을 참조할지 여부를 확인할 때까지 메모를 버려서는 안됩니다.


**TranslateGemma 4B Q4_K_M CPU** (14.891초)

미나는 주원이 이전 버전에서 설정 단계를 생략했다는 것을 알게 된 후, 수정된 가이드를 보냈습니다. 이 가이드는 8페이지 대신 12페이지로 구성되어 있지만, 주원은 여전히 유효한지 확인한 후에 기존 메모를 버려야 합니다.


### probe-20260909-g12 — 독립 작성 예시 — 대안과 조건부 일정

**영어 원문**

The library will keep the reading room open until 18:30 if enough volunteers are available. Otherwise, visitors can return borrowed books through the outside slot, although they will have to wait until the next morning to collect items reserved for them.

**도우미 작성 미검수 참조**

자원봉사자가 충분히 확보되면 도서관은 열람실을 18:30까지 개방한다. 그렇지 않더라도 방문객은 외부 반납구로 빌린 책을 반납할 수 있지만, 자신이 예약한 자료를 받으려면 다음 날 아침까지 기다려야 한다.


**Argos raw** (0.406초)

도서관은 충분한 자원 봉사자가 사용할 경우 18:30까지 독서실을 개방합니다. 그렇지 않으면, 방문자는 외부 슬롯을 통해 빌린 책을 반환 할 수 있지만, 그들은 다음 아침까지 기다려야하여 항목을 수집합니다.


**Marian finance-v3 FP32** (4.39초)

자원 봉사자 가 충분 한 경우 에는 열람실 을 18:30 까지 열어 둔다. 그렇지 않으면 방문객 은 차용 한 서적 을 외부 슬롯 을 통해 반납 할 수 있다. 하지만 차용 한 서적 을 예약 한 물건 을 수집 하기 위해서는 다음날 아침 까지 기다려야 한다.


**TranslateGemma 4B Q4_K_M CPU** (18.406초)

도서관은 충분한 자원봉사자가 확보될 경우, 독서실을 18:30까지 개방할 것입니다. 그렇지 않은 경우, 방문객은 외부 슬롯을 통해 빌려간 책을 반납할 수 있지만, 예약된 물품을 수령하려면 다음 아침까지 기다려야 합니다.
