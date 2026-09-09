# 로컬 번역 모델 가중치 미세조정

이 경로는 영어→한국어 번역 모델의 **실제 파라미터를 역전파로 갱신**하는 학습 도구다. 기존 Argos의 용어 교정 및 SQLite 검수 번역 재사용과 별개다. 처음부터 번역 모델을 만드는 사전학습이 아니라 공개 번역 모델의 추가 미세조정이다.

2026-09-09 실행 `finance-v3`의 학습과 원본 FP32 모델의 최종 평가를 완료했다. 약 2.09억 개 학습 가능 파라미터를 150회 갱신하고 dev로 step 100을 선택했다. 고정 test 60문장에서 금융 용어 표기 적중은 15/48 → 25/48, 전체 chrF는 30.669 → 34.473이었다. 실제 tensor 변화와 평가의 한계는 [학습 결과 보고서](../../content/training/TRAINING_REPORT.md)에 기록했다.

현재 앱 기본은 **Argos**다. CTranslate2 INT8 변환과 시작 가중치를 보존한 수정본까지 실제로 검증했지만, 수정본의 금융 BLEU가 원본보다 1.111점 낮아 허용 하락 1점 기준을 통과하지 못했다. 일반 문장 기준은 수정본에서 통과했다. 학습된 FP32 원본은 아래 `infer:model`로 사용할 수 있다. 최종 test는 이미 사용했으므로 이를 보고 재학습하거나 기준 번역을 바꾸지 않는다.

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

기본은 Intel XPU용 PyTorch 2.8이다. Intel GPU가 없는 PC는 `--backend cpu`를 붙인다. `.venv-training`에 학습 패키지, `.training/base-model`에 검증된 원본을 설치하고 `.training/prepared-model`에 아래 어휘표 수정을 적용한 학습 기준본을 만든다. 실제 설치 목록은 `.training/requirements.lock.txt`에 기록한다. 설치에만 인터넷 연결이 필요하다. 학습 시 로컬 파일만 사용한다.

### 공개 모델의 어휘표 수정

고정한 공개 revision의 `vocab.json`은 한국어 target.spm의 ID표인데 영어 입력에도 공통으로 연결되어 있었다. 실제 CPU/GPU 검사에서 일반 영어 단어가 `<unk>`로 바뀌어 의미 없는 번역이 생성되는 것을 재현했다. 관련 공개 보고는 [OPUS-MT issue 81](https://github.com/Helsinki-NLP/OPUS-MT-train/issues/81)과 [tokenizer 수정 제안](https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-en-ko/discussions/7)에 있다.

`prepare_tokenizer.py`는 원본을 그대로 보존하고, 고정된 source.spm의 ID표로 영어 vocab.json을 재구성한다. 한국어 target.spm에서 target_vocab.json을 만들고 원래 한국어 어휘표와 모든 ID가 같은지 확인한 뒤 `separate_vocabs=true`로 연결한다. 가중치 파일 SHA는 수정 전후 동일하다. **어휘표 복원 자체는 학습 성과가 아니다.** 학습 전후 모두 이 수정된 어휘표를 사용한다. 각 파일의 해시와 수정 출처는 prepared-manifest.json에 기록한다.

잘못 연결된 초기 기준본으로 시작했던 `finance-v2`는 19번 업데이트 후 중단했다. 해당 번역 및 부분 학습을 품질 개선 증거로 사용하지 않는다. 최종 평가 자료는 이 실행에서 사용하지 않았다.

이 PC에서 확인한 Intel XPU 환경 전체 버전은 `requirements.xpu.lock.txt`에 보존한다. 기본 설치 후 같은 환경을 재현할 때 `.venv-training/Scripts/python.exe -m pip install -r scripts/model-training/requirements.xpu.lock.txt --extra-index-url https://download.pytorch.org/whl/xpu`로 맞출 수 있다. 이 잠금 파일은 Intel XPU용이므로 CPU 전용 설치에 적용하지 않는다.

## 실행 순서

```powershell
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/train.py bench --run-id finance-v3
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/train.py train --run-id finance-v3
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/train.py evaluate --run-id finance-v3
```

첫 `bench`는 실제 optimizer 업데이트 2회로 시간을 측정하고 그 가중치를 폐기한다. `train`은 원본에서 시작해 3회 학습하며 개발 점수로 체크포인트를 선택한다. `evaluate`는 선택된 모델과 원본을 같은 조건에서 고정된 최종 평가 문장에 적용한다. 위 run ID는 이번 실행용이다. 다른 데이터·설정·학습 코드로 새 실험을 하려면 새 ID를 사용한다.

`npm.cmd run bench:model -- --run-id finance-v3`처럼 npm 명령도 제공한다. 이 PC의 Windows PowerShell 5.1에서는 `npm.ps1`이 `--run-id` 등의 옵션을 누락하는 현상을 확인했으므로 인자가 있는 명령은 **`npm.cmd`** 또는 위의 Python 직접 실행을 사용한다. 중단 후에는 같은 인자와 `train --resume`으로 마지막 정상 체크포인트부터 이어간다. 평가 재개 시 완료한 예측은 다시 생성하지 않으며 실행 장치·정밀도·생성 설정이 달라진 캐시는 혼합하지 않는다. 최종 test를 소비한 뒤 같은 test로 새 학습을 반복할 수 없게 실행 기록으로 차단한다.

최종 기준을 통과한 실행만 `export_model.py --run-id finance-v3`로 CTranslate2 INT8 형식으로 변환할 수 있다. 현재 `npm.cmd run export:model`도 이 별도 스크립트를 실행한다. 학습 당시 `train.py`의 원래 export 구현은 재현 기록으로 보존하지만 새 배포에는 사용하지 않는다. 변환 이후의 번역 품질 비교·숫자 보존·앱 저장과 캐시는 별도 확인 대상이며, 변환 명령만으로 앱 설정은 바뀌지 않는다.

전체 학습·추론 도구 검증: `npm run test:model`. 현재 Python 검사 48개를 실행한다.

## 학습된 모델 사용과 앱 등록

현재 완료한 모델을 원본 정밀도로 직접 실행한다. CPU가 기본이고 이 PC의 Intel GPU를 쓰려면 `--device xpu`를 추가한다. 모델 파일을 검증한 뒤 로컬에서만 번역하며 용어 보정·검수 메모리를 적용하지 않는다.

```powershell
npm.cmd run infer:model -- --run-id finance-v3 --text "The cost of equity reflects the return required by shareholders."
```

위 명령의 CPU 실행에서 `자기자본비용은 주주가 요구하는 수익률을 반영한다.`를 확인했다. 결과는 번역문·숫자 경고·모델 해시를 포함한 JSON이며, 앱 엔진을 바꾸지 않는다. PowerShell 인자 전달에 문제가 있으면 다음과 같이 직접 실행한다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-training/infer.py --run-id finance-v3 --text "The cost of equity reflects the return required by shareholders."
```

앱용 변환과 등록 명령은 순서대로 다음과 같다. 현재 `finance-v3`는 두 번째 단계에서 기준 미달이므로 **등록하지 않았다**.

```powershell
npm.cmd run export:model -- --run-id finance-v3
npm.cmd run verify:model-export -- --run-id finance-v3
npm.cmd run register:model -- --run-id finance-v3
```

`verify:model-export`는 선택 체크포인트의 기존 FP32 dev 예측과 변환 모델을 비교한다. test를 다시 읽지 않는다. 금융·일반 각각 chrF/BLEU 하락 최대 1점, 용어 적중률 하락 최대 2%p, 숫자 보존 비율 하락 없음, 빈 결과·잘림 없음이 기준이다. 첫 실패 기록은 `parity-summary.json`과 `parity-dev-predictions.jsonl`, 수정본은 아래 v2 파일에 보존한다.

첫 변환에서 학습된 `<pad>` 시작 임베딩이 제거되는 차이를 확인했다. `export_model.py`는 이 행을 보존한 32,001개 별도 영한 어휘와 실제 `<pad>` 시작 토큰을 사용하고, 추론에서는 `<pad>` 출력만 금지한다. 수정본은 `ctranslate2-pad-v2/`, `export-manifest-v2.json`, `parity-summary-v2.json`, `parity-dev-predictions-v2.jsonl`에 분리한다. 기존 파일은 그대로 두고 `deployment-attempts/int8-v1/`에도 해시와 원래 경로를 포함해 보관한다. 현재 verify/register 명령은 v2 경로만 사용하며, 학습 가중치·학습 스크립트·test·통과 기준은 변경하지 않는다.

수정본의 2026-09-09 실제 dev 40문장 비교에서 금융 BLEU는 15.777430 → 14.666549로 기준 미달, 일반 BLEU는 11.563406 → 11.492243으로 기준을 통과했다. 용어는 14/31 → 15/31, 숫자 검사는 40/40 → 40/40이었다. 전체 판정은 실패이며 등록하지 않았다. 시작 임베딩 차이를 고친 뒤에도 beam 종료 방식과 CPU INT8/XPU FP32 수치 환경 차이는 남으므로 이를 양자화만의 영향으로 단정하지 않는다. 추가 설정 탐색이나 기준 완화는 하지 않았다.

`register:model`은 같은 모델·런타임·예측 해시의 통과 결과를 다시 확인하고 `.training/deployed/manifest.json`만 등록한다. 실패하면 등록할 수 없다. 등록에 성공한 후에도 앱에서 쓰려면 `.env.local`의 `TRANSLATION_PROVIDER=finetuned`를 명시하고 웹·worker를 재시작한 뒤 실제 HTML/PDF 번역·저장·캐시를 검증해야 한다. 이 단계는 이번 실행에서 수행하지 않았다. 현재 Argos의 실제 브라우저 검증 18개는 통과했다. Argos로 돌아갈 때는 `TRANSLATION_PROVIDER=argos`로 설정하고 재시작한다. 제공자·모델별 캐시가 분리되어 기존 번역과 검수 이력은 보존된다.

## 데이터 출처와 분할

`content/training/train.jsonl`은 학습, `dev.jsonl`은 체크포인트 선택, `evaluation.jsonl`은 최종 비교용이다. 초기 데이터는 다모다란 자료의 금융 개념과 프로젝트 용어 표기를 참고해 도우미가 별도로 작성한 문장쌍이다. 원문 번역문 모음이나 사용자가 검수한 데이터라고 표시하지 않는다. 각 행은 출처 유형, 분할, 금융/일반 영역과 평가할 용어를 포함한다.

학습·개발·최종 평가 사이의 동일 문장과 숫자만 바꾼 문장을 검사한다. 최종 평가의 원문과 기준 번역으로 가중치를 학습하거나 하이퍼파라미터를 조절하지 않는다. 동일한 기반 모델과 학습 모델에 용어 후처리·번역 메모리를 적용하지 않고 비교해 **추가 학습의 효과**를 측정한다. 일반 문장 점수도 함께 확인한다.

이 작은 도우미 작성 표본은 전문 번역 품질의 보증이 아니다. 사용자 검수 문장이 쌓이면 `npm run export:translation-memory`로 내보내 별도 검토·중복 제거·문서 단위 분할을 거쳐 후속 데이터에 포함할 수 있다. 검수 저장 자체가 자동 학습을 실행하지는 않는다.

## 저장과 재현

학습 환경과 모델·체크포인트는 Git 및 웹 공개 경로에서 제외한다. 실행별 데이터 해시, 설정, 학습 손실, optimizer 갱신 수, 가중치 변화, 개발·최종 평가를 `.training/runs/`에 보존한다. 학습된 가중치는 원본에서 재설치할 수 없는 개인 파생 결과물이므로 **별도 보관 대상**이다. 앱의 SQLite 백업 명령은 이 학습 폴더를 포함하지 않는다.

최종 평가를 통과하지 못한 체크포인트를 앱 기본 번역기로 자동 전환하지 않는다. 학습 완료와 번역 품질 개선, 앱 적용 완료를 각각 구분해 보고한다.
