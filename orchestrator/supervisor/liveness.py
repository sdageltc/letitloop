"""orchestrator/supervisor/liveness.py — Zero-daemon process supervisor with crash recovery and liveness.

Bridges the gap between state durability (WAL persistence) and process liveness
(auto-restarting on SIGKILL / unhandled crash until completion).

Invariants enforced:
1. Signal Classification: SIGINT (130) and SIGTERM (143) halt immediately without restart.
2. Rapid-Failure Circuit Breaker: Catches deterministic syntax/logic bugs (<5s uptime x 3 attempts) and halts.
3. Dual-OS Process Encapsulation: Win32 Job Objects on Windows, process groups on POSIX.
4. Zero-Pipe Deadlock: Inherited stdio streaming.
5. Isolated State Boundary: Does not acquire or contend for the worker runtime lock.
"""

from __future__ import annotations

import functools
import os
import random
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional


class CircuitBreakerError(RuntimeError):
    """Raised when consecutive rapid failures exceed the threshold."""


class SupervisorError(RuntimeError):
    """Raised on unrecoverable supervisor failure."""


# Signal exit codes
EXIT_CLEAN = 0
EXIT_SIGINT = 130
EXIT_SIGKILL = 137
EXIT_SIGSEGV = 139
EXIT_SIGTERM = 143


def _is_graceful_exit(exit_code: int) -> bool:
    """Return True if exit code represents intentional user/system termination."""
    return exit_code in (
        EXIT_CLEAN,
        EXIT_SIGINT,
        EXIT_SIGTERM,
        -signal.SIGINT,
        -signal.SIGTERM if hasattr(signal, "SIGTERM") else -15,
    )


def _setup_win32_job() -> Optional[Any]:
    """Create a Windows Job Object configured to terminate all child processes on close."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        CreateJobObjectW = kernel32.CreateJobObjectW
        CreateJobObjectW.restype = wintypes.HANDLE
        CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)

        SetInformationJobObject = kernel32.SetInformationJobObject
        SetInformationJobObject.restype = wintypes.BOOL
        SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)

        job = CreateJobObjectW(None, None)
        if not job:
            return None

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
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
                ("PeakProcessMemoryLimit", ctypes.c_size_t),
                ("PeakJobMemoryLimit", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        success = SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        return job if success else None
    except Exception:
        return None


def _assign_process_to_job(job_handle: Any, proc: subprocess.Popen) -> None:
    """Assign a Windows child process to the active Job Object."""
    if sys.platform != "win32" or not job_handle:
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        OpenProcess = kernel32.OpenProcess
        OpenProcess.restype = wintypes.HANDLE
        OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)

        AssignProcessToJobObject = kernel32.AssignProcessToJobObject
        AssignProcessToJobObject.restype = wintypes.BOOL
        AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)

        # PROCESS_ALL_ACCESS = 0x1F0FFF
        h_proc = OpenProcess(0x1F0FFF, False, proc.pid)
        if h_proc:
            AssignProcessToJobObject(job_handle, h_proc)
    except Exception:
        pass


class LivenessSupervisor:
    """Zero-daemon process supervisor enforcing crash recovery with rapid-failure circuit breaking."""

    def __init__(
        self,
        command: List[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        max_restarts: int = 5,
        backoff: float = 1.0,
        max_backoff: float = 30.0,
        jitter: float = 0.2,
        healthy_threshold_sec: float = 5.0,
        max_rapid_failures: int = 3,
        silent: bool = False,
    ):
        self.command = command
        self.cwd = cwd or os.getcwd()
        self.env = (env or os.environ).copy()
        self.max_restarts = max_restarts
        self.backoff = backoff
        self.max_backoff = max_backoff
        self.jitter = jitter
        self.healthy_threshold_sec = healthy_threshold_sec
        self.max_rapid_failures = max_rapid_failures
        self.silent = silent

        self.restart_count = 0
        self.rapid_failure_count = 0
        self.current_process: Optional[subprocess.Popen] = None
        self._interrupted = False
        self._job_handle = _setup_win32_job() if sys.platform == "win32" else None

    def _log(self, message: str) -> None:
        if not self.silent:
            print(f"[LetItLoop Supervisor] {message}", file=sys.stderr, flush=True)

    def _calculate_backoff(self, attempt: int) -> float:
        """Exponential backoff with randomized jitter."""
        delay = min(self.backoff * (2 ** (attempt - 1)), self.max_backoff)
        jitter_delta = random.uniform(-self.jitter * delay, self.jitter * delay)
        return max(0.1, delay + jitter_delta)

    def _terminate_child(self) -> None:
        """Cleanly terminate child and grandchild processes."""
        if not self.current_process or self.current_process.poll() is not None:
            return

        proc = self.current_process
        try:
            if sys.platform != "win32":
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except OSError:
                    proc.terminate()
            else:
                proc.terminate()

            proc.wait(timeout=3.0)
        except (subprocess.TimeoutExpired, OSError):
            try:
                if sys.platform != "win32":
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.kill()
            except OSError:
                pass

    def run(self) -> int:
        """Run supervised execution loop until completion, graceful exit, or circuit break."""
        self.env["LETITLOOP_SUPERVISED"] = "1"

        def _handle_signal(signum, frame):
            self._interrupted = True
            self._log(f"Received termination signal ({signum}). Shutting down child cleanly...")
            self._terminate_child()
            sys.exit(EXIT_SIGINT if signum == signal.SIGINT else EXIT_SIGTERM)

        prev_sigint = signal.signal(signal.SIGINT, _handle_signal)
        prev_sigterm = signal.signal(signal.SIGTERM, _handle_signal) if hasattr(signal, "SIGTERM") else None

        try:
            while True:
                if self._interrupted:
                    return EXIT_SIGINT

                start_time = time.monotonic()
                popen_kwargs: Dict[str, Any] = {
                    "cwd": self.cwd,
                    "env": self.env,
                    "stdout": None,
                    "stderr": None,
                }

                if sys.platform == "win32":
                    popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    popen_kwargs["start_new_session"] = True

                self.current_process = subprocess.Popen(self.command, **popen_kwargs)
                if self._job_handle:
                    _assign_process_to_job(self._job_handle, self.current_process)

                try:
                    exit_code = self.current_process.wait()
                except KeyboardInterrupt:
                    self._log("KeyboardInterrupt caught by supervisor. Halting.")
                    self._terminate_child()
                    return EXIT_SIGINT

                duration = time.monotonic() - start_time

                # 1. Clean or Graceful Exit (Zero restarts)
                if _is_graceful_exit(exit_code):
                    if exit_code == EXIT_CLEAN:
                        self._log(f"Process completed successfully (exit code 0 in {duration:.2f}s).")
                    else:
                        self._log(f"Process halted gracefully on signal (exit code {exit_code}).")
                    return exit_code

                # 2. Check Rapid-Failure Circuit Breaker
                if duration < self.healthy_threshold_sec:
                    self.rapid_failure_count += 1
                else:
                    self.rapid_failure_count = 0

                if self.rapid_failure_count >= self.max_rapid_failures:
                    self._log(
                        f"CIRCUIT BREAKER TRIPPED: Child failed {self.rapid_failure_count} times in <{self.healthy_threshold_sec}s. "
                        "Halting to prevent runaway CPU spin on fatal syntax/logic error."
                    )
                    raise CircuitBreakerError(
                        f"Circuit breaker tripped after {self.rapid_failure_count} rapid consecutive crashes (last exit code {exit_code})."
                    )

                # 3. Check Max Restarts
                if self.restart_count >= self.max_restarts:
                    self._log(f"Maximum restart limit reached ({self.max_restarts}). Halting.")
                    return exit_code

                self.restart_count += 1
                backoff_delay = self._calculate_backoff(self.restart_count)
                self._log(
                    f"Crash detected (exit code {exit_code}). Resuming via WAL in {backoff_delay:.2f}s... (Restart {self.restart_count}/{self.max_restarts})"
                )
                time.sleep(backoff_delay)

        finally:
            self._terminate_child()
            signal.signal(signal.SIGINT, prev_sigint)
            if prev_sigterm and hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, prev_sigterm)


def supervise(
    target: Optional[Callable[..., Any]] = None,
    *args: Any,
    max_restarts: int = 5,
    backoff: float = 1.0,
    max_backoff: float = 30.0,
    healthy_threshold_sec: float = 5.0,
    max_rapid_failures: int = 3,
    **kwargs: Any,
) -> Any:
    """Programmatic supervisor / decorator: executes target in a supervised subprocess, auto-restarting on crash."""

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def _wrapper(*f_args: Any, **f_kwargs: Any) -> Any:
            if os.environ.get("LETITLOOP_SUPERVISED") == "1":
                return fn(*f_args, **f_kwargs)

            script_path = sys.argv[0]
            if not os.path.isabs(script_path):
                script_path = os.path.abspath(script_path)

            cmd = [sys.executable, script_path] + sys.argv[1:]
            sup = Supervisor(
                command=cmd,
                max_restarts=max_restarts,
                backoff=backoff,
                max_backoff=max_backoff,
                healthy_threshold_sec=healthy_threshold_sec,
                max_rapid_failures=max_rapid_failures,
            )
            exit_code = sup.run()
            if exit_code != 0:
                sys.exit(exit_code)
            return exit_code

        return _wrapper

    if target is None:
        return _decorator

    if callable(target):
        if os.environ.get("LETITLOOP_SUPERVISED") == "1":
            return target(*args, **kwargs)

        script_path = sys.argv[0]
        if not os.path.isabs(script_path):
            script_path = os.path.abspath(script_path)

        cmd = [sys.executable, script_path] + sys.argv[1:]
        sup = Supervisor(
            command=cmd,
            max_restarts=max_restarts,
            backoff=backoff,
            max_backoff=max_backoff,
            healthy_threshold_sec=healthy_threshold_sec,
            max_rapid_failures=max_rapid_failures,
        )
        exit_code = sup.run()
        if exit_code != 0:
            sys.exit(exit_code)
        return exit_code

    raise TypeError("supervise() target must be callable")


Supervisor = LivenessSupervisor
