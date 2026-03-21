"""
Permafrost Hook System — Event-driven lifecycle hooks.

Events:
  - on_start: Brain 啟動時
  - on_stop: Brain 停止時
  - on_message_in: 收到訊息時（處理前）
  - on_message_out: 發送回覆後
  - on_error: 發生錯誤時
  - on_reload: Config 重新載入時
  - on_compact: Context 壓縮時

Hook 定義在 config.json:
{
  "hooks": {
    "on_start": ["python scripts/startup.py"],
    "on_message_in": ["python scripts/log_message.py"],
    "on_error": ["python scripts/alert.py"]
  }
}

每個 hook 收到 JSON stdin：
{
  "event": "on_message_in",
  "timestamp": "ISO",
  "data": { ... event-specific data ... }
}

Hook 可以返回 JSON stdout：
{
  "systemMessage": "注入到 context 的訊息",
  "block": false  // true = 阻止後續處理
}
"""

import json
import logging
import subprocess
import shlex
import sys
from datetime import datetime
from typing import Any

log = logging.getLogger("permafrost.hooks")

VALID_EVENTS = {
    "on_start",
    "on_stop",
    "on_message_in",
    "on_message_out",
    "on_error",
    "on_reload",
    "on_compact",
}

HOOK_TIMEOUT = 5  # seconds


class HookResult:
    """Result from a single hook execution."""

    __slots__ = ("system_message", "block")

    def __init__(self, system_message: str = "", block: bool = False):
        self.system_message = system_message
        self.block = block


class HookManager:
    """Manages event-driven lifecycle hooks for Permafrost Brain."""

    def __init__(self, config: dict):
        self._hooks: dict[str, list[str]] = {}
        self._load_hooks(config)

    def _load_hooks(self, config: dict):
        """Parse hooks from config dict."""
        raw = config.get("hooks", {})
        if not isinstance(raw, dict):
            log.warning("hooks config is not a dict, ignoring")
            return

        for event, commands in raw.items():
            if event not in VALID_EVENTS:
                log.warning(f"unknown hook event '{event}', skipping")
                continue
            if isinstance(commands, str):
                commands = [commands]
            if not isinstance(commands, list):
                log.warning(f"hooks[{event}] must be a string or list, skipping")
                continue
            self._hooks[event] = [c for c in commands if isinstance(c, str) and c.strip()]

        registered = {k: len(v) for k, v in self._hooks.items() if v}
        if registered:
            log.info(f"hooks registered: {registered}")

    def reload(self, config: dict):
        """Reload hooks from updated config."""
        self._hooks.clear()
        self._load_hooks(config)

    def emit(self, event: str, data: dict[str, Any] | None = None) -> HookResult:
        """
        Execute all hooks for the given event.

        Returns a merged HookResult. If any hook sets block=True, the result
        will have block=True. System messages are concatenated.
        """
        result = HookResult()
        commands = self._hooks.get(event, [])
        if not commands:
            return result

        payload = json.dumps({
            "event": event,
            "timestamp": datetime.now().isoformat(),
            "data": data or {},
        }, ensure_ascii=False)

        for cmd in commands:
            try:
                hook_out = self._run_hook(cmd, payload)
                if hook_out:
                    if hook_out.get("block"):
                        result.block = True
                    msg = hook_out.get("systemMessage", "")
                    if msg:
                        if result.system_message:
                            result.system_message += "\n"
                        result.system_message += msg
            except Exception as e:
                log.error(f"hook [{event}] '{cmd}' failed: {e}")

        return result

    def _run_hook(self, cmd: str, stdin_payload: str) -> dict | None:
        """Run a single hook command via subprocess. Returns parsed JSON or None."""
        if sys.platform == "win32":
            # On Windows, use shell=True to handle 'python script.py' style commands
            args = cmd
            use_shell = True
        else:
            args = shlex.split(cmd)
            use_shell = False

        proc = subprocess.run(
            args,
            input=stdin_payload,
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT,
            shell=use_shell,
        )

        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            if stderr:
                log.warning(f"hook '{cmd}' exit {proc.returncode}: {stderr[:200]}")
            return None

        stdout = proc.stdout.strip()
        if not stdout:
            return None

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            log.warning(f"hook '{cmd}' returned non-JSON output: {stdout[:100]}")
            return None
