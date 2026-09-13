# 일반 번역·문맥 프로필 비교 CLI

동결된 [general16 자료](../../content/model-comparison/general-context-dev-20260911.jsonl)를 같은 Hy-MT 후보의 `raw`와 `contextual`로 비교하기 위한 별도 실행기다. 기존 생산자·공통 프롬프트·등록 파일·Python 앱 제공자를 수정하지 않는다. 개발 관측이며 새 학습이나 앱 승격은 수행하지 않는다.

`hy7`은 기존 Q8 모델과 b10874 CPU 명령·EOS 보정·샘플링을 사용한다. `hy30`은 기존 v4 Q4 모델과 b10888 CPU mmap/no-repack 명령·원본 템플릿·샘플링을 사용한다. 두 후보의 샘플링을 서로 같게 만들지는 않는다. **한 후보 안에서 두 프로필의 설정이 같다.** Hy30의 요청 top-k=-1과 실제 보고 0의 기존 검증도 그대로 사용한다.

새 실행기는 두 후보 모두 기존 v4의 소유 프로세스 생성·일시정지 상태에서 작업 집합 제한 적용·메모리 감시를 재사용한다. Hy7에도 이 제한을 적용하는 별도 실험이므로 과거 무제한 Hy7 실행의 출력 바이트나 속도와 같다고 가정하지 않는다. 기본 native 작업 집합 관측 상한은 12 GiB이고 8~12 GiB 중 명시 선택할 수 있다. 여유 물리 메모리와 commit도 확인하며 메모리 부족이 전혀 없다는 보장은 아니다. startup 한도는 Hy7 240초·Hy30 1800초, 요청 한도는 1800초, 전체 감시 한도는 12시간이다.

`general_context_input.py`는 자료와 manifest SHA를 고정하고 16개 ID·일반 12/문맥 4·원문/문맥 SHA·학습 금지·원문 감사를 확인한다. 모델 경계로 나가는 행 필드는 `id/source/context/domain/sourceSha256/contextSha256`뿐이다. `referenceKo`, `checks`, 원문 감사는 모델 입력으로 보내지 않는다.

`raw`는 기존 일반 한국어 번역 지시와 source만 사용한다. `contextual`은 기존 다체·원문 관계 보존·불필요한 제목/설명 금지·조건부 용어·이웃 문맥 지시를 그대로 사용한다. 이 자료에 별도 제목 필드는 없으며 새 제목 힌트를 만들어 넣지 않는다. 단일 일반어를 무조건 금융 용어로 치환하는 처리도 추가하지 않는다. 문맥 4개는 모두 비금융 의미이므로 금융 의미를 함께 시험하는 균형 자료라고 주장하지 않는다.

2026-09-11에 동결 입력과 기존 사전(SHA `d4af6a9fed8c6dba4c528abc52c64c19621c5fac98fd2bdb09e035769963de67`)을 모델 실행 없이 조립했을 때 조건부 용어 매칭은 0건이었다. 따라서 이 버전의 실제 차이는 일반 지시·문체와 4개 이웃 문맥이며, 금융 용어 힌트가 삽입된 경우의 회귀까지 검증했다고 주장하지 않는다. 이 관측을 이유로 동결 자료나 사전을 사후 수정하지 않았다.

모델을 읽거나 시작하지 않는 준비 예시:

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_general_context.py --candidate hy7 --profile raw --output .training/comparisons/general-context-dev-20260911/hy7-raw-plan
```

후보 선택 후 실행할 때만 `--run`을 붙인다. 두 명령을 순차 실행하며 각 명령은 새 폴더와 새 소유 native 프로세스를 사용한다. 아래의 `hy7`은 사용법 예시이지 후보 선택 결과가 아니다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_general_context.py --candidate hy7 --profile raw --run --output .training/comparisons/general-context-dev-20260911/selected-raw
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/run_general_context.py --candidate hy7 --profile contextual --run --output .training/comparisons/general-context-dev-20260911/selected-contextual
```

기존 출력 폴더를 재개·덮어쓰지 않는다. 실패 응답은 원시 응답 파일에 보존하며 해당 행의 실패와 나머지 미실행을 구분한다. 자동 재시도·다른 제공자로의 전환·번역 보정·번역 메모리 사용은 없다. 입력·사전·코드를 각 요청 전후 검증하고 설치된 가중치·런타임은 실행 전과 소유 자식 종료 후 검증한다. 일반 준비 경로에서는 모델·설치 파일을 읽지 않는다.

`plan.json`/`summary.json`은 입력·사전·실행 코드·프로필·샘플링·모델 식별값·원문/실제 사용 문맥/사용자 프롬프트 SHA를 기록한다. 완료 행은 전체 프롬프트 및 입출력 토큰 SHA, 원시 응답 SHA와 연결된다. 성공·실패 자동 진단을 지우지 않으며 `completed`는 요청 완료이지 의미 정확성 승인이 아니다. 준비 상태는 모델 설치·실제 추론 검증 완료가 아니다.

두 프로필이 완료되면 별도 파일 관계 검사만 실행할 수 있다. 모델 점수나 키를 생성하지 않으며 실패한 자동 검사를 품질 통과로 바꾸지 않는다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/verify_general_context_pair.py --raw .training/comparisons/general-context-dev-20260911/selected-raw --contextual .training/comparisons/general-context-dev-20260911/selected-contextual --output .training/comparisons/general-context-dev-20260911/profile-pair-evidence.json
```

실행 시점의 코드 SHA와 프로필 쌍의 코드 SHA를 비교한다. 코드 변경 이후 과거 결과의 판정을 기준에 맞추어 고치지 않으며 필요하면 새 버전의 비교를 따로 만든다. 일반/문맥 실제 번역의 익명 의미 검토는 이 CLI의 구조 검증과 별도 후속 작업이다.

합성 검사는 원문 허용 필드·참조 누출 방지·해시·원문 감사·프로필 격리·명시 실행·기존 출력 거부·두 후보의 소유 프로세스 종료·응답 실패 보존·프로필 쌍 정체성 경계를 다룬다. 모델 가중치나 native 서버를 열지 않는다.

초기 구현 검증은 합성 27개·CLI help 2개 및 실제 동결 자료의 읽기·프롬프트 해시 조립이다. 실제 Hy7/Hy30 추론과 Windows native 자식 생성은 이 작업에서 실행하지 않았다. 준비 경로 확인과 합성 자식 검사를 실제 엔진 검증으로 보고하지 않는다.

```powershell
.venv-training/Scripts/python.exe -X utf8 -m unittest discover -s scripts/model-comparison -p test_general_context.py -v
```

## 초기 구현 이후 실제 Hy7 비교

2026-09-11에 CPU4/실제 BelowNormal·8GiB 관측 한도로 raw16과 contextual16의 생성·무결성·종료를 완료했다. 같은 코드·모델·샘플링의 두 프로필을 검증하고, 후보를 보기 전의 원문 명제 계획과 새 한국어 전용 답변자를 분리해 평가했다. raw는 중요 오류3·보류1, 질문30/32·핵심15/16이고 contextual은 중요 오류2·보류1, 질문29/32·핵심14/16으로 모두 수용 기준 미달이다. 질문 점수 차이에는 답변자 차이가 있고 contextual 전체 시간에는 Modern Standby가 포함된다. [실제 비교 보고서](../../content/model-comparison/GENERAL_CONTEXT_REPORT_20260911.md)에 원시 근거와 한계를 기록했다. 위 초기 구현 당시의 미실행 기록은 당시 상태로 보존하며, 이 후속 결과를 Hy30 일반16 실행이나 금융 용어 힌트 삽입의 검증으로 확장하지 않는다.
