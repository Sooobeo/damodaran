# 문맥을 받는 로컬 번역 실행기

선택형 `hymt` 제공자 연결을 구현하고, [Q26 비교](../../content/model-comparison/QUALITY_LOCAL_REPORT.md)의 네 구성·24문단·96개 익명 도우미 판단을 마친 뒤 Hy-MT2 7B Q8의 `contextual` 구성을 등록했다. 최초 앱 QA 시작 실패 후 원자적 Windows Job 생성으로 수정했고 실제 제품 엔진의 모델 시작·정상 종료와 새 등록 CLI를 통과했다. 후속 v1 앱 QA에서 HTML 3문단·PDF 7블록의 생성·저장·캐시와 자동 검사를 완료했다. PDF 도입 블록의 의미 오류를 보완한 v2에서도 새 PDF 4블록의 실제 생성·저장·캐시를 확인했고 기존 HTML 3문단은 추가 추론 없이 캐시를 재사용했다. 실제 운영 빌드를 통과한 뒤 이 PC의 `.env.local`에 `TRANSLATION_PROVIDER=hymt`를 명시하고 단일 웹·worker를 시작했다. 운영 HTML 3문단의 새 생성·저장·캐시 검증도 완료했고 최종 API의 `configured`·`liveVerified`가 모두 `true`다. 운영 검증 범위는 `html-only`이며 PDF 성공은 앞선 격리 앱 검증의 결과다. 새 설치의 초기 기본값 Argos와 이 PC의 명시 선택을 구분한다.

현재 등록은 `hymt-contextual-q26-20260910-nativejob`, manifest SHA `cb3031d87372fbb1b0c3dcaf2071064bb59a8a21bd96480ef2d5a66752ae52bd`다. 최초 `hymt-contextual-q26-20260910`의 SHA `8f898e6358c336e6eef44a562b84da63e861d7deb327399959e2aabbc8e12840`과 [당시 코드 6개](../../.training/verifications/hymt-initial-registration-code-20260910/manifest.json)는 이전 등록의 근거로 보존한다. 모델 설치·구성 등록·엔진 시작과 실제 앱 사용 검증을 구분한다.

`engine.py`는 고정한 Hy-MT2 Q8 비교 실행기의 모델·토크나이저·번역 지시·생성 설정을 사용한다. 문단과 참고 문맥을 구분하고 번역 출력에 용어 치환을 적용하지 않는다. 빈 출력·잘림·제어 토큰 유출을 거부하고 숫자·통화 표기 차이는 검토 필요 사유로 반환한다. 이런 자동 검사가 의미 정확성을 보장하지는 않는다.

`process_owner.py`는 Python 실행기를 전용 Windows Job Object에 넣고, `_OwnedPopen`의 `CreateProcessW`에서 `PROC_THREAD_ATTRIBUTE_JOB_LIST`로 native 서버를 같은 Job에 원자적으로 배정한다. 암묵적 상속에 기대거나 서버 생성 뒤에만 배정하지 않는다. `HANDLE_LIST`에는 자식의 표준 입출력 핸들만 전달하고 Job 핸들은 상속하지 않는다. 배정할 수 없으면 생성에 실패하며 소유권 없는 방식으로 재시도하지 않는다. 정상 종료에서는 서버를 종료하고 기다린 뒤 Python이 반환하며, Python 강제 종료 시에는 OS가 소유 핸들을 회수해 같은 Job의 서버도 종료하게 한다. 소유하지 않은 앱이나 번역 작업을 종료하는 명령은 사용하지 않는다.

서버를 생성한 뒤에도 실제 프로세스 핸들의 Job 소속을 확인하고, 확인되지 않으면 방금 만든 Popen 핸들만 종료·거부한다. 지원 대상은 Windows·CPython 3.11의 앱 고정 native 서버이며 일반 venv redirector의 모든 자손을 포괄하는 실행 도구로 제공하지 않는다. 기본·가상환경 부모, Node 강제 종료·stdin EOF, `spawn()` 반환 전 부모 종료, Unicode argv/env/cwd, binary pipe와 합친 stderr를 검사했다. 프로세스·엔진·bridge의 최종 통합 [67개 검사](../../.training/verifications/hymt-job-list-tests-20260910-final.json)가 모두 통과했다.

추론 전 모델 로그에서 고정된 토크나이저 증거를 확인하고 버린다. 실제 원문을 보내기 전 로그 수집을 중지하며 원문·문맥·응답을 콘솔이나 파일에 기록하지 않는다. 원문 요청은 인증된 loopback 연결 안에서 처리한다.

`bridge.py`는 등록된 전체 모델 정체성을 준비 응답과 각 요청에서 확인한다. 원문·문맥은 별도 필드로 보내고 앱의 일반 용어 목록 대신 등록한 고정 사전만 사용한다. 요청 중 실행 파일이나 등록본이 바뀌면 생성한 결과를 정상 응답으로 반환하지 않는다. 빈 출력·누락·중복·잘림 및 숫자·수식 검사는 앱의 저장 경로에서도 유지한다.

TypeScript 제공자·캐시·무료 사용량 연결과 기존 유료 작업 재개 차단을 구현했다. 초기 관련 검사 32개와 타입 검사, 실행기 mock 26개·NDJSON mock 14개를 통과했고 Windows 프로세스 보완 후 관련 19개를 기본·가상환경 Python에서 각각 통과했다. Hy-MT2의 Q26 원문만·문맥 구성은 각각 24개 실제 번역을 완료했지만 이 결과를 앱의 작업·저장·캐시 검증으로 대신하지 않는다. [첫 앱 QA 실패 기록](../../test-results/hymt-runtime-verification-1789021931429.json)은 실제 PDF의 새 `pdf-paragraphs-v1` 3페이지 7블록 전문·순서 검증 후 `real-translation` 단계에서 중단됐음을 남긴다. 등록 명세·명령은 [연결 계약](DEPLOYMENT.md)을 따른다.

원자적 Job 생성 제품 코드 SHA는 `9a00d809466e304359fb47c374c8fc2464ce31cdbc9557fbc9eee089bfe9d8b2`다. 이 코드로 실제 `OwnedHymtEngine`의 [시작·정상 종료](../../.training/verifications/hymt-native-job-startup-20260910.json)를 확인했다. 이 시작 시험의 원문 전송과 생성 요청은 0개였으며, 후속 v1 앱 번역 QA 완료와 구분한다. 이전 격리 Next.js 빌드는 이 Python 수정 전 코드이며, 이후 [실제 운영 빌드](../../.training/verifications/hymt-production-build-20260910.json)는 새 코드로 exit 0을 기록했다.

[v1 실제 앱 QA](../../test-results/hymt-runtime-verification-1789024231920.json)는 모두 자동 통과했지만 [PDF 원문 대조](../../.training/verifications/hymt-app-pdf-v1-assistant-review-20260910.json)에서 미완결 도입부에 다음 하위 항목의 내용이 추가된 오류가 확인됐다. [v2 실제 QA](../../test-results/hymt-runtime-v2-verification-1789025037793.json)는 기존 HTML 3문단의 유효 캐시를 재사용하고 새 PDF 4블록만 실제 생성해 저장·캐시 검사를 완료했다. Root 도우미 대조에서 도입부와 3개 조건이 보존돼 v1의 내용 추가가 해소됐지만 본문의 `자산 in place`는 남았다. v1 데이터·출력·검증 기록은 보존하며 모델·프롬프트·사전·Q26 결과는 바꾸지 않는다.
