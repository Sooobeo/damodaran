# Qwen3.5-9B CPU·메모리 최적화 검토 v1

2026-09-11. 이 문서는 기존 `runtime_v3.py`, 동결 prompt/schema, 실행 결과와 정책 v1을 바꾸지 않는 후속 프로필 제안이다. 전체 48행 생성은 시작하지 않았다. 새 경고 매핑은 root가 별도로 만든 `scripts/local-qe/qwen-material-warning-policy-v2.json`과 `material_warning.py`를 사용하며 후속 실행 전에 각각의 해시를 동결해야 한다.

## 확인한 계약

고정 llama.cpp commit은 `72797e89198ab564fd0e6baa54ab196e8dd1d884`다. 원문 소스와 해시는 `.translation/qe/llm-candidates/qwen35-9b/cache-reuse-audit-v1.json`에 기록한다.

- [server README](https://github.com/ggml-org/llama.cpp/blob/72797e89198ab564fd0e6baa54ab196e8dd1d884/tools/server/README.md#post-completion): `cache_prompt=true`는 가능한 공통 prefix를 재사용한다. 배치 크기에 따른 logit 차이 때문에 같은 seed라도 출력 바이트가 같다고 보장하지 않는다.
- [server-context.cpp](https://github.com/ggml-org/llama.cpp/blob/72797e89198ab564fd0e6baa54ab196e8dd1d884/tools/server/server-context.cpp): 3217행부터 공통 prefix를 찾고, 3349~3384행에서 과거 순환 상태를 복원할 체크포인트가 없으면 전체 prompt를 다시 처리한다. `--cache-ram 0`은 별도 prompt-cache 객체를 끄는 것이며 slot의 context checkpoint 옵션과 구분된다(1351~1369행).
- [hybrid memory](https://github.com/ggml-org/llama.cpp/blob/72797e89198ab564fd0e6baa54ab196e8dd1d884/src/llama-memory-hybrid.cpp): `PARTIAL_ONLY` 체크포인트는 attention KV를 중복 저장하지 않고 recurrent state를 저장한다(173~183행). [recurrent memory](https://github.com/ggml-org/llama.cpp/blob/72797e89198ab564fd0e6baa54ab196e8dd1d884/src/llama-memory-recurrent.cpp)의 과거 토큰 복원은 `n_rs_seq` 범위에 제한된다.
- 실제 v3 로그는 `n_rs_seq=0`, partial sequence removal 불가, checkpoints disabled를 확인한다. 따라서 기존 설정에서 `cache_prompt=true`만 바꾸는 것으로 prefix 재사용을 보장할 수 없다.
- native `/completion`은 `message_delimiters`를 읽어 token 경계를 계산한다(server-context.cpp 4304~4321행). [common/chat.cpp](https://github.com/ggml-org/llama.cpp/blob/72797e89198ab564fd0e6baa54ab196e8dd1d884/common/chat.cpp) 126~150행의 형식은 `[{"role":"user","delimiter":"<|im_start|>user\n"}, ...]`다. 이는 모델 입력 문자열을 바꾸지 않는 실행 메타데이터다. 실제 template와 구분자 토큰의 유일한 경계를 검증해야 한다.
- 체크포인트는 마지막 user 경계와 prompt 말미 두 지점에서 만들어질 수 있다(3520~3635행). 고정 system+user 요청에서는 3개가 있어야 user 경계 상태를 말미 상태가 밀어내지 않는 후보 구성이 된다. 생성·복원 로그로 이를 실제 확인하기 전에는 재사용 성공으로 보고하지 않는다.

## 제안하는 새 프로필

| 항목 | 고정 후보 설정 | 근거·한계 |
|---|---|---|
| CPU | generation threads 4, batch threads 4 | 반응성을 위한 후보이며 총 실행시간 개선은 미측정 |
| 우선순위·대기 | `--prio -1`, 소유 Windows process BelowNormal, `--poll 0 --poll-batch 0` | 기존 Windows Job 소유권 유지, 다른 프로세스·전역 전원 설정은 변경하지 않음 |
| context/output | 4096 / 2048 | vocab-only로 실제 template 적용 48행 모두 `inputTokens+2048<4096`일 때만 채택; 자동 잘림·출력 축소 없음 |
| batch | logical 128 / physical 128 유지 | 기존 숫자를 그대로 기록하고 cache에 따른 배치 경로 차이는 별도 실측 |
| 캐시 | `cache_prompt=true`, `id_slot=0`, `--ctx-checkpoints 3`, `--cache-ram 0` | system/user/assistant 경계 구분자 고정, 복원 실패 시 조용히 성공으로 해석하지 않음 |
| 자원 제한 | 자식 working/private 8 GiB, 시작 전 물리·commit 여유 각각 11 GiB, 실행 중 바닥 1 GiB | 기존 보수적 제한 유지; OOM 불가능 보장 아님 |
| 기존 고정값 | no-warmup, no-repack, mmap, CPU only, 단일 slot, no-context-shift, enable_thinking=false | sampler·prompt/schema·누출 차단·원시 결과 보존 유지 |

v3 실제 KV는 8192 context에서 256 MiB였으므로 4096에서는 약 128 MiB로 예상된다. 순환 상태 버퍼는 실제 50.25 MiB였으며 3개 checkpoint 사본은 약 150.75 MiB와 직렬화 오버헤드가 더 필요할 수 있다. KV 감소와 단순 합산하면 기존 대비 약 22.75 MiB 증가지만 전체 peak는 allocator·배치·mmap 동작 때문에 새 실측이 필요하다. 모델 파일 5.29 GiB를 context 감소만으로 줄일 수 없다.

## 실행 전·후 검증

1. 별도 vocab-only audit의 첫 3행 Jinja 바이트·token ID 대조와 전체 48행 예산이 모두 통과해야 한다. 실제 생성 0, tensor 전체 로드 0을 기록한다.
2. 새 runner/profile freeze에 모델·runtime·prompt/schema·sanitized input·소유 helper·v2 경고 매핑 파일 해시를 포함한다. 기존 파일·동결 결과는 보존한다.
3. root의 슬롯 승인 후 동일한 첫 3행 순서로 새 smoke를 수행한다. row2/3의 restored context checkpoint, prompt 처리 토큰 수, 캐시 토큰 수, 출력 토큰/EOS, 메모리·CPU 시간·실제 priority, 소유 종료를 기록한다. 응답의 `tokens_cached` 하나만으로 공통 prefix 절약량을 해석하지 않는다.
4. raw JSON/schema 통과와 의미 판별 품질을 분리한다. 새 출력은 v2 경고 매핑과 별도 고정 정답 평가가 필요하다. v1 전체 48행은 실행하지 않으며 v1에서 확정된 FP를 감추거나 v2 결과로 대체하지 않는다.

현재 상태는 **고정 코드 계약 검토 및 새 실행 설정 제안**이다. prefix 재사용·CPU 개선·새 품질 통과는 아직 실측하지 않았다.
