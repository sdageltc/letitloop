import importlib.util
import os
import platform
import shutil
import sys
import urllib.error
import urllib.request
from typing import Optional

from .config import DEFAULT_RUN_DIR


def print_status(ok: bool, msg: str, is_warning: bool = False) -> None:
    if ok:
        print(f"[\033[92m+\033[0m] {msg}")
    elif is_warning:
        print(f"[\033[93m!\033[0m] {msg}")
    else:
        print(f"[\033[91mx\033[0m] {msg}")


def _probe_endpoint(url: str, timeout: float = 1.5) -> bool:
    """Lightweight reachability probe. Returns True if the endpoint responds or rejects auth (HTTP 200/401/403)."""
    if not (url.startswith("https://") or url.startswith("http://")):
        return False
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "letitloop-doctor/0.1.0"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        # HTTP 400, 401, 403, 404, 405 indicate the host is alive and responding
        return e.code in (200, 400, 401, 403, 404, 405)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def run_env_doctor(run_dir: Optional[str] = None, check_connectivity: bool = False) -> int:
    fatal = False

    # 1. Python & Runtime
    py_version = sys.version_info
    py_ok = py_version >= (3, 11)
    py_msg = f"Python {platform.python_version()} "
    if py_ok:
        print_status(True, py_msg + "(OK)")
    else:
        print_status(False, py_msg + "(Requires >= 3.11)")
        fatal = True

    # Check optional packages aligned with pyproject.toml extras
    has_pytest = importlib.util.find_spec("pytest") is not None
    has_ruff = shutil.which("ruff") is not None or importlib.util.find_spec("ruff") is not None
    has_bandit = importlib.util.find_spec("bandit") is not None
    has_openai = importlib.util.find_spec("openai") is not None
    has_anthropic = importlib.util.find_spec("anthropic") is not None
    has_google = (
        importlib.util.find_spec("google.genai") is not None
        or importlib.util.find_spec("google.generativeai") is not None
    )

    installed_extras = []
    if has_pytest and has_ruff:
        installed_extras.append("dev")
    if has_bandit:
        installed_extras.append("security")
    if has_openai:
        installed_extras.append("openai")
    if has_anthropic:
        installed_extras.append("anthropic")
    if has_google:
        installed_extras.append("google")

    if installed_extras:
        print_status(True, f"Installed Extras: {', '.join(installed_extras)} (OK)")
    else:
        print_status(
            False,
            "Installed Extras: None detected (e.g. pip install -e '.[dev,security,all-providers]')",
            is_warning=True,
        )

    # 2. Storage
    target_run_dir = run_dir or os.environ.get("LIL_RUN_DIR", DEFAULT_RUN_DIR)
    try:
        os.makedirs(target_run_dir, exist_ok=True)
        is_writable = os.access(target_run_dir, os.W_OK)
    except OSError:
        is_writable = False

    if is_writable:
        try:
            total, used, free = shutil.disk_usage(target_run_dir)
            free_gb = free // (2**30)
            print_status(True, f"Storage: {target_run_dir} (Writable, {free_gb} GB free)")
        except OSError:
            print_status(True, f"Storage: {target_run_dir} (Writable)")
    else:
        print_status(False, f"Storage: {target_run_dir} (Not writable)")
        fatal = True

    # 3. Worker Adapter Tooling
    clis = ["claude", "agy", "opencode", "hermes", "cline", "aider", "docker"]
    print("[!] Agent CLIs:")
    for cli in clis:
        path = shutil.which(cli)
        if path:
            print(f"    \033[92m+\033[0m {cli} ({path})")
        else:
            desc = "container sandbox disabled" if cli == "docker" else "optional"
            print(f"    \033[91mx\033[0m {cli} (not found - {desc})")

    # 4. LLM API Keys & Connectivity
    probe_targets = {
        "OPENAI_API_KEY": "https://api.openai.com/v1/models",
        "ANTHROPIC_API_KEY": "https://api.anthropic.com/v1/models",
        "GEMINI_API_KEY": "https://generativelanguage.googleapis.com/v1beta/models",
        "DEEPSEEK_API_KEY": "https://api.deepseek.com/v1/models",
        "LLM_BASE_URL": None,
    }

    configured = []
    for k, url in probe_targets.items():
        if os.environ.get(k):
            if check_connectivity and url:
                reachable = _probe_endpoint(url)
                status_str = "reachable" if reachable else "unreachable / offline"
                configured.append(f"{k} (configured, {status_str})")
            else:
                configured.append(f"{k} (configured)")

    if configured:
        print_status(True, f"Providers: {', '.join(configured)}")
    else:
        print_status(
            False,
            "Providers: No API keys configured in environment (e.g., OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY)",
            is_warning=True,
        )

    if fatal:
        print("\nPrerequisites failed. Please fix the errors above.")
        return 1

    print('\nEverything is ready! Run `lil propose "Your Goal"` to start.')
    return 0
