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

## 실제 모델 가중치 미세조정

최신 사용자 승인으로 무료 공개 Marian 모델을 금융 영한 문장에 맞춰 추가 학습하는 도구를 넣었다. [Helsinki-NLP/opus-mt-tc-big-en-ko](https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-en-ko)의 고정 revision `ae8606b7b29a495f31ce679cee2007f536a3a5ce`를 사용한다. 실제로 불러온 모델은 전체 211,223,552개, 학습 가능한 파라미터는 209,126,400개다. 기존 Argos의 용어 사후 보정과 별도로 실제 가중치를 갱신한다.

학습 환경은 `.venv-training/`, 기반 모델·체크포인트·평가 기록은 `.training/`에 둔다. `npm run setup:training`으로 설치하고 이후 학습·평가는 로컬 파일만 사용한다. 앱의 기본 Argos 환경과 운영 DB는 그대로 사용한다. 학습된 결과가 자동으로 앱 기본 번역기를 바꾸지는 않는다.

초기 데이터는 train 200쌍(금융 160·일반 40), dev 40쌍(금융 30·일반 10), 독립 최종 test 60쌍이다. 모두 도우미가 작성한 미검수 보조 문장으로, 실제 사용자 검수쌍이나 다모다란 공식 한국어 번역이 아니다. [데이터 출처·분할 기준](content/training/README.md)을 확인할 수 있다. dev로 모델을 선택하고 최종 test로 다시 학습을 조정하지 않는다.

`finance-v3`는 Intel Arc 140V의 XPU/FP32에서 **150 updates·3 epochs 학습을 완료**했다. dev로 step 100을 선택했고 255개 tensor 중 253개에서 값이 바뀌었다. 학습 전후 모두 원래 SentencePiece ID에 맞게 복원한 같은 토크나이저를 사용했다. 어휘 복원 효과를 학습 성과로 세지 않는다.

고정 test 60쌍에서 용어 적중은 **15/48 → 25/48**, 전체 chrF는 **30.668730 → 34.473414**로 증가했고 금융·일반 판정 기준을 통과했다. 전체 숫자 검사 일치는 두 모델 모두 **59/60**이며, 원문에 명시적 숫자가 있던 5개 행은 모두 보존했다. 개별 의미 오역과 회귀가 남아 전문 검수 완료를 뜻하지 않는다. 데이터·손실·기간·해시·영역별 정확 수치는 [실제 학습 보고서](content/training/TRAINING_REPORT.md)에 있다.

**실제 학습한 FP32 모델은 로컬 CLI로 제공하고, 앱은 Argos를 유지한다.** 첫 INT8 변환은 금융·일반 BLEU 회귀로 실패했다. 학습된 decoder 시작 임베딩을 보존한 v2를 별도로 변환해 다시 비교했지만 금융 BLEU가 15.777430 → 14.666549로 1.110881점 하락하여 허용치 1점을 넘었다. 일반 영역은 통과했으나 앱 등록은 실행하지 않았다. 양자화만을 원인으로 단정하지 않으며 추가 변형·설정 스윕·재학습 없이 이번 실험을 종료했다. 학습 가중치·최종 test·판정 기준과 첫 실패 기록을 보존했다.

선택된 FP32 모델의 실제 CPU 추론을 확인했다. 아래처럼 사용할 수 있다. 이 실행은 앱 번역 제공자를 바꾸거나 SQLite에 번역을 저장하지 않는다.

```powershell
npm.cmd run infer:model -- --run-id finance-v3 --text "The cost of equity reflects the return required by shareholders."
```

`export:model`의 현재 경로는 시작 임베딩을 보존하는 `scripts/model-training/export_model.py`다. 첫 변환·v2의 결과와 보존 경로는 [구현 상태](IMPLEMENTATION_STATUS.md), 실행 단계·옵션은 [학습 도구 안내](scripts/model-training/README.md)를 따른다. 새 finetuned 모델의 앱 E2E를 통과한 것으로 표시하지 않으며, 현재 Argos 앱의 번역·저장·캐시 E2E 18개는 통과했다.

`.training/`의 학습 가중치·체크포인트·optimizer 상태·평가 기록은 **DB 백업에 포함되지 않는다.** 학습을 정상 중단하거나 완료한 뒤 실행 폴더와 사용한 데이터·설치 명세를 별도 저장소에 보관한다. 기반 모델 재설치만으로 개인 미세조정 결과를 복구할 수는 없다.

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
.venv-training/        별도 모델 학습 Python 환경
.training/             기반 모델·개인 학습 가중치·체크포인트·평가 기록
```

DB는 `better-sqlite3`와 버전 관리하는 SQL 마이그레이션으로 관리한다. WAL·외래키·busy timeout·짧은 트랜잭션을 적용했다. 설치한 드라이버의 SQLite 런타임은 3.53.4다. 초기 Drizzle 후보 대신 직접 파라미터 SQL을 선택하여 복합 외래키·작업 lease·네이티브 백업을 한 계층에서 관리한다.

`DATA_DIR`는 앱 루트 기준 상대 경로 또는 절대 경로다. 웹·worker·CLI는 공통 설정을 읽는다. DB 내부 파일 경로는 DATA_DIR 기준 상대 경로로 저장하므로 다른 폴더로 복원할 수 있다. 원문 업데이트는 새 버전을 만들고 이전 번역과 메모를 유지한다.

DB·원문·키·로컬 번역 환경과 모델은 Git과 public 폴더에서 제외한다. PDF.js worker·폰트·CMaps·WASM은 setup/build에서 설치 패키지로부터 로컬 public 자산으로 복사한다. 저장 원문·번역·메모를 읽을 때 외부 CDN이 필요하지 않다. 오프라인 읽기와 설치 후 로컬 번역은 로컬 웹·worker가 실행 중인 상태에서 인터넷 없이 이용하는 기능이다. 새 원문 가져오기·최초 모델 설치·선택형 OpenAI 번역은 연결이 필요하다.

## 백업과 복원

```powershell
npm run backup
```

SQLite 백업 API로 DB 스냅샷을 만들고 그 DB가 참조하는 원본·자산 파일을 복사한다. DB에 기록된 해시·크기와 실제 파일을 대조하고 manifest를 남긴다. `.env.local`과 키, 재설치 가능한 Python 환경·모델은 제외한다. 새 PC에서는 `npm run setup:translation`으로 번역 환경을 다시 준비한다. 실행 중인 SQLite 파일 하나를 수동 복사하지 않는다.

별도 미세조정 작업의 `.training/`도 이 백업 대상에 포함되지 않는다. 이 안의 개인 학습 결과는 재설치 가능한 기본 모델과 다르므로 위의 별도 보관 절차를 따른다.

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
| `npm run setup:training` | 격리된 학습 Python 환경·고정 공개 Marian 모델 설치 |
| `npm.cmd run bench:model -- --run-id <ID>` | 실제 optimizer 시험 갱신·가중치 변화·시간 확인. 시험 가중치는 본학습에 사용하지 않음 |
| `npm.cmd run train:model -- --run-id <ID>` | train으로 가중치 학습·체크포인트 저장·dev 평가와 선택 |
| `npm.cmd run evaluate:model -- --run-id <ID>` | 고정 최종 test에서 기반 모델과 선택한 학습 모델 비교 |
| `npm.cmd run export:model -- --run-id <ID>` | 평가 기준을 통과한 모델의 배포 형식 변환. 앱 적용·추론 동등성 검증과 별개 |
| `npm.cmd run infer:model -- --run-id <ID> ...` | 선택한 원시 학습 모델의 로컬 단문 추론. 입력 옵션은 학습 도구 안내 참조 |
| `npm.cmd run verify:model-export -- --run-id <ID>` | 변환본과 선택 FP32 모델의 저장된 dev 결과 비교. 최종 test 재사용 없음 |
| `npm.cmd run register:model -- --run-id <ID>` | 최종 평가·변환 비교·무결성을 통과한 모델만 등록. 제공자 설정 전환과 별개 |
| `npm run test:model` | 실제 모델을 로딩하지 않는 학습·추론 helper 회귀 검사 |
| `npm run backup` / `npm run restore -- ...` | 백업 / 새 폴더 복원 |
| `npm run typecheck` | TypeScript 검사 |
| `npm test` | 임시 DATA_DIR에서 저장·추출·작업·번역 무결성 테스트 |
| `npm run test:browser` | 격리된 DB·웹·worker에서 저장·PDF·재시작 브라우저 검증 |
| `npm run test:smoke` | 실행 중인 웹의 PC·모바일 주요 화면 확인 |

학습 명령의 `<ID>`는 같은 실험에 사용할 실행 ID로 바꾼다. 학습·최종 평가·내보내기·등록은 순서와 전제조건이 있으므로 [학습 도구 안내](scripts/model-training/README.md)를 먼저 따른다. `finance-v3`의 고정 test는 이미 최종 비교에 사용했으므로 같은 test 결과를 보며 새 학습을 반복하지 않는다. **Windows PowerShell 5.1에서는 옵션이 있는 학습 명령에 `npm.cmd`를 사용한다.** `npm.ps1`을 거치면 `--run-id` 등의 인자가 누락되는 문제를 실제 확인했다. 학습 도구 안내의 Python 직접 실행도 사용할 수 있으며 옵션 없는 `npm test`·build 등은 이 문제와 구분한다.

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
