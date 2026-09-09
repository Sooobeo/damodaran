# 가치평가 공부방

Damodaran의 선별 자료로 공부하는 로컬 한국어 학습실이다. 자료실·8개 학습 단원·HTML/PDF 읽기·무료 로컬 한국어 번역·금융용어·Excel 가이드·메모와 학습 기록을 연결한다. 기본 번역은 Argos Translate이며 API 키가 필요 없다.

## 시작하기

웹·저장 기능의 검증 환경은 Windows PowerShell, Node.js 22.15.0, npm 10.9.2다. Node 22.15 이상을 사용한다. 로컬 번역 설치는 기본적으로 Windows의 Python 3.11(`py -3.11`)을 사용한다. Python 실행 경로를 직접 지정하려면 설치 전에 `TRANSLATION_SETUP_PYTHON` 환경변수에 지정한다. 새 번역 환경의 실제 설치·성능 검증 상태는 [구현 상태](IMPLEMENTATION_STATUS.md)를 확인한다.

```powershell
cd C:\Users\Insun\damodaran
npm ci
npm run setup
npm run setup:translation
npm run import:core
npm run dev
```

[공부방 열기](http://127.0.0.1:3000). 기본 주소는 이 PC의 localhost다. `dev`는 웹과 별도 작업 처리기를 함께 실행하고 Ctrl+C로 종료한다. `setup`은 DB와 초기 콘텐츠를 만들며 다시 실행해도 개인 메모·진도·북마크를 지우지 않는다. `.env.local`이 없으면 비밀값 없는 예제를 복사한다.

`setup:translation`은 Python 가상환경과 영어→한국어 모델 1.1을 설치한다. 최초 패키지·모델 다운로드에는 인터넷이 필요하다. 모델 외 Python 의존성도 설치하므로 전체 설치 크기는 모델 파일보다 크다. 기존 개인 DB를 초기화하지 않으며 설치가 안 된 상태에서도 탐색·원문·메모는 사용할 수 있다.

`import:core`는 실제 입문 HTML 3개와 R06 목록에서 확인한 대표 PDF를 가져온다. 일부 원문이 실패하면 명령은 실패 종료 코드를 반환하고 자료·작업 상태에 원인을 남긴다. 성공한 원문은 계속 읽을 수 있다. 현재 대표 PDF는 원문 서버 시간초과가 확인되었으며, 자료실의 **PDF 올리기**로 가지고 있는 텍스트 PDF를 읽을 수 있다.

## 사용하는 순서

1. 학습 경로에서 단원을 선택한다. 선수 용어·목표·읽을 자료·확인 질문·관련 Excel을 확인한다.
2. 자료 상세에서 **원문 가져오기**를 누른다. 다른 화면으로 이동해도 worker가 처리를 계속한다.
3. 읽기 화면에서 원문·한국어·나란히 모드를 선택한다. HTML은 문단별, PDF는 페이지별로 원문과 번역을 대응한다.
4. 문단을 선택해 메모를 남기거나 북마크한다. 다음날 홈·내 기록에서 같은 버전과 위치로 돌아간다.
5. 단원 완료는 직접 체크한다. 자료 방문이나 번역 완료만으로 진도를 올리지 않는다.

초기 콘텐츠는 자료 36개, 단원 8개, 금융용어 44개, Excel 가이드 10개다. 실제 목록에서 발견한 첨부자료·업로드는 추가 자료로 등록되므로 이후 자료 개수는 늘어난다. 첫 실제 가져오기에는 대표 PDF 항목 1개가 추가된다.

## 무료 로컬 번역

기본 `.env.local` 설정은 다음과 같다. 기존 파일의 제공자가 `openai`라면 이 값만 바꾸고 웹과 worker를 재시작한다. 이미 있는 설정 파일을 예제로 덮어쓰지 않는다.

```dotenv
TRANSLATION_PROVIDER=argos
```

`npm run setup:translation`으로 준비한 [Argos Translate](https://github.com/argosopentech/argos-translate)와 [공식 영어→한국어 패키지 1.1](https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json)을 사용한다. 설치 후에는 인터넷이나 API 키 없이 선택 문단·PDF 페이지를 번역한다. 원문은 PC의 Python 하위 프로세스에서 처리하며 외부 번역 서버로 보내지 않는다. 웹 화면은 기존 작업 큐·저장 번역·메모를 그대로 사용한다.

번역은 클릭한 범위에만 실행하고 유효한 저장 결과는 재사용한다. 기본 한도는 작업당 20,000자, 하루 100,000자이며 로컬 모드에서는 CPU·메모리·대기열 보호용이다. 하루 경계는 Asia/Seoul이고 원시 시각은 UTC로 저장한다. 로컬 사용량의 글자 수·처리 단위를 선택형 API 토큰과 구분한다. 제공자·실제 모델 해시·런타임이 바뀌면 캐시를 분리하고 기존 번역은 보존한다. 제공자를 변경한 뒤 이전 OpenAI 대기 작업이 자동 실행되지 않게 한다.

Argos는 무료 기계번역 초안이다. [다모다란 자료 기반 용어 규칙](content/translation-glossary.md)을 문단의 관련 용어와 함께 전달한다. 문장 전체를 번역한 뒤, 그 원문에 해당 영어 표현이 있고 관측된 한국어 오역이 유일하게 대응할 때만 교정한다. 불확실한 치환은 하지 않고 검토 필요로 표시한다. 용어만 있는 제목·셀에는 정확 일치 규칙을 적용한다. 한국어 표기는 학습실의 번역 기준이며 다모다란의 공식 한국어 번역이 아니다.

숫자·수식은 모델에 보내지 않고 해당 구간을 나누어 보존하므로 어순과 문장 자연스러움에 제약이 남는다. 자동 검사는 숫자·통화·부호·수식·응답 ID 등의 무결성을 확인하며, 의미 정확성이나 사용자 검수 완료를 뜻하지 않는다.

### 번역 수정과 검수 번역 재사용

리더의 번역 문단에서 **번역 수정 → 검수 완료로 저장**을 선택한다. 숫자·통화·수식은 원문과 같아야 하며, 다른 창에서 수정한 경우 최신본을 비교한 뒤 다시 저장한다. 표는 셀 편집이 필요해 이 기능에서 제외하며 HTML 문단·PDF 텍스트 문단을 지원한다.

원래 기계 번역과 모든 검수 이력을 SQLite에 따로 보존한다. 검수본은 원문·이웃 문맥·적용 용어집·블록 종류가 같을 때 번역 요청에서 재사용하고, 추가 번역 호출이나 처리량을 발생시키지 않는다. 원문이나 용어집이 바뀌면 이전 검수본은 보존하되 최신으로 표시하지 않는다. 설정 화면에서 용어 규칙 수와 검수 문장 수를 확인한다.

`npm run export:translation-memory`는 검수한 영한 문장쌍을 `data/derived/reviewed-translations-*.jsonl`로 내보낸다. 이력과 연결 정보는 DB 백업에 포함되며 내보낸 JSONL은 DB에서 다시 만들 수 있다. 이 기능은 번역 메모리와 학습 자료 축적이며 모델 가중치를 자동 학습시키는 기능은 아니다.

기존 설치는 웹·worker를 종료한 뒤 `npm run setup`, `npm run setup:translation`, `npm run build`, `npm start` 순서로 갱신한다. setup은 스키마 1에서 2로 옮기기 전에 원본을 포함한 일관된 백업을 자동 생성한다. 이전 스키마의 백업도 새 폴더로 복원할 수 있으며, 해당 DATA_DIR에서 setup을 실행한 뒤 앱을 시작한다.

```powershell
npm run verify:translation
# PDF가 아직 없을 때 HTML만 검증
npm run verify:translation:html
```

기본 검증은 실제 HTML 3문단과 가져온 PDF 1페이지의 번역·저장·캐시를 확인한다. PDF는 원격 원문이나 업로드한 텍스트 PDF를 사용할 수 있다. `verify:translation:html`은 내부적으로 `--html-only`를 전달해 HTML만 검증한다. PowerShell에서 npm 인자 전달이 생략되는 문제를 피하도록 별도 명령을 제공한다. 결과에는 제공자·모델 정체성·검증 범위·시각을 남기며 HTML만 성공한 것을 PDF까지 성공했다고 표시하지 않는다. 실제 실행 결과와 남은 검증은 구현 상태 문서에 기록한다.

이 PC에서 실행 환경과 모델은 합계 약 1.28 GB를 사용했다. 첫 모델 열기는 보통 수 초였지만 한 번은 222초가 걸렸으며 원인은 확정하지 못했다. 시작과 개별 번역 요청은 각각 최대 5분까지 기다리고, 시간이 초과되면 실패를 표시한다. 준비 후에는 같은 worker에서 모델을 재사용한다.

## 선택형 OpenAI 번역

OpenAI를 사용하려는 경우에만 `.env.local`을 다음처럼 설정하고 웹과 worker를 재시작한다. 기본 로컬 번역에 이 설정은 필요 없다.

```dotenv
TRANSLATION_PROVIDER=openai
OPENAI_API_KEY=본인의_API_키
TRANSLATION_MODEL=계정에서_사용_가능한_구조화_출력_지원_모델
```

이 모드는 서버 공식 SDK의 Responses API로 원문·참고 문맥·관련 용어를 전송하며 API 사용량이 발생한다. 키가 있다는 이유로 자동 선택하지 않는다. 모델명은 서버 설정에서 읽고, 키·모델이 없으면 API 설정 필요를 안내한다. 원문·사전·메모·저장된 번역은 계속 동작한다.

API의 실제 input/output token과 불명확한 호출을 별도 기록한다. 검증한 가격표가 없으면 금액 대신 토큰·처리 분량을 표시하며 알 수 없는 비용을 0원으로 표시하지 않는다. 분량 한도는 API 비용의 보장 상한이 아니다. 선택형 OpenAI 실호출 검증은 로컬 Argos 검증과 구분한다.

## 파일과 DB

```text
data/
  library.sqlite       자료·번역·메모·진도·작업 큐
  originals/           원본 HTML·PDF·Excel
  derived/             로컬 이미지·파생 파일·가져오기 보고서
  backups/             검증한 DB와 원본 파일 백업
  tmp/                 임시 파일
.venv-translation/     재설치 가능한 Python 가상환경
.translation/          로컬 번역 모델·설치 정보
```

DB는 `better-sqlite3`와 버전 관리하는 SQL 마이그레이션으로 관리한다. WAL·외래키·busy timeout·짧은 트랜잭션을 적용했다. 설치한 드라이버의 SQLite 런타임은 3.53.4다. 초기 Drizzle 후보 대신 직접 파라미터 SQL을 선택하여 복합 외래키·작업 lease·네이티브 백업을 한 계층에서 관리한다.

`DATA_DIR`는 앱 루트 기준 상대 경로 또는 절대 경로다. 웹·worker·CLI는 공통 설정을 읽는다. DB 내부 파일 경로는 DATA_DIR 기준 상대 경로로 저장하므로 다른 폴더로 복원할 수 있다. 원문 업데이트는 새 버전을 만들고 이전 번역과 메모를 유지한다.

DB·원문·키·로컬 번역 환경과 모델은 Git과 public 폴더에서 제외한다. PDF.js worker·폰트·CMaps·WASM은 setup/build에서 설치 패키지로부터 로컬 public 자산으로 복사한다. 저장 원문·번역·메모를 읽을 때 외부 CDN이 필요하지 않다. 오프라인 읽기와 설치 후 로컬 번역은 로컬 웹·worker가 실행 중인 상태에서 인터넷 없이 이용하는 기능이다. 새 원문 가져오기·최초 모델 설치·선택형 OpenAI 번역은 연결이 필요하다.

## 백업과 복원

```powershell
npm run backup
```

SQLite 백업 API로 DB 스냅샷을 만들고 그 DB가 참조하는 원본·자산 파일을 복사한다. DB에 기록된 해시·크기와 실제 파일을 대조하고 manifest를 남긴다. `.env.local`과 키, 재설치 가능한 Python 환경·모델은 제외한다. 새 PC에서는 `npm run setup:translation`으로 번역 환경을 다시 준비한다. 실행 중인 SQLite 파일 하나를 수동 복사하지 않는다.

복원은 기존 데이터와 다른 **존재하지 않는 새 폴더**를 지정한다.

```powershell
npm run restore -- "C:\Users\Insun\damodaran\data\backups\백업폴더명" "C:\Users\Insun\damodaran-restored"
```

DB 무결성·외래키·누락 파일·해시를 검증한 뒤 복원한다. 대기·실행 중 작업은 복원 사유가 있는 취소 상태로 바뀌며 로컬 처리와 선택형 유료 호출이 자동 재개되지 않는다. 완료 결과·사용량 이력은 유지한다.

기존 앱을 종료하고 다음처럼 복원 사본의 웹만 실행해 메모·진도·원문을 확인한다.

```powershell
$env:DATA_DIR = 'C:\Users\Insun\damodaran-restored'
$env:WEB_ONLY = '1'
npm start
```

`npm start`는 미리 `npm run build`를 실행한 경우에 사용할 수 있다. 복원을 확인한 뒤 종료하고 WEB_ONLY를 해제해 정상 실행한다. 필요하면 DATA_DIR를 `.env.local`에 기록한다. 키 설정은 원래 앱의 `.env.local`에서 읽는다.

```powershell
Remove-Item Env:WEB_ONLY
npm start
```

## 실행·검증 명령

| 명령 | 동작 |
|---|---|
| `npm run setup` | DB·마이그레이션·안전한 seed·로컬 PDF 자산 준비 |
| `npm run setup:translation` | 무료 Argos Python 환경·영어→한국어 모델 설치·무결성 확인 |
| `npm run dev` | localhost 개발 웹 + 별도 worker |
| `npm run worker` | worker만 실행 |
| `npm run build` | 프로덕션 빌드 |
| `npm start` | localhost 프로덕션 웹 + 별도 worker |
| `npm run import:core` | 실제 초기 원문 가져오기·결과 보고 |
| `npm run verify:translation` | 현재 제공자의 실제 HTML 3문단·PDF 1페이지 번역·캐시 검증 |
| `npm run verify:translation:html` | 실제 HTML 3문단만 번역 검증, 범위 명시 |
| `npm run export:translation-memory` | 검수된 영한 문장쌍을 로컬 JSONL로 내보내기. 학습 실행 없음 |
| `npm run backup` / `npm run restore -- ...` | 백업 / 새 폴더 복원 |
| `npm run typecheck` | TypeScript 검사 |
| `npm test` | 임시 DATA_DIR에서 저장·추출·작업·번역 무결성 테스트 |
| `npm run test:browser` | 격리된 DB·웹·worker에서 저장·PDF·재시작 브라우저 검증 |
| `npm run test:smoke` | 실행 중인 웹의 PC·모바일 주요 화면 확인 |

브라우저 검증에는 이 PC에 설치된 Google Chrome이 필요하다. `test:browser`는 먼저 프로덕션 빌드를 완료하고 R01 원문을 가져온 상태에서 실행한다. 운영 DB를 백업·복원한 임시 사본과 포트 3011을 사용하며 원본 메모·진도는 바꾸지 않는다. T01을 미리 가져오면 실제 Excel 파일 해시도 확인한다. 결과는 `test-results/browser-e2e.json`에 남는다.

`test:smoke`는 실행 중인 기본 주소 또는 BASE_URL을 사용한다. 읽기 화면을 방문하므로 그 서버의 최근 읽기 위치는 갱신된다. 전체 결과는 구현 상태 문서에 설명한다. 테스트 번역 제공자는 테스트 환경에서만 주입 가능하며 실제 UI 콘텐츠로 초기화되지 않는다.

## 원문 연결·문제 해결

- **로컬 번역 설치 필요:** Python 3.11과 인터넷 연결을 확인하고 `npm run setup:translation`을 실행한다. 성공 후 웹·worker를 재시작한다. 로컬 모드에는 OpenAI 키를 입력할 필요가 없다.
- **번역 설정 변경:** `.env.local`의 제공자 변경 후 웹·worker를 함께 재시작한다. 이전 설정의 대기 작업은 자동 재개하지 않으므로 현재 설정으로 필요한 범위를 다시 요청한다.
- **PDF 서버 시간초과:** 실제 실패 원인을 작업 목록에서 확인하고 재시도하거나 개인 PDF를 업로드한다. 현재 대표 PDF 실패를 성공으로 숨기지 않는다.
- **NYU 리디렉션:** 일부 원본은 www → people → pages 호스트로 이동한다. 실제 이동을 확인하여 `.env.example`에 `EXTRA_SOURCE_HOSTS=people.stern.nyu.edu`를 등록했다. 기존 도메인·경로 검사와 리디렉션마다 DNS 검증은 유지한다.
- **스캔 PDF:** 텍스트가 없는 페이지는 OCR 필요로 표시한다. P0는 OCR을 수행하지 않는다.
- **표·수식:** 구형 HTML 비교표와 위·아래첨자를 보존한다. 복잡한 PDF 수식·다단 문서는 원본 페이지를 함께 확인한다.
- **원문 문제·해답:** 별도 해답 자료는 연결해서 제공한다. 같은 HTML 안의 해답을 안전하게 분리하기 어려우면 통합 원문으로 읽는다.
- **Excel:** 한국어 목적·입력·결과 해석 가이드를 읽고 원본 파일로 실습한다. 웹에서 수식·매크로를 실행하거나 셀 문구를 자동 치환하지 않는다. 시트·셀 위치는 미확인으로 표시한다.
- **작업 중 앱 종료:** 저장한 처리 단위는 남는다. 비정상 종료의 만료 lease는 worker가 복구한다. 취소 전에 이미 전송한 외부 요청의 사용량이 없어지지는 않는다.
- **포트 사용 중:** 기존 실행을 종료하거나 `.env.local`의 APP_PORT를 바꾸고 웹·worker를 함께 재시작한다.

개발 전 [AGENTS.md](AGENTS.md), [제품 요구사항](DAMODARAN_KO_LEARNING_SPEC.md), [DB·페이지 설계](SYSTEM_DESIGN.md)를 참고한다. 실제 완료·검증·외부 제약은 [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)에 기록한다.
