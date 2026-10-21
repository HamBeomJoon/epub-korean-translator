"""Official SIWC public-client OAuth flow, invoked only by explicit CLI actions."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .chatgpt_store import CredentialStore

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
DISCOVERY = ISSUER + "/.well-known/openid-configuration"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
USAGE_URL = "https://chatgpt.com/settings/usage"
APP_NAME = "EPUB Korean Translator"
TOKEN_FIELDS = ("access_token", "refresh_token", "id_token", "expires_at", "earliest_refresh_at")
TERMINAL_REFRESH_ERRORS = {"invalid_grant", "invalid_refresh_token", "token_expired",
                           "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"}


def safe_code(value) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", value) else None


class OAuthError(RuntimeError):
    def __init__(self, status: int | None, code: str | None = None, request_id: str | None = None):
        self.status, self.code, self.request_id = status, safe_code(code), safe_code(request_id)
        super().__init__(f"ChatGPT 연결 요청 실패: HTTP {status or 'network'}, code={self.code or 'unknown'}, request_id={self.request_id or 'unknown'}")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OAuthHTTP:
    """No environment API keys, redirected credentials, or diagnostic response-body dumps."""
    def request(self, method: str, url: str, *, form: dict | None = None, bearer: str | None = None):
        origin = urlsplit(url)
        if origin.scheme != "https" or origin.netloc not in {"auth.openai.com", "api.openai.com"}:
            raise RuntimeError("허용되지 않은 ChatGPT 연결 주소입니다.")
        headers = {"Accept": "application/json"}
        data = None
        if form is not None:
            data = urlencode(form).encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with build_opener(NoRedirect()).open(request, timeout=30) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except HTTPError as exc:
            try:
                body = json.loads(exc.read(65536))
                error = body.get("error") if isinstance(body, dict) else None
                code = error.get("code") if isinstance(error, dict) else error
            except (ValueError, OSError):
                code = None
            status, request_id = exc.code, exc.headers.get("x-request-id")
            exc.close()
            raise OAuthError(status, code, request_id) from None
        except (URLError, TimeoutError, OSError):
            raise OAuthError(None) from None
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError("ChatGPT 연결 응답이 너무 큽니다.")
        if not raw and method == "POST":
            return {}
        try:
            value = json.loads(raw)
        except ValueError:
            raise RuntimeError("ChatGPT 연결 응답이 JSON이 아닙니다.") from None
        if not isinstance(value, dict):
            raise RuntimeError("ChatGPT 연결 응답 형식이 올바르지 않습니다.")
        return value


class IDTokenVerifier:
    def __init__(self, http: OAuthHTTP):
        self.http = http

    @staticmethod
    def dependencies():
        try:
            import jwt
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError:
            raise RuntimeError("로그인 검증에는 pip install -r requirements.txt 가 필요합니다.") from None
        return jwt

    def verify(self, token: str, client_id: str, metadata: dict, nonce: str | None = None) -> dict:
        jwt = self.dependencies()
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in {"RS256", "ES256"} or not isinstance(header.get("kid"), str):
                raise ValueError("Unsupported signing key")
            keys = jwt.PyJWKSet.from_dict(self.http.request("GET", metadata["jwks_uri"]))
            key = next(k for k in keys.keys if k.key_id == header["kid"] and k.algorithm_name == header["alg"])
            claims = jwt.decode(token, key.key, algorithms=[header["alg"]], audience=client_id,
                                issuer=ISSUER, leeway=5,
                                options={"require": ["iss", "aud", "sub", "exp", "iat"]})
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                raise ValueError("Missing subject")
            if claims.get("azp", client_id) != client_id:
                raise ValueError("Wrong authorized party")
            if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")).encode("utf-8"), nonce.encode("utf-8")):
                raise ValueError("Wrong nonce")
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, StopIteration):
            raise RuntimeError("ChatGPT ID token 서명·issuer·audience·만료·nonce 검증에 실패했습니다.") from None


def consume_callback(path: str, state: str, expected_client: str | None) -> tuple[str, str]:
    parsed = urlsplit(path)
    if parsed.path != "/auth/callback" or len(path) > 16384:
        raise ValueError("잘못된 로그인 callback 경로입니다.")
    try:
        params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
    except ValueError:
        raise ValueError("잘못된 로그인 callback입니다.") from None
    if any(len(values) != 1 for values in params.values()):
        raise ValueError("중복된 로그인 callback 매개변수입니다.")
    if not secrets.compare_digest(params.get("state", [""])[0].encode("utf-8"), state.encode("utf-8")):
        raise ValueError("로그인 state가 일치하지 않습니다.")
    if "error" in params:
        raise RuntimeError("ChatGPT 로그인 또는 사용량 사용 동의가 취소되었습니다.")
    code = params.get("code", [""])[0]
    client_id = params.get("client_id", [expected_client or ""])[0]
    if not code or not client_id or client_id == "dynamic_agent_client":
        raise ValueError("발급된 client_id 또는 로그인 code가 없습니다.")
    if expected_client and client_id != expected_client:
        raise ValueError("다른 계정 등록의 client_id가 반환되었습니다.")
    return code, client_id


def browser_authorize(make_url, state, expected_client, *, timeout=300, opener=webbrowser.open):
    """One pending attempt, loopback only. Never log callback or authorization URLs."""
    result = {}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self.send_error(400)
                return
            path = urlsplit(self.path).path
            if path == "/login":
                self.send_response(302)
                self.send_header("Location", authorization_url)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                return
            if path != "/auth/callback":
                self.send_error(404)
                return
            try:
                result["value"] = consume_callback(self.path, state, expected_client)
                status, message = 200, "Sign-in received. Return to the terminal for verification."
            except (ValueError, RuntimeError) as exc:
                result["error"] = exc
                status, message = 400, "Sign-in failed. Return to the terminal."
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(message.encode("utf-8"))

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 1
        redirect_uri = f"http://127.0.0.1:{server.server_port}/auth/callback"
        authorization_url = make_url(redirect_uri)
        landing = f"http://127.0.0.1:{server.server_port}/login"
        print("Continue with ChatGPT — 브라우저에서 로그인하고 이 프로그램의 사용량 사용을 승인하세요.")
        print(f"브라우저가 열리지 않으면 이 로컬 주소를 여세요: {landing}")
        opener(landing)
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.handle_request()
        if not result:
            raise RuntimeError("로그인 시간이 초과되었습니다. --chatgpt-login을 다시 실행하세요.")
        if "error" in result:
            raise result["error"]
        code, client_id = result["value"]
        return code, client_id, redirect_uri


class ChatGPTSession:
    def __init__(self, store: CredentialStore | None = None, profile: str = "default", *,
                 http: OAuthHTTP | None = None, verifier=None, authorize=browser_authorize, clock=time.time):
        CredentialStore.check_profile(profile)
        self.store, self.profile = store or CredentialStore(), profile
        self.http = http or OAuthHTTP()
        self.verifier = verifier or IDTokenVerifier(self.http)
        self.authorize, self.clock = authorize, clock
        self._metadata = None

    def metadata(self):
        if self._metadata is None:
            value = self.http.request("GET", DISCOVERY)
            if value.get("issuer") != ISSUER:
                raise RuntimeError("OpenAI OIDC issuer가 일치하지 않습니다.")
            for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "revocation_endpoint"):
                parsed = urlsplit(value.get(key, ""))
                if parsed.scheme != "https" or parsed.netloc != "auth.openai.com" or parsed.query or parsed.fragment:
                    raise RuntimeError("OpenAI OIDC endpoint가 올바르지 않습니다.")
            self._metadata = value
        return self._metadata

    def _tokens(self, response: dict, old: dict | None = None) -> dict:
        for key in ("access_token", "refresh_token"):
            if not isinstance(response.get(key), str) or not response[key]:
                raise RuntimeError("OAuth 응답에 access/refresh token이 없습니다. 다시 로그인하세요.")
        if str(response.get("token_type", "")).lower() != "bearer":
            raise RuntimeError("지원하지 않는 OAuth token_type입니다.")
        try:
            ttl = float(response["expires_in"])
            earliest = float(response.get("earliest_refresh_at") or 0)
            if ttl <= 0 or not math.isfinite(ttl) or not math.isfinite(earliest):
                raise ValueError()
        except (TypeError, ValueError, KeyError):
            raise RuntimeError("OAuth 만료 시간 정보가 올바르지 않습니다.") from None
        scope = response.get("scope", old.get("scope") if old else None)
        if not isinstance(scope, str):
            raise RuntimeError("OAuth 응답에 granted scope가 없습니다.")
        return {"access_token": response["access_token"], "refresh_token": response["refresh_token"],
                "expires_at": self.clock() + ttl, "earliest_refresh_at": earliest, "scope": scope}

    def login(self, *, enable_plan=False):
        if isinstance(self.verifier, IDTokenVerifier):
            self.verifier.dependencies()
        with self.store.lock():
            data = self.store.load()
            old = data["profiles"].get(self.profile)
            pending = data.setdefault("pending_registrations", {}).get(self.profile)
            expected_client = old["client_id"] if old else pending
            self.store.save(data)  # Persist this installation's host ID before registration.
            metadata = self.metadata()
            state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
            def make_url(redirect_uri):
                params = {"client_id": expected_client or "dynamic_agent_client",
                          "ext_agent_host_id": data["host_id"], "response_type": "code",
                          "redirect_uri": redirect_uri, "scope": SCOPES, "resource": RESOURCE,
                          "state": state, "nonce": nonce, "code_challenge_method": "S256",
                          "code_challenge": challenge}
                if old:
                    if old.get("id_token"):
                        params["id_token_hint"] = old["id_token"]
                elif not expected_client:
                    params["agent_name_hint"] = APP_NAME
                if enable_plan:
                    params["prompt"] = "consent"
                return metadata["authorization_endpoint"] + "?" + urlencode(params)
            code, client_id, redirect_uri = self.authorize(make_url, state, expected_client)
            # Unverified registration stays separate from active credentials after exchange failure.
            if not old:
                data["pending_registrations"][self.profile] = client_id
                self.store.save(data)
            response = self.http.request("POST", metadata["token_endpoint"], form={
                "grant_type": "authorization_code", "client_id": client_id, "code": code,
                "code_verifier": verifier, "redirect_uri": redirect_uri, "resource": RESOURCE})
            if not isinstance(response.get("id_token"), str):
                raise RuntimeError("OAuth 응답에 ID token이 없습니다.")
            identity = self.verifier.verify(response["id_token"], client_id, metadata, nonce)
            if old and identity["sub"] != old["sub"]:
                raise RuntimeError("기존 profile과 다른 계정입니다. 새 --chatgpt-profile 이름으로 로그인하세요.")
            saved = {"client_id": client_id, "sub": identity["sub"], "email": identity.get("email"),
                     "id_token": response["id_token"], **self._tokens(response)}
            data["profiles"][self.profile] = saved
            data["pending_registrations"].pop(self.profile, None)
            self.store.save(data)
        return self.status()

    @staticmethod
    def _clear_tokens(profile):
        for key in TOKEN_FIELDS:
            profile.pop(key, None)
        profile["scope"] = ""

    def access_token(self) -> str:
        with self.store.lock():
            data = self.store.load()
            saved = data["profiles"].get(self.profile)
            if not saved or not saved.get("access_token"):
                raise RuntimeError("먼저 --chatgpt-login을 실행하세요.")
            if PLAN_SCOPE not in saved.get("scope", "").split():
                raise RuntimeError("ChatGPT 사용량 사용 권한이 없습니다. --chatgpt-login --chatgpt-enable-plan으로 동의하세요.")
            now = self.clock()
            if saved["expires_at"] > now + 60:
                return saved["access_token"]
            if saved.get("earliest_refresh_at", 0) > now:
                if saved["expires_at"] > now:
                    return saved["access_token"]
                raise RuntimeError("서버가 허용한 토큰 갱신 시간 이후 다시 실행하세요.")
            try:
                response = self.http.request("POST", self.metadata()["token_endpoint"], form={
                    "grant_type": "refresh_token", "client_id": saved["client_id"],
                    "refresh_token": saved["refresh_token"], "resource": RESOURCE})
            except OAuthError as exc:
                if exc.code in TERMINAL_REFRESH_ERRORS:
                    self._clear_tokens(saved)
                    self.store.save(data)
                    raise RuntimeError("ChatGPT 로그인이 만료 또는 해제되었습니다. --chatgpt-login을 다시 실행하세요.") from None
                raise
            updated = self._tokens(response, saved)
            if response.get("id_token"):
                identity = self.verifier.verify(response["id_token"], saved["client_id"], self.metadata())
                if identity["sub"] != saved["sub"]:
                    self._clear_tokens(saved)
                    self.store.save(data)
                    raise RuntimeError("갱신된 로그인 계정이 다릅니다. 다시 로그인하세요.")
                updated["id_token"] = response["id_token"]
            saved.update(updated)
            self.store.save(data)  # Atomically save rotated refresh token before releasing the lock.
            if PLAN_SCOPE not in saved["scope"].split():
                raise RuntimeError("ChatGPT 사용량 사용 권한이 해제되었습니다. 다시 동의하세요.")
            return saved["access_token"]

    def models(self) -> list[dict]:
        value = self.http.request("GET", RESOURCE + "/models", bearer=self.access_token())
        if not isinstance(value.get("models"), list):
            raise RuntimeError("ChatGPT 모델 목록 형식이 올바르지 않습니다.")
        return [{"slug": p["slug"], "display_name": p.get("display_name", p["slug"])}
                for p in value["models"] if isinstance(p, dict) and p.get("visibility") == "list"
                and isinstance(p.get("slug"), str) and p["slug"]]

    def status(self):
        data = self.store.load()
        return {"selected_profile": self.profile, "profiles": [
            {"profile": name, "email": saved.get("email"), "connected": bool(saved.get("access_token")),
             "plan_usage_enabled": bool(saved.get("access_token")) and PLAN_SCOPE in saved.get("scope", "").split(),
             "expires_at": saved.get("expires_at")}
            for name, saved in sorted(data["profiles"].items())]}

    def logout(self):
        confirmed = True
        with self.store.lock():
            data = self.store.load()
            saved = data["profiles"].get(self.profile)
            if saved:
                if saved.get("refresh_token"):
                    try:
                        self.http.request("POST", self.metadata()["revocation_endpoint"], form={
                            "token": saved["refresh_token"], "token_type_hint": "refresh_token",
                            "client_id": saved["client_id"]})
                    except (OAuthError, RuntimeError):
                        confirmed = False
                self._clear_tokens(saved)
                self.store.save(data)
        return {"profile": self.profile, "local_credentials_cleared": True,
                "remote_revocation_confirmed": confirmed, "manage_usage": USAGE_URL}
