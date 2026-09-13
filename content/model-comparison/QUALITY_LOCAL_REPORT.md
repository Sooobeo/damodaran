# 무료 로컬 번역 전체 품질 개선

사용자의 **무료 로컬 모델만으로 현재 PC에서 번역 전체 품질을 최대한 높이려는 요청**에 따라 네 구성을 실제 비교했다. 24개 장문·96개 번역의 익명 도우미 대조에서 문맥을 함께 준 **Hy-MT2 7B Q8**의 실질적 의미 오류가 가장 적어 선택·등록했다. 첫 앱 QA 시작 실패 뒤 Windows Job을 서버 생성 시 원자적으로 배정하도록 수정했고, 실제 제품 엔진의 모델 시작·정상 종료를 확인해 새 코드로 재등록했다. 후속 v1 앱 QA의 HTML 3문단·PDF 7블록 생성·저장·캐시와 자동 검사는 완료했다. PDF 도입 블록의 내용 추가를 보완한 v2에서도 새 PDF 4블록의 실제 생성·저장·캐시를 확인했다. 기존 HTML 3문단은 추가 추론 없이 캐시를 재사용했다. Root 도우미 대조에서 도입부와 3개 조건이 보존됐으며 본문의 영어 잔류는 경미한 문제로 남았다. 실제 운영 빌드 후 이 PC에 Hymt를 명시 설정하고 웹·worker를 시작했다. 운영 HTML 3문단의 새 생성·저장·캐시 검증과 최종 API의 실제 검증 완료 표시도 확인했다. 운영 검증 범위는 `html-only`이며 PDF는 앞선 격리 앱 QA의 근거다. 새 설치의 초기 기본값은 Argos다. 사람 검수 자료는 없고 완벽한 번역을 보장하지 않는다.

## 전체 의미 대조 결과

금융 16개·일반 8개에 대해 세 도우미가 모델 이름을 가린 네 선택지를 개별 판단했다. 원문·문맥·원시 번역·해시를 보존한 96개 판단을 모두 채운 후 모델 이름을 공개해 집계했다. 오류 수는 **실질적 의미 오류가 하나 이상 있는 문단 수**이며, 문장·단어의 전체 정확도나 전문가 평가가 아니다.

| 구성 | 의미 오류 /24 | 금융 /16 | 일반 /8 | 자연스러움 /24 | 참조 표현의 문자 일치 /57 |
|---|---:|---:|---:|---:|---:|
| Argos 앱 경로 | 24 | 16 | 8 | 0 | 5 |
| 학습 Marian v5 FP32 | 20 | 13 | 7 | 1 | 15 |
| Hy-MT2 원문만 | 10 | 9 | 1 | 20 | 15 |
| **Hy-MT2 문맥 사용** | **5** | **5** | **0** | **22** | **16** |

선택 구성에도 오류가 남았다. Q26-007은 재고 평가감액을 감가상각으로 부르고 추가 판매 비용 **제외를 포함으로 반전**했다. Q26-016은 후순위 채권액 $40,000과 실제 회수액 $20,000을 모두 받는다고 옮겼다. Root가 5개 의미 오류 전체를 다시 원문 대조해 확인했다. 반면 일반 문맥의 단어 뜻은 8개 모두 보존한 것으로 판단됐다. 이 시험 결과로 프롬프트·사전·샘플링이나 가중치를 다시 조정하지 않았다.

문자열 지표만 보면 v5의 chrF/BLEU가 41.199774/13.805922로 선택 구성의 39.235568/10.641908보다 높았다. 그러나 실제 의미 대조에서는 오류가 더 많았다. 의미·조건·주체·수량·누락/추가를 우선하고 자연스러움을 함께 보아 선택했다.

**용어 90%에 대한 범위를 구분한다.** v5가 별도의 고정 54개 용어 최종 시험에서 103/110(93.64%)을 달성한 사실은 유지한다. 이 장문 비교의 57개 참조 표현은 다른 범위·문자열 규칙이며, Hy-MT2의 16/57을 용어 90% 달성으로 표시하지 않는다. 새 앱 구성의 모든 금융 표기가 90% 이상 맞는다는 근거는 아직 없다.

원시 숫자 토큰 일치는 네 구성 모두 23/24였다. Hy-MT2 두 구성의 자동 경고는 각각 12개로 통화 표기 변경도 포함한다. 숫자 토큰이 같아도 Q26-016처럼 금액의 관계가 틀릴 수 있으며, 자동 검사를 의미 검수로 대신하지 않는다. 빈 출력은 없고 Hy/Marian의 잘림·생성 상한 도달도 없었다. Argos 실행기는 생성 상한 정보를 제공하지 않으므로 그 항목을 확인했다고 주장하지 않는다.

문맥 구성의 관측 생성 시간 중간값은 123.4295초다. 절전·다른 검사 부하·입력 문맥 차이가 있어 통제된 속도 비교로 해석하지 않는다. 속도보다 전체 품질을 우선한 선택이며 긴 원문에는 더 오래 걸릴 수 있다.

- 익명 검토 명세 SHA: `8c833e41c56a5433dff267496e411ebd88ece52607ea742165aa723b6b99f7c1`
- 완료 대조 요약 SHA: `77c31aae63260b45332343621bb24d3801a8433d1ce1035c886387cf2673d053`
- 원시 결과·익명 판단·선택 근거: `.training/comparisons/finance-quality-20260910/`
- 현재 등록 ID: `hymt-contextual-q26-20260910-nativejob`, manifest SHA `cb3031d87372fbb1b0c3dcaf2071064bb59a8a21bd96480ef2d5a66752ae52bd`
- 최초 등록 ID: `hymt-contextual-q26-20260910`, manifest SHA `8f898e6358c336e6eef44a562b84da63e861d7deb327399959e2aabbc8e12840`. [당시 코드 6개 보존 명세](../../.training/verifications/hymt-initial-registration-code-20260910/manifest.json)와 첫 앱 QA 실패를 유지

## 현재 근거와 비교 범위

v5는 1,152개 학습쌍으로 고정한 6주기·1,728회 가중치 갱신을 완료했다. 개발용 140개로 step 1728을 선택했고, 새 독립 최종 시험 140개에서 용어 **103/110(93.64%)**, chrF **48.860325**, BLEU **23.448432**, 숫자 문자열 일치 **139/140**이었다. 실제 텐서 253개가 변경됐다. 용어 점수를 문장 의미 정확도로 해석하지 않는다. 완료한 280개 익명 판단에서 실질적 의미 오류는 부모 79/140 → v5 50/140이며 일반 영역은 16/32 → 18/32로 늘었다. 자동 gate는 통과한 그대로 보존하되 전체 품질이 부족해 앱 기본으로 선택하지 않았다. [가중치 학습 기록](../training/FINANCE_V5_REPORT.md)과 이 시스템 비교를 구분한다.

Root가 이 **중간 dev 출력의 용어 미적중·숫자 검사 불일치 행**을 원문과 대조했다. V5-DEV-043은 받지 못한 고객 대금(`payment remained outstanding`)을 지급액이 미미하다고 번역했고, V5-DEV-070은 총 주주가치와 주당 가치의 구분을 같은 표현 두 개로 번역했다. V5-DEV-094에서는 큰 매출채권을 큰 매출액으로 바꾸었다. 이런 의미 손상은 용어 표기 적중만 높인다고 해결되지는 않는다. 이 대조는 도우미의 개발용 진단이며 최종 모델·독립 test 판정이 아니다.

반면 숫자 문자열 검사에서 불일치한 세 행은 `a year/the year → 1년` 두 개와 `09:40 → 9시 40분` 한 개였다. 숫자 검사 137/140을 수량 의미 오류 3건으로 해석하면 안 된다. 고정한 기존 검사를 사후 수정하지 않고, 새 비교에서 수량·단위·시간의 실제 의미를 별도로 대조한다.

앱 코드의 문맥 처리도 확인했다. 기존 `structured-v2` PDF 추출은 줄별 블록을 만들었고, Argos 번역은 숫자·수식 보호 표식 사이 조각을 번역하며 TypeScript가 모은 참고 문맥을 Argos에 전달하지 않는다. 이 구조는 문장과 의미 관계를 나눌 수 있다. 코드 점검으로 찾은 위험이며 실제 오류율 측정은 아니다. 추가 Hy-MT2 제공자는 문단 원문과 기존 제목·이웃 문맥을 보존하도록 연결했다. 새 PDF에는 아래의 별도 문단 추출 버전을 사용하며 기존 원문 버전·블록·개인 기록을 덮어쓰지 않는다.

새 비교는 다음 네 가지 구성을 구분한다. 같은 모델의 순수 가중치 학습 효과를 측정하는 v5 시험과 달리, 입력 문맥·처리 방식·런타임·정밀도까지 포함하는 사용 방식의 비교다.

| 구성 | 입력과 처리 |
|---|---|
| 비교 당시 Argos 앱 경로 | 실제 앱의 숫자·수식 보호와 용어 보정. 원문과 참고 문맥으로 사전을 선택하지만 모델에는 문맥이 전달되지 않음. DB·검수 메모리·작업 큐는 호출하지 않음 |
| 선택된 v5 Marian FP32 | 검증한 모델·토크나이저로 문단 전체를 CPU에서 원시 번역. 사후 용어 치환·문맥·숫자 보호 없음 |
| Hy-MT2 7B Q8 원문만 | 공식 기본 번역 지시와 원문만 입력 |
| Hy-MT2 7B Q8 문맥 사용 | 참고 문맥과 번역 대상을 구분하고, 원문의 실제 표현에 해당하는 기존 사전의 뜻·표기를 조건부로 참고. 일반적인 뜻이나 다른 금융 뜻에는 사전 표기를 강제하지 않음 |

한국어 참조·평가 용어·금지 표현·검토 항목은 모델 프롬프트에 넣지 않는다. 문맥 사용 구성의 용어 힌트는 평가 정답에서 얻지 않고 이미 고정한 앱 사전 54개에서 원문 표현으로 선택한다. 그 결과를 사전 없이 생성한 원시 모델의 용어 점수라고 표시하지 않는다.

## 새 비교 자료

별도 도우미가 금융 16개·일반 8개를 작성하고 원문과 한국어를 전수 대조했다. 금융 문단은 68~84단어, 일반은 50~53단어다. 8개에는 별도 문맥 28~33단어를 제공하고, 보호 수식 5개 및 숫자·단위가 있는 17개를 포함한다. 모든 자료는 도우미 창작·사람 미검수다. 실제 NYU 문서나 전문가 정답이 아니다.

Root가 모델 출력을 보기 전에 24개 모두를 다시 원문 대조했다. 계속 영업사업의 terminal value와 서비스 revenue 두 표현은 기존 사전의 계속가치·매출액으로 통일했다. 원문과 문맥은 바꾸지 않았고 이전 한국어 표현을 의미 오류로 집계하지 않았다. 작성·대조 전후 파일을 보존했다.

이전 train/dev/test와 12개 비교·다의어 자료에 대한 본문·문맥의 정확 중복·숫자 템플릿·근접 중복 검사를 통과했다. 이 새 자료는 가중치 갱신에 사용하지 않는다. 각 결과의 ID·원문·문맥·입력 파일·모델/런타임/코드 해시와 실패·상한·실행 시간을 남기고 모델 이름을 가려 의미를 대조한다. 숫자 토큰 일치와 문자열 용어 적중만으로 의미 정확도를 판정하지 않는다.

- 확정 자료: `.training/comparisons/finance-quality-20260910/dataset.jsonl`
- SHA-256: `73fb2b2c4291090e49205aeff34cecfa24132d91c19522834fe779139a457737`
- 출판 명세: 같은 폴더의 `dataset-manifest.json`
- 작성자·두 번째 도우미 대조: `author-review.json`, `second-review.json`

## 추가 공개 모델과 실행 계약

후보는 [Tencent Hy-MT2 7B](https://huggingface.co/tencent/Hy-MT2-7B)의 [공식 Q8 GGUF 배포](https://huggingface.co/tencent/Hy-MT2-7B-GGUF/tree/ab8472660ac61fac25f1af43fac2599d52a8a775)다. 공식 안내는 한국어 및 문맥·용어 지시를 지원한다고 설명한다. 이 사실만으로 금융 영한 품질의 우위를 보장하지 않는다. 게시자의 Q8 변환본이며 원본 BF16과 같다고 가정하지 않는다.

가중치 7,981,928,896바이트와 SHA-256 `58b3ad55dd6f6fa08c695cddc34fb5f8f708a844f78ae10508071914b0ed67c0`을 실제 확인했다. 기존 설치와 분리한 llama.cpp b10874 Windows 실행기 52개를 공식 ZIP 바이트와 대조했다. 환경·설정·DB·앱 제공자는 바꾸지 않았다. 설치 기록은 `.training/comparisons/hy-mt2-7b-q8/`에 있다. 원래 설치기의 LICENSE 링크 정정은 원본 manifest를 보존하고 `installation-notes.json`에 별도로 남겼다.

공식 Q8의 종료 토큰 메타데이터가 달러 토큰 ID 3을 가리키는 문제를 파일 헤더에서 확인했다. 원본 토크나이저·generation 설정에 근거해 실행 시 EOS/EOT와 자동 BOS/EOS 설정을 명시한다. 가중치 바이트는 바꾸지 않는다. 실제 모델 로드에서 종료 토큰은 127957·127960·127967이며 달러 ID 3은 포함되지 않음을 확인했다. 원문 생성 전 실제 토큰·BOS·문맥 크기·템플릿 검사를 통과했다.

첫 실제 시작은 종료 토큰 목록이 로그에 없어서 생성 요청 0회로 중단됐다. [고정 llama.cpp의 로그 콜백](https://raw.githubusercontent.com/ggml-org/llama.cpp/b10874/common/log.cpp)은 라이브러리 INFO를 TRACE 수준 4로 처리하므로, 기존 verbosity 3을 4로 수정해 필요한 실제 증거를 확보했다. 생성·샘플링·모델 바이트는 바꾸지 않았다. 실패 폴더·원래 실행 코드·summary SHA를 `hymt-raw-startup-failure-1/`과 `startup-failures.json`에 보존했고 관련 기존 검사 23개가 통과했다. 수정 코드 SHA는 `a80c431e51c17d510273457d0991e2febba57bac33a8f89a067e1f571387a410`이다.

Hy-MT2 두 구성은 CPU 4 threads, context 8192, 최대 생성 4096, seed 42를 사용한다. sampling은 공식 모델 카드의 temperature 0.7·top_p 0.6·top_k 20·repetition penalty 1.05를 따른다. 별도 generation_config의 top_p 0.8과 구분하여 카드 우선으로 고정한다. sampler 순서와 추가 기본값도 실행기에 명시하며 Transformers와 비트 단위 동일성을 주장하지 않는다. 정답을 본 뒤 이 설정을 바꾸지 않는다.

원문만 구성 실행 중 Windows System의 Kernel-Power 506/507 기록에서 Modern Standby 진입·복귀를 확인했다. `04:50:40Z`~`05:08:15Z`의 절전 구간이 섞인 Q26-014는 1,112.156초로 기록됐다. 원래 시간·결과를 삭제하거나 정상 속도로 간주하지 않는다. 사건과 SHA는 `power-events.json`(`a98191cbf5f0341e03c47367e2eb3ecb845018d408991d301984db62384b5fe8`)에 보존했다. 앞선 학습 중의 긴 시간 공백에도 같은 종류의 이벤트가 관측됐다.

`05:13:26Z`에 이 비교 작업 동안만 `ES_CONTINUOUS | ES_SYSTEM_REQUIRED` 요청을 적용했다. [Windows 실행 상태 API](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate)의 성공 반환을 확인했고 화면 강제 켜짐·away mode·영구 전원 계획 변경은 사용하지 않는다. 이 요청은 사용자가 직접 선택한 절전을 막지 않는다. [최대 2시간 요청](../../.training/verifications/keep-awake-20260910.json)은 `07:13:27Z`에 자동 해제됐다. 이어 앱 v2 검증용으로 `07:14:39Z`에 적용한 [최대 30분 요청](../../.training/verifications/keep-awake-app-v2-20260910.json)도 수동 절전이 포함된 실제 경과 뒤 `07:58:45Z`에 해제돼 두 기록 모두 `released: true`다. `powercfg /requests` 조회는 관리자 권한 요구로 수행하지 못했으므로 그 조회의 성공을 주장하지 않는다.

문맥 구성의 Q26-013에도 수동 절전이 섞였다. `05:50:33Z`의 전원 버튼에 따른 진입과 `05:52:34Z`의 덮개에 따른 복귀가 관측됐고 원래 205.250초를 보존했다(`power-events-contextual.json`). 임시 idle 요청의 성공을 수동 절전 방지 성공으로 표시하지 않는다. 앱 검사 부하와 겹친 시간도 `verification-load-context.json`에 남겼으며 네 구성의 시간을 통제된 단독 속도 비교로 해석하지 않는다.

## 실행과 현재 검증

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/setup_hymt.py

# 자료는 이미 확정됨. 출력은 존재하지 않는 새 디렉터리를 사용한다.
node_modules/.bin/tsx.cmd scripts/model-comparison/run_app_baseline.ts --input .training/comparisons/finance-quality-20260910/dataset.jsonl --output .training/comparisons/finance-quality-20260910/argos-app
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_quality_marian.py --input .training/comparisons/finance-quality-20260910/dataset.jsonl --output .training/comparisons/finance-quality-20260910/marian-v5 --run-id finance-v5-terminology
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_hymt.py --input .training/comparisons/finance-quality-20260910/dataset.jsonl --output .training/comparisons/finance-quality-20260910/hymt-raw --profile raw
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_hymt.py --input .training/comparisons/finance-quality-20260910/dataset.jsonl --output .training/comparisons/finance-quality-20260910/hymt-contextual --profile contextual
```

Argos 앱 경로는 `2026-09-10T02:14:57.188Z`에 **24/24개 실제 생성과 무결성 확인**을 완료했다. 자동 경고는 0개였지만 이후 익명 대조에서는 24개 모두에 실질적 의미 오류가 있다고 판단했다. 결과 SHA-256은 `8e39f5efa6cab92a10e3b8ea0e26e5b938212f6d8433202f11dfeb0f51e6a94c`다. DB·검수 메모리·큐·유료 API는 사용하지 않았다. 실행 당시 코드 14개는 결과에 기록된 SHA와 일치하는 바이트 사본으로 별도 보존하여 이후 앱 수정과 구분한다.

나머지 큰 모델은 본학습 후 순서대로 실제 실행했다. Hy 원문만·Marian v5·Hy 문맥 각각 24개를 완료하고 원문·결과·실행 정체성을 확인했다. 문맥 구성은 `2026-09-10T06:13:25.992348Z`에 완료했고 원시 결과 SHA는 `1d4659264775cb2e6c44ed5d75d9b934386269263691c1e4e344fa2d49995158`이다. 소유한 Hy 서버 종료도 확인했다. Argos의 시간에는 학습과의 자원 경합이 포함되므로 다른 모델의 단독 실행 시간과 속도 우열을 판단하는 데 사용하지 않는다. `execution-context.json`과 별도 절전·검사 부하 기록에 이 조건을 남겼다. 원문은 loopback 안에서 처리했고 유료 API 호출은 없다.

새 자료의 출처·대조·누수 검사, TypeScript 검사, 설치 자산 SHA 확인과 네 구성 각각 24개 실제 생성을 확인했다. 설치·Hy-MT2 실행기의 synthetic 검사 46개, 네 구성 비교·익명 검토·코드 보존 도구 33개, 학습·익명 검토 도구 전체 159개가 각각 통과했다. 이 코드 검사와 실제 추론 증거는 구분한다. 초기 앱 실행기의 Windows Job 정리 검사 13개(실제 소형 하위 프로세스 포함), 엔진 mock 검사 26개, NDJSON mock 검사 14개도 통과했다. 앱 제공자 연결·무결성·사용량·기존 유료 작업 차단 관련 TypeScript 검사 32개와 타입 검사를 통과했다. 이 비교·구현 검사 당시에는 앱 제공자 설정을 바꾸지 않았으며 이후 실제 앱 검증과 명시 선택은 아래에 구분한다.

의미 품질과 선택 판정은 위의 전수 대조 결과를 따른다. 실제 앱 추론 검증·활성화는 별도 단계로 기록한다. 원본·가중치·비교 결과는 Git/public/SQLite 백업에 포함되지 않으므로 결과 폴더를 별도로 보관한다.

### 실제 앱 검증 준비에서 확인한 사항

전체 앱 검사 67개가 통과한 뒤, `.venv-training/Scripts/python.exe`로 추가 Python 실행기 통합 검사 68개를 실행했다. 이 중 Windows 프로세스 검사 3개가 실패했고 symlink 권한 검사 1개는 제외됐다. 가상환경의 launcher와 실제 Python PID가 달라 기존 종료 검사가 잘못된 프로세스를 가리키는 상황이었다. 아래의 프로세스 보완과 재검증으로 구분해 기록하며, 최초 실패를 숨기거나 모델 추론 성공으로 표시하지 않는다.

추가 조사에서는 일반 venv 자식이 소유 Job 밖에서 실행되는 경우도 재현했다. 앱의 고정 native 서버 경로로 계약을 제한하고, 실제 child 핸들의 Job 소속을 확인한 뒤 반환하며 미확인이면 종료·거부하도록 보완했다. 실제 launcher·Python·native child의 생성 시각과 OS 핸들을 유지해 종료를 검사했다. 최종 관련 검사 19개는 기본·가상환경 Python에서 각각 통과했다(skip·경고 없음). Node `child.kill()`·`stdin.end()` 경로와 무관한 control 프로세스 생존을 포함한다. 같은 버전 문자열에서도 바뀐 Python/Jinja 코드, 등록 중 바뀐 원시 입력을 거부하도록 보완했고 이후 전체 앱 검사 **73개가 통과**했다.

실제 PDF 준비 중 공개 NYU DNS의 `64:ff9b::/96` NAT64 주소를 기존 IPv6 검사가 차단하는 문제를 관측했다. [RFC 6052](https://www.rfc-editor.org/rfc/rfc6052.html#section-3.1)에 따라 이 prefix에서 공개 IPv4를 복원해 검사하도록 한정 수정하고 주소 검사 4개 그룹 및 타입 검사를 통과했다. 호스트 허용 목록·리디렉션별 검사·사설 주소 차단은 유지한다.

수정 뒤 R06의 실제 링크로 [Session 2 PDF](https://pages.stern.nyu.edu/~adamodar/pdfiles/FoundationsOnline/slides/session2.pdf)를 다운로드했다. 원본 4,065,909바이트, SHA-256 `de10cd81e1b2d49ed9d3a17cad40d8a1b4be89fcbb1c4462d0c2ebd7ff86d895`다. 기존 PDF.js 추출은 10페이지·179블록·ready를 반환했으며 원본 해시는 추출 전후 동일했다. 제목 위주인 1~2페이지 대신 설명 본문이 있는 **3페이지**를 실제 앱 검증 범위로 정했다.

3페이지를 실제 렌더링하고 원문 항목을 대조했다. `pdf-paragraphs-v1`은 글꼴·들여쓰기·줄 간격이 맞는 연속 줄만 묶어 이 페이지의 13블록을 7블록으로 만들며 전문·수치·좌표·원본 바이트를 보존했다. 표·회전 글자가 있는 7·9페이지도 렌더링과 텍스트 조각으로 대조했다. 초기 검사에서 발견한 표 라벨 오병합과 소수·음수 사이 공백 문제를 수정했고, 복잡한 배치는 병합하지 않는다. 신규 검사 12개·기존 추출 회귀 3개와 타입 검사를 통과했다. 새 가져오기·업로드만 새 버전을 생성하고, 저장된 기존 `structured-v2` 작업은 기존 규칙으로 처리한다. 시각·추출 검증을 실제 번역의 의미 품질 검사로 표시하지 않는다.

원본·초기 실패·수정 후 리디렉션·헤더·출처·추출 요약은 `.training/verifications/source-pdf-20260910/`에 보존했다. 운영 DB·원문 버전·개인 설정은 바꾸지 않았다.

등록 후 첫 실제 앱 QA는 `2026-09-10T06:30:04.523Z`~`06:32:11.427Z`에 별도 데이터 폴더에서 수행했고 모델 시작 단계에서 실패했다. [실패 기록](../../test-results/hymt-runtime-verification-1789021931429.json)은 `real-translation` 단계의 `VERIFICATION_FAILED`와 완료 검사 0개를 남긴다. 앞선 실제 PDF 추출은 `pdf-paragraphs-v1`의 3페이지 7블록 전문·순서·원본 SHA까지 통과했다. 이 추출 성공을 HTML/PDF 번역·저장·캐시 성공으로 표시하지 않는다. 운영 원문·개인 기록과 기본 Argos 제공자는 보존했다.

첫 앱 QA 이후 Windows native 서버의 Job 배정을 암묵적 상속에 맡기지 않고 `CreateProcessW`의 `PROC_THREAD_ATTRIBUTE_JOB_LIST`에서 원자적으로 수행하도록 고쳤다. 제품 `process_owner.py` SHA는 `9a00d809466e304359fb47c374c8fc2464ce31cdbc9557fbc9eee089bfe9d8b2`다. [최종 통합 검사 67개](../../.training/verifications/hymt-job-list-tests-20260910-final.json)는 모두 통과했으며, 기본·venv 부모와 Node 종료·stdin EOF·native 자식·생성 반환 전 종료·무관한 프로세스 보존·Unicode 인자/환경/경로·binary 입출력·오류 시 핸들 정리를 포함한다. mock·가벼운 native 검사에서 모델은 로드하지 않았다.

별도 실제 제품 코드의 `OwnedHymtEngine`은 **모델 시작·정상 종료에 성공**했다. [실제 시작 기록](../../.training/verifications/hymt-native-job-startup-20260910.json)은 `2026-09-10T06:57:54Z` 관측 완료, exit 0, 원문 전송·생성 요청 0개를 남긴다. 진단용 대체 생성 함수의 성공과 구분한다. 모델·런타임 파일·생성 설정·Q26 결과를 바꾸지 않았으며 실제 등록 CLI를 통해 위의 `nativejob` 등록본을 새로 만들었다. 최초 등록의 코드 6개와 QA 실패를 보존한 뒤 아래의 v1 실제 앱 QA를 완료했다.

v1 실제 앱 QA는 `2026-09-10T07:10:31.919Z`에 완료했다. [완료 기록](../../test-results/hymt-runtime-verification-1789024231920.json)(SHA `2131739d65c3ed68bb825c92d22c25854891d0b3a45e264c2cb974565525b73c`)은 실제 R01 HTML 3문단과 NYU PDF 3페이지 7블록의 새 생성·작업/사용량 연결·저장·캐시 재사용을 확인한다. 자동 검사는 10/10 `passed`, `needs_review` 0개였으며 운영 원문·개인 기록·Argos 설정은 보존했다. PDF의 숫자 푸터 1개는 한국어 문장 번역 사례와 구분한다.

[HTML 독립 도우미 대조](../../.training/verifications/hymt-app-html-assistant-review-20260910.json)는 핵심 의미 3/3 보존, 실질적 오류 0개, 용어 정밀도 문제 1개·문체 불일치 2개를 기록했다. [PDF v1 도우미 대조](../../.training/verifications/hymt-app-pdf-v1-assistant-review-20260910.json)에서는 자동 통과와 별개로 미완결 도입 블록에 다음 하위 항목의 내용이 추가된 실질적 오류 1개가 있었다. 본문의 `자산 in place` 영어 잔류와 표현·문체 문제도 남았다. 이 대조는 사람 검수가 아니며 자동 `passed`를 의미 무오류로 해석하지 않는다.

후속 `pdf-paragraphs-v2`는 안전한 미완결 글머리 도입과 2개 이상 하위 목록 전체를 하나의 원문 단위로 묶도록 구현했다. 뒤쪽 항목의 구조가 다르거나 전체 6,000자를 넘으면 일부만 묶지 않는다. 실제 PDF 3페이지는 legacy 13블록·v1 7블록·v2 4블록이며 v2의 원래 줄 수는 `[1,4,7,1]`이다. [관련 검사 18개](../../.training/verifications/pdf-v2-contract-20260910.json)가 전문·기호·좌표 보존, 복잡한 7·9페이지 미변경, 격리 DB의 v1 대기 작업·기존 메모/검수/캐시·설정 해시 보존을 포함해 통과했다. 기존 PDF.js TT32 경고는 남았다.

v1 원문·출력·QA 기록을 보존한 새 복원 폴더에서 [v2 실제 QA](../../test-results/hymt-runtime-v2-verification-1789025037793.json)를 `2026-09-10T07:27:34.171Z`에 완료했다(기록 SHA `56c1d722523cadcdef19b886f56e931c7d09aaa6c8a770f92fe9c7174bd7a80d`). 새 PDF 4블록의 실제 생성·저장·캐시와 기존 HTML 3문단의 원문·문맥·모델·번역·사용량 일치 및 캐시 재사용을 확인했다. 추가 HTML 생성은 0회이며 PDF·HTML 모두 자동 `passed`, `needs_review` 0개였다. 원래 v1 기록·운영 원문·개인 기록·Argos 설정은 유지했다. 이 QA는 새 PDF 4개와 HTML 캐시 확인만 포함하므로 v1 전체 QA나 Q26 모델 속도와 직접 비교하지 않는다.

Root가 v2 4블록을 원문과 직접 대조한 결과, 도입부와 하위의 낮음·높음·유사함 세 조건을 모두 보존해 v1의 실질적인 내용 추가가 해소됐다고 판단했다. 본문의 `자산 in place` 영어 잔류는 경미한 문제로 남았다. [별도 도우미 대조](../../.training/verifications/hymt-app-pdf-v2-assistant-review-20260910.json)(SHA `6745aaa0a9f095963a15cb80a2769c3870777222ba88ab7977f038d5ea86a76d`)도 4블록의 중요한 의미 오류는 없으며 본문 1개의 경미한 완전성/정밀도 문제·본문 2개의 문체 차이가 남았다고 기록했다. 사람·전문가 검수나 일반적인 무오류 보장은 아니다. 가중치·프롬프트·고정 사전·Q26 시험은 바꾸지 않았다.

최신 [전체 앱 검사](../../.training/verifications/npm-test-pdf-v2-20260910.json)는 93개 중 91 통과·2 제외·실패 0이며 `npm run typecheck`도 exit 0이었다. 기본 실행에서 제외한 실제 PDF 2개는 별도 18개 검사에서 통과했다. 새 코드의 실제 운영 빌드·Hymt 명시 설정·운영 HTML 검증도 완료했다.

앞선 격리 `npm run build` 자체는 exit 0이었지만 strict helper는 Next.js의 `next-env.d.ts` 두 import 생성 변경 때문에 effective exit 1을 기록했다. [원래 receipt](../../.training/verifications/hymt-build-final-1789021228540/build-execution.json)를 보존하고, [별도 독립 검증](../../.training/verifications/hymt-build-final-1789021228540/generated-declarations-verification.json)에서 **당시** 두 경로 치환 외 바이트 동일성·생성 선언 존재·나머지 코드/설정 SHA·PDF.js 자산 196개를 확인했다. 이후 `process_owner.py`가 바뀌었으므로 현재 코드 133개가 그 빌드와 같다고 표시하지 않는다. 이 과거 격리 검증 자체는 운영 앱의 재빌드·재시작 결과가 아니며, 이후 새 코드의 운영 빌드는 별도 기록이다.

[실제 운영 빌드](../../.training/verifications/hymt-production-build-20260910.json)는 `2026-09-10T08:00:10.084Z`에 exit 0을 기록했다. 참조 파일 52개를 포함한 DB 백업 `data/backups/2026-09-10T07-59-06-100Z-71b70e6b`와 이전 `.next`의 별도 사본을 보존했다. [제공자 선택](../../.training/verifications/hymt-provider-selection-20260910.json)은 기존 `.env.local` 원시 바이트를 유지하면서 없던 `TRANSLATION_PROVIDER=hymt` 항목만 추가했다. 단일 웹·worker를 새로 시작한 뒤 [운영 HTML QA](../../test-results/hymt-operational-html-1789027497099-402418fd-52a6-4044-a80a-ed95287bfee5.json)를 `08:14:10.018Z`에 완료했다. 새 HTML 3문단 생성·저장·자동 검사와 캐시 3개를 확인했고 유료 호출 0개, 기존 기록·원본 파일 보존을 확인했다. 이 운영 기록 SHA는 `2fa4c71b8b590de4de7efd757356175414e86bd11626d7a2217e82f9d672e00f`다.

운영 QA helper는 동일 경로의 구분자 표기 차이로 [사전조건 검사에서 실패](../../test-results/hymt-operational-html-1789027271165-bc690d1a-b11c-49fb-91f4-306c7eddc525.json)했으며 번역 요청은 0개였다. [이전 helper](../../test-results/verify-hymt-operational-html-before-path-fix.mts)를 보존하고 해석한 절대 경로·실제 SHA를 함께 확인하도록 고쳤다. 수정 helper SHA `62e5458d7b8df2c0f4b379de44131954a2341150151d0187258b17bc21e98499`의 읽기 전용 검증·변조 4건 거부·엄격한 타입 검사는 통과했다. 이 helper 수정은 앱 코드나 모델을 바꾸지 않는다.

[운영 원문 대조](../../.training/verifications/hymt-operational-html-semantic-evidence-20260910.json)(SHA `dfe617622a33e0b4e2a03cc8a2791788434ac86de99a78e78bb4af5e6795732e`)는 새 HTML 3개가 앞서 읽고 검토한 원문·번역 쌍과 동일함을 확인했다. 기존의 경미한 용어 정밀도·문체 문제는 남으며 사람 검수는 아니다. [표준 `--html-only` 검증](../../.training/verifications/hymt-operational-metadata-1789028103209/report.json)은 `08:15:14.636Z`에 exit 0으로 완료했고 추가 추론 없이 jobs·사용량·번역 개수가 같았다. 이전 운영 metadata를 보존한 뒤 새 모델의 HTML 검증 상태만 저장했다.

[최종 API·DB 확인](../../.training/verifications/hymt-final-operational-state-20260910.json)은 `08:16:12.246Z`에 Hymt `configured: true`·`liveVerified: true`, 로컬 처리·API 키 불필요, 활성 작업 0개와 활성화 후 사용량 3개가 모두 현재 Hy-MT2 모델임을 확인했다. 운영 검증 범위는 `html-only`이며 PDF 4블록은 앞선 격리 앱 QA의 근거다. [PC 1440px·모바일 390px 홈/설정 4개 화면](../../test-results/hymt-readonly-ui-1789027442434/verification.json)(SHA `f1db55c0d6203f4245d50723e3f6c47444c33e3e38d71d573090b8cfcfab1dc8`)은 읽기 전용 검사에서 통과했다. 이 화면은 운영 추론 전 `liveVerified: false`였으므로 최종 API 확인 시점과 구분한다.

후속 [최종 설정 화면 1440px](../../test-results/hymt-final-settings-ui-1789028256056/verification.json)(SHA `7dfa0db041b7bd738588dd0ff3b57bcb7fbbbadfcd91766374fb3c5260b623a0`)도 실제 ‘검증 기록 있음’ 표시와 Hymt `configured: true`·`liveVerified: true`를 확인했다. GET만 사용했고 오류·가로 넘침·추가 추론은 0개였다.

v5 장문 비교의 첫 시도는 모델을 로드하기 전에 토크나이저 검사에서 중단됐다. 조사 결과 부모와 선택 모델의 토크나이저 5개 파일은 바이트까지 같고, `special_tokens_map.json`만 문자열에서 같은 내용·기본 옵션의 AddedToken 객체로 저장된 차이였다. 설치된 Transformers의 직렬화와 Windows CRLF를 적용한 결과가 후보 파일 SHA와 정확히 일치했으며, 별도 진단 예문 영문 6개·한글 5개의 토큰화·복원도 동일했다.

동결된 `infer_v5.py` 직접 CLI에는 기존 바이트 비교 제한을 남기고, 새 Q26 비교 경로만 `verify_quality_v5.py`를 사용하도록 수정했다. 부모·선택 체크포인트·9개 모델 파일 inventory·평가 정체성·완료 ledger 검사는 유지하고 정확히 같은 특수 토큰의 확인된 직렬화만 허용한다. 변경된 토큰·추가 옵션·중복 JSON 키는 거부한다. 새 검사 13개와 기존 비교 요약 검사 24개가 통과했고 실제 선택 모델의 파일 검증도 통과했다. 이 진단에서 모델 가중치를 로드하거나 번역을 다시 생성하지 않았다. 원래 학습 코드·가중치·평가 결과는 그대로 보존했으며, 실제 관측 해시와 수정 범위는 `.training/comparisons/finance-quality-20260910/tokenizer-serialization-verification.json`에 기록했다.

그 뒤 별도 `infer_quality_v5.py`가 같은 검증기를 사용하도록 연결했고 실제 CPU FP32 직접 CLI도 정상 종료했다. [직접 CLI 기록](../../.training/verifications/v5-direct-cli-20260910.json)의 입력 6토큰·생성 5토큰·생성 5.906초·경고 0개는 짧은 새 예문의 실제 생성 결과다. 동결된 `infer_v5.py`와 최종 시험은 변경하지 않았으며 이 Marian CLI 성공을 Hy-MT2 앱 검증으로 표시하지 않는다.
