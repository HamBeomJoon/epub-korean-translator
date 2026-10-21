# EPUB 한국어 3종 번역 파이프라인

Python 3.10+ CLI입니다. `faithful`(원문 충실형), `modern`(자연스러운 현대어), `easy`(쉬운 한국어)를 **동일 원문에서 독립적으로** 생성하고, 별도의 원문 대조 AI 검수와 문단 정렬을 수행합니다. 기본 번역 provider는 **`chatgpt`**이며 원문·실행 계획만 저장합니다. **ChatGPT 번역은 `--approve-plan`, API 키 번역은 `--provider openai --approve-api`로 명시적으로 승인해야 실행됩니다.** 로그인/모델 조회는 각각 별도 명령입니다.

이번 구현과 테스트에서는 실제 API를 호출하지 않았습니다. 번역의 문학적 품질과 실제 모델 호환성은 승인 후 별도 평가가 필요합니다.

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

ChatGPT 방식은 `.env`나 `OPENAI_API_KEY`가 필요 없습니다. 기존 API 키 방식만 `.env`에 `OPENAI_API_KEY`를 설정합니다. 원문 구조 확인과 기본 모의 테스트는 외부 패키지/API 키 없이 실행되며, 실제 JWT 서명/SDK 테스트는 의존성이 없으면 skip됩니다. `PyJWT[crypto]`는 공식 OIDC ID token의 서명을 검증하는 데 사용합니다. OpenAI SDK와 JWT는 해당 기능을 실행할 때만 import합니다. API 키 방식은 기존 기본 모델 `gpt-5-mini`를 유지하고 ChatGPT 방식은 계정 모델 목록에서 `--model`로 직접 선택합니다.

## 원본 EPUB 준비

**원본 EPUB과 실제 번역 결과는 저장소에 포함하지 않습니다.** 사용자가 이용 권한을 확인한 EPUB을 직접 준비하고 `--input`에 로컬 파일 경로를 지정합니다. 프로그램은 책을 자동으로 다운로드하지 않습니다. EPUB을 프로젝트 폴더에 복사할 필요는 없습니다.

```bash
python translate_epub.py --input "/path/to/book.epub" --list-chapters
```

예시의 `/path/to/book.epub`은 본인 파일 경로로 바꾸세요. `.gitignore`는 대소문자와 관계없이 `.epub` 파일과 기본 출력 폴더 `output/`을 제외합니다. `examples/demian_glossary.json`은 데미안용 설정 예시이며 다른 작품에는 그 작품에 맞는 용어집을 준비하세요.

## ChatGPT Plus/Pro로 개인 컴퓨터에서 실행

공식 [Sign in with ChatGPT 등록 절차](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)와 [모델/추론 절차](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)를 구현했습니다. 개인용 로컬 프로그램을 위한 연동이며, 실제 계정의 이용 자격·workspace 정책·모델/Structured Outputs 지원 여부는 로그인 후 서버에서 확인해야 합니다. **구현 검증 중 실제 계정 로그인·모델 조회·번역 요청은 수행하지 않았습니다.**

환경 준비 후 다음 순서로 실행하세요.

```bash
# 1. Continue with ChatGPT: 브라우저에서 로그인하고 사용량 사용 동의
python translate_epub.py --chatgpt-login

# 2. 연결 상태 확인 (네트워크 없음)
python translate_epub.py --chatgpt-status

# 3. 이 계정에서 표시 가능한 모델 목록 조회 (번역 생성 없음)
python translate_epub.py --list-models
```

목록의 `slug` 하나를 선택해 `YOUR_MODEL_SLUG`를 바꾸세요. 사용할 수 있는 모델을 추측하거나 고정하지 않습니다.

```bash
# 4. 직접 준비한 책의 1장 3문단 번역 계획 확인 (네트워크 없음)
python translate_epub.py \
  --input "/path/to/book.epub" \
  --model YOUR_MODEL_SLUG --chapter 1 --sample-paragraphs 3 \
  --review --dry-run

# 5. 선택한 ChatGPT 계정의 사용량을 쓰는 실행을 명시적으로 승인
python translate_epub.py \
  --input "/path/to/book.epub" \
  --model YOUR_MODEL_SLUG --chapter 1 --sample-paragraphs 3 \
  --review --approve-plan
```

`--provider chatgpt`는 기본값이므로 생략할 수 있습니다. 전체 번역은 `--chapter 1 --sample-paragraphs 3`을 `--all-chapters --include-prologue`로 바꿉니다. `--review-model`도 같은 계정에서 제공하는 slug를 선택해야 합니다. `--dry-run`이나 승인 옵션이 없는 실행은 로그인 확인·토큰 갱신·모델 조회도 하지 않습니다. ChatGPT 경로의 dry-run에서 모델을 생략하면 계획의 모델은 `SELECT_CHATGPT_MODEL`로 표시되며 실제 실행 전에 명시적으로 선택해야 합니다.

모델 목록 조회는 읽기 요청이며, 로그인/로그아웃도 네트워크를 사용합니다. 이 명령들은 EPUB 번역 인자와 함께 사용할 수 없습니다. `--approve-plan`은 번역 요청이 구독의 사용량을 소비하는 데 동의하는 옵션이며, 브라우저의 OAuth 사용 동의와 별개입니다. ChatGPT 권한/한도 오류가 발생해도 **API 키 방식으로 자동 전환하지 않습니다.**

Plus 사용량은 다른 연결 앱과 한도를 공유하며 무제한이 아닙니다. 설정한 계정의 앱별 한도나 허용한 추가 크레딧이 적용될 수 있습니다. [ChatGPT Settings → Usage](https://chatgpt.com/settings/usage)에서 관리하세요. 사용량 한도(`subscription_sharing_usage_limit_exceeded`)·이용 자격·지원 기능 오류는 즉시 중단합니다. 일시적 가용성 오류와 일반 rate limit만 기존 제한 재시도를 사용합니다. 스트림에서 실패한 응답도 usage가 있으면 원장에 기록하며 `response.completed` 이전의 텍스트를 성공 캐시에 넣지 않습니다.

## ChatGPT 계정 관리와 로컬 저장

```bash
# 다른 계정/workspace는 별도 이름으로 등록; 영문/숫자/_/- 1~40자
python translate_epub.py --chatgpt-login --chatgpt-profile personal
python translate_epub.py --list-models --chatgpt-profile personal

# 사용량 사용 권한을 앞서 거부한 경우 명시적으로 다시 동의
python translate_epub.py --chatgpt-login --chatgpt-enable-plan --chatgpt-profile personal

# 선택 계정의 세션 해제
python translate_epub.py --chatgpt-logout --chatgpt-profile personal
```

위 계정으로 번역할 때도 `--chatgpt-profile personal`을 붙입니다. 같은 profile 이름으로 다른 계정의 자격 증명을 덮어쓰지 않습니다. 별도 이름을 사용하세요.

자격 증명 기본 경로는 **`~/.epub_translation_tool/chatgpt/session.json`**입니다. 디렉터리 `0700`, 파일 `0600`으로 보호하고 심볼릭 링크를 거부합니다. 토큰은 암호화하지 않은 본인 전용 파일에 저장됩니다. `--chatgpt-auth-dir /private/path`로 바꿀 수 있으며 소스/번역 출력/공유 폴더와 분리하세요. 토큰·인증 code·callback URL·ID token hint를 로그나 결과 파일에 기록하지 않습니다.

최초 등록에는 설치별 고정 host ID와 공식 `dynamic_agent_client`를 사용하고, 발급받은 `client_id`를 계정의 검증된 `sub`와 함께 저장해 재로그인/갱신에 재사용합니다. callback은 `127.0.0.1`의 임시 포트와 고정 `/auth/callback` 경로로만 수신합니다. PKCE S256, 요청별 state/nonce, OpenAI JWKS 서명·issuer·audience·만료·subject를 검증하며 granted scope에 `chatgpt.tokens.use.direct`가 없으면 로그인 상태만 저장하고 추론은 차단합니다. 브라우저가 열리지 않으면 터미널에 표시한 **로컬 `/login` 주소**를 직접 열 수 있습니다.

토큰 갱신은 파일 잠금 안에서 직렬화하고 회전된 refresh token을 원자적으로 저장합니다. 일시적인 갱신 오류는 기존 자격 증명을 보존하며, 사용 불가능한 refresh token 오류는 토큰을 제거하고 재로그인을 안내합니다. 로그아웃은 원격 세션 해제를 시도한 뒤 로컬 토큰을 지웁니다. 해제가 확인되지 않으면 `remote_revocation_confirmed: false`를 표시하므로 ChatGPT 설정에서도 연결을 해제하세요. 등록 client ID와 host ID는 다음 로그인을 위해 유지합니다. 실행 중 OAuth 사용 동의가 취소되어도 유료 API 과금으로 넘어가지 않습니다.

## 무료 구조 확인·실행 계획

```bash
python translate_epub.py --input /path/to/book.epub --list-chapters
python translate_epub.py --input /path/to/book.epub --chapter 1 --dry-run --review
python translate_epub.py --input /path/to/book.epub --all-chapters --dry-run --review
```

`plan.json`에는 문단 수·원문 문자 수·알려진 캐시 적중 수·재시도를 제외한 예상 신규 호출 수·재시도를 포함한 최대 요청 시도 수가 기록됩니다. 프롬프트·문맥·스키마를 포함한 알려진 요청 문자 수도 표시합니다. 검수 본문은 번역 후 확정되므로 캐시가 없으면 그 분량을 미산정으로 표시합니다. 캐시 상태에 따른 실제 호출 수는 실행 중 달라질 수 있습니다.

문자 수를 토큰 수로 가장하지 않습니다. 사전 `estimated_tokens`/`estimated_cost`는 `null`이며 금액 상한을 보장하지 않습니다. 실제 응답 usage와 외부 요금표로 비용을 기록합니다. `--dry-run`은 선택한 방식의 승인 옵션과 함께 있어도 항상 오프라인입니다. `source.preview.json`에는 원문 AST·문단·경고가 저장되며 기존 완성본과 manifest를 덮어쓰지 않습니다.

## 기존 API 키 방식으로 실행

다음 명령은 사용자가 실행할 때 별도 API 비용을 발생시킵니다. ChatGPT 구독 사용량을 쓰려면 위의 `--approve-plan` 명령을 사용하세요. 기존 API 명령에는 이제 `--provider openai`를 명시해야 합니다.

```bash
python translate_epub.py \
  --provider openai --input /path/to/book.epub --chapter 1 --sample-paragraphs 3 \
  --glossary examples/demian_glossary.json \
  --consistency examples/consistency.example.json \
  --review --approve-api
```

작품별 지침 예시는 대상 작품의 실제 원문에 맞게 수정하세요. 전체 번역:

```bash
python translate_epub.py \
  --provider openai --input /path/to/book.epub --all-chapters --include-prologue \
  --styles faithful modern easy --review \
  --glossary examples/demian_glossary.json \
  --consistency examples/consistency.example.json \
  --pricing /path/to/your-rates.json --approve-api
```

`--chapter`의 기본값은 1, 프롤로그는 0입니다. `--all-chapters`가 있으면 선택 장 번호보다 우선합니다. 프롤로그는 `--include-prologue` 또는 `--chapter 0`으로 포함합니다. `--sample-paragraphs N`은 각 선택 장의 처음 N문단을 별도 `sample-N/`에 저장하며 제목도 별도 번역합니다.

`--max-items 8`, `--max-chars 2600`으로 문단 경계를 유지해 배치를 만듭니다. 긴 단일 문단은 분할하지 않으므로 문자 제한을 넘을 수 있고 계획에 해당 ID를 표시합니다. 입력 길이를 고려해 모델과 배치 크기를 선택해야 합니다. `--context-items 2`/`--context-chars 1600`은 앞뒤 각각의 원문 참고 범위입니다. 인접 장과 샘플 바깥 원문도 참고할 수 있습니다. `0`이면 문맥을 끕니다.

세 스타일에는 같은 원문 배치·앞뒤 원문·용어집·작품별 호칭/인물/시점 지침을 전달합니다. 참고 문맥은 번역 대상과 명시적으로 구별하며 다른 스타일 번역이나 이전 번역문을 전달하지 않습니다. 작품 전체 일관성은 명시적 지침과 AI 검수에 의존하며 완전한 보장은 아닙니다.

## AI 검수와 상태

`--review`는 각 스타일/배치에 추가 검수 요청을 보냅니다. `--review-model MODEL`로 번역 모델과 분리할 수 있습니다. 원문·번역·용어집·호칭/시점 지침·스타일 규칙을 함께 대조하며 다음 항목을 ID와 설명으로 기록합니다.

- `omission`: 번역 누락
- `addition`: 원문에 없는 정보 추가
- `distortion`: 의미 왜곡
- `terminology`: 용어/호칭 불일치
- `style`: 지정 문체 위반

`review_report.json`의 각 `issues` 항목에는 기존 `id`, `category`, `description`과 함께 `chapter_number`(챕터 번호), `paragraph_number`(본문 문단 번호), `source_text`(원문 전체), `translation_text`(해당 스타일의 현재 번역문 전체)를 저장합니다. 본문 문단 번호는 추출한 본문 항목의 순서대로 1부터 시작하며 제목은 0입니다. 프롤로그의 챕터 번호는 0입니다. EPUB의 화면별 페이지 번호 대신 이 위치와 문장으로 찾을 수 있습니다. 이 정보는 저장 단계에서 붙이므로 AI 요청 형식·번역/검수 캐시는 바뀌지 않습니다.

다음은 보고서 형식을 설명하기 위한 가상 문단 예시입니다.

```json
{
  "faithful": {
    "issues": [
      {
        "id": "chapter-example-p-001",
        "category": "terminology",
        "description": "knocker는 문고리가 아니라 문 두드리개를 뜻합니다.",
        "chapter_number": 1,
        "paragraph_number": 1,
        "source_text": "The brass knocker was heavy.",
        "translation_text": "놋쇠 문고리는 묵직했다."
      }
    ],
    "ai_review_executed": true,
    "ai_review_passed": false,
    "human_review_required": true
  }
}
```

`ai_review_executed: true`는 AI 검수 결과가 반영되었다는 뜻이며 캐시에서 재사용한 결과도 포함합니다. `description`은 문제 설명과 수정 제안이고, `translation_text`는 저장 시점의 실제 번역문입니다. 수정 제안이 보고서에 있어도 번역에 자동 반영되지는 않습니다.

잘못된 ID·카테고리·빈 설명은 응답 오류로 처리해 재시도하며 조용히 버리지 않습니다. 자동 수정은 하지 않습니다. 문단별 `review_status`는 `not_generated`, `not_reviewed`, `passed`, `flagged`입니다. 요청한 배치의 유효 검수 결과에 문제가 없을 때만 `passed`입니다. 리포트의 `ai_review_passed`는 미검수 시 `null`, 검수 완료 후 이슈가 없으면 `true`입니다. 구조 검증 통과와 의미 검수 통과는 서로 다릅니다. `human_review_required`는 항상 `true`이며 사람의 출판 승인과 구분합니다. 검수 이슈가 있어도 결과를 저장하므로 서비스 배포 여부는 이 상태로 판단하세요.

## 저장·재개·비용

같은 명령을 다시 실행하면 검증된 번역·검수 캐시를 재사용합니다. `--max-attempts 3`은 캐시 미적중 요청별 최대 시도 횟수입니다. 408/409/429/5xx, 연결·타임아웃 오류, 스키마/ID/완료 상태 오류를 제한 재시도합니다. 지수 backoff와 jitter, 서버 `Retry-After`를 사용하고 60초 초과 대기는 오류로 종료하여 나중에 재개하게 합니다. 인증·권한 등 영구 API 오류는 즉시 중단합니다. SDK 내부 자동 재시도는 꺼서 요청 시도를 개별 기록합니다.

캐시 키에는 provider(`chatgpt`/`openai`), 파이프라인/파서 버전, 원본 EPUB SHA-256, 모델, 프롬프트 전체, 스키마, 제목, 원문 배치, 문맥, 용어집, 작품별 지침을 포함합니다. 검수 키에는 실제 검수 번역과 검수 모델/프롬프트도 포함합니다. 원본 EPUB의 어느 부분이든 바뀌면 해당 책의 캐시는 무효화됩니다. 기존 MVP 캐시는 포맷이 달라 재사용하지 않습니다. 이번 provider 분리 전 캐시도 공급 경로가 명시되지 않아 다시 생성합니다.

캐시/시도 기록/출력은 같은 디렉터리의 임시 파일을 `fsync` 후 `os.replace`하는 방식으로 저장합니다. `fcntl` 잠금으로 같은 장 출력에 대한 동시 쓰기를 거부합니다. 로컬 macOS/Linux 파일시스템을 대상으로 하며 분산 워커나 Windows용 잠금은 구현하지 않았습니다.

각 요청 전 `_관리/.usage/<attempt-id>.json`을 만들고 응답 usage를 **JSON 검증 전에** 기록합니다. 잘못된 응답·재시도·검수의 토큰도 합산됩니다. `usage.json`은 이번 실행과 출력 디렉터리 누적 통계를 분리하며 캐시 재사용에는 신규 사용량을 더하지 않습니다. 누적 통계에는 이전 원문/설정 버전의 시도도 포함됩니다. `_관리/.usage`가 원본 기록이며 `usage.json`은 실행 종료 시 갱신되는 요약입니다.

ChatGPT 경로는 사용 토큰을 기록하되 `cost.billing_mode=chatgpt_plan`, 금액/통화는 `null`로 두며 API 달러 요금을 적용하지 않습니다. `--pricing`과 ChatGPT provider를 함께 지정하면 오류입니다. 구독 사용량·크레딧 잔액은 토큰 수만으로 계산하지 않고 ChatGPT Usage에서 확인하세요.

API 키 방식의 요금표는 다음 구조입니다. **`examples/pricing.example.json`의 0은 형식 설명용 자리표시자**이므로 실제 검증된 요금으로 바꿔 별도 파일을 사용하세요. 예제 값을 실요금으로 사용하지 마세요.

```json
{
  "YOUR_MODEL_ID": {
    "currency": "USD",
    "input_per_million": "0",
    "cached_input_per_million": "0",
    "output_per_million": "0",
    "as_of": "REPLACE_WITH_RATE_DATE"
  }
}
```

요금은 코드에 하드코딩하지 않습니다. 입력 토큰에서 cached input을 분리하고 출력 토큰을 곱해 Decimal로 계산하며 적용 요금표를 시도 기록에 보존합니다. 금액은 JSON 문자열입니다. 요금표가 없거나 타임아웃 등으로 usage를 받지 못하면 비용은 `null`, 토큰 미상 횟수는 별도 집계합니다. 이는 무료 요청을 뜻하지 않습니다. 응답을 기록하기 전 강제 종료되거나 서버 처리 후 연결이 끊기면 청구/성공 여부가 불명확하여 재실행이 중복 비용을 만들 수 있습니다. 기록된 토큰·설정 요금 기준의 계산이며 API 청구서와의 자동 대사는 포함하지 않습니다.

## 출력과 앱 연동

기본 책 폴더명은 입력 EPUB의 파일명에서 확장자를 뺀 값입니다. 이름을 바꾼 책은 출력 루트의 숨김 파일 `.book-names.json`에 원본 stem → 폴더명 매핑을 보관합니다. 현재 데미안은 `hermann-hesse_demian_n-h-priday` → **`hermann-hesse_demian`**으로 설정되어, 원본 EPUB 파일명을 유지하고 다음 챕터도 짧은 이름의 폴더에 저장합니다. 매핑은 출력 위치별로 적용하며 번역 내용·판본 정보·캐시 키에는 영향을 주지 않습니다.

```text
output/<epub-name>/
  book.json                       # --all-chapters 완료 시 발행
  chapter-01/
    번역본/
      faithful.md                 # 원문 충실형
      modern.md                   # 자연스러운 현대어
      easy.md                     # 쉬운 한국어
    _관리/
      plan.json
      source.preview.json         # 오프라인 원문 확인
      source.json
      aligned.json                # 앱용 원문·세 번역 정렬
      review_report.json          # AI 검수 결과
      usage.json
      manifest.json
      generations/<revision>/     # 완성된 출력 스냅샷
      .cache/                     # 재개용 체크포인트
      .usage/                     # 개별 요청 원장
```

읽거나 복사할 번역본은 **`번역본/`**에만 저장합니다. JSON, 캐시, 요청 기록, 버전 스냅샷은 **`_관리/`**에 보관합니다. `--sample-paragraphs N` 결과도 `chapter-01/sample-N/번역본/`, `chapter-01/sample-N/_관리/`로 같은 구조를 사용합니다. 실행 잠금 `.pipeline.lock`과 사용자가 만든 파일은 장 폴더에 그대로 둡니다.

기존 결과는 다음 명령으로 **번역 요청 없이** 정리할 수 있습니다. 이후 번역/재개 명령으로 해당 장을 열어도 자동으로 정리합니다.

```bash
python translate_epub.py --organize-output output/hermann-hesse_demian/chapter-01
```

기존 Markdown·JSON·캐시·버전 파일의 내용은 변경하지 않고 위치만 옮깁니다. 같은 이름의 파일이 새 위치에 이미 있으면 덮어쓰지 않고 중단합니다. 기존 실행과 같은 잠금 파일을 사용하여 번역 실행 중에는 이동하지 않습니다. 이동 중 중단되어도 같은 명령으로 나머지를 정리할 수 있습니다. `book.json`이나 샘플 책 인덱스가 있으면 해당 장의 경로도 갱신합니다. 외부 도구에서 기존 장 루트의 파일을 직접 참조했다면 새 위치로 변경해야 합니다.

앱은 **`_관리/manifest.json`**을 읽고, 그 파일이 있는 `_관리/` 폴더를 기준으로 상대 `aligned` 경로를 해석합니다. 버전별 파일을 모두 저장한 후 manifest를 원자적으로 교체합니다. `번역본/`의 Markdown과 `_관리/`의 JSON은 편의용 사본이며 여러 파일이 한 번에 교체되는 것은 아닙니다. 책 루트의 `book.json`은 각 장의 고정 `revision`, 새 `manifest` 경로와 고정 `aligned` 경로를 포함합니다. 실패 시 전체 책 인덱스는 새로 발행하지 않습니다.

`aligned.json`은 `schema_version`, `chapter_id`, `version`(원문 버전), `revision`(출력 스냅샷), `model`, `review_model`, `sample`, 번역된 `title`, 정렬된 `paragraphs`를 포함합니다. 문단은 `chapter_id`, `paragraph_id`, `source_text`, `translations.{faithful,modern,easy}`, 문체별 `review_status`, `version`, `model`, 원문 위치를 가집니다. 요청하지 않은 스타일은 `null`입니다. 기존 소비자를 위한 `id`, `source`, 최상위 `chapter`도 유지합니다. 실제 응답의 model ID는 사용량 원장과 캐시에 남고 출력의 `model`은 요청한 모델 ID입니다.

## EPUB 파싱 범위

표지·제목 페이지·판권·출판 정보·목차처럼 본문이 아닌 의미 태그가 있는 spine 문서는 로그에 남기고 제외합니다. OPF spine 순서, 한 파일 안의 여러 장, `doc-chapter`/`doc-prologue`, 의미 태그 없는 XHTML fallback을 처리합니다. 제목·문단·인용문·각주·제목 묶음·강조 등의 **원문 요소/속성/text/tail 전체 트리**를 `source.json`의 AST에 보존합니다. `<hgroup>`은 장 제목의 번역 원문으로 사용하여 제목 묶음 안의 문단을 본문과 중복 번역하지 않습니다. 일반 heading·각주·인용문은 별도 ID로 번역하고 `<br>`/`pre`의 줄바꿈을 보존합니다. 미지원 요소와 장 밖의 텍스트, 비선형 문서, 빈 장은 경고합니다. spine 문서가 없거나 지원하지 않는 미디어이면 오류로 중단합니다. 프롤로그 제외도 로그에 표시합니다.

chapter_id는 EPUB 내부 문서 경로와 장의 XML id/구조 위치, paragraph_id는 장 ID와 요소의 XML id/구조 위치에서 생성합니다. 같은 위치의 원문 수정이나 장 순서 변경에도 XML id가 유지되면 ID가 안정적입니다. XML id 없는 구조를 삽입·이동하거나 내부 파일 경로를 바꾸면 ID도 달라질 수 있습니다. 동일 원문으로 만든 세 스타일은 순서·개수·ID가 정확히 같아야 최종 출력됩니다.

**AST 보존은 번역문의 서식 복원을 의미하지 않습니다.** 강조·링크·각주 참조를 한국어 어절 위치로 다시 연결하거나 EPUB을 재출판하는 기능은 없습니다. Markdown은 제목·인용·줄바꿈을 반영하지만 표·이미지·수식·중첩 목록·인라인 강조의 완전한 재현은 지원하지 않습니다. 복잡한 EPUB은 경고와 AST를 대조해 확인해야 합니다. DRM EPUB은 지원하지 않습니다.

## 구조와 구현 단계

```text
translate_epub.py               # 기존 진입점과 최소 호환 helper
  epub_pipeline/cli.py          # 인자·승인·의존성 조립
  epub_pipeline/domain.py       # Chapter/응답 모델, 배치·정렬·검수 검증
  epub_pipeline/ports.py        # LLMProvider, Repository 인터페이스
  epub_pipeline/pipeline.py     # 번역·검수·재개·출력 use case
  epub_pipeline/parser.py       # EPUB/XHTML adapter
  epub_pipeline/provider.py     # API 키 방식 OpenAI SDK adapter
  epub_pipeline/chatgpt_provider.py # OAuth bearer + 스트리밍 Responses
  epub_pipeline/chatgpt_auth.py  # 브라우저 PKCE/OIDC·모델 조회·갱신/해제
  epub_pipeline/chatgpt_store.py # 계정별 비공개 자격 증명·파일 잠금
  epub_pipeline/repository.py   # 읽기/관리 출력 분리·기존 결과 이동·원자적 저장·캐시·잠금
  epub_pipeline/pricing.py      # 외부 요금표와 비용 집계
  epub_pipeline/prompts.py      # 번역/검수 지침과 스키마
```

기존 분석에서 확인한 문제는 단일 파일에 책임이 몰린 점, 재시도 사용량 누락, 검수 응답의 잘못된 ID를 버리는 점, 검수 프롬프트가 캐시 키에 없는 점, 앞뒤 문맥 부재였습니다. 다음 순서로 개선했습니다.

1. 기존 CLI와 helper 테스트를 유지하며 domain/use case/adapter 분리.
2. spine·원문 AST·장/문단 ID·문맥·작품별 일관성 지침 구현.
3. 독립 번역·엄격한 검수 검증·제한 재시도·원자적 재개·비용 원장·명시적 API 승인 구현.
4. 버전별 앱 JSON/Markdown 발행, 네트워크 없는 통합 테스트와 운영 제약 문서화.

OpenAI Responses API 형식은 [official OpenAI documentation](https://developers.openai.com/api/docs/guides/structured-outputs)을 참고했습니다. Structured Outputs 뒤에도 애플리케이션에서 ID·텍스트·검수 항목·완료 상태를 재검증합니다.

## 테스트와 남은 검증

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q epub_pipeline translate_epub.py tests
```

테스트는 가짜 provider/SDK 응답과 ZIP으로 만든 합성 EPUB을 사용합니다. 신규 통합 테스트는 socket 연결도 차단합니다. 독립 번역·문단 정렬·스타일 상태·원문/설정/프롬프트 캐시 무효화·오류 응답 토큰 집계·재시도·중단 재개·동시 실행 잠금·manifest 발행 실패·승인 없는 CLI 실행을 검증합니다.

개발 중 별도로 제공받은 `hermann-hesse_demian_n-h-priday.epub`로 **8개 장·1장 본문 91문단** 회귀 테스트를 실행해 통과했습니다. 표지/판권 페이지를 본문 장으로 세던 fallback 오류도 수정했습니다. 이 EPUB은 저장소에 포함하지 않습니다. 같은 판본을 사용자가 별도로 준비한 경우에만 다음처럼 경로를 지정해 해당 회귀 테스트를 실행하세요. `DEMIAN_EPUB`을 지정하지 않으면 이 테스트만 skip되며 나머지 테스트는 합성 EPUB으로 실행됩니다.

```bash
DEMIAN_EPUB=/path/to/hermann-hesse_demian_n-h-priday.epub \
  python3 -m unittest discover -s tests -v
```

OAuth 테스트는 잘못된 state/nonce/client/계정, 실제 RSA 서명·issuer·audience·만료, 동의 거부, 갱신/회전·로그아웃, 비공개 파일 권한, 브라우저 callback과 토큰 로그 비노출을 검증합니다. OpenAI SDK 테스트는 모의 HTTP/SSE transport로 실제 SDK 파싱까지 확인하며 원격 네트워크 연결을 차단합니다. 의존성을 설치한 환경에서 실제 데미안 회귀 테스트를 포함한 전체 66개 테스트를 통과했습니다. 검수 보고서의 위치·원문·스타일별 번역문과 캐시 재사용 시 보고서 형식도 검증합니다.

후속 검증은 사용자의 실제 브라우저 로그인과 이용 자격 확인, 명시적으로 승인한 3문단 번역 샘플, 세 문체별 의미·인물 호칭 평가입니다. 실제 서비스의 분산 큐/DB·사용자 인증·배포 승인 UI·모니터링 및 청구 대사는 범위에 포함하지 않았습니다. AI 검수는 누락/왜곡을 모두 검출한다고 보장하지 않으며 출판 전 원문 대조가 필요합니다. 원작·사용 원문 판본의 이용 권리도 별도로 확인해야 합니다.
