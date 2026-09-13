# TG27 재시도 첫2문단의 자원 관측

재시도 첫2문단은 각각1411.015초와1344.906초에 완료됐다. 원시 응답과 의미 대조는 [부분 검토 기록](../../scripts/model-comparison/PARTIAL_TG27_REVIEW.md)에 보존했다. 현재 전체18+읽기6 완료 시간이나 전체 번역 품질은 확정하지 않는다.

| 항목 | LDEV26-001 | LDEV26-002 |
|---|---:|---:|
| 출력 토큰 | 48 | 46 |
| 입력 처리 시간 | 104.474초 | 132.527초 |
| 출력 생성 시간 | 1306.511초 | 1212.155초 |
| 출력 생성 속도 | 0.03597토큰/초 | 0.03712토큰/초 |
| 요청 구간 native kernel CPU 비중 | 84.51% | 83.61% |
| 요청 구간 native 총 page fault 증가 | 198,191,300 | 190,242,229 |

CPU 비중의 분모는 같은 native의 kernel+user CPU 증가분이다. 전체 PC CPU 사용률이나 디스크 대기 시간의 비율이 아니다. 여러 스레드의 CPU 초는 합산되므로 벽시계 시간보다 클 수 있다. page fault는 soft/hard 합계이며 이 수로 디스크 읽기 바이트를 계산하지 않는다.

현재 GGUF 파일은16,546,704,480바이트(약15.41GiB), 실행 budget은8GiB이고 실제 native hard working-set 상한은 약7.94GiB다. 파일 전체가 그 작업 집합에 동시에 들어가지는 않는다. Windows는 프로세스의 작업 집합에서 제거한 페이지를 나중에 다시 필요로 할 때 soft 또는 hard fault로 처리할 수 있다. [Microsoft working set 설명](https://learn.microsoft.com/en-us/windows/win32/memory/working-set)

기존 Hy30의 GGUF에는 `hy_v3`·48층·expert128개 중8개 사용, TG27에는 `gemma3`·62층·embedding5376이 기록돼 있다. Hy30이 MoE 모델이라는 설명과 expert 설정은 [Tencent 모델 카드](https://huggingface.co/tencent/Hy-MT2-30B-A3B), [공식 config](https://huggingface.co/tencent/Hy-MT2-30B-A3B/blob/main/config.json)와 맞는다. TranslateGemma가 Gemma3 기반이라는 점은 [Google 모델 카드](https://huggingface.co/google/translategemma-27b-it)에 명시돼 있다. 30B와27B라는 전체 크기만으로 토큰당 같은 계산량·메모리 접근량을 가정할 수 없다.

관측은 반복적인 페이지 교체가 지연에 기여할 가능성과 일치한다. 다만 이번 재시도에서 hard fault의 파일별 귀속·디스크 바이트를 측정하거나 RAM 상한만 바꾼 짝 대조는 하지 않았다. kernel CPU 전체를 paging 비용으로 해석하거나 메모리를 늘리면 특정 배수로 빨라진다고 단정하지 않는다. 기존 실행과 다른 입력·토큰·모델 구조 차이도 남는다.

원자료와 계산은 [자원 분석 receipt](../../.training/verifications/tg27-first2-resource-analysis-20260912.json)에 있다. 이미 보존한 prediction2개와 start/Hy30 summary의 해시, 모델 파일 크기만 읽었으며 모델 가중치를 다시 읽거나 새 native를 실행하지 않았다. 현재 CPU4/BelowNormal·8GiB·메모리 여유 guard·요청 한도와 전역 전원 설정을 유지한다.

실행 중 [별도 관측 receipt](../../.training/verifications/tg27-retry-partial-resource-extrema-20260912.json)에는 시작 단계의 일시적인 여유 physical575,619,072바이트(약0.536GiB), commit2,389,819,392바이트와 생성 중 최대 heartbeat 공백14.578초도 보존했다. 이후 관측에서는 physical 여유가 회복됐다. 시작 시점의 한계 근접 관측을 숨기거나 전체 실행의 자원 안전을 인증하지 않는다. 이 일시 저하의 원인도 확정하지 않았다.
