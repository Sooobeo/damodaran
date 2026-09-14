# S4 Hy7 자원 프로필 v1

`resource_guard.py`의 `hy7-cpu4-context8192-observed-ws8-v1`을 최초 S4 생성 전에 고정한다. 모델·샘플러·프롬프트는 S1/S2 계약을 따르고, 이 프로필은 별도 생성기의 시작 조건·스케줄링·감시·소유 종료를 정한다. 기존 등록과 운영 worker 코드는 바꾸지 않는다.

| 항목 | 고정값 |
|---|---|
| 모델 슬롯·CPU | 한 번에 1개, CPU4, BelowNormal |
| 문맥·출력·입력 | 8192 / 4096 / 최대4095토큰 |
| 시작 가용 physical | 11GiB = 11,811,160,064바이트 이상 |
| 시작 가용 commit | 3GiB = 3,221,225,472바이트 이상 |
| 전원 | 조회 성공 및 `ACLineStatus=1` |
| 시작 관측 | 최초 무결성 검사 전과 native 시작 직전 각각 새 관측. 마지막 시작 확인은 3초 간격의 2회 관측 |
| native hard working set 요청 | 8GiB−64MiB = 8,522,825,728바이트; readback 정확 일치 |
| 실행 중 관측 상한 | working set·peak working set 모두8GiB = 8,589,934,592바이트 이하 |
| 관측·기록 간격 | 메모리·전원0.5초 / 파일 기록5초, 오류 즉시 기록 |
| 실행기 경합 검사 | 별도 감시 thread에서5초 간격; 조회 timeout10초 |
| 낮은 자원 중단 | physical 또는 commit512MiB 미만이 각각3초 지속 |
| 시간 상한 | native 시작240초 / 요청1,800초 / 전체28,800초(8시간) |
| 전원 요청 | 이 실행의 thread 수명 동안 임시 display+system 요청, 종료 시 해제 |

시작 수치는 과거 실제 **Hy7 CPU4/context8192/8GiB** 실행에서 가져왔다. 근거는 저장된 [readiness receipt](../../../.training/verifications/input-preparation-v1-s4-readiness-20260913.json)와 그 안의 `historicalHy7` 파일 해시다. 해당 일반16 실행은 native CPU mapped weights7604.99MiB·KV1024MiB·compute31MiB·output0.49MiB를 보고했고, 관측 최대 working set8,522,842,112바이트·peak8,522,891,264바이트·private1,290,080,256바이트였다. 최소 가용 physical3,489,034,240바이트였으며 자원 중단은 없었다. 이 peak는 이미 제한된 실행의 관측이므로 무제한 실행의 필요 RAM으로 바꾸어 해석하지 않는다.

시작 physical11GiB는 관측 상한8GiB에 정책 여유3GiB를 더한 값이다. 추가 commit3GiB도 이 실행의 정책이며 OOM 불가능을 보장하는 실측값이 아니다. TG27의 가중치·KV 공식 또는 프로필을 Hy7에 복사하지 않았다. 240초·1,800초·8시간 상한은 이번 실행의 실패 중단 정책이며 완료 시간 예측이 아니다. native 시작 전 producer가 잡은 `run_started`를 `ResourceMonitor`에 전달하고, readiness 후 `ready()`, 요청 경계마다 `begin_request()/end_request()`를 호출한다.

`GlobalMemoryStatusEx.ullAvailPageFile`을 가용 **system commit**으로 사용한다. pagefile 디스크 여유로 표시하지 않는다. working set은 해당 native의 물리 상주 페이지 범위이며 전체 프로세스 합산 RAM·commit·OS 파일 캐시 제한이 아니다. 총 page fault 계수에는 soft fault도 포함되고 DWORD가 순환할 수 있으므로 디스크 paging 횟수나 바이트로 해석하지 않는다. hard maximum flag의 의미는 [Windows working set API](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-setprocessworkingsetsizeex)를 따르고, 계속 변하는 가용 메모리는 [GlobalMemoryStatusEx](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-globalmemorystatusex)로 새로 읽는다.

`claim_process_owner()`의 기존 비상속·kill-on-close Job Object를 빌리고 기존 `SuspendedProcessOwner`로 native를 원자적으로 Job에 넣어 suspended 상태로 만든다. 첫 thread가 실행되기 전에 소유 Job/PID/생성시각·hard working set 설정과 readback·BelowNormal을 검증한다. 성공한 경우만 `ResumeThread`의 이전 정지계수1을 받아 진행한다. 이후 표본에서도 같은 handle의 PID/생성시각·Job·제한·우선순위를 확인한다. 예외·자원 부족·전원 분리·조회 실패·시간 초과에는 해당 실행이 보유한 native handle만 종료하고 `wait()`한다. 외부 프로세스나 저장한 예전 PID를 종료하지 않는다.

`Local\MoonModaran.ExclusiveLocalModelSlot.V1` named mutex는 이 프로토콜을 사용하는 새 실험들 사이에서 동시 실행을 막는다. 비협력 프로세스의 시작을 Windows 전체에서 막는 기능은 아니다. **기존 운영 worker는 이 mutex를 읽지 않는다.** 따라서 Toolhelp32 native 이름과 CIM의 정확한 worker/알려진 모델 실행 경로·GGUF 인수를 시작 전과 실행 중 함께 검사한다. 관련 프로세스가 생기면 새 요청을 중단하고 소유 native를 종료한다. 이 감지는 검사 간격과 최대10초 조회 지연의 틈이 있으므로 절대 동시 실행 방지라고 보고하지 않는다. 실패한 조회는 시작 거부/실행 중단이다. 명령줄이 확인된 무관한 Node·테스트·도우미는 충돌로 삼지 않고, 명령줄이 없는 후보 수와 전체 모델 부재 미확정 사실을 보존한다. 명령줄 원문은 결과·로그에 저장하지 않는다. mutex 수명·소유는 [CreateMutexW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw)를 따른다.

임시 전원 요청은 AC가 확인된 같은 thread에서 `ES_CONTINUOUS|ES_SYSTEM_REQUIRED|ES_DISPLAY_REQUIRED`로 켜고, `finally`의 같은 thread에서 `ES_CONTINUOUS`로 해제한다. 사용자의 덮개 닫기·전원 버튼·수동 절전을 막거나 전역 전원 계획을 바꾸지 않는다. 이 제한은 [SetThreadExecutionState](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate)에 명시되어 있다. main thread에서 `monitor.stop()` → `stop_owned()` → 전원 요청 scope 종료 → mutex scope 종료를 수행한다. 부모 자신의 수명 Job handle은 임의로 닫지 않는다.

검증 명령은 아래와 같다. 2026-09-13 guard 전용23개가 통과했다. 모두 가짜 process/API를 사용한 실패 경로·경계·수명 검사이므로 native 모델 실행 검증이 아니다. Windows read-only preflight는 실제 API로 별도 수행했다. 실제 hard limit·resume·전원 요청·서버 종료의 검증은 S4 producer 실행 receipt에서 확인한다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 -m unittest discover -s scripts/model-comparison/input_execution_v1 -p test_resource_guard.py -v
```
