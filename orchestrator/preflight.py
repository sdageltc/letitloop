"""Preflight checks: required files, commands, output allow-list, secrets/ports."""

import os
import shlex
import subprocess
import json

CHECK_KINDS = {"required_files", "required_commands", "output_allow_list",
               "required_secrets", "local_ports"}


def _check_required_file(path):
    if not os.path.isfile(path):
        return False, f"required file not found: {path}"
    return True, None


def _check_command(name):
    import shutil
    resolved = shutil.which(name)
    if resolved:
        return True, None
    return False, f"command not found: {name}"


def _check_output_allowed(path, allowed_paths, denied_paths, workspace_root):
    from .contract import check_path_allowed
    ok, err = check_path_allowed(path, allowed_paths, denied_paths, workspace_root)
    if not ok:
        return False, err
    return True, None


def run_preflight(contract, workspace_root, run_dir):
    """Run all preflight checks defined implicitly from contract structure.

    Checks:
        - All input files exist
        - All output paths are within allow-list
        - Required commands from acceptance checks are available
        - Declared secrets report unsupported
        - Declared local ports report unsupported

    Returns (passed: bool, results: list[dict], evidence_path: str|None).

    Results list contains dicts with keys: check_id, kind, passed, message.
    """
    results = []
    evidence = {
        "preflight_checks": [],
    }

    allow = contract.allowed_paths()
    deny = contract.denied_paths()

    for inp in contract.inputs:
        if isinstance(inp, str):
            inp = {"path": inp}
        if isinstance(inp, dict) and "path" in inp:
            path = inp["path"]
            full_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
            ok, msg = _check_required_file(full_path)
            results.append({
                "check_id": f"input_file:{path}",
                "kind": "required_files",
                "passed": ok,
                "message": msg or f"input file exists: {path}",
            })

    for out in contract.outputs:
        if isinstance(out, str):
            out = {"path": out}
        if isinstance(out, dict) and "path" in out:
            path = out["path"]
            ok, msg = _check_output_allowed(path, allow, deny, workspace_root)
            results.append({
                "check_id": f"output_allowed:{path}",
                "kind": "output_allow_list",
                "passed": ok,
                "message": msg or f"output path allowed: {path}",
            })

    for check in contract.acceptance_checks:
        if check["kind"] == "command":
            cmd_ident = check.get("command", "")
            try:
                cmd_parts = shlex.split(cmd_ident)
            except ValueError:
                results.append({
                    "check_id": f"command:{cmd_ident}",
                    "kind": "required_commands",
                    "passed": False,
                    "message": f"malformed command string: {cmd_ident!r}",
                })
                continue
            cmd_name = cmd_parts[0] if cmd_parts else cmd_ident
            ok, msg = _check_command(cmd_name)
            results.append({
                "check_id": f"command:{cmd_name}",
                "kind": "required_commands",
                "passed": ok,
                "message": msg or f"command available: {cmd_name}",
            })

    results.append({
        "check_id": "secrets",
        "kind": "required_secrets",
        "passed": True,
        "message": "secret injection unsupported in Phase 1 (skipping)",
    })

    results.append({
        "check_id": "local_ports",
        "kind": "local_ports",
        "passed": True,
        "message": "local port checking unsupported in Phase 1 (skipping)",
    })

    evidence["preflight_checks"] = results
    all_passed = all(r["passed"] for r in results)

    evidence_path = None
    if run_dir:
        os.makedirs(run_dir, exist_ok=True)
        evidence_path = os.path.join(run_dir, "preflight_evidence.json")
        with open(evidence_path, "w", encoding="utf-8") as f:
            json.dump(evidence, f, indent=2, ensure_ascii=False)

    return all_passed, results, evidence_path
