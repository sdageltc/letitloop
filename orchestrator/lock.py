"""Lock file — prevents concurrent execution of the same goal."""

import hashlib
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

LOCK_FILENAME = ".goal.lock"
STALE_TIMEOUT_SEC = 300  # 5 minutes


class LockError(Exception):
    """Raised when lock cannot be acquired."""


class LockHeldError(LockError):
    """Raised when another process holds the lock."""


class LockStaleError(LockHeldError):
    """Raised when lock is held but appears stale."""


class FileLock:
    """Cross-process file lock (best-effort) for concurrent ledger writes.

    Uses an exclusive lock file with O_CREAT|O_EXCL and a bounded wait loop.
    On Windows there is no flock(); the atomic-create + stale-PID check is the
    pragmatic portable approach. Raises LockHeldError if not acquired within
    `timeout_sec`.

    With `stale_steal=True` (default), a lock file whose holder is dead on this
    host — or whose heartbeat/`created_at` is older than `STALE_TIMEOUT_SEC` —
    is transparently auto-stolen rather than waiting the full `timeout_sec`.
    This mirrors `acquire_lock()`'s lock-v2 stale-steal and prevents a 120s
    merge-admission deadlock when a crash leaves a stale `.merge_admission.lock`.
    """

    def __init__(self, path: str, timeout_sec: float = 30.0, poll_sec: float = 0.1, stale_steal: bool = True):
        self.path = path
        self.timeout_sec = timeout_sec
        self.poll_sec = poll_sec
        self.stale_steal = stale_steal
        self._acquired = False

    def _lock_file_is_stale(self) -> bool:
        """Return True if the lock file at self.path is stale (holder dead or aged)."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lock = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # Unparseable lock file is not provably stale — do not steal.
            return False
        if not isinstance(lock, dict):
            return False

        lock_pid = lock.get("pid")
        lock_host = lock.get("hostname")
        if lock_host == socket.gethostname() and isinstance(lock_pid, int) and lock_pid > 0:
            expected_token = lock.get("process_start_token")
            if expected_token is not None and not _pid_alive(lock_pid, expected_token):
                return True
            if expected_token is None and not _pid_alive(lock_pid):
                return True

        reference = lock.get("heartbeat") or lock.get("created_at")
        if not isinstance(reference, str) or not reference:
            return True
        try:
            ref_dt = datetime.fromisoformat(reference)
            age = (datetime.now(timezone.utc) - ref_dt).total_seconds()
            return age > STALE_TIMEOUT_SEC
        except (ValueError, TypeError):
            return True

    def acquire(self) -> None:
        deadline = time.monotonic() + self.timeout_sec
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "pid": os.getpid(),
                            "hostname": socket.gethostname(),
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        },
                        f,
                    )
                self._acquired = True
                return
            except FileExistsError:
                if self.stale_steal and self._lock_file_is_stale():
                    # Lock-v2 semantics: transparently auto-steal dead/stale locks
                    # instead of waiting out the full timeout.
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    # Re-run the race-check loop: if another process already
                    # re-created the lock, we loop again and either steal (if
                    # stale) or keep waiting.
                    continue
                if time.monotonic() >= deadline:
                    raise LockHeldError(f"File lock not acquired within {self.timeout_sec}s: {self.path}")
                time.sleep(self.poll_sec)

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            os.remove(self.path)
        except OSError:
            pass
        self._acquired = False

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def _process_start_token(pid: int) -> Optional[str]:
    """Return a stable PID start-time token.

    The token is deliberately opaque. It is compared only with a token
    produced by the same platform implementation. Returning None means
    that the platform cannot establish process identity.
    """
    if not isinstance(pid, int) or pid <= 0:
        return None

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                pid,
            )
            if not handle:
                return None
            try:
                creation = wintypes.FILETIME()
                exit_time = wintypes.FILETIME()
                kernel_time = wintypes.FILETIME()
                user_time = wintypes.FILETIME()
                ok = ctypes.windll.kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel_time),
                    ctypes.byref(user_time),
                )
                if not ok:
                    return None
                exit_code = wintypes.DWORD()
                if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    if exit_code.value != 259:  # STILL_ACTIVE
                        return None
                value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
                return f"win:{value}"
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError, TypeError, ValueError):
            return None

    if sys.platform == "darwin":
        try:
            import subprocess

            out = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "lstart="],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1,
            ).strip()
            if out:
                return f"mac:{out}"
        except (subprocess.SubprocessError, OSError, ValueError):
            pass

    proc_stat = f"/proc/{pid}/stat"
    try:
        with open(proc_stat, "r", encoding="utf-8") as f:
            line = f.read()
        closing_paren = line.rfind(")")
        if closing_paren >= 0:
            fields = line[closing_paren + 2 :].split()
            # The suffix begins at field 3; starttime is field 22.
            if len(fields) > 19:
                return f"proc:{fields[19]}"
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        pass

    try:
        import psutil

        return f"psutil:{psutil.Process(pid).create_time():.6f}"
    except Exception:
        return None


def _pid_alive(pid: int, expected_start_token: Optional[str] = None) -> bool:
    if not pid or pid <= 0:
        return False

    is_alive = False
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            ERROR_ACCESS_DENIED = 5
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                err = ctypes.GetLastError()
                is_alive = err == ERROR_ACCESS_DENIED
            else:
                try:
                    exit_code = wintypes.DWORD()
                    if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                        is_alive = exit_code.value == STILL_ACTIVE
                    else:
                        is_alive = True
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError, TypeError, ValueError):
            is_alive = False
    else:
        try:
            os.kill(pid, 0)
            is_alive = True
        except ProcessLookupError:
            is_alive = False
        except PermissionError:
            is_alive = True
        except OSError:
            is_alive = False

    if not is_alive:
        return False

    if expected_start_token is not None:
        actual_start_token = _process_start_token(pid)
        return actual_start_token is not None and actual_start_token == expected_start_token

    return True


def _lock_path(run_dir: str) -> str:
    return os.path.join(run_dir, LOCK_FILENAME)


def _read_lock(run_dir: str) -> Optional[dict]:
    path = _lock_path(run_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _build_lock_payload(goal_id: str) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    cmd_str = " ".join(sys.argv) if hasattr(sys, "argv") else ""
    cmdline_hash = hashlib.sha256(cmd_str.encode("utf-8", errors="replace")).hexdigest()[:8]
    payload = {
        "goal_id": goal_id,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "created_at": now_iso,
        "heartbeat": now_iso,
        "cmdline_hash": cmdline_hash,
    }
    start_token = _process_start_token(os.getpid())
    if start_token is not None:
        payload["process_start_token"] = start_token
    return payload


def _write_lock(run_dir: str, goal_id: str) -> dict:
    os.makedirs(run_dir, exist_ok=True)
    lock_data = _build_lock_payload(goal_id)
    path = _lock_path(run_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(lock_data, f, indent=2)
    return lock_data


def _remove_lock(run_dir: str) -> None:
    path = _lock_path(run_dir)
    try:
        os.remove(path)
    except (FileNotFoundError, OSError):
        pass


def _lock_is_stale(run_dir: str) -> bool:
    lock = _read_lock(run_dir)
    if lock is None:
        return False
    lock_pid = lock.get("pid")
    lock_host = lock.get("hostname")
    if lock_host == socket.gethostname() and lock_pid:
        expected_token = lock.get("process_start_token")
        if expected_token is not None:
            if not _pid_alive(lock_pid, expected_token):
                return True
        elif not _pid_alive(lock_pid):
            return True

    # Heartbeat is the authoritative activity signal. created_at is used
    # only for legacy locks that have no heartbeat.
    reference = lock.get("heartbeat") or lock.get("created_at")
    if not reference:
        return True
    try:
        ref_dt = datetime.fromisoformat(reference)
        age = (datetime.now(timezone.utc) - ref_dt).total_seconds()
        return age > STALE_TIMEOUT_SEC
    except (ValueError, TypeError):
        return True


def touch_lock_heartbeat(run_dir: str) -> None:
    lock = _read_lock(run_dir)
    if lock and lock.get("pid") == os.getpid():
        lock["heartbeat"] = datetime.now(timezone.utc).isoformat()
        path = _lock_path(run_dir)
        tmp_path = path + f".tmp.{os.getpid()}_{time.time_ns()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(lock, f, indent=2)
            os.replace(tmp_path, path)
        except OSError:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


def acquire_lock(goal_id: str, run_dir: str, force: bool = False) -> dict:
    """Acquire execution lock for a goal.

    Uses atomic file creation (O_CREAT|O_EXCL) to prevent TOCTOU races.
    Falls back to read-then-decide if the lock file already exists.

    Args:
        goal_id: Unique goal identifier.
        run_dir: Run directory where lock file lives.
        force: If True, force-acquire by removing stale or existing lock.

    Returns:
        Lock data dict.

    Raises:
        LockHeldError: Another process holds a non-stale lock.
    """
    os.makedirs(run_dir, exist_ok=True)
    lock_path = _lock_path(run_dir)
    lock_data = _build_lock_payload(goal_id)

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(lock_data, f, indent=2)
        return lock_data
    except FileExistsError:
        pass

    existing = _read_lock(run_dir)
    if existing is None:
        _remove_lock(run_dir)
        return acquire_lock(goal_id, run_dir, force=False)
    if force:
        _remove_lock(run_dir)
        return acquire_lock(goal_id, run_dir, force=False)
    if _lock_is_stale(run_dir):
        # Lock v2: Transparently auto-steal dead / stale locks without raising LockStaleError
        time.sleep(0.005)
        if _lock_is_stale(run_dir):
            _remove_lock(run_dir)
            stolen_data = _build_lock_payload(goal_id)
            stolen_data["adopted_from"] = f"{existing.get('pid')}@{existing.get('hostname')}"
            path = _lock_path(run_dir)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(stolen_data, f, indent=2)
            return stolen_data
    raise LockHeldError(
        f"Goal '{goal_id}' is already locked by PID {existing.get('pid', '?')} "
        f"on {existing.get('hostname', '?')} (since {existing.get('created_at', '?')}). "
        "Wait for it to complete or use force=True."
    )


class LockHeartbeatDaemon:
    """Daemon thread periodically touching goal lock heartbeat."""

    def __init__(self, run_dir: str, interval_sec: float = 5.0):
        self.run_dir = run_dir
        self.interval_sec = interval_sec
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_sec):
            touch_lock_heartbeat(self.run_dir)


def _read_lock_snapshot(run_dir: str):
    """Return (raw_bytes, parsed_lock), or (None, None) if unavailable."""
    path = _lock_path(run_dir)
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return raw, json.loads(raw.decode("utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None


def release_lock(run_dir: str) -> bool:
    """Release the current process's lock without deleting a replacement lock.

    acquire_lock() returns the lock-data dict written to the lock file. Callers
    may retain that dict as an ownership token, although release_lock() also
    protects itself by comparing the lock bytes immediately before unlinking.

    Returns True if this process's unchanged lock was released, otherwise False.
    """
    path = _lock_path(run_dir)
    first_raw, lock = _read_lock_snapshot(run_dir)
    if first_raw is None or lock is None:
        return False

    lock_pid = lock.get("pid")
    if lock_pid is not None and lock_pid != os.getpid():
        return False

    second_raw, current_lock = _read_lock_snapshot(run_dir)
    if second_raw is None or current_lock is None or second_raw != first_raw or current_lock != lock:
        return False

    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def is_locked(run_dir: str) -> bool:
    """Check if a lock exists (regardless of staleness)."""
    return _read_lock(run_dir) is not None


def is_locked_by_other(run_dir: str) -> bool:
    """Check if another process (PID+hostname) holds the lock."""
    lock = _read_lock(run_dir)
    if lock is None:
        return False
    lock_pid = lock.get("pid")
    lock_host = lock.get("hostname")
    if lock_pid is None:
        return False
    if lock_pid != os.getpid():
        return True
    if lock_host and lock_host != socket.gethostname():
        return True
    return False


def lock_info(run_dir: str) -> Optional[dict]:
    """Return lock info dict, or None if not locked."""
    return _read_lock(run_dir)
