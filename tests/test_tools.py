"""Tests for core.tools — all 64 tools registry + key tool functions."""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tools import (
    TOOLS, execute_tool, get_tool_prompt, get_tools_schema,
    parse_tool_calls, strip_tool_calls, has_tool_calls,
    normalize_tool_calls,
)


class TestToolRegistry:
    def test_tool_count(self):
        assert len(TOOLS) >= 60, f"Expected 60+ tools, got {len(TOOLS)}"

    def test_all_tools_callable(self):
        for name, info in TOOLS.items():
            assert callable(info["function"]), f"Tool {name} is not callable"

    def test_all_tools_have_description(self):
        for name, info in TOOLS.items():
            assert info.get("description"), f"Tool {name} has no description"

    def test_all_tools_have_parameters(self):
        for name, info in TOOLS.items():
            assert "parameters" in info, f"Tool {name} has no parameters dict"


class TestToolSchemas:
    def test_openai_schema(self):
        schemas = get_tools_schema("openai")
        assert len(schemas) > 0
        for s in schemas:
            assert s["type"] == "function"
            assert "name" in s["function"]

    def test_claude_schema(self):
        schemas = get_tools_schema("claude")
        assert len(schemas) > 0
        for s in schemas:
            assert "name" in s
            assert "input_schema" in s

    def test_gemini_schema(self):
        schemas = get_tools_schema("gemini")
        assert len(schemas) == 1  # All functions in ONE object
        assert "function_declarations" in schemas[0]
        assert len(schemas[0]["function_declarations"]) > 0


class TestToolCallParsing:
    def test_parse_standard(self):
        text = 'Hello [TOOL_CALL]{"name": "bash", "args": {"command": "ls"}}[/TOOL_CALL]'
        calls = parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "bash"

    def test_parse_variant_tool_code(self):
        text = '[TOOL_CODE]{"name": "bash", "args": {"command": "ls"}}[/TOOL_CODE]'
        calls = parse_tool_calls(text)
        assert len(calls) == 1

    def test_has_tool_calls(self):
        assert has_tool_calls('[TOOL_CALL]{"name":"x","args":{}}[/TOOL_CALL]')
        assert has_tool_calls('[TOOL_CODE]{"name":"x","args":{}}[/TOOL_CODE]')
        assert not has_tool_calls("no tools here")

    def test_strip_tool_calls(self):
        text = 'Hello [TOOL_CALL]{"name":"x","args":{}}[/TOOL_CALL] world'
        result = strip_tool_calls(text)
        assert "[TOOL_CALL]" not in result
        assert "Hello" in result
        assert "world" in result

    def test_normalize_tool_code(self):
        text = '[TOOL_CODE]{"name":"x","args":{}}[/TOOL_CODE]'
        result = normalize_tool_calls(text)
        assert "[TOOL_CALL]" in result


class TestBuiltinTools:
    def test_get_datetime(self):
        result = execute_tool("get_datetime", {})
        assert "Date:" in result
        assert "Time:" in result

    def test_calculate(self):
        result = execute_tool("calculate", {"expression": "2**10"})
        assert "1024" in result

    def test_calculate_sqrt(self):
        result = execute_tool("calculate", {"expression": "sqrt(144)"})
        assert "12" in result

    def test_generate_uuid(self):
        result = execute_tool("generate_uuid", {})
        assert len(result) == 36  # UUID format
        assert "-" in result

    def test_generate_password(self):
        result = execute_tool("generate_password", {"length": 20})
        assert len(result) == 20

    def test_encode_base64(self):
        result = execute_tool("encode_decode", {"text": "hello", "method": "base64_encode"})
        assert result == "aGVsbG8="

    def test_decode_base64(self):
        result = execute_tool("encode_decode", {"text": "aGVsbG8=", "method": "base64_decode"})
        assert result == "hello"

    def test_regex_extract(self):
        result = execute_tool("regex_extract", {
            "text": "email: test@example.com and other@test.org",
            "pattern": r"[\w.]+@[\w.]+"
        })
        parsed = json.loads(result)
        assert len(parsed) == 2

    def test_text_stats(self):
        result = execute_tool("text_stats", {"text": "hello world\nsecond line"})
        assert "Lines: 2" in result
        assert "Words: 4" in result

    def test_system_info(self):
        result = execute_tool("system_info", {})
        assert "OS:" in result
        assert "Python:" in result

    def test_list_files(self):
        result = execute_tool("list_files", {"path": "."})
        assert len(result) > 0

    def test_json_read_write(self, tmp_path):
        path = str(tmp_path / "test.json")
        execute_tool("json_write", {"path": path, "data": '{"key": "value"}'})
        result = execute_tool("json_read", {"path": path})
        parsed = json.loads(result)
        assert parsed["key"] == "value"

    def test_json_read_key(self, tmp_path):
        path = str(tmp_path / "test2.json")
        execute_tool("json_write", {"path": path, "data": '{"a": {"b": "deep"}}'})
        result = execute_tool("json_read", {"path": path, "key": "a.b"})
        assert "deep" in result


class TestSecurityWhitelist:
    def test_whitelist_matches_registry(self):
        from core.security import DEFAULT_TOOL_WHITELIST
        strict = DEFAULT_TOOL_WHITELIST["strict"]
        missing = [t for t in strict if t not in TOOLS]
        extra = [t for t in TOOLS if t not in strict]
        assert not missing, f"In whitelist but not registered: {missing}"
        assert not extra, f"Registered but not in whitelist: {extra}"
