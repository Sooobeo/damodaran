# 로컬 번역 후보 비교

후속 언어학적 비교는 [실행 원칙](../../content/model-comparison/LINGUISTIC_OPTIMIZATION_PROTOCOL.md)과 [익명 검토 계약](LINGUISTIC_REVIEW.md)을 따른다. 처리 시간보다 의미 정확성과 한국어 자연스러움을 우선한다. `linguistic-dev-20260910.jsonl`은 동결한 도우미 작성 개발 자료 18개이며, 최소대립쌍 6쌍과 문단 6개를 포함한다. 정규 용어 출현은 12개뿐이므로 금융 용어 90% 인증 자료가 아니다. 별도 `real-reading-check-20260910`의 6개 문단은 보존된 공개 원문에서 출력 관측 전에 선별했고 참조 번역은 도우미 작성으로 분리했다.

7B·12B의 완료된 실제 실행과 48개 익명 판단은 [후속 비교 기록](../../content/model-comparison/LINGUISTIC_COMPARISON_REPORT.md)에 있다. 개발 자료의 중요 오류는 각각 4/18·6/18, 공개 원문에서는 1/6·3/6이었다. 큰 후보의 설치·기능 시험·품질 검토 상태는 이 기록에서 별도로 확인한다. 새 번역을 학습 자료로 채택할 때는 [최소 수용 정책](../../content/model-comparison/TRAINING_QUALITY_FLOOR.md)을 따른다.

새 후보는 아래처럼 순서대로 실행한다. 출력 디렉터리는 새 경로여야 한다. 원문·참조·모델·실행기 해시가 맞지 않으면 중단하며, 앱 DB·제공자 등록·가중치 학습은 수행하지 않는다.

```powershell
# 설치는 최초 다운로드가 필요하며 실제 번역 실행과 별개다.
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/setup_translategemma27.py --model-size 12b
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/setup_translategemma27.py --model-size 27b
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/setup_hymt30.py --download

# 한 번에 한 모델만 실행한다. 같은 입력을 쓰되 각 모델의 원래 템플릿과 설정은 구분한다.
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_translategemma_large_v2.py --model-size 12b --input content/model-comparison/linguistic-dev-20260910.jsonl --threads 4 --output .training/comparisons/my-linguistic-run/tg12
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_translategemma_large_v2.py --model-size 27b --input content/model-comparison/linguistic-dev-20260910.jsonl --threads 4 --output .training/comparisons/my-linguistic-run/tg27
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_hymt30_v2.py --input content/model-comparison/linguistic-dev-20260910.jsonl --output .training/comparisons/my-linguistic-run/hy30 --run
```

TranslateGemma v1의 12B 시작 실패와 진단은 보존했다. v2는 실제 최종 EOG 집합 `1/106/212`를 확인하며, 원래 종료 토큰 `1/106`과 실행기가 추가한 `212`를 구분한다. 212로 끝난 출력은 원시 결과를 남기고 정상 완료로 인정하지 않는다. CPU의 추가 가중치 재배열 복사를 끄되 양자화 정밀도와 번역 설정은 유지한다. 사전 메모리 검사는 물리 메모리와 남은 Windows commit을 따로 검사하며, 실행 후 소유한 모델 프로세스를 종료하고 반환된 메모리를 기록한다. Hy30 v2는 지속적인 메모리 감시도 적용하지만 TranslateGemma v2는 사전·단계별 측정이며 지속 감시가 아니다. 설정만으로 메모리 부족이 불가능해졌다고 보증하지 않는다.

가중치 전체 상주가 어려운 경우의 v3는 별도 제한 실험이다. `--ram-budget-gib`는 8~12GiB 중 명시하며 기본은 12GiB다. 예산에 3GiB를 더한 물리 여유와 모델별 commit 여유가 있어야 시작하고, API 상한은 관측 예산보다 64MiB 낮춘다. 운영체제 전체 메모리나 파일 캐시의 상한이 아니며 반복 페이지 교체로 매우 느려질 수 있다. 기존 Q4 정밀도·번역 템플릿·설정은 유지하고, 소유 프로세스를 중지 상태로 생성한 뒤 한도를 적용한다. 예산·시간·메모리 조건 위반과 종료 실패는 실패로 남긴다. 먼저 짧은 기능 입력을 검사하고 모델별 시작·종료·출력 무결성을 확인한 뒤 개발 자료를 실행한다.

후속 v4는 v3 파일과 실행 결과를 보존한다. TranslateGemma v4는 느린 장문을 위해 요청 한도만 30분에서 2시간, 총 한도는 12시간에서 24시간으로 늘렸으며 시작 한도 30분과 메모리·번역 조건은 같다. Hy30 v4는 고정 native 실행기가 `top_k=-1`을 같은 비활성 의미의 `0`으로 보고하는 동작만 명시적으로 검증한다. 요청 샘플링은 바꾸지 않고 원본 보고값을 함께 보존한다. 이는 번역 품질 판정의 완화가 아니다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_translategemma_large_v4.py --model-size 27b --threads 4 --ram-budget-gib 8 --smoke --output .training/comparisons/my-paging-run/tg27-smoke-v4
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_hymt30_v4.py --run --smoke --ram-budget-gib 8 --output .training/comparisons/my-paging-run/hy30-smoke-v4
# 각 후보의 기능 검사 후 별도 실행: --smoke를 빼고 --input으로 고정 자료를 지정한다.
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_translategemma_large_v4.py --model-size 27b --threads 4 --ram-budget-gib 8 --input content/model-comparison/linguistic-dev-20260910.jsonl --output .training/comparisons/my-paging-run/tg27-dev-v4
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_hymt30_v4.py --run --ram-budget-gib 8 --input content/model-comparison/linguistic-dev-20260910.jsonl --output .training/comparisons/my-paging-run/hy30-dev-v4
```

원문 해독 결함은 모델 번역 오류와 분리한다. [R05 조사 메모](../../content/model-comparison/R05_SOURCE_DECODING_NOTE.md)의 문자 손상 자료는 이번 깨끗한 원문 후속 비교에서 제외했고 운영 원본·블록을 수정하지 않았다.

v4 실행 결과에는 별도 [v4 검수 도구](REVIEW_V4.md)의 `prepare_linguistic_reviews_v4.py`·`summarize_linguistic_reviews_v4.py`를 사용한다. 실제 원문 6개는 [후속 검토 계약](READING_REVIEW.md)과 `prepare_reading_reviews_v4.py`·`summarize_reading_reviews_v4.py`를 따른다. 기존에 확정한 7B·12B 패킷은 기존 검수 도구로 재검증한다. 개발 18개 집계기와 읽기 자료 6개 집계기를 섞지 않으며, 모든 원문·후보 판단을 검증한 뒤 모델 이름을 해독한다. 준비 시점의 해시 결속과 생산자 실행 종료 시점의 해시 증거 범위도 구분한다.

앞선 무료 로컬 전체 품질 비교와 현재 7B 앱 등록은 [2026-09-10 기록](../../content/model-comparison/QUALITY_LOCAL_REPORT.md)에 있다. `setup_hymt.py`는 고정 공식 Q8·Windows 실행기를 분리 설치하고, `run_hymt.py`는 원문만/문맥 사용 두 구성을 비교한다. `run_app_baseline.ts`와 `run_quality_marian.py`는 같은 새 문단에서 현재 앱 처리와 v5 원시 모델을 구분한다. 이전 실험과 출력은 유지하며 각 비교의 실제 완료 여부는 해당 기록을 확인한다.

네 구성의 실제 결과가 모두 완성되면 다음 명령으로 수치 지표와 익명 원문 대조 자료를 만든다. 코드 보존은 완료한 Argos/Marian 실행 직후, 해당 실행 코드가 바뀌기 전에 수행한다. 실행 결과의 코드 SHA와 동일한 바이트만 보존하며 나중의 코드 변경을 과거 결과로 소급하지 않는다. 아래 결과 디렉터리는 이미 존재하면 동일성을 확인하고 덮어쓰지 않는다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/code_snapshot.py --run-directory .training/comparisons/finance-quality-20260910/argos-app
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/code_snapshot.py --run-directory .training/comparisons/finance-quality-20260910/marian-v5
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/summarize_quality.py --input .training/comparisons/finance-quality-20260910/dataset.jsonl --results .training/comparisons/finance-quality-20260910 --output .training/comparisons/finance-quality-20260910/prepared-review --marian-run-id finance-v5-terminology
# 익명 자료 24개 × 네 출력의 대조를 완료한 후, 불변 prepared-review 폴더 밖에 검토본을 저장한다.
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/summarize_quality_reviews.py --prepared .training/comparisons/finance-quality-20260910/prepared-review --reviews .training/comparisons/finance-quality-20260910/review-a.jsonl .training/comparisons/finance-quality-20260910/review-b.jsonl --output .training/comparisons/finance-quality-20260910/assistant-review-summary.json
```

검토자는 모델 이름을 해독하는 `review-key.json`과 지표를 열기 전에 원문·문맥과 번역을 대조한다. 자동 수치·도우미 의미 대조·사람 검수를 구분하고, 96개 판단에 미해결 항목이나 원문/출력 해시 변경이 있으면 완료 집계를 거부한다. 이 집계기는 새로운 품질 통과 기준이나 앱 승격 결정을 자동으로 만들지 않는다.

사용자 요청에 따른 탐색용 비교 도구다. 기존 Argos·finance-v3 FP32·TranslateGemma 4B 제3자 Q4_K_M 변환본을 새로운 [12개 문단](../../content/model-comparison/README.md)으로 비교한다. 도우미 작성 미검수 자료이며 독립 최종 시험·앱 등록·새 가중치 학습을 수행하지 않는다.

Windows PowerShell에서 저장소 루트를 기준으로 실행한다. 기존 `.venv-translation`과 `.venv-training`이 준비되어 있어야 한다. Python 패키지를 추가 설치하거나 기존 환경을 바꾸지 않는다. 모델 로드는 메모리를 사용하므로 아래 명령을 순서대로 실행한다.

```powershell
# 공개 llama.cpp Windows 바이너리와 제3자 GGUF 설치: 최초에는 네트워크 필요
py -3.11 -X utf8 scripts/model-comparison/setup_candidate.py

# 이미 완료한 출력은 덮어쓰지 않음. 재측정에는 별도 출력 디렉터리 사용
py -3.11 -X utf8 scripts/model-comparison/run_baselines.py --help
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_candidate.py --device cpu --help

# 기존 환경에서 source만 번역. 학습/앱 DB/유료 API 호출 없음
.venv-translation/Scripts/python.exe -X utf8 scripts/model-comparison/run_baselines.py --model argos --input content/model-comparison/finance-probe-20260909.jsonl --output-dir .training/comparisons/finance-probe-20260909
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_baselines.py --model marian --input content/model-comparison/finance-probe-20260909.jsonl --output-dir .training/comparisons/finance-probe-20260909 --run-id finance-v3
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_candidate.py --device cpu

# 3개 모델의 12개 결과가 모두 있어야 지표·모델 이름을 숨긴 대조 자료 생성
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/summarize.py
```

실제 결과와 오류 대조는 [비교 보고서](../../content/model-comparison/COMPARISON_REPORT.md)에 있다. 후보의 기본 장치는 CPU다. Intel Arc 140V의 Vulkan 실행은 첫 번역에서 `ErrorDeviceLost`로 실패했으며 정상 GPU 속도를 측정한 것으로 보고하지 않는다. 실패 로그는 `.training/comparisons/finance-probe-20260909/translategemma-runtime-raw-completion.log`에 보존한다. 초기에 llama.cpp가 TranslateGemma의 언어 필드가 있는 chat 템플릿을 자동 해석하지 못한 시작 실패도 `translategemma-runtime.log`에 보존한다. 실제 CPU 실행은 GGUF의 원래 템플릿을 Jinja로 렌더링한 토큰을 `/completion`에 제공한다. 서버 chat 템플릿은 이 원시 요청에 적용되지 않는다.

GGUF 출처는 [mradermacher/translategemma-4b-it-GGUF](https://huggingface.co/mradermacher/translategemma-4b-it-GGUF/tree/35a7486e128b19642cdc72d7b91b21ba388aaf42), 기반 모델 저작자는 [Google Translate](https://huggingface.co/google/translategemma-4b-it), 이용 조건은 [Gemma](https://ai.google.dev/gemma/terms)다. Google의 원본 FP32/BF16 가중치와 같은 품질이라고 가정하지 않는다. llama.cpp는 [b10874 공식 Windows Vulkan 배포](https://github.com/ggml-org/llama.cpp/releases/tag/b10874)를 사용한다. 두 배포 파일을 게시된 SHA-256과 대조하고 설치 manifest를 남긴다.

출력은 `.training/comparisons/`에 저장하며 Git·public·SQLite 백업에 포함되지 않는다. 다운로드한 실행기와 모델은 manifest로 재설치할 수 있다. 실측 출력·의미 대조·실패 기록을 보존하려면 비교 실행 폴더를 별도로 보관한다. 고정 데이터·모델 해시가 같아도 하드웨어 부하와 실행 조건에 따라 속도와 결과가 달라질 수 있다.

숫자 토큰 검사·chrF·BLEU·용어 표기 일치는 의미 품질이나 수식 보존의 보증이 아니다. 같은 원문에 대한 모델별 출력과 누락·부정·관계·기호를 별도로 확인한다. 기준모델의 문장 분할/beam/출력 상한과 후보의 문단/greedy/출력 상한이 달라 실사용 후보 비교로만 해석한다.
