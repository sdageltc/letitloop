import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


class ProcessLifecycleGuard:
    """Manages cross-platform bottom-up process tree termination."""

    def __init__(self, pid: int):
        self.pid = pid

    def inject_kill(self, signal_type: str = "SIGKILL") -> bool:
        if HAS_PSUTIL:
            try:
                parent = psutil.Process(self.pid)
                children = parent.children(recursive=True)

                # 1. Terminate children bottom-up (leaves first)
                for child in reversed(children):
                    try:
                        if signal_type == "SIGKILL":
                            child.kill()
                        else:
                            child.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                # 2. Terminate parent process
                try:
                    if signal_type == "SIGKILL":
                        parent.kill()
                    else:
                        parent.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

                # 3. Wait for full tree termination to prevent orphan locks
                procs = children + [parent]
                gone, alive = psutil.wait_procs(procs, timeout=3.0)

                # Force kill any lingering processes
                for p in alive:
                    try:
                        p.kill()
                    except Exception:
                        pass
                return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return False

        # Fallback without psutil
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                sig = signal.SIGKILL if signal_type == "SIGKILL" else signal.SIGTERM
                os.kill(self.pid, sig)
            return True
        except Exception:
            return False


class PhaseSentinelWatcher:
    """Watches process stdout stream asynchronously with multi-phase query support."""

    def __init__(self, process_stdout, default_phase_regex: Optional[str] = None):
        self.stdout = process_stdout
        self.default_pattern = re.compile(default_phase_regex) if default_phase_regex else None
        self.line_queue = queue.Queue()

        self.reader_thread = threading.Thread(target=self._enqueue_output, daemon=True)
        self.reader_thread.start()

    def _enqueue_output(self):
        try:
            for line in iter(self.stdout.readline, ""):
                if not line:
                    break
                self.line_queue.put(line)
        except Exception:
            pass

    def wait_for_phase(self, target_phase_regex: Optional[str] = None, timeout_seconds: float = 2.0) -> bool:
        pattern = re.compile(target_phase_regex) if target_phase_regex else self.default_pattern
        if not pattern:
            raise ValueError("No target regex pattern provided.")

        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                remaining_time = max(0.01, timeout_seconds - (time.time() - start_time))
                line = self.line_queue.get(timeout=min(0.02, remaining_time))
                if pattern.search(line):
                    return True
            except queue.Empty:
                continue
        return False
