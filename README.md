# EPUB 한국어 3종 번역

Python CLI로 EPUB을 챕터·문단 단위로 번역하고 AI 검수, Markdown과 앱용 JSON을 저장합니다. `faithful`(원문 충실형), `modern`(자연스러운 현대어), `easy`(쉬운 한국어)는 **같은 원문에서 각각 독립적으로 생성**합니다. 검수는 문제를 보고하며 번역문을 자동 수정하지 않습니다.

Python 3.10+와 macOS/Linux를 지원합니다. 파일 잠금에 `fcntl`을 사용하므로 Windows는 현재 지원하지 않습니다.

## 설치와 원본 준비

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

원본 EPUB은 사용자가 이용 권한을 확인해 직접 준비합니다. 책을 자동 다운로드하지 않으며 EPUB과 실제 번역 결과는 저장소에 포함하지 않습니다. `--input`에 파일 경로를 지정하므로 프로젝트 폴더에 책을 복사할 필요는 없습니다. 아래 예시의 `/path/to/book.epub`을 본인 파일 경로로 바꾸세요.

## ChatGPT 계정으로 실행

기본 provider는 `chatgpt`입니다. 공식 [Sign in with ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)와 [추론 절차](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)를 사용하며, 계정의 이용 자격과 지원 모델·기능은 서버에서 확인합니다. `.env`나 API 키는 필요 없습니다.

```bash
# 브라우저 로그인과 사용량 사용 동의
python translate_epub.py --chatgpt-login

# 로컬 연결 상태 / 계정에서 제공하는 모델 조회
python translate_epub.py --chatgpt-status
python translate_epub.py --list-models

# EPUB 장 목록과 오프라인 번역 계획
python translate_epub.py --input "/path/to/book.epub" --list-chapters
python translate_epub.py --input "/path/to/book.epub" \
  --model YOUR_MODEL_SLUG --chapter 1 --review --dry-run

# 1장 전체 번역: 실제 사용량 사용 승인
python translate_epub.py --input "/path/to/book.epub" \
  --model YOUR_MODEL_SLUG --chapter 1 --review --approve-plan
```

`YOUR_MODEL_SLUG`는 `--list-models` 결과에서 선택합니다. `--approve-plan`은 이번 번역의 사용량 사용 승인이고 로그인 동의와 별개입니다. 로그인·모델 조회는 네트워크를 사용하지만 `--chatgpt-status`, dry-run과 승인 없는 계획 확인은 세션 갱신이나 API 요청도 하지 않습니다. `--dry-run`은 승인 옵션을 붙여도 항상 오프라인입니다.

Plus/Pro 사용량은 계정의 한도와 정책을 따르며 무제한이 아닙니다. 권한·이용 자격·사용량 한도 오류는 중단하고 유료 API 키 방식으로 자동 전환하지 않습니다. 연결과 사용량은 [ChatGPT Usage](https://chatgpt.com/settings/usage)에서 관리합니다.

다른 계정은 `--chatgpt-profile personal`처럼 별도 프로필로 로그인하고 번역할 때도 같은 옵션을 사용합니다. 사용량 권한에 다시 동의하려면 `--chatgpt-login --chatgpt-enable-plan`, 로그아웃은 `--chatgpt-logout`입니다. 로그인·모델 조회 명령은 EPUB 번역 인자와 분리해 실행하세요.

인증 정보는 기본적으로 `~/.epub_translation_tool/chatgpt/session.json`에 저장됩니다. 폴더 `0700`, 파일 `0600`으로 보호하지만 토큰 자체는 암호화되지 않습니다. `--chatgpt-auth-dir`로 위치를 바꿀 때도 코드·출력·공유 폴더와 분리하세요. 로그인 파일과 토큰을 Git에 올리지 마세요.

## 번역 범위와 품질 설정

| 옵션 | 동작 |
|---|---|
| `--chapter 1` | 1장 전체. 기본 선택 장도 1장 |
| `--all-chapters --include-prologue` | 프롤로그를 포함한 전체 책 |
| `--chapter 0` | 프롤로그만 선택 |
| `--sample-paragraphs 3` | 선택한 장의 처음 3문단을 별도 `sample-3/`에 저장. 제목도 번역 |
| `--styles faithful` | 원문 충실형만 생성. 기본값은 세 스타일 모두 |
| `--review` | 번역 배치마다 원문 대조 AI 검수 추가 |
| `--review-model MODEL` | 검수 모델을 별도 지정. 기본값은 번역 모델 |
| `--glossary FILE` | 원문 표기 → 한국어 표기의 JSON 용어집 |
| `--consistency FILE` | 인물·호칭·시점·문체 지침 JSON |

`examples/demian_glossary.json`과 `examples/consistency.example.json`은 데미안용 예시입니다. 다른 작품은 그 작품에 맞는 파일을 준비하세요.

문단 경계로 배치하며 기본값은 `--max-items 8 --max-chars 2600`입니다. 긴 단일 문단은 쪼개지 않으므로 문자 제한을 넘을 수 있습니다. 앞뒤 원문은 각각 `--context-items 2 --context-chars 1600`까지 참고하며 `0`이면 문맥을 끕니다. 세 스타일 모두 같은 원문·문맥·용어집을 받고 다른 스타일의 번역문을 중간 원본으로 사용하지 않습니다.

`plan.json`은 예상 신규 요청 수, 캐시 적중 수, 최대 시도 횟수와 알려진 요청 문자 수를 기록합니다. 아직 생성되지 않은 검수 본문·출력·추론 토큰·재시도 분량은 미산정이며 토큰과 비용의 사전 추정값은 `null`입니다.

## API 키 방식

ChatGPT 구독과 별도 과금입니다. 환경변수 또는 로컬 `.env`에 본인의 `OPENAI_API_KEY`를 설정하고 실행합니다. `.env.example`은 키가 없는 양식입니다.

```bash
python translate_epub.py --provider openai \
  --input "/path/to/book.epub" --model YOUR_API_MODEL \
  --chapter 1 --review --approve-api
```

API 키 방식은 `--approve-api`, ChatGPT 방식은 `--approve-plan`을 사용합니다. 서로 다른 승인 옵션을 섞으면 오류입니다. API 키 방식에서 모델을 생략하면 코드의 기본값 `gpt-5-mini`를 사용합니다.

`--pricing FILE`로 API 모델별 요금표를 지정할 수 있습니다. 형식은 `examples/pricing.example.json`을 참고하되 **예시의 0은 자리표시자**이므로 실제 요금으로 바꾸세요. 금액은 Decimal로 계산해 JSON 문자열로 기록하며 요금표·응답 usage가 없으면 `null`입니다. 이는 무료라는 의미가 아닙니다. ChatGPT provider에는 API 달러 요금표를 적용하지 않고 `billing_mode=chatgpt_plan`으로 기록합니다.

## 출력과 앱 연동

```text
output/<epub-name>/
  book.json                     # 전체 책 번역 완료 시 장별 고정 스냅샷 인덱스
  chapter-01/
    번역본/
      faithful.md
      modern.md
      easy.md
    _관리/
      plan.json
      source.preview.json       # dry-run 원문
      source.json
      aligned.json
      review_report.json
      review_report.html        # 브라우저에서 원문·세 번역·AI 지적 비교
      usage.json
      manifest.json
      generations/<revision>/  # 완성된 결과 스냅샷
      .cache/                  # 번역·검수 재개용 캐시
      .usage/                  # 요청별 사용량 원장
```

기본 책 폴더명은 입력 파일의 확장자를 뺀 값입니다. `--output`으로 출력 루트를 바꿀 수 있습니다. 이름 매핑이 필요한 경우 해당 출력 루트의 `.book-names.json`에 원본 stem → 폴더명을 저장합니다. 예: `{"hermann-hesse_demian_n-h-priday": "hermann-hesse_demian"}`. 로컬 매핑은 입력 판본·캐시 키에 영향을 주지 않습니다.

앱은 `_관리/manifest.json`을 읽고 **그 파일이 있는 폴더 기준**으로 `aligned`, `review`, `markdown`의 상대 경로를 해석합니다. 새 출력의 `review_html`도 같은 기준의 HTML 스냅샷 경로입니다. 스냅샷을 모두 저장한 뒤 manifest를 원자적으로 교체합니다. `번역본/`과 `_관리/` 바로 아래 결과는 편의용 사본이며 여러 파일이 동시에 교체되지는 않습니다. 캐시·사용량 원장·스냅샷은 재개와 앱 연동에 필요하므로 단순 중복 파일로 삭제하지 마세요.

`aligned.json`에는 원문 버전·출력 revision·모델·제목·문단이 있습니다. 문단은 `chapter_id`, `paragraph_id`, `source_text`, `translations.{faithful,modern,easy}`, `review_status`, 원문 위치를 포함하며 미생성 스타일은 `null`입니다. 기존 `id`, `source`, 최상위 `chapter`도 유지합니다.

기존 출력은 `python translate_epub.py --organize-output /path/to/chapter-01`로 API 호출 없이 정리할 수 있습니다. 기존 파일의 내용은 그대로 이동하며 충돌 시 덮어쓰지 않습니다. 같은 장의 번역/재개 실행도 이전 구조를 정리합니다.

## AI 검수 보고서

`review_report.json`은 스타일별 `issues`와 검수 상태를 저장합니다. 각 이슈에는 다음 정보가 있습니다.

| 필드 | 내용 |
|---|---|
| `id` | 원문 문단 ID |
| `chapter_number` | 챕터 번호. 프롤로그는 0 |
| `paragraph_number` | 추출한 본문 항목 순서로 1부터. 제목은 0 |
| `source_text` | 원문 전체 |
| `translation_text` | 해당 스타일의 현재 번역문 전체 |
| `category` | `omission`, `addition`, `distortion`, `terminology`, `style` |
| `description` | 문제의 근거와 수정 제안 |

`ai_review_executed`는 검수 결과 반영 여부이며 캐시 재사용도 포함합니다. `ai_review_passed`는 미검수 시 `null`, 지적이 없으면 `true`, 있으면 `false`입니다. `human_review_required`는 항상 `true`이며 사람의 최종 승인과 구분합니다. 문단별 상태는 `not_generated`, `not_reviewed`, `passed`, `flagged`입니다.

**수정 제안은 자동 반영되지 않습니다.** 지적이 있어도 결과는 저장되며 AI가 모든 누락·왜곡을 검출한다고 보장하지 않습니다. 실제 출판 전에는 원문 대조와 사용 판본의 권리 확인이 필요합니다.

### 브라우저에서 읽는 HTML 보고서

새 번역 결과에는 `_관리/review_report.html`도 자동 저장됩니다. 제목(문단 0)·챕터 번호·본문 문단 번호·문단 ID와 원문·세 번역·해당 문체의 지적 사항을 나란히 표시합니다. 문체 선택, 지적 있는 문단만 보기, 검색, 인쇄를 지원합니다. 미생성·미검수 상태와 AI 지적 없음을 구분하며 HTML에서도 번역문을 자동 수정하거나 사람의 승인을 기록하지 않습니다.

이미 번역한 장은 EPUB·모델·승인 옵션 없이 다음 명령으로 생성하세요.

```bash
python translate_epub.py --review-html "output/book/chapter-01"
# macOS에서 브라우저로 열기
open "output/book/chapter-01/_관리/review_report.html"
```

이 명령은 `manifest.json`이 가리키는 원문·번역·검수 JSON 스냅샷을 읽습니다. manifest가 없는 구형 출력은 같은 폴더의 `aligned.json`과 `review_report.json`을 사용합니다. 최신 구조와 구형 장 폴더 모두 입력할 수 있으며 HTML만 생성합니다. 기존 JSON·Markdown·manifest·캐시·사용량은 변경하지 않습니다. AI 요청·로그인·토큰 사용은 없습니다.

HTML 한 파일에 서식과 필터 기능을 포함하므로 인터넷 없이 브라우저에서 열 수 있습니다. 외부 리소스는 불러오지 않고 책과 지적 내용은 HTML 텍스트로 이스케이프합니다. 원문과 번역문이 들어 있는 개인 출력물이므로 Git이나 공개 사이트에 올리지 마세요. 브라우저의 JavaScript를 끄면 전체 보고서만 표시되며 검색·필터는 사용할 수 없습니다.

## 소요 시간·재개·사용량

종료 시 다음처럼 챕터별·전체 시간을 표시합니다. 1시간 이상이면 시간도 포함하고 화면에서는 소수점 이하 초를 버립니다.

```text
1장 완료 소요 시간: 25분 3초
완료: output/book/chapter-01
전체 작업 소요 시간: 25분 5초
```

각 장의 `usage.json`에는 이번 실행의 `timing.started_at`, `finished_at`(UTC), `elapsed_seconds`(소수점 포함 초), `status`를 저장합니다. 단조 시계로 측정하며 챕터 시간은 번역·검수·재시도 대기·캐시·결과 저장을 포함합니다. 전체 시간에는 실행 계획과 provider 준비, 책 인덱스 저장도 포함됩니다. 상태는 `completed`, `failed`, `interrupted`이며 재개 시에는 이번 실행 시간만 측정합니다. dry-run·로그인에는 번역 시간을 표시하지 않고 과거 결과의 시간을 추정하지 않습니다.

같은 명령을 다시 실행하면 검증된 캐시를 재사용합니다. 캐시 키에는 provider·파이프라인/파서 버전·원본 EPUB 해시·모델·프롬프트·스키마·문맥·용어집·작품 지침이 포함됩니다. 검수 키에는 실제 번역문도 포함됩니다. 원본이나 요청 의미가 바뀌면 해당 캐시는 재사용하지 않습니다.

`--max-attempts 3`은 요청별 최대 시도 횟수입니다. 일시적 연결·요청 제한·서버·응답 형식 오류는 제한 재시도하며 서버의 `Retry-After`가 60초를 넘으면 종료 후 나중에 재개하도록 안내합니다. 인증·권한·구독 한도 오류는 즉시 중단합니다.

요청별 `.usage/` 기록은 응답 JSON 검증 전에 받은 토큰을 저장하므로 잘못된 응답과 재시도의 사용량도 포함합니다. `usage.json`의 `this_run`은 이번 실행, `output_directory_lifetime`은 출력 폴더 누적 합계입니다. 캐시 재사용은 새 사용량을 더하지 않습니다. 파일은 원자적으로 저장하고 같은 장의 동시 실행은 잠금으로 막습니다. 분산 파일시스템·워커는 지원하지 않습니다.

강제 종료·네트워크 단절·디스크 오류 시에는 시간·성공·과금 기록을 모두 보장할 수 없습니다. 서버 처리 후 캐시 저장 전에 끊기면 재개가 요청을 중복할 수 있으며 기록된 비용은 실제 청구서와 자동 대사하지 않습니다.

## 개발과 테스트

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q epub_pipeline translate_epub.py tests scripts
```

테스트 함수명은 `test_승인이_없으면_공급자를_호출하지_않는다`처럼 `test_` 뒤에 한글로 동작을 설명합니다. API·EPUB·JSON 등 기술 용어는 유지합니다. 공통 합성 EPUB·모의 공급자·네트워크 차단은 `tests/helpers.py`에 모았고 테스트 파일끼리 서로 import하지 않습니다. 실제 계정·API 키 없이 검증하며 SDK/JWT 의존성이 없으면 해당 검증을 skip합니다.

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
| `provider.py`, `chatgpt_provider.py` | API 키/OAuth 추론 어댑터 |
| `chatgpt_auth.py`, `chatgpt_store.py` | 로그인·계정·인증 정보의 비공개 저장 |
| `repository.py`, `pricing.py` | 출력·캐시·사용량 저장과 외부 요금 계산 |

`translate_epub.py`는 CLI 전용입니다. Python에서 기능을 사용할 때는 `epub_pipeline`의 해당 모듈을 직접 import하세요. 사용하지 않는 구형 MVP의 `compat.py`와 `api_json`, `translate_batch`, `review_batch` 재노출은 제거했습니다. 명령행 옵션과 기존 출력 형식은 유지합니다.

EPUB의 제목·본문·각주·인용문과 원문 AST를 보존하지만 강조·링크·각주를 한국어 위치에 재연결하거나 EPUB을 재출판하지는 않습니다. 표·이미지·수식·중첩 구조는 경고와 AST를 대조하세요. DRM EPUB은 지원하지 않습니다. 개인 개발 지침인 `CODEX_PROMPT.md`는 로컬에만 보관하고 Git 추적에서 제외합니다.
