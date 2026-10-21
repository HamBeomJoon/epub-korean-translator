"""HTML output uses saved paragraph IDs and never requests another AI review."""
from copy import deepcopy
from html.parser import HTMLParser
import json
from unittest.mock import patch

from helpers import FakeProvider, OfflineTestCase
from epub_pipeline.cli import build_parser, run
from epub_pipeline.domain import Chapter
from epub_pipeline.pipeline import Settings, TranslationPipeline
from epub_pipeline.repository import FileRepository
from epub_pipeline.review_html import render_review_html


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags, self.text = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


class ReviewHTMLTest(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.repo = FileRepository(self.root / 'chapter-01')
        self.chapter = Chapter(1, 'Original title', 'chapter.xhtml',
                               [{'id': 'p1', 'text': 'Same source'}, {'id': 'p2', 'text': 'Same source'}],
                               chapter_id='stable')
        self.translations = {s: {p['id']: f'{s}:{p["id"]}\n한국어 번역' for p in self.chapter.items}
                             for s in ('faithful', 'modern', 'easy')}
        self.issues = {s: [] for s in self.translations}
        self.issues['modern'] = [{'id': 'p2', 'category': 'omission', 'description': '두 번째 문단 누락 확인'}]

    def export(self, review=True):
        pipeline = TranslationPipeline(self.repo, Settings(model='mock', review=review))
        bundle = pipeline._export(self.chapter, 'v1', self.translations, self.issues, False)
        report = json.loads(self.repo.artifact_path('review_report.json').read_text())
        return bundle, report

    def test_자동_보고서에_제목과_문단과_세_문체와_지적을_ID로_정렬한다(self):
        bundle, report = self.export()
        html = self.repo.artifact_path('review_report.html').read_text()
        sections = html.split('<section class="paragraph"')[1:]
        self.assertEqual(len(sections), 3)
        self.assertIn('제목 (문단 0)', sections[0])
        self.assertNotIn('두 번째 문단 누락 확인', sections[1])
        self.assertIn('1장 · 문단 2', sections[2])
        for style in self.translations:
            self.assertIn(f'{style}:p2', sections[2])
        self.assertIn('두 번째 문단 누락 확인', sections[2])
        manifest = json.loads(self.repo.artifact_path('manifest.json').read_text())
        self.assertEqual((self.repo.internal / manifest['review_html']).read_text(), html)
        original = deepcopy((bundle, report))
        self.assertEqual(render_review_html(bundle, report), html)
        self.assertEqual((bundle, report), original)

    def test_미생성과_미검수를_AI_지적_없음과_구분한다(self):
        self.translations = {'faithful': self.translations['faithful']}
        self.issues = {'faithful': []}
        self.export(review=False)
        html = self.repo.artifact_path('review_report.html').read_text()
        text = ''.join(Document(html).text)
        self.assertIn('번역 미생성', text)
        self.assertIn('AI 검수 미실행', text)
        self.assertNotIn('AI 지적 없음', text)
        self.assertIn('AI 지적은 자동 수정되지 않았습니다.', text)

    def test_책과_번역과_지적에_들어있는_HTML을_실행하지_않는다(self):
        bundle, report = self.export()
        payload = '</script><img src="https://example.test/leak" onerror="alert(1)">&'
        bundle['original_title'] = payload
        bundle['paragraphs'][1]['id'] = payload
        bundle['paragraphs'][1]['source_text'] = payload
        bundle['paragraphs'][1]['translations']['modern'] = payload
        report['modern']['issues'][0].update(id=payload, description=payload)
        html = render_review_html(bundle, report)
        doc = Document(html)
        self.assertIn(payload, ''.join(doc.text))
        self.assertFalse(any(tag == 'img' for tag, _ in doc.tags))
        self.assertEqual(sum(tag == 'script' for tag, _ in doc.tags), 1)
        self.assertFalse(any('src' in attrs or 'href' in attrs or 'onerror' in attrs for _, attrs in doc.tags))
        self.assertIn("default-src 'none'", html)

    def test_기존_결과는_발행_스냅샷을_읽고_JSON과_사용량과_캐시를_보존한다(self):
        self.export()
        self.repo.artifact_path('aligned.json').write_text('{}')
        self.repo.artifact_path('review_report.json').write_text('{}')
        self.repo.write('usage.json', {'old': 'usage'})
        self.repo.save_cache('saved', {'old': 'cache'})
        before = {p: p.read_bytes() for p in self.repo.output.rglob('*') if p.is_file()}
        args = build_parser().parse_args(['--review-html', str(self.repo.output)])
        with patch('epub_pipeline.cli.ChatGPTSession', side_effect=AssertionError('No auth')), \
                patch('epub_pipeline.cli.OpenAIProvider.from_environment', side_effect=AssertionError('No API')), \
                patch('epub_pipeline.cli.ChatGPTProvider.from_saved_session', side_effect=AssertionError('No API')), \
                patch('builtins.print'):
            run(args)
        html = self.repo.artifact_path('review_report.html').read_text()
        self.assertIn('두 번째 문단 누락 확인', html)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    def test_기존_루트_JSON만_있어도_HTML을_반복_생성하고_잘못된_ID는_거부한다(self):
        bundle, report = self.export()
        legacy = self.root / 'legacy'
        legacy.mkdir()
        for name, value in (('aligned.json', bundle), ('review_report.json', report)):
            (legacy / name).write_text(json.dumps(value))
        args = build_parser().parse_args(['--review-html', str(legacy)])
        with patch('builtins.print'):
            run(args)
            run(args)
        self.assertTrue((legacy / '_관리/review_report.html').exists())
        report['modern']['issues'][0]['id'] = 'unknown'
        with self.assertRaises(ValueError):
            render_review_html(bundle, report)

    def test_추가_요청_없이_HTML을_저장하고_캐시_재개에도_새_호출이_없다(self):
        fake = FakeProvider()
        settings = Settings(model='mock', review=True)
        pipeline = TranslationPipeline(self.repo, settings, fake)
        pipeline.run(self.chapter, 'v1', approved=True)
        self.assertEqual(len(fake.calls), 6)
        fake.calls.clear()
        pipeline.run(self.chapter, 'v1', approved=True)
        self.assertEqual(fake.calls, [])

    def test_manifest의_외부_경로와_revision_불일치와_없는_입력을_거부한다(self):
        self.export()
        manifest = json.loads(self.repo.artifact_path('manifest.json').read_text())
        for bad in ('../outside.json', '/tmp/outside.json'):
            self.repo.write('manifest.json', {**manifest, 'review': bad})
            with self.assertRaises(ValueError):
                self.repo.read_review_data()
        outside = self.root / 'outside.json'
        outside.write_text('{}')
        (self.repo.internal / 'linked.json').symlink_to(outside)
        self.repo.write('manifest.json', {**manifest, 'review': 'linked.json'})
        with self.assertRaises(ValueError):
            self.repo.read_review_data()
        self.repo.write('manifest.json', {**manifest, 'revision': 'wrong'})
        with self.assertRaises(ValueError):
            self.repo.read_review_data()
        for arguments in (['--review-html', str(self.root / 'missing')],
                          ['--review-html', str(self.repo.output), '--approve-plan']):
            with self.assertRaises(ValueError):
                run(build_parser().parse_args(arguments))

    def test_HTML_발행에_실패하면_이전_manifest를_유지한다(self):
        self.export()
        before = self.repo.artifact_path('manifest.json').read_bytes()
        original = self.repo.write_text
        def fail_html(name, content):
            if name.startswith('generations/') and name.endswith('.html'):
                raise OSError('disk full')
            original(name, content)
        with patch.object(self.repo, 'write_text', side_effect=fail_html):
            with self.assertRaises(OSError):
                self.export()
        self.assertEqual(self.repo.artifact_path('manifest.json').read_bytes(), before)
