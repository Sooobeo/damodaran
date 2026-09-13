# v4 실행 결과 검수

기존 `prepare_linguistic_reviews.py`, `summarize_linguistic_reviews.py`, `reading_review_common.py`, 읽기 검수 CLI와 v3 helper는 변경하지 않는다. 이미 만든 7B/12B prepared와 집계에는 기존 도구를 사용한다. 새 v4 CLI는 별도 prepared를 만들며 이전 prepared를 재사용하지 않는다.

새로 허용한 생산자는 `translategemma-large-screen-v4`와 `hymt30-development-screen-v4`다. 이전에 허용한 생산자와 함께 비교할 수 있다. 완료 상태, 같은 입력, 정확한 문장 수, 종료·무결성·메모리 guard 기록을 요구한다. TG27 시간 한도는 시작/요청/전체 1800/7200/86400초, Hy30은 1800/1800/43200초다.

Hy30 요청 `top_k=-1`과 서버 보고 `top_k=0`을 별도로 보존한다. 고정된 런타임 revision과 공식 소스 두 파일의 SHA, 정규화 선언, 실제 응답 설정을 확인한다. 그 외 샘플링 설정은 기존 값이어야 한다. 평가 기준, 출력 텍스트, 의미 판단은 바꾸지 않는다.

v4 결과에서는 memory log의 실제 SHA와 기록된 SHA를 대조하고 원시 응답의 텍스트·설정·토큰·종료 기록을 prediction과 연결한다. Hy30은 생산자가 기록한 원시 응답 SHA도 확인한다. TG27 원시 응답에는 생산 시점 SHA가 없으므로 준비 시점부터 파일 바이트를 고정한다. 전체 native 서버 스키마를 감사하거나 메모리 사용량을 독립 재측정한 결과는 아니다.

prepared manifest는 실제 검수 코드 SHA와 추가 증거 파일 목록의 SHA를 보존한다. 모델 경로를 노출하는 목록 자체는 익명 manifest에 넣지 않는다. 집계는 모든 익명 판단이 갖춰진 뒤 처음 키를 열고 같은 파일을 다시 검증한다. 파일 변경·미완료·알 수 없는 버전은 거부하며 기존 출력에 덮어쓰지 않는다.

`completed`는 실행 기록의 완료다. `outputIntegrityPassed`나 자동 검사 실패 진단을 지우거나 해당 문장을 조용히 제외하지 않는다. 실행 완료는 의미 정확성 승인, 사람 검수 완료, 학습 데이터 적합성 보장이 아니다.

저장소 루트 PowerShell에서, 두 생산자의 **18개 개발 문장 실행이 완료된 뒤** 새 패킷을 준비하는 예시는 다음과 같다. `hymt30-paging-v4`는 18개 생성·출력 무결성·모델 종료를 확인했다. 의미 검수와 새 패킷 준비는 인계 시점에 아직 수행하지 않았다. 전체 후속 작업은 [인계 문서](../../TRANSLATION_HANDOFF_20260911.md)를 따른다.

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/model-comparison/prepare_linguistic_reviews_v4.py --input content/model-comparison/linguistic-dev-20260910.jsonl --source-audit .training/verifications/linguistic-dev-source-audit-20260910.json --results .training/comparisons/linguistic-dev-20260910/hy7-baseline .training/comparisons/linguistic-dev-20260910/hymt30-paging-v4 --output .training/comparisons/linguistic-dev-20260910/prepared-hy7-hy30-v4
```

검수 후 집계 CLI는 `summarize_linguistic_reviews_v4.py --prepared <새 prepared> --reviews <모든 검수 JSONL> --output <새 summary.json>`이다. 실제 읽기 6문장에는 같은 인자 형태의 `prepare_reading_reviews_v4.py`와 `summarize_reading_reviews_v4.py`를 사용한다. 읽기 준비 CLI의 입력은 `content/model-comparison/real-reading-check-20260910/sources.jsonl`이며 `--source-audit` 인자는 없다. 준비 및 집계 출력 경로는 새 경로여야 한다.

검증은 합성 64개(기존 기준 회귀 53개 + 새 v4 경계 11개), CLI help 4개, 실제 Hy30v4 간단 시험 2건의 기록·파일 검증으로 나눠 기록했다. 실제 개발 번역 품질을 평가한 검사는 아니다. 실행 증거와 코드 스냅샷은 `.training/verifications/review-v4-contracts-20260911.json`에 연결한다.
