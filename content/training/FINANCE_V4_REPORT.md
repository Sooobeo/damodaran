# 실제 원문을 도우미가 번역한 후속 학습

사용자는 별도 사람 검수 자료가 없다고 확인했고, 도우미가 실제 자료를 번역하여 학습시키는 작업을 요청했다. 이 보고서는 이전 `finance-v3` 및 12개 문단 후보 비교와 분리한 `finance-v4-teacher` 실행을 기록한다. 본학습과 독립 최종 평가를 완료했다. 용어 적중은 이전 학습 모델 57/102에서 60/102로 소폭 개선했지만 이전 모델 대비 추가 개선 기준에는 미달했다. 앱 기본 제공자는 Argos로 유지한다.

## 데이터와 독립성

- 저장된 R01 회계 원리, R05 가치평가 소개를 학습에 사용한다. R02 현재가치 입문은 개발, B01 별도 글은 최종 시험용으로 문서 전체를 분리했다.
- 실제 학습 원문 후보 196개 중 원문 단어가 문맥과 충돌하는 1개를 제외하여 번역쌍 195개를 작성했다. 그림에 없는 내용이나 원문의 오타를 추측해 정답으로 만들지 않는다.
- 이전 train 200개와 새 일반 학습 문장 40개를 함께 사용한다. 기존 dev/test 및 이미 소비한 시험 결과는 새 데이터 작성·학습에 사용하지 않는다.
- dev는 실제 원문 36개와 새 일반 문장 12개, test는 실제 원문 14개와 독립 작성 금융 16개·일반 12개다. 최종 검사에서 제외가 생기면 아래 실행 기록에 확정 수량을 남긴다.
- 모든 기준 번역은 도우미가 작성했다. 도우미 간 원문 대조와 자동 검사는 사람 검수가 아니다. 실제 사용자 검수쌍은 0개다.

원문 출처는 [회계 원리](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/AccPrimer/accstate.htm), [가치평가 소개](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/background/valintro.htm), [현재가치 입문](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/PVPrimer/pvprimer.htm), [내재가치에 대한 글](https://aswathdamodaran.blogspot.com/2011/06/thoughts-on-intrinsic-value.html)이다. 이번 작업은 저장된 불변 버전을 사용했다. 원문의 과거 세제·금액·수익률 설명을 현행 사실로 제시하지 않는다.

## 재현과 판정

데이터·원문 버전·출처·검토 기록은 `.training/datasets/finance-v4/`, 실행과 가중치는 `.training/runs/finance-v4-teacher/`에 보관한다. Git·공개 경로와 운영 DB 백업에는 포함되지 않으므로 별도로 보관해야 한다.

기존 고정 `train.py`와 원본 기반 모델에서 시작하며 train으로만 가중치를 갱신한다. dev로 선택한 이후 test 추론 전에 `evaluate_v4.py freeze`로 세 모델·데이터·코드·추론 조건을 고정한다. 숫자/부호/백분율, 용어 표기, chrF/BLEU, 일반 문장 유지의 기존 기준을 바꾸지 않는다. 추가 단위·수식 진단과 모델명을 가린 의미 대조를 함께 남긴다. 단위 경고나 용어 점수는 전체 의미 정확도가 아니다.

학습 성공과 품질 기준 통과, 앱용 변환·등록·실제 저장/캐시는 각각 별도 상태다. 현재 앱 기본 제공자는 Argos다.

## 고정 데이터와 실행 설정

| 분할 | 문장·문단쌍 | 금융 / 일반 | 원문 단어 수 | SHA-256 |
|---|---:|---:|---:|---|
| train | 435 | 355 / 80 | 14,269 | `4cb6a18b165af96e746b7e000404e0cd7aa9ec9dd97872dd5911863475c0fbd7` |
| dev | 48 | 36 / 12 | 1,858 | `16ecde5ad5115d7d5dca67fd7d1f9451e596987537b54b10d2bc577e2451fb36` |
| test | 42 | 30 / 12 | 1,972 | `b0ed0de7d3a56d422a113d8cc0317f5d52e04877ebb2742309f3f45211e162ed` |

학습 원문 번역 195개는 R01 17개·R05 178개이며 영어 10,244단어, 40단어 이상 문단 140개다. 이 수량은 저장된 허용 자료에서 이번에 준비하고 대조한 범위이며 PC의 최대 학습 데이터 용량을 뜻하지 않는다. 데이터가 늘면 주로 학습 시간이 늘고, 동시에 처리하는 문장 길이·배치 크기가 메모리에 영향을 준다.

전체 학습 원문은 이전 2,803단어의 약 5.1배다. 도우미가 이 대화에서 작성한 번역문을 파일·해시로 고정했으며, 정확한 도우미 모델 가중치 revision을 확인한 것은 아니다. 저장된 문장쌍으로 학습을 재현할 수 있지만 도우미의 원래 생성 과정을 완전히 재현한다고 주장하지 않는다.

이전과 같은 고정 Marian 기반 가중치·토크나이저를 사용한다. 장치 XPU, FP32, 학습률 `1e-5`, batch 1, accumulation 4, 3 epochs, seed 20260909, threads 4, 입력·정답 상한 384 tokens, 생성 상한 384 tokens, beam 4다. 문장을 자르지 않고 길이 초과를 거부한다. 두 번의 실제 optimizer 벤치마크는 12.797초·3.281초이며 본학습에는 사용하지 않는다. 예정 갱신은 327회다.

PowerShell에서 같은 인자를 모든 단계에 전달한다.

```powershell
$v4Args = @('--run-id','finance-v4-teacher','--train-data','.training/datasets/finance-v4/train.jsonl','--dev-data','.training/datasets/finance-v4/dev.jsonl','--test-data','.training/datasets/finance-v4/test.jsonl','--device','xpu','--precision','fp32','--epochs','3','--batch-size','1','--accumulation','4','--learning-rate','1e-5','--max-length','384','--max-new-tokens','384','--beams','4','--checkpoint-every','50','--seed','20260909','--threads','4')
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/train.py bench @v4Args
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/train.py train @v4Args
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/evaluate_v4.py freeze --run-id finance-v4-teacher --previous-run-id finance-v3
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/evaluate_v4.py compare --run-id finance-v4-teacher --split dev
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/train.py evaluate @v4Args
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/evaluate_v4.py compare --run-id finance-v4-teacher --split test
```

중단된 본학습은 같은 명령에 `--resume`을 붙인다. 최종 시험 이후 같은 데이터로 본학습을 반복할 수 없다. 원래 학습 스크립트 SHA-256은 `556d1d9845efea3fc34c3fd4013cb025bc2cba6796b0a1ce2366ab56c7f2ec3d`로 보존했다.

익명 의미 대조의 심각도는 모델 출력을 읽기 전에 0(의미 보존), 1(어색함·경미한 부정확성), 2(주장·조건·수량 등에 영향을 주는 오역/누락/추가), 3(대부분 사용 불가 또는 핵심 주장 반전)으로 정했다. 도우미의 주관적 표본 대조이며 전문 검수나 전체 번역 정확도로 표시하지 않는다.

## 본학습 완료와 개발 선택

327회 optimizer 갱신·3 epochs를 완료했다. 학습 시작은 `2026-09-09T12:46:14.175108+00:00`, 완료는 `2026-09-09T13:07:32.845542+00:00`로 개발 추론·저장 시간을 포함해 약 21분 19초다. 선택 모델은 두 번째 회차의 step 218이며 255개 tensor 중 253개가 원본과 달라졌다. 마지막 회차 가중치를 무조건 채택하지 않았다.

| 모델 / 회차 | dev chrF | dev BLEU | 용어 적중 | 숫자 표기 일치 | 기존 dev 판정 |
|---|---:|---:|---:|---:|---|
| 기반 모델 | 36.307452 | 6.635427 | 66/95 | 43/48 | 비교 기준 |
| 1회차 / step 109 | 43.470684 | 18.892917 | 71/95 | 41/48 | 숫자 회귀로 실패 |
| 2회차 / step 218 | 44.439649 | 18.939046 | 72/95 | 43/48 | 통과·선택 |
| 3회차 / step 327 | 44.010893 | 18.777584 | 71/95 | 42/48 | 숫자 회귀로 실패 |

선택 모델의 dev 정답 token loss는 기반 1.527813 → 1.281572다. 모델 파일 SHA-256은 `feff4d684e52e5d7cae7eccc0bf19316687159169e5c766aa03de73e5dc40a1e`다. 학습 데이터·기존 학습 코드·이전 실험·앱 기본 제공자는 바꾸지 않았다. 숫자 지표는 명시적 숫자/부호/백분율 문자열의 일치이며, 영어 수사를 숫자로 쓰는 올바른 번역도 불일치가 될 수 있다. 실제 수량 오류는 원문 대조로 구분한다.

최종 평가 전 `2026-09-09T13:08:50.932197+00:00`에 비교를 동결했다. 동결 ID는 `f468c140cdcd78ba6bab588e0d89cdfe689603694f6f73c3bcc79e5e1ec2c52f`, 평가 스크립트 SHA-256은 `89e8aea709c279c9cf1bdc8838d87e4a56cb397bc43356a2767983c494ec19cf`다.

## 문맥에 따른 다의어 추가 점검

사용자의 후속 주의사항을 반영해 interest, return, capital, bond, equity, period, duration, charge의 금융·일반 문맥을 각각 한 쌍씩 마련했다. 총 16개 도우미 작성 사례이며 정답을 독립 도우미가 대조했다. 모델 학습·선택·통과 기준에 넣지 않는 별도 진단이다. 원문 문맥에 맞는 동의 표현도 인정하며 금융 단어로 일괄 치환하지 않는다.

`.training/datasets/finance-v4/polysemy-probe.jsonl` SHA-256은 `1c6814325e6cd7f4f3f84162c625049dc8ba6c5b422b5b1a3117dc7c2a842252`다. 정상 최종 비교가 완료된 뒤 다음 명령으로 같은 세 모델을 실행하고 익명 의미 대조를 남긴다.

```powershell
.venv-training/Scripts/python.exe -X utf8 -u scripts/model-training/probe_word_sense.py --run-id finance-v4-teacher --data-file .training/datasets/finance-v4/polysemy-probe.jsonl
```

이 도구는 최종 비교의 동결 ID·데이터 해시·원예측과 비교 산출물 파일 해시를 확인한 뒤 실행한다. 생성한 진단 자료와 결과로 현재 모델을 재학습하거나 기준을 바꾸지 않는다.

세 모델 모두 16개 추론을 완료했고 모델명을 가린 도우미 대조 48후보의 입력·번역 해시를 검증했다. 초점 단어의 문맥상 뜻 보존은 원본 15/16, v3 14/16, v4 15/16이었다. 문장 전체에 실질적 의미 오류가 있는 심각도 2 이상은 각각 9/7/5개다. 초점 단어가 맞아도 다른 수식어·주장·역할이 틀릴 수 있음을 보여 주는 작은 진단이며 90% 용어 목표 달성 증거로 사용하지 않는다. 집계는 `word-sense/assistant-review-summary.json`에 있다.

## 독립 최종 평가 완료

소비한 최종 test 42개에서 용어 보정·번역 메모리 없이 동일 XPU/FP32·beam 4 조건으로 비교했다.

| 모델 | 전체 chrF | 전체 BLEU | 용어 표기 적중 | 숫자 표기 일치 |
|---|---:|---:|---:|---:|
| 원본 Marian | 30.812363 | 5.264026 | 33/102 (32.35%) | 41/42 |
| 이전 finance-v3 | 36.895197 | 11.846662 | 57/102 (55.88%) | 41/42 |
| finance-v4-teacher | 38.127597 | 12.841360 | 60/102 (58.82%) | 41/42 |

빈 결과·생성 상한 도달은 세 모델 모두 0개다. 실제 B01 원문 14개만의 용어 적중은 10/38 → 21/38 → 24/38, chrF는 30.048179 → 31.907985 → 32.919876이다. 독립 작성 28개에서는 이전과 새 모델 모두 36/64로 용어 적중이 같았다. 원본 대비 기존 자동 기준은 통과했지만 이전 학습 모델 대비 용어 개선은 2.94%p여서 사전에 정한 5%p 추가 기준에는 실패했다. 원본 대비 `promotionEligible` 필드를 앱 적용 승인으로 해석하지 않는다.

모델명을 가린 도우미 원문 대조도 42개·126후보를 완료했다. 심각도 2 이상(실질적 의미 오류)이 원본 23개, v3 29개, v4 26개였다. 명시 숫자 검사와 별도로 발견한 수량·단위·수식 오류는 각각 1/4/2개였다. 용어·문자열 지표 개선만으로 의미 품질이 충분히 좋아졌다고 말할 수 없다. 이 평가는 도우미의 주관적 진단이며 사람 검수 정확도가 아니다. 입력·후보 해시와 전체 범위 검증을 통과한 집계는 로컬 `comparison-v4/test-assistant-review-summary.json`에 있다.

사용자의 후속 목표인 용어 적중 90%는 아직 달성하지 않았다. 별도 v5에서는 기존 앱 사전의 54개 표기를 먼저 고정하고, 새 문맥 학습 자료와 새 dev/test를 준비한다. v4의 소비한 test·다의어 진단 문장 또는 그 오류를 학습 자료로 재사용하지 않는다.
