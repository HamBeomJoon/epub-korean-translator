"""OAuth, stream and CLI tests. All remote sockets and DNS are prohibited."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import socket
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from epub_pipeline.chatgpt_auth import (ChatGPTSession, OAuthHTTP, OAuthError, IDTokenVerifier,
                                        ISSUER, RESOURCE, PLAN_SCOPE, consume_callback, browser_authorize)
from epub_pipeline.chatgpt_provider import ChatGPTProvider
from epub_pipeline.chatgpt_store import CredentialStore
from epub_pipeline.cli import build_parser, run
from epub_pipeline.domain import APIResponse, Chapter, RetryableError
from epub_pipeline.pipeline import Settings, TranslationPipeline
from epub_pipeline.pricing import RateCard
from epub_pipeline.repository import FileRepository

METADATA = {'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/api/accounts/authorize',
            'token_endpoint': ISSUER + '/api/accounts/oauth/token', 'jwks_uri': ISSUER + '/.well-known/jwks.json',
            'revocation_endpoint': ISSUER + '/oauth/revoke'}


def token_response(**changes):
    return {'access_token': 'access-SECRET', 'refresh_token': 'refresh-SECRET', 'id_token': 'identity-SECRET',
            'token_type': 'Bearer', 'expires_in': 3600,
            'scope': 'openid profile email offline_access resource.invoke ' + PLAN_SCOPE, **changes}


class FakeHTTP:
    def __init__(self):
        self.calls = []
        self.responses = []
        self.catalog = [{'slug': 'chosen-model', 'display_name': 'Chosen model', 'visibility': 'list'},
                        {'slug': 'hidden-model', 'visibility': 'hide'}]

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith('openid-configuration'):
            return dict(METADATA)
        if url.endswith('/models'):
            return {'models': self.catalog}
        if url.endswith('/revoke'):
            if self.responses:
                result = self.responses.pop(0)
                if isinstance(result, BaseException):
                    raise result
            return {}
        result = self.responses.pop(0) if self.responses else token_response()
        if isinstance(result, BaseException):
            raise result
        return result


class FakeVerifier:
    def __init__(self):
        self.subject = 'account-1'
        self.calls = []

    def verify(self, *args):
        self.calls.append(args)
        return {'sub': self.subject, 'email': 'person@example.test'}


class NetworkBlocked(unittest.TestCase):
    def setUp(self):
        for target in ('socket.create_connection', 'socket.socket.connect', 'socket.socket.connect_ex', 'socket.getaddrinfo'):
            p = patch(target, side_effect=AssertionError('Remote network is prohibited'))
            p.start()
            self.addCleanup(p.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CredentialStore(self.root / 'auth')
        self.http, self.verifier = FakeHTTP(), FakeVerifier()
        self.authorizations = []
        def authorize(make_url, state, expected_client):
            uri = 'http://127.0.0.1:54321/auth/callback'
            params = parse_qs(urlsplit(make_url(uri)).query)
            self.authorizations.append(params)
            self.assertEqual(params['state'], [state])
            return 'authorization-SECRET', expected_client or 'oaiapp_issued', uri
        self.session = ChatGPTSession(self.store, http=self.http, verifier=self.verifier,
                                      authorize=authorize, clock=lambda: 1000)

    def login(self):
        return self.session.login()


class AuthTest(NetworkBlocked):
    def test_신규_등록에서_PKCE와_검증된_비공개_저장을_사용한다(self):
        self.login()
        auth = self.authorizations[0]
        self.assertEqual(auth['client_id'], ['dynamic_agent_client'])
        self.assertEqual(auth['agent_name_hint'], ['EPUB Korean Translator'])
        self.assertEqual(auth['resource'], [RESOURCE])
        self.assertIn(PLAN_SCOPE, auth['scope'][0])
        request = next(kw['form'] for _, url, kw in self.http.calls if url.endswith('/oauth/token'))
        self.assertEqual(request['client_id'], 'oaiapp_issued')
        self.assertEqual(request['redirect_uri'], auth['redirect_uri'][0])
        expected = base64.urlsafe_b64encode(hashlib.sha256(request['code_verifier'].encode()).digest()).decode().rstrip('=')
        self.assertEqual(auth['code_challenge'], [expected])
        self.assertNotIn('client_secret', request)
        self.assertEqual(self.verifier.calls[0][3], auth['nonce'][0])
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.session.access_token(), 'access-SECRET')

    def test_재로그인하면_발급된_클라이언트와_고정된_호스트를_재사용한다(self):
        self.login()
        self.login()
        first, second = self.authorizations
        self.assertEqual(second['client_id'], ['oaiapp_issued'])
        self.assertNotIn('agent_name_hint', second)
        self.assertEqual(second['ext_agent_host_id'], first['ext_agent_host_id'])
        self.assertEqual(second['id_token_hint'], ['identity-SECRET'])
        self.assertNotEqual(second['state'], first['state'])
        self.assertNotEqual(second['nonce'], first['nonce'])
        self.assertNotEqual(second['code_challenge'], first['code_challenge'])

    def test_콜백의_state_오류와_중복_필드와_클라이언트_변경을_거부한다(self):
        path = '/auth/callback?' + urlencode({'state': 'expected', 'code': 'code', 'client_id': 'issued'})
        self.assertEqual(consume_callback(path, 'expected', None), ('code', 'issued'))
        self.assertEqual(consume_callback('/auth/callback?state=expected&code=code', 'expected', 'issued'), ('code', 'issued'))
        for bad in (path.replace('expected', 'wrong'), path + '&state=expected',
                    path.replace('/auth/callback', '/callback'), path.replace('issued', 'dynamic_agent_client'),
                    path.replace('state=expected', 'state=%ED%95%9C%EA%B8%80')):
            with self.assertRaises(ValueError):
                consume_callback(bad, 'expected', None)
        with self.assertRaises(ValueError):
            consume_callback(path, 'expected', 'other')
        with self.assertRaises(RuntimeError):
            consume_callback('/auth/callback?state=expected&error=access_denied', 'expected', None)

    def test_토큰_교환에_실패하면_인증_정보_없이_대기_등록을_유지한다(self):
        self.http.responses = [OAuthError(400, 'invalid_grant')]
        with self.assertRaises(OAuthError):
            self.login()
        saved = self.store.load()
        self.assertEqual(saved['profiles'], {})
        self.assertEqual(saved['pending_registrations']['default'], 'oaiapp_issued')
        self.login()
        self.assertEqual(self.authorizations[-1]['client_id'], ['oaiapp_issued'])
        self.assertNotIn('agent_name_hint', self.authorizations[-1])
        self.assertEqual(self.store.load()['pending_registrations'], {})

    def test_사용량_권한이_없으면_로그인은_유지하고_모델_조회와_추론을_차단한다(self):
        self.http.responses = [token_response(scope='openid profile email')]
        result = self.login()
        self.assertFalse(result['profiles'][0]['plan_usage_enabled'])
        with self.assertRaisesRegex(RuntimeError, '사용 권한'):
            self.session.models()
        self.assertFalse(any(url.endswith('/models') for _, url, _ in self.http.calls))
        self.session.login(enable_plan=True)
        self.assertEqual(self.authorizations[-1]['prompt'], ['consent'])

    def test_다른_계정은_기존_프로필을_덮어쓸_수_없다(self):
        self.login()
        original = self.store.path.read_bytes()
        self.verifier.subject = 'account-2'
        with self.assertRaisesRegex(RuntimeError, '다른 계정'):
            self.login()
        self.assertEqual(self.store.path.read_bytes(), original)
        another = ChatGPTSession(self.store, 'second', http=self.http, verifier=self.verifier,
                                  authorize=self.session.authorize, clock=lambda: 1000)
        another.login()
        self.assertEqual(len(self.session.status()['profiles']), 2)

    def test_토큰_갱신은_인증_정보를_한_번_교체하고_scope를_보내지_않는다(self):
        self.login()
        self.session.clock = lambda: 4550
        self.http.responses = [token_response(access_token='access-NEW', refresh_token='refresh-NEW')]
        self.assertEqual(self.session.access_token(), 'access-NEW')
        self.assertEqual(self.session.access_token(), 'access-NEW')
        refreshes = [kw['form'] for _, _, kw in self.http.calls if kw.get('form', {}).get('grant_type') == 'refresh_token']
        self.assertEqual(len(refreshes), 1)
        self.assertEqual(refreshes[0]['client_id'], 'oaiapp_issued')
        self.assertNotIn('scope', refreshes[0])
        self.assertEqual(self.store.load()['profiles']['default']['refresh_token'], 'refresh-NEW')

    def test_복구할_수_없는_갱신_오류면_토큰을_지우고_계정과_클라이언트는_유지한다(self):
        self.login()
        self.session.clock = lambda: 5000
        self.http.responses = [OAuthError(400, 'refresh_token_reused')]
        with self.assertRaisesRegex(RuntimeError, '로그인이 만료'):
            self.session.access_token()
        saved = self.store.load()['profiles']['default']
        self.assertNotIn('access_token', saved)
        self.assertNotIn('refresh_token', saved)
        self.assertEqual(saved['client_id'], 'oaiapp_issued')
        self.assertEqual(saved['sub'], 'account-1')

    def test_일시적인_토큰_갱신_오류는_기존_인증_정보를_보존한다(self):
        self.login()
        original = self.store.path.read_bytes()
        self.session.clock = lambda: 5000
        self.http.responses = [OAuthError(503)]
        with self.assertRaises(OAuthError):
            self.session.access_token()
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_로그아웃은_갱신_토큰을_폐기하며_네트워크_실패에도_로컬_토큰을_지운다(self):
        self.login()
        self.http.responses = [OAuthError(None)]
        result = self.session.logout()
        self.assertFalse(result['remote_revocation_confirmed'])
        saved = self.store.load()['profiles']['default']
        self.assertNotIn('id_token', saved)
        self.assertNotIn('access_token', saved)
        self.assertEqual(saved['client_id'], 'oaiapp_issued')
        self.login()
        self.assertTrue(self.session.logout()['remote_revocation_confirmed'])

    def test_상태와_모델_목록에_토큰을_노출하지_않는다(self):
        self.login()
        self.assertEqual(self.session.models(), [{'slug': 'chosen-model', 'display_name': 'Chosen model'}])
        raw = json.dumps(self.session.status())
        for secret in ('access-SECRET', 'refresh-SECRET', 'identity-SECRET', 'authorization-SECRET'):
            self.assertNotIn(secret, raw)

    def test_인증_저장은_안전하지_않은_권한과_심볼릭_링크를_거부한다(self):
        self.login()
        self.store.path.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, '0600'):
            self.store.load()
        self.store.path.chmod(0o600)
        linked = self.root / 'linked'
        linked.symlink_to(self.store.directory, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, '심볼릭'):
            CredentialStore(linked).load()

    def test_토큰_폐기의_빈_응답을_허용하고_오류에서_비밀정보를_제거한다(self):
        response = NS(read=lambda _: b'')
        class Context:
            def __enter__(self):
                return response
            def __exit__(self, *args):
                pass
        opener = NS(open=lambda *a, **kw: Context())
        with patch('epub_pipeline.chatgpt_auth.build_opener', return_value=opener):
            self.assertEqual(OAuthHTTP().request('POST', METADATA['revocation_endpoint'], form={'token': 'private'}), {})
        from urllib.error import HTTPError
        err = HTTPError(METADATA['token_endpoint'], 400, 'bad', {}, io.BytesIO(b'{"error":"invalid_grant","description":"refresh-SECRET"}'))
        with patch('epub_pipeline.chatgpt_auth.build_opener', return_value=NS(open=unittest.mock.Mock(side_effect=err))):
            with self.assertRaises(OAuthError) as cm:
                OAuthHTTP().request('POST', METADATA['token_endpoint'], form={'refresh_token': 'refresh-SECRET'})
        self.assertNotIn('refresh-SECRET', str(cm.exception))
        self.assertEqual(cm.exception.code, 'invalid_grant')

    def test_악성_인증_탐색_주소와_HTTP_리다이렉트_목적지를_거부한다(self):
        self.http.request = lambda *a, **kw: {**METADATA, 'token_endpoint': 'https://attacker.invalid/steal'}
        with self.assertRaisesRegex(RuntimeError, 'endpoint'):
            self.session.metadata()
        with self.assertRaises(RuntimeError):
            OAuthHTTP().request('POST', 'http://api.openai.com/v1/responses', bearer='private')

    def test_브라우저_콜백은_루프백을_사용하고_URL_로그를_남기지_않는다(self):
        paths = ['/login', '/auth/callback?state=expected&code=secret&client_id=issued']
        packets = []
        class FakeSocket:
            def __init__(self, path):
                self.path = path
            def makefile(self, mode, *args):
                return io.BytesIO(f'GET {self.path} HTTP/1.0\r\nHost: 127.0.0.1:43210\r\n\r\n'.encode())
            def sendall(self, packet):
                packets.append(packet)
        class Server:
            server_port = 43210
            def __init__(self, address, handler):
                self.address, self.handler = address, handler
            def __enter__(self):
                self_test.assertEqual(self.address, ('127.0.0.1', 0))
                return self
            def __exit__(self, *args):
                pass
            def handle_request(self):
                self.handler(FakeSocket(paths.pop(0)), ('127.0.0.1', 1), self)
        self_test = self
        with patch('epub_pipeline.chatgpt_auth.HTTPServer', Server), patch('builtins.print') as output:
            result = browser_authorize(lambda uri: 'https://auth.openai.com/authorize?id_token_hint=identity-SECRET',
                                        'expected', None, opener=lambda _: None)
        self.assertEqual(result, ('secret', 'issued', 'http://127.0.0.1:43210/auth/callback'))
        self.assertNotIn('identity-SECRET', str(output.call_args_list))
        self.assertIn(b'Cache-Control: no-store', b''.join(packets))


class FakeStream:
    def __init__(self, events):
        self.events = events
    def __enter__(self):
        return iter(self.events)
    def __exit__(self, *args):
        pass


class ProviderTest(NetworkBlocked):
    def setUp(self):
        super().setUp()
        self.login()
        self.requests, self.factories = [], []
        self.events = []
        def factory(**kwargs):
            self.factories.append(kwargs)
            def create(**body):
                self.requests.append(body)
                return FakeStream(self.events)
            return NS(responses=NS(create=create), close=lambda: None)
        self.provider = ChatGPTProvider(self.session, factory)

    def completed(self, status='completed', code=None):
        return NS(type='response.' + status, response=NS(status=status, id='response-1', model='chosen-snapshot',
                  output_text='{"translations":[]}', error=NS(code=code, param='model') if code else None,
                  usage=NS(input_tokens=100, output_tokens=20, input_tokens_details=NS(cached_tokens=10))))

    def call(self):
        return self.provider.complete('chosen-model', 'instructions', {'paragraphs': []}, {'type': 'object'}, 'schema')

    def test_공개_스트리밍_요청에_OAuth를_명시하고_API_키로_전환하지_않는다(self):
        self.events = [NS(type='response.output_text.delta', delta='partial'), self.completed()]
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'paid-api-SECRET', 'OPENAI_BASE_URL': 'https://attacker.invalid'}):
            response = self.call()
        self.assertEqual(response.status, 'completed')
        self.assertEqual(response.usage['input_tokens'], 100)
        self.assertEqual(self.factories[0]['api_key'], 'access-SECRET')
        self.assertEqual(self.factories[0]['base_url'], RESOURCE)
        self.assertEqual(self.factories[0]['max_retries'], 0)
        body = self.requests[0]
        self.assertTrue(body['stream'])
        self.assertFalse(body['store'])
        self.assertIsInstance(body['input'], list)
        self.assertEqual(body['text']['format']['type'], 'json_schema')
        self.assertNotIn('access-SECRET', json.dumps(body))

    def test_목록에_없는_모델은_추론_전에_거부한다(self):
        with self.assertRaisesRegex(RuntimeError, '모델이 현재'):
            self.provider.complete('hidden-model', '', {}, {}, 'schema')
        self.assertEqual(self.requests, [])

    def test_중단된_스트림을_완료된_응답으로_처리하지_않는다(self):
        self.events = [NS(type='response.output_text.delta', delta='{"translations":[]}')]
        self.assertEqual(self.call().status, 'interrupted')

    def test_스트림_사용량_한도_오류는_재시도_없이_중단하고_사용량을_기록한다(self):
        self.events = [self.completed('failed', 'subscription_sharing_usage_limit_exceeded')]
        repo = FileRepository(self.root / 'output')
        pipeline = TranslationPipeline(repo, Settings(model='chosen-model', provider='chatgpt', styles=('faithful',)),
                                       self.provider, sleep=lambda _: self.fail('Usage limit must not retry'))
        with self.assertRaisesRegex(RuntimeError, '사용량 한도'):
            pipeline.run(Chapter(1, 'T', 'c', [{'id': 'p1', 'text': 'Original'}]), 'v1', approved=True)
        records = repo.attempts()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(records[0]['usage']['input_tokens'], 100)
        self.assertEqual(records[0]['error_code'], 'subscription_sharing_usage_limit_exceeded')
        self.assertEqual(records[0]['cost']['billing_mode'], 'chatgpt_plan')
        self.assertIsNone(records[0]['cost']['amount'])
        self.assertNotIn('SECRET', json.dumps(records))
        self.assertFalse((repo.artifact_path('manifest.json')).exists())

    def test_일시적인_구독_오류는_받은_사용량을_보존하고_재시도한다(self):
        self.events = [self.completed('failed', 'subscription_sharing_usage_unavailable')]
        response = self.call()
        self.assertTrue(response.retryable)
        self.assertEqual(response.usage['output_tokens'], 20)

    def test_구독_사용량_한도는_중단하고_일반_요청_제한은_재시도한다(self):
        class APIProblem(Exception):
            status_code = 429
            request_id = 'req-known'
            response = NS(headers={'retry-after': '2'})
            def __init__(self, code):
                self.code = code
                self.body = {'error': {'code': code, 'param': 'model', 'message': 'access-SECRET'}}
        def factory(**kwargs):
            return NS(responses=NS(create=unittest.mock.Mock(side_effect=APIProblem(code))), close=lambda: None)
        self.provider.client_factory = factory
        for code, expected in [('subscription_sharing_usage_limit_exceeded', RuntimeError), ('rate_limit_exceeded', RetryableError)]:
            with self.assertRaises(expected) as cm:
                self.call()
            self.assertNotIn('access-SECRET', str(cm.exception))
            self.assertEqual(cm.exception.request_id, 'req-known')
            if isinstance(cm.exception, RetryableError):
                self.assertEqual(cm.exception.retry_after, 2)

    def test_공급자별로_서로_다른_캐시_키를_사용한다(self):
        repo = FileRepository(self.root / 'output')
        a = TranslationPipeline(repo, Settings(provider='openai'))
        b = TranslationPipeline(repo, Settings(provider='chatgpt'))
        self.assertNotEqual(a._key({'model': 'same'}), b._key({'model': 'same'}))


class CLITest(NetworkBlocked):
    def test_연결_상태_확인은_네트워크를_사용하거나_비밀정보를_출력하지_않는다(self):
        self.login()
        args = build_parser().parse_args(['--chatgpt-status', '--chatgpt-auth-dir', str(self.store.directory)])
        with patch('epub_pipeline.chatgpt_auth.OAuthHTTP.request', side_effect=AssertionError('No network')), patch('builtins.print') as output:
            run(args)
        self.assertNotIn('SECRET', str(output.call_args_list))

    def test_입력이_없거나_과금_승인_방식이_섞이면_거부한다(self):
        for values in ([], ['--input', 'x', '--approve-api'], ['--input', 'x', '--provider', 'openai', '--approve-plan'],
                       ['--chatgpt-login', '--dry-run'], ['--input', 'x', '--approve-plan'], ['--chatgpt-enable-plan']):
            with self.assertRaises(ValueError):
                run(build_parser().parse_args(values))

    def test_ChatGPT_dry_run은_승인해도_세션을_읽거나_갱신하지_않는다(self):
        chapter = Chapter(1, 'T', 'c', [{'id': 'p', 'text': 'Original'}])
        epub = self.root / 'book.epub'
        epub.write_bytes(b'offline fixture')
        args = build_parser().parse_args(['--input', str(epub), '--dry-run', '--approve-plan', '--output', str(self.root / 'output')])
        with patch('epub_pipeline.cli.read_epub', return_value=[chapter]), patch('epub_pipeline.chatgpt_auth.OAuthHTTP.request', side_effect=AssertionError('No network')), patch('builtins.print'):
            run(args)
        self.assertFalse(self.store.path.exists())


@unittest.skipUnless(importlib.util.find_spec('jwt') and importlib.util.find_spec('cryptography'), 'PyJWT[crypto] 설치 필요')
class CryptographicIdentityTest(NetworkBlocked):
    def test_실제_RSA_서명과_발급자와_대상과_만료와_nonce와_변조를_검증한다(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        jwk.update(kid='key1', alg='RS256', use='sig')
        http = NS(request=lambda *a, **kw: {'keys': [jwk]})
        verifier = IDTokenVerifier(http)
        claims = {'sub': 'account', 'iss': ISSUER, 'aud': 'issued', 'iat': int(time.time()),
                  'exp': int(time.time()) + 600, 'nonce': 'expected'}
        def signed(value, signing_key=key, algorithm='RS256'):
            return jwt.encode(value, signing_key, algorithm=algorithm, headers={'kid': 'key1'})
        self.assertEqual(verifier.verify(signed(claims), 'issued', METADATA, 'expected')['sub'], 'account')
        invalid = [signed({**claims, 'iss': 'https://attacker.invalid'}), signed({**claims, 'aud': 'other'}),
                   signed({**claims, 'exp': int(time.time())-100}), signed({**claims, 'nonce': 'other'}),
                   signed({k: v for k, v in claims.items() if k != 'sub'}), signed(claims, 'shared-secret-for-rejection-test-123456', 'HS256')]
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        invalid.append(signed(claims, other_key))
        for token in invalid:
            with self.assertRaises(RuntimeError):
                verifier.verify(token, 'issued', METADATA, 'expected')


@unittest.skipUnless(importlib.util.find_spec('openai'), 'OpenAI SDK 설치 필요')
class RealSDKStreamTest(NetworkBlocked):
    def test_실제_SDK가_네트워크_없이_모의_SSE를_해석한다(self):
        import openai
        import openai._base_client as sdk_transport
        httpx2 = getattr(sdk_transport, "httpx2", None) or getattr(sdk_transport, "httpx")
        self.login()
        response = {'id': 'resp_mock', 'object': 'response', 'created_at': 1, 'status': 'completed',
                    'model': 'chosen-model', 'output': [{'id': 'msg_1', 'type': 'message', 'role': 'assistant',
                    'status': 'completed', 'content': [{'type': 'output_text', 'text': '{"translations":[]}', 'annotations': []}]}],
                    'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}
        event = {'type': 'response.completed', 'response': response, 'sequence_number': 1}
        def handler(request):
            self.assertEqual(str(request.url), RESOURCE + '/responses')
            self.assertEqual(request.headers['authorization'], 'Bearer access-SECRET')
            self.assertTrue(json.loads(request.content)['stream'])
            return httpx2.Response(200, headers={'content-type': 'text/event-stream'},
                                    content='event: response.completed\ndata: ' + json.dumps(event) + '\n\n')
        def factory(**kwargs):
            return openai.OpenAI(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
        result = ChatGPTProvider(self.session, factory).complete('chosen-model', '', {}, {'type': 'object'}, 'schema')
        self.assertEqual(result.text, '{"translations":[]}')
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.usage['input_tokens'], 10)


if __name__ == '__main__':
    unittest.main()
