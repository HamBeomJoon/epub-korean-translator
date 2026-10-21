"""Private, atomic local OAuth storage; unrelated to translation checkpoints."""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


def default_auth_dir() -> Path:
    return Path.home() / ".epub_translation_tool" / "chatgpt"


class CredentialStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory if directory is not None else default_auth_dir()
        self.path = self.directory / "session.json"

    @staticmethod
    def check_profile(profile: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", profile):
            raise ValueError("ChatGPT profile은 영문/숫자/_/- 1~40자로 지정하세요.")

    def _private_directory(self):
        if self.directory.is_symlink():
            raise RuntimeError("로그인 저장 디렉터리에 심볼릭 링크를 사용할 수 없습니다.")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.stat().st_uid != os.getuid():
            raise RuntimeError("로그인 저장 디렉터리의 소유자가 다릅니다.")
        self.directory.chmod(0o700)

    @contextmanager
    def lock(self):
        self._private_directory()
        descriptor = os.open(self.directory / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a+") as handle:
            os.fchmod(handle.fileno(), 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def load(self) -> dict:
        if self.directory.is_symlink():
            raise RuntimeError("로그인 저장 디렉터리에 심볼릭 링크를 사용할 수 없습니다.")
        if not self.path.exists():
            if self.path.is_symlink():
                raise RuntimeError("로그인 파일에 심볼릭 링크를 사용할 수 없습니다.")
            return {"host_id": f"urn:uuid:{uuid.uuid4()}", "profiles": {}}
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "r", encoding="utf-8") as f:
                info = os.fstat(f.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise RuntimeError("로그인 파일은 본인 소유·0600 권한이어야 합니다.")
                value = json.load(f)
        except (ValueError, OSError) as exc:
            raise RuntimeError("로그인 저장 파일을 읽을 수 없습니다. 파일 내용은 출력하지 않습니다.") from None
        if (not isinstance(value, dict) or not isinstance(value.get("host_id"), str)
                or not isinstance(value.get("profiles"), dict)):
            raise RuntimeError("로그인 저장 파일 형식이 올바르지 않습니다.")
        return value

    def save(self, value: dict) -> None:
        self._private_directory()
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".session-", delete=False) as f:
                name = f.name
                os.fchmod(f.fileno(), 0o600)
                json.dump(value, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)
