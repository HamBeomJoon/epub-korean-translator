"""Domain models and validation; no SDK or filesystem dependencies."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

VERSION = "2.0"
PARSER_VERSION = "3"


@dataclass
class Chapter:
    index: int
    title: str
    source_file: str
    paragraphs: list[dict[str, Any]]
    kind: str = "chapter"
    chapter_id: str = ""
    ast: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    title_source: str = ""

    @property
    def prefix(self) -> str:
        return "prologue" if self.kind == "prologue" else f"ch{self.index:02d}"

    @property
    def items(self) -> list[dict[str, Any]]:
        return [{"id": f"{self.chapter_id or self.prefix}-title", "text": self.title_source or self.title, "kind": "title"}] + self.paragraphs


@dataclass(frozen=True)
class APIResponse:
    text: str
    usage: dict[str, int] | None
    status: str = "completed"
    response_id: str | None = None
    model: str | None = None
    error_code: str | None = None
    error_param: str | None = None
    request_id: str | None = None
    retryable: bool = False
    error_message: str | None = None


class RequestError(RuntimeError):
    def __init__(self, message: str, *, error_code=None, error_param=None, request_id=None, status_code=None):
        super().__init__(message)
        self.error_code, self.error_param = error_code, error_param
        self.request_id, self.status_code = request_id, status_code


class RetryableError(RequestError):
    def __init__(self, message: str, retry_after: float | None = None, **diagnostics):
        super().__init__(message, **diagnostics)
        self.retry_after = retry_after


def signature(content: Any) -> str:
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_batches(items: list[dict], max_chars: int, max_items: int) -> list[list[dict]]:
    if max_chars <= 0 or max_items <= 0:
        raise ValueError("max-chars/max-items는 양수여야 합니다.")
    batches, batch, length = [], [], 0
    for item in items:
        size = len(item["text"])
        if batch and (length + size > max_chars or len(batch) >= max_items):
            batches.append(batch)
            batch, length = [], 0
        batch.append(item)
        length += size
    if batch:
        batches.append(batch)
    return batches


def validate_translations(source: list[dict], translations: Any) -> dict[str, str]:
    if not isinstance(translations, list) or any(
        not isinstance(p, dict) or set(p) != {"id", "text"} for p in translations
    ):
        raise ValueError("translations는 id/text 객체 배열이어야 합니다.")
    expected, received = [p["id"] for p in source], [p["id"] for p in translations]
    if len(expected) != len(set(expected)) or expected != received:
        raise ValueError(f"응답 ID/순서 불일치: expected={expected}, received={received}")
    if any(not isinstance(p["text"], str) or not p["text"].strip() for p in translations):
        raise ValueError("비어 있는 번역문이 존재합니다.")
    return {p["id"]: p["text"].strip() for p in translations}


CATEGORIES = ("omission", "addition", "distortion", "terminology", "style")


def validate_result(source: list[dict], result: Any) -> dict[str, str]:
    if not isinstance(result, dict) or set(result) != {"translations"}:
        raise ValueError("번역 응답 스키마 불일치")
    return validate_translations(source, result["translations"])


def validate_review(source: list[dict], result: Any) -> list[dict]:
    if not isinstance(result, dict) or set(result) != {"issues"} or not isinstance(result["issues"], list):
        raise ValueError("검수 응답 스키마 불일치")
    valid_ids = {p["id"] for p in source}
    for item in result["issues"]:
        if (not isinstance(item, dict) or set(item) != {"id", "category", "description"}
                or not isinstance(item["id"], str) or item["id"] not in valid_ids
                or item["category"] not in CATEGORIES
                or not isinstance(item["description"], str) or not item["description"].strip()):
            raise ValueError(f"잘못된 검수 항목: {item!r}")
    return result["issues"]
