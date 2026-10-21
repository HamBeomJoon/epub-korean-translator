"""Persisted output names do not rename sources or change translation requests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from epub_pipeline.cli import book_output_path, build_parser, run
from epub_pipeline.domain import Chapter


class BookNameTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_default_stem_and_whitespace_are_unchanged_without_mapping(self):
        self.assertEqual(book_output_path(Path('author_title_translator.epub'), self.root), self.root / 'author_title_translator')
        self.assertEqual(book_output_path(Path('my book.epub'), self.root), self.root / 'my_book')

    def test_named_book_and_other_books_resolve_independently(self):
        (self.root / '.book-names.json').write_text(json.dumps({'author_title_translator': 'author_title'}))
        self.assertEqual(book_output_path(Path('author_title_translator.epub'), self.root), self.root / 'author_title')
        self.assertEqual(book_output_path(Path('other.epub'), self.root), self.root / 'other')

    def test_mapping_cannot_escape_output_root_or_use_invalid_values(self):
        for value in ('../outside', '/absolute', 'a/b', 'a\\b', '', ' ', '.', '..', '\x00', 5):
            (self.root / '.book-names.json').write_text(json.dumps({'book': value}))
            with self.assertRaises(ValueError):
                book_output_path(Path('book.epub'), self.root)

    def test_cli_uses_saved_name_for_the_next_chapter_without_network(self):
        source = self.root / 'author_title_translator.epub'
        source.write_bytes(b'source unchanged')
        output = self.root / 'output'
        output.mkdir()
        (output / '.book-names.json').write_text(json.dumps({'author_title_translator': 'author_title'}))
        chapters = [Chapter(i, f'Chapter {i}', f'{i}.xhtml', [{'id': f'p{i}', 'text': f'Original {i}'}]) for i in (1, 2)]
        args = build_parser().parse_args(['--input', str(source), '--chapter', '2', '--dry-run', '--output', str(output)])
        with patch('epub_pipeline.cli.read_epub', return_value=chapters), patch('epub_pipeline.chatgpt_auth.OAuthHTTP.request', side_effect=AssertionError('No network')), patch('builtins.print'):
            run(args)
        self.assertTrue((output / 'author_title/chapter-02/_관리/source.preview.json').is_file())
        self.assertFalse((output / 'author_title_translator').exists())
        self.assertEqual(source.read_bytes(), b'source unchanged')
