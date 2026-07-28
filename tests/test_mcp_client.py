"""Tests for core/mcp_client.py — MCP server connections and tool registration."""
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.mcp_client import MCPConnection, PFMCPManager


@pytest.fixture
def clean_tools_registry():
    """Register/unregister MCP tools without polluting the shared TOOLS dict."""
    from core.tools import TOOLS
    before = set(TOOLS.keys())
    yield TOOLS
    for key in set(TOOLS.keys()) - before:
        del TOOLS[key]


# ---------------------------------------------------------------------------
# TestMCPConnectionInit
# ---------------------------------------------------------------------------

class TestMCPConnectionInit:
    def test_defaults(self):
        conn = MCPConnection(name="chrome", command="npx")
        assert conn.name == "chrome"
        assert conn.command == "npx"
        assert conn.args == []
        assert conn.env == {}
        assert conn.cwd is None
        assert conn.process is None
        assert conn.tools == []
        assert conn._request_id == 0
        assert conn._connected is False

    def test_custom_args_env_cwd(self):
        conn = MCPConnection(name="discord", command="node", args=["index.js"],
                              env={"TOKEN": "x"}, cwd="/tmp")
        assert conn.args == ["index.js"]
        assert conn.env == {"TOKEN": "x"}
        assert conn.cwd == "/tmp"


# ---------------------------------------------------------------------------
# TestConnect
# ---------------------------------------------------------------------------

class TestConnect:
    def test_successful_connect_with_tools(self):
        conn = MCPConnection(name="chrome", command="npx")
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock.MagicMock()
            with mock.patch.object(conn, "_send_request") as mock_req, \
                 mock.patch.object(conn, "_send_notification") as mock_notif:
                mock_req.side_effect = [
                    {"capabilities": {}},
                    {"tools": [{"name": "screenshot"}, {"name": "click"}]},
                ]
                result = conn.connect()
        assert result is True
        assert conn._connected is True
        assert len(conn.tools) == 2
        mock_notif.assert_called_once_with("notifications/initialized", {})

    def test_connect_init_fails_returns_false(self):
        conn = MCPConnection(name="chrome", command="npx")
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock.MagicMock()
            with mock.patch.object(conn, "_send_request", return_value=None):
                result = conn.connect()
        assert result is False
        assert conn._connected is False

    def test_connect_no_tools_key_defaults_empty(self):
        conn = MCPConnection(name="chrome", command="npx")
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock.MagicMock()
            with mock.patch.object(conn, "_send_request") as mock_req, \
                 mock.patch.object(conn, "_send_notification"):
                mock_req.side_effect = [{"capabilities": {}}, {}]
                result = conn.connect()
        assert result is True
        assert conn.tools == []

    def test_connect_command_not_found(self):
        conn = MCPConnection(name="chrome", command="nonexistent_cmd_xyz")
        with mock.patch("subprocess.Popen", side_effect=FileNotFoundError()):
            result = conn.connect()
        assert result is False
        assert conn._connected is False

    def test_connect_generic_exception_returns_false(self):
        conn = MCPConnection(name="chrome", command="npx")
        with mock.patch("subprocess.Popen", side_effect=RuntimeError("boom")):
            result = conn.connect()
        assert result is False
        assert conn._connected is False

    def test_connect_sends_correct_initialize_params(self):
        conn = MCPConnection(name="chrome", command="npx")
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock.MagicMock()
            with mock.patch.object(conn, "_send_request") as mock_req, \
                 mock.patch.object(conn, "_send_notification"):
                mock_req.side_effect = [{"capabilities": {}}, {"tools": []}]
                conn.connect()
        init_call = mock_req.call_args_list[0]
        assert init_call[0][0] == "initialize"
        assert init_call[0][1]["clientInfo"]["name"] == "permafrost"

    def test_connect_lists_tools_after_initialize(self):
        conn = MCPConnection(name="chrome", command="npx")
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock.MagicMock()
            with mock.patch.object(conn, "_send_request") as mock_req, \
                 mock.patch.object(conn, "_send_notification"):
                mock_req.side_effect = [{"capabilities": {}}, {"tools": []}]
                conn.connect()
        assert mock_req.call_args_list[1][0][0] == "tools/list"


# ---------------------------------------------------------------------------
# TestDisconnect
# ---------------------------------------------------------------------------

class TestDisconnect:
    def test_disconnect_no_process_does_not_raise(self):
        conn = MCPConnection(name="chrome", command="npx")
        conn.disconnect()
        assert conn._connected is False

    def test_disconnect_terminates_process(self):
        conn = MCPConnection(name="chrome", command="npx")
        proc = mock.MagicMock()
        conn.process = proc
        conn._connected = True
        conn.disconnect()
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once_with(timeout=5)
        assert conn.process is None
        assert conn._connected is False

    def test_disconnect_kills_on_terminate_failure(self):
        conn = MCPConnection(name="chrome", command="npx")
        proc = mock.MagicMock()
        proc.wait.side_effect = Exception("timeout")
        conn.process = proc
        conn.disconnect()
        proc.kill.assert_called_once()
        assert conn.process is None

    def test_disconnect_swallows_kill_failure(self):
        conn = MCPConnection(name="chrome", command="npx")
        proc = mock.MagicMock()
        proc.wait.side_effect = Exception("timeout")
        proc.kill.side_effect = Exception("already dead")
        conn.process = proc
        conn.disconnect()
        assert conn.process is None


# ---------------------------------------------------------------------------
# TestIsConnected
# ---------------------------------------------------------------------------

class TestIsConnected:
    def test_false_when_connected_flag_false(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.poll.return_value = None
        conn._connected = False
        assert conn.is_connected is False

    def test_false_when_no_process(self):
        conn = MCPConnection(name="c", command="npx")
        conn._connected = True
        conn.process = None
        assert conn.is_connected is False

    def test_false_when_process_exited(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.poll.return_value = 1
        conn._connected = True
        assert conn.is_connected is False

    def test_true_when_running(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.poll.return_value = None
        conn._connected = True
        assert conn.is_connected is True


# ---------------------------------------------------------------------------
# TestCallTool
# ---------------------------------------------------------------------------

class TestCallTool:
    def test_not_connected_returns_error(self):
        conn = MCPConnection(name="chrome", command="npx")
        result = conn.call_tool("screenshot")
        assert "not connected" in result
        assert "chrome" in result

    def test_no_process_returns_error(self):
        conn = MCPConnection(name="chrome", command="npx")
        conn._connected = True
        conn.process = None
        result = conn.call_tool("screenshot")
        assert "not connected" in result

    def test_request_failure_returns_error(self):
        conn = MCPConnection(name="chrome", command="npx")
        conn._connected = True
        conn.process = mock.MagicMock()
        with mock.patch.object(conn, "_send_request", return_value=None):
            result = conn.call_tool("screenshot")
        assert "MCP tool call failed" in result
        assert "screenshot" in result

    def test_text_content_returned(self):
        conn = MCPConnection(name="chrome", command="npx")
        conn._connected = True
        conn.process = mock.MagicMock()
        with mock.patch.object(conn, "_send_request",
                                return_value={"content": [{"type": "text", "text": "hello"}]}):
            result = conn.call_tool("screenshot")
        assert result == "hello"

    def test_multiple_text_blocks_joined_with_newline(self):
        conn = MCPConnection(name="chrome", command="npx")
        conn._connected = True
        conn.process = mock.MagicMock()
        content = [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}]
        with mock.patch.object(conn, "_send_request", return_value={"content": content}):
            result = conn.call_tool("screenshot")
        assert result == "line1\nline2"

    def test_image_content_placeholder(self):
        conn = MCPConnection(name="chrome", command="npx")
        conn._connected = True
        conn.process = mock.MagicMock()
        content = [{"type": "image", "mimeType": "image/png"}]
        with mock.patch.object(conn, "_send_request", return_value={"content": content}):
            result = conn.call_tool("screenshot")
        assert result == "[image: image/png]"

    def test_string_content_item(self):
        conn = MCPConnection(name="chrome", command="npx")
        conn._connected = True
        conn.process = mock.MagicMock()
        with mock.patch.object(conn, "_send_request", return_value={"content": ["plain string"]}):
            result = conn.call_tool("screenshot")
        assert result == "plain string"

    def test_empty_content_falls_back_to_json(self):
        conn = MCPConnection(name="chrome", command="npx")
        conn._connected = True
        conn.process = mock.MagicMock()
        with mock.patch.object(conn, "_send_request", return_value={"content": [], "status": "ok"}):
            result = conn.call_tool("screenshot")
        assert "status" in result

    def test_passes_arguments(self):
        conn = MCPConnection(name="chrome", command="npx")
        conn._connected = True
        conn.process = mock.MagicMock()
        with mock.patch.object(conn, "_send_request", return_value={"content": []}) as mock_req:
            conn.call_tool("click", {"x": 10, "y": 20})
        mock_req.assert_called_once_with("tools/call", {"name": "click", "arguments": {"x": 10, "y": 20}})

    def test_defaults_arguments_to_empty_dict(self):
        conn = MCPConnection(name="chrome", command="npx")
        conn._connected = True
        conn.process = mock.MagicMock()
        with mock.patch.object(conn, "_send_request", return_value={"content": []}) as mock_req:
            conn.call_tool("click")
        mock_req.assert_called_once_with("tools/call", {"name": "click", "arguments": {}})


# ---------------------------------------------------------------------------
# TestSendRequest / TestSendNotification
# ---------------------------------------------------------------------------

class TestSendRequest:
    def test_increments_request_id(self):
        conn = MCPConnection(name="c", command="npx")
        with mock.patch.object(conn, "_rpc", return_value={"ok": True}) as mock_rpc:
            conn._send_request("ping", {})
            conn._send_request("ping", {})
        assert conn._request_id == 2
        assert mock_rpc.call_args_list[0][0][0]["id"] == 1
        assert mock_rpc.call_args_list[1][0][0]["id"] == 2

    def test_request_shape(self):
        conn = MCPConnection(name="c", command="npx")
        with mock.patch.object(conn, "_rpc", return_value={}) as mock_rpc:
            conn._send_request("tools/list", {"a": 1})
        req = mock_rpc.call_args[0][0]
        assert req["jsonrpc"] == "2.0"
        assert req["method"] == "tools/list"
        assert req["params"] == {"a": 1}


class TestSendNotification:
    def test_sends_without_id(self):
        conn = MCPConnection(name="c", command="npx")
        with mock.patch.object(conn, "_write") as mock_write:
            conn._send_notification("notifications/initialized", {})
        msg = mock_write.call_args[0][0]
        assert "id" not in msg
        assert msg["method"] == "notifications/initialized"
        assert msg["jsonrpc"] == "2.0"


# ---------------------------------------------------------------------------
# TestRpc
# ---------------------------------------------------------------------------

class TestRpc:
    def test_no_process_returns_none(self):
        conn = MCPConnection(name="c", command="npx")
        assert conn._rpc({"method": "x"}) is None

    def test_no_stdin_returns_none(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.stdin = None
        assert conn._rpc({"method": "x"}) is None

    def test_no_stdout_returns_none(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.stdout = None
        assert conn._rpc({"method": "x"}) is None

    def test_successful_roundtrip(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.stdout.readline.return_value = json.dumps({"result": {"ok": 1}}).encode() + b"\n"
        result = conn._rpc({"jsonrpc": "2.0", "id": 1, "method": "x", "params": {}})
        assert result == {"ok": 1}

    def test_no_result_key_returns_empty_dict(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.stdout.readline.return_value = json.dumps({}).encode() + b"\n"
        result = conn._rpc({"jsonrpc": "2.0", "id": 1, "method": "x", "params": {}})
        assert result == {}

    def test_empty_line_returns_none(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.stdout.readline.return_value = b""
        result = conn._rpc({"method": "x"})
        assert result is None

    def test_error_in_response_returns_none(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.stdout.readline.return_value = json.dumps(
            {"error": {"code": -1, "message": "bad"}}).encode() + b"\n"
        result = conn._rpc({"method": "x"})
        assert result is None

    def test_invalid_json_returns_none(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.stdout.readline.return_value = b"not json\n"
        result = conn._rpc({"method": "x"})
        assert result is None

    def test_undecodable_bytes_returns_none(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.stdout.readline.return_value = b"\xff\xfe not utf8"
        result = conn._rpc({"method": "x"})
        assert result is None

    def test_writes_before_reading(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.stdout.readline.return_value = json.dumps({"result": {}}).encode() + b"\n"
        with mock.patch.object(conn, "_write") as mock_write:
            conn._rpc({"method": "x"})
        mock_write.assert_called_once_with({"method": "x"})


# ---------------------------------------------------------------------------
# TestWrite
# ---------------------------------------------------------------------------

class TestWrite:
    def test_no_process_does_not_raise(self):
        conn = MCPConnection(name="c", command="npx")
        conn._write({"a": 1})

    def test_no_stdin_does_not_raise(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.stdin = None
        conn._write({"a": 1})

    def test_writes_json_with_trailing_newline(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn._write({"a": 1})
        written = conn.process.stdin.write.call_args[0][0]
        assert written.decode("utf-8").endswith("\n")
        assert json.loads(written.decode("utf-8").strip()) == {"a": 1}
        conn.process.stdin.flush.assert_called_once()

    def test_oserror_sets_disconnected(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.stdin.write.side_effect = OSError("broken")
        conn._connected = True
        conn._write({"a": 1})
        assert conn._connected is False

    def test_brokenpipeerror_sets_disconnected(self):
        conn = MCPConnection(name="c", command="npx")
        conn.process = mock.MagicMock()
        conn.process.stdin.write.side_effect = BrokenPipeError()
        conn._connected = True
        conn._write({"a": 1})
        assert conn._connected is False


# ---------------------------------------------------------------------------
# TestPFMCPManagerInit
# ---------------------------------------------------------------------------

class TestPFMCPManagerInit:
    def test_defaults(self):
        mgr = PFMCPManager()
        assert mgr.config == {}
        assert mgr.connections == {}
        assert mgr.data_dir == Path.home() / ".permafrost"

    def test_custom_config_and_data_dir(self, tmp_path):
        mgr = PFMCPManager(config={"mcp_servers": {}}, data_dir=str(tmp_path))
        assert mgr.config == {"mcp_servers": {}}
        assert mgr.data_dir == tmp_path


# ---------------------------------------------------------------------------
# TestStartAll
# ---------------------------------------------------------------------------

class TestStartAll:
    def test_no_config_does_nothing(self):
        mgr = PFMCPManager(config={})
        mgr.start_all()
        assert mgr.connections == {}

    def test_disabled_server_skipped(self):
        mgr = PFMCPManager(config={"mcp_servers": {"chrome": {"enabled": False, "command": "npx"}}})
        mgr.start_all()
        assert mgr.connections == {}

    def test_missing_command_skipped(self):
        mgr = PFMCPManager(config={"mcp_servers": {"chrome": {}}})
        mgr.start_all()
        assert mgr.connections == {}

    def test_successful_connection_added(self):
        mgr = PFMCPManager(config={"mcp_servers": {"chrome": {"command": "npx", "args": ["-y"]}}})
        with mock.patch.object(MCPConnection, "connect", return_value=True):
            mgr.start_all()
        assert "chrome" in mgr.connections

    def test_failed_connection_not_added(self):
        mgr = PFMCPManager(config={"mcp_servers": {"chrome": {"command": "npx"}}})
        with mock.patch.object(MCPConnection, "connect", return_value=False):
            mgr.start_all()
        assert "chrome" not in mgr.connections

    def test_multiple_servers_all_connected(self):
        mgr = PFMCPManager(config={"mcp_servers": {
            "chrome": {"command": "npx"},
            "discord": {"command": "node"},
        }})
        with mock.patch.object(MCPConnection, "connect", return_value=True):
            mgr.start_all()
        assert set(mgr.connections.keys()) == {"chrome", "discord"}

    def test_enabled_true_explicit_connects(self):
        mgr = PFMCPManager(config={"mcp_servers": {"chrome": {"enabled": True, "command": "npx"}}})
        with mock.patch.object(MCPConnection, "connect", return_value=True):
            mgr.start_all()
        assert "chrome" in mgr.connections


# ---------------------------------------------------------------------------
# TestStopAll
# ---------------------------------------------------------------------------

class TestStopAll:
    def test_disconnects_all_and_clears(self):
        mgr = PFMCPManager()
        conn1 = mock.MagicMock()
        conn2 = mock.MagicMock()
        mgr.connections = {"a": conn1, "b": conn2}
        mgr.stop_all()
        conn1.disconnect.assert_called_once()
        conn2.disconnect.assert_called_once()
        assert mgr.connections == {}

    def test_empty_connections_no_error(self):
        mgr = PFMCPManager()
        mgr.stop_all()
        assert mgr.connections == {}


# ---------------------------------------------------------------------------
# TestRegisterTools
# ---------------------------------------------------------------------------

class TestRegisterTools:
    def test_registers_tool_with_prefixed_name(self, clean_tools_registry):
        mgr = PFMCPManager()
        conn = MCPConnection(name="chrome", command="npx")
        conn.tools = [{"name": "screenshot", "description": "Take a screenshot", "inputSchema": {}}]
        mgr.connections = {"chrome": conn}
        count = mgr.register_tools()
        assert count == 1
        assert "mcp_chrome_screenshot" in clean_tools_registry

    def test_description_prefixed_with_server_name(self, clean_tools_registry):
        mgr = PFMCPManager()
        conn = MCPConnection(name="chrome", command="npx")
        conn.tools = [{"name": "click", "description": "Click element", "inputSchema": {}}]
        mgr.connections = {"chrome": conn}
        mgr.register_tools()
        assert clean_tools_registry["mcp_chrome_click"]["description"] == "[MCP:chrome] Click element"

    def test_missing_description_uses_default(self, clean_tools_registry):
        mgr = PFMCPManager()
        conn = MCPConnection(name="chrome", command="npx")
        conn.tools = [{"name": "click", "inputSchema": {}}]
        mgr.connections = {"chrome": conn}
        mgr.register_tools()
        assert "MCP tool from chrome" in clean_tools_registry["mcp_chrome_click"]["description"]

    def test_parameters_converted_from_input_schema(self, clean_tools_registry):
        mgr = PFMCPManager()
        conn = MCPConnection(name="chrome", command="npx")
        conn.tools = [{
            "name": "type",
            "description": "Type text",
            "inputSchema": {"properties": {
                "text": {"type": "string", "description": "text to type"},
                "count": {"type": "integer", "description": "repeat count"},
            }},
        }]
        mgr.connections = {"chrome": conn}
        mgr.register_tools()
        params = clean_tools_registry["mcp_chrome_type"]["parameters"]
        assert params["text"] == {"type": "string", "description": "text to type"}
        assert params["count"] == {"type": "integer", "description": "repeat count"}

    def test_no_input_schema_gives_empty_parameters(self, clean_tools_registry):
        mgr = PFMCPManager()
        conn = MCPConnection(name="chrome", command="npx")
        conn.tools = [{"name": "noop", "description": "does nothing"}]
        mgr.connections = {"chrome": conn}
        mgr.register_tools()
        assert clean_tools_registry["mcp_chrome_noop"]["parameters"] == {}

    def test_returns_total_count_across_servers(self, clean_tools_registry):
        mgr = PFMCPManager()
        conn1 = MCPConnection(name="chrome", command="npx")
        conn1.tools = [{"name": "a", "inputSchema": {}}, {"name": "b", "inputSchema": {}}]
        conn2 = MCPConnection(name="discord", command="node")
        conn2.tools = [{"name": "send", "inputSchema": {}}]
        mgr.connections = {"chrome": conn1, "discord": conn2}
        count = mgr.register_tools()
        assert count == 3

    def test_no_connections_returns_zero(self, clean_tools_registry):
        mgr = PFMCPManager()
        count = mgr.register_tools()
        assert count == 0

    def test_handler_calls_connection_when_connected(self, clean_tools_registry):
        mgr = PFMCPManager()
        conn = MCPConnection(name="chrome", command="npx")
        conn.tools = [{"name": "click", "inputSchema": {}}]
        conn._connected = True
        conn.process = mock.MagicMock()
        conn.process.poll.return_value = None
        mgr.connections = {"chrome": conn}
        mgr.register_tools()
        with mock.patch.object(conn, "call_tool", return_value="clicked") as mock_call:
            result = clean_tools_registry["mcp_chrome_click"]["function"](x=1, y=2)
        assert result == "clicked"
        mock_call.assert_called_once_with("click", {"x": 1, "y": 2})

    def test_handler_returns_error_when_disconnected(self, clean_tools_registry):
        mgr = PFMCPManager()
        conn = MCPConnection(name="chrome", command="npx")
        conn.tools = [{"name": "click", "inputSchema": {}}]
        conn._connected = False
        mgr.connections = {"chrome": conn}
        mgr.register_tools()
        result = clean_tools_registry["mcp_chrome_click"]["function"]()
        assert "not connected" in result

    def test_handler_returns_error_when_server_removed(self, clean_tools_registry):
        mgr = PFMCPManager()
        conn = MCPConnection(name="chrome", command="npx")
        conn.tools = [{"name": "click", "inputSchema": {}}]
        mgr.connections = {"chrome": conn}
        mgr.register_tools()
        handler = clean_tools_registry["mcp_chrome_click"]["function"]
        del mgr.connections["chrome"]
        result = handler()
        assert "not connected" in result


# ---------------------------------------------------------------------------
# TestGetStatus
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_empty_connections(self):
        mgr = PFMCPManager()
        assert mgr.get_status() == []

    def test_status_reflects_connection_state(self):
        mgr = PFMCPManager()
        conn = MCPConnection(name="chrome", command="npx")
        conn.tools = [{"name": "screenshot"}, {"name": "click"}]
        conn._connected = True
        conn.process = mock.MagicMock()
        conn.process.poll.return_value = None
        mgr.connections = {"chrome": conn}
        status = mgr.get_status()
        assert status == [{
            "name": "chrome",
            "connected": True,
            "tools": 2,
            "tool_names": ["screenshot", "click"],
        }]

    def test_status_disconnected(self):
        mgr = PFMCPManager()
        conn = MCPConnection(name="chrome", command="npx")
        conn._connected = False
        mgr.connections = {"chrome": conn}
        status = mgr.get_status()
        assert status[0]["connected"] is False


# ---------------------------------------------------------------------------
# TestManagerCallTool
# ---------------------------------------------------------------------------

class TestManagerCallTool:
    def test_server_not_found(self):
        mgr = PFMCPManager()
        result = mgr.call_tool("missing", "tool")
        assert "not found" in result
        assert "missing" in result

    def test_delegates_to_connection(self):
        mgr = PFMCPManager()
        conn = mock.MagicMock()
        conn.call_tool.return_value = "done"
        mgr.connections = {"chrome": conn}
        result = mgr.call_tool("chrome", "screenshot", {"a": 1})
        assert result == "done"
        conn.call_tool.assert_called_once_with("screenshot", {"a": 1})

    def test_default_arguments_is_none(self):
        mgr = PFMCPManager()
        conn = mock.MagicMock()
        conn.call_tool.return_value = "ok"
        mgr.connections = {"chrome": conn}
        mgr.call_tool("chrome", "screenshot")
        conn.call_tool.assert_called_once_with("screenshot", None)
