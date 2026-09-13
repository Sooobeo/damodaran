# TG27 재시도 자원·관측 계획

2026-09-11 · `tg27-resource-retry-plan-v1` · **설계만 완료, 새 모델 실행 없음**

최신 사용자의 “메모리, CPU 최적화하면서 진행” 지시에 따라 자원 설정을 명시한 새 실행을 허용한다. [기존 실행 원칙](LINGUISTIC_OPTIMIZATION_PROTOCOL.md)의 “추가 속도 튜닝은 중단”은 당시 결정으로 보존한다. 기존 실패·동결 데이터·품질 기준을 바꾸지 않으며, 느리다는 이유로 TG27을 제외하지 않는다. 목표는 여전히 **개발 18개 + 실제 읽기 6개**다. 사용자 앱 종료, 메모리/standby cache 비우기, 페이지 파일·OS 전원 정책 변경은 하지 않는다.

## 기존 실패에서 확인한 것과 미확정 원인

[기존 summary](../../.training/comparisons/linguistic-dev-20260910/translategemma-27b-paging-v4/summary.json)는 `failed`, `count=0`, `expectedCount=recordedCount=18`이다. 시작/EOG·토큰 검증은 통과했고 모델 load 기록은 11.016초다. 첫 요청에서 `ConnectionResetError`, 감시 결과 `request_time_limit`, `childProcessStopped=true`가 남았다. 생성 최종 무결성 통과가 아니다.

메모리 기록에는 **676.937→4664.390초(3987.453초)**, **4669.484→21874.093초(17204.609초)**의 관측 공백이 있다. 수면, 감시/스케줄링 정체, 메모리·디스크 압박, 연결 종료의 최초 원인과 인과 순서는 미확정이다. 7200초 요청 제한은 감시 코드가 다시 실행돼야 집행되므로 관측 공백을 정상적인 연속 추론 시간으로 해석하지 않는다. 누적 page fault 약 1.177억 회도 디스크 hard fault 횟수나 읽기 바이트가 아니다.

| 보존 근거 | SHA-256 |
|---|---|
| 기존 `summary.json` | `1cf836d433010e45342c6596ab4bdb59091efe9110575241ab03d5a699eb8aaa` |
| 기존 `predictions.jsonl` | `7d0579fca4fa4d99345b6036dcababf2780d64f0122a00e2441e04d82a1ea641` |
| 기존 `memory-samples.jsonl` | `50890d0365e9a8fdad28ff45ac67ee6a4ac141dc390805d609433a2be98eddb4` |
| [현재 v4 실행기](../../scripts/model-comparison/run_translategemma_large_v4.py), 실패 당시와 같음 | `6277e73c9ee9f8d35c79fa197028eb8a5345a77078779802d81405845c0a7a2d` |

## 유지할 실행 계약과 예산 선택

CPU 4스레드, native 프로세스 **BelowNormal 실측 확인**, 단일 모델/단일 slot, 원래 source-only 입력·Q4 가중치·template·EOS·sampling을 유지한다. context 2048, 출력 상한 768, mmap·no-repack·CPU 실행을 유지한다. BelowNormal은 우선순위이며 CPU 사용률 hard cap이 아니다. 실행 중 budget을 바꾸지 않는다.

v4의 제한은 startup **1800초**, 요청 **7200초**, 전체 **86400초**다. 0.5초 감시/약 5초 기록, 물리 또는 commit 여유가 512MiB 미만으로 3초 지속하거나 선택 WS 예산을 넘으면 소유 child를 중단한다. WS는 프로세스 상주 메모리이며 전체 commit·OS 파일 캐시·다른 앱의 합계 상한이 아니다. [Microsoft 작업 집합 설명](https://learn.microsoft.com/en-us/windows/win32/psapi/working-set-information)

시작 직전 3초 간격 두 관측의 **작은 가용 RAM**을 사용한다. 이 계획의 후보 선택 규칙은 아래 조건을 만족하는 허용값 중 가장 큰 값이다. 이는 강제 비상주 압박을 줄일 수 있는지 확인할 **사전 실험 선택**이며, 큰 budget이 기존 실패나 속도를 개선한다는 실측 근거는 아직 없다. 어떤 값도 충족하지 않으면 기존 앱을 건드리지 않고 대기한다.

| 선택 budget GiB | 필요한 시작 가용 물리 GiB | API WS 상한 MiB |
|---:|---:|---:|
| 8 | 11 이상 | 8128 |
| 9 | 12 이상 | 9152 |
| 10 | 13 이상 | 10176 |
| 11 | 14 이상 | 11200 |
| 12 | 15 이상 | 12224 |

모든 값에 별도로 가용 commit **4,261,412,864바이트(3.96875GiB)** 이상을 요구한다. 이는 기존 최대 KV 추정 0.96875GiB + private 여유 3GiB다. 위 선택 후에도 v4 자체의 설치 검증 전·실제 시작 직전 검사를 통과해야 한다. 현재 메모리가 표를 만족한다고 다음 시점까지 예약되는 것은 아니다.

## 18+6 순서와 관측

1. root가 앞선 소유 모델의 종료와 단일 heavy slot을 확인한다. 사용자 앱/운영 worker를 새로 시작하거나 임의 종료하지 않는다. 입력·실행기·도우미 해시, 선택 budget, 두 RAM/commit 관측, 목적·중단 기준을 새 실행 기록에 남긴다.
2. 별도 smoke/첫 항목 시험을 다시 만든 뒤 18개를 중복 실행하지 않는다. **새 개발 18개 실행의 첫 요청 자체를 관측 지점**으로 삼는다. 기존 0/18 디렉터리는 재사용하지 않는다.
3. 시작 증거에서 native PID+생성시각을 대조한 뒤 BelowNormal·4스레드·WS 상한을 재조회한다. 도우미가 Python을 BelowNormal로 시작했다는 기록만으로 native 우선순위까지 확인했다고 쓰지 않는다. 불일치하면 소유 실행을 정상 중단하고 원인을 검토한다.
4. load 완료 직후, 첫 요청 초반, 첫 결과 직후와 이후 행 경계에서 아래 항목을 함께 본다. 추가 OS counter는 짧은 3~5초 간격 두 샘플로 제한하고, 고빈도/장시간 감시 프로세스를 중복 추가하지 않는다. [Microsoft counter 수집 범위](https://learn.microsoft.com/en-us/windows/win32/perfctrs/about-performance-counters)
5. 18개 종료 후 `status/count/expectedCount`, 응답·prediction 해시, `integrityVerified`, guard/cleanup 오류, `childProcessStopped`와 실제 소유 프로세스 소멸을 확인한다. 물리/commit을 다시 읽고 동일 budget이 맞으면 새 디렉터리에서 읽기 6개를 실행한다. 여유가 부족하면 읽기 6개는 대기하며, 예산 변경이나 18개 재실행을 자동으로 하지 않는다.

| 관측 | 현재 v4에서 얻는 것 / 해석 제한 |
|---|---|
| 처리 진행 | 완료 행 수·현재 대기 행, UTC·monotonic 시각, 요청 전후 시간. 현재 v4는 응답 뒤에만 행 진행을 출력하므로 요청 중 정확한 토큰 진행·첫 토큰 시간은 **미관측**이다. |
| CPU | 소유 child의 CPU seconds 차이/실제 관측 간격, 논리 CPU 수로 나눈 전체 사용률, 우선순위. CPU가 움직인다는 사실만으로 정상 번역 진행을 확정하지 않는다. |
| 메모리 | 물리/commit 여유, WS·peak WS·private bytes, 선택/API 상한, 신규 샘플 간격. 공백을 이전 값으로 보간하지 않는다. |
| paging | 프로세스 page-fault 증가율은 soft+hard 합계다. 시스템 `Page Reads/sec`, `Pages Input/sec`와 디스크 읽기/지연을 함께 보되 시스템 수치를 TG27에 전부 귀속하지 않는다. 프로세스 IO 읽기 0만으로 mapped-file hard fault 0을 주장하지 않는다. 프로세스별 hard fault 증거가 없으면 해당 수치는 미측정으로 둔다. |
| 시간/완료 | prompt/generation 시간과 tok/s는 실제 응답의 timings가 있을 때만 기록한다. 0/18 실패의 `generationSeconds=0`은 계산 시간이 0이었다는 뜻이 아니다. 샘플 공백·요청 deadline 접근·OS 응답성 저하를 root에 즉시 알린다. |

프로세스 page fault는 디스크 없이 해결될 수 있다. hard fault는 backing file/page file에서 읽어 해결되는 경우이며, 모든 hard fault가 페이지 파일 부족을 뜻하지는 않는다. [Microsoft 프로세스 counter](https://learn.microsoft.com/en-us/previous-versions/aa394323(v=vs.85)), [Microsoft hard-fault 설명](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/xperf/hard-faults)

느리지만 연속 관측·자원 여유·소유 관계가 유지되면 고정 제한 안에서 진행한다. 장시간 관측 공백, 자원 경보, 종료/소유 불명확 상태에서는 다른 모델을 겹쳐 시작하지 않고 원인을 확인한다. 중단·timeout은 실행 실패로 보존하며 품질 실패나 TG27 비교 제외로 바꾸지 않는다. 짧은 성능 수치로 18+6 전체 완료 시간을 보장하지 않는다.

## keep-awake와 필요한 최소 보완

[기존 도우미](../../.training/verifications/run-awake-below-normal-20260911.py)의 SHA는 `8e10effd8ffcecaea5b6e261a6c206741f062a27056a087746d885ad0cb6a89f`다. 실행 동안 `ES_CONTINUOUS | ES_SYSTEM_REQUIRED`를 요청하고 종료 시 해제하며, OS 전원 설정·display/away mode는 바꾸지 않는다. **자동 idle sleep 억제 범위**일 뿐 사용자 수면/덮개 닫기, 명시적 최대절전·전원 손실·시스템 정체를 막거나 과거 공백 원인을 입증하지 않는다. [Microsoft SetThreadExecutionState](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate)

도우미의 최대 86400초와 v4 전체 제한이 같아 외부 watchdog이 먼저 강제 종료하면 v4의 최종 summary 저장이 완료되지 않을 수 있다. 이 경우 정상 완료를 주장하지 않고 도우미 기록·마지막 샘플·소유 프로세스 종료를 별도로 확인한다. 도우미 자체는 CPU/메모리 원인 진단기가 아니다.

현재 v4는 원문 보호를 위해 시작 로그를 비우고 요청 로그를 보존하지 않는다. **새 v5를 제안할 필요가 생기는 경우는 요청 중 진행 증거가 없어 동일 실패를 다시 분류하지 못할 때**다. 그때도 원문/번역/API 키 없이 `phase, rowId, requestStartedUtc, requestElapsed, heartbeatGap, CPU/WS`만 기록하는 최소 telemetry를 별도 버전으로 검토한다. 샘플링·시간 제한을 몰래 늘리거나 기존 v4를 고치지 않는다. 이 문서에서는 코드 보완을 구현하지 않았다.

## 다음 명령 — root 검토와 단일 slot 확인 후

다음은 **안내이며 이 문서 작업에서 실행하지 않았다**. v4는 `--run` 플래그가 없고 아래 호출 자체가 실제 모델 실행이다. 먼저 다음 읽기 전용 명령을 3초 이상 간격으로 두 번 실행해 표로 budget을 선택하고, `$tg27BudgetGiB`에 그 정수 값을 명시한다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 -c "import sys,json; sys.path.insert(0,'scripts/model-comparison'); import linguistic_screen as s; print(json.dumps(s.memory_status()))"
```

아래의 `$tg27BudgetGiB`는 **현재 관측에 따라 root가 앞서 선택한 8~12 정수**여야 한다. 미지정이면 즉시 거부한다. root가 새 run tag와 예산·시작 관측 기록을 확정한 뒤 개발 18개를 실행한다.

```powershell
if ($null -eq $tg27BudgetGiB -or $tg27BudgetGiB -notin 8,9,10,11,12) { throw 'Select a verified TG27 budget first' }
$tg27RunTag = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$tg27DevOut = ".training/comparisons/linguistic-dev-20260910/translategemma-27b-resource-retry-$tg27RunTag-b$tg27BudgetGiB"
.venv-training\Scripts\python.exe -B .training/verifications/run-awake-below-normal-20260911.py --record ".training/verifications/tg27-$tg27RunTag-dev18-awake.json" --max-seconds 86400 -- .venv-training\Scripts\python.exe -B scripts/model-comparison/run_translategemma_large_v4.py --model-size 27b --input content/model-comparison/linguistic-dev-20260910.jsonl --output $tg27DevOut --threads 4 --ram-budget-gib $tg27BudgetGiB
```

18개 완료·종료 및 읽기 6개 직전의 재검사가 끝난 뒤에만 다음 명령을 실행한다. 두 명령을 자동 연속 실행하지 않는다.

```powershell
$tg27ReadingTag = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$tg27ReadingOut = ".training/comparisons/real-reading-check-20260910/translategemma-27b-resource-retry-$tg27ReadingTag-b$tg27BudgetGiB"
.venv-training\Scripts\python.exe -B .training/verifications/run-awake-below-normal-20260911.py --record ".training/verifications/tg27-$tg27ReadingTag-reading6-awake.json" --max-seconds 86400 -- .venv-training\Scripts\python.exe -B scripts/model-comparison/run_translategemma_large_v4.py --model-size 27b --input content/model-comparison/real-reading-check-20260910/sources.jsonl --output $tg27ReadingOut --threads 4 --ram-budget-gib $tg27BudgetGiB
```

이 문서 검증은 작은 기록·코드의 해시, 입력/도우미 경로와 CLI 인자 확인에 한정했다. 설치·모델 가중치 읽기·추론·worker 시작·등록·앱 데이터·기존 freeze 변경은 수행하지 않았다.
