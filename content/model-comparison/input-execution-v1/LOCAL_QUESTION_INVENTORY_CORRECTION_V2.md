# 로컬 질문 실행기 파일 목록 교정 v2

최초 로컬 질문 실행 시도는 설치 검증의 `runtime_52_file_inventory`에서 중단됐다. 고정 설치 manifest와 실제 런타임 디렉터리는 모두 51개이며, 추가·누락 파일은 없었다. root의 원인 감사는 런타임 51개와 모델의 실제 해시를 재검증했고, 실행 폴더와 영구 generation claim이 생성되기 전 실패했음을 확인했다. 이 시도의 native 프로세스와 질문 completion은 각각 0회다.

근거: [원인·실제 파일 목록 감사](../../../.training/verifications/question-runtime-inventory-failure-audit-20260914.json), SHA256 `a6a8ac0117da40621fba5a53adf73217d75138af48879eb6ee08e8e6f73e4936`. 이 기록은 실패를 성공으로 바꾸지 않는다.

원래 [로컬 질문 실행 동결 v1](local-question-execution-freeze-v1.json)의 16개 파일, [질문 평가 절차 v1](LOCAL_QUESTION_REVIEW_PROTOCOL_V1.md), 정식 S5 packet 및 질문 export 65개 파일은 보존한다. 모델·프롬프트·JSON 문법·sampling·단위별 완전히 새 native 프로세스·단일 completion·감시·정리·질문 답변/채점 격리·평가 기준은 바꾸지 않는다. 기존 runner의 `installation()`에서만 실제 51개가 아닌 52개를 요구한 것이 이번 교정의 원인이다.

새 경로는 다음 네 모듈과 각 전용 테스트, 이 문서의 정확히 9개 파일로 구성한다. root가 `local-question-inventory-correction-freeze-v2.json`을 최초 질문 실행 전에 추가한다. 이 교정 동결은 원래 v1 동결 SHA를 바인딩하며 v1 동결을 대체하지 않는다.

- `local_question_runner_inventory_v2.py`: 실제 고정 manifest의 51개 목록과 디렉터리 전체 목록·개별 SHA를 대조한다. coordinator의 run/validate 경로를 명시적으로 분리하고 원래 `execute_context`, parity, guard, 소유 프로세스 정리를 그대로 호출한다. 실행 summary는 새 runner 버전이며, 개별 context의 실제 producer 버전은 frozen v1이다.
- `local_question_transport_inventory_v2.py`: 원래 export 검증과 reviewer·답변 구조 변환 계약을 재사용한다. 새 runner의 `validate_run()`만 명시적으로 호출하고 교정 lineage를 다시 확인한 뒤 새 collection을 기록한다. v1 collection을 새 검증 결과처럼 수용하지 않는다.
- `local_question_evaluation_inventory_v2.py`: 원래 정식 S5 loader·원문/질문 검증·집계 함수와 평가 기준을 재사용한다. 새 collection validator에 연결한 freeze/read/report 경로에 교정 lineage를 함께 보존한다. 64개 답변이 모두 검증·동결되기 전에는 질문 채점 완료를 주장하지 않는다.
- `local_question_append_evidence_inventory_v2.py`: 새 평가 보고서와 기존 회복 relation 보고서를 재검증한다. 각 input/relation observation에 교정 lineage를 기록하며, 38개 원래 실패 run의 출력과 26개 회복 run의 실제 상태·정체성을 유지한다. 기본 동작은 prepare이고, `--append`가 있어야 원장 writer를 호출한다.

동결 파일의 전역 변수나 함수를 바꾸는 monkeypatch는 사용하지 않는다. 의존 함수가 전역 이름에 묶여 있어 재사용할 수 없는 수명주기 메서드는 새 모듈에 정적으로 복사하여 import와 명시적 버전·증거 연결만 바꾼다. 순수 집계·span 변환·동일성 검사와 실제 생성 코어는 원래 함수를 호출한다. 테스트의 임시 파일과 stub은 실제 실행 증거가 아니다.

`runner.load_correction(root)`의 반환은 `{version, baseExecutionFreeze, correctionFreeze, files}`이며 각 파일 참조는 `{path, sha256}`다. run plan/summary, collection, 답변 동결 영수증, 평가 보고서에 같은 `inventoryCorrection`을 기록한다. 새 adapter에서 한 개라도 파일이 바뀌거나 원래 동결/새 교정 동결이 다르면 실행·검증·수집·동결을 거부한다. 코드와 작은 원시 증거만 일반 evidence 목록에 넣고, 큰 모델 가중치는 runner 내부에서 스트리밍 해시로 검증한다.

아래 명령은 이미 확정된 질문 export와 새 출력 폴더를 사용한다. 실제 native 실행은 다른 모델이 종료되고 전원·자원 검사가 통과한 뒤 root가 수행한다. 기존 실패 시도의 기록이나 기존 질문 export를 덮어쓰지 않는다.

```powershell
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_runner_inventory_v2.py run --export <기존-질문-export> --destination <새-native-run>
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_transport_inventory_v2.py collect --formal <정식-S5> --export <기존-질문-export> --run <검증완료-native-run> --destination <새-collection>
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_evaluation_inventory_v2.py freeze-answers --folder <정식-S5> --collection <새-collection>
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_evaluation_inventory_v2.py report --folder <정식-S5> --source-reviews <확정-원문검토> --grades <동결답변-별도채점> --output <새-보고서>
.venv-training/Scripts/python.exe -B -X utf8 scripts/model-comparison/input_execution_v1/local_question_append_evidence_inventory_v2.py --folder <정식-S5> --cohort <회복-cohort> --source-reviews <확정-원문검토> --grades <동결답변-별도채점> --report <새-보고서> --relations <회복-relation> --destination <새-prepare>
```

이번 교정은 실제 질문 답변, 답변 정확성, 후보 개선 또는 S6 수용을 입증하지 않는다. 질문의 유효한 전체 64개 답변을 먼저 동결하고, 별도 원문 평가자가 원문/고정 정답에 근거하여 채점한 뒤 기존 개발 선택 조건을 적용한다. 새 holdout은 후보 선정·동결 이후 별도 독립 context에서 준비한다.
