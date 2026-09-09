# 무료 로컬 번역 실행기

Argos Translate 1.11.0과 공식 영어 → 한국어 1.1 모델을 CPU에서 실행한다. 실행 환경은 프로젝트의 `.venv-translation/`, 모델과 설치 명세는 `.translation/`에 둔다. 학습 DB의 DATA_DIR와 별개이며 재설치해도 원문·메모·저장 번역을 수정하지 않는다.

`node --import tsx scripts/setup-translation.ts`로 설치한다. Windows 기본 Python은 `py -3.11`이며 다른 Python 실행 파일을 사용하려면 `TRANSLATION_SETUP_PYTHON`에 경로를 지정한다. 가상환경, 고정 버전 의존성, 공식 모델을 설치하고 오프라인 상태에서 실제 한국어 추론을 확인한다. 전체 설치 의존성은 `.translation/requirements.lock.txt`에 기록한다. 최초 검증한 환경은 이 폴더의 `requirements.lock.txt`에도 저장하며 이후 재설치에 constraints로 사용한다. 의존성을 의도적으로 올릴 때는 직접 의존성과 lock 파일을 함께 갱신하고 실제 번역을 다시 확인한다.

Node worker가 가상환경의 Python으로 `bridge.py`를 한 번 실행하고, NDJSON 요청을 stdin으로 보낸다. 모델을 메모리에 유지하며 EOF 때 종료한다. 모델 해시·파일 목록·런타임 버전을 확인한 후 ready 메시지를 보낸다. Python의 네트워크 audit hook과 Argos 제공자 고정으로 추론 중 외부 연결을 차단한다. 각 요청의 ID를 그대로 반환하며 예외 메시지에 원문이나 메모를 포함하지 않는다.

문장 분리·디코딩·용어 처리 규칙을 변경하면 `runtime.py`의 `BRIDGE_VERSION`을 올리고 설치 검증을 다시 실행한다. 이 값과 모델 해시가 번역 캐시의 제공자 식별자에 포함되어 이전 설정의 번역을 새 번역으로 오인하지 않게 한다.

```json
{"id":"request-1","segments":[{"id":"block-1","text":"Present Value"}],"glossary":[{"source":"Present Value (PV)","target":"현재가치"}]}
```

숫자·수식 보호 표식 `__PV_...__`과 비교·계산 기호는 번역기에 보내지 않고 해당 위치에 그대로 남긴다. 표식 사이의 일반 영문만 번역하므로 긴 문장이 어색하게 나뉠 수 있다. spaCy의 `blank('en')` 규칙형 문장분리기를 사용하여 추가 문장분리 모델을 다운로드하지 않는다. 토큰 길이를 제한해 긴 입력의 묵시적 잘림을 방지한다.

bridge v3는 [출처 있는 금융 용어 규칙](../../content/translation-glossary.md)을 받아 문장 단위로 적용한다. 문장 전체를 먼저 MT에 보내고, 그 원문의 영어 용어와 관측된 한국어 오역이 유일하게 대응할 때만 교정한다. 긴 복합구를 우선하며 겹치는 교정·불명확한 대응은 경고로 반환한다. 정상적인 한국어 띄어쓰기 차이는 허용하고 교정한 말에 붙는 조사를 맞춘다. 영어 별칭·약어와 정확 일치 셀·제목도 처리한다. 기존 v2의 고정 재무제표 복합구 분할은 사용하지 않는다.

일반 본문 속 primer·return 등의 다의어는 일괄 치환하지 않는다. 확인된 오역 후보가 없는 용어를 임의로 삽입하지 않는다. 이 기능은 모델 가중치 재학습이나 전문 번역의 의미 정확성 검증이 아니다. 일반 서술·문법·문맥의 오역은 남을 수 있다. 원문과 함께 읽고 필요한 경우 리더에서 사용자가 수정·검수한다.

공식 참고 자료:

- [Argos Translate 라이브러리·라이선스](https://github.com/argosopentech/argos-translate): MIT 또는 CC0.
- [Argos 모델 인덱스](https://github.com/argosopentech/argospm-index): 영어 → 한국어 패키지 1.1.
- [LibreTranslate 지원 언어 표](https://docs.libretranslate.com/guides/supported_languages/): 해당 압축 모델 약 115 MB. Python 의존성의 설치 용량은 별도다.
- [CTranslate2 Windows 설치 조건](https://opennmt.net/CTranslate2/installation.html): x86-64 Python wheel, Visual C++ 런타임 필요.

LibreTranslate는 같은 Argos 엔진의 HTTP 서버다. 이 앱은 이미 Node worker가 있으므로 Argos를 직접 호출한다. Transformers.js의 NLLB-600M ONNX도 후보였으나 필요한 q8 encoder와 decoder가 합계 약 895 MB이고 모델 라이선스가 CC-BY-NC 4.0이어서 기본 제공자로 선택하지 않았다.

설치한 영어 → 한국어 모델의 원본 README와 출처는 `.translation/packages/en_ko/README.md`에 보존한다. 패키지에는 OPUS·Wiktionary·Stanza 등의 출처가 기재되어 있고 독립 LICENSE 파일은 없다. 라이브러리 라이선스를 모델과 학습 데이터 전체의 라이선스로 단정하지 않는다. 현재 구현 범위는 개인 PC에서의 학습 사용이다.
