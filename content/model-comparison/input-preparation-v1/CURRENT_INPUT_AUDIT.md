# S1 — 현재 Hy7 정체성과 입력 경로 감사

2026-09-13 KST. [인수인계 28절](../../../TRANSLATION_HANDOFF_20260911.md#28-공통-입력-처리와-의미-관계-검사-개선-실행-계획)의 S1 자료 고정을 위한 읽기 감사다. 실제 파일의 바이트 해시, 실행 상수, 저장 구조의 가용 범위를 확인했다. 새 문맥 선택기·사전 선택기·관계 검사기 구현, 토큰 예산 실측, 번역 생성·품질 개선·앱 검증은 수행하지 않았다.

기계 판독 근거는 [audit-identity.json](audit-identity.json)이다. 운영 DB·기존 등록·고정 코드·사전·평가 자료를 변경하지 않았다. 새 개발 자료·사전·평가·독립 평가 계약은 이 디렉터리의 별도 S1 문서를 따른다.

## 1. 실제 등록 정체성과 해시 범위

| 항목 | 2026-09-13 읽기 감사 결과 |
|---|---|
| 활성 등록 | `hymt-contextual-q26-20260910-nativejob`, `profile: contextual` |
| 활성 manifest | `.translation/hymt/manifest.json` |
| manifest SHA-256 | `cb3031d87372fbb1b0c3dcaf2071064bb59a8a21bd96480ef2d5a66752ae52bd` |
| 모델 | `tencent/Hy-MT2-7B-GGUF`, `HY-MT2-7B-Q8_0.gguf` |
| 변환 저장소 revision | `ab8472660ac61fac25f1af43fac2599d52a8a775` |
| 모델 SHA-256 | `58b3ad55dd6f6fa08c695cddc34fb5f8f708a844f78ae10508071914b0ed67c0` |
| 모델 크기 | 7,981,928,896 바이트. 전체 바이트를 이번에 순차 읽어 SHA-256을 확인했다. |
| runtimeVersion | `hymt-local-v1:7a54164a4bded0f10fd538be4b47f9f310a53da3849dbcab5a07ae5607ea7953` |
| 고정 native 배포 | `llama.cpp b10874`, 설치 코드의 commit 표기는 `e2d2c0d6aa9b996d5d3a3c1d5e24c8c19728bb3d`, Windows x64 |
| 등록 사전 | 등록 디렉터리의 `catalog.json`, 54개. SHA-256 `d4af6a9fed8c6dba4c528abc52c64c19621c5fac98fd2bdb09e035769963de67` |
| Python/Jinja | 현재 실행 Python 3.11.9, Jinja 3.1.6. 등록 근거에 결속된 venv `python.exe`와 Jinja `.py` 25개를 재해시하고 추가·누락 없음을 확인했다. |
| 현재 환경 선택 | 공통 `lib/config.ts` 로더의 `TRANSLATION_PROVIDER=hymt`, `DATA_DIR=C:\Users\Insun\damodaran\data`. 키·다른 환경값은 출력하지 않았다. |

전체 캐시/handshake 모델 정체성은 다음과 같다.

```text
hymt:58b3ad55dd6f6fa08c695cddc34fb5f8f708a844f78ae10508071914b0ed67c0:cb3031d87372fbb1b0c3dcaf2071064bb59a8a21bd96480ef2d5a66752ae52bd
```

총 104개 파일을 읽고 해시를 남겼다. 활성·보존 manifest 2개, 모델 1개, runtime 52개, 등록 코드 6개, 등록 사전 1개, 기존 비교 증거 4개, Python/Jinja 26개, 앱 입력·추출·스키마·패키지 코드 12개다. manifest 및 이전 prepared manifest가 예상 해시를 제공하는 파일은 모두 일치했다. 앱 입력 경로 12개와 활성 manifest는 이번 현재 바이트를 기록한 것이며 새로 존재하는 과거 기대값을 만들어 대조하지 않았다. 각 읽기 전후 size/mtime 안정과 경로의 범위·reparse point 부재도 확인했다.

기존 비교 metrics·검토 summary·selection 파일은 바이트 해시만 확인했다. prepared manifest에서는 Python/Jinja 파일 목록만 사용했다. 소비한 최종 test 내용·참조 번역·질문 정답은 열지 않았고, 기존 출력 내용을 새 개발 자료로 복사하지 않았다. 등록 검증기 전체의 의미 평가 재검증을 다시 수행한 것은 아니다.

이 감사는 등록 범위의 파일 무결성 확인이다. native 실행·프로세스 건강 상태·현재 RAM/전원은 측정하지 않았다. venv의 `python.exe`는 launcher 파일이며, 기반 CPython 실행 파일/DLL/표준 라이브러리와 모든 전이 의존성의 바이트를 새로 인증한 것은 아니다. 원본 BF16 가중치 revision도 GGUF 배포자가 보증한 값으로 추정하지 않는다.

## 2. 현재 입력이 만들어지고 전달되는 경로

1. [`translationSnapshot`](../../../lib/translation/index.ts)은 블록 ID로 `source_blocks → source_versions → resources`를 JOIN한다. 대상 버전 ID·블록 text/type/structure·자료의 영문/한국어 제목을 얻는다. 문맥 SQL은 **동일 버전**에서 `sort_order BETWEEN target-1 AND target+1 ORDER BY sort_order`다. 세 줄이 언제나 존재하는 것은 아니며 실제로 존재하는 순번만 사용한다.
2. 현재 문맥은 정확히 `` `${title_en}\n${title_ko}\n${neighbors.map(n=>n.text).join('\n')}`.slice(0,10000) ``이다. 범위에 **대상 블록 자체가 포함**된다. 문자열 공백 정리·절 선별·부모 목록 선별·중복 제거가 없고, JS UTF-16 code unit 기준으로 10,000에서 자르므로 문장·구조 경계 절단도 가능하다. C0/C2 재현에서 이 동작을 개선해 버리면 기준 구성이 달라진다.
3. 앱 일반 glossary는 source/context로 선택되어 snapshot과 캐시 `glossaryVersion`에 반영된다. 현재 캐시는 블록/버전/type/structure hash/추출기/source hash/context hash/언어/provider/full model identity/promptVersion/glossaryVersion으로 구분된다. snapshot에는 title/neighbor 각각의 출처·선택 이유 목록은 없다.
4. [`translateSnapshot`](../../../lib/translation/index.ts)은 일반 블록의 저장된 text를 그대로 하나의 segment로 만든다. `table`은 영어가 있는 셀만 `blockId:rowIndex:cellIndex`로 나눠 보내며 **표 전체 블록 snapshot의 같은 context**를 각 셀에 공유한다. 수식·숫자 보호 표식은 Hymt에서 적용하지 않는다. 새 C0 비교가 표 셀을 대상으로 삼으면 이 분할과 표 전체 문맥도 재현해야 한다.
5. [`local.ts`](../../../lib/translation/local.ts)는 `.venv-training/Scripts/python.exe -u scripts/local-hymt/bridge.py` 경로의 NDJSON 연결을 사용한다. Hymt 요청은 `id, modelIdentity, context, segments, glossary`이며 준비 응답의 provider/model/hash/runtime/full identity가 모두 일치해야 한다. 시작은 최대 5분, 요청은 최대 30분이다.
6. [`bridge.py`](../../../scripts/local-hymt/bridge.py)는 요청 identity, 길이, 중복 ID 등을 검사하고 **호출자가 보낸 glossary를 힌트로 쓰지 않는다**. 등록 manifest에 묶인 54개 `bundle.terms`만 engine에 보낸다. 각 segment를 같은 context와 함께 순차 처리하며 파일 변경을 호출 전후에 검사한다. context/개별 source 최대 12,000 Python 문자, segment 합계 최대 150,000 문자, 1~1,000 segment라는 별도 프로토콜 제한이 있다.
7. [`engine.py`](../../../scripts/local-hymt/engine.py)는 source/context와 고정 사전만 clean row에 넣어 [`run_hymt.py`](../../../scripts/model-comparison/run_hymt.py)의 `build_user_prompt`와 `render_prompt`를 호출한다. 평가 ID·참조 번역·정답 용어·질문은 이 경로의 허용 필드가 아니다. 소유한 native server에서 실제 tokenize 후 예산/제어 토큰을 검사하고 completion을 호출한다.
8. 반환 경로는 원시 번역을 강제 치환하지 않는다. 빈 출력·잘림·생성 상한·제어 토큰 누출은 실패이고 숫자·통화 등의 경고는 별도로 남는다. 앱에서도 응답 ID의 누락/중복/추가 및 숫자·수식 검사를 한다. 자동 검사 통과가 명제/논리 보존이나 사용자 검수를 뜻하지 않는다.

여기서 “원문 보존”은 비교의 입력으로 고정한 **기존 source block text의 UTF-8 바이트 보존**이다. 원본 파일 바이트와 추출된 text는 같지 않다. 기존 HTML 추출은 공백 정리·위아래첨자 표기·긴 문단 분할을 하고, PDF 추출은 고정 버전의 줄 결합을 수행한다. S1~S4는 이러한 추출을 다시 수행해 대상 단위를 바꾸지 않는다.

## 3. C0를 정확히 재현할 때 고정할 항목

문서 묶음에는 자료 제목 EN/KO, sourceVersionId, target blockId/sortOrder/type/text/structure, 같은 버전의 순번이 포함된 블록 목록이 필요하다. C0의 이웃은 배열상 바로 앞/뒤가 아니라 **sort_order 값의 ±1 범위**다. 버전·블록 소속, 정렬 순번, 텍스트 hash가 맞지 않으면 거부한다. 합성 자료는 제목·순번·구조를 도우미가 만든 사실을 별도로 표시하며 운영 DB에서 복제한 자료라고 부르지 않는다.

C0 사전 매칭은 source에서만 수행한다. source 또는 aliases의 단어 경계를 확인하고, 대문자 단일 토큰 약어는 대소문자를 구분하며 그 외에는 구분하지 않는다. 긴 구문→시작 위치→term ID 순으로 겹침을 제거한 뒤 원문 출현 순서로 정렬한다. 같은 ID의 힌트는 한 번만 넣는다. 힌트 형식은 `실제 출현 → target (뜻: definition)`이고 원문/문맥의 뜻 선택은 모델에 맡긴다. 등록 사전에 source/alias가 정확히 `equity`인 항목은 **0개**다. C1에서 사전 항목/힌트 형식을 바꾸면 문맥 효과만의 비교가 아니다.

현재 contextual 공통 지시를 그대로 쓰고 `[Background Information]`, `[Conditional terminology references]`, `[Source Text]` 순서를 유지한다. 빈 문맥은 `None provided.`, 매칭 없음은 `None matched.`다. 등록 Jinja 템플릿의 결과는 정확히 `<|startoftext|>` + user content + `<|extra_0|>`다. 예전 raw 결과나 context가 빈 개발 결과는 새 C0를 대신하지 않는다.

가중치·양자화·runtime·template·공통 지시·sampling·seed·context size·최대 출력은 C0~C3에서 같아야 한다. C2/C3의 새 힌트 형식과 사전 선택만 의도한 변수로 기록한다. 현재 `run_hymt.py`의 기본 비교 CLI는 Q26의 24행/dataVersion/catalog 계약에 묶여 있으므로 새 16단위 실행에 그대로 사용하거나 해당 파일의 행 수를 고치지 않는다. 후속 구현은 새 adapter/실행 코드와 정체성을 만들고 고정 코드의 함수 계약을 대조한다.

## 4. tokenizer와 토큰 예산

GGUF의 첫 8 MiB만 읽는 기존 parser로 metadata/tensor descriptor를 대조했다. metadata 경계는 7,498,031 바이트이고 SHA-256은 `cc88c338a3aae5e90d69acfa0ed7c47c433d0eee02a58363440563681556baff`, 템플릿 SHA-256은 `788ac16c5d7bfefc28655928ad524c8f378a44cb24d24fb125d6a5859b167677`이다. 어휘 크기는 128,167이고 F32 tensor descriptor 129개/Q8_0 225개다. 가중치 tensor 로딩·행렬 연산은 하지 않았다.

원본 GGUF metadata의 EOS ID 3은 실제 `$`이므로 현재 runtime은 EOS=127960, EOT=127967, add_bos=false, add_eos=false를 override한다. BOS=127958, template 종료=127962이며 실제 실행 시 EOG 목록과 7개 특수 토큰의 tokenize/detokenize를 검사한다. 이번에는 header와 Jinja sentinel만 대조했고 native 토큰 ID 동작을 새로 실행하지 않았다.

| 고정값 | 값 |
|---|---|
| context size | 8,192 토큰 |
| 최대 출력 예약 | 4,096 토큰 |
| 허용 조건 | `len(full_rendered_prompt_tokens) + 4096 < 8192` |
| 실제 허용 prompt 상한 | **4,095 토큰**. 4,096은 실패한다. |
| tokenize 옵션 | full rendered prompt, `add_special:false`, `parse_special:true` |
| 생성 핵심 설정 | CPU only, threads 4, seed 42, temperature 0.7, top_p 0.6, top_k 20, repeat_penalty 1.05 |

전체 sampling dictionary와 runtime overrides는 JSON에 그대로 남겼다. 템플릿/공통 지시/사전/원문이 먼저 예산에 들어가야 하며, C1/C3에서 참고 조각만 문장·구조 경계로 추가/제외한다. 토크나이저의 경계 결합 때문에 조각별 토큰 수의 합으로 최종 prompt 토큰 수를 대신하지 않는다. C0/C2가 넘치면 원문/문맥/출력을 몰래 자르지 않고 해당 입력의 기술 불가와 다음 계약 결정을 기록한다. 결과를 본 뒤 단위를 빼거나 출력 상한을 바꾸지 않는다.

S2의 실제 tokenizer 경로는 다음 두 후보를 구분한다.

- 등록 runtime에는 `llama-tokenize.exe`가 존재하고 해당 바이트 해시는 확인됐다. vocabulary only 실행이 같은 GGUF/override/특수 토큰 옵션을 지원하는지, 고정 server의 `/tokenize`와 ID 배열이 같은지는 아직 검증하지 않았다. 이를 확인한 뒤에만 가중치 미로딩 경량 경로로 채택할 수 있다. 다른 HF tokenizer나 문자 수 추정으로 정확한 예산을 주장하지 않는다.
- 대체 경로는 고정 native server의 `/tokenize` 전용 확인이다. 이 경로는 모델을 메모리에 올릴 수 있으므로 번역 요청 0인 별도 단일 슬롯 작업으로 수행한다. 그 직전의 소유 프로세스·AC·physical/commit·Hy7 고정 자원 조건을 확인하고 소유 종료를 남겨야 한다. `/completion`은 호출하지 않는다. S1에서 이 작업을 수행한 것으로 표시하지 않는다.

현재 Hy7 고정 실행 코드는 CPU 4 등을 지정하지만 S1 감사 자체에 현재 RAM/AC 조건이나 새 실험의 최소 physical/commit 임계값이 포함되지는 않는다. 후속 tokenizer/생성 실행 계약에서 **Hy7에 근거한 조건을 확정**해야 하며 TG27 임계값이나 과거 PASS를 그대로 복사하지 않는다.

## 5. 저장 구조: 가능한 것과 없는 것

[`source_blocks` 스키마](../../../lib/db/migrations/001_initial.sql)는 source_version_id, sort_order, type, text/source_hash, page_index, bbox_json, structure_json을 가진다. 부모 block ID·section ID·list depth·표 머리글의 의미 연결 필드는 없다.

| 구조 | 현재 추출/저장 근거 | 새 입력 선택에서의 제한 |
|---|---|---|
| HTML 절 제목 | `heading`, `structure.level` 1~6, 버전 내 순서 | 가장 가까운 실제 heading을 순서/level 근거로 제안할 수 있지만 저장된 section membership이라고 표현하지 않는다. 제목처럼 보이는 일반 문단을 임의 승격하지 않는다. |
| HTML 목록 | `list_item`, text, links | 부모 목록 ID·깊이·도입문 연결은 저장되지 않는다. 인접했다는 이유만으로 종속 관계를 확정하지 않는다. 중첩 li는 부모 text에 포함될 수도 있다. |
| HTML 표 | `rows[].cells[]`의 text, header(th 여부), rowSpan, colSpan | header=true는 태그 근거다. `scope/headers`와 의미상 행/열/단위 연결은 저장되지 않는다. `th` 없는 비교표를 포함하며 자동 의미 관계 추출이 완료된 것이 아니다. |
| PDF v1/v2 | `paragraph`, page_index, bbox, `pdfParagraphVersion`, lineCount, lineBoxes | heading/list/table 의미 노드는 없다. lineBoxes는 좌표이며 각 줄의 별도 text/글꼴/부모 ID 저장은 없다. 합친 문단을 원래 목록으로 완전히 역복원했다고 주장하지 않는다. |
| 기존 PDF structured-v2 | page_index/bbox와 schemaVersion | v1/v2의 lineBoxes·목록 병합 정체성을 소급 부여하지 않는다. |
| 출처 | 불변 버전·블록 ID/순서/hash와 원본 파일 참조 | 같은 버전의 존재하는 정보만 사용한다. 정확한 DOM selector/원본 글자 offset은 현재 저장 계약에 없다. |

운영 DB는 공통 환경 로더로 확인한 `data/library.sqlite`를 URI `mode=ro`, `PRAGMA query_only=ON`, 명시적 읽기 transaction으로 집계하고 rollback/close했다. 본문·제목·개인 기록·번역·검수 이력은 조회하지 않았다. `structure_json`에서는 최상위 key 이름만 집계했고 내부 셀 text나 이미지 URL은 산출물에 남기지 않았다.

실제 저장 상태는 HTML `structured-v1` 3버전(462블록), `structured-v2` 4버전(463블록), XLS `structured-v2` 2버전이다. v1/v2 각각 heading 40개와 list_item 18개가 있고, v2에는 table 8개가 있다. heading의 level과 table의 rows가 실제 저장된 것을 확인했다. **이 운영 DB에는 PDF 버전 0개**다. 위 PDF 지원 설명은 구현 코드 계약이며 현재 운영 DB의 실물 PDF 감사를 뜻하지 않는다. 기존 격리 PDF QA 결과를 이번 새로운 실제 PDF 입력 감사로 바꾸지 않는다.

S1 개발 자료에서 실제 PDF 구조 사례를 포함하려면 별도로 허용된 공개 원문·이미 보존된 격리 자료의 버전/블록/좌표를 확인하거나 합성 PDF 구조임을 명시해야 한다. 운영 개인 DB에 새 버전을 만들거나 기존 PDF를 자동 재추출하는 단계는 아니다.

## 6. 오류 유형과 해석 경계

[인수인계 28.1](../../../TRANSLATION_HANDOFF_20260911.md#281-출발점과-이미-확인한-근거)과 [기존 오류 분석](../ERROR_LEDGER_REPORT_20260911.md)의 알려진 관측은 회귀/진단 가설로만 사용한다. 상대 %/%p, 시작값/증가분/결과값, 나눗셈 순서, 포함/제외, 이후/도중·조건, 다의어와 당사자 역할이 우선 비교 범위다. `equity` 단독 항목 부재나 대상 중복 문맥은 코드상 사실이지만 그것이 특정 모델 오역의 원인이라는 인과 결론은 아니다.

현재 숫자/기호 검사는 의미 관계 전체를 검사하지 않는다. 새 경고 검출력이 좋아져도 기존 번역 생성 품질이 좋아졌다고 집계하지 않는다. 저장 구조가 없는 표·부모 목록·일반 부정 범위는 미지원/불확실로 남기며 정상 통과 분모로 편입하지 않는다. 새 평가 정답/용어 출현 주석/질문 답안을 새 사전 선택기의 입력으로 사용하지 않는다.

## 7. 이번 검사와 다음 미완료

완료: 104개 실제 파일 hash inventory, 기대 hash/size 일치, active/registration 동일 바이트, Python/Jinja `.py` 목록 일치, 실행 canonical SHA 및 코드 상수 일치, GGUF metadata/template 계약, Jinja sentinel render, source structure metadata 집계, 실제 입력 경로 읽기 대조.

미실행: 실제 tokenizer 0회, native 모델 시작 0회, 번역 생성 0회, 모델 가중치 학습 0회, 앱 테스트/HTML3·PDF1 번역 저장·캐시 검증 0회. 기존 모델이나 등록의 품질 수용을 새로 인증하지 않는다.

S2 이후 미완료는 실제 token ID 동등성/예산, 문맥·사전 선택 구현 및 평가 주석 유출 거부, C0~C3 prompt 실물·해시 고정, Hy7 실행 직전 자원 조건, 새 관계 검사 검증, 동일 입력 생성/원문 대조/독립 질문과 최종 독립 평가다. 현재 감사 결과만으로 S2~S6을 완료 처리하지 않는다.
