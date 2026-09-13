# TG27 v5 검수 증거·준비·집계

새 v5 실행의 완료 증거를 기존 v4 검사에 더해 검증하는 읽기 전용 도구다. 실제 모델·producer 코드·worker·운영 DB를 실행하지 않는다. 준비와 집계는 지정한 **새 산출물**에만 쓴다. 기존 v4 도구/기록, Hy7/Qwen 실행기, general 답변 grader와 freeze를 변경하지 않았다.

## 파일과 계약

| 파일 | 역할 |
|---|---|
| [v5_review_evidence.py](v5_review_evidence.py) | 기존 v4 guard/원시 응답/토큰/settings 검증 + v5 telemetry·소유 종료·원시 SHA 검사 |
| [prepare_linguistic_reviews_v5.py](prepare_linguistic_reviews_v5.py) | 고정 개발18, 사전 source audit, 2~4개 완료 시스템의 익명 검수 packet 생성 |
| [summarize_linguistic_reviews_v5.py](summarize_linguistic_reviews_v5.py) | 18×시스템 수의 모든 판단을 먼저 확인하고 키를 열어 기존 의미/용어/대조쌍 집계 |
| [reading_review_common_v5.py](reading_review_common_v5.py) | 읽기6 원문·참조·advisory·producer·판정 계약 |
| [prepare_reading_reviews_v5.py](prepare_reading_reviews_v5.py) | 고정 읽기6, 2~4개 완료 시스템의 packet·작성용 template 생성 |
| [summarize_reading_reviews_v5.py](summarize_reading_reviews_v5.py) | 모든 읽기 판단 검증 후 키 연결·집계 |

새 준비 manifest는 dev `reviewEvidenceVersion:v5`, reading `real-reading-blind-review-packets-v5`이며 `v5ArtifactFilesSha256`에 검증한 원시 파일·telemetry·지원 코드의 inventory 해시를 묶는다. 집계기는 같은 새 준비 계약만 읽고 producer 증거를 다시 확인한다. 완료되지 않은 판단은 키 접근 전 거부한다. 입력/packet/판정/키/원시 결과 중복·누락·변조·경로 이탈·기존 출력 덮어쓰기 방지도 v4와 같다.

과거 지원 producer(Hy7, Hy30 및 TG 이전 버전)는 각 기존 계약으로 계속 검사할 수 있다. **새 TG27 v5에만 v5 필수 관측 계약을 적용한다.** v5 검증은 private 메모리 복사의 version 한 필드만 v4 검사 함수에 투영하여 기존 검사를 호출한다. 디스크의 원본 summary, 그 해시, 집계의 `runMetadata/summarySchema`에는 원래 `translategemma-large-screen-v5`를 그대로 보존한다. v5를 v4 실행으로 표시하거나 기존 module/global을 monkeypatch하지 않는다.

## v5에서 필수인 증거

- status 완료, 동일 입력의 정확한 18 또는 6개 결과, 최종 무결성, 소유 child 종료, guard/cleanup 오류 없음.
- 고정 CPU4·source-only·sampling·1800/7200/86400초·8~12GiB와 시작 +3GiB 물리 여유·commit 여유, hard WS readback·별도 physical/commit guard.
- 원래 Job의 suspended 생성, PID/creationTicks, resume count, 설정한 WS receipt와 summary/각 메모리 행/종료 receipt의 소유 정체성 일치.
- native priority 0x4000 BelowNormal 실측과 CPU kernel/user 100ns tick·초·합계의 일관성, 비감소 CPU 누적값, 실제 WS/peak와 기록된 extrema의 모순 없음.
- startup/runtime-validation, **각 행의 요청 시작과 응답 후 단계**, 마지막 final-integrity가 존재하고 요청 ID/시작 UTC/활성 여부/경과시간이 일관됨. 7200초 이상은 거부한다. 관측 heartbeat와 파일 기록 간격·window maximum·summary maximum을 구분해 대조한다.
- 필수 telemetry 필드 누락, 잘못된 숫자/NaN/중복 JSON 키, 원문·키 같은 임의 추가 필드, 만들어 낸 token progress를 거부한다. 요청 중 token progress는 계속 미측정이다.
- `childCreationCleanupUnconfirmed:false`, `memoryMonitoring.lastRequestDeadlineExceededAtEnd:false`가 **명시적으로 존재**해야 한다. 미확인 생성/종료와 종료 시 deadline 초과를 성공으로 보정하지 않는다.
- 각 prediction의 원시 응답 파일명/SHA와 summary의 정확한 전체 `responseFilesSha256`, raw content/settings/tokens/stop/잘림이 서로 같아야 한다. 반환된 객체만 있고 완료된 18/6개가 아니면 준비하지 않는다.

지원 runner SHA는 최종 검토된 `946119b19e982e311680491cc6cd4b4ab9109336873ae4ffac27fecf0d2c6d54`로 고정했다. `SUPPORTED_CODE`는 runner, WS limiter, suspended owner, 공통 process owner의 명시한 SHA를 producer `codeHashes`와 현재 로컬 파일에서 대조한다. 임의 output 폴더 Python을 import/실행하지 않는다. 그 파일들이 나중에 바뀌면 과거 결과도 현재 코드로 조용히 통과시키지 않는다. 새로 검토한 버전이나 보존한 실행 코드로 연결하는 별도 명시 계약이 필요하다.

memory JSONL은 128MiB/행 64KiB 이내에서 스트리밍으로 읽고 전후 해시를 검증한다. 하루치 5초 기록이 기존 16MiB 일반 JSON 읽기 한도를 넘을 수 있어서 메모리 파일만 별도로 처리한다. 모델 가중치나 runtime 바이너리를 읽지 않는다.

## 해석 제한

이 검사는 **보존된 증거의 완전성과 일관성**을 확인한다. 서명된 하드웨어 측정이나 과거 프로세스의 재실측이 아니다. 5초 기록 사이의 모든 0.5초 샘플은 저장되지 않아 extrema와 공백을 완전히 재구성할 수 없다. 기록된 값이 summary와 모순되면 거부하지만, 누락된 실제 순간을 복원하지 않는다. page fault는 soft+hard 합계이고 disk hard fault나 paging bytes는 미측정이다. 공백의 원인·품질·모델 속도를 자동 판정하지 않는다.

검수 판단과 기존 집계 방식은 그대로다. 자연스러움과 의미 오류, 용어 뜻과 표기, 6개 대조쌍을 분리하고, 사람이 검수한 gold/독립 test/학습 효과 검증으로 표시하지 않는다. 이 도구에서 새 품질 threshold·자동 승격·95% 통과 결론을 만들지 않는다. 실제 판단을 문자열 일치로 대체하지 않는다.

## 명령

아래는 CLI 계약 안내다. 이 작업에서는 실제 결과의 packet/검수/집계를 생성하지 않았다. `.venv-training` Python에서 실행한다. `--results`는 같은 고정 입력으로 완료한 2~4개 서로 다른 시스템 디렉터리, `--output`은 새 경로로 지정한다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/prepare_linguistic_reviews_v5.py --input content/model-comparison/linguistic-dev-20260910.jsonl --source-audit $devSourceAudit --results $hy7Dev $tg27V5Dev --output $newDevPrepared
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/summarize_linguistic_reviews_v5.py --prepared $newDevPrepared --reviews $devReview1 $devReview2 $devReview3 --output $newDevSummary
```

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/prepare_reading_reviews_v5.py --input content/model-comparison/real-reading-check-20260910/sources.jsonl --results $hy7Reading $tg27V5Reading --output $newReadingPrepared
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/summarize_reading_reviews_v5.py --prepared $newReadingPrepared --reviews $readingReview --output $newReadingSummary
```

```powershell
.venv-training\Scripts\python.exe -B -X utf8 -m unittest discover -s scripts/model-comparison -p '*reviews_v5_legacy_regression.py'
.venv-training\Scripts\python.exe -B -X utf8 -m unittest discover -s scripts/model-comparison -p test_v5_review_evidence.py
```

합성 검증: 기존 소비 계약 회귀 53개, v5 증거/개발18·읽기6 준비→집계 연결 12개. 실제 모델 실행과 실제 v5 산출물 검증은 아직 수행하지 않았다. TG27의 실제 18+6 완료와 원문 중심 판단이 별도로 남아 있다.
