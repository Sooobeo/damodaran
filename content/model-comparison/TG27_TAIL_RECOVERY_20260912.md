# TG27 미완료 2문단 별도 재실행

2026-09-12 11:13:57 KST에 기존 개발18 재시도가 요청17의7200초 제한으로 종료됐다. 완료16·실패1·미실행1이며, 단일 개발18 완료로 처리하지 않는다. 완료16의 번역·검토와 실패를 보존한 뒤 **기존 실행기의 `--ids LDEV26-017 LDEV26-018`로 미완료2개만 새 폴더에서 재실행**한다. 사용자의 실패 재시도·계속 진행 지시에 따른 후속이며 이미 완료한16개를 다시 생성하지 않는다.

## 확인한 실패와 보완

017에서120초를 넘는 기록 공백의 합은5,415.046초였고, 해당 공백 동안 native CPU 증가 합은14.28125초였다. Windows 유휴 Modern Standby·전원 상태 변경 이벤트와 함께 기록했다. 약90분 공백을 모델 연산90분으로 해석하지 않는다. 파일58개의 해시·완료16개의 raw/EOS·보존 prediction 일치·소유 종료·wrapper 해제·coordinator 종료를 `.training/verifications/tg27-retry-17-standby-failure-20260912.json`에서 확인했다.

기존 system 전원 요청에 임시 display 요청을 추가한 뒤 관측668.36초 동안 native CPU는1,307.96875초 증가했고 최대 기록 heartbeat는1.203초였다. 이미 누적한 경과시간은 줄이지 않아 원래 제한대로 종료했다. 이는 해당 관측 창의 결과이고 향후 절전이 전혀 없다는 보장은 아니다. 원래 실행기·guard·시간 제한은 변경하지 않았다.

[Windows 공식 계약](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate)에 따른 display+system 전원 요청을 새 순차 실행 시작부터 유지한다. 전역 전원 설정이나 사용자 입력을 조작하지 않는다. 전원 분리·취소 시 요청을 해제하고 이후 기동을 중지하며, 이미 활성화한 native의 종료는 기존 소유 wrapper가 담당한다. 사용자 수동 절전은 우회하지 않는다. 이전 임시 helper는 coordinator 종료와 함께 요청 해제·감시 handle 닫기를 완료했다.

## 별도 실행의 범위와 증거

| 항목 | 계약 |
|---|---|
| 개발 미완료 입력 | 기존 고정 원문18 중017·018, 순서 고정 |
| 실제 producer | 기존 `run_translategemma_large_v5.py`, 코드·모델·추론 설정 불변 |
| 자원·시간 | CPU4·BelowNormal·8GiB, 시작 physical11GiB/commit 조건, startup1800·요청7200·전체86400초 유지 |
| 새 2개 실행 | `.training/comparisons/linguistic-dev-20260910/translategemma-27b-v5-tail-retry-20260912-b8` |
| 이후 읽기6 | `.training/comparisons/real-reading-check-20260910/translategemma-27b-v5-tail-recovery-20260912-b8` |
| 순차 제어 | `.training/verifications/continue-tg27-tail-recovery-20260912.py`, shell session89362 |
| 상태·이력 | `.training/verifications/tg27-tail-recovery-20260912.json` 및 같은 이름의 JSONL |
| 중단 플래그 | 기존 `.training/verifications/tg27-user-retry-continuation-20260911.cancel` 재사용. 활성 native를 직접 종료하는 플래그는 아님 |
| 시작 관측 | 11:19 KST 두 관측의 가용 physical 최솟값12,401,569,792바이트, native0 확인 후 기동 |
| 최초 native | PID75880/creationTicks134336531944836318. 종료 시 현재 handle·생성시각 재확인 필요 |

새 2개 consumer [v5_tail2_review_evidence.py](../../scripts/model-comparison/v5_tail2_review_evidence.py)는 기존 v5 검증을 별도 파일로 유지하면서 **017·018의 정확한2개**만 받도록 범위를 지정했다. 원래 전체6/18 consumer의 기준을 고치지 않았다. 모델/원시 응답·EOS·설정·자원·소유 종료·heartbeat 검증은 유지한다. 복사본의 변경 범위는 설명, 별도 계약명·허용 ID,2개 cardinality, v5 producer 전용 제한이다. 임의 부분집합·순서 변경·누락·기술 실패·시간/메모리 위반은 거부한다. 합성6개를 통과했으며 원래 전체 v5 consumer가2개를 여전히 거부하는 것도 확인했다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/verify_tg27_tail2.py --output .training/verifications/tg27-tail2-v5-verification-20260912.json
```

이 명령은 새2개 전체 종료 뒤에만 성공할 수 있다. `tail2RunValidated:true`와 `fullDev18RunValidated:false`를 구분한다. coordinator는2개 종료·검증 뒤 새 메모리/native0 관측을 다시 통과해야 읽기6을 시작한다. 읽기6은 기존 전체 v5 검증기로 확인한다. 자동 재시도·학습·등록은 하지 않는다.

새 원문 대조는 [partial_tg27_recovery_review.py](../../scripts/model-comparison/partial_tg27_recovery_review.py)와 `tg27-recovery-root-review-v1` 판단을 사용한다. 출력은 `.training/quality-evaluation/tg27-recovery-root-review-20260912/partial/`이며 원래16개 snapshot과 다른 폴더다. 기존 인용·raw·해시·용어 전체 출현 검증을 유지하고 개발017/018·읽기REAL001~006만 허용한다. 합성11개를 통과했다. 현재 새 실제 응답을 검토 완료했다는 뜻은 아니다.

## 결과 해석

이전16개와 이후2개가 모두 관측되더라도 **서로 다른 실행의 진단 자료**다. 원래 실패한 개발 실행을 완료로 바꾸거나 기존 전체18 검증기·금융 질문 준비기에 하나의 성공 실행처럼 전달하지 않는다. 기존 `complete_tg27_root_review.py --cohort dev18`가 실패한 실행을 거부하는 동작은 유지한다. 향후 진단 자료를 함께 집계하려면 개별 실행·실패·부분 검증 범위를 보존하는 별도 명시 연결이 필요하다.

실행 실패1건만 별도로 원장에 추가해611사건(번역128·질문76·QE104·실행5·용어298)이 됐다. 반복 가져오기는 추가0·재사용1이었고 부분16개의 의미/용어 판단은 아직 편입하지 않았다. 보고서는 `.training/quality-evaluation/error-ledger/v1/reports/20260912T022029584075Z.json`이다. 질문 답변자 제약·중요 의미 오류5문단·기존 보류는 그대로다. 모델 비교·독립 질문·전체 학습 수용·앱 적용을 완료로 선언하지 않는다.

11:37 KST 후속: [실패 실행 부분 진단 연결](../../scripts/model-comparison/PARTIAL_TG27_REVIEW.md#실패-실행의-완료16개를-진단-원장에-연결)에서 완료16 검토·용어81 판정도97사건으로 추가했다. 원장은708개이고 중복 재입력은 추가0·재사용97이다. 원래 실행 실패·부분 검증 범위·자동 검사·의미 보류는 모든 사건에 보존했다. 원장 편입은 전체 실행 성공이나 품질 수용 판정을 만들지 않는다.

12:21 KST까지 새017의112토큰·정상 EOS106 출력을 저장하고018로 넘어갔다. [017 부분 대조](TG27_PARTIAL_REVIEW_20260912.md#별도-재실행017)는 통제변수·추정 상관을 보존했으나 인과 입증 부정의 명확성은 보류했다. 새 snapshot은 기존16개와 분리했으며 아직 전체2개 종료 검증은 아니다. 원장은 기존 의미 규칙의16개 관측 추가로724개이고 새017의 부분 판단은 아직 편입하지 않았다.


## 13:35 KST — 새2개 종료와 읽기6 시작

새017·018은13:22:55 KST에 정상 종료했고 wrapper의 childStopped/released와 exit0을 확인했다. 최대 heartbeat 간격1.313초, 최저 가용 물리 메모리771,960,832바이트이며 abortReason/monitorError는 null이다. 고정2개 기술 검증은 `tail2RunValidated=true`, `fullDev18RunValidated=false`를 유지한다.017 보류·018 관측 오류 없음과 용어11보존을 완료 증거에 연결해13사건을 추가했고 반복 추가0·재사용13을 확인했다. 원장은737사건이다.

새 자원 검사에서 native0을 두 번 확인하고 최소 물리12,524,666,880바이트·commit14,886,109,184바이트로 읽기6을 시작했다. coordinator는 `reading6_running`, 첫 요청은13:24:12 KST의REAL26-001이다. 현재 동작의 [상태 파일](../../.training/verifications/tg27-tail-recovery-20260912.json)과 [읽기 wrapper](../../.training/verifications/tg27-reading6-recovery-20260912-awake.json)를 확인하며 이미 종료한 tail2 native PID로 종료를 시도하지 않는다. 읽기6의 기술 종료·원문 대조·57용어·독립 질문은 아직 완료하지 않았다. 일시적 AC 전원 요청과 기존 guard를 유지하고 추가 모델을 동시에 띄우지 않는다.


## 17:09 KST — 읽기 실행 소실과 새 사전검사 보류

읽기 run은14:16:33 KST 이후 관측이 없고 재연결 시 native·wrapper·coordinator가 없었다. 저장 prediction0·summary 없음으로 [별도 중단 관측](../../.training/verifications/tg27-reading6-interruption-20260912.json)에49파일을 보존했다. 기존 `running` JSON은 마지막 저장 상태이며 현재 실행으로 읽지 않는다. 정상 소유 종료/종료 코드는 미확정이다. 실행 사건1개만 추가해 원장은738개다.

AC 재연결 뒤 새 읽기6 retry를 시작하려 했으나 [사전검사](../../.training/verifications/tg27-reading6-slot-reading-retry-20260912.json)에서 최저 가용 물리10.64GiB와 첫 native 조회 시간 초과로 보류됐다. 새 모델 로딩은0이고 [새 coordinator](../../.training/verifications/tg27-reading-retry-20260912.json)는 중단·일시 전원 요청 해제를 기록했다. 기존 원문/번역/검토/실행기와 완료2개는 보존한다. 현재 모델 실행은 없으며 전체 재개 조건과 남은 작업은 인수인계27절을 따른다.
