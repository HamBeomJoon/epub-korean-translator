"""Markdown loading and prompt delivery without model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys

from helpers import FakeProvider, OfflineTestCase

from epub_pipeline.domain import Chapter
from epub_pipeline.pipeline import Settings, TranslationPipeline
from epub_pipeline.prompts import BASE_PROMPT, REVIEW_PROMPT, STYLE_PROMPTS, _load_prompts
from epub_pipeline.repository import FileRepository


PROJECT = Path(__file__).resolve().parents[1]


class PromptsTest(OfflineTestCase):
    def document(self, **overrides):
        sections = {name: f'{name} 지침' for name in ('base', 'faithful', 'modern', 'easy', 'review')}
        sections.update(overrides)
        return '# 프롬프트 편집 안내\n\n' + '\n\n'.join(
            f'## {name}\n\n{text}' for name, text in sections.items() if text is not None
        ) + '\n'

    def load(self, document):
        path = self.root / 'prompts.md'
        path.write_text(document, encoding='utf-8')
        return _load_prompts(path)

    def test_구역_제목과_문서_안내는_제외하고_본문과_줄바꿈을_보존한다(self):
        prompts = self.load(self.document(base='첫째 줄\n\n[규칙]\n- 둘째 줄'))
        self.assertEqual(prompts['base'], '첫째 줄\n\n[규칙]\n- 둘째 줄')
        self.assertEqual(prompts['review'], 'review 지침')
        self.assertEqual(set(prompts), {'base', 'faithful', 'modern', 'easy', 'review'})

    def test_필수_구역이_없거나_비어_있으면_실패한다(self):
        for name in ('base', 'faithful', 'modern', 'easy', 'review'):
            for value in (None, '', ' \n\t'):
                with self.subTest(name=name, value=value):
                    with self.assertRaisesRegex(ValueError, f'필수 프롬프트.*{name}'):
                        self.load(self.document(**{name: value}))

    def test_중복되거나_알_수_없는_구역은_실패한다(self):
        for name in ('base', 'unknown'):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, f'알 수 없거나 중복된.*{name}'):
                    self.load(self.document() + f'\n## {name}\n\n추가 지침\n')

    def test_파일이_없거나_UTF8이_아니면_경로를_알린다(self):
        path = self.root / 'missing.md'
        with self.assertRaisesRegex(ValueError, str(path)):
            _load_prompts(path)
        path.write_bytes(b'\xff')
        with self.assertRaisesRegex(ValueError, str(path)):
            _load_prompts(path)

    def test_다른_작업_디렉터리에서도_모듈_옆의_Markdown을_읽는다(self):
        env = {**os.environ, 'PYTHONPATH': str(PROJECT)}
        script = ('import json; from epub_pipeline.prompts import BASE_PROMPT, STYLE_PROMPTS, REVIEW_PROMPT; '
                  'print(json.dumps([BASE_PROMPT, STYLE_PROMPTS, REVIEW_PROMPT]))')
        result = subprocess.run([sys.executable, '-c', script], cwd=self.root, env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [BASE_PROMPT, STYLE_PROMPTS, REVIEW_PROMPT])

    def test_Markdown_지침을_세_문체_번역과_검수에_전달한다(self):
        prompts = _load_prompts(PROJECT / 'epub_pipeline/prompts.md')
        provider = FakeProvider()
        pipeline = TranslationPipeline(FileRepository(self.root / 'output'),
                                       Settings(model='mock', review=True, workers=1), provider)
        pipeline.run(Chapter(1, 'Title', 'chapter.xhtml', [{'id': 'p1', 'text': 'Source'}]),
                     'source-v1', approved=True)
        translations = [call for call in provider.calls if call['name'] == 'translated_paragraphs']
        reviews = [call for call in provider.calls if call['name'] == 'review_issues']
        self.assertEqual(len(translations), 3)
        self.assertEqual(len(reviews), 3)
        for style, translation, review in zip(STYLE_PROMPTS, translations, reviews):
            self.assertEqual(translation['instructions'], prompts['base'] + '\n\n' + prompts[style])
            self.assertEqual(review['instructions'], prompts['review'])
            self.assertEqual(review['payload']['style_rules'], prompts[style])
