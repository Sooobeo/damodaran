# S1 일반 의미 사전과 출처

2026-09-13 KST 작성. [인수인계 28.3~28.5](../../../TRANSLATION_HANDOFF_20260911.md#283-먼저-만들-입력과-검사-계약)의 **입력 사전 명세**다. [sense-dictionary.json](sense-dictionary.json)에 8개 표제 개념·24개 뜻·11개 출처를 기록했다. 문맥별 선택기·프롬프트 연결·번역 생성·품질 개선·앱 등록은 아직 수행하지 않았다.

한국어 정의·허용 표현·적용/제외 조건은 도우미가 영어 자료에 근거해 작성한 미검수 편집자 제안이다. 공식 한국어 번역이나 평가 정답이 아니다. 단어만으로 문장을 바꾸지 않으며, 같은 영어 표기가 문단 안에서 서로 다른 뜻을 가지면 출현별로 판단한다.

[현재 공통 지시](../../../scripts/model-comparison/run_hymt.py)의 `CONTEXT_INSTRUCTION`은 금융 의미에만 용어 항목을 사용하도록 제한한다. C0~C3의 공통 지시는 유지한다. 따라서 비금융 뜻은 선택기에서 금융 힌트의 오적용을 억제하거나 뜻을 보류하는 근거로 사용하며, 한국어 표현·정의를 프롬프트에 직접 넣지 않는다. 일반 뜻을 직접 안내하는 프롬프트는 공통 지시 변경을 포함하는 후속 별도 실험으로 보류한다.

## 확인한 범위

| 표제 개념 | 뜻 수 | 구분 |
|---|---:|---|
| equity | 3 | 소유 지분 / 부채 차감 뒤 자본 / 공정한 대우 |
| interest | 4 | 이자 금액 / 관심 / 재산 권리 / 당사자의 이익 |
| security | 3 | 거래되는 증권 / 보호·안정 / 채무 담보 |
| principal | 3 | 원금 / 조직 책임자 / 주요하다는 수식 |
| overhead | 2 | 사업 간접비 / 머리 위 공간 |
| margin | 5 | 이익 금액 / 영업이익 비율 / 지면 여백 / 수량 차이 / 거래 증거금 |
| percentage change / percentage point | 2 | 기준값 대비 상대 변화 / 두 백분율의 직접 차이 |
| increase by / increase to | 2 | 증가분 / 도달 수준 |

각 뜻의 `prompt_action`은 금융·회계 11개가 `hint_financial`, 비금융·일반·문법·범용 수량 13개가 `suppress_nonfinancial_hint`다. 이는 명확한 뜻이 선택됐을 때의 렌더링 가능성이다. 모호하거나 지원 밖이면 금융 뜻도 힌트를 생략한다. 상대 변화율·퍼센트포인트·증가분/도달 수준은 금융 자료에도 등장하는 범용 관계이므로 이 비교에서 직접 용어 힌트로 출력하지 않고 오적용 억제 및 별도 관계 검사 명세에만 사용한다.

`margin`을 언제나 비율로 취급하지 않는다. SEC 안내에는 금액인 gross margin과 비율인 operating margin이 각각 등장한다. `equity`도 지분 거래와 회계상 자본 잔액을 분리한다. 법률상 형평법·본인/대리인, 컴퓨터 처리 overhead, interest rate와 compound interest 등 이 버전에 정의가 없는 복합 개념은 단독 단어의 힌트를 억제하고 미지원으로 남긴다.

이 목록은 금융과 일반 의미를 대조할 수 있는 제한된 일반 사전이다. **C0/C1은 등록54개 사전만, C2/C3은 이 초점8개 사전만 사용하며 암묵적으로 병합하지 않는다.** 따라서 사전 범위가 달라지는 효과도 C2/C3의 변화에 포함한다. 이 사전은 전체 금융 용어를 다루거나 앱 사전을 대체하는 완성본이 아니며, 특정 평가 문항의 정답 목록도 아니다. 출처 자료의 문장 예시를 개발 평가 원문으로 복사하지 않았고, 소비한 test·개인 검수쌍·모델 출력을 읽어 항목별 정답을 만들지 않았다. 기존에 알려진 오류 유형을 바탕으로 범주를 고른 사실과 개별 문항 정답을 입력에 넣는 행위는 구분한다.

## 출처와 확인 방식

모두 2026-09-13에 내용을 확인했다. 짧은 원문 발췌는 JSON의 `sources[].excerpt` 한 곳에만 보존하며 출처별 25개 영어 단어 이내다. 아래 표와 각 뜻의 `source_ids`로 정의의 근거를 연결한다. 출처의 전체 페이지·예문·코퍼스·다른 발행사의 인용 내용을 사전으로 복제하지 않는다.

| 출처 ID | 직접 근거 | 내용 확인 경로 |
|---|---|---|
| cambridge-equity | [Cambridge — equity](https://dictionary.cambridge.org/us/dictionary/english/equity): VALUE, FAIRNESS, Business English | 발행사 URL의 확장된 검색 본문 |
| cambridge-interest | [Cambridge — interest](https://dictionary.cambridge.org/dictionary/english/interest): INVOLVEMENT, ADVANTAGE, MONEY, LEGAL RIGHT | 발행사 URL의 확장된 검색 본문 |
| cambridge-security | [Cambridge — security](https://dictionary.cambridge.org/dictionary/english/security): 투자·위험 회피·보호·담보 정의 | 발행사 URL의 확장된 검색 본문 |
| cambridge-overhead | [Cambridge — overhead](https://dictionary.cambridge.org/dictionary/english/overhead?topic=general-terms-used-in-ball-sports): 공간과 비용 정의 | 발행사 URL의 확장된 검색 본문 |
| cambridge-margin | [Cambridge — margin](https://dictionary.cambridge.org/us/dictionary/english/margin): BORDER, AMOUNT/DEGREE, Business English | 발행사 URL의 확장된 검색 본문 |
| cambridge-increase | [Cambridge — increase](https://dictionary.cambridge.org/us/dictionary/english/increase): Business English의 by/to 용례 | 발행사 URL의 확장된 검색 본문 |
| investor-principal | [Investor.gov — Principal](https://www.investor.gov/introduction-investing/investing-basics/glossary/principal): 차입·대여·투자의 기본 금액 | 발행사 HTML 직접 열람 |
| dictionary-principal | [Dictionary.com — principal](https://www.dictionary.com/browse/principal): 주요하다는 형용사, 책임자·학교장 명사 | 발행사 HTML 직접 열람 |
| sec-financial-statements | [SEC — Beginners' Guide to Financial Statements](https://www.sec.gov/about/reports-publications/beginners-guide-financial-statements): Balance Sheets, Income Statements, Financial Statement Ratios and Calculations | 발행사 HTML 직접 열람 |
| open-university-overheads | [OpenLearn — Direct and indirect production costs](https://www.open.edu/openlearn/money-business/fundamentals-cost-accounting-and-environmental-management-accounting/content-section-10.4): 간접비와 overheads의 관계 | 저작 대학 HTML 직접 열람 |
| eurostat-percent | [Eurostat — Percentage change and percentage points](https://ec.europa.eu/eurostat/statistics-explained/SEPDF/cache/54057.pdf): PDF 1페이지의 정의·수식 | 공식 PDF 텍스트 직접 열람 |

Cambridge의 직접 페이지 열기 시도에는 HTTP 403이 반환됐다. 정의 머리말과 문단이 포함된 웹 검색 도구의 확장 본문을 읽어 위 의미를 확인했으며, 검색 제목 한 줄이나 제3자 요약만으로 판단하지 않았다. 이 경로를 `access_method=search_returned_expanded_publisher_content`로 구분했고 당일 실시간 원본 바이트 다운로드 성공으로 보고하지 않는다. 문서 날짜가 확인된 자료만 별도 날짜를 적었다. `retrieved_on`은 발행·개정일이 아니다.

일반 영어 뜻은 사전 편찬 발행사의 정의를 1차 어휘 자료로 사용했다. 회계·통계의 좁은 구분에는 SEC·Eurostat·Open University의 직접 작성 교육 자료를 더했다. 이 자료의 교육상 정의를 현행 법률·회계 기준 전체의 권위 있는 해석으로 확대하지 않는다. by/to의 선택 조건, 동음·다의어 제외 조건과 한국어 허용 표현은 이 자료들로부터 도출한 편집자 해석이며 발행사가 제공한 자동 선택 알고리즘이 아니다.

## S2에 전달하는 선택 정책

1. 입력은 변경하지 않은 번역 대상, 허용된 같은 버전 원문 문맥, 동결된 일반 사전뿐이다. 평가 주석·참조 번역·질문 정답·판정·개인 검수·후보 모델 출력은 선택기에 넘기지 않는다.
2. 같은 원문 출현을 덮는 긴 복합구를 단독 단어보다 먼저 검토한다. 실제 토큰 수와 문자 길이를 기준으로 하고, 복합구가 보류이면 그 내부 단독 단어 힌트도 억제한다. `gross margin`을 짧은 `margin` 뜻으로 우회 확정하는 식의 처리를 금지한다.
3. 각 뜻의 적용 조건을 실제 원문/허용 문맥 인용으로 뒷받침하고 제외 조건을 확인한다. 문서 분야나 키워드 하나는 확정 근거가 아니다. 명확한 단일 뜻이 없으면 `omit_hint_and_record_deferred`, 정의하지 않은 뜻이면 `omit_hint_and_record_unsupported`다.
4. 선택 시 버전·블록/문서 위치·원문 출현과 오프셋·문맥 인용·뜻 ID·선택 이유·제외 확인·`prompt_action`·실제 힌트 또는 null·사전 해시·선택기 버전을 보존한다. 문맥 인용은 실제 존재하는 범위를 그대로 사용한다.
5. 명확한 금융·회계 뜻만 기존 `source → target (뜻: definition)` 형식으로 렌더링한다. source는 실제 원문 출현, target은 조건을 충족하는 허용 한국어 표현, definition은 선택한 금융 뜻이다. 일반 뜻의 `suppress_nonfinancial_hint`와 모호한/미지원 출현은 힌트 null로 남긴다. 비금융 정의·보류 이유를 Background Information으로 우회 전달하지 않는다. C2/C3은 초점8개 사전만 사용한다.
6. 허용 한국어 표현은 문맥에 맞는 참고 후보이다. 비금융 표현은 사전 설명·선택 판단용이며 이 비교의 모델 입력이 아니다. 단어 치환·원문 계산 수정·생략된 결론 추가·번역 후 강제 보정에 사용하지 않는다. 숫자·단위·부정·조건을 보존하며, %/%p와 by/to가 원문에서 불명확하면 더 그럴듯한 쪽으로 고치지 않는다.
7. 이 사전과 선택 정책은 S1의 자료 동결 대상이다. 실제 선택·토큰 계수·프롬프트 반영·오류 및 반례 검증은 S2가 수행하고 새 코드·프로필 정체성으로 남긴다. 기존 등록 사전·코드·manifest를 수정하지 않는다. 일반 뜻의 직접 힌트는 후속 별도 공통 지시 실험을 정하기 전까지 보류한다.

나눗셈의 피제수/제수, 포함/제외, 시간·조건·부정 범위는 별도 관계 검사 계약에서 정한다. 이 사전에 관계 개념 두 항목이 있다는 이유로 일반 의미 분석기나 관계 검사가 구현됐다고 표시하지 않는다.
