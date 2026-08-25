"""Tamper-evident evidence receipts — HMAC-sealed artifacts per run.

Every run directory gets a symmetric key (``.receipt_key``, mode 0600 on
POSIX). Artifacts are sealed by writing ``<path>.sig`` containing the
HMAC-SHA256 digest of the file contents. Verification fails closed on
tampering, missing signatures, or a missing key.
"""

import hashlib
import hmac
import os
import secrets

RECEIPT_KEY_FILENAME = ".receipt_key"


def _key_path(run_dir: str) -> str:
    return os.path.join(run_dir, RECEIPT_KEY_FILENAME)


def load_or_create_run_key(run_dir: str) -> str:
    """Load (or create) the run-scoped signing key. Stable across the run."""
    os.makedirs(run_dir, exist_ok=True)
    path = _key_path(run_dir)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(key)
    if os.name != "nt":
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, path)
    return key


def _sign(body: bytes, key: str) -> str:
    return hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()


def seal_artifact(path: str, key: str) -> str:
    """Write ``<path>.sig`` containing the HMAC of the artifact. Returns sig path."""
    with open(path, "rb") as f:
        body = f.read()
    sig_path = path + ".sig"
    tmp = sig_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(_sign(body, key))
    os.replace(tmp, sig_path)
    return sig_path


def verify_artifact(path: str, key: str) -> bool:
    """Fail-closed verification: False on tampering, missing sig, or missing key."""
    sig_path = path + ".sig"
    if not os.path.isfile(path) or not os.path.isfile(sig_path):
        return False
    try:
        with open(path, "rb") as f:
            body = f.read()
        with open(sig_path, "r", encoding="utf-8") as f:
            expected = f.read().strip()
    except OSError:
        return False
    return hmac.compare_digest(_sign(body, key), expected)
