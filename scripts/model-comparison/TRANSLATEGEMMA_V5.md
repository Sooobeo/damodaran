# TG27 v5 — 소유 프로세스의 요청·자원 관측

> **최신 상태: 2026-09-11 17:33 KST 사용자 요청으로 중단.** 이후 자원 검사를 통과해 실제 native 로드·첫 요청·관측 경로를 실행했으나 번역 완료는0/18이다. 소유 native·실행 도우미 종료와 전원 유지 요청 해제를 확인했다. 새 실험은 시작하지 않으며, [인수인계 문서22절](../../TRANSLATION_HANDOFF_20260911.md)의 중단 기록·미완료·재개 순서가 최신 기준이다. 아래 미실측·보류 설명은 실행기 작성 당시의 이력이다.

**후속 상태:** 아래 실행기 작성 당시 미구현이었던 검수 연결은 별도 [v5 검수 어댑터](V5_REVIEW_ADAPTERS.md)로 준비했다. 기존 v4 파일은 그대로이며 새 어댑터 합성65개와 CLI 도움말4개를 확인했다. 실제 v5 생성·검수는 아직 미실측이다. 2026-09-11 16:30 KST 모델 없는 사전검사에서 가용 물리 메모리 최솟값10.28GiB가 최소 시작 조건11GiB보다 적어 시작을 보류했다. [자원 관측 receipt](../../.training/verifications/tg27-v5-slot-preflight-20260911-agent-001.json)에 두 실제 관측과 종료·파일 정체성 검사를 보존했다.

2026-09-11. 새 [run_translategemma_large_v5.py](run_translategemma_large_v5.py)는 v4를 복사한 별도 실행기다. v4 파일·실패 기록·기존 freeze는 변경하지 않았다. 모델 로드·추론·worker 시작·등록·운영 DB 변경 없이 합성 검사와 CLI 도움말만 확인했다. **실제 Windows native 프로세스에서 새 관측 경로가 동작하는지는 아직 미실측**이다.

기존 [재시도 자원 계획](../../content/model-comparison/TG27_RETRY_RESOURCE_PLAN_20260911.md)의 CPU4, 단일 모델, 8~12GiB + 3GiB 시작 여유, 새 개발18 → 종료 검증 → 새 읽기6 순서를 유지한다. 과거 문서의 속도 튜닝 중단은 당시 기록으로 보존하고, 이후 사용자의 CPU·메모리 최적화 지시에 따라 별도 자원 프로필을 검토한다. 느리다는 이유로 TG27 18+6 비교를 제외하지 않는다. 큰 budget이 과거 실패를 해결한다는 근거는 없다.

## 보존한 실행 계약

- Q4 모델·original Gemma template·원문만 입력·context 2048·출력 768·CPU 4·고정 sampling/settings 그대로다.
- startup 1800초, request 7200초, total 86400초, 0.5초 관측/5초 정기 기록, physical/commit 각각 512MiB 미만 3초 guard를 유지한다. 행/단계 경계와 guard 실패에는 5초 전에 추가 기록할 수 있다.
- 실제 WS 및 peak WS의 선택 budget 초과를 거부한다. Win32 hard maximum은 선택 budget보다 64MiB 낮다. 다른 앱·OS cache·commit 전체 상한이라는 뜻은 아니다.
- `SuspendedProcessOwner`와 `WorkingSetLimit`을 그대로 사용한다. 원래 Job 소유권, suspended 상태에서의 WS 설정/readback, 보존된 process handle, PID와 creationTicks 확인 뒤 native를 관측한다. 사용자 프로세스 종료·OS/덮개 정책 변경은 하지 않는다.
- 기존 소유 child 종료와 `childProcessStopped`, code/input/asset 최종 무결성, 실패/미실행 행 보존을 유지한다. generation 출력/판정 정책은 바꾸지 않는다.

v5의 `version`은 `translategemma-large-screen-v5`, telemetry는 `owned-phase-resource-v1`이다. `copiedFromV4Sha256`은 `6277e73c9ee9f8d35c79fa197028eb8a5345a77078779802d81405845c0a7a2d`. 기존 `code_hashes()`가 새 v5 파일 자체와 사용한 공통 코드·Python/Jinja 파일 해시를 기록한다. v4를 import하거나 monkeypatch하지 않는다. 합성 검사에서는 보존한 생성·메모리 함수와 설정의 AST 및 실제 v4 SHA가 그대로인지 대조한다.

## 추가 관측의 의미

`memory-samples.jsonl`에 다음 값을 함께 기록하고 최종 SHA를 summary에 묶는다. 임의 free text는 기록하지 않고 필드/phase/입력 ID를 허용 목록으로 제한한다.

| 필드 | 의미와 제한 |
|---|---|
| `observedAtUTC`, `seconds`, `previousObservationUTC` | 이번/직전 관측의 UTC와 run 시작 이후 monotonic 시간. 공백의 원인은 자동 분류하지 않는다. |
| `phase`, `rowId` | startup, runtime-validation, prompt-preflight, generation-request, response-validation, request-failed, final-integrity 등. rowId는 이번 고정 입력에 있는 안전한 식별자만 허용한다. |
| `requestStartUTC`, `requestElapsedSeconds`, `requestActive` | 요청 guard를 활성화한 시점/경과. 요청 직전의 동기 소유·메모리 검사 시간도 포함하며, 서버가 접수하거나 첫 토큰을 생성한 시각은 아니다. HTTP 예외면 마지막 요청은 request-failed로 보존한다. |
| `heartbeatGapSeconds` | **직전 실제 관측**부터의 monotonic 간격. 5초 파일 기록 간격과 다르다. |
| `maximumHeartbeatGapSincePreviousRecordSeconds`, `recordGapSeconds` | 5초 사이에 발생한 짧은 공백도 다음 기록에 남긴다. lifetime 최대값은 summary `memoryMonitoring.maximumHeartbeatGapSeconds`. |
| `child.pid`, `creationTicks` | 새로 PID를 열지 않고 보존된 소유 handle의 PID·생성시각을 다시 확인한다. WS 관측과 CPU 조회의 정체성이 다르면 거부한다. |
| `cpuKernelTicks`, `cpuUserTicks`, `cpu*Seconds` | GetProcessTimes의 누적 CPU 시간(1 tick = 100ns). CPU 증가량과 실제 관측 시간차를 함께 보며, CPU가 움직인다는 사실만으로 번역 진행을 확정하지 않는다. |
| `priorityClass`, `belowNormalVerified` | native 생성 시 0x4000 BelowNormal을 명시하고 실제 값을 매번 조회한다. 초기 불일치는 localhost 요청 전에, 이후 불일치는 다음 관측에서 거부한다. 실행 중 다른 프로세스 우선순위를 바꾸지 않는다. |
| WS/peak/private/pageFault 및 physical/commit | 기존 guard와 같은 숫자. pageFaultCount는 soft+hard 합계이고 DWORD wrap 가능성이 있다. disk hard fault 수·paging bytes·특정 디스크 원인은 여전히 미측정이다. |

`requestTokenProgressAvailable:false`, `requestTokenProgress:null`을 명시한다. 동기 `/completion` 계약을 유지하므로 요청 도중 토큰 진행·TTFT는 **알 수 없다**. tok/s나 생성 토큰 수는 실제 응답이 돌아온 이후 값만 사용한다. native 시작 로그는 기존 고정 EOS/EOT/EOG 확인 뒤 비우고, 요청 로그는 계속 버린다. 원문·번역·API 키·전체 prompt·전체 command line을 telemetry나 native 진단 로그에 복구하지 않는다. 원래 허용된 별도 predictions/raw 응답 산출물은 기존처럼 보존한다.

원시 응답마다 완료 시점 `responseFilesSha256`을 summary에 남기고 prediction의 `rawResponseFile/rawResponseSha256`과 연결한다. 정상 마지막 단계에서 파일 해시를 재확인한다. 응답 프로토콜이 틀리거나 guard가 이미 중지시켰어도 반환된 raw 객체는 먼저 보존한다. 실패한 런에는 정상 최종 무결성을 주장하지 않는다.

child 종료 관측은 `memoryMonitoring.ownedChildExitObserved`에 원래 소유 handle의 exit code/시각/정체성을 남긴다. 종료 후 과거 WS/CPU를 새 관측처럼 쓰지 않는다. 종료와 cleanup 성공은 `childProcessStopped` 및 guard/cleanup 오류와 함께 확인한다. summary가 없으면 마지막 메모리 파일만으로 완료를 인정하지 않는다.

실행 전 검토에서 v4부터 이어진 생성 실패 기록 문제를 수정했다. `spawn`이 handle을 반환하기 전에 `owned_child_creation_cleanup_failed` 또는 `unowned_child_cleanup_failed`를 던지면 종료가 확인되지 않은 것이다. v5는 구체적인 `ownershipErrorCode`와 `childCreationCleanupUnconfirmed:true`, `childProcessStopped:false`, 같은 `cleanupError`를 보존한다. `stop_process(None)`의 성공 반환으로 이 상태를 덮지 않는다. 다른 소유권 오류도 구체적인 고정 오류 코드를 남기며, 공통 owner와 v4 파일은 바꾸지 않았다.

요청 종료 때도 기존 7200초 deadline을 대조한다. 관측 thread가 지연되어도 종료 시각이 deadline 이상이면 `request_time_limit`을 잠금 안에서 남기고, summary의 `memoryMonitoring.lastRequestDeadlineExceededAtEnd:true`로 기록한다. 여기서는 예외나 kill을 호출하지 않는다. 반환된 raw 객체와 SHA를 먼저 저장한 뒤 guard 검사로 실패시키고 소유 child를 종료한다. 응답 없이 HTTP가 실패한 경우는 raw를 만들어 내지 않고 HTTP 오류와 deadline 위반을 함께 보존한다. 7200초 미만/같음/초과 및 응답 유무를 합성 검사하며, 기준 시간은 늘리지 않았다.

기존 TG27 실패는 0/18, 요청 중 긴 관측 공백, ConnectionResetError/request_time_limit이며 **원인 미확정**이다. 2026-09-11 다른 Hy7 contextual 실행에서는 Modern Standby 시각과 공백이 확인되었으나 그 증거를 과거 TG 실패 원인으로 소급하지 않는다. 이 도구도 공백을 sleep·품질 오류·모델 속도로 단정하지 않는다.

기존 keep-awake helper의 `ES_SYSTEM_REQUIRED`는 자동 idle sleep 억제 요청이며 사용자 수면·덮개 닫기를 막는 보장은 없다. 외부 helper의 86400초가 native 실행기보다 먼저 시작하므로 native 24시간 마무리/summary 저장을 선제 종료할 수 있는 기존 한계도 남아 있다. **이를 해결했다고 주장하거나 budget을 늘리지 않았다.** 외부 종료 시 helper 기록/마지막 관측/실제 소유 child 소멸을 별도로 확인한다. [Microsoft SetThreadExecutionState](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate)

## 검수 준비 경로: 현재 v4 도구는 v5를 거부

기존 파일을 수정하지 않고 읽어 확인했다.

- `prepare_linguistic_reviews_v4.py:40` → `summarize_linguistic_reviews_v4.py:152`의 `normalize_producer_summary`는 TG v1~v4만 받으므로 v5는 `unsupported_producer_summary_version`으로 거부한다.
- `prepare_reading_reviews_v4.py` → `reading_review_common_v4.py:132`의 `producer`도 TG v2~v4만 받아 v5를 `unsupported_producer_version`으로 거부한다.
- 공통 `v4_review_evidence.py:16`의 `VERSIONS`와 `check_v4_evidence`도 v5를 지원하지 않는다. 추가 codeHashes 자체가 거부 원인은 아니고 producer version 계약이 먼저 불일치한다.

따라서 v5 런을 v4로 이름만 바꿔 통과시키면 안 된다. **후속 최소 범위**는 새 `v5_review_evidence.py` + 새 dev/reading v5 준비 어댑터다. 원본 v5 metadata/version을 보존하면서 완료18/6·입력/모델/코드 identity·고정 generation/resource guard·BelowNormal/소유 생성시각·원시 응답 SHA·memory SHA·final integrity·종료를 검증해야 한다. 기존 익명화/원문 감사/판정 정책은 재사용하되 v5 증거를 manifest와 code inventory에 명시하고, 최종 검수 집계도 그 새 manifest 계약을 읽도록 별도 버전으로 연결해야 한다. 이 후속 어댑터는 이번 작업에서 구현하지 않았다. 현재 Hy7 실행이 참조할 수 있는 기존 검수 파일 해시는 그대로다.

## 합성 검사와 다음 명령

```powershell
.venv-training\Scripts\python.exe -B -X utf8 -m unittest discover -s scripts/model-comparison -p test_run_translategemma_large_v5.py
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/run_translategemma_large_v5.py --help
```

합성 53개: v4 계약 회귀, 단계/요청 실패, 실제 관측 간격과 기록 간격 분리, 소유 identity 불일치·자식 종료·priority 조회 실패/불일치, 초기 요청 거부, 로그의 원문/키 제외, 원시 응답 변경 거부, 실패 행 보존과 종료 검증. 생성 중 종료 미확인과 구체 소유권 오류, 요청 종료 deadline 경계와 응답 유무도 검사한다. Win32 CPU/priority API는 가짜 반환값을 쓰며 실제 native 프로세스는 만들지 않는다.

다음 모델 명령은 **root 검토, 앞 모델 종료, 단일 heavy slot, 실행 직전 2회 물리/commit 여유와 선택 budget 기록 후**의 안내다. 아직 실행하지 않았다. `--run` 스위치가 없으므로 아래 호출 자체가 실제 모델 실행이다. v5 지원 검수 어댑터 준비도 root가 별도로 결정해야 한다. 반복 smoke를 추가하지 않고 새 개발18의 첫 요청을 관측한다.

```powershell
if ($null -eq $tg27BudgetGiB -or $tg27BudgetGiB -notin 8,9,10,11,12) { throw 'Select a verified TG27 budget first' }
$tg27RunTag = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$tg27DevOut = ".training/comparisons/linguistic-dev-20260910/translategemma-27b-v5-$tg27RunTag-b$tg27BudgetGiB"
.venv-training\Scripts\python.exe -B .training/verifications/run-awake-below-normal-20260911.py --record ".training/verifications/tg27-v5-$tg27RunTag-dev18-awake.json" --max-seconds 86400 -- .venv-training\Scripts\python.exe -B scripts/model-comparison/run_translategemma_large_v5.py --model-size 27b --input content/model-comparison/linguistic-dev-20260910.jsonl --output $tg27DevOut --threads 4 --ram-budget-gib $tg27BudgetGiB
```

개발18의 완료/원시 무결성/소유 child 종료를 확인하고, 읽기6 직전 새 여유를 다시 관측한 뒤에만 다음을 실행한다. 두 실행을 자동 연결하거나 실패를 자동 재시도하지 않는다.

```powershell
$tg27ReadingTag = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$tg27ReadingOut = ".training/comparisons/real-reading-check-20260910/translategemma-27b-v5-$tg27ReadingTag-b$tg27BudgetGiB"
.venv-training\Scripts\python.exe -B .training/verifications/run-awake-below-normal-20260911.py --record ".training/verifications/tg27-v5-$tg27ReadingTag-reading6-awake.json" --max-seconds 86400 -- .venv-training\Scripts\python.exe -B scripts/model-comparison/run_translategemma_large_v5.py --model-size 27b --input content/model-comparison/real-reading-check-20260910/sources.jsonl --output $tg27ReadingOut --threads 4 --ram-budget-gib $tg27BudgetGiB
```
