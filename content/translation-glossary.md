# 출처가 있는 금융 번역 용어 규칙

[translation-glossary.json](translation-glossary.json)은 번역기가 참고할 영어 표현과 학습실의 한국어 용어 정책을 담는다. 버전 1에는 규칙 47개가 있으며 `phrase` 42개, `exact` 5개다. 기존 [index.ts](index.ts)의 학습용 용어 44개를 수정하거나 대체하지 않는다.

**한국어 표제어와 주의 설명은 학습실이 작성한 것이며 Damodaran의 공식 한국어 번역이 아니다.** 출처는 영어 표현의 존재와 금융 개념의 범위를 확인하는 근거다. 원문 정의를 대량 복사하지 않았으며 모델을 재학습하거나 가중치를 변경하는 자료도 아니다. 검수한 문장쌍을 축적하는 기능과 이 용어 규칙은 구분한다.

## 선정 기준

- 기존 금융용어 44개 가운데 의미를 비교적 분명히 지정할 수 있는 복합구를 우선했다. 재무제표 입문에서 자주 등장하는 보고서·재무상태표 분류·주당순이익 표현도 포함했다.
- 기존 학습용 표제어 37개가 규칙의 기준 표현 또는 별칭과 연결되며 한국어 표제어는 모두 일치한다. 나머지 `Beta`, `Revenue`, `Variance`, `Correlation`, `Regression`과 독립 표기 `EBITDA`, `EV/EBITDA`는 자동 본문 치환으로 확장하지 않았다. 학습용 사전에서는 계속 조회할 수 있다.
- 각 규칙의 `source`는 연결된 공식 영문 자료에서 확인했다. `aliases`는 같은 개념의 단수·복수, 공백·하이픈·철자 변형을 명시한 편집 목록이다. 모든 별칭이 모든 출처에 그대로 등장한다는 뜻은 아니다.
- `return`, `capital`, `primer`, `value`, `income`, `statement` 같은 단일 다의어를 본문 자동 치환 규칙으로 등록하지 않았다. `PV`, `WACC`, `PE`, `PB` 등 독립 약어나 수식 변수도 본문 치환 별칭에서 제외했다.
- `compounding`, `annuity`, `perpetuity`는 단어만 있는 표제·셀을 위한 `exact` 규칙이다. `terminal value`도 기업 DCF의 계속가치와 프로젝트 종료의 잔존가치를 무조건 합치지 않도록 `exact`로 제한했다.
- R01의 확인된 제목 전체는 FT47의 `exact` 규칙으로 기존 편집 제목을 유지한다. 일반적인 `primer` 본문 치환을 허용하는 예외가 아니다.
- 원문이 같은 이름의 지표를 다른 범위로 계산하더라도 이 규칙으로 분모·세금·차입·현금 포함 범위를 바꾸지 않는다. `note`에 구분해야 할 인접 개념을 적었다.

## 출처

확인일은 2026-09-09다. 실제 원문은 오래된 회계 처리나 당시 사례를 포함할 수 있으므로 현재 회계기준의 권위 있는 번역 사전으로 표시하지 않는다.

| 공식 영문 자료 | 확인한 범위 |
|---|---|
| [Glossary](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/glossary.htm) | 기본 금융 표현, 자금조달 비용, 위험·베타, 잉여현금흐름 등 |
| [Financial Ratios and Measures](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/definitions.html) | 지표의 계산 범위와 구분, 비율·시장가치·장부가치 표현 |
| [A Primer on Financial Statements](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/AccPrimer/accstate.htm) | 재무제표·손익·유동 항목·주당 이익 표현 |
| [A Primer on the Time Value of Money](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/PVPrimer/pvprimer.htm) | 현재가치·미래가치·복리·연금 표현 |
| [FCFF Valuation Models](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/lectures/fcff.html) | WACC·FCFF·안정성장·계속가치 문맥 |
| [Thoughts on intrinsic value](https://aswathdamodaran.blogspot.com/2011/06/thoughts-on-intrinsic-value.html) | 저자 블로그의 내재가치 표현 |

각 규칙의 `sources`에는 실제로 해당 규칙의 근거로 사용한 URL만 넣었다. 기본 사전이나 지표 정의 페이지에서 찾지 못한 표현은 PV 입문·FCFF 강의·저자 블로그로 보완했다.

## 데이터 계약과 적용 경계

최상위 구조는 `{ "version": 1, "rules": [...] }`다.

| 필드 | 의미 |
|---|---|
| `id` | 안정적인 규칙 ID. 초기 범위 FT01~FT47 |
| `source` | 공식 영문 자료에서 확인한 기준 표현 |
| `target` | 학습실에서 선택한 한국어 표제어 |
| `aliases` | 같은 개념의 영어 변형. 임의 어간 추출·유사어 확장 대신 명시 목록 사용 |
| `replacements` | 실제 번역에서 관측하고 제한 보정 대상으로 선정한 한국어 출력. 빈 배열은 확인한 후보가 없다는 뜻 |
| `sources` | 개념·영문 표현 확인에 사용한 공식 원문 URL |
| `note` | 적용 범위와 혼동하기 쉬운 개념에 대한 학습실 설명 |
| `mode` | `phrase`: 해당 영문 표현을 포함하는 범위에서 제한 보정. `exact`: 표제·문단·셀 전체가 해당 표현일 때만 적용 |

`phrase`는 영문 구간을 잘라 각각 번역하라는 지시가 아니다. 문장을 통째로 번역한 뒤, 대응하는 원문 범위에 `source`나 명시 별칭이 있고 한국어 출력에 확인한 `replacements` 후보가 있는 경우에만 보정을 검토한다. 목표 표제어가 이미 적절히 들어 있으면 불필요하게 바꾸지 않는다. 대상이 모호하거나 여러 규칙이 충돌하면 원 번역을 유지하고 검토 대상으로 남긴다.

긴 복합구를 먼저 판별해야 한다. 예를 들어 `net present value`를 `present value`보다, `weighted average cost of capital`을 `cost of capital`보다, `non-cash working capital`을 `working capital`보다 먼저 처리한다. 영문 단어 경계·기호·숫자·약어·수식을 유지하며 `replacements`를 문서 전체에서 무조건 찾아 바꾸지 않는다. 조사·어순까지 정확해졌다는 보장으로 표시하지 않는다.

`replacements: []`인 규칙은 관측하지 않은 한국어 오역을 추측해 교정하지 않는다. 완전 일치 용어 표제·셀에는 표제어를 제공할 수 있지만 일반 문장 속 임의의 한국어 표현을 목표 용어로 덮어쓰는 근거는 아니다. 데이터 파일만 추가한 것을 실제 런타임 통합·문장 품질 검증 성공으로 보고하지 않는다.

## 관측한 보정 후보

최초 후보는 연구 검증에서 공유된 실제 R01 재무제표 입문의 Argos 영어→한국어 1.1 출력이다. 이어서 만든 [로컬 관측 자료](../.translation/term-evaluation-baseline.json)는 47개 용어·제목 표본과 실제 R01 3문단, 총 50개 입력의 규칙 적용 전 출력을 담는다. 평가용 문장에는 `synthetic evaluation sentence` 출처 구분이 있으며 Damodaran 원문으로 표시하지 않는다. 이 목록은 모델의 모든 출력이나 공식 번역을 뜻하지 않는다.

관측 시각은 `2026-09-09T08:17:26.879834+00:00`, 모델은 `argos-en_ko-1.1`, 모델 해시는 `b484889c73a79fd7bde433897646c5986e42bf2806297a39413a125a1721c7b7`이다. 해당 관측은 Argos 1.11.0·CTranslate2 4.8.2·SentencePiece 0.2.2·spaCy 3.8.16에서 수행했다. 관측 자료는 재설치 가능한 `.translation/`의 로컬 검증 산출물이며 배포용 원문·번역 seed가 아니다.

25개 규칙에 28개 후보를 등록했다. 규칙당 한 형태를 기본으로 하며 FT01·FT02는 실제 R01에서 여러 형태가 관측되어 함께 기록했다. `금융 성명`은 앞선 실제 R01 관측에서 전달받은 형태이며 이번 50개 표본의 FT01에는 `금융 진술`과 `재무 성명`이 확인된다.

| 규칙 | 원문 표현 | 관측한 한국어 출력 | 학습실 표제어 |
|---|---|---|---|
| FT01 | financial statements | 금융 진술, 금융 성명, 재무 성명 | 재무제표 |
| FT02 | accounting statements | 회계 진술, 회계 성명 | 재무제표 |
| FT03 | balance sheet | 잔액 시트 | 재무상태표 |
| FT04 | income statement | 소득표 | 손익계산서 |
| FT05 | statement of cash flows | 현금 흐름의 성명 | 현금흐름표 |
| FT08 | net present value | net 선물 값 | 순현재가치 |
| FT10 | internal rate of return | 반환의 내부 비율 | 내부수익률 |
| FT13 | cost of equity | 주식의 비용 | 자기자본비용 |
| FT15 | cost of capital | 자본금의 비용 | 자본비용 |
| FT16 | weighted average cost of capital | 자본의 무게가 큰 평균 비용 | 가중평균자본비용 |
| FT17 | default spread | 기본 스프레드 | 부도위험 스프레드 |
| FT18 | interest coverage ratio | 관심 적용 비율 | 이자보상배율 |
| FT19 | return on equity | 주식의 수익 | 자기자본이익률 |
| FT21 | operating income | 운영 소득 | 영업이익 |
| FT25 | working capital | 작업 자본 | 운전자본 |
| FT26 | non-cash working capital | 비 현금 작업 자본 | 비현금 운전자본 |
| FT28 | free cash flow to the firm | 회사의 무료 현금 흐름 | 기업잉여현금흐름 |
| FT29 | free cash flow to equity | 주식에 자유로운 현금 교류 | 주주잉여현금흐름 |
| FT35 | market capitalization | 시장 자본 | 시가총액 |
| FT36 | book value | 책 값 | 장부가치 |
| FT37 | price earnings ratio | 가격 수입 비율 | 주가수익비율 |
| FT38 | price to book ratio | 책 비율의 가격 | 주가순자산비율 |
| FT41 | current assets | 현재 자산 | 유동자산 |
| FT42 | current liabilities | 현재 책임 | 유동부채 |
| FT43 | earnings per share | 몫 당 수입 | 주당순이익 |

용어가 사라진 FT24의 출력 `비현금은 비현금입니다.`는 후보에 넣지 않았다. 원문의 `noncash expenses`까지 손상시킬 수 있어 부분 문자열 교정의 근거가 되지 않기 때문이다. `미래 가치`, `주식 위험 프리미엄`, `기업 가치`처럼 띄어쓰기만 다른 표기나 `순 소득`, `자본 지출`, `본질적인 가치` 같은 해석 가능한 표현도 무조건 오역으로 분류하지 않았다. FT39·FT40의 영문 잔존은 한국어 오역 후보가 아니므로 별도 검토 대상으로 남긴다.

새 후보는 실제 모델·입력·출력으로 재현한 뒤 추가한다. 적절한 동의어를 단지 표기가 다르다는 이유로 오역으로 등록하지 않는다. 출처와 관측 자료를 구분하고 규칙을 바꾸면 번역 캐시가 변경을 구별하도록 콘텐츠 해시나 규칙 버전을 갱신한다. 저장된 기존 번역과 사용자 검수 기록을 자동으로 덮어쓰지 않는다.

정적 검증에서 JSON 필드·고정 ID·영문 표현의 중복·금지 단독어를 확인했다. 기준 표현은 각 규칙의 연결 출처 중 하나에 실존하며, 기존 학습 용어와 연결되는 37개 표제어에 한국어 불일치가 없다. 이 검사는 앱의 실제 문장 보정이나 사용자 검수 품질을 통과했다는 뜻이 아니다.
