# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |

## Security Boundaries

Metodoloji enforces policy at the hook points exposed by the configured agent runtime. It is not an operating-system sandbox: tools or write paths that do not pass through a registered hook are outside the gate's control. Runtime-specific coverage and known gaps are documented in [Runtime security coverage](docs/runtime-security-coverage.md).

Within those boundaries, the plugin provides:
- **Gate Key Isolation:** Machine-local HMAC secret (`~/.bmad/gate-key`) generated with 0600 permissions, stored strictly outside repositories.
- **Secret Protection:** Automatic detection and instant blocking (`DENY`) of commands and file contents attempting to expose keys or tokens.
- **Tamper Resistance:** Experiment approvals are signed with HMAC-SHA256 bound to the record claim, measured value, experiment ID, and measurement command.

The gate protects against accidental or agent-mediated policy violations through supported hooks. It does not defend against a malicious user, a compromised runtime, direct filesystem access outside the hook, or runtime tools whose writes cannot be inspected. Configure runtime-specific controls where broader enforcement is required.

## Reporting a Vulnerability

If you discover a security vulnerability or a gate bypass flaw within Metodoloji:
1. **Do not create a public GitHub issue.**
2. Email your findings privately to `mail@yunusgungor.com`.
3. Include detailed steps to reproduce the bypass or vulnerability.

We appreciate responsible disclosure and will respond promptly to verify and patch valid findings.
