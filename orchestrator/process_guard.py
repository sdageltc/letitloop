"""Cross-platform process-tree orphan protection for worker and verifier subprocesses.

Guarantees that child AND grandchild processes spawned during contract execution are
terminated when a timeout fires, when the supervisor exits, or on SIGINT/SIGTERM.

Strategy:
- Windows: children are assigned to a kernel Job Object with
  JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE so descendants die atomically even if the
  direct child refuses to die; ``taskkill /F /T`` is used for explicit tree kills.
- POSIX: children are started with ``start_new_session=True`` so the whole tree
  shares a process group that can be signalled with ``os.killpg(SIGKILL)``.

Stdlib-only (ctypes on Windows); no psutil/pywin32 dependency.
"""

from __future__ import annotations

import atexit
import errno
import os
import signal
import subprocess  # nosec B404
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

IS_WINDOWS = os.name == "nt"

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFO_CLASS = 9
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


# ---------------------------------------------------------------------------
# Low-level platform primitives
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def pid_alive(pid: int) -> bool:
    """Return True when *pid* refers to a live process (safe liveness probe)."""
    if pid is None or pid <= 0:
        return False
    if IS_WINDOWS:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno != errno.EPERM
    return True


def _windows_new_job_object() -> Optional[Any]:
    """Create a kill-on-close Job Object; returns the handle or None on failure."""
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFO_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        kernel32.CloseHandle(job)
        return None
    return job


def _windows_assign_to_job(job_handle: Any, proc: subprocess.Popen) -> bool:
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    try:
        return bool(kernel32.AssignProcessToJobObject(job_handle, int(proc._handle)))  # noqa: SLF001
    except Exception:
        return False


def containment_kwargs() -> Dict[str, Any]:
    """Pre-spawn kwargs giving the child its own process group/session on POSIX."""
    if IS_WINDOWS:
        return {}
    return {"start_new_session": True}


def attach_containment(proc: subprocess.Popen) -> Optional[Any]:
    """Post-spawn OS containment for a child (Windows Job Object assignment)."""
    if IS_WINDOWS:
        job = _windows_new_job_object()
        if job is not None and not _windows_assign_to_job(job, proc):
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.CloseHandle(job)
            return None
        return job
    return None


def close_job_handle(job_handle: Any) -> None:
    """Close a Job Object handle, triggering KILL_ON_JOB_CLOSE reaping. Never raises."""
    if job_handle is None:
        return
    if IS_WINDOWS:
        try:
            ctypes.windll.kernel32.CloseHandle(job_handle)  # type: ignore[attr-defined]
        except Exception:
            pass


def kill_process_tree(pid: int) -> None:
    """Terminate *pid* and all of its descendants. Never raises."""
    if pid is None or pid <= 0:
        return
    if IS_WINDOWS:
        try:
            subprocess.run(  # nosec B603 B607
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except Exception:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_TERMINATE = 0x0001
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
            if handle:
                try:
                    kernel32.TerminateProcess(handle, 137)
                finally:
                    kernel32.CloseHandle(handle)
        return
    # POSIX: prefer killing the whole group (children share it via setsid).
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    try:
        if pgid is not None and pgid != os.getpgrp():
            os.killpg(pgid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# ProcessGuard registry
# ---------------------------------------------------------------------------


class ProcessGuard:
    """Registry of supervised subprocesses with guaranteed tree cleanup."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: List[subprocess.Popen] = []
        self._jobs: List[Any] = []

    def register(self, proc: subprocess.Popen, job_handle: Optional[Any] = None) -> subprocess.Popen:
        with self._lock:
            self._procs.append(proc)
            if job_handle is not None:
                self._jobs.append(job_handle)
        return proc

    def unregister(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if proc in self._procs:
                self._procs.remove(proc)

    def spawn(self, args: Iterable[str], **kwargs: Any) -> subprocess.Popen:
        """Popen with full OS containment (session leader on POSIX, job object on Windows)."""
        popen_kwargs = dict(kwargs)
        popen_kwargs.update(containment_kwargs())
        proc = subprocess.Popen(list(args), **popen_kwargs)  # nosec B603
        job = attach_containment(proc)
        self.register(proc, job)
        return proc

    def sweep(self, timeout: float = 5.0) -> None:
        """Kill every registered process tree and reap what we can."""
        with self._lock:
            procs = list(self._procs)
            jobs = list(self._jobs)
            self._procs.clear()
            self._jobs.clear()
        for proc in procs:
            if proc.poll() is None:
                kill_process_tree(proc.pid)
        deadline = time.time() + timeout
        for proc in procs:
            remaining = max(0.05, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
            except Exception:
                pass
        if IS_WINDOWS:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            for job in jobs:
                # Closing the kill-on-close handle makes the kernel reap survivors.
                kernel32.CloseHandle(job)

    def __enter__(self) -> "ProcessGuard":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.sweep()


DEFAULT_GUARD = ProcessGuard()


# ---------------------------------------------------------------------------
# Signal / exit hooks
# ---------------------------------------------------------------------------

_installed_lock = threading.Lock()
_installed = False
_previous_handlers: Dict[int, Any] = {}


def _make_signal_handler(guard: ProcessGuard, signum: int):
    def handler(received_signum, frame):  # type: ignore[no-untyped-def]
        guard.sweep()
        previous = _previous_handlers.get(signum)
        if callable(previous) and previous not in (signal.SIG_DFL, signal.SIG_IGN):
            previous(received_signum, frame)
            return
        if received_signum == signal.SIGINT:
            raise KeyboardInterrupt
        # SIGTERM and friends: mimic default termination.
        signal.signal(received_signum, signal.SIG_DFL)
        os.kill(os.getpid(), received_signum)

    return handler


def _atexit_sweep(guard: ProcessGuard) -> None:
    try:
        guard.sweep(timeout=2.0)
    except Exception:
        pass


def install_signal_handlers(guard: ProcessGuard = None) -> bool:  # type: ignore[assignment]
    """Register atexit + SIGINT/SIGTERM(+SIGHUP) sweeps. Idempotent. True when installed now."""
    global _installed
    guard = guard or DEFAULT_GUARD
    with _installed_lock:
        if _installed:
            return False
        signals = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGHUP"):
            signals.append(signal.SIGHUP)
        for signum in signals:
            try:
                _previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, _make_signal_handler(guard, signum))
            except (ValueError, OSError):
                # Not the main thread of the main interpreter; skip this signal.
                _previous_handlers.pop(signum, None)
        atexit.register(_atexit_sweep, guard)
        _installed = True
        return True


def uninstall_signal_handlers() -> None:
    """Restore handlers captured by install_signal_handlers (test support)."""
    global _installed
    with _installed_lock:
        if not _installed:
            return
        for signum, previous in _previous_handlers.items():
            try:
                signal.signal(signum, previous)
            except (ValueError, OSError):
                pass
        _previous_handlers.clear()
        _installed = False


__all__ = [
    "DEFAULT_GUARD",
    "ProcessGuard",
    "attach_containment",
    "close_job_handle",
    "containment_kwargs",
    "install_signal_handlers",
    "kill_process_tree",
    "pid_alive",
    "uninstall_signal_handlers",
]
