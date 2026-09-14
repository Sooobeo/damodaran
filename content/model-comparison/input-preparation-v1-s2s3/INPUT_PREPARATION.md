# S2 입력 준비 구현과 실제 토큰 검사

2026-09-13, [S1 동결 계약](../input-preparation-v1/CONTRACT.md)을 따르는 비교 전용 코드다. [문맥 선택기](../../../scripts/model-comparison/input_preparation_v1/context_selection.py), [의미 선택기](../../../scripts/model-comparison/input_preparation_v1/sense_selection.py), [입력 연결](../../../scripts/model-comparison/input_preparation_v1/prepare_inputs.py), [토크나이저 클라이언트](../../../scripts/model-comparison/input_preparation_v1/tokenizer_client.py)를 별도로 추가했다. 활성 등록·기존 사전54개·가중치·운영 DB·페이지/API·큐·캐시는 변경하지 않는다.

## 입력과 예산

고정 source 파일3개에서 개발16단위를 읽는다. 전역 버전/블록 소속·순서·원문 해시·공유 snapshot을 검증한 뒤 선택기에 `document`와 `targetBlockId`만 넘긴다. domain·평가 ID·자료 provenance는 선택/렌더링 후 보고에 붙인다. 평가 파일·과거 번역·개인 DB를 읽으면 거부하는 파일 읽기 감사도 적용한다. S1 전체 검증기는 평가 인용을 검사하므로 입력 준비 프로세스에 import하지 않고 별도 명령으로 실행한다.

| 구성 | 문맥 | 힌트 |
|---|---|---|
| C0 | 실제 앱의 제목2개·대상 포함±1블록·JS UTF-16 `.slice(0,10000)` | 등록54개와 기존 matcher |
| C1 | 저장된 heading stack·명시 부모·앞뒤 문단, 전체 블록 단위 예산 | C0와 동일 |
| C2 | C0와 동일 | 새8개념 사전에서 출현별 뜻 선택 |
| C3 | C1과 동일한 선택 정책 | C2와 동일, 최종 남은 문맥만 근거로 사용 |

C1/C3은 대상 중복을 제거하고 다음 heading을 넘지 않는다. 저장된 관계가 없는 목록 부모·PDF 문맥·표 의미는 추정하지 않는다. 표는 명시 `header=true`인 단순 격자의 좌표 근거만 지원한다. C0/C2의 기존 절단은 보존하며, UTF-16 절단이 짝 없는 surrogate를 만들면 UTF-8 토큰화에서 거부한다. 원문을 몰래 정규화하지 않는다.

같은 공통 지시·힌트 형식·고정 Jinja template을 사용한다. 대상+지시+힌트를 먼저 예약하고 완성 prompt 전체를 실제 native tokenizer로 센다. 상한은 `promptTokens + 4096 < 8192`, 즉4095다. C3에서 최종 문맥의 뜻 선택으로 예산이 넘으면 낮은 우선순위 문맥과 의존 힌트를 제거한다. 문맥 제거로 다른 뜻이 새로 확정되어도 힌트를 추가하지 않으며 sidecar에도 미출력 상태를 남긴다.

사전은8개념·24뜻에 대한 명시적 국소 관계의 제한 패턴이다. 금융11뜻만 prompt 힌트를 내보내고 일반/공통13뜻은 금융 오적용 억제에 사용한다. 부정·조건 범위, 암묵 지시, 미등록 복합구, 방향/순서 없는 정의 참조는 보류한다. 일반 의미 분석을 구현한 것으로 해석하지 않는다. 리뷰에서 발견한 같은 절의 다른 출현에 정의 참조가 새던 문제와 예산 제거 뒤 새 힌트가 추가되던 문제는 반례로 검사한다.

## 실제 토크나이저 경로

등록된 b10874의 `llama.dll`, 같은 GGUF와 tokenizer overrides를 사용한다. [pinned llama.h](https://raw.githubusercontent.com/ggml-org/llama.cpp/e2d2c0d6aa9b996d5d3a3c1d5e24c8c19728bb3d/include/llama.h)의 Windows x64 ABI를 확인한 [어휘 전용 child](../../../scripts/model-comparison/input_preparation_v1/vocab_worker.py)가 `vocab_only=true`로 모델의 어휘를 읽는다. `llama_context`를 만들거나 decode/생성 API를 호출하지 않는다. [해당 tokenize.cpp](https://raw.githubusercontent.com/ggml-org/llama.cpp/e2d2c0d6aa9b996d5d3a3c1d5e24c8c19728bb3d/tools/tokenize/tokenize.cpp)의 CLI는 `kv_overrides`를 model params에 전달하지 않으므로, Hy7의 EOS/EOT 보정을 적용하려고 DLL 경로를 사용했다.

전체 모델 파일/런타임52개 해시, BOS/EOS/EOT, 어휘128167개, EOG3개, `$`와 special token ID를 검증한다. `add_special=false, parse_special=true`이며 각 입력의 token IDs·count·UTF-8 해시·detokenize 왕복 결과를 남긴다. 부모는 기존 소유 Job 구현을 재사용해 child를 원자적으로 배정하고 동일 핸들로 종료/대기한다. BelowNormal·각 프로세스 commit1GiB 상한·최소 physical/commit 여유2GiB·시작120초/요청30초/전체600초 제한을 적용한다. 이 조건은 어휘만 읽는 소규모 검사 조건이며 S4 가중치 생성 조건으로 재사용하지 않는다.

첫 실측 smoke는9.547초·peak working set103,800,832바이트·생성0·context0·exit0이었다. [원시 기록](../../../.training/verifications/input-preparation-v1-s2-tokenizer-smoke-20260913/tokenizer-receipt.json)을 보존했다. 동일 native DLL을 직접 사용한 토큰 검증과 실제 서버 endpoint 대조는 구분한다. S4 첫 생성 전에 `/apply-template`·`/tokenize` 및 샘플링/모델 정체성 대조가 추가로 필요하다.

## 64개 준비 결과와 해석 제한

[완료 manifest](../../../.training/comparisons/input-preparation-v1/s2-prepared/attempt-002/manifest.json) SHA는 `b007e567881c1a6dfa556c775eda65916aa49d76559dedc14420c0e127866653`이다. 코드9개·산출물5개와64개 prompt/token IDs를 고정했다. 성공 실행은 실제 tokenization88요청·11.750초·peak working set103,772,160바이트·생성0·context0·exit0이다. 재사용한 토큰 측정은 동일 UTF-8 prompt 해시일 때만 적용했다.

| 구성 | 입력 수 | 최소/최대 토큰 | 토큰 합계 | 금융 힌트 행 | 예산 제외 |
|---|---:|---:|---:|---:|---:|
| C0 | 16 | 342/621 | 6352 | 3 | 0 |
| C1 | 16 | 281/539 | 5157 | 3 | 0 |
| C2 | 16 | 342/577 | 6185 | 0 | 0 |
| C3 | 16 | 281/539 | 4990 | 0 | 0 |

**이번 개발16에서 새 사전의 금융 힌트는0개다.** 뜻 선택기는 합성 반례에서는 금융/일반 선택을 수행하지만 이번 실제 입력의 국소 표현·부정/조건은 대부분 좁은 지원 범위 밖이다. C2의21개 출현 결정은 미지원11·모호8·복합구 중첩 억제1·비금융 억제1이었다. C2/C3의 사전 변화가 prompt에 나타난 것은 F02/F04의 기존3힌트 제거뿐이다. 따라서 이 자료로 새 금융 힌트 전달이나 문맥별 뜻 분별의 생성 효과를 실증했다고 주장하지 않는다. 이 사실을 숨기기 위해 동결 자료를 바꾸거나 보류를 강제 선택하지 않았다.

C0 대비 C1/C3은16대상 모두 달라지고 C2는2대상만 달라진다.64개의 논리적 입력 중 완전히 같은 prompt 문자열을 묶으면36개다. 이는 생성이나 품질 결과가 아니며 처리 정체성이 다른 구성의 출력을 자동 재사용할 수 있다는 뜻도 아니다. 후속 S4는 동결 계약의 재사용 조건과 출력 상한을 별도로 집행해야 한다.

첫 전체 시도 [attempt-001](../../../.training/comparisons/input-preparation-v1/s2-prepared/attempt-001/failure.json)은 native 토큰화와 종료 뒤 코드 해시 재검사에서 Path 인덱싱 오류로 실패했다. 이 기록은 보존했고, 해당 기술 오류 수정 후 새 attempt-002를 사용했다. 두 시도와 smoke 모두 번역 생성은0이다. [Node C0 문맥16/16 대조](../../../.training/verifications/input-preparation-v1-s2-context-check-20260913.json)와 별도 재생 검증은 실제 토큰 측정과 구분해서 보관한다.

[읽기 전용 재검증 기록](../../../.training/verifications/input-preparation-v1-s2-prepared-verification-20260913.json)은 코드9개·산출물5개·원문16개·입력64개를 확인했다. 기록된88개 native 요청의 text SHA→count를334번 조회해 선택기 전체를 재생하고, 첫 요청 순서·원문/문맥/힌트/prompt·최종 token IDs 해시가 동일함을 확인했다. 이 검증은 재토큰화가 아니며 새 native 호출0이다. [검증기](../../../scripts/model-comparison/input_preparation_v1/verify_prepared_inputs.py)는 평가/DB 읽기·프로세스 생성·native DLL 로드를 거부하며 별도 실패 반례5개도 확인한다.

## 실행

기존 Windows `.venv-training`의 Python3.11.9/Jinja3.1.6과 설치된 등록 모델/runtime을 사용한다. 새 설치나 자동 다운로드는 없다. 토크나이저 ABI 확인에 사용한 공식 소스는 `.training/verifications/input-preparation-v1-s2-tokenizer-source-20260913/`의 고정 사본과 SHA를 요구한다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 -m unittest discover -s scripts/model-comparison/input_preparation_v1 -p "test_*.py" -v
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_preparation_v1/prepare_inputs.py --run

# 이번 동결 attempt-002를 기록된 토큰으로 재검증한다(새 native 없음).
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_preparation_v1/verify_prepared_inputs.py
```

준비 명령은 `.training/comparisons/input-preparation-v1/s2-prepared/attempt-NNN/`을 새로 만들며 기존 산출물을 덮어쓰지 않는다. 모든16×4 입력이 예산을 통과해야 `prompts.jsonl`과 해시 manifest를 확정한다. 원문이 포함된 prompt·문맥·사전 근거는 Git 제외 로컬 산출물에만 저장한다. 한 단위라도 실패하면 실행 전체를 실패로 보존하고 불완전한 비교 입력을 완료로 내보내지 않는다. `--run`이 없으면 native를 시작하지 않는다.

앱 build/E2E·실제 번역 생성·의미 대조·질문 평가·독립 holdout·앱 등록은 이 명령의 검증 범위에 포함되지 않는다.
