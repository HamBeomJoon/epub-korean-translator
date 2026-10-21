"""Batching, paragraph validation and basic EPUB parsing tests."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from epub_pipeline.domain import make_batches, validate_translations
from epub_pipeline.parser import read_epub


class TranslationTest(unittest.TestCase):
    def test_배치_제한과_문단_순서를_유지한다(self):
        items = [{"id": "p1", "text": "abcd"}, {"id": "p2", "text": "efgh"}, {"id": "p3", "text": "i"}]
        batches = make_batches(items, max_chars=7, max_items=2)
        self.assertEqual([[x["id"] for x in batch] for batch in batches], [["p1"], ["p2", "p3"]])

    def test_문단_ID와_응답_순서를_검증한다(self):
        src = [{"id": "1", "text": "a"}, {"id": "2", "text": "b"}]
        with self.assertRaises(ValueError):
            validate_translations(src, [{"id": "2", "text": "b"}, {"id": "1", "text": "a"}])
        with self.assertRaises(ValueError):
            validate_translations(src, [{"id": "1", "text": "a"}])
        self.assertEqual(validate_translations(src, [{"id": "1", "text": "가"}, {"id": "2", "text": "나"}]), {"1": "가", "2": "나"})

    def test_EPUB의_spine과_본문_구획을_읽는다(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.epub"
            container = """<?xml version='1.0'?><container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'><rootfiles><rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/></rootfiles></container>"""
            opf = """<package xmlns='http://www.idpf.org/2007/opf'><manifest><item id='c1' href='text/ch1.xhtml' media-type='application/xhtml+xml'/></manifest><spine><itemref idref='c1'/></spine></package>"""
            chapter = """<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'><head><title>I: Two Worlds</title></head><body><section epub:type='chapter'><hgroup><h2>I</h2><p>Two Worlds</p></hgroup><p>Hello <em>world</em>.</p><blockquote><p>Quote.</p></blockquote><p>End.</p></section></body></html>"""
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("META-INF/container.xml", container)
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/text/ch1.xhtml", chapter)
            chapters = read_epub(path)
            self.assertEqual(len(chapters), 1)
            self.assertEqual(chapters[0].title, "I: Two Worlds")
            self.assertEqual([x["text"] for x in chapters[0].paragraphs], ["Hello world.", "Quote.", "End."])


if __name__ == "__main__":
    unittest.main()
