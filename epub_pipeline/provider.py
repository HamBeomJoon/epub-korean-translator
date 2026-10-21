"""Only this adapter knows the OpenAI SDK. Construction never sends a request."""
from __future__ import annotations

import json
import os
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Any

from .domain import APIResponse, RetryableError


def retry_after(headers: Any) -> float | None:
    if not headers:
        return None
    try:
        if headers.get("retry-after-ms"):
            return max(0.0, float(headers["retry-after-ms"]) / 1000)
        value = headers.get("retry-after")
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


class OpenAIProvider:
    def __init__(self, client: Any):
        self.client = client

    @classmethod
    def from_environment(cls):
        try:
            from dotenv import load_dotenv
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("pip install -r requirements.txt 를 실행하세요.") from exc
        load_dotenv()
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("환경변수 OPENAI_API_KEY가 없습니다. .env 또는 쉘 환경변수를 설정하세요.")
        # All retries are owned by the use case so every attempt is accounted for.
        return cls(OpenAI(timeout=180, max_retries=0))

    def complete(self, model, instructions, payload, schema, name) -> APIResponse:
        try:
            response = self.client.responses.create(
                model=model, instructions=instructions, input=json.dumps(payload, ensure_ascii=False),
                store=False,
                text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status in (408, 409, 429) or (isinstance(status, int) and status >= 500) or type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}:
                headers = getattr(getattr(exc, "response", None), "headers", None)
                raise RetryableError(type(exc).__name__, retry_after(headers)) from exc
            raise RuntimeError(f"OpenAI 요청 실패: {type(exc).__name__}") from exc
        usage_obj = getattr(response, "usage", None)
        usage = None
        if usage_obj is not None:
            usage = {"input_tokens": int(usage_obj.input_tokens),
                     "output_tokens": int(usage_obj.output_tokens),
                     "cached_input_tokens": int(getattr(getattr(usage_obj, "input_tokens_details", None), "cached_tokens", 0) or 0)}
        return APIResponse(getattr(response, "output_text", "") or "", usage,
                           getattr(response, "status", "unknown"), getattr(response, "id", None),
                           getattr(response, "model", None))
