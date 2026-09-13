# Qwen3.5-9B 독립 영한 의미 검토 실험

Hy7 번역 뒤 별도 Qwen 가중치로 원문과 번역을 대조하는 개발용 CLI다. 공개 모델 설치, 기능 검증, 의미 품질 평가, 앱 등록을 구분한다. 기존 COMET-QE, 번역 제공자, 운영 DB, 등록 Python과 가상환경을 변경하지 않는다. 학습하지 않으며 95% 검출·정밀도를 보장하지 않는다.

`prepare.py --download`는 Unsloth `Qwen3.5-9B-Q4_K_M.gguf`의 고정 revision `3885219b6810b007914f3a7950a8d1b469d598a5`, 5,680,522,464 bytes, SHA-256 `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8`를 검증한다. 원본 Qwen 및 양자화 metadata 라이선스는 Apache-2.0, 공개 ungated다. 원본 관측 revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`는 실제 변환에 사용된 가중치 revision의 입증이 아니다. 공식 라이선스 원문·카드·Hub metadata를 `.translation/qe/llm-candidates/qwen35-9b/evidence/`에 보존한다.

Windows CPU llama.cpp b10888 / commit `72797e89198ab564fd0e6baa54ab196e8dd1d884`는 기존 Hy30 설치의 runtime-cpu 폴더를 읽기 전용으로 재사용한다. 공식 CPU ZIP SHA와 51개 파일을 대조하고 `--version`/`--help`만 실행해 설치 계약을 확인했다. 모델 로더 지원은 `src/models/qwen35.cpp`의 32층 9B 분기로 확인한다. 실제 모델 시작·토크나이저·JSON 결과는 별도 smoke에서 확인해야 한다. 런타임 라이선스는 MIT다.

```powershell
python -B -X utf8 scripts/local-qe/llm-qwen35/prepare.py --download
python -B -X utf8 scripts/local-qe/llm-qwen35/test_contract.py
python -B -X utf8 scripts/local-qe/llm-qwen35/test_runtime.py
python -B -X utf8 scripts/local-qe/llm-qwen35/prepare_inputs.py
```

기존 경로를 덮어쓰지 않는다. 중단된 GGUF `.part`는 동일 pinned URL의 Range 요청으로 재개하고 완성 뒤 전체 해시를 확인한다. 관측된 IPv6 TLS reset 때문에 인증 검증을 유지하는 Windows curl IPv4를 사용한다. 전송 실패 JSON을 보존한다. 이미 install manifest가 있으면 재설치를 거부한다.

모델 입력은 `source`, `translation`, `context`뿐이다. `id`는 로컬 대응용이고 모델에 전달하지 않는다. 직접 입력 reader는 추가 키·중복 키·중복 id·상한 초과·실제 chat 제어 토큰을 거부한다. 기존 dev48 input의 출처·번역 모델 metadata는 `prepare_inputs.py`의 투영 단계에서 제거한다. 참조 번역·checks·판정·namedError·labels 파일을 읽거나 모델에 보내지 않는다. 입력의 명령문은 분석할 문자열이다.

고정 `prompt-v1.txt`와 `response-schema-v1.json`을 사용한다. 조건·부정·주체/수취·수량/연산·비교·시간·대명사·허용/의무를 검사하고 자연스러운 다른 표현을 허용한다. 의미 오류, 불확실성, 유창성/용어 표기 메모를 별도 배열로 반환한다. 의미가 바뀐 용어는 의미 오류로 기록한다. JSON Schema와 실제 continuous quote를 따로 검증한다. 유일한 quote만 Python 검증기가 UTF-16 offset으로 변환하고, 없는/중복 quote는 의미 주장을 보존하면서 문단 검토 권장·위치 미확인으로 남긴다. `no_findings`는 모델이 오류를 보고하지 않았다는 뜻이며 사용자 검수나 의미 품질 인증이 아니다.

명시된 `qwen35-cpu-8g-v2`는 CPU 8 threads·ctx8192·output2048·1slot이다. 기존 12GiB+4GiB 제안은 `resource-profiles.json`에 미실행으로 보존했다. 시작 전 물리 여유와 commit 각각 11GiB를 요구하며, 소유 child의 working set·private bytes가 8GiB를 넘거나 시스템 여유가 1GiB 아래면 중단한다. working set hard limit은 전체 프로세스 집계·commit·OS 캐시의 한도가 아니다. KV 256MiB/recurrent core48MiB는 원본 config에 따른 계산이고 실제 scratch·최고 메모리는 미측정이다. mmap·공유 GPU 메모리를 추가 RAM으로 계산하지 않는다. 공간이 부족하면 조용히 문맥이나 양자화를 바꾸지 않는다.

`runtime.py`는 `--run` 없이는 요청 묶음만 작성한다. 실제 실행에는 기존 Windows CPython 3.11과 coordinator의 단일 모델 슬롯 승인 기록이 필요하다. 고정 소유 Job·CREATE_SUSPENDED·한도 readback 후 child를 시작한다. 원래 Job/프로세스 도우미 3개의 해시를 확인하고 읽기 전용으로 가져온다. 다른 프로세스를 종료하지 않는다. 시작600초·행600초·전체4시간, 응답2MiB 상한을 고정한다. 실패와 미실행 행을 구분하고 raw bytes·prompt/token 결과·메모리·소유 종료 확인을 보존한다. 자식 종료가 미확인되면 run lock을 보존한다.

```powershell
# 추론 없는 요청 준비: output은 반드시 새 이름
python -B -X utf8 scripts/local-qe/llm-qwen35/runtime.py --input .translation/qe/llm-candidates/qwen35-9b/inputs/dev48-source-only-v1.jsonl --output .translation/qe/llm-candidates/qwen35-9b/runs/prepared-dev48-v1

# 실제 추론: 다른 모델 종료와 슬롯 승인을 확인한 뒤 실행
.venv-training/Scripts/python.exe -B -X utf8 scripts/local-qe/llm-qwen35/runtime.py --input .translation/qe/llm-candidates/qwen35-9b/inputs/smoke-first3-v1.jsonl --output .translation/qe/llm-candidates/qwen35-9b/runs/smoke-first3-v1 --run --slot-approval root-approved-qwen-first3-after-build-20260911
```

실제 GGUF의 EOS는 248046 `<|im_end|>`이며, 현재 원본 config의 숫자 248044를 덮어씌우지 않는다. GGUF의 EOS 문자열/ID와 runtime EOG 로그·boundary tokenize 결과를 검사한다. `enable_thinking:false`를 실제 chat template에 넘겨 생성한 prompt의 빈 think suffix를 확인한 뒤 `/completion`에 동일 tokens와 JSON schema를 전달한다. EOS 종료·미잘림·실제 sampling을 검사한다. 사고 모드를 껐을 때 정확도와 출력 한도를 줄였을 때 품질은 공개 benchmark와 동일하다고 간주하지 않는다.

프롬프트·스키마·실행 구성은 실제 출력 전에 동결한다. 출력 관측 뒤 의미 지시를 변경하려면 새 prompt 버전과 새 실행 기록이 필요하다. 합성 검사나 첫 3개 기술 smoke는 기존 TG27·Hy30 비교, 48개 개발 평가 또는 독립 검수의 대체물이 아니다.

공식 근거: [Qwen 모델 카드](https://huggingface.co/Qwen/Qwen3.5-9B), [Unsloth 양자화](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF), [b10888 release](https://github.com/ggml-org/llama.cpp/releases/tag/b10888), [Qwen35 로더](https://github.com/ggml-org/llama.cpp/blob/72797e89198ab564fd0e6baa54ab196e8dd1d884/src/models/qwen35.cpp), [서버 계약](https://github.com/ggml-org/llama.cpp/blob/72797e89198ab564fd0e6baa54ab196e8dd1d884/tools/server/README.md).
