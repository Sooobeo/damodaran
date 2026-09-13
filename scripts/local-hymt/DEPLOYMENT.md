# Hy-MT2 앱 제공자 연결 계약 v1

품질 비교 뒤 선택한 구성을 등록하는 선택형 무료 로컬 제공자다. Q26의 네 구성·24문단·96개 도우미 판단 뒤 `contextual` 구성을 선택했고, Windows Job 생성 수정 후 현재 등록은 `hymt-contextual-q26-20260910-nativejob`이다(manifest SHA `cb3031d87372fbb1b0c3dcaf2071064bb59a8a21bd96480ef2d5a66752ae52bd`). 최초 등록·앱 QA 실패는 별도로 보존한다. 실제 제품 엔진의 시작·정상 종료와 v1 HTML 3문단·PDF 7블록의 생성·저장·캐시 검증은 완료했다. PDF 도입 블록의 내용 추가를 보완한 v2도 새 PDF 4블록의 실제 생성·저장·캐시와 기존 HTML 3문단의 캐시 재사용을 확인했다. 추가 HTML 추론은 0회였다. 실제 운영 빌드와 기존 환경설정 바이트 보존 후 이 PC는 `.env.local`에 Hymt를 명시하고 웹·worker를 시작했다. 운영 HTML 3문단의 새 생성·저장·캐시와 최종 API의 `configured: true`·`liveVerified: true`를 확인했다. 운영 검증 범위는 `html-only`이며 격리 PDF QA와 구분한다. 새 설치에서 설정을 생략한 초기 기본값은 Argos다. 활성 manifest의 등록 상태와 앱의 활성 제공자 설정을 구분하며 자동 변경·대체하지 않는다.

## 파일과 정체성

활성 manifest: `.translation/hymt/manifest.json`. 등록 시 동일 바이트를 `.translation/hymt/registrations/<registrationId>/manifest.json`에도 보존한다. 원본 Q8·Windows 실행기는 기존 `.training/comparisons/hy-mt2-7b-q8/`를 참조하며 큰 가중치를 복제하지 않는다.

manifest 필수 필드:

- `schemaVersion: 1`, `provider: "hymt"`, `model: "tencent/Hy-MT2-7B-GGUF"`
- `modelHash`: 공식 GGUF SHA-256
- `runtimeVersion`: `hymt-local-v1:`과 실행 계약의 canonical JSON SHA-256
- `installedAt`, `registrationId`: 등록 시각과 등록 ID
- `pythonPath: ".venv-training/Scripts/python.exe"`
- `modelPath: ".training/comparisons/hy-mt2-7b-q8"` (디렉터리)
- `profile`: `raw` 또는 `contextual`. 비교에서 실제 선택한 구성 그대로 사용
- `modelFiles`: GGUF 한 개의 `{path, size, sha256}` 목록
- `runtimeFiles`: 설치 검증된 Windows 실행 파일 52개의 같은 형식 목록
- `codeFiles`: bridge·deployment·engine·process_owner·run_hymt·setup_hymt의 같은 형식 목록
- `catalog`: 등록본 `catalog.json`의 `{path, size, sha256}`. 기존 고정 54개 용어 사전의 동일 바이트 사본
- `evidenceFiles`: 비교 manifest·metrics·완료한 도우미 검토 요약·선택 근거의 `{path, size, sha256}` 목록
- `execution`: revision, templateSha256, runtimeOverrides, sampling, contextSize, threads, cpuOnly, contextPolicyVersion, pythonVersion, jinjaVersion

모든 path는 APP_ROOT 기준 상대 경로이며 symlink/junction·범위 이탈을 거부한다. 파일 목록의 중복을 거부한다. 미등록·누락·무결성 실패는 준비되지 않은 상태다.

Python 실행 파일과 Jinja의 전체 `.py` 목록·SHA는 완료한 비교 manifest의 `identity.inputFiles`에 결속한다. 새 시작과 TS 준비 상태 확인에서도 고정된 가상환경 경로의 실제 바이트를 대조하고 Jinja 파일의 추가·삭제를 거부한다. 등록 과정은 전체 비교 입력과 검토 JSONL을 재검증 직전에 해시·파일 상태로 기록하여 큰 모델 파일 검사와 활성화가 끝날 때까지 변경을 감지한다.

전체 실행 정체성은 **`hymt:<modelHash>:<활성 manifest 원본 바이트 SHA-256>`**이다. 이를 캐시의 model 값과 NDJSON handshake에 그대로 사용한다. `runtimeVersion`의 canonical JSON은 Python의 `ensure_ascii=True, sort_keys=True, separators=(',', ':')`다. TS는 실행 계약을 임의로 재구성하지 않고 파일·코드의 실제 SHA 및 manifest 바이트를 검사한다. 해시는 기존 stat 기반 캐시에 보관해 같은 파일을 화면 조회 때마다 다시 읽지 않는다.

## NDJSON과 생성

Node는 Python `.venv-training/Scripts/python.exe -u scripts/local-hymt/bridge.py`만 생성한다. Python이 manifest·모델·런타임을 실제 검증하고 전용 Job 소유권을 확보한 뒤 native 서버를 생성한다. Windows·CPython 3.11의 `_OwnedPopen`은 `CreateProcessW`의 `PROC_THREAD_ATTRIBUTE_JOB_LIST`로 서버를 생성하는 순간 해당 Job을 명시적으로 배정한다. 암묵적 Job 상속이나 생성 후 배정에 의존하지 않으며 배정 실패 시 다른 생성 방식으로 재시도하지 않는다. `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`에는 표준 입출력의 상속 가능한 핸들만 넣고 Job 핸들은 상속하지 않는다. 생성 후 실제 child 핸들의 소속도 검증하며 일반 프로세스 launcher 지원으로 범위를 넓히지 않는다. stdout은 NDJSON 외에 출력하지 않는다.

준비 응답은 `{ready:true,provider:"hymt",model,modelHash,runtimeVersion,identity}`다. Node는 모든 값과 full identity를 확인한다. 요청은 `{id,modelIdentity,context,segments:[{id,text}],glossary}`이며 Hymt는 등록 사전을 사용하므로 전달된 앱 glossary를 번역 힌트에 사용하지 않는다. Node가 요청마다 보내는 `modelIdentity`는 준비된 identity와 같아야 한다. 응답은 기존 `{id,data:{segments:[{id,translatedText,warnings}]}}`이고 유료 토큰/요청 ID는 null이다.

Hymt에만 원문을 보호 표식으로 바꾸지 않고 그대로 전달한다. `raw`는 원문만, `contextual`은 원문과 참고 문맥 및 원문에 실제 등장하는 고정 사전 항목의 조건부 정의를 사용한다. 생성 후 ID·중복·누락·숫자·수식 검사를 유지한다. 빈 출력·잘림·상한·제어 토큰은 실패, 숫자·통화 등 검토 경고는 `needs_review`로 저장한다. 원시 번역에 사후 치환을 적용하지 않는다.

제공자 연결은 기존의 제목+이웃 문맥 정책을 유지하며 그 자체로 원문을 재추출하거나 기존 검수 기록을 무효화하지 않는다. 실행 계약의 `contextPolicyVersion`은 `existing-title-neighbors-v1`이다. 입력 토큰 예산을 넘으면 원문을 몰래 자르지 않고 실패한다. 실제 PDF 번역에서 확인한 단위 경계 문제는 별도 `pdf-paragraphs-v2`로 보완한다. 신규 추출만 안전한 미완결 도입과 하위 목록 전체를 묶으며 기존 v1·`structured-v2` 원문/작업/번역은 보존한다. 모델 프롬프트·등록 사전·Q26 결과는 변경하지 않는다.

모델 시작은 기존 5분 한도를 유지한다. Hy-MT2의 문단 요청은 큰 CPU 모델과 긴 원문을 처리할 수 있도록 Python HTTP 실행기와 같은 최대 30분을 허용하며, Argos·finetuned의 기존 5분 한도는 유지한다. 작업 lease/heartbeat와 시간 초과 시 소유 프로세스 정리는 유지한다. 30분은 최대 대기 한도이며 처리 시간 보장이 아니다. Python의 정상 종료에서는 소유한 서버를 종료하고 기다리며, Python 강제 종료 시 Job Object가 서버까지 정리한다. 원문·문맥·응답을 native 진단 로그에 저장하지 않는다. 앱 worker 외에 새 상시 worker를 만들지 않는다.

## 앱·데이터 영향

`TRANSLATION_PROVIDER=hymt`를 명시할 때만 사용한다. 설정·client provider union·무료 사용량 분류·검증기록 provider를 확장한다. provider DB CHECK가 없어 신규 스키마 migration은 필요 없다. 기존 원문 버전·번역·검수·메모·북마크·진도를 보존한다. 제공자와 정체성이 달라지면 이전 캐시를 현재 결과로 표시하지 않는다. 이전 OpenAI 대기·재시도 작업을 새 제공자로 조용히 재개하지 않는다.

등록 CLI는 실제 비교·전체 도우미 대조가 완료된 근거를 확인하고 등록한다. 실제 앱 추론·저장·캐시 검증과 최종 활성 제공자 변경은 별도 단계로 기록한다. 자동 검사나 도우미 대조를 사람 검수로 표시하지 않는다. 등록본과 비교·학습 결과는 SQLite 백업 외 별도 보관 대상이다.
