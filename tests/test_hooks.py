"""
Tests for core/hooks.py — HookManager lifecycle hook system.
"""
import json
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hooks import HookManager, HookResult, VALID_EVENTS, HOOK_TIMEOUT


# ── HookResult ────────────────────────────────────────────────────────────────


class TestHookResult:
    def test_defaults(self):
        r = HookResult()
        assert r.system_message == ""
        assert r.block is False

    def test_explicit_values(self):
        r = HookResult(system_message="hello", block=True)
        assert r.system_message == "hello"
        assert r.block is True


# ── VALID_EVENTS ──────────────────────────────────────────────────────────────


class TestValidEvents:
    def test_contains_standard_events(self):
        for ev in ("on_start", "on_stop", "on_message_in", "on_message_out",
                   "on_error", "on_reload", "on_compact"):
            assert ev in VALID_EVENTS


# ── HookManager._load_hooks ───────────────────────────────────────────────────


class TestHookManagerLoad:
    def test_empty_config(self):
        hm = HookManager({})
        assert hm._hooks == {}

    def test_no_hooks_key(self):
        hm = HookManager({"other_key": 123})
        assert hm._hooks == {}

    def test_non_dict_hooks_ignored(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="permafrost.hooks"):
            hm = HookManager({"hooks": "not-a-dict"})
        assert hm._hooks == {}
        assert "not a dict" in caplog.text

    def test_valid_single_string_command(self):
        hm = HookManager({"hooks": {"on_start": "echo hello"}})
        assert "on_start" in hm._hooks
        assert hm._hooks["on_start"] == ["echo hello"]

    def test_valid_list_of_commands(self):
        hm = HookManager({"hooks": {"on_stop": ["echo a", "echo b"]}})
        assert hm._hooks["on_stop"] == ["echo a", "echo b"]

    def test_unknown_event_skipped(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="permafrost.hooks"):
            hm = HookManager({"hooks": {"on_fake_event": "echo x"}})
        assert "on_fake_event" not in hm._hooks
        assert "unknown hook event" in caplog.text

    def test_non_string_non_list_value_skipped(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="permafrost.hooks"):
            hm = HookManager({"hooks": {"on_start": 42}})
        assert "on_start" not in hm._hooks

    def test_empty_command_strings_filtered(self):
        hm = HookManager({"hooks": {"on_start": ["echo ok", "   ", ""]}})
        assert hm._hooks["on_start"] == ["echo ok"]

    def test_multiple_events(self):
        cfg = {"hooks": {"on_start": "echo start", "on_stop": "echo stop"}}
        hm = HookManager(cfg)
        assert "on_start" in hm._hooks
        assert "on_stop" in hm._hooks

    def test_non_string_items_in_list_filtered(self):
        hm = HookManager({"hooks": {"on_start": ["echo ok", 123, None]}})
        assert hm._hooks["on_start"] == ["echo ok"]


# ── HookManager.reload ────────────────────────────────────────────────────────


class TestHookManagerReload:
    def test_reload_replaces_hooks(self):
        hm = HookManager({"hooks": {"on_start": "echo first"}})
        assert "on_start" in hm._hooks

        hm.reload({"hooks": {"on_stop": "echo second"}})
        assert "on_start" not in hm._hooks
        assert "on_stop" in hm._hooks

    def test_reload_clears_all_when_no_hooks(self):
        hm = HookManager({"hooks": {"on_start": "echo x"}})
        hm.reload({})
        assert hm._hooks == {}


# ── HookManager.emit (no hooks registered) ───────────────────────────────────


class TestHookManagerEmitNoHooks:
    def test_emit_unknown_event_returns_default_result(self):
        hm = HookManager({})
        result = hm.emit("on_start")
        assert isinstance(result, HookResult)
        assert result.block is False
        assert result.system_message == ""

    def test_emit_with_data_returns_default_result(self):
        hm = HookManager({})
        result = hm.emit("on_message_in", {"text": "hi"})
        assert result.system_message == ""
        assert result.block is False


# ── HookManager.emit (with real subprocess hooks) ────────────────────────────


class TestHookManagerEmitReal:
    """Use platform-compatible echo commands to test real subprocess execution."""

    def _echo_hook_cmd(self, output: str) -> str:
        """Return a command that writes `output` to stdout."""
        if sys.platform == "win32":
            # cmd /c echo prints the string (may have trailing space on some Windows)
            return f'cmd /c echo {output}'
        return f"echo '{output}'"

    def _python_hook(self, code: str) -> str:
        """Return a python -c '...' command that writes JSON to stdout."""
        safe = code.replace('"', '\\"')
        return f'python -c "{safe}"'

    def test_emit_calls_hook_and_returns_result(self):
        # Hook outputs valid JSON with systemMessage
        cmd = self._python_hook(
            'import sys; sys.stdout.write(\'{"systemMessage": "boot ok"}\')'
        )
        hm = HookManager({"hooks": {"on_start": cmd}})
        result = hm.emit("on_start")
        assert "boot ok" in result.system_message
        assert result.block is False

    def test_emit_hook_sets_block_true(self):
        cmd = self._python_hook(
            'import sys; sys.stdout.write(\'{"block": true}\')'
        )
        hm = HookManager({"hooks": {"on_message_in": cmd}})
        result = hm.emit("on_message_in", {"text": "hello"})
        assert result.block is True

    def test_emit_multiple_hooks_concatenates_messages(self):
        cmd_a = self._python_hook(
            'import sys; sys.stdout.write(\'{"systemMessage": "A"}\')'
        )
        cmd_b = self._python_hook(
            'import sys; sys.stdout.write(\'{"systemMessage": "B"}\')'
        )
        hm = HookManager({"hooks": {"on_start": [cmd_a, cmd_b]}})
        result = hm.emit("on_start")
        assert "A" in result.system_message
        assert "B" in result.system_message

    def test_emit_multiple_hooks_any_block_propagates(self):
        cmd_a = self._python_hook(
            'import sys; sys.stdout.write(\'{"block": true}\')'
        )
        cmd_b = self._python_hook(
            'import sys; sys.stdout.write(\'{"block": false}\')'
        )
        hm = HookManager({"hooks": {"on_message_in": [cmd_a, cmd_b]}})
        result = hm.emit("on_message_in")
        assert result.block is True

    def test_emit_hook_non_json_stdout_is_ignored(self):
        # Hook writes non-JSON; should not raise, block stays False
        cmd = self._python_hook(
            'import sys; sys.stdout.write("just plain text")'
        )
        hm = HookManager({"hooks": {"on_start": cmd}})
        result = hm.emit("on_start")
        assert result.block is False
        assert result.system_message == ""

    def test_emit_hook_empty_stdout_is_ignored(self):
        cmd = self._python_hook('pass')
        hm = HookManager({"hooks": {"on_start": cmd}})
        result = hm.emit("on_start")
        assert result.system_message == ""
        assert result.block is False

    def test_emit_hook_nonzero_exit_returns_none(self, caplog):
        import logging
        cmd = self._python_hook('import sys; sys.exit(1)')
        hm = HookManager({"hooks": {"on_error": cmd}})
        with caplog.at_level(logging.WARNING, logger="permafrost.hooks"):
            result = hm.emit("on_error")
        assert result.block is False

    def test_emit_passes_event_and_data_on_stdin(self):
        """Hook reads stdin and echoes the event name back in systemMessage."""
        cmd = self._python_hook(
            'import sys, json; data=json.load(sys.stdin); '
            'sys.stdout.write(json.dumps({"systemMessage": data["event"]}))'
        )
        hm = HookManager({"hooks": {"on_compact": cmd}})
        result = hm.emit("on_compact", {"context_size": 100})
        assert "on_compact" in result.system_message

    def test_emit_hook_invalid_command_logs_error(self, caplog):
        import logging
        # An invalid command that will fail
        hm = HookManager({"hooks": {"on_start": "this_command_does_not_exist_xyz"}})
        with caplog.at_level(logging.ERROR, logger="permafrost.hooks"):
            result = hm.emit("on_start")
        assert result.block is False


# ── HookManager._run_hook ─────────────────────────────────────────────────────


class TestHookManagerRunHook:
    def _python_hook(self, code: str) -> str:
        safe = code.replace('"', '\\"')
        return f'python -c "{safe}"'

    def test_run_hook_returns_dict_on_valid_json(self):
        cmd = self._python_hook(
            'import sys; sys.stdout.write(\'{"key": "val"}\')'
        )
        hm = HookManager({})
        result = hm._run_hook(cmd, '{}')
        assert result == {"key": "val"}

    def test_run_hook_returns_none_on_empty_stdout(self):
        cmd = self._python_hook('pass')
        hm = HookManager({})
        result = hm._run_hook(cmd, '{}')
        assert result is None

    def test_run_hook_returns_none_on_nonzero_exit(self):
        cmd = self._python_hook('import sys; sys.exit(2)')
        hm = HookManager({})
        result = hm._run_hook(cmd, '{}')
        assert result is None

    def test_run_hook_returns_none_on_invalid_json(self):
        cmd = self._python_hook('import sys; sys.stdout.write("not json")')
        hm = HookManager({})
        result = hm._run_hook(cmd, '{}')
        assert result is None
