"""Tests run without credentials or any network access."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[1] / "translate_epub.py"
spec = importlib.util.spec_from_file_location("translate_epub", SCRIPT)
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FakeResponses:
    def create(self, **kwargs):
        payload = json.loads(kwargs["input"])
        paragraphs = payload["paragraphs"]
        translations = [{"id": p["id"], "text": "번역: " + p["text"]} for p in paragraphs]
        return SimpleNamespace(
            output_text=json.dumps({"translations": translations}, ensure_ascii=False),
            status="completed", usage=SimpleNamespace(input_tokens=40, output_tokens=60)
        )


class TranslationTest(unittest.TestCase):
    def test_배치_제한과_문단_순서를_유지한다(self):
        items = [{"id": "p1", "text": "abcd"}, {"id": "p2", "text": "efgh"}, {"id": "p3", "text": "i"}]
        batches = module.make_batches(items, max_chars=7, max_items=2)
        self.assertEqual([[x["id"] for x in batch] for batch in batches], [["p1"], ["p2", "p3"]])

    def test_문단_ID와_응답_순서를_검증한다(self):
        src = [{"id": "1", "text": "a"}, {"id": "2", "text": "b"}]
        with self.assertRaises(ValueError):
            module.validate_translations(src, [{"id": "2", "text": "b"}, {"id": "1", "text": "a"}])
        with self.assertRaises(ValueError):
            module.validate_translations(src, [{"id": "1", "text": "a"}])
        self.assertEqual(module.validate_translations(src, [{"id": "1", "text": "가"}, {"id": "2", "text": "나"}]), {"1": "가", "2": "나"})

    def test_모의_API_응답에서_번역과_사용량을_반환한다(self):
        fake_client = SimpleNamespace(responses=FakeResponses())
        source = [{"id": "ch01-title", "text": "I: Two Worlds"}, {"id": "ch01-p001", "text": "Hello."}]
        result, usage = module.translate_batch(fake_client, "test-model", "modern", source, {}, "I: Two Worlds")
        self.assertEqual(result["ch01-p001"], "번역: Hello.")
        self.assertEqual(usage, {"input_tokens": 40, "output_tokens": 60})

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
            chapters = module.read_epub(path)
            self.assertEqual(len(chapters), 1)
            self.assertEqual(chapters[0].title, "I: Two Worlds")
            self.assertEqual([x["text"] for x in chapters[0].paragraphs], ["Hello world.", "Quote.", "End."])


if __name__ == "__main__":
    unittest.main()
