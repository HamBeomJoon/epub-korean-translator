"""Offline progress and notification tests; real desktop alerts are mocked."""
import io
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import patch

from helpers import FakeProvider, OfflineTestCase, epub
from epub_pipeline.cli import build_parser, run
from epub_pipeline.domain import APIResponse, Chapter, ProgressEvent, RetryableError
from epub_pipeline.notifications import SCRIPT, notify_macos
from epub_pipeline.pipeline import Settings, TranslationPipeline
from epub_pipeline.progress import TerminalProgress, format_duration
from epub_pipeline.repository import FileRepository


class ProgressTest(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.chapter = Chapter(3, 'Title', 'chapter.xhtml', [{'id': 'p1', 'text': 'Original'}])
        self.repo = FileRepository(self.root / 'out')

    def test_완료_작업과_현재_스타일과_배치와_검수를_구분한다(self):
        events = []
        fake = FakeProvider()
        settings = Settings(model='mock', review=True, max_items=1, workers=1)
        pipeline = TranslationPipeline(self.repo, settings, fake, on_progress=events.append)
        pipeline.run(self.chapter, 'v1', approved=True)
        completed = [e for e in events if e.phase == 'completed']
        self.assertEqual([e.completed for e in completed], list(range(1, 13)))
        self.assertEqual({e.total for e in events}, {12})
        self.assertEqual({e.batches for e in events}, {2})
        self.assertEqual({e.chapter for e in events}, {3})
        self.assertEqual({e.style for e in completed}, {'faithful', 'modern', 'easy'})
        self.assertEqual([e.stage for e in completed], ['translation', 'review'] * 6)
        self.assertEqual(events[-1].stage, 'saving')
        self.assertEqual(len(fake.calls), 12)
        requests = list(fake.calls)
        keys = [pipeline._key(r) for r in requests]
        events.clear()
        fake.calls.clear()
        resumed = TranslationPipeline(self.repo, settings, fake, on_progress=events.append)
        resumed.run(self.chapter, 'v1', approved=True)
        self.assertEqual(fake.calls, [])
        self.assertEqual(events[-1].cache_hits, 12)
        self.assertEqual(events[-1].completed, 12)
        self.assertTrue(all(self.repo.load_cache(key) for key in keys))

    def test_재시도와_잘못된_응답은_완료_작업을_늘리지_않는다(self):
        events = []
        fake = FakeProvider([RetryableError('busy', 4), APIResponse('invalid JSON', {'input_tokens': 2})])
        settings = Settings(model='mock', styles=('faithful',))
        sleeps = []
        pipeline = TranslationPipeline(self.repo, settings, fake, sleep=sleeps.append, on_progress=events.append)
        pipeline.run(self.chapter, 'v1', approved=True)
        self.assertEqual([e.attempt for e in events if e.phase == 'request'], [1, 2, 3])
        self.assertEqual([e.completed for e in events if e.phase == 'completed'], [1])
        self.assertEqual(sleeps[0], 4)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(next(e for e in events if e.phase == 'retry_wait').wait_seconds, 4)

    def test_진행률_콜백이_실패해도_요청과_결과가_바뀌지_않는다(self):
        fake = FakeProvider()
        observer = SimpleNamespace(calls=0)
        def broken(event):
            observer.calls += 1
            raise OSError('closed terminal')
        settings = Settings(model='mock', styles=('faithful',))
        result = TranslationPipeline(self.repo, settings, fake, on_progress=broken).run(self.chapter, 'v1', approved=True)
        baseline_provider = FakeProvider()
        baseline = TranslationPipeline(FileRepository(self.root / 'baseline'), settings, baseline_provider).run(self.chapter, 'v1', approved=True)
        self.assertEqual(observer.calls, 1)
        self.assertEqual(fake.calls, baseline_provider.calls)
        self.assertEqual(result, baseline)

    def test_터미널_이외에는_줄단위로_표시하고_진행률을_끌_수_있다(self):
        stream = io.StringIO()
        event = ProgressEvent(3, 'modern', 'review', 'retry_wait', 2, 4, 7, 24, 3, 2, 5)
        with patch('epub_pipeline.progress.time.monotonic', return_value=1703):
            with TerminalProgress(3, 1, 2, 200, stream=stream) as progress:
                progress.update(event)
                self.assertIsNone(progress._thread)
        text = stream.getvalue()
        for value in ('선택 1/2 · 3장', '자연스러운 현대어', 'AI 검수', '현재 배치 2/4',
                      '완료 작업 7/24', '캐시 3', '재시도 대기 5초', '경과 25분 3초'):
            self.assertIn(value, text)
        self.assertNotIn('\033', text)
        stream = io.StringIO()
        with TerminalProgress(3, 1, 2, 0, enabled=False, stream=stream) as progress:
            progress.update(event)
        self.assertEqual(stream.getvalue(), '')
        self.assertEqual(format_duration(3601.9), '1시간 0분 1초')
        closed = io.StringIO()
        closed.close()
        with TerminalProgress(3, 1, 2, 0, stream=closed) as progress:
            progress.update(event)

    def test_대화형_터미널은_대기_중에도_갱신하고_종료하면_스레드를_정리한다(self):
        class LiveStream(io.StringIO):
            def __init__(self):
                super().__init__()
                self.refreshed = threading.Event()
                self.writes = 0
            def isatty(self):
                return True
            def write(self, text):
                self.writes += 1
                if self.writes >= 2:
                    self.refreshed.set()
                return super().write(text)
        stream = LiveStream()
        with TerminalProgress(3, 1, 1, 0, stream=stream) as progress:
            self.assertTrue(stream.refreshed.wait(3))
        self.assertFalse(progress._thread.is_alive())
        self.assertTrue(stream.getvalue().endswith('\n'))

    def test_먼저_끝난_작업_대신_실제로_진행_중인_문체를_표시한다(self):
        stream = io.StringIO()
        with TerminalProgress(1, 1, 1, 0, stream=stream) as progress:
            progress.update(ProgressEvent(1, 'faithful', 'translation', 'request', 1, 1, 0, 6, 0, 1))
            progress.update(ProgressEvent(1, 'modern', 'translation', 'request', 1, 1, 0, 6, 0, 1))
            self.assertIn('진행 중 2작업', stream.getvalue().splitlines()[-1])
            progress.update(ProgressEvent(1, 'faithful', 'translation', 'completed', 1, 1, 1, 6, 0))
            line = stream.getvalue().splitlines()[-1]
            self.assertIn('자연스러운 현대어', line)
            self.assertIn('진행 중 1작업', line)
            self.assertIn('완료 작업 1/6', line)


class NotificationTest(OfflineTestCase):
    def test_알림_내용을_스크립트가_아닌_인자로_전달한다(self):
        content = '"\n do shell script "unexpected"'
        with patch('epub_pipeline.notifications.sys.platform', 'darwin'), \
                patch('epub_pipeline.notifications.subprocess.run', return_value=SimpleNamespace(returncode=0)) as process:
            self.assertTrue(notify_macos('알림 테스트', content))
        args, kwargs = process.call_args
        self.assertEqual(args[0], ['/usr/bin/osascript', '-', '알림 테스트', content])
        self.assertEqual(kwargs['input'], SCRIPT)
        self.assertNotIn(content, SCRIPT)
        self.assertNotIn('shell', kwargs)
        self.assertEqual(kwargs['timeout'], 3)

    def test_다른_OS와_알림_실패는_번역_실패로_처리하지_않는다(self):
        with patch('epub_pipeline.notifications.sys.platform', 'linux'), \
                patch('epub_pipeline.notifications.subprocess.run') as process:
            self.assertFalse(notify_macos('title', 'message'))
            process.assert_not_called()
        for failure in (OSError('missing'), subprocess.TimeoutExpired('osascript', 3)):
            with patch('epub_pipeline.notifications.sys.platform', 'darwin'), \
                    patch('epub_pipeline.notifications.subprocess.run', side_effect=failure):
                self.assertFalse(notify_macos('title', 'message'))

    def arguments(self, *extra):
        path = self.root / 'input.epub'
        epub(path, ['<section epub:type="chapter"><p>A</p></section>',
                    '<section epub:type="chapter"><p>B</p></section>'])
        return build_parser().parse_args(['--input', str(path), '--provider', 'openai', '--all-chapters',
                                        '--styles', 'faithful', '--output', str(self.root / 'out'), *extra])

    def test_전체_장_완료_후_한_번_알리고_캐시_실행도_완료로_처리한다(self):
        args = self.arguments('--approve-api')
        with patch('epub_pipeline.cli.OpenAIProvider.from_environment', return_value=FakeProvider()), \
                patch('builtins.print'):
            run(args)
        self.notification.assert_called_once()
        self.assertEqual(self.notification.call_args.args[0], 'EPUB 번역 완료')
        self.assertIn('2/2개 장 완료', self.notification.call_args.args[1])
        self.notification.reset_mock()
        with patch('epub_pipeline.cli.OpenAIProvider.from_environment', side_effect=AssertionError('No API')), \
                patch('builtins.print'):
            run(args)
        self.notification.assert_called_once()
        self.assertEqual(self.notification.call_args.args[0], 'EPUB 번역 완료')

    def test_실패와_중단을_완료와_구분하고_원래_예외를_보존한다(self):
        for failure, label in ((RuntimeError('provider failed'), '실패'), (KeyboardInterrupt(), '중단')):
            self.notification.reset_mock()
            args = self.arguments('--approve-api')
            with patch('epub_pipeline.cli.OpenAIProvider.from_environment', side_effect=failure), \
                    patch('builtins.print'):
                with self.assertRaises(type(failure)):
                    run(args)
            self.assertEqual(self.notification.call_args.args[0], f'EPUB 번역 {label}')
            self.assertIn('0/2개 장 완료', self.notification.call_args.args[1])

    def test_책_인덱스_저장_실패에는_완료_알림을_보내지_않는다(self):
        args = self.arguments('--approve-api')
        original = FileRepository.write
        def fail_index(repo, name, value):
            if name == 'book.json':
                raise OSError('disk full')
            original(repo, name, value)
        with patch('epub_pipeline.cli.OpenAIProvider.from_environment', return_value=FakeProvider()), \
                patch.object(FileRepository, 'write', fail_index), patch('builtins.print'):
            with self.assertRaises(OSError):
                run(args)
        self.assertEqual(self.notification.call_args.args[0], 'EPUB 번역 실패')

    def test_미승인과_dry_run과_알림_끄기에서는_알리지_않는다(self):
        for extra in ((), ('--approve-api', '--dry-run'), ('--approve-api', '--no-notify', '--no-progress')):
            self.notification.reset_mock()
            args = self.arguments(*extra)
            with patch('epub_pipeline.cli.OpenAIProvider.from_environment', return_value=FakeProvider()), \
                    patch('builtins.print'), patch('sys.stderr', new_callable=io.StringIO) as stream:
                run(args)
            self.notification.assert_not_called()
            self.assertEqual(stream.getvalue(), '')
