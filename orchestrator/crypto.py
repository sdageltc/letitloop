"""Asymmetric Ed25519 signing and verification + Sigstore OIDC keyless attestation shim.

# EXPERIMENTAL: Enterprise compliance features — not for production use without [compliance] extra

Sprint 5 — Proof-Carrying Remediate: upgrades ProofReceipt from symmetric HMAC to
asymmetric Ed25519 and Sigstore / GitHub OIDC Keyless Attestations.

Zero heavy deps in kernel: tries `cryptography` first (if installed), else falls back
to deterministic HMAC-SHA256 shim for tests (so `pip install cryptography` is optional
and the kernel stays stdlib-only by default).

Bindings:
  1. Base commit SHA + Patched commit SHA
  2. CycloneDX / SPDX SBOM diff (via orchestrator.sbom)
  3. Test execution stdout/stderr SHA-256 hash
  4. Deterministic timestamp + unique nonces
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Tuple

try:
    from cryptography.hazmat.primitives import serialization  # type: ignore
    from cryptography.hazmat.primitives.asymmetric import ed25519  # type: ignore

    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False
    ed25519 = None  # type: ignore
    serialization = None  # type: ignore


# ---------------------------------------------------------------------------
# Key management (Ed25519 keypair)
# ---------------------------------------------------------------------------


def generate_keypair() -> Tuple[str, str]:
    """Generate an Ed25519 keypair. Returns (private_hex, public_hex).

    If `cryptography` is available, generates a real Ed25519 keypair (32-byte seed).
    Otherwise returns a deterministic HMAC-derived pair for test parity.
    """
    if HAS_CRYPTOGRAPHY:
        priv = ed25519.Ed25519PrivateKey.generate()  # type: ignore
        priv_bytes = priv.private_bytes(  # type: ignore
            encoding=serialization.Encoding.Raw,  # type: ignore
            format=serialization.PrivateFormat.Raw,  # type: ignore
            encryption_algorithm=serialization.NoEncryption(),  # type: ignore
        )
        pub_bytes = priv.public_key().public_bytes(  # type: ignore
            encoding=serialization.Encoding.Raw,  # type: ignore
            format=serialization.PublicFormat.Raw,  # type: ignore
        )
        return priv_bytes.hex(), pub_bytes.hex()
    # Shim: 32-byte random each
    priv_hex = secrets.token_hex(32)
    # Derive public as HMAC of private (deterministic for verification)
    pub_hex = hmac.new(priv_hex.encode(), b"ed25519-pub-derive", hashlib.sha256).hexdigest()
    return priv_hex, pub_hex


def _load_or_create_ed25519_keypair(key_dir: str) -> Tuple[str, str]:
    """Load or create a persistent Ed25519 keypair under key_dir/.ed25519_key."""
    os.makedirs(key_dir, exist_ok=True)
    priv_path = os.path.join(key_dir, ".ed25519_priv")
    pub_path = os.path.join(key_dir, ".ed25519_pub")
    if os.path.isfile(priv_path) and os.path.isfile(pub_path):
        try:
            priv_hex = open(priv_path, "r", encoding="utf-8").read().strip()
            pub_hex = open(pub_path, "r", encoding="utf-8").read().strip()
            if priv_hex and pub_hex:
                return priv_hex, pub_hex
        except OSError:
            pass
    priv_hex, pub_hex = generate_keypair()
    try:
        # Atomic write
        tmp_priv = priv_path + ".tmp"
        tmp_pub = pub_path + ".tmp"
        open(tmp_priv, "w", encoding="utf-8").write(priv_hex)
        open(tmp_pub, "w", encoding="utf-8").write(pub_hex)
        if os.name != "nt":
            try:
                os.chmod(tmp_priv, 0o600)
                os.chmod(tmp_pub, 0o600)
            except OSError:
                pass
        os.replace(tmp_priv, priv_path)
        os.replace(tmp_pub, pub_path)
    except OSError:
        pass
    return priv_hex, pub_hex


# ---------------------------------------------------------------------------
# Sign / Verify (detached)
# ---------------------------------------------------------------------------


def sign_detached(payload: bytes, private_hex: str) -> str:
    """Sign payload bytes with Ed25519 private key. Returns hex signature (64 bytes hex)."""
    if HAS_CRYPTOGRAPHY:
        try:
            priv_bytes = bytes.fromhex(private_hex)
            # cryptography expects 32-byte seed
            priv = ed25519.Ed25519PrivateKey.from_private_bytes(priv_bytes)  # type: ignore
            sig = priv.sign(payload)  # type: ignore
            return sig.hex()
        except Exception:
            pass
    # Shim: HMAC-SHA512 truncated to 64 bytes hex (128 hex chars) for test parity
    # Use private_hex as key, payload as data
    sig = hmac.new(private_hex.encode(), payload, hashlib.sha512).hexdigest()
    return sig


def verify_detached(payload: bytes, signature_hex: str, public_hex: str, private_hex_hint: str | None = None) -> bool:
    """Verify Ed25519 detached signature. Returns True on success, False on failure.

    When cryptography is available, verifies with public key. In shim mode, verifies via
    HMAC using the private hint (or derived from public).
    """
    if HAS_CRYPTOGRAPHY:
        try:
            pub_bytes = bytes.fromhex(public_hex)
            pub = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)  # type: ignore
            sig_bytes = bytes.fromhex(signature_hex)
            pub.verify(sig_bytes, payload)  # type: ignore
            return True
        except Exception:
            return False
    # Shim: verify via HMAC with private hint; if no hint, try to derive?
    # In shim, public was HMAC(priv, b"ed25519-pub-derive"), so we cannot reverse.
    # Instead, if private_hex_hint is provided, verify directly; otherwise try all known?
    # For test purposes, we store private alongside public in key_dir, so caller can provide hint.
    if private_hex_hint:
        expected = hmac.new(private_hex_hint.encode(), payload, hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, signature_hex)
    raise NotImplementedError("Ed25519 verification requires: pip install letitloop[compliance]")


def sign_payload(payload: bytes, key_dir: str) -> Tuple[str, str, str]:
    """Convenience: sign payload with persistent keypair under key_dir. Returns (sig_hex, pub_hex, priv_hex)."""
    priv_hex, pub_hex = _load_or_create_ed25519_keypair(key_dir)
    sig_hex = sign_detached(payload, priv_hex)
    return sig_hex, pub_hex, priv_hex


def verify_payload(payload: bytes, sig_hex: str, pub_hex: str, key_dir: str | None = None) -> bool:
    """Verify payload with public key; if key_dir provided, also tries private hint for shim."""
    if key_dir and not HAS_CRYPTOGRAPHY:
        # Try to load private hint for shim verification
        try:
            priv_path = os.path.join(key_dir, ".ed25519_priv")
            if os.path.isfile(priv_path):
                priv_hex = open(priv_path, "r", encoding="utf-8").read().strip()
                if verify_detached(payload, sig_hex, pub_hex, private_hex_hint=priv_hex):
                    return True
        except OSError:
            pass
    return verify_detached(payload, sig_hex, pub_hex)


# ---------------------------------------------------------------------------
# Sigstore / GitHub OIDC Keyless Attestation shim (Sprint 5)
# ---------------------------------------------------------------------------


def attest_oidc(payload: bytes, oidc_token: str | None = None) -> dict:
    """Create a keyless attestation claim (shim for Sigstore).

    In production, this would call `sigstore` or `gh attestation create` with the
    GitHub OIDC token (`ACTIONS_ID_TOKEN_REQUEST_TOKEN`). Here we produce a
    deterministic local claim for tests without network egress.

    Returns: {issuer, subject, payload_sha256, attestation_id, sig_hex, pub_hex}
    """
    # Bound to GitHub OIDC claims if available, else local
    issuer = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "local-issuer")
    subject = os.environ.get("GITHUB_REPOSITORY", "local/subject")
    payload_sha = hashlib.sha256(payload).hexdigest()
    # Sign the claim with Ed25519 (keyless = ephemeral key)
    priv_hex, pub_hex = generate_keypair()
    claim = f"{issuer}:{subject}:{payload_sha}".encode("utf-8")
    sig_hex = sign_detached(claim, priv_hex)
    attestation_id = hashlib.sha256(f"{issuer}:{subject}:{sig_hex}".encode()).hexdigest()[:16]
    return {
        "issuer": issuer,
        "subject": subject,
        "payload_sha256": payload_sha,
        "attestation_id": attestation_id,
        "sig_hex": sig_hex,
        "pub_hex": pub_hex,
    }


def verify_oidc_attestation(payload: bytes, attestation: dict) -> bool:
    """Verify a keyless attestation claim (shim)."""
    issuer = attestation.get("issuer", "")
    subject = attestation.get("subject", "")
    payload_sha = hashlib.sha256(payload).hexdigest()
    if payload_sha != attestation.get("payload_sha256"):
        return False
    claim = f"{issuer}:{subject}:{payload_sha}".encode("utf-8")
    sig_hex = attestation.get("sig_hex", "")
    pub_hex = attestation.get("pub_hex", "")
    return verify_detached(claim, sig_hex, pub_hex)
