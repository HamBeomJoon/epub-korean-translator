# 상세 사용법

[빠른 시작](../README.md) · [개발 안내](development.md)

명령은 프로젝트 루트에서 실행합니다. 원본 EPUB은 이용 권한을 확인해 직접 준비하세요.

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

`--consistency`에 전달하는 JSON은 작품 전체의 번역 기준인 '작품 바이블'로 사용할 수 있습니다. 원문 샘플을 읽고 서술 시점·시제, 인물 표기, 누가 누구에게 어떤 높임 등급과 호칭을 쓰는지, 의도적 반복·모호함의 처리 방침을 정해 사람이 확인한 뒤 넣으세요. 인물 관계가 바뀌면 그 조건도 적고, 용어의 확정 표기는 `--glossary`에 모으세요. 이 도구가 바이블을 자동 생성하거나 배치 사이에서 새 용어·인물 관계를 누적하지는 않습니다.

번역 프롬프트는 정보 보존 → 원문의 문체와 효과 → 자연스러운 한국어 순으로 우선합니다. 번역투 교정 기준은 의미나 의도적 문체를 훼손하지 않는 범위에서 적용합니다. 별도 우리말 다듬기 호출은 없으며, `--review`는 원문 대조 검수를 추가합니다. 검수는 원문·번역 표현과 판단 근거, 최소 수정 제안을 기존 `description` 필드에 보고하고 자동 반영하지 않습니다.

문단 경계로 배치하며 기본값은 `--max-items 8 --max-chars 2600`입니다. 긴 단일 문단은 쪼개지 않으므로 문자 제한을 넘을 수 있습니다. 앞뒤 원문은 각각 `--context-items 2 --context-chars 1600`까지 참고하며 `0`이면 문맥을 끕니다. 세 스타일 모두 같은 원문·문맥·용어집을 받고 다른 스타일의 번역문을 중간 원본으로 사용하지 않습니다.

`plan.json`은 예상 신규 요청 수, 캐시 적중 수, 최대 시도 횟수와 알려진 요청 문자 수를 기록합니다. 아직 생성되지 않은 검수 본문·출력·추론 토큰·재시도 분량은 미산정이며 토큰과 비용의 사전 추정값은 `null`입니다.

### 프롬프트 파일 편집

공통 번역·문체·검수 지침은 `epub_pipeline/prompts.md`에 있습니다. 다음 다섯 구역의 이름을 그대로 유지하고 본문을 수정하세요.

| 구역 제목 | 용도 |
|---|---|
| `## base` | 모든 번역에 적용할 충실성·한국어·작품 지침·문단·출력 규칙 |
| `## faithful` | 원문 충실형 문체 |
| `## modern` | 자연스러운 현대어 문체 |
| `## easy` | 쉬운 한국어 문체 |
| `## review` | `--review` 실행 시 원문 대조 검수 지침 |

구역 제목은 모델에 전달하지 않고 각 구역의 본문을 읽습니다. 구역 안의 소제목은 `###` 또는 `[제목]`을 쓰세요. `## `는 구역 구분용입니다. 필수 구역이 없거나 비어 있거나, 이름이 중복되거나 알 수 없는 구역이 있으면 시작 시 오류로 알립니다. 파일은 UTF-8로 저장하고 프로그램과 함께 보관하세요.

로더는 현재 작업 폴더가 아닌 `prompts.py` 옆의 Markdown을 읽으므로 다른 폴더에서 CLI를 실행해도 같은 지침을 사용합니다. Python 모듈을 처음 불러올 때 읽으며, 실행 중 편집한 내용은 프로세스를 다시 시작해야 적용됩니다. `prompts.py`에는 로딩 코드·문체 표시명·JSON 출력 스키마가 남습니다. 출력 필드를 추가하려면 Markdown뿐 아니라 스키마와 응답 검증·저장 코드도 함께 변경해야 합니다.

캐시 키에는 파일 경로가 아닌 실제 요청 지침이 들어갑니다. 첫 구역 앞의 문서 안내나 본문의 바깥 공백만 바꿔 요청 내용이 같으면 캐시는 유지됩니다. 공통·문체 지침을 바꾸면 해당 번역 요청이 갱신됩니다. 검수 캐시는 검수 지침·문체 지침·실제 번역문 등 요청 내용이 같을 때 재사용할 수 있고, 검수 지침만 바꾸면 번역 캐시는 유지됩니다. `--dry-run`으로 새 요청 수를 먼저 확인할 수 있습니다.

## 동시 요청 제한

기본값은 `--workers 2`이며 동시 번역·검수 요청의 상한은 2개입니다. 한 작업은 한 문체의 한 원문 배치를 번역하고 그 결과를 검수합니다. 서로 독립적인 작업만 병렬로 실행하며, 해당 배치의 검수는 번역이 완료된 뒤 시작합니다. 모델·배치 크기·원문과 앞뒤 문맥·용어집·문체 및 검수 프롬프트는 순차 실행과 같습니다. 완료 순서가 달라도 번역문과 지적 사항은 원래 문단 순서로 정렬해 저장합니다.

`--workers 1`을 추가하면 기존 순차 실행으로 전환합니다. workers 값은 요청 내용과 캐시 키에 포함하지 않으므로 기존 캐시를 재사용할 수 있습니다. 실행 계획에는 `workers`가 표시됩니다. 단일 문체도 서로 독립적인 배치가 여러 개면 두 작업을 동시에 처리할 수 있습니다. 장별 실행과 최종 발행은 순서를 유지합니다.

일시적인 요청 제한과 서버 오류는 기존 `--max-attempts`·`Retry-After` 정책에 따라 해당 작업이 대기 후 재시도합니다. 요청 제한이 자주 발생하면 `--workers 1`로 실행하세요. 인증·사용량 한도·최종 실패가 발생하면 새 배치 예약과 후속 검수를 중단합니다. 이미 진행 중인 응답은 사용량과 유효한 캐시를 기록한 뒤 종료하므로 실패·Ctrl+C 후에도 종료까지 시간이 걸릴 수 있습니다. 서버에 전송된 요청의 즉시 취소는 보장하지 않습니다. 오류 상태에서는 최종 결과를 새로 발행하지 않으며 같은 명령으로 재개할 수 있습니다.

동시 2개가 정확히 두 배의 속도를 보장하지는 않습니다. 서버 처리 속도·사용량 제한·재시도에 따라 효과가 달라집니다. 요청 수와 내용은 유지하지만 생성 길이와 재시도에 따라 실제 토큰 사용량이 달라질 수 있습니다. 실제 계정의 처리 시간과 번역 품질은 별도 평가가 필요합니다.

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

## 진행률과 macOS 알림

승인한 번역 실행에서는 다음 정보를 stderr에 표시합니다. stdout의 실행 계획 JSON과 기존 완료 메시지는 유지합니다.

```text
[선택 1/8 · 1장] · 원문 충실형 · 번역 · 현재 배치 2/12 · 완료 작업 7/72 · 캐시 0 · 요청 중 (시도 1) · 경과 8분 32초
```

`선택 1/8`은 선택한 장들 중 처리 순서이며 `1장`은 EPUB의 실제 장 번호입니다. 현재 배치는 원문 묶음 번호입니다. 완료 작업 수는 **현재 장에서 문체별 번역 배치와 검수 배치를 각각 1개**로 셉니다. 응답 검증과 캐시 저장을 마친 뒤 증가하며 재시도는 완료 수를 늘리지 않습니다. 검증된 캐시를 읽은 작업도 완료 수에 포함하고 캐시 개수를 별도로 표시합니다. 번역·검수 작업 수가 모두 채워진 뒤에는 결과 저장 단계가 이어집니다.

대화형 터미널에서는 같은 줄에서 경과 시간을 약 1초마다 갱신하므로 긴 요청 중에도 시간을 볼 수 있습니다. 출력이 파일이나 파이프로 연결되면 상태가 바뀔 때만 줄 단위로 기록합니다. 경과 시간은 이번 전체 실행 기준이고 완료 수는 장마다 다시 시작합니다. 배치별 처리 시간이 달라 예상 남은 시간은 표시하지 않습니다.

병렬 실행에서는 진행 중인 작업 수를 함께 표시하고, 그중 먼저 시작한 활성 작업의 문체·단계를 보여줍니다. 완료 수와 캐시 수는 현재 장의 모든 작업을 합산합니다.

macOS에서는 선택한 모든 장과 책 인덱스(전체 책 실행 시)의 저장을 마친 뒤 **완료 알림을 한 번** 보냅니다. 실행 중 오류와 키보드 중단은 각각 실패·중단 알림으로 구분하며 완료한 장 수와 경과 시간을 포함합니다. 캐시만 사용한 실행도 완료 알림을 보냅니다. 완료는 파일 생성 완료를 뜻하며 AI 지적 해결이나 사람의 승인과는 별개입니다.

`--no-progress`로 진행률만, `--no-notify`로 알림만 끌 수 있으며 두 옵션을 함께 사용할 수 있습니다. 종료 시 소요 시간 표시는 유지됩니다. dry-run·미승인 계획·로그인·모델 조회·기존 HTML 생성에는 번역 진행률과 알림을 실행하지 않습니다.

알림은 추가 패키지 없이 macOS의 `osascript`를 사용합니다. 다른 OS에서는 알림을 생략하고 진행률은 유지합니다. macOS 알림 권한과 방해금지 설정에 따라 배너가 표시되지 않을 수 있습니다. 알림 전송 실패·3초 초과는 경고만 남기며 번역 성공·실패 상태를 바꾸지 않습니다. 알림에는 책 원문·번역·토큰·오류 원문을 넣지 않습니다. 추가 AI 호출·토큰 사용은 없습니다.

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

## EPUB 지원 범위

EPUB의 제목·본문·각주·인용문과 원문 AST를 보존하지만 강조·링크·각주를 한국어 위치에 재연결하거나 EPUB을 재출판하지는 않습니다. 표·이미지·수식·중첩 구조는 경고와 AST를 대조하세요. DRM EPUB은 지원하지 않습니다.
