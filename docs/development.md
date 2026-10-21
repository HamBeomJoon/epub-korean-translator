# 개발 안내

[빠른 시작](../README.md) · [상세 사용법](usage.md)

명령은 clone한 Git 저장소의 프로젝트 루트에서 실행합니다.

## 개발과 테스트

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q epub_pipeline translate_epub.py tests scripts
```

테스트 함수명은 `test_승인이_없으면_공급자를_호출하지_않는다`처럼 `test_` 뒤에 한글로 동작을 설명합니다. API·EPUB·JSON 등 기술 용어는 유지합니다. 공통 합성 EPUB·모의 공급자·네트워크 차단은 `tests/helpers.py`에 모았고 테스트 파일끼리 서로 import하지 않습니다. 실제 계정·API 키 없이 검증하며 SDK/JWT 의존성이 없으면 해당 검증을 skip합니다.

진행률 테스트는 배치·검수·재시도·캐시 계수와 대화형 갱신 스레드 정리를 확인합니다. CLI 테스트의 실제 macOS 알림은 공통 fixture에서 차단하고 알림 어댑터는 subprocess 모의 응답으로 검증합니다. 테스트 실행으로 데스크톱 알림을 보내지 않습니다.

실제 데미안 8개 장·1장 91문단 회귀 테스트는 사용자가 해당 판본을 별도로 준비했을 때만 실행합니다. 미지정 시 이 테스트 1개만 skip되며 EPUB은 저장소에 포함하지 않습니다.

```bash
DEMIAN_EPUB=/path/to/hermann-hesse_demian_n-h-priday.epub \
  python3 -m unittest discover -s tests -v
```

## push 자동 검사와 커밋 전 보호

[GitHub Actions](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)는 `main`에 push하면 Python 3.10·3.13에서 비밀정보 검사, 문법 검사, 오프라인 테스트를 실행합니다. PR 없이 `기능 수정 → 커밋 → push → Actions 결과 확인` 순서로 사용할 수 있습니다. 향후 `main` 대상 PR에도 같은 검사를 실행하며 Actions 화면에서 수동 실행도 가능합니다. EPUB·API 키·GitHub Secrets 등록은 필요 없고 실제 번역 API는 호출하지 않습니다. 실제 책 회귀 테스트 1개는 건너뜁니다.

커밋 전 보호는 clone한 Git 저장소에서 **한 번 설치**합니다. Git hook 설정은 clone에 포함되지 않으므로 새 컴퓨터나 새 clone에서는 다시 설치하세요.

```bash
python3 scripts/install_hooks.py

# 수동 검사
python3 scripts/check_secrets.py --staged
python3 scripts/check_secrets.py --all
```

이후 `git commit` 시 `.githooks/pre-commit`이 staged 파일의 **Git index 내용**을 검사합니다. OpenAI·GitHub·AWS·Slack 키, JWT·개인 키·긴 인증정보 할당과 `.env`·인증 파일·EPUB·출력 폴더·`CODEX_PROMPT.md`를 검출하면 커밋을 차단합니다. 값은 출력하지 않으며 `.env.example`의 기본 자리표시자는 허용합니다. 별도 Python 패키지는 필요 없습니다. 다른 `core.hooksPath` 설정이나 기존 `pre-commit` hook이 있으면 설치가 덮어쓰지 않고 중단합니다.

차단된 개인 파일은 `git restore --staged -- 경로`로 staging에서 빼면 로컬에 남습니다. 키가 코드에 들어갔다면 제거하고 다시 `git add`하세요. 알려진 형식과 파일 경로를 검사하므로 모든 비밀정보를 검출한다고 보장하지 않습니다. [Git hook은 우회할 수 있으며](https://git-scm.com/docs/githooks#_pre_commit), GitHub 검사 실패는 이미 끝난 push를 취소하거나 업로드를 막지 않습니다. GitHub에서도 전체 추적 파일을 같은 검사기로 확인해 실패를 표시합니다.

## 코드 구조

| 모듈 | 책임 |
|---|---|
| `translate_epub.py` | CLI 진입점과 종료 코드 |
| `epub_pipeline/cli.py` | 인자·승인·의존성 조립 |
| `domain.py`, `prompts.py` | 데이터·검증과 번역/검수 프롬프트·스키마 |
| `pipeline.py`, `ports.py` | 번역·검수·재개·발행 흐름과 어댑터 인터페이스 |
| `parser.py` | EPUB spine·XHTML·원문 AST·안정적인 ID 추출 |
| `review_html.py` | 정렬된 원문·번역·검수 JSON의 오프라인 HTML 표현 |
| `progress.py`, `notifications.py` | 터미널 진행률 표시와 macOS 로컬 알림 |
| `provider.py`, `chatgpt_provider.py` | API 키/OAuth 추론 어댑터 |
| `chatgpt_auth.py`, `chatgpt_store.py` | 로그인·계정·인증 정보의 비공개 저장 |
| `repository.py`, `pricing.py` | 출력·캐시·사용량 저장과 외부 요금 계산 |

`translate_epub.py`는 CLI 전용입니다. Python에서 기능을 사용할 때는 `epub_pipeline`의 해당 모듈을 직접 import하세요. 사용하지 않는 구형 MVP의 `compat.py`와 `api_json`, `translate_batch`, `review_batch` 재노출은 제거했습니다. 명령행 옵션과 기존 출력 형식은 유지합니다.

개인 개발 지침인 `CODEX_PROMPT.md`는 로컬에만 보관하고 Git 추적에서 제외합니다.
