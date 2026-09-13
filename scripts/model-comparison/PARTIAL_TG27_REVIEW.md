# 실행 중 완료 문단의 원문 대조 보존

`partial_tg27_review.py`는 현재 승인된 TG27 8GiB 재시도의 **이미 완료된 한 문단**과 직접 작성한 의미·용어 판단을 새 폴더에 보존한다. 원문·번역·문맥 해시, 원시 응답/EOS/설정/토큰/프롬프트, 실제 인용과 원문 용어 전체 출현을 대조한다. 계속 추가되는 predictions 파일 전체를 완료된 불변 파일로 취급하지 않으며, 완결되지 않은 마지막 JSON 행은 관측으로 읽지 않는다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/partial_tg27_review.py --review .training/verifications/tg27-root-review-LDEV26-001-20260912.json
.venv-training\Scripts\python.exe -B -X utf8 -m unittest discover -s scripts/model-comparison -p test_partial_tg27_review.py -q
```

결과는 `.training/quality-evaluation/tg27-user-retry-root-review-20260912/partial/<원문 ID>/`에 원문, 용어 inventory, 선택 prediction, 원시 응답, 직접 작성한 판단, 당시 보존 코드와 해시 receipt로 저장한다. 기존 폴더를 덮어쓰지 않는다. 완료한 원문 선정149출현 및 별도 선정 주석 정정은 유지한다. 이 도구에 고정한 재시도 이름·입력·코드를 다른 실행을 위해 수정하면 새 버전으로 관리한다.

모든 결과는 `partialOnly:true`, `fullRunValidated:false`, `qualityAccepted:false`다. 중요 의미 오류가 없다는 개별 판단을 전체 수용으로 확대하지 않는다. 자동 통화 기호 검사 실패도 원래 값으로 보존하며 의미 대조 주석을 따로 둔다. 의미 판단은 도우미가 직접 작성하고 문자열 포함 검사는 인용 위치만 확인한다.

검토자는 원문과 이전 후보 출력을 보았으므로 `independentBlindReview:false`이며 한국어 전용 질문 답변을 생성하지 않는다. 이 결과는 독립 검수나 사람 gold가 아니다. 무거운 모델·worker·운영 DB·기존 결과·학습·앱 등록을 실행하거나 변경하지 않는다. 전체18+읽기6의 종료·무결성은 [기존 v5 검증](V5_REVIEW_ADAPTERS.md)에서 별도로 확인해야 한다.

2026-09-12: 합성9검사를 통과했고 LDEV26-001/002 실제 부분 보존을 확인했다. 원문 의미 관계8개와 용어14출현의 직접 대조를 저장했으며 전체 문단·독립 질문·앱 검증은 아직 미완료다. 첫 대조쌍에서 운송업체/수출업체의 지급자·수취인 교환을 유지했다. 둘째 번역의 포괄적인 '지불'은 같은 문단의 '이 환불금'과 연결해 비용 반환 의미가 보존된 것으로 판단했다.

후속 LDEV26-003도 실제 보존했다. 당시3문단·의미 관계14개·용어24출현을 대조했고 용어21개 보존·3개 보류였다. 세 번째 문단의 `lender`→'대출자'는 당사자 명확성을, `waiver`→'면책 동의서'의2출현은 원문이 특정하지 않은 면제 범위를 좁혔는지 보류했다. '대출자'를 반드시 차입자라고 확정하지 않으며, 반대 뜻의 용례도 보조 검토 기록에 남겼다. 해당 외부 용례는 공식기관 검색 결과의 짧은 발췌만 확인했고 전체 페이지 가져오기는 실패했으므로 기관 gold나 전문 검토로 표시하지 않는다. 시간 조건·미서명 초안 부정은 유지됐지만 이 문단의 전체 의미 수용은 보류다. 전체24문단의 정확도는 산출하지 않았다.

2026-09-12 후속: 같은 코드를 변경하지 않고 새004~015의 직접 판단과 원문 인용·실제 응답을 모두 검증한 후12개 부분 snapshot을 추가했다. 현재15문단·용어76출현 중66보존/4오역/6보류다. 문단 의미는 중요5·경미2·관측 오류 없음4·보류4이며, snapshot90파일과 receipt15개 확인을 `.training/verifications/tg27-first15-root-review-check-20260912.json`에 기록했다. [관측 보고서](../../content/model-comparison/TG27_PARTIAL_REVIEW_20260912.md)는 자동 검사 통과와 실제 의미 오류, 원문 자체의 보류를 구분한다. 전체 실행·독립 질문·앱 수용을 완료로 표시하지 않았다.

## 전체 실행 종료 후 연결

**2026-09-12 최신 제한:** 아래 도구가 고정한 원래 개발 실행은16개 생성 후17번의 시간 제한으로 실패했다. 따라서 아래 개발18 명령은 현재 실행을 거부하는 것이 맞다. 미완료017/018과 읽기6은 [별도 실행 계약](../../content/model-comparison/TG27_TAIL_RECOVERY_20260912.md)을 따른다. 이 새 실행의 직접 판단은 `partial_tg27_recovery_review.py --review <판정 JSON>`와 version `tg27-recovery-root-review-v1`를 사용하며 `.training/quality-evaluation/tg27-recovery-root-review-20260912/partial/`에 보존한다. 허용 ID는 개발017/018·REAL001~006이고 합성11개를 통과했다. 원래16개 snapshot 및 기존 전체 실행 검증 계약을 변경하지 않는다. 실패 실행과 새2개를 한 번에 성공한18개로 편입할 수 없다.

[complete_tg27_root_review.py](complete_tg27_root_review.py)는 **한 집합 전체18/6 생성·소유 종료·기존 v5 검증·모든 부분 수동 판정**이 있어야 새 전체 대조 산출물을 만들고 선택적으로 기존 오류 원장에 추가한다. 실행기·기존 부분 도구·원장을 수정하지 않는다. `prepare_finance_answerability.py`의 기존 완료 실행 증거 읽기를 재사용하지만 질문 packet·답변·채점은 생성하지 않는다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/complete_tg27_root_review.py --cohort dev18 --output .training/quality-evaluation/tg27-user-retry-root-review-20260912/completed-dev18-v1 --append-to-ledger
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/complete_tg27_root_review.py --cohort reading6 --output .training/quality-evaluation/tg27-user-retry-root-review-20260912/completed-reading6-v1 --append-to-ledger
```

원문149출현 분모는 집합별92/57로 유지하며 전체 용어가 모두 맞아도 문단의 중요 오류·보류를 지우지 않는다. 원래 `fullRunValidated:false`인 부분 receipt를 덮어쓰지 않고 완료 실행을 새로 대조한 사실을 별도 기록한다. `unresolved`, 비맹검·비인간 검토 이력, 원래 자동 검사 값, 여러 실제 인용 조각을 유지한다. 원장 사건은 내용 해시로 중복을 막고 기존 사건을 재작성하지 않는다. 재실행하려면 새 output 경로가 필요하다. 실제 완료 결과에 연결한 코드·판정은 이후 임의 수정하지 않는다.

준비 단계에서 새 연결 검사6개와 기존 부분 계약9개, 총15개를 통과했다. 실제 진행 중인 개발 실행은 `completed_run_summary_missing`으로 거부됐고 output 생성·원장 추가·native 호출은0이었다. 증거는 `.training/verifications/tg27-completed-review-link-preparation-20260912.json`이다. 이는 거부 경로 확인이며 실제 완료18/6 검증이나 전체 원장 편입 성공은 아직 아니다.

## 실패 실행의 완료16개를 진단 원장에 연결

[record_tg27_failed_run_reviews.py](record_tg27_failed_run_reviews.py)는 위 전체 성공 연결과 별도다. 고정된 기존 실행의 완료16·실패17·미실행18, 요청 제한 원인·소유 종료·불변 실패 증거58파일을 먼저 확인한다. 이어 원래16개 부분 snapshot·원문·원시 응답·인용·고정 용어81출현을 다시 검증하고 원장 사건을 추가한다. 원래 전체 검증기를 변경하거나 통과시키지 않는다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/record_tg27_failed_run_reviews.py --output .training/quality-evaluation/tg27-user-retry-root-review-20260912/failed-run-diagnostic16-ledger-v2 --append-to-ledger
```

출력은 존재하지 않는 새 폴더여야 한다. 실제 첫 검증은 `failed-run-diagnostic16-v1`, 원장 연결은 `failed-run-diagnostic16-ledger-v1`, 중복 검사는 `failed-run-diagnostic16-ledger-repeat-v1`에 보존했다. 재실행은 해시가 같은97사건을 재사용하며 의미 판정을 바꾸지 않는다. 당시 실행 코드도 증거에 묶였으므로 이 도구와 고정 의존 코드를 임의 수정하지 않는다.

모든 사건에 `originalRunStatus:failed`, `diagnosticOnly:true`, `partialOnly:true`, `fullRunEvidenceValidated:false`, `fullDev18RunValidated:false`와 비맹검 이력을 보존한다. 원래 자동 검사 값·보류·여러 인용 조각도 유지한다. 부분 용어81개를 전체92/149개의 정확도로 표시하지 않으며, 미검토68개와 질문 미평가를 명시한다. 합성6개에서 미종료·상이한 실패·행 누락/교체·중복/손상·전면 성공 오표시를 검사했다. 실제97건 추가와 반복 추가0·재사용97을 확인했고 원장은708사건이 됐다. 모델 호출·운영 DB 변경·학습·앱 등록은0이다.

## 새2개와 읽기6의 완료 검토 연결

[record_tg27_recovery_reviews.py](record_tg27_recovery_reviews.py)는 새 고정 실행을 각각 연결한다. `tail2`는 원래 v5의 별도2개 증거 검증을, `reading6`은 기존 전체6개 검증을 통과하고 소유 종료·원시 응답·모든 새 부분 수동 판정이 있어야 한다. 두 집합의 용어 분모는11/57이다. 이전 실패 실행의16개를 끼워 넣지 않으며 전체 개발18 성공을 주장하지 않는다.

```powershell
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/record_tg27_recovery_reviews.py --cohort tail2 --output .training/quality-evaluation/tg27-recovery-root-review-20260912/completed-tail2-v1 --append-to-ledger
.venv-training\Scripts\python.exe -B -X utf8 scripts/model-comparison/record_tg27_recovery_reviews.py --cohort reading6 --output .training/quality-evaluation/tg27-recovery-root-review-20260912/completed-reading6-v1 --append-to-ledger
```

원래 부분 receipt는 그대로 남고 새 사건에 `selectedRunScope:tail2|reading6`, `fullDev18RunValidated:false`, 원래 자동 검사·보류·비맹검 이력을 기록한다. 읽기6의 실제 종료 검증과 전체 금융24 수용을 구분하며 질문 답변은 생성하지 않는다. 합성6개 통과 및 두 미완료 실행의 실제 거부를 `.training/verifications/tg27-recovery-review-link-preparation-20260912.json`에 보존했다. 준비 확인 시 output 생성·원장 추가·native 호출은0이며 실제 완료 검토를 편입한 것은 아니다.


2026-09-12 13:30 KST 실제 연결: tail2가 종료된 뒤 `record_tg27_recovery_reviews.py --cohort tail2`로 `completed-tail2-v1`에2개 검토·11용어를 확인하고13사건을 추가했다. 반복 폴더에서는 추가0·재사용13이다. 별도 `dev18-cross-run-diagnostic-v1.json`은 기존 실패16+새 성공2의 원문 중복/누락·92용어·342근거 파일을 확인하며 `fullDev18RunValidated=false`와 질문 미평가를 보존한다. 이 보고서는 기존 전체18 consumer의 우회 입력이 아니다. 읽기6은 현재 실행 중이며 같은 도구의 `--cohort reading6`는 실제 종료와6개 수동 snapshot이 모두 있어야 실행한다.
