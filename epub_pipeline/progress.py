"""Terminal progress adapter; periodic refresh is restricted to interactive stderr."""
import sys
import threading
import time

from .domain import ProgressEvent
from .prompts import STYLE_NAMES


def format_duration(seconds: float) -> str:
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return (f'{hours}시간 ' if hours else '') + f'{minutes}분 {seconds_part}초'


class TerminalProgress:
    def __init__(self, chapter: int, position: int, chapters: int, started: float,
                 *, enabled: bool = True, stream=None):
        self.chapter, self.position, self.chapters, self.started = chapter, position, chapters, started
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = enabled
        try:
            self.live = enabled and self.stream.isatty()
        except (OSError, ValueError):
            self.enabled, self.live = False, False
        self.event: ProgressEvent | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._line_open = False

    def _render(self):
        if not self.enabled:
            return
        chapter = '프롤로그' if self.chapter == 0 else f'{self.chapter}장'
        parts = [f'[선택 {self.position}/{self.chapters} · {chapter}]']
        event = self.event
        if event is None:
            parts.append('작업 준비')
        else:
            if event.style:
                parts.append(STYLE_NAMES[event.style])
            parts.append({'translation': '번역', 'review': 'AI 검수', 'saving': '결과 저장'}[event.stage])
            if event.stage != 'saving':
                parts.append(f'현재 배치 {event.batch}/{event.batches}')
            parts.append(f'완료 작업 {event.completed}/{event.total} · 캐시 {event.cache_hits}')
            if event.phase == 'retry_wait':
                parts.append(f'재시도 대기 {event.wait_seconds:g}초 (시도 {event.attempt})')
            elif event.phase == 'request':
                parts.append(f'요청 중 (시도 {event.attempt})')
            elif event.phase == 'cached':
                parts.append('캐시 재사용')
        parts.append(f'경과 {format_duration(time.monotonic() - self.started)}')
        line = ' · '.join(parts)
        try:
            self.stream.write(('\r\033[2K' + line) if self.live else (line + '\n'))
            self.stream.flush()
            self._line_open = self.live
        except (OSError, ValueError):
            self.enabled = False

    def update(self, event: ProgressEvent):
        with self._lock:
            self.event = event
            self._render()

    def _refresh(self):
        while not self._stop.wait(1):
            with self._lock:
                self._render()

    def __enter__(self):
        with self._lock:
            self._render()
        if self.live:
            self._thread = threading.Thread(target=self._refresh, name='epub-progress', daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self._line_open:
            try:
                self.stream.write('\n')
                self.stream.flush()
            except (OSError, ValueError):
                pass
