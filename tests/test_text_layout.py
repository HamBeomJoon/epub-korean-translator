"""Prose reflow preserves meaningful breaks, words, snapshots and AI history."""
from copy import deepcopy
import json
import re
from unittest.mock import patch

from helpers import FakeProvider, OfflineTestCase, epub
from epub_pipeline.cli import build_parser, run
from epub_pipeline.parser import read_epub
from epub_pipeline.pipeline import Settings, TranslationPipeline
from epub_pipeline.repository import FileRepository
from epub_pipeline.text_layout import annotate_layout, translation_text


class TextLayoutTest(OfflineTestCase):
    def setUp(self):
        super().setUp()
        path = self.root / 'input.epub'
        epub(path, ['<section epub:type="chapter"><p id="prose">Alpha\nBeta</p>'
                    '<p id="br">First<br/>Second</p><div class="poetry"><p id="verse">One\nTwo</p></div>'
                    '<pre id="pre">First\n    Indented</pre><blockquote><p id="quote">Quote\ncontinues</p>'
                    '</blockquote></section>'])
        self.chapter = read_epub(path)[0]
        self.repo = FileRepository(self.root / 'output' / 'chapter-01')

    def test_조판_개행과_명시적_개행과_운문과_공백_보존을_구별한다(self):
        paragraphs = self.chapter.paragraphs
        self.assertEqual([p['preserve_line_breaks'] for p in paragraphs], [False, True, True, True, False])
        self.assertEqual(paragraphs[0]['text'], 'Alpha\nBeta')
        self.assertEqual(paragraphs[1]['text'], 'First\nSecond')
        self.assertTrue(paragraphs[3]['preformatted'])
        self.assertEqual(translation_text('첫째\r\n\r\n둘째', paragraphs[0]), '첫째 둘째')
        for p in paragraphs[1:4]:
            self.assertEqual(translation_text('첫째\n둘째', p), '첫째\n둘째')
        self.assertEqual(translation_text('판단\n불가', {}), '판단\n불가')

    def test_번역_검수_JSON과_Markdown과_HTML에_같은_산문_정리를_적용한다(self):
        provider = FakeProvider()
        pipeline = TranslationPipeline(self.repo, Settings(model='mock', styles=('modern',), review=True), provider)
        bundle = pipeline.run(self.chapter, 'v1', approved=True)
        prose = bundle['paragraphs'][0]
        self.assertEqual(prose['translations']['modern'], '번역 Alpha Beta')
        self.assertEqual(prose['source_text'], 'Alpha\nBeta')
        review = next(call for call in provider.calls if call['name'] == 'review_issues')
        self.assertEqual(review['payload']['paragraphs'][1]['translation'], '번역 Alpha Beta')
        markdown = self.repo.artifact_path('modern.md').read_text()
        html = self.repo.artifact_path('review_report.html').read_text()
        self.assertIn('번역 Alpha Beta', markdown)
        self.assertIn('번역 Alpha Beta', html)
        self.assertIn('번역 First  \nSecond', markdown)
        self.assertIn('번역 One  \nTwo', markdown)
        self.assertIn('> 번역 Quote continues', markdown)
        self.assertIn('    번역 First\n        Indented', markdown)
        raw_caches = [json.loads(p.read_text()) for p in (self.repo.internal / '.cache').glob('*.json')]
        raw = next(c['result'] for c in raw_caches if 'translations' in c['result'])
        self.assertIn('\n', raw['translations'][1]['text'])
        provider.calls.clear()
        self.assertEqual(pipeline.run(self.chapter, 'v1', approved=True), bundle)
        self.assertEqual(provider.calls, [])

    def legacy(self):
        chapter = deepcopy(self.chapter)
        for paragraph in chapter.paragraphs:
            paragraph.pop('preserve_line_breaks')
            paragraph.pop('preformatted')
        values = {p['id']: '번역 ' + p['text'] for p in chapter.items}
        pipeline = TranslationPipeline(self.repo, Settings(model='mock', styles=('modern',), review=True))
        bundle = pipeline._export(chapter, 'legacy-version', {'modern': values}, {'modern': []}, False)
        self.repo.write('usage.json', {'unchanged': True})
        self.repo.save_cache('saved', {'unchanged': True})
        return bundle

    def reflow(self):
        args = build_parser().parse_args(['--reflow-output', str(self.repo.output)])
        with patch('epub_pipeline.cli.ChatGPTSession', side_effect=AssertionError('No auth')), \
                patch('epub_pipeline.cli.OpenAIProvider.from_environment', side_effect=AssertionError('No API')), \
                patch('epub_pipeline.cli.ChatGPTProvider.from_saved_session', side_effect=AssertionError('No API')), \
                patch('builtins.print'):
            run(args)

    def test_기존_결과는_추론_없이_새_스냅샷으로_정리하고_백업과_이력을_보존한다(self):
        original = self.legacy()
        before = {p: p.read_bytes() for p in self.repo.internal.rglob('*') if p.is_file()}
        old_markdown = self.repo.artifact_path('modern.md').read_bytes()
        self.reflow()
        current, report = self.repo.read_review_data()
        self.assertNotEqual(current['revision'], original['revision'])
        self.assertEqual(current['paragraphs'][0]['translations']['modern'], '번역 Alpha Beta')
        self.assertEqual(current['paragraphs'][1]['translations']['modern'], '번역 First\nSecond')
        self.assertEqual(current['paragraphs'][2]['translations']['modern'], '번역 One\nTwo')
        self.assertTrue(report['modern']['ai_review_executed'])
        for old, new in zip(original['paragraphs'], current['paragraphs']):
            self.assertEqual(re.sub(r'\s', '', old['translations']['modern']), re.sub(r'\s', '', new['translations']['modern']))
            self.assertEqual(old['id'], new['id'])
            self.assertEqual(old['source_text'], new['source_text'])
        for path, value in before.items():
            if '/generations/' in str(path) or '/.cache/' in str(path) or path.name == 'usage.json':
                self.assertEqual(path.read_bytes(), value)
        backups = list((self.repo.internal / 'reflow-backups').glob('*/modern.md'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), old_markdown)
        self.reflow()
        self.assertEqual(self.repo.read_review_data()[0]['revision'], current['revision'])

    def test_원본_AST에_없는_문단은_개행을_추측해_제거하지_않는다(self):
        paragraphs = [{'text': 'Unknown\ntext'}]
        annotate_layout(paragraphs, self.chapter.ast)
        self.assertTrue(paragraphs[0]['preserve_line_breaks'])

    def test_발행_후_사람이_수정한_번역본은_덮어쓰지_않는다(self):
        self.legacy()
        self.repo.artifact_path('modern.md').write_text('사람이 수정한 본문')
        manifest = self.repo.artifact_path('manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '발행 뒤 수정된 파일'):
            self.reflow()
        self.assertEqual(self.repo.artifact_path('modern.md').read_text(), '사람이 수정한 본문')
        self.assertEqual(self.repo.artifact_path('manifest.json').read_bytes(), manifest)

    def test_스냅샷_저장_실패_시_이전_발행과_읽기용_번역을_유지한다(self):
        self.legacy()
        manifest = self.repo.artifact_path('manifest.json').read_bytes()
        markdown = self.repo.artifact_path('modern.md').read_bytes()
        original_write = FileRepository.write_text
        def fail_html(repo, name, value):
            if name.startswith('generations/') and name.endswith('.html'):
                raise OSError('disk full')
            return original_write(repo, name, value)
        with patch.object(FileRepository, 'write_text', fail_html):
            with self.assertRaises(OSError):
                self.reflow()
        self.assertEqual(self.repo.artifact_path('manifest.json').read_bytes(), manifest)
        self.assertEqual(self.repo.artifact_path('modern.md').read_bytes(), markdown)

    def test_원문_버전_불일치와_외부_원문_경로와_실행_인자_혼합을_거부한다(self):
        self.legacy()
        source = self.repo.read_source_data()
        manifest = json.loads(self.repo.artifact_path('manifest.json').read_text())
        source_path = self.repo.internal / manifest['aligned'].replace('aligned.json', 'source.json')
        source_path.write_text(json.dumps({**source, 'version': 'different'}))
        with self.assertRaisesRegex(ValueError, '원문과 번역 스냅샷'):
            self.reflow()
        self.repo.write('manifest.json', {**manifest, 'aligned': '../outside/aligned.json'})
        with self.assertRaises(ValueError):
            self.repo.read_source_data()
        for arguments in (['--reflow-output', str(self.root / 'missing')],
                          ['--reflow-output', str(self.repo.output), '--approve-plan']):
            with self.assertRaises(ValueError):
                run(build_parser().parse_args(arguments))
