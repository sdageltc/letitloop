"""Verified QC overrule — tamper-evidence binding to already-recorded output.

The verifier does NOT re-execute any command and does not touch the
workspace. It binds operator-supplied overrule evidence to the
verification evidence the supervisor already recorded
(<task_dir>/verification_evidence.json). Pure function, no side effects.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import List, Tuple


def verify_overrule(
    evidence: dict,
    secret: str,
    verification_evidence: dict,
) -> Tuple[bool, List[str]]:
    """Verify operator overrule evidence against recorded verification output.

    The out-of-band secret is validated by the caller (supervisor) against
    the run-scoped secret hash BEFORE this function is invoked; here we only
    require the evidence to carry a non-empty secret field so the binding is
    structurally complete. The following must all hold:

      1. evidence is a dict with a non-empty string 'secret' field.
      2. verification_evidence is a dict with a verification_results list.
      3. evidence.check_id references an existing verification result.
      4. sha256(recorded stdout) == evidence.stdout_hash (64 hex chars).
      5. evidence.assertions is a non-empty list of non-empty strings.

    Returns (True, []) on success, else (False, errors).
    """
    errors: List[str] = []

    if not isinstance(evidence, dict):
        return False, ["evidence must be a dict"]
    if not isinstance(secret, str) or not secret.strip():
        return False, ["secret must be a non-empty string"]

    if not isinstance(verification_evidence, dict):
        return False, ["verification_evidence must be a dict"]
    results = verification_evidence.get("verification_results")
    if not isinstance(results, list):
        return False, ["verification_evidence.verification_results must be a list"]

    check_id = evidence.get("check_id")
    if not isinstance(check_id, str) or not check_id:
        errors.append("evidence.check_id must be a non-empty string")

    recorded_result = None
    for result in results:
        if isinstance(result, dict) and result.get("check_id") == check_id:
            recorded_result = result
            break
    if recorded_result is None:
        errors.append(f"check_id {check_id!r} not found in verification results")

    stdout_hash = evidence.get("stdout_hash")
    if not isinstance(stdout_hash, str) or len(stdout_hash) != 64:
        errors.append("evidence.stdout_hash must be a 64-char sha256 hex digest")
    else:
        try:
            bytes.fromhex(stdout_hash)
        except ValueError:
            errors.append("evidence.stdout_hash is not valid hex")
            stdout_hash = None

    assertions = evidence.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        errors.append("evidence.assertions must be a non-empty list")
    elif not all(isinstance(a, str) and a.strip() for a in assertions):
        errors.append("every assertion must be a non-empty string")

    if recorded_result is not None and stdout_hash:
        recorded_stdout = recorded_result.get("stdout", "")
        if not isinstance(recorded_stdout, str):
            recorded_stdout = str(recorded_stdout)
        recorded_hash = hashlib.sha256(recorded_stdout.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(recorded_hash, stdout_hash):
            errors.append("stdout_hash does not match recorded verification stdout")

    if errors:
        return False, errors
    return True, []
