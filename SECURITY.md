# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| 0.1.x   | :white_check_mark: |

## Security Architecture

Metodoloji enforces strict security boundaries around agent execution:
- **Gate Key Isolation:** Machine-local HMAC secret (`~/.bmad/gate-key`) generated with 0600 permissions, stored strictly outside repositories.
- **Secret Protection:** Automatic detection and instant blocking (`DENY`) of commands and file contents attempting to expose keys or tokens.
- **Tamper Resistance:** Experiment approvals are signed with HMAC-SHA256 bound to the record claim, measured value, experiment ID, and measurement command.

## Reporting a Vulnerability

If you discover a security vulnerability or a gate bypass flaw within Metodoloji:
1. **Do not create a public GitHub issue.**
2. Email your findings privately to `mail@yunusgungor.com`.
3. Include detailed steps to reproduce the bypass or vulnerability.

We appreciate responsible disclosure and will respond promptly to verify and patch valid findings.
