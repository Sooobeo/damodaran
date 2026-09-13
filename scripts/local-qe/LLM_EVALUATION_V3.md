# 의미 전용 고정 개발8 읽기 평가기 v3

현재 상태: **구현 및 합성 검사 완료, root 검토·실제 실행 증거 검증 미완료**.
사용자의 2026-09-11 중단 지시에 따라 이 평가기의 작성 중 작업만 마무리한다.
이 작업에서 모델 로드·native 실행·vocab 실행·설치·DB 변경은 하지 않았다.

`evaluate_llm_review_v3.py`는 고정 개발8, `qwen35-semantic-review-run-v5`,
`qwen35-meaning-v2`, `qwen-material-warning-policy-v3`, `screen_gate_v1`만 지원한다.
기존 v2 평가기와 원래48 정답, 이전 결과는 변경하지 않는다. `--run`은 읽을 폴더다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 -m unittest discover -s scripts/local-qe -p test_llm_evaluation_v3.py
# 아래 평가 명령은 실제 실행과 root binding이 준비된 후 사용한다. 현재 실행하지 않았다.
.venv-training\Scripts\python.exe -B -X utf8 scripts/local-qe/evaluate_llm_review_v3.py --run <기존-v5-run-폴더> --input .translation/qe/llm-candidates/qwen35-9b/inputs/semantic-v2-dev8-v1.jsonl --execution-binding <root-검토-binding.json> --output <새-평가.json>
```

API는 `analyze_run(run, input_path, binding_path)`이며 JSON 직렬화 가능한 결과를 반환한다.
출력 파일은 새 파일만 허용한다. 손상·미지원 계약은 예외로 거부하고 통과 결과를 만들지 않는다.
Python 모듈은 고정 경로와 SHA가 일치하는 contract/mapping/gate만 import한다.
실행기·소유 helper·tokenizer는 코드 해시를 읽으며 import하지 않는다.
summary 또는 receipt가 지정한 임의 코드를 import하지 않는다.

root의 `qwen-v5-dev8-execution-binding-v1`, `rootReviewed:true`와 정확한
`freeze-v5-dev8.json` SHA가 필요하다. freeze는 평가기 자체 SHA, 정확한 실행 코드·Python·
소유 helper·자료·정책 파일 집합, profile 및 vocab audit SHA를 묶는다.
아직 만들어지지 않은 freeze나 미완료 audit를 우회하는 준비 모드는 없다.

고정8 입력을 원래48의 source/translation/context와 순서까지 대조한다.
각 응답의 저장 바이트 SHA, 실제 template/prompt, token IDs와 vocab audit의 일치,
4096 문맥과 2048 출력 예약, sampling, EOS/잘림, JSON schema, cache 계산을 검증한다.
계약과 material mapping 및 screen gate를 원시 content에서 다시 계산한다.
UTF16 위치는 계약이 실제 quote의 유일 일치로 산출하며 이 검증은 의미 정확성 판정이 아니다.

CPU4/BelowNormal, 단일 슬롯/checkpoint3, 시작 여유9GiB, 자식 WS/private6GiB,
소유 PID/creation, CPU 누적량, 요청 phase/heartbeat, guard, 최종 무결성과 종료를 대조한다.
정확한 6GiB 경계는 허용하고 초과를 거부한다. audit의 vocabulary-only 원본 코드,
10개 도움말·버전·행 tokenizer의 종료 및 코드 정체성도 읽기 검증한다.
모델 파일은 기존 설치 manifest와 stat 정체성을 확인하며 가중치를 재해시하거나 로드하지 않는다.
토큰의 의미를 새 tokenizer로 재산출하는 것이 아니라 native 기록과 동결 audit의 일치를 검증한다.

`completed`, `stopped_futility`, `failed`를 보존한다. 첫 유효 FP/FN은 완료 단위에 포함하되
그 뒤 template/tokenize/generation 증거가 있으면 거부한다. 무효·실패·미실행은 검출 성공으로
세지 않는다. 양성 미완료는 개발8의 FN에 포함하고 분모0은 null로 남긴다.
부분 응답의 유효성과 실행 전체의 기술 종료는 별도 필드다.
`diagnosticAll8Matched`와 무관하게 `fullBaselineAccepted`, 채택·등록은 항상 false다.

13/14 재현율 또는14/15 정밀도 상한은 **관측 출력을 유지한다는 조건**에서만 적용한다.
전체48의95/95, 모델별·split별·교차별 기준과 필수6 sentinel은 그대로이며 개발8은 대체하지 않는다.
8개는 정상4/오류4이고 source SHA가 유일해도 근접 대립 문장의 의존성이 남는다.
정상4개가 모두 Hy7인 선택 편향과 기존 개발 노출이 있다. 독립 test가 아니다.
빨간 구간의 의미 precision은 `not_evaluated`; quote 유일 일치 수만 별도 기록한다.

합성 검사는 첫 FN/FP 상한, 전체8 일치의 비채택, 실패 부분 보존, null 분모,
EOS/잘림·template/token/schema/sampling·mapping/gate 조작, PID/priority/guard,
메모리 경계, 중단 뒤 요청, 임의 파일·JSON 중복키·고정 입력·binding 거부를 다룬다.
합성 native fixture는 정체성 준비 단계를 stub한 뒤 원시 증거 경로를 검사하며,
binding/hash allowlist는 별도 거부 검사한다. 실제 runtime 검증 통과를 주장하지 않는다.

남은 일: root의 현재 코드 검토, 실제 vocab audit/profile 최종화, evaluator SHA가 포함된
freeze와 root binding 생성, 사용자가 작업을 재개한 뒤 실제 진단 실행 및 읽기 평가.
이 문서의 root 검토는 내부 통합 순서이며 새로운 사용자 허가 질문을 뜻하지 않는다.
