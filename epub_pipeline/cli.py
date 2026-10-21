"""CLI composition root. API clients are constructed only after explicit approval."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

from .domain import PARSER_VERSION, signature
from .parser import read_epub
from .pipeline import Settings, TranslationPipeline
from .pricing import RateCard
from .prompts import STYLE_PROMPTS
from .provider import OpenAIProvider
from .chatgpt_provider import ChatGPTProvider
from .chatgpt_auth import ChatGPTSession
from .chatgpt_store import CredentialStore
from .repository import FileRepository


def load_json(path: Path | None) -> dict:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 객체가 필요합니다: {path}")
    return value



def book_output_path(input_path: Path, output_root: Path) -> Path:
    """Honor persisted folder renames without changing the source edition or cache keys."""
    mapping_file = output_root / ".book-names.json"
    names = load_json(mapping_file) if mapping_file.exists() else {}
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in names.items()):
        raise ValueError(".book-names.json은 원본 파일 stem과 출력 폴더명의 문자열 매핑이어야 합니다.")
    name = names.get(input_path.stem, input_path.stem.replace(" ", "_"))
    if not name.strip() or name in {".", ".."} or any(c in name for c in ("/", "\\", "\0")):
        raise ValueError("출력 책 이름은 경로 구분자 없는 단일 폴더명이어야 합니다.")
    return output_root / name


def load_glossary(path: Path | None) -> dict[str, str]:
    value = load_json(path)
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError("용어집 JSON은 {'원문 표기':'한국어 표기'} 형태여야 합니다.")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EPUB 한국어 3종 독립 번역 / 검수 / Markdown·JSON 출력")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--provider", choices=("chatgpt", "openai"), default="chatgpt",
                        help="기본 chatgpt: Plus/Pro 사용량; openai: API 키 별도 과금")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--chatgpt-login", action="store_true", help="Continue with ChatGPT: 브라우저 로그인")
    actions.add_argument("--chatgpt-status", action="store_true", help="네트워크 없이 로그인 상태 확인")
    actions.add_argument("--chatgpt-logout", action="store_true", help="선택 profile 로그아웃·세션 해제")
    actions.add_argument("--organize-output", type=Path, help="기존 장 출력 폴더를 번역본/과 _관리/로 정리 (오프라인)")
    actions.add_argument("--list-models", action="store_true", help="선택 ChatGPT 계정의 모델 slug 조회 (번역 없음)")
    parser.add_argument("--chatgpt-profile", default="default", help="저장 계정 이름; 새 이름으로 다른 계정 등록")
    parser.add_argument("--chatgpt-auth-dir", type=Path, help="기본 ~/.epub_translation_tool/chatgpt, 자격 증명 전용")
    parser.add_argument("--chatgpt-enable-plan", action="store_true", help="--chatgpt-login 시 사용량 사용에 다시 동의")
    parser.add_argument("--approve-plan", action="store_true", help="이번 번역의 ChatGPT 사용량 사용을 승인")
    parser.add_argument("--chapter", type=int, default=1, help="선택 장 번호. 0은 프롤로그")
    parser.add_argument("--all-chapters", action="store_true")
    parser.add_argument("--include-prologue", action="store_true")
    parser.add_argument("--list-chapters", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="API 없이 원문과 계획 저장")
    parser.add_argument("--approve-api", action="store_true", help="이 실행의 실제 API 요청 및 비용을 명시적으로 승인")
    parser.add_argument("--sample-paragraphs", type=int, default=0)
    parser.add_argument("--styles", nargs="+", choices=list(STYLE_PROMPTS), default=list(STYLE_PROMPTS))
    parser.add_argument("--model", help="ChatGPT: --list-models의 slug를 직접 선택. openai 기본 gpt-5-mini")
    parser.add_argument("--review-model", help="검수용 모델. 생략하면 번역 모델 사용")
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--glossary", type=Path)
    parser.add_argument("--consistency", type=Path, help="작품별 인물/호칭/시점/문체 지침 JSON 객체")
    parser.add_argument("--pricing", type=Path, help="모델별 외부 요금표 JSON")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--max-chars", type=int, default=2600)
    parser.add_argument("--max-items", type=int, default=8)
    parser.add_argument("--context-items", type=int, default=2)
    parser.add_argument("--context-chars", type=int, default=1600)
    parser.add_argument("--max-attempts", type=int, default=3)
    return parser


def _print_elapsed(label: str, seconds: float) -> None:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds_part = divmod(remainder, 60)
    duration = (f"{hours}시간 " if hours else "") + f"{minutes}분 {seconds_part}초"
    print(f"{label} 소요 시간: {duration}", flush=True)


def run(args: argparse.Namespace) -> None:
    if args.organize_output is not None:
        if args.input or args.dry_run or args.approve_api or args.approve_plan or args.chatgpt_enable_plan:
            raise ValueError("--organize-output은 번역/로그인 인자와 분리해 실행하세요.")
        if not args.organize_output.is_dir():
            raise ValueError("정리할 기존 장 출력 폴더를 지정하세요.")
        repo = FileRepository(args.organize_output)
        with repo.lock():
            print(f"정리 완료: {len(repo.last_migration)}개 항목 이동")
            print(f"번역본: {repo.readable}")
            print(f"관리 파일: {repo.internal}")
        return
    auth_action = args.chatgpt_login or args.chatgpt_status or args.chatgpt_logout or args.list_models
    if args.chatgpt_enable_plan and not args.chatgpt_login:
        raise ValueError("--chatgpt-enable-plan은 --chatgpt-login과 함께 사용하세요.")
    if auth_action:
        if args.input or args.dry_run or args.approve_api or args.approve_plan:
            raise ValueError("로그인/모델 조회 명령은 번역 인자와 분리해 실행하세요.")
        session = ChatGPTSession(CredentialStore(args.chatgpt_auth_dir), args.chatgpt_profile)
        if args.chatgpt_login:
            result = session.login(enable_plan=args.chatgpt_enable_plan)
        elif args.chatgpt_status:
            result = session.status()
        elif args.chatgpt_logout:
            result = session.logout()
        else:
            result = session.models()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if not args.input:
        raise ValueError("--input EPUB 경로가 필요합니다.")
    if args.provider == "chatgpt" and args.approve_api:
        raise ValueError("ChatGPT 사용량 승인에는 --approve-plan을 사용하세요. API 키 방식은 --provider openai입니다.")
    if args.provider == "openai" and args.approve_plan:
        raise ValueError("API 키 실행 승인에는 --approve-api를 사용하세요.")
    if args.provider == "chatgpt" and args.pricing:
        raise ValueError("ChatGPT 구독 사용량에는 API 달러 요금표를 적용하지 않습니다.")
    approved = args.approve_plan if args.provider == "chatgpt" else args.approve_api
    model = args.model or ("gpt-5-mini" if args.provider == "openai" else "SELECT_CHATGPT_MODEL")
    if args.provider == "chatgpt" and approved and not args.dry_run and not args.model:
        raise ValueError("--list-models로 모델 목록을 확인하고 --model SLUG를 지정하세요.")
    if args.sample_paragraphs < 0:
        raise ValueError("sample-paragraphs는 0 이상이어야 합니다.")
    chapters = read_epub(args.input, args.include_prologue or args.chapter == 0)
    if args.list_chapters:
        for ch in chapters:
            print(f"{ch.index:>2} | {ch.title} | {len(ch.paragraphs)}문단 | {ch.source_file} | {ch.chapter_id}")
        return
    settings = Settings(model=model, provider=args.provider, review_model=args.review_model, styles=tuple(args.styles),
                        review=args.review, max_chars=args.max_chars, max_items=args.max_items,
                        context_items=args.context_items, context_chars=args.context_chars,
                        max_attempts=args.max_attempts, glossary=load_glossary(args.glossary),
                        consistency=load_json(args.consistency))
    rates = RateCard(load_json(args.pricing))
    selected = chapters if args.all_chapters else [c for c in chapters if c.index == args.chapter]
    if not selected:
        raise ValueError(f"{args.chapter}장을 찾을 수 없습니다. --list-chapters로 확인하세요.")
    digest = hashlib.sha256()
    with args.input.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    source_version = signature({"epub_sha256": digest.hexdigest(), "parser_version": PARSER_VERSION})
    base = book_output_path(args.input, args.output)
    provider = None
    completed = []
    started = time.monotonic()
    try:
        for original in selected:
            chapter = replace(original, paragraphs=original.paragraphs[:args.sample_paragraphs]) if args.sample_paragraphs else original
            pos = chapters.index(original)
            before = [p for c in chapters[:pos] for p in c.paragraphs][-settings.context_items:] if settings.context_items else []
            remaining = original.paragraphs[len(chapter.paragraphs):]
            after = (remaining + [p for c in chapters[pos + 1:] for p in c.paragraphs])[:settings.context_items]
            output = base / ("prologue" if chapter.kind == "prologue" else f"chapter-{chapter.index:02d}")
            if args.sample_paragraphs:
                output /= f"sample-{args.sample_paragraphs}"
            repo = FileRepository(output)
            with repo.lock():
                pipeline = TranslationPipeline(repo, settings, rates=rates)
                plan = pipeline.plan(chapter, source_version, before, after)
                repo.write("plan.json", plan)
                print(json.dumps(plan, ensure_ascii=False, indent=2))
                if args.dry_run or not approved:
                    repo.write("source.preview.json", {"title": chapter.title, "chapter_id": chapter.chapter_id,
                               "version": source_version, "source_file": chapter.source_file,
                               "paragraphs": chapter.paragraphs, "ast": chapter.ast, "warnings": chapter.warnings})
                    approval_option = "--approve-plan" if args.provider == "chatgpt" else "--approve-api"
                    print(f"요청 없음: {output} ({approval_option}으로 명시적 승인)")
                    continue
                if provider is None and plan["estimated_new_calls_without_retries"]:
                    provider = (ChatGPTProvider.from_saved_session(args.chatgpt_auth_dir, args.chatgpt_profile)
                                if args.provider == "chatgpt" else OpenAIProvider.from_environment())
                pipeline.provider = provider
                try:
                    bundle = pipeline.run(chapter, source_version, approved=True, sample=bool(args.sample_paragraphs),
                                          before=before, after=after)
                finally:
                    if pipeline.timing is not None:
                        status = {"completed": "완료", "interrupted": "중단", "failed": "실패"}[pipeline.timing["status"]]
                        _print_elapsed(f"{chapter.index}장 {status}", pipeline.timing["elapsed_seconds"])
                completed.append({"chapter_id": chapter.chapter_id, "revision": bundle["revision"],
                                  "manifest": str(repo.artifact_path("manifest.json").relative_to(base)),
                                  "aligned": str(repo.artifact_path(f"generations/{bundle['revision']}/aligned.json").relative_to(base))})
                print(f"완료: {output}")
        if args.all_chapters and approved and not args.dry_run:
            book_repo = FileRepository(base)
            # Chapter outputs are independently resumable; this pointer is published only after all finish.
            name = f"book.sample-{args.sample_paragraphs}.json" if args.sample_paragraphs else "book.json"
            with book_repo.lock():
                book_repo.write(name, {"source_version": source_version, "sample": bool(args.sample_paragraphs),
                                       "chapters": completed})
    finally:
        if approved and not args.dry_run:
            _print_elapsed("전체 작업", time.monotonic() - started)
