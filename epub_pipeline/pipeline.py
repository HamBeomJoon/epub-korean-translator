"""Application use case: source -> independent translations -> review -> publication."""
from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from concurrent.futures import CancelledError, FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .domain import (Chapter, PARSER_VERSION, VERSION, ProgressEvent, RetryableError, make_batches,
                     signature, validate_result, validate_review)
from .ports import LLMProvider, Repository
from .pricing import RateCard, summarize
from .prompts import BASE_PROMPT, STYLE_PROMPTS, STYLE_NAMES, RESULT_SCHEMA, REVIEW_SCHEMA, REVIEW_PROMPT
from .review_html import render_review_html
from .text_layout import annotate_layout, render_markdown, translation_text


@dataclass
class Settings:
    model: str = "gpt-5-mini"
    provider: str = "openai"
    review_model: str | None = None
    styles: tuple[str, ...] = tuple(STYLE_PROMPTS)
    review: bool = False
    max_chars: int = 2600
    max_items: int = 8
    context_items: int = 2
    context_chars: int = 1600
    max_attempts: int = 3
    glossary: dict[str, str] = field(default_factory=dict)
    consistency: dict[str, Any] = field(default_factory=dict)
    workers: int = 2

    def __post_init__(self):
        if self.provider not in {"openai", "chatgpt"}:
            raise ValueError("지원하지 않는 provider입니다.")
        if type(self.workers) is not int or self.workers not in (1, 2):
            raise ValueError("workers는 1 또는 2여야 합니다.")
        if not self.styles or len(set(self.styles)) != len(self.styles) or any(s not in STYLE_PROMPTS for s in self.styles):
            raise ValueError("중복 없이 유효한 번역 스타일을 지정하세요.")
        if min(self.max_chars, self.max_items, self.max_attempts) <= 0 or min(self.context_items, self.context_chars) < 0:
            raise ValueError("배치/시도 횟수는 양수, 문맥 범위는 0 이상이어야 합니다.")
        if not isinstance(self.glossary, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in self.glossary.items()):
            raise ValueError("용어집은 문자열 매핑이어야 합니다.")
        if not isinstance(self.consistency, dict):
            raise ValueError("consistency는 JSON 객체여야 합니다.")


class TranslationPipeline:
    def __init__(self, repository: Repository, settings: Settings, provider: LLMProvider | None = None,
                 rates: RateCard | None = None, sleep: Callable = time.sleep,
                 on_progress: Callable[[ProgressEvent], None] | None = None):
        self.repo, self.settings, self.provider = repository, settings, provider
        self.rates, self.sleep = rates or RateCard(), sleep
        self.run_id = uuid.uuid4().hex
        self.records = []
        self.cache_hits = 0
        self.timing: dict | None = None
        self.on_progress = on_progress
        self._state_lock = threading.RLock()
        self._stop = threading.Event()
        self._failure: BaseException | None = None

    def _progress(self, event: ProgressEvent):
        if self.on_progress is not None:
            try:
                self.on_progress(event)
            except Exception:
                logging.warning('진행률 표시 실패: 번역 작업을 계속합니다.')
                self.on_progress = None

    def _context(self, items, start, end, before, after):
        cfg = self.settings
        def trim(values, reverse=False):
            if not cfg.context_items or not cfg.context_chars:
                return []
            chosen = values[-cfg.context_items:] if reverse else values[:cfg.context_items]
            result, remaining = [], cfg.context_chars
            sequence = reversed(chosen) if reverse else chosen
            for item in sequence:
                text = item["text"][-remaining:] if reverse else item["text"][:remaining]
                if text:
                    result.append({"id": item["id"], "text": text})
                    remaining -= len(text)
                if not remaining:
                    break
            return list(reversed(result)) if reverse else result
        return {"context_before": trim(before + items[:start], True),
                "context_after": trim(items[end:] + after)}

    def _translation_requests(self, chapter, source_version, before, after):
        items = chapter.items
        start = 0
        for batch in make_batches(items, self.settings.max_chars, self.settings.max_items):
            end = start + len(batch)
            context = self._context(items, start, end, before, after)
            for style in self.settings.styles:
                yield style, batch, {
                    "model": self.settings.model,
                    "instructions": BASE_PROMPT + "\n" + STYLE_PROMPTS[style],
                    "payload": {"chapter_id": chapter.chapter_id, "book_chapter_title": chapter.title,
                                "source_version": source_version, "glossary": self.settings.glossary,
                                "consistency": self.settings.consistency, "paragraphs": batch, **context},
                    "schema": RESULT_SCHEMA, "name": "translated_paragraphs",
                }
            start = end

    def _review_request(self, request, batch, translated, style):
        return {"model": self.settings.review_model or self.settings.model,
                "instructions": REVIEW_PROMPT,
                "payload": {**request["payload"], "style": STYLE_NAMES[style],
                            "style_rules": STYLE_PROMPTS[style],
                            "paragraphs": [{**p, "source": p["text"], "translation": translated[p["id"]]} for p in batch]},
                "schema": REVIEW_SCHEMA, "name": "review_issues"}

    def _key(self, request):
        return signature({"pipeline_version": VERSION, "parser_version": PARSER_VERSION,
                          "provider": self.settings.provider, "request": request})

    def _cached(self, request, validator):
        saved = self.repo.load_cache(self._key(request))
        if saved is not None:
            try:
                return validator(saved["result"]), saved
            except (KeyError, TypeError, ValueError):
                logging.warning("검증 실패 캐시 무시: %s", self._key(request))
        return None

    def plan(self, chapter: Chapter, source_version: str, before=None, after=None) -> dict:
        calls, cache_hits, review_unknown, request_chars = 0, 0, 0, 0
        requests = list(self._translation_requests(chapter, source_version, before or [], after or []))
        for style, batch, req in requests:
            request_chars += len(req["instructions"]) + len(json.dumps(req["payload"], ensure_ascii=False)) + len(json.dumps(req["schema"]))
            cached = self._cached(req, lambda r: validate_result(batch, r))
            if cached:
                cache_hits += 1
            else:
                calls += 1
            if self.settings.review:
                if cached:
                    review_req = self._review_request(req, batch, cached[0], style)
                    request_chars += len(review_req["instructions"]) + len(json.dumps(review_req["payload"], ensure_ascii=False)) + len(json.dumps(review_req["schema"]))
                    if self._cached(review_req, lambda r: validate_review(batch, r)):
                        cache_hits += 1
                    else:
                        calls += 1
                else:
                    calls += 1
                    review_unknown += 1
        return {"provider": self.settings.provider, "workers": self.settings.workers,
                "chapter_id": chapter.chapter_id, "paragraphs": len(chapter.paragraphs),
                "source_characters": sum(len(p["text"]) for p in chapter.items),
                "translation_batches": len(requests), "known_cache_hits": cache_hits,
                "estimated_new_calls_without_retries": calls,
                "maximum_attempts_including_retries": calls * self.settings.max_attempts,
                "known_request_characters_including_cache": request_chars,
                "review_payloads_pending_translation": review_unknown,
                "estimated_tokens": None, "estimated_cost": None,
                "estimate_note": "문자 수는 알려진 요청만 포함. 미생성 검수 본문·출력/추론 토큰·재시도는 미산정.",
                "oversized_item_ids": [p["id"] for p in chapter.items if len(p["text"]) > self.settings.max_chars]}

    def _cost(self, model, usage):
        if self.settings.provider == "chatgpt":
            return {"amount": None, "currency": None, "billing_mode": "chatgpt_plan",
                    "note": "ChatGPT 구독 사용량: API 달러 요금을 적용하지 않음"}
        return self.rates.cost(model, usage)

    def _execute(self, request, validator, emit=lambda *args: None):
        if self._stop.is_set():
            raise CancelledError()
        cached = self._cached(request, validator)
        if cached:
            with self._state_lock:
                self.cache_hits += 1
            emit('cached')
            return cached[0]
        if self.provider is None:
            raise RuntimeError("승인된 LLM provider가 없습니다.")
        key = self._key(request)
        for attempt in range(self.settings.max_attempts):
            if self._stop.is_set():
                raise CancelledError()
            emit('request', attempt + 1)
            attempt_id = uuid.uuid4().hex
            record = {"run_id": self.run_id, "attempt_id": attempt_id, "request_key": key,
                      "stage": request["name"], "provider": self.settings.provider, "model": request["model"], "attempt": attempt + 1,
                      "started_at": datetime.now(timezone.utc).isoformat(), "status": "started",
                      "usage": None, "cost": self._cost(request["model"], None)}
            with self._state_lock:
                self.records.append(record)
            self.repo.record_attempt(attempt_id, record)
            delay = None
            try:
                response = self.provider.complete(**request)
                record.update(status="received", response_id=response.response_id,
                              actual_model=response.model, usage=response.usage, error_code=response.error_code,
                              error_param=response.error_param, request_id=response.request_id,
                              cost=self._cost(request["model"], response.usage))
                # Persist received usage before parsing: rejected outputs still incur cost.
                self.repo.record_attempt(attempt_id, record)
                if response.error_code and self.settings.provider == "chatgpt":
                    if response.retryable:
                        raise RetryableError(f"ChatGPT 일시 오류: {response.error_code}")
                    raise RuntimeError(f"{response.error_message or 'ChatGPT 요청 실패'} code={response.error_code}")
                if response.status != "completed":
                    raise ValueError(f"완료되지 않은 응답: {response.status}")
                raw = json.loads(response.text)
                value = validator(raw)
            except RetryableError as exc:
                record.update(status="transient_error", error_type=type(exc).__name__)
                for field in ("error_code", "error_param", "request_id", "status_code"):
                    if getattr(exc, field, None) is not None:
                        record[field] = getattr(exc, field)
                delay = exc.retry_after
                error = exc
            except (ValueError, TypeError, KeyError) as exc:
                record.update(status="invalid_response", error_type=type(exc).__name__)
                error = exc
            except BaseException as exc:
                record.update(status="interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed",
                              error_type=type(exc).__name__)
                for field in ("error_code", "error_param", "request_id", "status_code"):
                    if getattr(exc, field, None) is not None:
                        record[field] = getattr(exc, field)
                self.repo.record_attempt(attempt_id, record)
                raise
            else:
                record["status"] = "accepted"
                self.repo.record_attempt(attempt_id, record)
                self.repo.save_cache(key, {"result": raw, "response_id": response.response_id,
                                           "model": response.model, "usage": response.usage})
                return value
            self.repo.record_attempt(attempt_id, record)
            if attempt + 1 == self.settings.max_attempts:
                raise RuntimeError(f"{request['name']} {self.settings.max_attempts}회 실패: {error}") from error
            if delay is not None and delay > 60:
                raise RuntimeError(f"서버 Retry-After={delay}초. 이후 같은 명령으로 재개하세요.") from error
            delay = delay if delay is not None else min(30, 2 ** attempt) + random.uniform(0, 0.5)
            emit('retry_wait', attempt + 1, delay)
            if self.sleep is time.sleep:
                if self._stop.wait(delay):
                    raise CancelledError()
            else:
                self.sleep(delay)
        raise AssertionError("unreachable")

    def _run_batches(self, requests, execute):
        """Keep only workers tasks in flight and drain them before publishing usage."""
        if self.settings.workers == 1 or len(requests) <= 1:
            return [execute(index, item) for index, item in enumerate(requests)]
        results = {}
        remaining = iter(enumerate(requests))
        executor = ThreadPoolExecutor(max_workers=self.settings.workers, thread_name_prefix='epub-batch')
        pending = {}
        try:
            for _ in range(min(self.settings.workers, len(requests))):
                index, item = next(remaining)
                pending[executor.submit(execute, index, item)] = index
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                # Inspect every finished result before scheduling any replacements.
                for future in done:
                    results[pending.pop(future)] = future.result()
                if self._stop.is_set():
                    # A failing worker may have set the flag just after wait returned.
                    continue
                for _ in done:
                    next_item = next(remaining, None)
                    if next_item is None:
                        break
                    index, item = next_item
                    pending[executor.submit(execute, index, item)] = index
        except BaseException as exc:
            self._stop.set()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                logging.warning('중단 요청: 이미 전송된 요청의 사용량과 캐시를 정리한 뒤 종료합니다.')
            for future in pending:
                future.cancel()
            if isinstance(exc, CancelledError) and self._failure is not None:
                raise self._failure
            raise
        finally:
            # In-flight calls still write their received usage and valid cache entries.
            executor.shutdown(wait=True, cancel_futures=True)
        return [results[index] for index in range(len(requests))]

    def run(self, chapter: Chapter, source_version: str, *, approved: bool = False,
            sample: bool = False, before=None, after=None) -> dict:
        if not approved:
            raise RuntimeError("실제 번역 실행에는 명시적 승인이 필요합니다 (--approve-api 또는 --approve-plan).")
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        status = "failed"
        translations = {style: {} for style in self.settings.styles}
        issues = {style: [] for style in self.settings.styles}
        completed, previous_hits = 0, self.cache_hits
        self._stop.clear()
        self._failure = None
        try:
            batches = len(make_batches(chapter.items, self.settings.max_chars, self.settings.max_items))
            total = batches * len(self.settings.styles) * (2 if self.settings.review else 1)
            requests = list(self._translation_requests(chapter, source_version, before or [], after or []))
            def execute(index, item):
                style, batch, request = item
                batch_number = index // len(self.settings.styles) + 1
                stage = 'translation'
                def emit(phase, attempt=0, wait_seconds=None):
                    nonlocal completed
                    with self._state_lock:
                        if phase == 'completed':
                            completed += 1
                        self._progress(ProgressEvent(chapter.index, style, stage, phase, batch_number, batches,
                                                     completed, total, self.cache_hits - previous_hits, attempt, wait_seconds))
                try:
                    def validate_translation(result):
                        values = validate_result(batch, result)
                        return {p["id"]: translation_text(values[p["id"]], p) for p in batch}
                    translated = self._execute(request, validate_translation, emit)
                    emit('completed')
                    reviewed = []
                    if self.settings.review:
                        stage = 'review'
                        review_req = self._review_request(request, batch, translated, style)
                        reviewed = self._execute(review_req, lambda r: validate_review(batch, r), emit)
                        emit('completed')
                    return translated, reviewed
                except BaseException as exc:
                    with self._state_lock:
                        if self._failure is None and not isinstance(exc, CancelledError):
                            self._failure = exc
                        self._stop.set()
                    raise
            results = self._run_batches(requests, execute)
            # Restore source/style order regardless of the workers' completion order.
            for (style, _, _), (translated, reviewed) in zip(requests, results):
                translations[style].update(translated)
                issues[style].extend(reviewed)
            self._progress(ProgressEvent(chapter.index, None, 'saving', 'saving', batches, batches,
                                         completed, total, self.cache_hits - previous_hits))
            bundle = self._export(chapter, source_version, translations, issues, sample)
            status = "completed"
            return bundle
        except (KeyboardInterrupt, SystemExit):
            status = "interrupted"
            raise
        finally:
            self.timing = {"started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
                           "elapsed_seconds": round(time.monotonic() - started, 3), "status": status}
            try:
                self.repo.write("usage.json", {"run_id": self.run_id, "this_run": summarize(self.records),
                                               "output_directory_lifetime": summarize(self.repo.attempts()),
                                               "cache_hits_this_run": self.cache_hits, "timing": self.timing})
            except BaseException as exc:
                self.timing["status"] = "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed"
                raise

    def _export(self, chapter, source_version, translations, issues, sample):
        expected = [p["id"] for p in chapter.items]
        if len(expected) != len(set(expected)):
            raise ValueError("원문 paragraph_id 중복")
        for style, values in translations.items():
            if list(values) != expected:
                raise ValueError(f"{style}: 최종 문단 ID/순서 불일치")
        translations = {style: {p["id"]: translation_text(values[p["id"]], p) for p in chapter.items}
                        for style, values in translations.items()}
        def status(pid, style):
            if style not in translations:
                return "not_generated"
            if not self.settings.review:
                return "not_reviewed"
            return "flagged" if any(i["id"] == pid for i in issues[style]) else "passed"
        def row(p):
            return {**p, "paragraph_id": p["id"], "chapter_id": chapter.chapter_id,
                    "source": p["text"], "source_text": p["text"],
                    "translations": {s: translations.get(s, {}).get(p["id"]) for s in STYLE_PROMPTS},
                    "review_status": {s: status(p["id"], s) for s in STYLE_PROMPTS},
                    "human_review_required": True, "version": source_version, "model": self.settings.model}
        review_items = {p["id"]: (number, p) for number, p in enumerate(chapter.items)}
        def report_issue(issue, style):
            number, item = review_items[issue["id"]]
            return {**issue, "chapter_number": chapter.index, "paragraph_number": number,
                    "source_text": item["text"], "translation_text": translations[style][issue["id"]]}
        report = {style: {"issues": [report_issue(i, style) for i in issues[style]],
                          "ai_review_executed": self.settings.review,
                          "ai_review_passed": not issues[style] if self.settings.review else None,
                          "human_review_required": True} for style in translations}
        bundle = {"schema_version": VERSION, "provider": self.settings.provider, "chapter": chapter.index, "chapter_id": chapter.chapter_id,
                  "source_file": chapter.source_file, "original_title": chapter.title, "sample": sample,
                  "styles": STYLE_NAMES, "generated_styles": list(translations),
                  "version": source_version, "model": self.settings.model,
                  "review_model": self.settings.review_model or self.settings.model,
                  "title": row(chapter.items[0]), "paragraphs": [row(p) for p in chapter.paragraphs],
                  "source_warnings": chapter.warnings, "human_review_required": True}
        revision = signature({"bundle": bundle, "report": report})
        bundle["revision"] = revision
        review_html = render_review_html(bundle, report)
        files = {"aligned.json": bundle, "review_report.json": report,
                 "source.json": {"chapter_id": chapter.chapter_id, "version": source_version,
                                 "title": chapter.title, "source_file": chapter.source_file,
                                 "paragraphs": chapter.paragraphs, "ast": chapter.ast, "warnings": chapter.warnings}}
        markdown = {}
        for style, values in translations.items():
            markdown[f"{style}.md"] = render_markdown(values[chapter.items[0]["id"]], chapter.paragraphs, values)
        # Immutable generation + atomic pointer under the internal directory.
        for name, value in files.items():
            self.repo.write(f"generations/{revision}/{name}", value)
        for name, value in markdown.items():
            self.repo.write_text(f"generations/{revision}/{name}", value)
        self.repo.write_text(f"generations/{revision}/review_report.html", review_html)
        for name, value in files.items():
            self.repo.write(name, value)
        for name, value in markdown.items():
            self.repo.write_text(name, value)
        self.repo.write_text("review_report.html", review_html)
        self.repo.write("manifest.json", {"schema_version": VERSION, "revision": revision,
                                         "aligned": f"generations/{revision}/aligned.json",
                                         "review": f"generations/{revision}/review_report.json",
                                         "review_html": f"generations/{revision}/review_report.html",
                                         "markdown": {s: f"generations/{revision}/{s}.md" for s in translations}})
        return bundle


def reflow_saved_output(repository):
    """Republish layout from saved source/translation snapshots without inference."""
    from .domain import Chapter
    bundle, report = repository.read_review_data()
    source = repository.read_source_data()
    if source.get("chapter_id") != bundle.get("chapter_id") or source.get("version") != bundle.get("version"):
        raise ValueError("원문과 번역 스냅샷의 장 ID 또는 버전이 다릅니다.")
    paragraphs = source["paragraphs"]
    if [p["id"] for p in paragraphs] != [p["id"] for p in bundle["paragraphs"]]:
        raise ValueError("원문과 번역 스냅샷의 문단 ID가 다릅니다.")
    annotate_layout(paragraphs, source["ast"])
    chapter = Chapter(bundle["chapter"], source["title"], source["source_file"], paragraphs,
                      "prologue" if bundle["chapter"] == 0 else "chapter", bundle["chapter_id"],
                      source["ast"], source.get("warnings", []), bundle["title"]["source_text"])
    styles = tuple(bundle["generated_styles"])
    reviewed = {report[style]["ai_review_executed"] for style in styles}
    if len(reviewed) != 1:
        raise ValueError("문체별 검수 실행 상태가 다른 결과는 오프라인 재정렬할 수 없습니다.")
    settings = Settings(model=bundle["model"], provider=bundle.get("provider", "openai"),
                        review_model=bundle.get("review_model"), styles=styles, review=reviewed.pop())
    items = [bundle["title"], *bundle["paragraphs"]]
    translations = {style: {p["id"]: p["translations"][style] for p in items} for style in styles}
    issues = {style: [{key: issue[key] for key in ("id", "category", "description")}
                      for issue in report[style]["issues"]] for style in styles}
    for style in styles:
        validate_review(chapter.items, {"issues": issues[style]})
    repository.backup_publication()
    return TranslationPipeline(repository, settings)._export(chapter, bundle["version"], translations, issues, bundle["sample"])
