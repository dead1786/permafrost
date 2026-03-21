"""
Permafrost Security — Permission system, prompt injection detection, and audit logging.

Defense layers:
  1. Tool whitelist — only approved tools can be called
  2. File ACL — restrict which paths AI can read/write/execute
  3. Dangerous operation approval — high-risk actions require human confirmation
  4. Prompt injection detection — block attempts to override system prompt
  5. Rate limiter — prevent runaway loops and token abuse
  6. Audit log — every action recorded for review

Default mode: STRICT (deny by default, allow by exception)
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from enum import Enum
from typing import Optional

log = logging.getLogger("permafrost.security")


# ── Security Levels ──────────────────────────────────────────────

class SecurityLevel(Enum):
    STRICT = "strict"       # Deny by default, explicit whitelist only
    STANDARD = "standard"   # Common tools allowed, dangerous ops need approval
    RELAXED = "relaxed"     # Most tools allowed, only block known-dangerous
    OFF = "off"             # No restrictions (development only, NOT recommended)


# ── Default Policies ─────────────────────────────────────────────

DEFAULT_TOOL_WHITELIST = {
    "strict": [
        "read_file", "write_file", "edit_file", "append_file",
        "list_files", "grep_files", "bash",
        "search_web", "web_fetch", "http_request",
        "python_exec", "get_datetime", "calculate",
        "json_read", "json_write",
        "memory_save", "memory_note", "memory_search",
        "memory_list", "memory_gc", "memory_reindex", "memory_stats",
        "set_reminder", "list_reminders", "delete_reminder",
        "send_notification", "create_tool",
        "create_pdf", "create_spreadsheet", "read_spreadsheet",
        "create_document", "read_pdf", "read_image", "resize_image",
    ],
    "standard": [
        "read_file", "write_file", "edit_file", "search", "list_files",
        "web_search", "web_fetch", "run_command",
    ],
    "relaxed": [
        "read_file", "write_file", "edit_file", "search", "list_files",
        "web_search", "web_fetch", "run_command", "delete_file",
        "create_directory",
    ],
}

DANGEROUS_TOOLS = {
    "delete_file", "run_command", "execute_code", "send_message",
    "send_email", "api_call", "database_query", "install_package",
}

DANGEROUS_COMMANDS = [
    r"\brm\s+-rf\b", r"\brm\s+-r\b", r"\brmdir\b",
    r"\bformat\b", r"\bdel\s+/[sS]\b",
    r"\bdrop\s+table\b", r"\bdrop\s+database\b",
    r"\bgit\s+push\s+--force\b", r"\bgit\s+reset\s+--hard\b",
    r"\bkill\s+-9\b", r"\btaskkill\s+/F\b",
    r"\bcurl\b.*\|\s*sh\b", r"\bwget\b.*\|\s*sh\b",
    r"\bchmod\s+777\b", r"\bsudo\b",
]

# ── Prompt Injection Patterns ────────────────────────────────────

INJECTION_PATTERNS = [
    # Direct override attempts
    r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|rules?|prompts?)",
    r"(?i)disregard\s+(all\s+)?(previous|prior|above)",
    r"(?i)forget\s+(everything|all|your)\s+(instructions?|rules?|training)",
    r"(?i)you\s+are\s+now\s+(a|an)\s+\w+",
    r"(?i)new\s+instructions?\s*[:=]",
    r"(?i)system\s*prompt\s*[:=]",
    r"(?i)override\s+(system|safety|security)",
    r"(?i)jailbreak",
    r"(?i)DAN\s+mode",

    # Role hijacking
    r"(?i)pretend\s+(you\s+are|to\s+be)\s+(a|an)\s+\w+",
    r"(?i)act\s+as\s+(a|an|if)\s+",
    r"(?i)roleplay\s+as\s+",
    r"(?i)you\s+must\s+(now\s+)?obey",

    # Encoded/obfuscated attempts
    r"(?i)base64\s*[:=]\s*[A-Za-z0-9+/=]{20,}",
    r"(?i)decode\s+this\s*[:=]",
    r"(?i)execute\s+the\s+following\s+(code|script|command)",

    # Data exfiltration
    r"(?i)show\s+me\s+(your|the)\s+(system\s+prompt|api\s+key|secret|password|token)",
    r"(?i)print\s+(your|the)\s+(system\s+prompt|instructions|config)",
    r"(?i)what\s+(is|are)\s+your\s+(system\s+prompt|instructions|rules)",
    r"(?i)reveal\s+(your|the)\s+(prompt|instructions|config)",
]


# ── File ACL ─────────────────────────────────────────────────────

DEFAULT_FILE_ACL = {
    "strict": {
        "allow_read": ["~/.permafrost/**", "~/data/**"],
        "allow_write": ["~/.permafrost/data/**", "~/.permafrost/logs/**"],
        "deny": ["~/.env", "~/**/.*token*", "~/**/*secret*", "~/**/*password*",
                 "~/**/*.pem", "~/**/*.key", "~/**/credentials*"],
    },
    "standard": {
        "allow_read": ["**"],
        "allow_write": ["~/.permafrost/**", "~/data/**", "/tmp/**"],
        "deny": ["~/.env", "~/**/.*token*", "~/**/*secret*", "~/**/*password*",
                 "~/**/*.pem", "~/**/*.key", "~/**/credentials*"],
    },
    "relaxed": {
        "allow_read": ["**"],
        "allow_write": ["**"],
        "deny": ["~/.env", "~/**/*.pem", "~/**/*.key"],
    },
}


# ── Security Manager ─────────────────────────────────────────────

class PFSecurity:
    """Central security manager for Permafrost."""

    def __init__(self, config: dict = None, data_dir: str = None):
        config = config or {}
        self.data_dir = Path(data_dir or os.path.expanduser("~/.permafrost"))
        self.audit_file = self.data_dir / "audit.jsonl"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Security level
        level_str = config.get("security_level", "strict")
        try:
            self.level = SecurityLevel(level_str)
        except ValueError:
            log.warning(f"Unknown security level '{level_str}', defaulting to STRICT")
            self.level = SecurityLevel.STRICT

        # Tool whitelist
        self.tool_whitelist = set(
            config.get("tool_whitelist", DEFAULT_TOOL_WHITELIST.get(self.level.value, []))
        )
        self.tool_blacklist = set(config.get("tool_blacklist", []))

        # File ACL
        acl_config = config.get("file_acl", DEFAULT_FILE_ACL.get(self.level.value, {}))
        self.file_allow_read = acl_config.get("allow_read", [])
        self.file_allow_write = acl_config.get("allow_write", [])
        self.file_deny = acl_config.get("deny", [])

        # Dangerous operation approval
        self.require_approval = config.get("require_approval", True)
        self.auto_deny_dangerous = config.get("auto_deny_dangerous", False)
        self._approval_callback = None

        # Rate limiter
        self.rate_limit = config.get("rate_limit", {
            "tools_per_minute": 30,
            "tokens_per_hour": 500000,
            "messages_per_minute": 10,
        })
        self._tool_timestamps = []
        self._message_timestamps = []
        self._token_count_hour = 0
        self._token_hour_start = time.time()

        # Injection detection
        self.injection_enabled = config.get("injection_detection", True)
        self.injection_action = config.get("injection_action", "block")  # block | warn | log
        self._injection_patterns = [re.compile(p) for p in INJECTION_PATTERNS]
        custom = config.get("custom_injection_patterns", [])
        self._injection_patterns.extend(re.compile(p) for p in custom)

        # Stats
        self.stats = {
            "tools_allowed": 0,
            "tools_blocked": 0,
            "injections_detected": 0,
            "dangerous_ops_blocked": 0,
            "rate_limits_hit": 0,
        }

        log.info(f"Security initialized: level={self.level.value}, "
                 f"tools={len(self.tool_whitelist)}, injection={'ON' if self.injection_enabled else 'OFF'}")

    # ── Tool Authorization ───────────────────────────────────────

    def authorize_tool(self, tool_name: str, args: dict = None) -> tuple[bool, str]:
        """Check if a tool call is allowed. Returns (allowed, reason)."""
        if self.level == SecurityLevel.OFF:
            self._audit("tool_allow", tool_name, "security OFF")
            return True, "security disabled"

        # Blacklist check (always applies)
        if tool_name in self.tool_blacklist:
            self.stats["tools_blocked"] += 1
            self._audit("tool_block", tool_name, "blacklisted")
            return False, f"Tool '{tool_name}' is blacklisted"

        # Whitelist check
        if self.level != SecurityLevel.RELAXED and tool_name not in self.tool_whitelist:
            self.stats["tools_blocked"] += 1
            self._audit("tool_block", tool_name, "not in whitelist")
            return False, f"Tool '{tool_name}' not in whitelist (level={self.level.value})"

        # Dangerous tool check
        if tool_name in DANGEROUS_TOOLS:
            if self.auto_deny_dangerous:
                self.stats["dangerous_ops_blocked"] += 1
                self._audit("tool_block", tool_name, "dangerous auto-deny")
                return False, f"Dangerous tool '{tool_name}' auto-denied"

            # Check command content for dangerous patterns
            if args and tool_name == "run_command":
                cmd = args.get("command", "")
                for pattern in DANGEROUS_COMMANDS:
                    if re.search(pattern, cmd):
                        self.stats["dangerous_ops_blocked"] += 1
                        self._audit("dangerous_block", tool_name, f"pattern match: {pattern}", cmd)
                        if self.require_approval:
                            return self._request_approval(tool_name, cmd)
                        return False, f"Dangerous command blocked: {cmd[:50]}..."

        # Rate limit check
        if not self._check_rate_limit("tool"):
            self.stats["rate_limits_hit"] += 1
            self._audit("rate_limit", tool_name, "tools_per_minute exceeded")
            return False, "Rate limit exceeded"

        self.stats["tools_allowed"] += 1
        self._audit("tool_allow", tool_name)
        return True, "allowed"

    # ── File Access Control ──────────────────────────────────────

    def authorize_file(self, path: str, mode: str = "read") -> tuple[bool, str]:
        """Check if file access is allowed. mode: 'read' or 'write'."""
        if self.level == SecurityLevel.OFF:
            return True, "security disabled"

        path = os.path.expanduser(path)
        abs_path = os.path.abspath(path)

        # Deny list always applies
        for pattern in self.file_deny:
            expanded = os.path.expanduser(pattern)
            if self._path_matches(abs_path, expanded):
                self._audit("file_block", abs_path, f"denied by pattern: {pattern}")
                return False, f"Access denied: {path} matches deny pattern '{pattern}'"

        # Check allow list
        allow_list = self.file_allow_read if mode == "read" else self.file_allow_write
        for pattern in allow_list:
            expanded = os.path.expanduser(pattern)
            if self._path_matches(abs_path, expanded):
                self._audit("file_allow", abs_path, mode)
                return True, "allowed"

        # Default deny in strict/standard
        if self.level in (SecurityLevel.STRICT, SecurityLevel.STANDARD):
            self._audit("file_block", abs_path, f"no matching allow rule for {mode}")
            return False, f"No allow rule for {mode} access to {path}"

        return True, "allowed (relaxed mode)"

    def _path_matches(self, path: str, pattern: str) -> bool:
        """Simple glob-like path matching."""
        import fnmatch
        return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.replace("/", os.sep))

    # ── Prompt Injection Detection ───────────────────────────────

    def check_injection(self, text: str) -> tuple[bool, Optional[str]]:
        """Scan text for prompt injection attempts.
        Returns (is_safe, matched_pattern or None).
        """
        if not self.injection_enabled or self.level == SecurityLevel.OFF:
            return True, None

        for pattern in self._injection_patterns:
            match = pattern.search(text)
            if match:
                self.stats["injections_detected"] += 1
                matched = match.group(0)
                self._audit("injection_detected", matched[:100], text[:200])
                log.warning(f"Prompt injection detected: '{matched[:50]}...'")

                if self.injection_action == "block":
                    return False, matched
                elif self.injection_action == "warn":
                    log.warning(f"Injection warning (not blocked): {matched[:50]}")
                    return True, matched
                else:  # log only
                    return True, matched

        return True, None

    # ── Rate Limiter ─────────────────────────────────────────────

    def _check_rate_limit(self, category: str) -> bool:
        """Check if action is within rate limits."""
        now = time.time()

        if category == "tool":
            limit = self.rate_limit.get("tools_per_minute", 30)
            self._tool_timestamps = [t for t in self._tool_timestamps if now - t < 60]
            if len(self._tool_timestamps) >= limit:
                return False
            self._tool_timestamps.append(now)

        elif category == "message":
            limit = self.rate_limit.get("messages_per_minute", 10)
            self._message_timestamps = [t for t in self._message_timestamps if now - t < 60]
            if len(self._message_timestamps) >= limit:
                return False
            self._message_timestamps.append(now)

        return True

    def track_tokens(self, count: int):
        """Track token usage for rate limiting."""
        now = time.time()
        if now - self._token_hour_start > 3600:
            self._token_count_hour = 0
            self._token_hour_start = now
        self._token_count_hour += count

        limit = self.rate_limit.get("tokens_per_hour", 500000)
        if self._token_count_hour > limit:
            log.warning(f"Token rate limit approaching: {self._token_count_hour}/{limit}")

    # ── Approval System ──────────────────────────────────────────

    def set_approval_callback(self, callback):
        """Set callback for human approval requests.
        callback(tool_name, details) -> bool
        """
        self._approval_callback = callback

    def _request_approval(self, tool_name: str, details: str) -> tuple[bool, str]:
        """Request human approval for dangerous operation."""
        if self._approval_callback:
            approved = self._approval_callback(tool_name, details)
            if approved:
                self._audit("approval_granted", tool_name, details[:200])
                return True, "approved by human"
            else:
                self._audit("approval_denied", tool_name, details[:200])
                return False, "denied by human"

        self._audit("approval_needed", tool_name, details[:200])
        return False, f"Dangerous operation needs approval: {tool_name}"

    # ── Audit Log ────────────────────────────────────────────────

    def _audit(self, event: str, target: str = "", detail: str = "", extra: str = ""):
        """Write audit log entry."""
        entry = {
            "ts": datetime.now().isoformat(),
            "event": event,
            "target": target,
        }
        if detail:
            entry["detail"] = detail[:500]
        if extra:
            entry["extra"] = extra[:200]

        try:
            with open(self.audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def get_stats(self) -> dict:
        """Return security statistics."""
        return {**self.stats, "level": self.level.value}

    def get_recent_audit(self, n: int = 20) -> list[dict]:
        """Return last N audit entries."""
        if not self.audit_file.exists():
            return []
        entries = []
        try:
            with open(self.audit_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
            return entries[-n:]
        except (OSError, json.JSONDecodeError):
            return []


# ── Convenience: Create Security from Config File ────────────────

def create_security(config_path: str = None, **overrides) -> PFSecurity:
    """Create PFSecurity from config file or defaults."""
    config = {}
    if config_path and os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            full_config = json.load(f)
        config = full_config.get("security", {})

    config.update(overrides)
    data_dir = config.pop("data_dir", None)
    return PFSecurity(config=config, data_dir=data_dir)
