# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability within letitloop, please report it via [GitHub Security Advisories](https://github.com/sdageltc/letitloop/security/advisories) or reach out directly on LinkedIn at [https://www.linkedin.com/in/oguzhankayan/](https://www.linkedin.com/in/oguzhankayan/). All security vulnerabilities will be promptly addressed.

Please include the following information in your report:

- Type of vulnerability (e.g. buffer overflow, SQL injection, cross-site scripting, etc.)
- Full paths of source file(s) related to the vulnerability
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit it

This information will help us triage your report more quickly.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Security Update Process

1. **Report received**: We acknowledge receipt of your vulnerability report within 48 hours.

2. **Assessment**: Our security team will assess the vulnerability and determine its impact.

3. **Fix development**: We will develop a fix for the vulnerability.

4. **Release**: We will release a security update as soon as possible.

5. **Disclosure**: We will publicly disclose the vulnerability after the fix is released.

## Security Best Practices

### API Key Management

- Never commit API keys to version control
- Use environment variables for all secrets
- Rotate API keys regularly
- Use least-privilege access when possible

### Configuration

- Use the scrubbed environment feature for command execution
- Enable safety checks in production
- Use scope restrictions to limit file access
- Enable audit logging for sensitive operations

### Deployment

- Run the orchestrator with minimal system privileges
- Use containerization when possible
- Monitor for unusual activity
- Keep dependencies updated

## Security Features

### Environment Scrubbing

The orchestrator automatically scrubs sensitive environment variables before executing commands:

```python
from orchestrator.verifier import _get_scrubbed_env

# This removes API keys and secrets from the environment
env = _get_scrubbed_env()
```

### Scope Restrictions

Use workspace scope to limit file access:

```python
from orchestrator.contract import Contract

contract = Contract({
    "workspace_scope": {
        "allow": ["scratch/"],
        "deny": ["AGENTS.md", "memory/", ".opencode/"]
    }
})
```

### Safety Checks

Enable safety checks for critical operations:

```python
from orchestrator.safety import run_safety_checks

report = run_safety_checks(plan, workspace_root)
if not report.passed:
    raise SafetyError("Safety checks failed")
```

## Vulnerability Disclosure Policy

- We follow responsible disclosure practices
- We request 90 days to address vulnerabilities before public disclosure
- We credit reporters in our security advisories (unless they prefer anonymity)
- We do not take legal action against researchers who follow this policy

## Contact

For security-related inquiries, please contact:

- GitHub Security Advisories: https://github.com/sdageltc/letitloop/security/advisories
- LinkedIn: https://www.linkedin.com/in/oguzhankayan/

## Acknowledgments

We would like to thank all security researchers and contributors for responsibly disclosing vulnerabilities.

Thank you for helping keep letitloop and its users safe!
