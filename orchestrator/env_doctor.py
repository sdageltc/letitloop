import importlib.util
import os
import platform
import shutil
import sys


def print_status(ok: bool, msg: str, is_warning: bool = False) -> None:
    if ok:
        print(f"[\033[92m+\033[0m] {msg}")
    elif is_warning:
        print(f"[\033[93m!\033[0m] {msg}")
    else:
        print(f"[\033[91mx\033[0m] {msg}")


def run_env_doctor() -> int:
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

    # Check dependencies via importlib
    has_dev = importlib.util.find_spec("pytest") is not None
    has_bandit = importlib.util.find_spec("bandit") is not None

    deps_msg = "Dependencies: "
    if has_dev and has_bandit:
        print_status(True, deps_msg + "dev, security (OK)")
    else:
        missing = []
        if not has_dev:
            missing.append("dev (pytest)")
        if not has_bandit:
            missing.append("security (bandit)")
        print_status(False, deps_msg + f"Missing extras: {', '.join(missing)}", is_warning=True)

    # 2. Storage
    from .cli import DEFAULT_RUN_DIR

    run_dir = os.environ.get("LIL_RUN_DIR", DEFAULT_RUN_DIR)
    try:
        os.makedirs(run_dir, exist_ok=True)
        is_writable = os.access(run_dir, os.W_OK)
    except OSError:
        is_writable = False

    if is_writable:
        try:
            total, used, free = shutil.disk_usage(run_dir)
            free_gb = free // (2**30)
            print_status(True, f"Storage: {run_dir} (Writable, {free_gb} GB free)")
        except OSError:
            print_status(True, f"Storage: {run_dir} (Writable)")
    else:
        print_status(False, f"Storage: {run_dir} (Not writable)")
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

    # 4. LLM API Keys
    keys = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "LLM_BASE_URL"]

    configured = [k for k in keys if os.environ.get(k)]

    if configured:
        print_status(True, f"Providers: {', '.join([c + ' (configured)' for c in configured])}")
    else:
        print_status(
            False,
            "Providers: No API keys configured (e.g., OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY)",
            is_warning=True,
        )

    if fatal:
        print("\nPrerequisites failed. Please fix the errors above.")
        return 1

    print('\nEverything is ready! Run `lil propose "Your Goal"` to start.')
    return 0
