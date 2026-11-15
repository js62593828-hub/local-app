# -*- coding: utf-8 -*-
"""구글 드라이브 동기화 폴더에서 안전하게 파일을 쓰는 도우미.

G:\\내 드라이브 같은 동기화 폴더는 드라이브 프로그램이 파일을 잠깐 잡고 있을 때
쓰기가 'OSError: [Errno 22] Invalid argument' 로 실패할 수 있다(11/10 주간 실행이 이것으로 중단됨).
- write_text_retry: 임시 파일에 쓰고 바꿔치기, 실패하면 잠깐 쉬었다가 몇 번 다시 시도한다.
- SafeLog: 실행 기록을 컴퓨터 안(동기화 안 되는 폴더)에 먼저 쓰고, 드라이브 쪽 기록 파일에는 모아서 옮겨 적는다.
  드라이브 쪽 쓰기가 실패해도 실행은 멈추지 않고, 다음에 다시 옮겨 적는다.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

RETRY_WAITS = (0.5, 1, 2, 3, 5, 8)


def write_text_retry(path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for wait in (0,) + RETRY_WAITS:
        if wait:
            time.sleep(wait)
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        try:
            tmp.write_text(text, encoding=encoding)
            os.replace(tmp, path)
            return
        except OSError as e:
            last = e
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    # 바꿔치기가 계속 안 되면 마지막으로 직접 덮어쓰기
    try:
        path.write_text(text, encoding=encoding)
        return
    except OSError:
        raise last


def local_log_dir() -> Path:
    """동기화되지 않는 컴퓨터 안 폴더 (Windows: %LOCALAPPDATA%\\local-app-logs, 그 외: 임시 폴더)."""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    d = Path(base) / "local-app-logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


class SafeLog:
    """한 줄씩 받아서 (1) 컴퓨터 안 기록 파일에 바로 쓰고 (2) 드라이브 쪽 기록 파일에는 flush_every 초마다 모아서 옮겨 적는다."""

    def __init__(self, drive_path, flush_every: float = 3.0):
        self.drive_path = Path(drive_path)
        self.local_path = local_log_dir() / self.drive_path.name
        self.local = self.local_path.open("a", encoding="utf-8")
        self.pending, self.last_flush, self.flush_every = [], 0.0, flush_every
        self.failures = 0

    def write(self, line: str) -> None:
        line = line.rstrip("\r\n") + "\n"
        try:
            self.local.write(line)
            self.local.flush()
        except OSError:
            pass
        self.pending.append(line)
        if time.time() - self.last_flush >= self.flush_every:
            self.flush()

    def flush(self, final: bool = False) -> bool:
        if not self.pending:
            return True
        waits = (0,) + (RETRY_WAITS if final else ())
        for wait in waits:
            if wait:
                time.sleep(wait)
            try:
                self.drive_path.parent.mkdir(parents=True, exist_ok=True)
                with self.drive_path.open("a", encoding="utf-8") as f:
                    f.write("".join(self.pending))
                self.pending = []
                self.last_flush = time.time()
                return True
            except OSError:
                self.failures += 1
        self.last_flush = time.time()      # 실패하면 다음 주기에 다시 시도 (실행은 계속)
        return False

    def close(self) -> None:
        ok = self.flush(final=True)
        if not ok:
            msg = (f"[주의] 드라이브 쪽 기록 파일({self.drive_path})에 마지막 {len(self.pending)}줄을 옮겨 적지 못했습니다. "
                   f"전체 기록은 {self.local_path} 에 있습니다.\n")
            try:
                self.local.write(msg)
            except OSError:
                pass
            print(msg, file=sys.stderr)
        try:
            self.local.close()
        except OSError:
            pass
