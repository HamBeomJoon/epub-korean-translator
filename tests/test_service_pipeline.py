"""All integration tests prohibit network connections, including accidental SDK calls."""
from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from helpers import FakeProvider, OfflineTestCase, epub

from epub_pipeline.cli import build_parser, run
from epub_pipeline.domain import APIResponse, Chapter, RetryableError, validate_review
from epub_pipeline.parser import read_epub
from epub_pipeline.pipeline import Settings, TranslationPipeline
from epub_pipeline.pricing import RateCard, summarize
from epub_pipeline.provider import OpenAIProvider, retry_after
from epub_pipeline.repository import FileRepository, write_json_atomic


class OfflineTest(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.repo = FileRepository(self.root / 'output')
        self.chapter = Chapter(1, 'Book title', 'OPS/1.xhtml',
                               [{'id': 'p1', 'text': 'ORIGINAL ONE'}, {'id': 'p2', 'text': 'ORIGINAL TWO'}],
                               chapter_id='chapter-stable')
        self.rates = RateCard({'mock': {'currency': 'USD', 'input_per_million': '2',
                                      'cached_input_per_million': '0.5', 'output_per_million': '8'}})

    def pipeline(self, provider=None, **kwargs):
        kwargs.setdefault('workers', 1)
        return TranslationPipeline(self.repo, Settings(model='mock', **kwargs), provider or FakeProvider(),
                                   rates=self.rates, sleep=lambda _: None)

    def test_세_번역_스타일에_동일한_원문을_독립적으로_전달한다(self):
        fake = FakeProvider()
        p = self.pipeline(fake, review=True, max_items=2, glossary={'ONE': '하나'}, consistency={'viewpoint': '1인칭'})
        bundle = p.run(self.chapter, 'v1', approved=True)
        translations = [r for r in fake.calls if r['name'] == 'translated_paragraphs']
        self.assertEqual(len(translations), 6)
        for i in (0, 3):
            self.assertEqual(translations[i]['payload'], translations[i+1]['payload'])
            self.assertEqual(translations[i]['payload'], translations[i+2]['payload'])
        for request in translations:
            raw = json.dumps(request['payload'], ensure_ascii=False)
            self.assertNotIn('번역 ', raw)
            self.assertIn('context_before', raw)
        self.assertTrue(bundle['human_review_required'])
        self.assertEqual(bundle['paragraphs'][0]['review_status']['faithful'], 'passed')
        self.assertEqual(set(bundle['paragraphs'][0]['translations']), {'faithful', 'modern', 'easy'})
        manifest = json.loads((self.repo.artifact_path('manifest.json')).read_text())
        self.assertEqual(json.loads((self.repo.internal / manifest['aligned']).read_text()), bundle)

    def test_승인이_없으면_공급자를_호출하지_않는다(self):
        fake = FakeProvider()
        with self.assertRaisesRegex(RuntimeError, '승인'):
            self.pipeline(fake).run(self.chapter, 'v1')
        self.assertEqual(fake.calls, [])

    def test_캐시_재개는_새_사용량을_추가하지_않고_검수_변경은_캐시를_무효화한다(self):
        p = self.pipeline(review=True)
        p.run(self.chapter, 'v1', approved=True)
        original_count = len(self.repo.attempts())
        fake = FakeProvider()
        q = self.pipeline(fake, review=True)
        q.run(self.chapter, 'v1', approved=True)
        self.assertEqual(fake.calls, [])
        self.assertEqual(q.cache_hits, 6)
        self.assertEqual(len(self.repo.attempts()), original_count)
        with patch('epub_pipeline.pipeline.REVIEW_PROMPT', 'new review prompt'):
            r = self.pipeline(fake, review=True)
            r.run(self.chapter, 'v1', approved=True)
        self.assertEqual([c['name'] for c in fake.calls], ['review_issues'] * 3)

    def test_소요_시간에_검수와_재시도_대기와_저장을_포함하고_과거_실행은_합산하지_않는다(self):
        clock = [0.0]
        def advance(seconds):
            clock[0] += seconds
        fake = FakeProvider([RetryableError('busy', 4)])
        complete = fake.complete
        def timed_request(**request):
            advance(10)
            return complete(**request)
        fake.complete = timed_request
        pipeline = self.pipeline(fake, styles=('faithful',), review=True)
        pipeline.sleep = advance
        export = pipeline._export
        def timed_export(*args):
            advance(2)
            return export(*args)
        with patch('epub_pipeline.pipeline.time.monotonic', side_effect=lambda: clock[0]), \
                patch.object(pipeline, '_export', side_effect=timed_export):
            bundle = pipeline.run(self.chapter, 'v1', approved=True)
        usage = json.loads(self.repo.artifact_path('usage.json').read_text())
        self.assertEqual(usage['timing'], pipeline.timing)
        self.assertEqual(usage['timing']['elapsed_seconds'], 36.0)
        self.assertEqual(usage['timing']['status'], 'completed')
        self.assertTrue(usage['timing']['started_at'].endswith('+00:00'))
        self.assertTrue(usage['timing']['finished_at'].endswith('+00:00'))
        fake = FakeProvider()
        resumed = self.pipeline(fake, styles=('faithful',), review=True)
        export = resumed._export
        with patch('epub_pipeline.pipeline.time.monotonic', side_effect=lambda: clock[0]), \
                patch.object(resumed, '_export', side_effect=timed_export):
            self.assertEqual(resumed.run(self.chapter, 'v1', approved=True), bundle)
        usage = json.loads(self.repo.artifact_path('usage.json').read_text())
        self.assertEqual(fake.calls, [])
        self.assertEqual(usage['timing']['elapsed_seconds'], 2.0)
        self.assertEqual(usage['this_run']['attempts'], 0)
        self.assertEqual(usage['cache_hits_this_run'], 2)

    def test_실패하거나_키보드로_중단해도_소요_시간을_저장한다(self):
        for failure, status in ((RuntimeError('failed'), 'failed'), (KeyboardInterrupt(), 'interrupted')):
            with self.subTest(status=status):
                clock = [0.0]
                fake = FakeProvider([failure])
                complete = fake.complete
                def timed_request(**request):
                    clock[0] += 7.5
                    return complete(**request)
                fake.complete = timed_request
                pipeline = self.pipeline(fake, styles=('faithful',))
                with patch('epub_pipeline.pipeline.time.monotonic', side_effect=lambda: clock[0]):
                    with self.assertRaises(type(failure)):
                        pipeline.run(self.chapter, 'v1', approved=True)
                usage = json.loads(self.repo.artifact_path('usage.json').read_text())
                self.assertEqual(usage['timing']['elapsed_seconds'], 7.5)
                self.assertEqual(usage['timing']['status'], status)

    def test_전체_장_번역에서_챕터별_소요_시간과_전체_시간을_출력한다(self):
        path = self.root / 'timed.epub'
        epub(path, ['<section epub:type="chapter"><p>A</p></section>',
                    '<section epub:type="chapter"><p>B</p></section>'])
        args = build_parser().parse_args(['--provider', 'openai', '--input', str(path), '--all-chapters',
                                         '--styles', 'faithful', '--approve-api', '--output', str(self.root / 'timed')])
        clock = [0.0]
        fake = FakeProvider()
        complete = fake.complete
        def timed_request(**request):
            clock[0] += 61.25
            return complete(**request)
        fake.complete = timed_request
        with patch('epub_pipeline.cli.OpenAIProvider.from_environment', return_value=fake), \
                patch('epub_pipeline.cli.time.monotonic', side_effect=lambda: clock[0]), \
                patch('builtins.print') as output:
            run(args)
        lines = [call.args[0] for call in output.call_args_list]
        self.assertIn('1장 완료 소요 시간: 1분 1초', lines)
        self.assertIn('2장 완료 소요 시간: 1분 1초', lines)
        self.assertIn('전체 작업 소요 시간: 2분 2초', lines)

    def test_명령행_작업이_중단되면_경과_시간을_출력한다(self):
        path = self.root / 'interrupted.epub'
        epub(path, ['<section epub:type="chapter"><p>A</p></section>'])
        args = build_parser().parse_args(['--provider', 'openai', '--input', str(path), '--styles', 'faithful',
                                         '--approve-api', '--output', str(self.root / 'interrupted')])
        clock = [0.0]
        fake = FakeProvider()
        def interrupted_request(**request):
            clock[0] += 3601.5
            raise KeyboardInterrupt()
        fake.complete = interrupted_request
        with patch('epub_pipeline.cli.OpenAIProvider.from_environment', return_value=fake), \
                patch('epub_pipeline.cli.time.monotonic', side_effect=lambda: clock[0]), \
                patch('builtins.print') as output:
            with self.assertRaises(KeyboardInterrupt):
                run(args)
        lines = [call.args[0] for call in output.call_args_list]
        self.assertIn('1장 중단 소요 시간: 1시간 0분 1초', lines)
        self.assertIn('전체 작업 소요 시간: 1시간 0분 1초', lines)

    def test_중단_후_재개하면_미완료_배치만_요청한다(self):
        fake = FakeProvider([None, KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            self.pipeline(fake, styles=('faithful',), max_items=1).run(self.chapter, 'v1', approved=True)
        self.assertTrue((self.repo.artifact_path('usage.json')).exists())
        resumed = FakeProvider()
        self.pipeline(resumed, styles=('faithful',), max_items=1).run(self.chapter, 'v1', approved=True)
        self.assertEqual(len(resumed.calls), 2)
        self.assertEqual(resumed.calls[0]['payload']['paragraphs'][0]['id'], 'p1')
        self.assertFalse((self.repo.output / 'aligned.json.tmp').exists())

    def test_잘못된_응답을_재시도해도_받은_토큰을_모두_합산한다(self):
        malformed = APIResponse('{"translations":[]}', {'input_tokens': 10, 'output_tokens': 5, 'cached_input_tokens': 0})
        fake = FakeProvider([malformed])
        self.pipeline(fake, styles=('faithful',)).run(self.chapter, 'v1', approved=True)
        summary = summarize(self.repo.attempts())
        self.assertEqual(summary['observed_tokens']['input_tokens'], 110)
        self.assertEqual(summary['observed_tokens']['output_tokens'], 55)
        self.assertEqual(summary['attempts'], 2)
        self.assertEqual(sum(r['status'] == 'invalid_response' for r in self.repo.attempts()), 1)
        self.assertEqual(Decimal(summary['known_cost_by_currency']['USD']), Decimal('0.00063'))

    def test_서버의_재시도_대기_시간과_최대_시도_횟수를_지킨다(self):
        sleep = []
        fake = FakeProvider([RetryableError('429', 4), RetryableError('503', 2)])
        p = self.pipeline(fake, styles=('faithful',))
        p.sleep = sleep.append
        p.run(self.chapter, 'v1', approved=True)
        self.assertEqual(sleep, [4, 2])
        self.assertEqual(summarize(self.repo.attempts())['unknown_usage_attempts'], 2)
        failure = FakeProvider([APIResponse('not json', {'input_tokens': 7, 'output_tokens': 1})] * 3)
        with self.assertRaisesRegex(RuntimeError, '3회 실패'):
            self.pipeline(failure, styles=('faithful',)).run(self.chapter, 'v2', approved=True)
        self.assertEqual(len(failure.calls), 3)

    def test_JSON이_유효해도_미완료_응답은_거부한다(self):
        fake = FakeProvider([APIResponse('{"translations":[]}', {'input_tokens': 9, 'output_tokens': 2}, 'incomplete')] * 3)
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            self.pipeline(fake, styles=('faithful',)).run(self.chapter, 'v1', approved=True)
        self.assertEqual(summarize(self.repo.attempts())['observed_tokens']['input_tokens'], 27)

    def test_검수의_잘못된_ID를_버리지_않고_재시도한다(self):
        issue = {'id': 'not-source', 'category': 'omission', 'description': '누락'}
        fake = FakeProvider(review_issues=[issue])
        with self.assertRaisesRegex(RuntimeError, '잘못된 검수 항목'):
            self.pipeline(fake, styles=('faithful',), review=True).run(self.chapter, 'v1', approved=True)
        self.assertEqual(len(fake.calls), 4)
        self.assertFalse((self.repo.artifact_path('manifest.json')).exists())
        for category in ('invented', [], None):
            with self.assertRaises(ValueError):
                validate_review(self.chapter.paragraphs, {'issues': [{'id': 'p1', 'category': category, 'description': 'x'}]})

    def test_검수_지적과_미검수와_미생성_스타일의_상태를_구분한다(self):
        fake = FakeProvider(review_issues=[{'id': 'p1', 'category': 'addition', 'description': '원문에 없는 정보'}])
        result = self.pipeline(fake, styles=('modern',), review=True).run(self.chapter, 'v1', approved=True)
        row = result['paragraphs'][0]
        self.assertEqual(row['review_status'], {'faithful': 'not_generated', 'modern': 'flagged', 'easy': 'not_generated'})
        self.assertIsNone(row['translations']['easy'])
        result2 = self.pipeline(styles=('modern',)).run(self.chapter, 'v1', approved=True)
        self.assertEqual(result2['paragraphs'][0]['review_status']['modern'], 'not_reviewed')

    def test_검수_보고서에_위치와_스타일별_본문을_추가하고_기존_지적은_보존한다(self):
        chapter = replace(self.chapter, index=3, title_source='Original\nchapter title')
        title_id = chapter.items[0]['id']
        translations = {style: {p['id']: style + '\n' + p['text'] for p in chapter.items}
                        for style in ('faithful', 'modern', 'easy')}
        raw_issues = [{'id': 'p2', 'category': 'omission', 'description': '원문 누락'},
                      {'id': title_id, 'category': 'style', 'description': '제목 문체'}]
        issues = {style: [dict(i) for i in raw_issues] for style in translations}
        self.pipeline(review=True)._export(chapter, 'v1', translations, issues, False)
        report = json.loads(self.repo.artifact_path('review_report.json').read_text())
        for style in translations:
            body, title = report[style]['issues']
            self.assertEqual(body, {**raw_issues[0], 'chapter_number': 3, 'paragraph_number': 2,
                                   'source_text': 'ORIGINAL TWO',
                                   'translation_text': translations[style]['p2']})
            self.assertEqual(title['paragraph_number'], 0)
            self.assertEqual(title['source_text'], 'Original\nchapter title')
            self.assertEqual(title['translation_text'], translations[style][title_id])
            self.assertEqual(issues[style], raw_issues)
        manifest = json.loads(self.repo.artifact_path('manifest.json').read_text())
        self.assertEqual(json.loads((self.repo.internal / manifest['review']).read_text()), report)

    def test_캐시된_검수에도_새_공급자_호출_없이_위치_정보를_추가한다(self):
        raw_issue = {'id': 'p1', 'category': 'terminology', 'description': '용어 확인'}
        self.pipeline(FakeProvider(review_issues=[raw_issue]), styles=('easy',), review=True).run(
            self.chapter, 'v1', approved=True)
        fake = FakeProvider()
        self.pipeline(fake, styles=('easy',), review=True).run(self.chapter, 'v1', approved=True)
        self.assertEqual(fake.calls, [])
        issue = json.loads(self.repo.artifact_path('review_report.json').read_text())['easy']['issues'][0]
        self.assertEqual(issue['chapter_number'], 1)
        self.assertEqual(issue['paragraph_number'], 1)
        self.assertEqual(issue['source_text'], 'ORIGINAL ONE')
        self.assertEqual(issue['translation_text'], '번역 ORIGINAL ONE')
        cached = [json.loads(p.read_text()) for p in (self.repo.internal / '.cache').glob('*.json')]
        review = next(c['result'] for c in cached if 'issues' in c.get('result', {}))
        self.assertEqual(review['issues'], [raw_issue])

    def test_요청_입력_변경에_따라_캐시를_무효화한다(self):
        self.pipeline(styles=('faithful',)).run(self.chapter, 'v1', approved=True)
        configurations = [({'glossary': {'ONE': '하나'}}, self.chapter, 'v1'),
                          ({'consistency': {'narrator': 'first'}}, self.chapter, 'v1'),
                          ({'review_model': 'mock2'}, self.chapter, 'v2'),
                          ({}, replace(self.chapter, title='changed'), 'v1')]
        for cfg, chapter, version in configurations:
            fake = FakeProvider()
            self.pipeline(fake, styles=('faithful',), **cfg).run(chapter, version, approved=True)
            self.assertEqual(len(fake.calls), 1)
        fake = FakeProvider()
        p = TranslationPipeline(self.repo, Settings(model='different', styles=('faithful',)), fake, sleep=lambda _: None)
        p.run(self.chapter, 'v1', approved=True)
        self.assertEqual(len(fake.calls), 1)
        with patch('epub_pipeline.pipeline.BASE_PROMPT', 'changed prompt'):
            fake = FakeProvider()
            self.pipeline(fake, styles=('faithful',)).run(self.chapter, 'v1', approved=True)
            self.assertEqual(len(fake.calls), 1)

    def test_손상된_캐시를_무시하고_변경된_문맥을_전달한다(self):
        p = self.pipeline(styles=('faithful',))
        p.run(self.chapter, 'v1', approved=True)
        next((self.repo.internal / '.cache').glob('*.json')).write_text('{broken')
        fake = FakeProvider()
        self.pipeline(fake, styles=('faithful',)).run(self.chapter, 'v1', approved=True)
        self.assertEqual(len(fake.calls), 1)
        fake = FakeProvider()
        self.pipeline(fake, styles=('faithful',)).run(self.chapter, 'v1', approved=True, before=[{'id': 'prev', 'text': 'previous chapter'}])
        self.assertEqual(fake.calls[0]['payload']['context_before'][0]['text'], 'previous chapter')

    def test_출력_발행에_실패하면_manifest는_이전_스냅샷을_유지한다(self):
        p = self.pipeline(styles=('faithful',))
        p.run(self.chapter, 'v1', approved=True)
        original = (self.repo.artifact_path('manifest.json')).read_text()
        old_write = self.repo.write
        def fail(name, value):
            if name.startswith('generations/') and name.endswith('/aligned.json'):
                raise OSError('disk failure')
            return old_write(name, value)
        with patch.object(self.repo, 'write', side_effect=fail):
            with self.assertRaises(OSError):
                self.pipeline(styles=('faithful',)).run(self.chapter, 'v2', approved=True)
        self.assertEqual((self.repo.artifact_path('manifest.json')).read_text(), original)

    def test_잠금으로_동시_쓰기를_막고_원자적_교체_실패_시_기존_데이터를_보존한다(self):
        with self.repo.lock():
            with self.assertRaises(RuntimeError):
                with FileRepository(self.repo.output).lock():
                    pass
        path = self.root / 'value.json'
        write_json_atomic(path, {'old': True})
        with patch('epub_pipeline.repository.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                write_json_atomic(path, {'new': True})
        self.assertEqual(json.loads(path.read_text()), {'old': True})
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_파서는_spine_순서와_여러_구획과_안정적인_ID를_보존한다(self):
        path = self.root / 'book.epub'
        epub(path, ['<section id="a" epub:type="chapter"><p id="p">A</p></section><section id="b" epub:type="chapter"><p>B</p></section>',
                    '<section id="c" epub:type="chapter"><p>C</p></section>'], [1, 0])
        chapters = read_epub(path)
        self.assertEqual([c.paragraphs[0]['text'] for c in chapters], ['C', 'A', 'B'])
        original = chapters[1].paragraphs[0]['id']
        epub(path, ['<section id="a" epub:type="chapter"><p id="p">Changed A</p></section><section id="b" epub:type="chapter"><p>B</p></section>',
                    '<section id="c" epub:type="chapter"><p>C</p></section>'], [0, 1])
        self.assertEqual(read_epub(path)[0].paragraphs[0]['id'], original)

    def test_파서는_각주와_강조와_줄바꿈을_보존하고_표는_경고한다(self):
        path = self.root / 'rich.epub'
        epub(path, ['<section epub:type="chapter"><h2>Heading</h2><p>Hello <em>world</em>.<br/>Next line.</p><blockquote><p>Quote</p></blockquote><aside epub:type="footnote" id="n"><p>Note</p></aside><table><tr><td>Cell</td></tr></table></section>'])
        with self.assertLogs(level='WARNING'):
            ch = read_epub(path)[0]
        self.assertEqual([p['text'] for p in ch.paragraphs], ['Heading', 'Hello world.\nNext line.', 'Quote', 'Note', 'Cell'])
        self.assertEqual([p['kind'] for p in ch.paragraphs], ['heading', 'paragraph', 'quote', 'footnote', 'paragraph'])
        self.assertIn('em', json.dumps(ch.ast))
        self.assertIn('table', ' '.join(ch.warnings))

    def test_승인이_없거나_dry_run이면_SDK를_생성하지_않는다(self):
        path = self.root / 'cli.epub'
        epub(path, ['<section epub:type="chapter"><p>Text</p></section>'])
        base = ['--provider', 'openai', '--input', str(path), '--output', str(self.root / 'cli-output')]
        with patch('epub_pipeline.cli.OpenAIProvider.from_environment', side_effect=AssertionError('SDK must not be constructed')), patch('builtins.print') as output:
            for options in ([], ['--dry-run'], ['--dry-run', '--approve-api']):
                run(build_parser().parse_args(base + options))
        self.assertFalse(any('소요 시간:' in call.args[0] for call in output.call_args_list))
        self.assertTrue((self.root / 'cli-output/cli/chapter-01/_관리/plan.json').exists())
        self.assertFalse((self.root / 'cli-output/cli/chapter-01/_관리/manifest.json').exists())

    def test_전체_장_번역은_고정된_스냅샷의_책_인덱스를_발행한다(self):
        path = self.root / 'complete.epub'
        epub(path, ['<section id="a" epub:type="chapter"><p>A1</p><p>A2</p></section>',
                    '<section id="b" epub:type="chapter"><p>B1</p></section>'])
        fake = FakeProvider()
        args = build_parser().parse_args(['--provider', 'openai', '--input', str(path), '--all-chapters', '--review',
                                         '--approve-api', '--output', str(self.root / 'books')])
        with patch('epub_pipeline.cli.OpenAIProvider.from_environment', return_value=fake), patch('builtins.print'):
            run(args)
        base = self.root / 'books/complete'
        index = json.loads((base / 'book.json').read_text())
        self.assertEqual(len(index['chapters']), 2)
        for chapter in index['chapters']:
            aligned = json.loads((base / chapter['aligned']).read_text())
            self.assertEqual(aligned['revision'], chapter['revision'])
            self.assertEqual(aligned['chapter_id'], chapter['chapter_id'])
        chapter_two = [c for c in fake.calls if c['payload']['book_chapter_title'] == 'Chapter 1']
        self.assertTrue(any(c['payload']['context_before'] for c in chapter_two))
        fake.calls.clear()
        with patch('epub_pipeline.cli.OpenAIProvider.from_environment', side_effect=AssertionError('Fully cached run needs no SDK')), patch('builtins.print'):
            run(args)
        self.assertEqual(fake.calls, [])

    def test_검수_모델과_스키마_변경은_검수_캐시만_무효화한다(self):
        self.pipeline(styles=('faithful',), review=True).run(self.chapter, 'v1', approved=True)
        fake = FakeProvider()
        self.pipeline(fake, styles=('faithful',), review=True, review_model='review-model').run(self.chapter, 'v1', approved=True)
        self.assertEqual([r['name'] for r in fake.calls], ['review_issues'])
        self.assertEqual(fake.calls[0]['model'], 'review-model')
        with patch('epub_pipeline.pipeline.REVIEW_SCHEMA', {'type': 'object', 'changed': True}):
            fake = FakeProvider()
            self.pipeline(fake, styles=('faithful',), review=True).run(self.chapter, 'v1', approved=True)
            self.assertEqual([r['name'] for r in fake.calls], ['review_issues'])

    def test_제목_묶음과_중복된_요소_ID를_검증한다(self):
        path = self.root / 'title.epub'
        epub(path, ['<section epub:type="chapter"><hgroup><h2>I</h2><p>Two Worlds</p></hgroup><p>Body</p></section>'])
        ch = read_epub(path)[0]
        self.assertEqual(ch.items[0]['text'], 'I Two Worlds')
        self.assertEqual([p['text'] for p in ch.paragraphs], ['Body'])
        epub(path, ['<section epub:type="chapter"><p id="same">A</p><p id="same">B</p></section>'])
        with self.assertRaisesRegex(ValueError, '중복'):
            read_epub(path)

    def test_요금표가_없으면_비용을_무료로_표시하지_않는다(self):
        self.assertIsNone(RateCard().cost('unknown', {'input_tokens': 100, 'output_tokens': 20})['amount'])
        self.assertFalse(summarize([{'usage': None}])['cost_complete'])
        for value in ('NaN', '-1', 'Infinity'):
            with self.assertRaises(ValueError):
                RateCard({'m': {'currency': 'USD', 'input_per_million': value, 'output_per_million': '1', 'cached_input_per_million': '1'}})

    def test_공급자의_응답_구조와_사용량과_요청_제한_처리를_검증한다(self):
        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: SimpleNamespace(
            output_text='{}', status='completed', id='r', model='mock-snapshot',
            usage=SimpleNamespace(input_tokens=20, output_tokens=10, input_tokens_details=SimpleNamespace(cached_tokens=5)))))
        response = OpenAIProvider(client).complete('mock', 'instructions', {}, {}, 'schema')
        self.assertEqual(response.usage['cached_input_tokens'], 5)
        self.assertEqual(retry_after({'retry-after-ms': '2500'}), 2.5)
        self.assertEqual(retry_after({'retry-after': '4'}), 4)
        class Throttled(Exception):
            status_code = 429
            response = SimpleNamespace(headers={'retry-after': '3'})
        with patch.object(client.responses, 'create', side_effect=Throttled()):
            with self.assertRaises(RetryableError) as cm:
                OpenAIProvider(client).complete('mock', '', {}, {}, 's')
        self.assertEqual(cm.exception.retry_after, 3)


@unittest.skipUnless(os.environ.get('DEMIAN_EPUB'), 'DEMIAN_EPUB fixture 경로 미지정')
class RealDemianRegression(unittest.TestCase):
    def test_실제_데미안의_8개_장과_첫_장의_91개_본문_문단을_추출한다(self):
        chapters = read_epub(Path(os.environ['DEMIAN_EPUB']))
        self.assertEqual(len(chapters), 8)
        self.assertEqual(len(chapters[0].paragraphs), 91)


if __name__ == '__main__':
    unittest.main()
