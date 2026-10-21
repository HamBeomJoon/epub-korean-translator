"""Local atomic checkpoint and export adapter (single writer per output folder)."""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

READABLE_DIRECTORY = "번역본"
INTERNAL_DIRECTORY = "_관리"
TRANSLATION_FILES = {"faithful.md", "modern.md", "easy.md"}
INTERNAL_FILES = {"plan.json", "source.preview.json", "source.json", "aligned.json",
                  "review_report.json", "review_report.html", "usage.json", "manifest.json", ".cache", ".usage", "generations"}


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}-", suffix=".tmp", delete=False) as f:
            name = f.name
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def write_json_atomic(path: Path, obj: Any) -> None:
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


class FileRepository:
    def __init__(self, output: Path):
        self.output = output
        self.last_migration: list[str] = []

    @property
    def internal(self) -> Path:
        return self.output / INTERNAL_DIRECTORY

    @property
    def readable(self) -> Path:
        return self.output / READABLE_DIRECTORY

    def artifact_path(self, name: str) -> Path:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("출력 이름은 출력 폴더 안의 상대 경로여야 합니다.")
        if name in TRANSLATION_FILES:
            return self.readable / name
        # Book-level indexes remain at the book root; their paths are relative to that root.
        if len(relative.parts) == 1 and (name == "book.json" or (name.startswith("book.sample-") and name.endswith(".json"))):
            return self.output / name
        return self.internal / relative

    def organize(self) -> list[str]:
        """Move legacy artifacts unchanged. Caller holds the stable root lock."""
        book_root = self.output.parent.parent if self.output.name.startswith("sample-") else self.output.parent
        indexes = list(book_root.glob("book.sample-*.json"))
        if (book_root / "book.json").exists():
            indexes.append(book_root / "book.json")
        guard = FileRepository(book_root).lock(organize=False) if indexes else nullcontext()
        with guard:
            updates = []
            for index in indexes:
                value = json.loads(index.read_text(encoding="utf-8"))
                changed = False
                prefix = self.output.relative_to(book_root).as_posix() + "/"
                for chapter in value.get("chapters", []):
                    for field in ("manifest", "aligned"):
                        old = chapter.get(field)
                        if not isinstance(old, str) or not old.startswith(prefix):
                            continue
                        suffix = old[len(prefix):]
                        if suffix == "manifest.json" or suffix.startswith("generations/"):
                            chapter[field] = prefix + INTERNAL_DIRECTORY + "/" + suffix
                            changed = True
                if changed:
                    updates.append((index, value))
            moved = self._organize_files()
            for index, value in updates:
                write_json_atomic(index, value)
            return moved

    def _organize_files(self) -> list[str]:
        moves = []
        for directory in (self.internal, self.readable):
            if directory.is_symlink():
                raise RuntimeError(f"출력 정리 대상에 심볼릭 링크를 사용할 수 없습니다: {directory}")
        for name in sorted(TRANSLATION_FILES | INTERNAL_FILES):
            source = self.output / name
            if not source.exists() and not source.is_symlink():
                continue
            target = self.artifact_path(name)
            if source.is_symlink() or target.exists() or target.is_symlink():
                raise RuntimeError(f"출력 정리 충돌: {source} -> {target}. 기존 파일을 덮어쓰지 않습니다.")
            moves.append((source, target))
        # Check every collision before moving any file. An interrupted move can be resumed.
        self.last_migration = []
        for source, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise RuntimeError(f"출력 정리 도중 대상 파일이 생겼습니다: {target}")
            source.rename(target)
            self.last_migration.append(source.name)
        return self.last_migration

    @contextmanager
    def lock(self, *, organize: bool = True):
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / ".pipeline.lock").open("a+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"같은 출력 폴더를 다른 프로세스가 사용 중: {self.output}") from exc
            try:
                if organize:
                    self.organize()
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def load_cache(self, key: str) -> Any:
        path = self.internal / ".cache" / f"{key}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (ValueError, UnicodeError):
            logging.warning("손상된 캐시 무시: %s", path)
            return None

    def save_cache(self, key: str, value: Any) -> None:
        write_json_atomic(self.internal / ".cache" / f"{key}.json", value)

    def record_attempt(self, attempt_id: str, value: dict) -> None:
        write_json_atomic(self.internal / ".usage" / f"{attempt_id}.json", value)

    def attempts(self) -> list[dict]:
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((self.internal / ".usage").glob("*.json"))]

    def write(self, name: str, value: Any) -> None:
        write_json_atomic(self.artifact_path(name), value)

    def read_review_data(self) -> tuple[dict, dict]:
        """Prefer the published snapshot; never mix it with possibly stale convenience copies."""
        if self.internal.is_symlink():
            raise ValueError('보고서 관리 폴더에 심볼릭 링크를 사용할 수 없습니다.')
        base = self.internal if any((self.internal / name).exists() for name in ('manifest.json', 'aligned.json')) else self.output
        def read(name):
            if not isinstance(name, str) or not name:
                raise ValueError('보고서 입력 파일의 상대 경로가 필요합니다.')
            relative = Path(name)
            path = base / relative
            if relative.is_absolute() or '..' in relative.parts or not path.resolve().is_relative_to(base.resolve()):
                raise ValueError('보고서 입력은 장 출력 폴더 안의 파일이어야 합니다.')
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError(f'보고서 JSON 객체가 필요합니다: {path}')
            return value
        manifest = read('manifest.json') if (base / 'manifest.json').exists() else None
        aligned = read(manifest['aligned'] if manifest is not None else 'aligned.json')
        report = read(manifest['review'] if manifest is not None else 'review_report.json')
        if manifest is not None and aligned.get('revision') != manifest.get('revision'):
            raise ValueError('발행 manifest와 원문·번역 revision이 다릅니다.')
        return aligned, report

    def write_text(self, name: str, value: str) -> None:
        write_text_atomic(self.artifact_path(name), value)
