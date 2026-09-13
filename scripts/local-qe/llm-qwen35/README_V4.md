# Qwen3.5-9B 의미 검토 실행기 v4

이 버전은 CPU·메모리 최적화와 별도 material-warning v2 매핑을 준비한다. 기존 `runtime_v3.py`, prompt/schema/contract v1, 정책 v1 및 모든 smoke 결과를 보존한다. Qwen v4 가중치 로드·추론은 아직 실행하지 않았다.

## 고정 설정과 검사

- CPU generation/batch threads 각각 4, BelowNormal, `--prio -1 --poll 0 --poll-batch 0`.
- context 4096, 출력 예약 2048, 논리/물리 batch 각각 128, 단일 slot, thinking=false.
- `cache_prompt=true`, message delimiter 고정, context checkpoint 3개, 별도 RAM prompt cache는 0. [고정 코드 검토](CACHE_REUSE_REVIEW_V1.md)를 따른다. 실제 캐시 복원 성공·시간 절약은 아직 미측정이다.
- 자식 working/private 8GiB 관측 상한, 시작 전 물리·commit 여유 각각 11GiB, 실행 중 각각 1GiB 바닥을 유지한다. 소유 Windows Job과 suspended 생성 전 working-set 제한을 재사용한다. 실제 priority·CPU time·PID/creation time을 기록한다.
- [새 프로필](resource-profile-v3.json)에 토큰 audit 및 v2 매핑 파일 해시를 포함한다. 모델에는 source/translation/context만 전달하며 ID·정답·참조·checks·평가 매핑을 보내지 않는다.

vocab-only 검사에서 고정 48행은 최대 1393토큰으로, 출력 2048토큰 예약 후 655토큰이 남았다. 첫 3행은 실제 v3 template UTF-8 바이트와 token ID가 모두 일치했다. 전체 공통 prefix는 1064토큰이며 모든 user 경계는 token index 1058이다. 검사기의 최대 working set은 104.36MiB, peak commit은 98.98MiB였다. 생성·전체 가중치 로드는 0이다.

근거: `.translation/qe/llm-candidates/qwen35-9b/token-budget-v1/attempt-001/summary.json`, SHA256 `11352d7f17267c88608192de6c47ee6616b1f7bc9bc586f509e4a98ed44045cb`. 전체 253개 결과물 해시를 재검증했다. 이는 4096 문맥의 실제 생성이나 cache 재사용 검증을 뜻하지 않는다.

v4는 이 audit의 현재 contract/prompt/schema 정체성, 입력 내용, template·token hash를 생성 전에 대조한다. 불일치·예산 초과 시 자동 잘림이나 출력 축소 없이 실패한다. `timings.cache_n`과 `prompt_n` 합계를 입력 길이와 대조하고, slot 전체 길이인 `tokens_cached`를 재사용량으로 오인하지 않는다. helper가 생성한 자식의 종료를 확인하지 못한 예외는 lock을 보존한다.

합성 검사는 token budget 12개, v4 runner 18개가 통과했다. 실제 모델에 대한 EOS·캐시 checkpoint 복원·자원 사용·경고 품질 검사는 별도다. v1 전체 48행 생성은 취소되었고, v2 매핑은 새 실제 출력 전체 평가가 필요하다. 의미 경고와 정확한 빨간 구간 후보의 검증도 구분한다.

## 명령

PowerShell, 저장소 루트, 기존 CPython 3.11 환경을 읽기 전용 재사용한다. 다음 합성 검사는 모델을 실행하지 않는다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 -m unittest discover -s scripts/local-qe/llm-qwen35 -p test_runtime_v4.py -v
```

`--run`을 생략하면 모델 추론 없이 새 실행 계획만 작성한다. 이미 존재하는 출력 폴더는 덮어쓰지 않는다. 실행 시에는 root의 실제 단일 모델 슬롯 승인과 물리·commit 여유 확인이 필요하다. 아래 `ROOT_SLOT_APPROVAL_RECEIPT`는 승인 내용을 기록하는 자리 표시자이며 승인 자체가 아니다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/local-qe/llm-qwen35/runtime_v4.py --input .translation/qe/llm-candidates/qwen35-9b/inputs/smoke-first3-v1.jsonl --output .translation/qe/llm-candidates/qwen35-9b/runs/smoke-first3-v4 --run --slot-approval "ROOT_SLOT_APPROVAL_RECEIPT"
```

새 smoke는 원본 입력 순서 첫 3행을 유지한다. 실제 입력·원시 응답·template/token·checkpoint 로그·CPU/메모리·소유 종료 및 v2 후처리 결과를 새 폴더에 보존한다. 같은 seed라도 캐시와 batch 경로가 달라 출력 바이트가 같음을 보장하지 않는다. 전체 48행의 새 v2 평가는 smoke 결과와 root의 후속 실행 결정을 따른다.
