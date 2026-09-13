# 무료 로컬 독립 번역 품질 추정

이 도구는 기존 번역의 검토 우선순위를 평가하는 별도 CPU 실행기다. 번역을 생성하거나 수정하지 않고 `source + translation`만 입력한다. 참조 번역·생성 모델의 토큰 확률·생성 모델 자기평가·유료 API를 사용하지 않는다. 기존 Argos/학습 환경, 운영 SQLite, 동결 번역·판정은 변경하지 않는다.

## 후보 선택 근거

| 후보 | 공식 입력·언어·이용 조건 | 이번 선택 |
|---|---|---|
| [Unbabel/wmt22-cometkiwi-da](https://huggingface.co/Unbabel/wmt22-cometkiwi-da) | 원문과 번역만 입력, 한국어 포함, CC-BY-NC-SA-4.0. 현재 gated 저장소로 계정 로그인·조건 동의 필요. 고정 후보 revision `1ad785194e391eebc6c53e2d0776cada8f83179a`, 체크포인트 2,260,734,705바이트 | 계정 조건이 충족되지 않아 다운로드·실행하지 않는다. |
| [Unbabel/wmt20-comet-qe-da](https://huggingface.co/Unbabel/wmt20-comet-qe-da) | 원문과 번역만 입력, 한국어 포함, Apache-2.0, ungated. 공식 설명은 점수를 오류 확률이 아닌 잡음 있는 순위 지표로 해석하도록 한다. | 초기 실측 후보. COMETKiwi라는 이름으로 표시하거나 동급 성능을 가정하지 않는다. |
| [COMETKiwi XL](https://huggingface.co/Unbabel/wmt23-cometkiwi-da-xl) | 3.5B, 공식 최소 GPU 메모리 15GB, gated·CC-BY-NC-SA-4.0 | 다운로드하지 않는다. 공유 GPU 용량을 추가 RAM으로 계산하지 않는다. |
| [XCOMET XL](https://huggingface.co/Unbabel/XCOMET-XL) | 오류 구간을 반환하는 별도 계열 | 초기 후보가 아니다. 구간 탐지 성능·정답 없는 경로·자원 실측 없이 가져오지 않는다. |

Windows CPU 경로는 [PyTorch 설치 문서](https://pytorch.org/get-started/locally/)와 [COMET 공식 CPU/참조 없는 평가 사용법](https://github.com/Unbabel/COMET)을 따른다. 실제 호환성과 성능은 설치 성공, 모델 로드, 48개 추론, 교정 평가로 나누어 확인한다. COMETKiwi를 사용할 수 없는 사유를 숨긴 자동 대체 기능은 없다.

## 정체성과 저장 경계

- 선택 후보: `Unbabel/wmt20-comet-qe-da`, revision `2e7ffc84fb67d99cf92506611766463bb9230cfb`.
- 공식 체크포인트 SHA-256: `05d892bf4a3e34b9a4de239109387d43107b2a8c55ad34b73a929ca6c1ede24e`, 크기 2,277,497,201바이트.
- encoder tokenizer/config: `FacebookAI/xlm-roberta-large`, revision `c23d21b0620b635a76227c604d44e43a9f0ee389`. 별도 encoder 가중치를 중복 다운로드하지 않는다.
- `.venv-qe/`: Windows CPython 3.11 전용 격리 환경. CPU PyTorch 2.8.0, COMET 2.2.7, Transformers 4.57.6. 실제 전체 버전은 `.translation/qe/requirements.lock.txt`, 설치한 wheel의 URL·SHA는 `install-torch.json`, `install-packages.json`으로 보존한다. `setuptools==81.0.0`은 기존 torchmetrics의 `pkg_resources` 의존과 맞춘다. [공식 저장소의 관련 문제 보고](https://github.com/Unbabel/COMET/issues/267).
- `requirements.windows.lock.txt`는 이번 Windows 설치에서 사용한 51개 wheel의 정확한 버전·SHA를 포함한다. 이후 `setup.py`도 이 파일을 `--require-hashes`로 사용한다. Windows의 간헐적인 TLS 연결 끊김은 인증서 검증을 유지한 curl TLS 1.2와 제한된 재시도로 처리했다. 최초 다운로드가 끝난 뒤 평가에는 네트워크가 필요하지 않다.
- `.translation/qe/candidates/wmt20-comet-qe-da/`: 공식 파일 원본과 설치 manifest. **설치됨은 앱 등록됨이 아니다.**
- `.training/quality-evaluation/dev48-v1/`: 기존 48개 원문/번역 입력·조정 완료 도우미 판정·사전 기준·참조 파일 해시.
- `.training/quality-evaluation/<새 실행 폴더>/`: 실행 코드·입력 정체성, 원시 점수, 시간/메모리, 교정 기록. SQLite 백업에 포함되지 않는 별도 실험 결과다.
- `.translation/qe/manifest.json`: 교정 기준을 모두 통과한 경우에만 `register.py`가 새로 만든다. 기존 manifest를 덮어쓰지 않는다.

## 실행

Windows PowerShell에서 아래와 같이 실행한다. 최초 설치만 네트워크를 사용하고, 이후 backend는 로컬 경로와 `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `local_files_only=True`를 강제한다. 임의 hub fallback은 없다. [Transformers 오프라인 모드](https://huggingface.co/docs/transformers/v4.57.1/en/installation#offline-mode).

```powershell
py -3.11 -X utf8 scripts/local-qe/setup.py
py -3.11 -X utf8 scripts/local-qe/prepare.py
.venv-qe/Scripts/python.exe -X utf8 -m unittest discover -s scripts/local-qe -p "test_*.py"
```

이미 생성한 `dev48-v1`을 덮어쓰거나 번역 모델을 다시 돌리지 않는다. 다음 실제 실행은 다른 무거운 모델이 종료되고 메모리 여유를 확인한 뒤 수행한다.

```powershell
.venv-qe/Scripts/python.exe -X utf8 -u scripts/local-qe/score.py --run --output .training/quality-evaluation/wmt20-dev48-next
py -3.11 -X utf8 scripts/local-qe/calibrate.py --run .training/quality-evaluation/wmt20-dev48-next --policy scripts/local-qe/policy-product-95-v2.json --output .training/quality-evaluation/wmt20-dev48-next/calibration-product-95-v2.json
py -3.11 -X utf8 scripts/local-qe/register.py --run .training/quality-evaluation/wmt20-dev48-next --evidence .training/quality-evaluation/wmt20-dev48-next/calibration-product-95-v2.json
```

실패한 실행 폴더를 지우거나 재사용하지 않는다. 새 실행이 필요하면 새 폴더명을 사용한다. 생성된 CLI `summary.json`의 `engineClosed`는 모델 객체·lock 정리를 뜻하고 실제 Python 프로세스 종료는 호출자의 exit code와 함께 확인한다. CPU 모델은 이 Python 프로세스 안에 존재하며 별도 native 서버를 만들지 않는다.

로드 전에 물리/commit 여유 각각 최소 8GiB를 요구한다. 다른 `llama-server`가 있으면 QE를 시작하지 않는다. 실행 중 작업 집합 8GiB 초과 또는 시스템 물리/commit 여유 512MiB 미만 3초 지속, 전체 1시간 초과 시 자신의 QE 프로세스만 종료한다. 감시 실패도 종료 처리한다. 강제 종료 기록은 CLI 실행 폴더의 `emergency.json`에 남긴다. 다른 앱 종료·OS 캐시 비우기는 하지 않는다. 메모리 상한은 실제 관측 guard이며 OS 전체 메모리 제한은 아니다.

runtime lock은 PID와 프로세스 생성시각을 함께 보존한다. Windows 이름 있는 mutex 안에서 생성·회수·해제를 직렬화한다. 이전 프로세스가 존재하지 않는 것이 확인된 경우에만 남은 lock을 회수한다. 살아 있는 소유자, PID 재사용, 접근 거부/불명확한 소유권, 손상된 metadata는 구분해 거부한다. 외부 강제 종료 후에도 다른 프로세스의 lock을 임의로 지우지 않는다.

## 사전 교정 기준과 한계

48개 출력은 개발 원문 18개·실제 읽기 원문 6개의 각 2개 번역이다. 기존 조정판정의 중요한 의미 오류는 14개, 알려진 필수 사례는 6개다. 모든 판정은 도우미 판정이며 사람 검수·새 최종 시험이 아니다. 참조 번역이나 판정은 평가 모델에 전달하지 않는다.

최초 점수 관측 전에 고정한 개발 기준은 중요한 의미 오류 재현율 ≥85%, 경고 정밀도 ≥60%, 전체 경고율 ≤50%, `equity` 의미 오류 2개·Treasury bills 만기 구분 2개·72 나눗셈 반전 1개·영구 성장 조건 누락 1개 전체 경고다. 이 원래 정책과 당시 결과는 그대로 보존한다. 낮은 점수를 위험으로 간주하고 `score <= threshold`를 적용한다. 조건을 통과한 후보 임계값 중 경고 정밀도를 우선하고 같은 정밀도에서는 경고율이 낮은 값을 선택한다.

실패하면 원시 점수와 진단 임계값만 보존하고 앱 manifest를 만들지 않는다. 전체를 경고하여 재현율만 높인 후보도 실패한다. 같은 개발 자료로 임계값을 선택하고 측정하므로 결과는 낙관적일 수 있다. 24개 공유 원문, 일반 영역 2개라는 한계 때문에 독립 정확도 인증으로 사용하지 않는다. 모델/분량/문서와 기존 오류 유형별 결과를 따로 남긴다.

## 후속 제품 기준과 기존 점수의 재해석

사용자가 기준 설계를 위임한 뒤 정한 현재 제품 정책은 [product-qe-95-v2](policy-product-95-v2.json)이며, [학습 준비 기준](../../content/model-comparison/LEARNING_READINESS_BASELINE_20260911.md)의 문단 위험 gate를 구현한다. major/critical 의미 오류의 문단 위험 경고 재현율 ≥95%, 경고 정밀도 ≥95%, 지정한 6개 오류 100% 경고를 요구한다. 별도 50% 경고율 상한은 새 정책에 두지 않는다. 95%는 보편적인 학술 정확도 수치가 아니라 제품이 선택한 수용 기준이다. 밑줄은 별도로 의미 탐지 성능과 offset 기술 유효성 100%를 검증해야 하며, 구간을 반환하지 않는 이 후보가 그 조건을 통과했다고 표시하지 않는다.

단일 공통 임계값으로 전체뿐 아니라 **각 번역 모델·구성, 각 dev/reading 집합, 모델·구성 × 집합 교차집합**이 각각 95/95를 통과해야 한다. 각 임계값의 `perBaselineGate`에 분자·분모·판정을 보존한다. 관측된 모델과 집합의 조합이 비어 있거나, 필요한 양성 정답 또는 경고가 없어 분모가 0이면 비율은 `null`, 상태는 `unassessed_requires_additional_validation`로 두고 추가 평가 전 등록을 거부한다. 평균으로 특정 baseline의 실패를 상쇄하지 않는다.

이 도구의 단위는 **중요 오류가 하나 이상 있는 문단별 번역 출력**이다. 오류 3개가 있는 문단에 경고 하나를 붙이면 문단 TP는 1개이며, 오류 3개를 모두 찾아냈다는 뜻이 아니다. 오류 유형별 결과도 그 유형이 있는 문단이 경고됐는지의 대리지표다. 개별 오류별 탐지·원인 식별·빨간 구간의 의미 정확성은 미평가로 남긴다. 완벽한 문단 경고 성능을 모든 오류 탐지나 밑줄 정확도로 환산하지 않는다.

`calibrate.py --policy`를 명시해야 새 정책을 적용한다. 임의로 기준을 완화한 JSON은 거부한다. 원사전정책과 적용정책의 경로·SHA, 새 모델 추론을 하지 않았다는 사실, 사후 재해석임을 새 결과에 기록한다. 기존 `dev48-v1/policy.json`, `wmt20-dev48-v2/calibration.json`, 점수와 판정은 변경하지 않는다. 전체 평균만 gate로 쓰던 [제품 정책 v1](policy-product-95-v1.json)과 그 재해석도 보존하고 분석 당시 코드 6개를 별도 snapshot에 남겼다. `register.py`와 bridge는 구85/60 정책이나 제품 정책 v1만 통과한 근거로 신규 등록·실행하는 것을 거부한다.

기존 48개 실측의 [제품 정책 v1 재해석](../../.training/quality-evaluation/wmt20-dev48-v2/calibration-product-95-v1.json)과 [각 baseline을 검사한 v2 재해석](../../.training/quality-evaluation/wmt20-dev48-v2/calibration-product-95-v2.json)은 모두 실패했다. 지정 오류 전체 경고 시 전체 재현율 100%, 정밀도 31.11%, 경고율 93.75%이며, 95/95를 만족하는 임계값이 없다. 이는 최초 실험의 사전 정책을 바꾼 결과나 추가 추론 결과가 아니다.

## 앱 NDJSON 계약

앱은 등록 manifest의 `pythonPath`, `bridgePath`를 실행하고 요청마다 한 줄 JSON을 보낸다. EOF가 도착하면 bridge가 모델을 해제하고 종료한다. stdout은 NDJSON만, 라이브러리 로그는 stderr만 사용한다.

```json
{"id":"request-id","source":"English source","translation":"현재 한국어 번역","context":""}
```

성공 응답에는 `id`, `status:"completed"`, `score`(유한한 원시 수치), `risk:"review"|"no_findings"`, `modelId`, `modelRevision`, `modelHash`, `modelIdentity`, `calibrationVersion`, `sourceSha256`, `translationSha256`, `contextSha256`, `contextUsed:false`, `spans:[]`가 들어 있다. 미등록·자원 부족·번역 모델 실행 중이면 `status:"unavailable"`, 나머지 오류는 `status:"failed"`, 둘 다 `risk:"unknown"`, `score:null`이다. score가 확률이라는 뜻은 아니다.

이 후보는 오류 구간 탐지 기능이 없으므로 단어 offset이나 오류 이유를 만들어 내지 않는다. 낮은 점수는 문장/문단 검토 안내만 가능하다. 향후 구간 지원 시 offset은 JavaScript에 맞춘 UTF-16 code unit으로 고정하고 실제 substring·범위·surrogate 경계를 검사한다. source-target 정렬을 제공하지 않는 후보에서 정밀한 영어 구간을 추정하지 않는다. 입력이 모델의 token limit을 넘으면 조용히 자르지 않고 실패 상태를 반환한다.

검수본 우선 표시, 이력 DB, 캐시, 번역 모델의 안전한 반환, 비동기 큐는 앱 계층의 책임이다. 자동 점수는 사용자 검수 완료나 무오류 보증을 만들지 않는다.

## 2026-09-11 실제 결과 — 미등록 유지

설치·패키지 import·`pip check`와 실제 CPU 48개 추론은 완료했다. 원본 체크포인트 로딩의 첫 `wmt20-dev48-v1` 시도는 RuntimeError로 0개에서 실패했다. 기존 Transformers가 저장하던 `position_ids` 파생 buffer를 현재 buffer와 dtype·shape·값까지 대조하고, 일치한 buffer 하나만 로딩 사본에서 제외한 뒤 모든 학습 파라미터를 `strict=True`로 로드하는 호환 처리를 추가했다. 원본 checkpoint 파일은 수정하지 않았으며 두 번째 실행은 48개 모두 완료했다.

| 항목 | 실제 관측 |
|---|---:|
| 완료 출력 | 48/48, 입력 잘림 없음 |
| 전체 실행 / 추론 합계 | 141.516초 / 121.438초 |
| peak 작업 집합 | 5,250,007,040바이트, 약 4.89GiB |
| 관측 최저 가용 물리 메모리 | 9,900,019,712바이트, 약 9.22GiB |
| 지정된 오류 6개를 모두 경고할 때 경고 수 | 45/48, 93.75% |
| 그때 중요한 오류 재현율 / 경고 정밀도 | 14/14, 100% / 14/45, 31.11% |
| 경고를 절반 이하로 제한할 때 최대 재현율 | 6/14, 42.86%; 경고 24개 중 18개는 중요 오류가 없는 번역 |
| 도입 판정 | **사전 기준 미달, 앱 등록 거부** |

나눗셈 방향을 보존한 Hy7의 `REAL26-005` 점수는 약 0.34356, 방향을 뒤집은 TG12의 점수는 약 0.40215였다. 높은 점수를 더 좋은 번역으로 해석하는 이 모델은 해당 비교에서도 잘못된 순서를 매겼다. 0.5928683876991272는 알려진 오류 전체를 포함하기 위한 **실패 후보의 진단 임계값**이며 앱에 적용할 임계값이 아니다. 경고를 거의 모든 번역에 붙여 높은 재현율을 만드는 결과를 성공으로 채택하지 않는다.

근거는 [48개 실행 summary](../../.training/quality-evaluation/wmt20-dev48-v2/summary.json), [원시 점수](../../.training/quality-evaluation/wmt20-dev48-v2/scores.jsonl), [교정 결과·전체 임계값 곡선](../../.training/quality-evaluation/wmt20-dev48-v2/calibration.json), [실행시점 코드 스냅샷 manifest](../../.training/quality-evaluation/wmt20-dev48-v2/snapshot-manifest.json)에 보존한다. 원시 점수 SHA-256은 `786160e66e27956914d5b55daaa4a03502dc4df6ee1c3a8eb8037fdc2d6f770c`다.

실제 실행의 Python exit code는 0이고 runtime lock은 해제됐다. 이후 소유 lock의 안전한 회수·protocol·구정책 등록 차단·각 baseline 검사 등을 보완했으며 합성/계약 검사 총 27개를 통과했다. 전체·모델·집합 평균은 통과하지만 특정 교차집합이 실패하는 반례와 분모0도 검사했다. 이 후속 수명 코드로 실제 모델을 다시 실행하지 않았고, 실행시점 코드 바이트는 따로 보존했다. 테스트 점수를 실제 QE 품질로 사용하지 않는다. `.translation/qe/manifest.json`은 생성하지 않았고 현재 bridge는 `qe_not_registered`를 반환한다. 이 결과는 COMETKiwi의 로컬 품질 결과가 아니라 별도 구형 COMET-QE 후보의 실패 결과다.

## 별도 앱 실측 준비 CLI

[`../verify-quality-app.ts`](../verify-quality-app.ts)는 운영 원문을 읽기 전용 SQLite transaction으로 읽고 새 `.training/quality-evaluation/` DATA_DIR에 seed·schema3와 불변 원문/블록/자산만 복제한다. 개인 기록·검수·기존 번역을 가져오지 않는다. 원본 바이트·행·블록 해시와 공개 원문 부분의 snapshot ID를 남기며, source 경로 이탈과 기존 폴더 덮어쓰기를 거부한다. 외부 다운로드·재추출·원본 DB 마이그레이션은 하지 않는다.

```powershell
node --import tsx scripts/verify-quality-app.ts --prepare-only --data-dir .training/quality-evaluation/app-hy7-quality-next --source-data-dir data --pdf-data-dir .training/verifications/hymt-app-v2-1789025039143 --pdf-version 64b5864e-31e1-4433-ae3a-8fee1793b960 --pdf-pages 3:3
node --import tsx scripts/verify-quality-app.ts --run --data-dir .training/quality-evaluation/app-hy7-quality-next --timeout-minutes 30
```

준비 당시 운영 DB는 schema2이고 PDF v2가 없었으므로 확인된 기존 격리 QA의 PDF v2를 명시했다. 기본 `3:3`은 **PDF 3페이지 한 장**의 텍스트 전블록이며 세 페이지를 뜻하지 않는다. `--run`은 계산 슬롯·메모리 확인 후 현재 등록 Hy7로 HTML 3문단과 PDF 범위 전체를 실제 생성·저장하고, 유효 번역 캐시와 품질 작업·미등록 의미 검사 상태를 검증한다. 완료/실패 시 소유 번역 및 QE 프로세스 종료를 await하고 격리 `derived/quality-app-report.json`에 기록한다. 자동 검사 경고가 있으면 기존 출력을 보존하고 재생성 없이 한계를 보고한다. 모델 의미 품질의 사람 검수나 95% 수용 검사를 대신하지 않는다.

## 별도 Qwen3.5 후보의 실패

2026-09-11에 별도 Qwen3.5-9B Q4_K_M v4·CPU4/BelowNormal·문맥4096·캐시 checkpoint3·중요 경고 매핑v2를 실행했다. 전체48 계획 중12개 생성 후 절전과 겹친 관측 공백으로 요청guard가 발동했으며 소유 종료와 최종 파일 무결성은 확인했다. 완료12개의 기술 증거를 전수 재검증한 뒤 기존 중요 오류3개에 경고가 없음을 확인했다. 나머지를 모두 맞혀도 재현율 상한11/14=78.57%이므로 같은 구성의36개 추가 추론은 생략한다. 원래 실패를 지우거나 매핑을 사후 바꾸지 않았다. [상세 보고서](../../content/model-comparison/QWEN35_V4_REVIEW_20260911.md)에 원시 결과·상한·자원·절전·구간 미평가를 연결했다. 이 후보는 현재 COMET bridge 계약과 다른 JSON 평가기이며 앱에 등록하지 않았다.
