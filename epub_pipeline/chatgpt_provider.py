"""ChatGPT plan adapter: account catalog, OAuth bearer, public streaming Responses."""
from __future__ import annotations

import json
import threading
from .chatgpt_auth import ChatGPTSession, RESOURCE, USAGE_URL, safe_code
from .domain import APIResponse, RetryableError, RequestError
from .provider import retry_after

TEMPORARY_CODES = {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable", "server_error", "rate_limit_exceeded"}


def plan_error_message(code: str | None) -> str:
    if code == "subscription_sharing_usage_limit_exceeded":
        return f"ChatGPT 사용량 한도에 도달했습니다. 새 요청을 중단합니다. {USAGE_URL}"
    if code == "subscription_sharing_user_not_eligible":
        return "이 계정 또는 workspace에서 ChatGPT plan 사용이 허용되지 않습니다."
    if code == "subscription_sharing_unsupported_capability":
        return "선택한 ChatGPT 모델이 요청 기능을 지원하지 않습니다. 모델 또는 요청 설정을 확인하세요."
    return "ChatGPT 요청이 거부되었습니다. 로그인 권한·모델·요청 설정을 확인하세요."


def response_value(response, text="", error_code=None) -> APIResponse:
    usage_obj = getattr(response, "usage", None)
    usage = None
    if usage_obj is not None:
        usage = {"input_tokens": int(usage_obj.input_tokens), "output_tokens": int(usage_obj.output_tokens),
                 "cached_input_tokens": int(getattr(getattr(usage_obj, "input_tokens_details", None), "cached_tokens", 0) or 0)}
    error = getattr(response, "error", None)
    code = safe_code(getattr(error, "code", None)) or safe_code(error_code)
    return APIResponse(getattr(response, "output_text", "") or text, usage,
                       getattr(response, "status", "interrupted"), getattr(response, "id", None),
                       getattr(response, "model", None), error_code=code,
                       error_param=safe_code(getattr(error, "param", None)),
                       request_id=safe_code(getattr(response, "_request_id", None)),
                       retryable=code in TEMPORARY_CODES,
                       error_message=plan_error_message(code) if code else None)



class ChatGPTProvider:
    def __init__(self, session: ChatGPTSession, client_factory=None):
        self.session = session
        self.client_factory = client_factory
        self._model_slugs = None
        self._session_lock = threading.Lock()

    @classmethod
    def from_saved_session(cls, directory=None, profile="default"):
        from .chatgpt_store import CredentialStore
        return cls(ChatGPTSession(CredentialStore(directory), profile))

    def complete(self, model, instructions, payload, schema, name) -> APIResponse:
        with self._session_lock:
            if self._model_slugs is None:
                self._model_slugs = {p["slug"] for p in self.session.models()}
            if model not in self._model_slugs:
                raise RuntimeError("선택한 모델이 현재 ChatGPT 계정 목록에 없습니다. --list-models로 slug를 확인하세요.")
            token = self.session.access_token()
        factory = self.client_factory
        if factory is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError("pip install -r requirements.txt 를 실행하세요.") from None
            factory = OpenAI
        # Explicit bearer + base URL prevents environment API keys/endpoints becoming a billing fallback.
        client = factory(api_key=token, base_url=RESOURCE, organization="", project="", max_retries=0, timeout=180)
        terminal = None
        chunks = []
        try:
            with client.responses.create(
                model=model, instructions=instructions,
                input=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                store=False, stream=True,
                text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
            ) as stream:
                for event in stream:
                    if event.type == "response.output_text.delta":
                        chunks.append(event.delta)
                    elif event.type in {"response.completed", "response.failed", "response.incomplete"}:
                        terminal = response_value(event.response, "".join(chunks))
                    elif event.type == "error":
                        code = safe_code(getattr(event, "code", None)) or "unknown_error"
                        terminal = APIResponse("".join(chunks), None, "failed", error_code=code,
                                               retryable=code in TEMPORARY_CODES, error_message=plan_error_message(code))
            return terminal or APIResponse("".join(chunks), None, "interrupted")
        except Exception as exc:
            if terminal is not None:
                return terminal
            status = getattr(exc, "status_code", None)
            body = getattr(exc, "body", None)
            error = body.get("error", body) if isinstance(body, dict) else {}
            code = safe_code(getattr(exc, "code", None)) or (safe_code(error.get("code")) if isinstance(error, dict) else None)
            diagnostics = {"error_code": code, "error_param": safe_code(error.get("param")) if isinstance(error, dict) else None,
                           "request_id": safe_code(getattr(exc, "request_id", None)), "status_code": status}
            if code and code.startswith(("subscription_sharing_", "chatpass_")) and code not in TEMPORARY_CODES:
                raise RequestError(f"{plan_error_message(code)} code={code}, HTTP={status}", **diagnostics) from None
            if code in TEMPORARY_CODES or status in (408, 409, 429) or (isinstance(status, int) and status >= 500) or type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}:
                headers = getattr(getattr(exc, "response", None), "headers", None)
                raise RetryableError(f"ChatGPT 일시 오류: HTTP={status}, code={code or 'unknown'}", retry_after(headers), **diagnostics) from None
            raise RequestError(f"{plan_error_message(code)} HTTP={status or 'unknown'}", **diagnostics) from None
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
