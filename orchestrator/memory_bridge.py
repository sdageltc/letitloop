"""Durable cross-subagent memory staging facility."""

import json
import os
import time
from typing import Any, List, Optional

LOCK_TTL_SEC = 35.0
STALE_TTL_SEC = 30.0


def _pid_alive(pid):
    """Return True if a process with the given pid is alive.

    Windows: os.kill(pid, 0) is unreliable (returns success for dead pids),
    so use OpenProcess. POSIX: signal 0 probe.
    """
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            open_process = kernel32.OpenProcess
            open_process.restype = ctypes.c_void_p
            open_process.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
            handle = open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, AttributeError):
        return False


class MemoryBridge:
    """Durable JSONL memory staging facility with cross-process locking.

    Concurrency (QC 2026-08-02, P1-4): the lock is a sibling file holding
    {pid, created_at}. A lock is stolen ONLY when its owner pid is verifiably
    dead (or the file is corrupt/older than STALE_TTL) — never while the owner
    lives, even if mtime is old. _release_lock() removes the lock only if the
    stored pid is still ours, so a slow writer can never delete another
    writer's lock. append() raises TimeoutError if the lock cannot be acquired
    within LOCK_TTL_SEC.
    """

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.lock_path = f"{self.path}.lock"

    def _read_lock(self):
        try:
            with open(self.lock_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, TypeError):
            return None

    def _steal_stale_lock(self) -> bool:
        """Remove the lock iff its owner is dead, or it is corrupt/absent and
        older than STALE_TTL_SEC. Returns True if removed (caller retries)."""
        data = self._read_lock()
        try:
            if data is None:
                if time.time() - os.path.getmtime(self.lock_path) > STALE_TTL_SEC:
                    os.remove(self.lock_path)
                    return True
                return False
            if not isinstance(data, dict):
                os.remove(self.lock_path)
                return True
            owner = data.get("pid")
            if isinstance(owner, int) and _pid_alive(owner):
                return False
            os.remove(self.lock_path)
            return True
        except OSError:
            return False

    def _acquire_lock(self) -> None:
        lock_dir = os.path.dirname(self.lock_path)
        if lock_dir:
            os.makedirs(lock_dir, exist_ok=True)
        deadline = time.time() + LOCK_TTL_SEC
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(), "created_at": time.time()}, f)
                    f.flush()
                    os.fsync(f.fileno())
                return
            except FileExistsError:
                if self._steal_stale_lock():
                    continue
                if time.time() >= deadline:
                    raise TimeoutError(
                        f"timed out acquiring memory bridge lock for {self.path}"
                    )
                time.sleep(0.01)

    def _release_lock(self) -> None:
        """Remove the lock only if we still own it.

        Atomic claim via rename: if the lock was stolen/replaced meanwhile, the
        renamed file's pid will differ and we restore it untouched.
        """
        tmp = f"{self.lock_path}.{os.getpid()}.{int(time.time() * 1000)}.tmp"
        try:
            os.rename(self.lock_path, tmp)
        except OSError:
            return
        data = self._read_lock_with(tmp)
        if data and data.get("pid") != os.getpid():
            try:
                os.rename(tmp, self.lock_path)
            except OSError:
                pass
            return
        try:
            os.remove(tmp)
        except OSError:
            pass

    @staticmethod
    def _read_lock_with(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, TypeError):
            return None

    def append(self, entry: dict) -> int:
        """Append one JSON line atomically with cross-process locking and fsync.

        Returns 1-based line number of the appended entry. Raises TimeoutError
        if the lock is held by a live process for longer than LOCK_TTL_SEC.
        """
        self._acquire_lock()
        try:
            line_count = 0
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                    line_count = sum(1 for _ in f)

            parent_dir = os.path.dirname(self.path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
                f.flush()
                os.fsync(f.fileno())

            return line_count + 1
        finally:
            self._release_lock()

    def read(self, scope: Optional[str] = None, limit: Optional[int] = None) -> List[dict]:
        """Read entries from JSONL file in chronological order (newest last).

        Ignores torn or malformed lines. If scope is specified, filters by scope.
        If limit is specified, returns only the most recent N entries.
        """
        if not os.path.exists(self.path):
            return []

        entries: List[dict] = []
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue

                if not isinstance(data, dict):
                    continue

                if scope is not None:
                    if data.get("scope") == scope:
                        entries.append(data)
                else:
                    entries.append(data)

        if limit is not None and limit > 0:
            entries = entries[-limit:]

        return entries

    def read_last(self, scope: Optional[str] = None) -> Optional[dict]:
        """Return the most recent matching entry or None."""
        entries = self.read(scope=scope)
        return entries[-1] if entries else None
