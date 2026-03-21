# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability in Permafrost, please report it responsibly:

1. **Do NOT** open a public GitHub issue
2. Email: [Create a private security advisory on GitHub](https://github.com/dead1786/permafrost/security/advisories/new)
3. Include: description, reproduction steps, potential impact

We will respond within 48 hours and provide a fix timeline.

## Security Architecture

Permafrost implements 6 layers of defense:

### Layer 1: Tool Whitelist
- Only explicitly allowed tools can be executed (64 tools registered)
- Default: strict mode (minimal tools enabled)
- Configurable per security profile: `strict` / `standard` / `relaxed` / `off`
- Blacklist overrides whitelist for extra safety

### Layer 2: File Access Control (ACL)
- AI can only read/write files within allowed directories
- Sensitive paths (`.env`, `secrets/`, `*.pem`, `*.key`, `credentials*`) blocked by default
- Configurable allow/deny lists per security level

### Layer 3: Workspace Boundary Enforcement
- Prevents sandbox escape via symlinks, `..` traversal, or absolute paths
- Resolves all paths to their real location before checking
- Windows-aware (normalizes drive letter case)
- Throws `ValueError` on any escape attempt

### Layer 4: Prompt Injection Detection
- 20+ detection patterns for common injection techniques
- Blocks: role hijacking, instruction override, system prompt extraction, encoding attacks, DAN mode
- Configurable action: `block` (default), `warn`, or `log`
- Custom pattern support via config

### Layer 5: Rate Limiter
- Configurable tools-per-minute and messages-per-minute limits
- Token-per-hour tracking to prevent runaway costs
- Prevents abuse and runaway loops

### Layer 6: Audit Log
- Every security event logged to `~/.permafrost/audit.jsonl`
- Structured JSON entries with timestamp, event type, target, details
- Queryable via `get_recent_audit(n)` API

### Additional Security Features

- **Payload Redaction**: Base64 image data automatically replaced with `<redacted>` in logs (prevents log bloat)
- **Error Classification**: 9-type `FailoverReason` system classifies provider errors (auth/billing/rate_limit/overloaded/timeout/model_not_found/context_overflow/network/unknown) for intelligent fallback decisions
- **Dangerous Command Detection**: Regex patterns catch `rm -rf`, `git push --force`, `drop table`, `curl | sh`, etc.
- **Approval System**: Dangerous operations can require human confirmation via callback

## Security Profiles

| Profile | Tools | File Access | Injection Detection | Rate Limit |
|---------|-------|-------------|-------------------|------------|
| `strict` | 62 whitelisted | Read-only, restricted dirs | ON (block) | 30/min |
| `standard` | Common set | Read/write, project dirs | ON (block) | 30/min |
| `relaxed` | Most tools | Broad access | ON (warn) | 60/min |
| `off` | All tools | Unrestricted | OFF | None |

## Best Practices

1. Always start with `strict` profile and relax only as needed
2. Review audit logs periodically: `~/.permafrost/audit.jsonl`
3. Never store API keys in `config.json` — use environment variables
4. Use Docker secrets for production deployments
5. Do NOT run Permafrost with administrator/root privileges
6. Set workspace boundary to restrict file tool access scope
