"""EPUB chapter headings, navigation boundaries and cross-file continuations."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
from zipfile import ZipFile

from helpers import FakeProvider, OfflineTestCase
from epub_pipeline.cli import build_parser, run
from epub_pipeline.parser import read_epub


class HeadingChaptersTest(OfflineTestCase):
    def book(self, bodies, links=(), navigation='ncx'):
        path = self.root / 'book.epub'
        nav = navigation == 'nav'
        items = ''.join(f'<item id="c{i}" href="{i}.xhtml" media-type="application/xhtml+xml"/>'
                        for i in range(len(bodies)))
        items += ('<item id="toc" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>' if nav
                  else '<item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
        spine = ''.join(f'<itemref idref="c{i}"/>' for i in range(len(bodies)))
        if nav:
            spine += '<itemref idref="toc"/>'
        container = ('<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                     '<rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        with ZipFile(path, 'w') as archive:
            archive.writestr('META-INF/container.xml', container)
            archive.writestr('OPS/book.opf', '<package xmlns="http://www.idpf.org/2007/opf">'
                             f'<manifest>{items}</manifest><spine toc="toc">{spine}</spine></package>')
            for i, body in enumerate(bodies):
                archive.writestr(f'OPS/{i}.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" '
                                 'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Whole book</title>'
                                 f'</head><body>{body}</body></html>')
            if nav:
                entries = ''.join(f'<li><a href="{escape(href)}">{escape(label)}</a></li>' for href, label in links)
                archive.writestr('OPS/nav.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" '
                                 'xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                                 f'<nav epub:type="toc"><ol>{entries}</ol></nav>'
                                 '<nav epub:type="page-list"><a href="0.xhtml#page">1</a></nav></body></html>')
            else:
                entries = ''.join(f'<navPoint id="n{i}"><navLabel><text>{escape(label)}</text></navLabel>'
                                  f'<content src="{escape(href)}"/></navPoint>' for i, (href, label) in enumerate(links))
                archive.writestr('OPS/toc.ncx', '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
                                 f'<navMap>{entries}</navMap><pageList><pageTarget>'
                                 '<content src="0.xhtml#page"/></pageTarget></pageList></ncx>')
        return path

    def sample(self, navigation='ncx'):
        body = ('<div id="pg-header"><h2>License header</h2><p>LICENSE ONLY</p></div>'
                '<h1 id="title">Book title</h1><p>Publication information</p>'
                '<h2 class="chapter" id="intro"/><p>INTRO ONLY</p>'
                '<h2 class="chapter" id="one">Erstes Kapitel<br/>Zwei Welten</h2><p id="a">FIRST ONLY</p>'
                '<h2 class="chapter" id="zwei_ä">Zweites Kapitel<br/>Kain</h2><p id="b">SECOND ONLY</p>'
                '<div id="works"><h2>Other works</h2><p>ADVERTISEMENT ONLY</p></div>'
                '<div id="pg-footer"><h2 id="license">License footer</h2><p>FOOTER ONLY</p></div>')
        return self.book([body], [('0.xhtml#title', 'Book title'), ('0.xhtml#one', 'First'),
                                 ('0.xhtml#zwei_%C3%A4', 'Second'), ('0.xhtml#works', 'Other works'),
                                 ('0.xhtml#license', 'License')], navigation)

    def test_NCX와_장_제목으로_실제_장만_추출한다(self):
        chapters = read_epub(self.sample())
        self.assertEqual([ch.index for ch in chapters], [1, 2])
        self.assertEqual([ch.title for ch in chapters], ['Erstes Kapitel\nZwei Welten', 'Zweites Kapitel\nKain'])
        self.assertEqual([[p['text'] for p in ch.paragraphs] for ch in chapters], [['FIRST ONLY'], ['SECOND ONLY']])
        ast = json.dumps([ch.ast for ch in chapters])
        for excluded in ('LICENSE ONLY', 'INTRO ONLY', 'ADVERTISEMENT ONLY', 'FOOTER ONLY', 'SECOND ONLY'):
            self.assertNotIn(excluded, json.dumps(chapters[0].ast))
        self.assertNotIn('ADVERTISEMENT ONLY', ast)

    def test_EPUB3_목차에서도_본문과_광고를_구분한다(self):
        chapters = read_epub(self.sample(navigation='nav'))
        self.assertEqual([ch.paragraphs[0]['text'] for ch in chapters], ['FIRST ONLY', 'SECOND ONLY'])

    def test_프롤로그는_명시적으로_선택할_때만_포함한다(self):
        chapters = read_epub(self.sample(), include_prologue=True)
        self.assertEqual([(ch.index, ch.kind) for ch in chapters], [(0, 'prologue'), (1, 'chapter'), (2, 'chapter')])
        self.assertEqual(chapters[0].paragraphs[0]['text'], 'INTRO ONLY')

    def test_다음_HTML의_이어지는_본문을_같은_장에_합치고_원본_위치를_보존한다(self):
        bodies = ['<h2 class="chapter" id="one">First</h2><p id="same">A</p>',
                  '<p id="same">B</p><blockquote><p>C</p></blockquote>'
                  '<h2 class="chapter" id="two">Second</h2><p>D</p>',
                  '<p>E</p><div id="works"><h2>Other works</h2><p>EXCLUDED</p></div>',
                  '<p>EXCLUDED AFTER APPENDIX</p>']
        links = [('0.xhtml#one', 'First'), ('1.xhtml#two', 'Second'), ('2.xhtml#works', 'Works')]
        chapters = read_epub(self.book(bodies, links))
        self.assertEqual([[p['text'] for p in ch.paragraphs] for ch in chapters], [['A', 'B', 'C'], ['D', 'E']])
        self.assertEqual([p['source_file'] for p in chapters[0].paragraphs], ['OPS/0.xhtml', 'OPS/1.xhtml', 'OPS/1.xhtml'])
        self.assertEqual(chapters[0].paragraphs[0]['source_locator'], 'body/1')
        self.assertEqual(chapters[0].paragraphs[1]['source_locator'], 'body/0')
        self.assertEqual(chapters[0].paragraphs[2]['kind'], 'quote')
        self.assertEqual(len({p['id'] for ch in chapters for p in ch.paragraphs}), 5)
        self.assertEqual(len(chapters[0].ast['children']), 2)
        before = [p['id'] for p in chapters[0].paragraphs]
        bodies[0] = bodies[0].replace('>A<', '>Changed<')
        changed = read_epub(self.book(bodies, links))
        self.assertEqual([p['id'] for p in changed[0].paragraphs], before)

    def test_중첩_컨테이너의_강조와_줄바꿈과_tail을_누락하지_않는다(self):
        body = ('<div>OUTSIDE<h2 class="chapter" id="one">First</h2>After heading'
                '<p>A<em>bold</em>tail<br/>Next line</p>After paragraph'
                '<h2 class="chapter" id="two">Second</h2><p>B</p></div>')
        chapters = read_epub(self.book([body]))
        self.assertEqual([p['text'] for p in chapters[0].paragraphs], ['After heading', 'Aboldtail\nNext line', 'After paragraph'])
        self.assertEqual([p['text'] for p in chapters[1].paragraphs], ['B'])
        self.assertNotIn('OUTSIDE', json.dumps(chapters[0].ast))
        self.assertIn('em', json.dumps(chapters[0].ast))

    def test_본문_소제목과_페이지_목록은_새로운_장이나_종료_경계로_삼지_않는다(self):
        body = ('<h2 class="chapter" id="one">First</h2><p>A</p>'
                '<h3 id="sub">Subheading</h3><p>B</p><h2 id="page">Page marker</h2><p>C</p>')
        for navigation in ('ncx', 'nav'):
            with self.subTest(navigation=navigation):
                chapters = read_epub(self.book([body], [('0.xhtml#one', 'First'), ('0.xhtml#sub', 'Subheading')], navigation))
                self.assertEqual(len(chapters), 1)
                self.assertEqual([p['text'] for p in chapters[0].paragraphs], ['A', 'Subheading', 'B', 'Page marker', 'C'])

    def test_기존_semantic_구획은_제목_class보다_우선한다(self):
        body = ('<section epub:type="chapter"><h2 class="chapter">First</h2><p>A</p>'
                '<h2 class="chapter">Internal heading</h2><p>B</p></section>')
        chapters = read_epub(self.book([body]))
        self.assertEqual(len(chapters), 1)
        self.assertEqual([p['text'] for p in chapters[0].paragraphs], ['First', 'A', 'Internal heading', 'B'])

    def test_같은_장_앵커가_중복되면_서로_다른_장으로_저장하지_않는다(self):
        body = ('<h2 class="chapter" id="same">First</h2><p>A</p>'
                '<h2 class="chapter" id="same">Second</h2><p>B</p>')
        with self.assertRaisesRegex(ValueError, '중복 XHTML 장 ID'):
            read_epub(self.book([body]))

    def test_장_표식이_없는_기존_문서는_경고하고_spine_단위로_유지한다(self):
        with self.assertLogs(level='WARNING') as logs:
            chapters = read_epub(self.book(['<h2>Heading</h2><p>A</p>']))
        self.assertEqual(len(chapters), 1)
        self.assertIn('spine 문서 전체', '\n'.join(logs.output))

    def test_CLI의_chapter_1은_첫_장만_번역기에_전달한다(self):
        provider = FakeProvider()
        args = build_parser().parse_args(['--input', str(self.sample()), '--provider', 'openai', '--model', 'mock',
                                         '--chapter', '1', '--styles', 'faithful', 'modern', '--approve-api',
                                         '--output', str(self.root / 'out'), '--no-progress', '--no-notify'])
        with patch('epub_pipeline.cli.OpenAIProvider.from_environment', return_value=provider), patch('builtins.print'):
            run(args)
        self.assertEqual(len(provider.calls), 2)
        for request in provider.calls:
            texts = [p['text'] for p in request['payload']['paragraphs']]
            self.assertEqual(texts, ['Erstes Kapitel\nZwei Welten', 'FIRST ONLY'])
            self.assertNotIn('LICENSE ONLY', json.dumps(request['payload']))

    def test_장_목록은_원문_제목의_줄바꿈을_한_줄로_표시한다(self):
        args = build_parser().parse_args(['--input', str(self.sample()), '--list-chapters'])
        with patch('builtins.print') as output:
            run(args)
        lines = [call.args[0] for call in output.call_args_list]
        self.assertEqual(len(lines), 2)
        self.assertIn('1 | Erstes Kapitel Zwei Welten | 1문단', lines[0])
        self.assertTrue(all('\n' not in line for line in lines))


@unittest.skipUnless(os.environ.get('DEMIAN_GERMAN_EPUB'), 'DEMIAN_GERMAN_EPUB fixture 경로 미지정')
class GermanDemianRegression(unittest.TestCase):
    def test_독일어_데미안의_8개_장과_파일을_넘는_본문을_보존한다(self):
        chapters = read_epub(Path(os.environ['DEMIAN_GERMAN_EPUB']))
        self.assertEqual(len(chapters), 8)
        self.assertEqual(chapters[0].title, 'Erstes Kapitel\n\nZwei Welten')
        self.assertEqual(len(chapters[0].paragraphs), 94)
        self.assertTrue(chapters[0].paragraphs[0]['text'].startswith('Ich beginne meine Geschichte'))
        self.assertTrue(chapters[1].title.startswith('Zweites Kapitel'))
        for chapter in chapters:
            self.assertNotIn('Project Gutenberg', '\n'.join(p['text'] for p in chapter.paragraphs))
        for index in (1, 3, 6):
            self.assertEqual(len({p['source_file'] for p in chapters[index].paragraphs}), 2)
