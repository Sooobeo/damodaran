# 로컬 번역 모델 가중치 미세조정

이 경로는 영어→한국어 번역 모델의 **실제 파라미터를 역전파로 갱신**하는 학습 도구다. 기존 Argos의 용어 교정 및 SQLite 검수 번역 재사용과 별개다. 처음부터 번역 모델을 만드는 사전학습이 아니라 공개 번역 모델의 추가 미세조정이다.

## 기반 모델과 설치

- 원저작자: [Helsinki-NLP / OPUS-MT](https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-en-ko).
- 기반 모델: `opus-mt-tc-big-en-ko`, MarianMT, 영어→한국어.
- 고정 revision: `ae8606b7b29a495f31ce679cee2007f536a3a5ce`.
- 모델 가중치 SHA-256: `f7d6ccf642f1672e6b06d46bc406a3f12220b70603f6745dfbce5c097f8511c2`.
- 이용 조건: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). 파생 모델을 공유한다면 원저작자·원본 링크·라이선스와 금융 문장 미세조정 사실을 함께 표시한다. 기반 모델 카드도 다운로드에 포함한다.
- 기존 Argos 배포물은 추론용 CTranslate2 형식이다. 그 파일을 학습 가능한 원래 체크포인트라고 취급하지 않는다.

Windows PowerShell, Python 3.11에서 다음 명령을 실행한다. 기존 `.venv-translation`은 변경하지 않는다.

```powershell
py -3.11 scripts/model-training/setup.py
```

기본은 Intel XPU용 PyTorch 2.8이다. Intel GPU가 없는 PC는 `--backend cpu`를 붙인다. `.venv-training`에 학습 패키지, `.training/base-model`에 검증된 기반 모델을 설치하고 실제 설치 목록을 `.training/requirements.lock.txt`에 기록한다. 설치에만 인터넷 연결이 필요하다. 학습 시 로컬 파일만 사용한다.

이 PC에서 확인한 Intel XPU 환경 전체 버전은 `requirements.xpu.lock.txt`에 보존한다. 기본 설치 후 같은 환경을 재현할 때 `.venv-training/Scripts/python.exe -m pip install -r scripts/model-training/requirements.xpu.lock.txt --extra-index-url https://download.pytorch.org/whl/xpu`로 맞출 수 있다. 이 잠금 파일은 Intel XPU용이므로 CPU 전용 설치에 적용하지 않는다.

## 실행 순서

```powershell
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/train.py bench --run-id finance-v2
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/train.py train --run-id finance-v2
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/train.py evaluate --run-id finance-v2
```

첫 `bench`는 실제 optimizer 업데이트 2회로 시간을 측정하고 그 가중치를 폐기한다. `train`은 원본에서 시작해 3회 학습하며 개발 점수로 체크포인트를 선택한다. `evaluate`는 선택된 모델과 원본을 같은 조건에서 고정된 최종 평가 문장에 적용한다. 위 run ID는 이번 실행용이다. 다른 데이터·설정·학습 코드로 새 실험을 하려면 새 ID를 사용한다.

`npm run bench:model -- --run-id finance-v2`처럼 npm 명령도 제공한다. PowerShell/npm이 인자를 누락하는 환경에서는 위의 Python 직접 실행을 사용한다. 중단 후에는 같은 인자와 `train --resume`으로 마지막 정상 체크포인트부터 이어간다. 평가 재개 시 완료한 예측은 다시 생성하지 않으며 실행 장치·정밀도·생성 설정이 달라진 캐시는 혼합하지 않는다. 최종 test를 소비한 뒤 같은 test로 새 학습을 반복할 수 없게 실행 기록으로 차단한다.

최종 기준을 통과한 실행만 `export --run-id finance-v2`로 CTranslate2 INT8 형식으로 변환할 수 있다. 변환 이후의 번역 동일성·숫자 보존·앱 저장과 캐시는 별도 확인 대상이며, 변환 명령만으로 앱 설정은 바뀌지 않는다.

검증 코드 실행: `.venv-training/Scripts/python.exe -X utf8 scripts/model-training/test_training.py`.

## 데이터 출처와 분할

`content/training/train.jsonl`은 학습, `dev.jsonl`은 체크포인트 선택, `evaluation.jsonl`은 최종 비교용이다. 초기 데이터는 다모다란 자료의 금융 개념과 프로젝트 용어 표기를 참고해 도우미가 별도로 작성한 문장쌍이다. 원문 번역문 모음이나 사용자가 검수한 데이터라고 표시하지 않는다. 각 행은 출처 유형, 분할, 금융/일반 영역과 평가할 용어를 포함한다.

학습·개발·최종 평가 사이의 동일 문장과 숫자만 바꾼 문장을 검사한다. 최종 평가의 원문과 기준 번역으로 가중치를 학습하거나 하이퍼파라미터를 조절하지 않는다. 동일한 기반 모델과 학습 모델에 용어 후처리·번역 메모리를 적용하지 않고 비교해 **추가 학습의 효과**를 측정한다. 일반 문장 점수도 함께 확인한다.

이 작은 도우미 작성 표본은 전문 번역 품질의 보증이 아니다. 사용자 검수 문장이 쌓이면 `npm run export:translation-memory`로 내보내 별도 검토·중복 제거·문서 단위 분할을 거쳐 후속 데이터에 포함할 수 있다. 검수 저장 자체가 자동 학습을 실행하지는 않는다.

## 저장과 재현

학습 환경과 모델·체크포인트는 Git 및 웹 공개 경로에서 제외한다. 실행별 데이터 해시, 설정, 학습 손실, optimizer 갱신 수, 가중치 변화, 개발·최종 평가를 `.training/runs/`에 보존한다. 학습된 가중치는 원본에서 재설치할 수 없는 개인 파생 결과물이므로 **별도 보관 대상**이다. 앱의 SQLite 백업 명령은 이 학습 폴더를 포함하지 않는다.

최종 평가를 통과하지 못한 체크포인트를 앱 기본 번역기로 자동 전환하지 않는다. 학습 완료와 번역 품질 개선, 앱 적용 완료를 각각 구분해 보고한다.
