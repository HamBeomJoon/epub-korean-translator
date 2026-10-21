"""Exercise real Git hooks and index scanning in disposable repositories."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from helpers import OfflineTestCase


PROJECT = Path(__file__).resolve().parents[1]


class GitChecksTest(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.test',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.test')
        self.git('init', '-q')
        shutil.copytree(PROJECT / 'scripts', self.root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(PROJECT / '.githooks', self.root / '.githooks')

    def command(self, *args):
        return subprocess.run(args, cwd=self.root, env=self.env, capture_output=True, text=True)

    def git(self, *args):
        result = self.command('git', *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        self.git('add', '--', name)

    def scan(self, mode='--staged'):
        return self.command(sys.executable, 'scripts/check_secrets.py', mode)

    def test_실제_커밋에서_키를_차단하고_값을_출력하지_않는다(self):
        installed = self.command(sys.executable, 'scripts/install_hooks.py')
        self.assertEqual(installed.returncode, 0, installed.stderr)
        secret = 'sk-' + 'A' * 40
        self.write('settings.py', f'KEY = "{secret}"\n')
        result = self.command('git', 'commit', '-m', '검사 차단 확인')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('OpenAI API 키', result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotEqual(self.command('git', 'rev-parse', '--verify', 'HEAD').returncode, 0)
        self.write('settings.py', 'KEY = None\n')
        result = self.command('git', 'commit', '-m', '검사 통과 확인')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_작업_파일을_수정해도_staged_비밀정보는_차단한다(self):
        self.write('config.txt', 'github_pat_' + 'B' * 40)
        (self.root / 'config.txt').write_text('safe\n')
        self.assertEqual(self.scan().returncode, 1)
        self.git('add', 'config.txt')
        (self.root / 'config.txt').write_text('sk-' + 'C' * 40)
        self.assertEqual(self.scan().returncode, 0)

    def test_강제_추가된_개인_파일과_한글_경로를_차단한다(self):
        self.write('.gitignore', '.env\noutput/\n')
        names = ['.env', '.env.local', 'secrets/session.json', 'BOOK.EPUB',
                 'output/book/source.json', '책/번역본/faithful.md', '책/_관리/source.json',
                 'CODEX_PROMPT.md', '.chatgpt-auth/session.json', 'private.pem']
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('private\n')
            self.git('add', '-f', '--', name)
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        for name in names:
            self.assertIn(name, result.stderr)

    def test_양식은_허용하되_양식에_실제_형태의_키를_넣으면_차단한다(self):
        self.write('.env.example', 'OPENAI_API_KEY=your_openai_api_key_here\n')
        self.write('examples/consistency.json', '{"narrator": "first person"}\n')
        self.write('provider.py', 'client(api_key=self.session.access_token())\n')
        self.assertEqual(self.scan().returncode, 0)
        self.write('.env.example', 'OPENAI_API_KEY=' + 'sk-' + 'D' * 40)
        self.assertEqual(self.scan().returncode, 1)

    def test_삭제한_비밀정보는_허용하고_전체_검사는_기존_추적_파일도_확인한다(self):
        self.write('old.txt', 'sk-' + 'E' * 40)
        self.git('commit', '-qm', '검사용 초기 기록')
        self.assertEqual(self.scan().returncode, 0)
        self.assertEqual(self.scan('--all').returncode, 1)
        self.git('rm', 'old.txt')
        self.assertEqual(self.scan().returncode, 0)
        self.assertEqual(self.scan('--all').returncode, 0)

    def test_인증정보와_JWT와_개인_키_형식을_검출한다(self):
        samples = ['refresh_token="' + 'R' * 40 + '"',
                   'client_secret: "' + 'S' * 40 + '"',
                   'eyJ' + 'a' * 20 + '.' + 'b' * 25 + '.' + 'c' * 25,
                   '-----BEGIN ' + 'PRIVATE KEY-----',
                   'AKIA' + 'A' * 16, 'xoxb-' + 'X' * 30]
        for sample in samples:
            with self.subTest(prefix=sample[:4]):
                self.write('config.txt', sample)
                self.assertEqual(self.scan().returncode, 1)

    def test_기존_hook_설정을_덮어쓰지_않고_재설치는_허용한다(self):
        self.git('config', '--local', 'core.hooksPath', 'custom-hooks')
        result = self.command(sys.executable, 'scripts/install_hooks.py')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git('config', '--get', 'core.hooksPath'), 'custom-hooks')
        self.git('config', '--unset', 'core.hooksPath')
        old_hook = self.root / '.git/hooks/pre-commit'
        old_hook.write_text('#!/bin/sh\nexit 0\n')
        result = self.command(sys.executable, 'scripts/install_hooks.py')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(old_hook.read_text(), '#!/bin/sh\nexit 0\n')
        old_hook.unlink()
        for _ in range(2):
            result = self.command(sys.executable, 'scripts/install_hooks.py')
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('config', '--get', 'core.hooksPath'), '.githooks')

    def test_Git_저장소가_아니면_검사를_실패로_처리한다(self):
        shutil.rmtree(self.root / '.git')
        self.assertEqual(self.scan().returncode, 2)


if __name__ == '__main__':
    unittest.main()
