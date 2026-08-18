import os
import sys
import shutil
import platform
import urllib.request
import urllib.error

def print_status(ok: bool, msg: str, is_warning: bool = False):
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
    
    # Check some dependencies (rudimentary check if they can be imported)
    try:
        import pytest
        has_dev = True
    except ImportError:
        has_dev = False
        
    try:
        import litellm
        has_providers = True
    except ImportError:
        has_providers = False

    deps_msg = "Dependencies: "
    if has_dev and has_providers:
        print_status(True, deps_msg + "dev, providers (OK)")
    else:
        missing = []
        if not has_dev: missing.append("dev")
        if not has_providers: missing.append("providers")
        print_status(False, deps_msg + f"Missing extras: {', '.join(missing)}", is_warning=True)

    # 2. Storage
    from .cli import DEFAULT_RUN_DIR
    try:
        os.makedirs(DEFAULT_RUN_DIR, exist_ok=True)
        is_writable = os.access(DEFAULT_RUN_DIR, os.W_OK)
    except Exception:
        is_writable = False
    
    if is_writable:
        try:
            total, used, free = shutil.disk_usage(DEFAULT_RUN_DIR)
            free_gb = free // (2**30)
            print_status(True, f"Storage: {DEFAULT_RUN_DIR} (Writable, {free_gb} GB free)")
        except Exception:
            print_status(True, f"Storage: {DEFAULT_RUN_DIR} (Writable)")
    else:
        print_status(False, f"Storage: {DEFAULT_RUN_DIR} (Not writable)")
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
    keys = {
        "OPENAI_API_KEY": "https://api.openai.com/v1/models",
        "ANTHROPIC_API_KEY": "https://api.anthropic.com/v1/models",
        "GEMINI_API_KEY": "https://generativelanguage.googleapis.com/v1beta/models",
        "DEEPSEEK_API_KEY": "https://api.deepseek.com/v1/models",
        "LLM_BASE_URL": None
    }
    
    configured = []
    for k, url in keys.items():
        if os.environ.get(k):
            configured.append(k)
    
    if configured:
        print_status(True, f"Providers: {', '.join([c + ' (configured)' for c in configured])}")
    else:
        print_status(False, "Providers: No API keys configured (e.g., OPENAI_API_KEY, ANTHROPIC_API_KEY)", is_warning=True)
    
    if fatal:
        print("\nPrerequisites failed. Please fix the errors above.")
        return 1
        
    print("\nEverything is ready! Run `lil propose \"Your Goal\"` to start.")
    return 0
