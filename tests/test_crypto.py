"""Unit tests for orchestrator.crypto — Ed25519 signing and verification."""

import pytest
from orchestrator.crypto import (
    generate_keypair,
    sign_detached,
    verify_detached,
    verify_payload,
)

pytestmark = pytest.mark.fast


def test_crypto_keypair_generation():
    priv, pub = generate_keypair()
    assert isinstance(priv, str) and len(priv) >= 32
    assert isinstance(pub, str) and len(pub) >= 32


def test_crypto_sign_and_verify_shim(monkeypatch):
    # Force shim mode (HAS_CRYPTOGRAPHY = False)
    monkeypatch.setattr("orchestrator.crypto.HAS_CRYPTOGRAPHY", False)

    priv, pub = generate_keypair()
    payload = b"hello compliance"
    sig = sign_detached(payload, priv)

    # Without hint -> raises NotImplementedError
    with pytest.raises(NotImplementedError) as exc_info:
        verify_detached(payload, sig, pub)
    assert "pip install letitloop[compliance]" in str(exc_info.value)

    # With hint -> succeeds in shim mode
    assert verify_detached(payload, sig, pub, private_hex_hint=priv) is True

    # Bad signature with hint -> False
    assert verify_detached(payload, "0" * len(sig), pub, private_hex_hint=priv) is False


def test_verify_payload_with_keydir(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.crypto.HAS_CRYPTOGRAPHY", False)
    from orchestrator.crypto import sign_payload

    key_dir = str(tmp_path / "keys")
    payload = b"test payload"
    sig, pub, priv = sign_payload(payload, key_dir)

    # verify_payload passes key_dir so it finds the private hint for shim
    assert verify_payload(payload, sig, pub, key_dir=key_dir) is True

    # without key_dir and without cryptography -> raises NotImplementedError
    with pytest.raises(NotImplementedError):
        verify_payload(payload, sig, pub, key_dir=None)
