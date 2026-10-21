"""Bounded concurrency tests use barriers and synthetic responses, never an API."""
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures
from dataclasses import replace
import json
import importlib.util
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from helpers import FakeProvider, OfflineTestCase
from epub_pipeline.chatgpt_provider import ChatGPTProvider
from epub_pipeline.cli import build_parser
from epub_pipeline.domain import Chapter, RetryableError, signature
from epub_pipeline.pipeline import Settings, TranslationPipeline
from epub_pipeline.repository import FileRepository
from epub_pipeline.provider import OpenAIProvider


class MeasuredProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.active, self.peak, self.started = 0, 0, 0
        self.first_pair = threading.Barrier(2)

    def complete(self, **request):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.started += 1
            number = self.started
            response = super().complete(**request)
        try:
            if number <= 2:
                self.first_pair.wait(timeout=5)
            time.sleep(0.005)
            return response
        finally:
            with self.lock:
                self.active -= 1


class ParallelTest(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.chapter = Chapter(1, 'Title', 'chapter.xhtml',
                               [{'id': 'p1', 'text': 'Original one'}, {'id': 'p2', 'text': 'Original two'}])
        self.repo = FileRepository(self.root / 'parallel')
        self.settings = Settings(model='mock', review=True, max_items=1,
                                 glossary={'one': '하나'}, consistency={'narrator': 'first person'})

    def test_동시_두_요청을_넘지_않고_순차_요청과_원문과_출력이_일치한다(self):
        serial_provider = FakeProvider()
        serial_repo = FileRepository(self.root / 'serial')
        serial = TranslationPipeline(serial_repo, replace(self.settings, workers=1), serial_provider)
        expected = serial.run(self.chapter, 'v1', approved=True)
        parallel_provider = MeasuredProvider()
        events = []
        parallel = TranslationPipeline(self.repo, self.settings, parallel_provider, on_progress=events.append)
        actual = parallel.run(self.chapter, 'v1', approved=True)
        self.assertEqual(parallel_provider.peak, 2)
        self.assertEqual(parallel_provider.active, 0)
        self.assertEqual(actual, expected)
        self.assertEqual(sorted(map(signature, parallel_provider.calls)), sorted(map(signature, serial_provider.calls)))
        for name in ('aligned.json', 'review_report.json', 'review_report.html', 'faithful.md', 'modern.md', 'easy.md'):
            self.assertEqual(self.repo.artifact_path(name).read_bytes(), serial_repo.artifact_path(name).read_bytes())
        usage = json.loads(self.repo.artifact_path('usage.json').read_text())
        self.assertEqual(usage['this_run']['attempts'], 18)
        self.assertEqual(usage['this_run']['observed_tokens']['input_tokens'], 1800)
        self.assertEqual(len(parallel.records), 18)
        self.assertEqual(len({r['attempt_id'] for r in self.repo.attempts()}), 18)
        self.assertEqual([e.completed for e in events if e.phase == 'completed'], list(range(1, 19)))
        resumed_provider = FakeProvider()
        resumed = TranslationPipeline(self.repo, replace(self.settings, workers=1), resumed_provider)
        self.assertEqual(resumed.plan(self.chapter, 'v1')['estimated_new_calls_without_retries'], 0)
        self.assertEqual(resumed.run(self.chapter, 'v1', approved=True), expected)
        self.assertEqual(resumed_provider.calls, [])
        self.assertEqual(resumed.cache_hits, 18)

    def test_뒤_문단이_먼저_끝나도_문단_ID와_검수_지적을_원문_순서로_저장한다(self):
        release = threading.Event()
        finished = []
        class ReversedProvider(FakeProvider):
            def complete(inner, **request):
                pid = request['payload']['paragraphs'][0]['id']
                if pid.endswith('-title'):
                    if not release.wait(5):
                        raise RuntimeError('second batch did not start')
                response = super().complete(**request)
                if request['name'] == 'review_issues':
                    response = replace(response, text=json.dumps({'issues': [
                        {'id': pid, 'category': 'terminology', 'description': '지적 ' + pid}]}))
                finished.append(pid)
                if pid == 'p1':
                    release.set()
                return response
        settings = replace(self.settings, styles=('faithful',))
        result = TranslationPipeline(self.repo, settings, ReversedProvider()).run(self.chapter, 'v1', approved=True)
        self.assertEqual(finished[0], 'p1')
        self.assertEqual([p['id'] for p in result['paragraphs']], ['p1', 'p2'])
        self.assertEqual(result['paragraphs'][0]['translations']['faithful'], '번역 Original one')
        report = json.loads(self.repo.artifact_path('review_report.json').read_text())
        self.assertEqual([i['id'] for i in report['faithful']['issues']], [p['id'] for p in self.chapter.items])

    def test_실패하면_새_작업과_후속_검수를_멈추고_진행_중_사용량과_캐시를_보존한다(self):
        ready = threading.Barrier(2)
        lock = threading.Lock()
        calls = []
        pipeline = None
        class FailingProvider(FakeProvider):
            def complete(inner, **request):
                with lock:
                    number = len(calls)
                    calls.append(request)
                ready.wait(timeout=5)
                if number == 0:
                    raise RuntimeError('original failure')
                if not pipeline._stop.wait(5):
                    raise RuntimeError('stop flag missing')
                return super().complete(**request)
        self.repo.write('manifest.json', {'revision': 'old'})
        pipeline = TranslationPipeline(self.repo, self.settings, FailingProvider())
        def canceled_first(pending, **kwargs):
            return wait_futures({list(pending)[1]})
        with patch('epub_pipeline.pipeline.wait', side_effect=canceled_first):
            with self.assertRaisesRegex(RuntimeError, 'original failure'):
                pipeline.run(self.chapter, 'v1', approved=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(self.repo.artifact_path('manifest.json').read_text()), {'revision': 'old'})
        usage = json.loads(self.repo.artifact_path('usage.json').read_text())
        self.assertEqual(usage['timing']['status'], 'failed')
        self.assertEqual(usage['this_run']['attempts'], 2)
        self.assertEqual(len(list((self.repo.internal / '.cache').glob('*.json'))), 1)
        resumed_provider = FakeProvider()
        TranslationPipeline(self.repo, self.settings, resumed_provider).run(self.chapter, 'v1', approved=True)
        self.assertEqual(len(resumed_provider.calls), 17)

    def test_중단_시_전송된_요청을_정리하고_작업_스레드가_끝난_뒤_사용량을_저장한다(self):
        ready, release = threading.Event(), threading.Event()
        lock = threading.Lock()
        active = [0]
        class WaitingProvider(FakeProvider):
            def complete(inner, **request):
                with lock:
                    active[0] += 1
                    if active[0] == 2:
                        ready.set()
                try:
                    if not release.wait(5):
                        raise RuntimeError('release missing')
                    return super().complete(**request)
                finally:
                    with lock:
                        active[0] -= 1
        pipeline = TranslationPipeline(self.repo, self.settings, WaitingProvider())
        def interrupt(*args, **kwargs):
            self.assertTrue(ready.wait(5))
            pipeline._stop.set()
            release.set()
            raise KeyboardInterrupt()
        with patch('epub_pipeline.pipeline.wait', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                pipeline.run(self.chapter, 'v1', approved=True)
        self.assertEqual(active[0], 0)
        usage = json.loads(self.repo.artifact_path('usage.json').read_text())
        self.assertEqual(usage['timing']['status'], 'interrupted')
        self.assertEqual(usage['this_run']['attempts'], 2)
        self.assertFalse(self.repo.artifact_path('manifest.json').exists())

    def test_병렬_실행의_재시도도_상한과_서버_대기_시간을_지킨다(self):
        sleeps, lock = [], threading.Lock()
        attempts = {}
        class RetryProvider(FakeProvider):
            def complete(inner, **request):
                key = signature(request)
                with lock:
                    count = attempts.get(key, 0)
                    attempts[key] = count + 1
                if count == 0:
                    raise RetryableError('429', 2)
                return super().complete(**request)
        pipeline = TranslationPipeline(self.repo, replace(self.settings, review=False), RetryProvider(), sleep=sleeps.append)
        pipeline.run(self.chapter, 'v1', approved=True)
        self.assertEqual(sleeps, [2] * 9)
        self.assertTrue(all(value == 2 for value in attempts.values()))
        self.assertEqual(len(pipeline.records), 18)

    def test_기본값은_두_작업이고_순차_전환과_오프라인_계획을_지원한다(self):
        self.assertEqual(build_parser().parse_args([]).workers, 2)
        self.assertEqual(build_parser().parse_args(['--workers', '1']).workers, 1)
        for value in (0, 3, -1, True, '2'):
            with self.assertRaises(ValueError):
                Settings(workers=value)
        fake = FakeProvider()
        plan = TranslationPipeline(self.repo, self.settings, fake).plan(self.chapter, 'v1')
        self.assertEqual(plan['workers'], 2)
        self.assertEqual(fake.calls, [])


class ChatGPTParallelTest(OfflineTestCase):
    def test_모델_목록과_토큰은_직렬로_읽고_스트림은_동시에_처리한다(self):
        counts = {'catalog': 0, 'token': 0}
        barrier = threading.Barrier(2)
        def models():
            counts['catalog'] += 1
            time.sleep(0.01)
            return [{'slug': 'mock'}]
        def token():
            counts['token'] += 1
            return 'synthetic-token'
        session = NS(models=models, access_token=token)
        class Stream:
            def __enter__(self):
                barrier.wait(timeout=5)
                return iter([NS(type='response.completed', response=NS(status='completed', output_text='{}', usage=None))])
            def __exit__(self, *args):
                pass
        def factory(**kwargs):
            self.assertEqual(kwargs['api_key'], 'synthetic-token')
            return NS(responses=NS(create=lambda **_: Stream()), close=lambda: None)
        provider = ChatGPTProvider(session, factory)
        def request():
            return provider.complete('mock', 'instructions', {}, {}, 'test')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: request(), range(2)))
        self.assertEqual(counts, {'catalog': 1, 'token': 2})
        self.assertTrue(all(r.status == 'completed' for r in results))

    @unittest.skipUnless(importlib.util.find_spec('openai'), 'OpenAI SDK 설치 필요')
    def test_API_키_어댑터의_공유_SDK도_모의_HTTP에서_동시에_응답을_처리한다(self):
        import openai
        import openai._base_client as sdk_transport
        httpx = getattr(sdk_transport, 'httpx2', None) or getattr(sdk_transport, 'httpx')
        barrier = threading.Barrier(2)
        def handler(request):
            barrier.wait(timeout=5)
            return httpx.Response(200, json={
                'id': 'synthetic-response', 'object': 'response', 'created_at': 0,
                'model': 'mock', 'status': 'completed', 'output': [],
                'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
            with openai.OpenAI(api_key='synthetic-key', max_retries=0, http_client=transport) as client:
                provider = OpenAIProvider(client)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: provider.complete('mock', 'instructions', {}, {}, 'test'), range(2)))
        self.assertTrue(all(r.status == 'completed' for r in results))
        self.assertTrue(all(r.usage['input_tokens'] == 10 for r in results))
