"""Inspect Git index blobs without reading local credentials or printing values."""
from __future__ import annotations

import argparse
import os
from pathlib import PurePosixPath
import re
import subprocess
import sys


PATTERNS = (
    ('OpenAI API 키', rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}'),
    ('GitHub 토큰', rb'\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}'),
    ('AWS 접근 키', rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    ('JWT', rb'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}'),
    ('개인 키', rb'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----'),
    ('Slack 토큰', rb'\bxox[baprs]-[A-Za-z0-9-]{20,}'),
    ('인증정보 할당', rb'''(?i)["']?\b(?:openai_api_key|api_key|access_token|refresh_token|id_token|client_secret)\b["']?\s*[:=]\s*["']([A-Za-z0-9_./+=-]{20,})["']'''),
    ('인증정보 할당', rb'''(?im)^\s*(?:export\s+)?(?:openai_api_key|api_key|access_token|refresh_token|id_token|client_secret)\s*[:=]\s*([A-Za-z0-9_./+=-]{20,})\s*(?:\#.*)?$'''),
)
PLACEHOLDERS = {b'your_openai_api_key_here', b'your_api_key_here'}
PRIVATE_DIRECTORIES = {'.chatgpt-auth', '.epub_translation_tool', '.aws', '.ssh',
                       'output', 'test-output', '_관리', '번역본', '.venv'}
PRIVATE_FILES = {'codex_prompt.md', 'session.json', 'credentials.json', 'credentials',
                 'tokens.json', 'token.json', 'auth.json', '.ds_store'}


def git(*args: str) -> bytes:
    return subprocess.check_output(['git', *args], stderr=subprocess.DEVNULL)


def private_path(name: str) -> bool:
    path = PurePosixPath(name)
    filename = path.name.casefold()
    return (filename in PRIVATE_FILES
            or (filename == '.env' or filename.startswith('.env.')) and filename != '.env.example'
            or path.suffix.casefold() in {'.epub', '.pem', '.key', '.p12', '.pfx'}
            or any(part.casefold() in PRIVATE_DIRECTORIES for part in path.parts[:-1]))


def scan(staged: bool) -> list[tuple[str, str]]:
    changed = None
    if staged:
        changed = set(git('diff', '--cached', '--name-only', '--diff-filter=ACMRT',
                          '--no-renames', '-z').split(b'\0'))
    findings = []
    for entry in git('ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        metadata, raw_name = entry.split(b'\t', 1)
        if changed is not None and raw_name not in changed:
            continue
        mode, oid, stage = metadata.split()
        name = os.fsdecode(raw_name)
        if private_path(name):
            findings.append((name, '공유 제외 파일'))
            continue
        if stage != b'0' or mode == b'160000':
            findings.append((name, '병합 충돌 또는 검사할 수 없는 하위 저장소'))
            continue
        content = git('cat-file', 'blob', oid.decode('ascii'))
        for label, pattern in PATTERNS:
            for match in re.finditer(pattern, content):
                if label == '인증정보 할당' and match.group(1).lower() in PLACEHOLDERS:
                    continue
                findings.append((name, label))
                break
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description='Git에 포함될 비밀정보·개인 파일 검사')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--staged', action='store_true', help='이번 커밋의 변경 파일 검사')
    mode.add_argument('--all', action='store_true', help='Git index의 전체 추적 파일 검사')
    args = parser.parse_args()
    try:
        findings = scan(args.staged)
    except (OSError, subprocess.CalledProcessError):
        print('검사 실패: Git 저장소와 index를 확인하세요.', file=sys.stderr)
        return 2
    if findings:
        print('커밋 검사 실패: 아래 파일을 staging에서 제외하거나 비밀정보를 제거하세요.', file=sys.stderr)
        for name, reason in findings:
            print(f'  {name!r}: {reason}', file=sys.stderr)
        return 1
    print('비밀정보·개인 파일 검사 통과')
    return 0


if __name__ == '__main__':
    sys.exit(main())
