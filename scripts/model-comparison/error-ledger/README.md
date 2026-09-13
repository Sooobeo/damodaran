# 번역 오류 증거 원장

선택한 개발 비교의 원문·번역·문맥 해시와 판정 근거를 추가 기록한다. 실제 모델 호출, 운영 DB 쓰기, 학습, 등록은 하지 않는다. 독립 test를 읽지 않는다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/error-ledger/ledger.py --output .training/quality-evaluation/error-ledger/v1
python -B -X utf8 -m unittest discover -s scripts/model-comparison/error-ledger -p test_ledger.py -q
```

`events/<내용 SHA256>.json`은 한 번 생성하면 덮어쓰지 않는다. 같은 입력을 다시 가져오면 재사용하며, 후속 검토는 원래 검토를 보존한 별도 사건이다. `reports/<UTC>.json`은 실행마다 새 집계와 읽은 증거 파일의 해시를 남긴다. `.writer.lock`으로 동시에 쓰지 못하게 하고 기존 사건의 내용 해시가 다르면 실패한다. 잠금 파일을 자동 삭제해 다른 작성자의 잠금을 빼앗지 않는다.

현재 명시적으로 연결한 자료는 기존 Hy7/TG12 개발18·읽기6의 조정 판정, Hy7/Hy30 2차 검토, Hy7 일반16 원문/문맥 비교, 일반 질문64개와 Hy30 읽기 질문12개, Qwen v4/v5·COMET 검사 결과 및 절전·사용자 중단 기록4개다. 새 비교를 추가하려면 해당 evidence adapter와 검증을 추가해야 한다. 폴더 전체를 재귀 탐색해 미지의 판정이나 test를 자동 편입하지 않는다.

새 TG27 재시도의 연결은 별도 [완료 대조 adapter](../PARTIAL_TG27_REVIEW.md#전체-실행-종료-후-연결)를 준비했다. 기존 `ledger.py`를 변경하지 않고 완료된 한 집합의 v5 실행 증거와 모든 원문·부분 판단을 대조한 뒤에만 번역/용어 사건을 추가한다. 현재는 합성 연결 검사와 실제 미완료 실행 거부까지만 확인했으며 TG27 첫15문단을 기존610사건에 추가하지 않았다.

2026-09-12 후속: 해당 TG27 실행은16완료·017실패·018미실행으로 종료됐다. 실행 실패 사건1개만 명시적으로 추가해611사건이 됐고 중복 가져오기 추가0·재사용1을 확인했다. 원래 `ledger.py` 바이트는 유지했다. [미완료2개 별도 재실행](../../../content/model-comparison/TG27_TAIL_RECOVERY_20260912.md)은 전체18 완료로 위장하지 않으며 기존 전체 대조 adapter로 실패한 실행을 편입하지 않는다.

| 사건 | 집계 단위·제한 |
|---|---|
| `translation_review` | 같은 출력의 검토회차별 의미 판정. 검토회차를 새 모델 오류로 합산하지 않음 |
| `question_judgment` | 같은 번역에 연결된 질문·동결 답변·채점. 오답을 번역 오류나 실제 사람의 학습 실패로 자동 판정하지 않음 |
| `qe_observation` | 고정 baseline 판정에 대한 검사기의 TP/FP/FN/TN. 실행되지 않은 문단은 `not_observed` |
| `runtime_observation` | 실행 실패·절전 관측·사용자 취소. 번역 품질 오류로 집계하지 않음 |
| `term_judgment` | 같은 번역의 고정 원문 용어 출현별 수동 판정. 단어 정답을 문단 의미 통과로 확대하지 않음 |

원시 인용과 정확히 일치하는지를 별도 보존한다. 문단 주석의 의역 인용을 실제 문자 위치로 위장하지 않는다. 원인 가설은 미확정이며 모델 내부 원인을 입증하지 않는다. 원장은 이미 작성된 도우미 판정을 연결하는 도구로, 사람 gold나 독립 평가를 새로 만드는 기능이 아니다. 과거 실행의 증거 무결성 검증과 모델의 현재 재실행도 구분한다.

2026-09-11~12 재개 검증: 원장8개+용어 검증4개, 합성12개 통과. 실제610사건(번역 검토128·질문76·QE104·실행4·용어298)을 연결했다. 자세한 관측·개선 판단은 [분석 보고서](../../../content/model-comparison/ERROR_LEDGER_REPORT_20260911.md), 원문149출현별 판정은 [용어 보고서](../../../content/model-comparison/FINANCE_TERM_REVIEW_20260911.md)를 따른다. TG27의 재개 실행 실패는 실행 관측에 포함하며 전체 개발/읽기 의미 검토는 아직 없다.

`term_review.py`는 기존 두 후보의 용어 대조 자료를 새 폴더에 준비하고, 직접 작성한 판정만 검증·집계한다. 준비 packet에 묶인 코드와 입력/원문 검토가 바뀌면 조용히 재사용하지 않는다. 현재 완료한 판정 파일을 수정하지 말고 후속 판정은 새 파일로 남긴다.

```powershell
# $newPacketFolder와 $newEvaluationFolder는 존재하지 않는 finance-terms 하위 경로다.
python -B -X utf8 scripts/model-comparison/error-ledger/term_review.py --prepare --output $newPacketFolder
python -B -X utf8 scripts/model-comparison/error-ledger/term_review.py --packet $packetFile --judgments $manualJudgmentsFile --output $newEvaluationFolder
```

2026-09-12 후속: TG27의17번 요청 제한 사건으로611개가 된 뒤, [실패 실행의 완료16 진단 연결](../PARTIAL_TG27_REVIEW.md#실패-실행의-완료16개를-진단-원장에-연결)로 문단16·용어81 사건을 추가해708개가 됐다. 번역144·질문76·QE104·실행5·용어379이며 반복 추가0·재사용97을 확인했다. 새 사건은 원래 실패와 부분 검증 범위를 명시한다. 기존 `ledger.py`는 고정된 코드로 보존하고, 별도 adapter 보고서가 부분 TG27 편입과 아직 남은017/018·읽기6·질문 평가를 구분한다.

11:54 KST 후속: 변경하지 않은 기존 앱 의미 규칙을 실제 TG27 부분16개에 적용한 관측도 연결해724개(번역144·질문76·QE120·실행5·용어379)가 됐다. TP1·FP0·FN4·TN6이며 판정 보류5개는 `observed_unadjudicated`와 materialError=null을 유지한다. 별도 importer가 이 관측을 기존 번역 사건에 연결했고 반복 추가0·재사용16을 확인했다. [분석 보고서](../../../content/model-comparison/ERROR_LEDGER_REPORT_20260911.md#기존-검사-코드에서-확인한-범위)의 기준·부분 범위를 따르며 기존 qe_event의 이진 판정 계약을 바꾸지 않는다.


13:30 KST 후속: 별도 성공 tail2의 번역2·용어11 판정을 추가해737개(번역146·질문76·QE120·실행5·용어390)가 됐다. 반복 추가0·재사용13을 확인했다. 개발18 교차 실행 진단은 실패16+성공2의 원문18·용어92·근거342파일을 확인하며, 단일 성공18 실행이나 독립 질문 통과로 올리지 않는다. 읽기6의57용어와 질문 평가는 남아 있다. 기존 ledger 코드와 사건을 수정하지 않았으며 실제 추가 범위는 새 adapter 보고서와 [최신 분석](../../../content/model-comparison/ERROR_LEDGER_REPORT_20260911.md)을 따른다.


17:09 KST 후속: 읽기6 중단의49근거 파일을 확인해 실행 사건1개를 추가했고738개가 됐다. 번역146·질문76·QE120·실행6·용어390이다. prediction0·summary/종료 코드 없음·현재 프로세스 부재를 보존하며 번역 오류/정상 종료로 해석하지 않는다. 반복 추가0·재사용1을 확인했다. 새 읽기 재시도는 RAM/프로세스 조회 사전검사 보류로 모델 로딩0이다.
