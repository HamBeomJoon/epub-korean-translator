"""Offline output migration: preserve bytes, checkpoints, locks and app references."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from epub_pipeline.cli import build_parser, run
from epub_pipeline.domain import Chapter
from epub_pipeline.pipeline import Settings, TranslationPipeline
from epub_pipeline.repository import FileRepository, INTERNAL_FILES, TRANSLATION_FILES
from test_service_pipeline import FakeProvider


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def legacy_files(root):
    root.mkdir(parents=True, exist_ok=True)
    for name in TRANSLATION_FILES:
        (root / name).write_text('# 번역 제목\n\n번역 본문\n', encoding='utf-8')
    revision = 'old-version'
    generation = root / 'generations' / revision
    generation.mkdir(parents=True)
    (generation / 'aligned.json').write_text('{"paragraphs": []}', encoding='utf-8')
    (generation / 'faithful.md').write_text('immutable old edition', encoding='utf-8')
    manifest = {'revision': revision, 'aligned': f'generations/{revision}/aligned.json',
                'markdown': {'faithful': f'generations/{revision}/faithful.md'}}
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (root / '.cache').mkdir()
    (root / '.cache/cached.json').write_text('{"result": {"translations": []}}', encoding='utf-8')
    (root / '.usage').mkdir()
    (root / '.usage/attempt.json').write_text('{"status": "accepted"}', encoding='utf-8')
    for name in ('plan.json', 'source.json', 'source.preview.json', 'review_report.json', 'aligned.json', 'usage.json'):
        (root / name).write_text('{}', encoding='utf-8')


class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'book' / 'chapter-01'
        self.repo = FileRepository(self.root)

    def test_legacy_migration_preserves_all_bytes_and_manifest_relative_paths(self):
        legacy_files(self.root)
        (self.root / 'personal-notes.txt').write_text('keep my notes')
        originals = {p.relative_to(self.root): digest(p) for p in self.root.rglob('*') if p.is_file()}
        with self.repo.lock():
            self.assertEqual(len(self.repo.last_migration), len(INTERNAL_FILES | TRANSLATION_FILES))
        for relative, expected in originals.items():
            path = self.root / relative if str(relative) == 'personal-notes.txt' else self.repo.artifact_path(str(relative))
            self.assertEqual(digest(path), expected, str(relative))
        manifest = json.loads(self.repo.artifact_path('manifest.json').read_text())
        self.assertTrue((self.repo.internal / manifest['aligned']).exists())
        self.assertTrue((self.repo.internal / manifest['markdown']['faithful']).exists())
        self.assertEqual(self.repo.load_cache('cached')['result'], {'translations': []})
        self.assertEqual(self.repo.attempts(), [{'status': 'accepted'}])
        with self.repo.lock():
            self.assertEqual(self.repo.last_migration, [])

    def test_conflict_never_overwrites_or_partially_moves_other_files(self):
        legacy_files(self.root)
        self.repo.readable.mkdir()
        (self.repo.readable / 'modern.md').write_text('my edited translation')
        with self.assertRaisesRegex(RuntimeError, '충돌'):
            with self.repo.lock():
                pass
        self.assertEqual((self.repo.readable / 'modern.md').read_text(), 'my edited translation')
        self.assertTrue((self.root / '.cache/cached.json').exists())
        self.assertTrue((self.root / 'faithful.md').exists())

    def test_interrupted_migration_can_resume_without_overwrite(self):
        legacy_files(self.root)
        rename = Path.rename
        calls = []
        def fail_after_first(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise OSError('interrupted rename')
            return rename(source, target)
        with patch.object(Path, 'rename', fail_after_first):
            with self.assertRaises(OSError):
                with self.repo.lock():
                    pass
        with self.repo.lock():
            pass
        self.assertTrue(self.repo.artifact_path('faithful.md').exists())
        self.assertTrue(self.repo.load_cache('cached'))
        self.assertEqual(len(self.repo.attempts()), 1)

    def test_old_writer_lock_prevents_migration(self):
        legacy_files(self.root)
        with self.repo.lock(organize=False):
            with self.assertRaisesRegex(RuntimeError, '다른 프로세스'):
                with FileRepository(self.root).lock():
                    pass
            self.assertTrue((self.root / 'modern.md').exists())
            self.assertFalse(self.repo.readable.exists())

    def test_existing_checkpoint_resume_uses_zero_new_requests(self):
        chapter = Chapter(1, 'Title', 'chapter.xhtml', [{'id': 'p1', 'text': 'Original'}])
        settings = Settings(model='mock', styles=('faithful',), review=True)
        fake = FakeProvider()
        TranslationPipeline(self.repo, settings, fake).run(chapter, 'source', approved=True)
        for child in self.repo.internal.iterdir():
            child.rename(self.root / child.name)
        self.repo.internal.rmdir()
        for child in self.repo.readable.iterdir():
            child.rename(self.root / child.name)
        self.repo.readable.rmdir()
        original = digest(self.root / 'faithful.md')
        fake.calls.clear()
        with self.repo.lock():
            TranslationPipeline(self.repo, settings, fake).run(chapter, 'source', approved=True)
        self.assertEqual(fake.calls, [])
        self.assertEqual(digest(self.repo.artifact_path('faithful.md')), original)

    def test_existing_book_index_is_updated_to_moved_immutable_generation(self):
        legacy_files(self.root)
        index = self.root.parent / 'book.json'
        index.write_text(json.dumps({'chapters': [{'chapter_id': 'stable', 'revision': 'old-version',
                          'manifest': 'chapter-01/manifest.json',
                          'aligned': 'chapter-01/generations/old-version/aligned.json'}]}))
        with self.repo.lock():
            pass
        chapter = json.loads(index.read_text())['chapters'][0]
        self.assertEqual(chapter['revision'], 'old-version')
        self.assertTrue((self.root.parent / chapter['manifest']).exists())
        self.assertTrue((self.root.parent / chapter['aligned']).exists())

    def test_cli_organize_is_offline_and_requires_existing_directory(self):
        legacy_files(self.root)
        args = build_parser().parse_args(['--organize-output', str(self.root)])
        with patch('epub_pipeline.chatgpt_auth.OAuthHTTP.request', side_effect=AssertionError('No network')), patch('builtins.print'):
            run(args)
        self.assertTrue(self.repo.artifact_path('easy.md').exists())
        with self.assertRaises(ValueError):
            run(build_parser().parse_args(['--organize-output', str(self.root / 'absent')]))
