# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability in Permafrost, please report it responsibly:

1. **Do NOT** open a public GitHub issue
2. Email: [Create a private security advisory on GitHub](https://github.com/dead1786/permafrost/security/advisories/new)
3. Include: description, reproduction steps, potential impact

We will respond within 48 hours and provide a fix timeline.

## Security Architecture

Permafrost implements 4 layers of defense:

### Layer 1: Tool Whitelist
- Only explicitly allowed tools can be executed
- Default: strict mode (minimal tools enabled)
- Configurable per security profile: `strict` / `standard` / `relaxed` / `off`

### Layer 2: File Access Control (ACL)
- AI can only read/write files within allowed directories
- Sensitive paths (`.env`, `secrets/`, system files) are blocked by default
- Configurable allow/deny lists

### Layer 3: Prompt Injection Detection
- 20+ detection patterns for common injection techniques
- Scans all incoming messages before processing
- Blocks: role hijacking, instruction override, system prompt extraction, encoding attacks
- Logs all detected attempts to audit log

### Layer 4: Rate Limiter
- Configurable requests-per-minute limit
- Prevents abuse and runaway loops
- Per-channel rate limiting

## Security Profiles

| Profile | Tools | File Access | Injection Detection | Rate Limit |
|---------|-------|-------------|-------------------|------------|
| `strict` | Minimal | Read-only, restricted dirs | ON | 10/min |
| `standard` | Common set | Read/write, project dirs | ON | 30/min |
| `relaxed` | Most tools | Broad access | ON | 60/min |
| `off` | All tools | Unrestricted | OFF | None |

## Audit Log

All security events are logged to `~/.permafrost/audit.log`:
- Tool execution attempts (allowed/denied)
- File access attempts (allowed/denied)
- Prompt injection detections
- Rate limit triggers

## Best Practices

1. Always start with `strict` profile and relax only as needed
2. Review audit logs periodically
3. Never store API keys in `config.json` — use environment variables
4. Use Docker secrets for production deployments
