"""
Permafrost MCP Client — Connect to external MCP servers.

MCP (Model Context Protocol) lets PF use tools from any MCP-compatible server:
  - Chrome browser automation
  - Discord/Slack messaging
  - Database queries
  - File system operations
  - Any custom MCP server

Architecture:
  PF Brain -> MCP Client -> MCP Server (subprocess/stdio or HTTP)
                         -> Tool registry (auto-register as PF tools)

Config (in config.json):
  "mcp_servers": {
    "chrome": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-chrome"],
      "env": {}
    },
    "discord": {
      "command": "node",
      "args": ["path/to/discord-mcp/index.js"],
      "env": {"DISCORD_TOKEN": "..."}
    }
  }
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("permafrost.mcp")


class MCPConnection:
    """Single MCP server connection via stdio (JSON-RPC over stdin/stdout)."""

    def __init__(self, name: str, command: str, args: list = None, env: dict = None,
                 cwd: str = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd
        self.process: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self._request_id = 0
        self._lock = threading.Lock()
        self._connected = False

    def connect(self) -> bool:
        """Start the MCP server process and initialize."""
        try:
            full_env = os.environ.copy()
            full_env.update(self.env)

            cmd = [self.command] + self.args
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=full_env,
                cwd=self.cwd,
                bufsize=0,
            )

            # Initialize MCP protocol
            init_result = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "permafrost", "version": "0.8.0"},
            })

            if init_result is None:
                log.error(f"MCP [{self.name}] initialization failed")
                self.disconnect()
                return False

            # Send initialized notification
            self._send_notification("notifications/initialized", {})

            # List available tools
            tools_result = self._send_request("tools/list", {})
            if tools_result and "tools" in tools_result:
                self.tools = tools_result["tools"]
                log.info(f"MCP [{self.name}] connected: {len(self.tools)} tools")
            else:
                self.tools = []
                log.info(f"MCP [{self.name}] connected: no tools")

            self._connected = True
            return True

        except FileNotFoundError:
            log.error(f"MCP [{self.name}] command not found: {self.command}")
            return False
        except Exception as e:
            log.error(f"MCP [{self.name}] connection failed: {e}")
            self.disconnect()
            return False

    def disconnect(self):
        """Stop the MCP server process."""
        self._connected = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
        log.info(f"MCP [{self.name}] disconnected")

    def call_tool(self, tool_name: str, arguments: dict = None) -> str:
        """Call a tool on this MCP server."""
        if not self._connected or not self.process:
            return f"[error] MCP server '{self.name}' not connected"

        result = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments or {},
        })

        if result is None:
            return f"[error] MCP tool call failed: {tool_name}"

        # Extract text content from MCP response
        content = result.get("content", [])
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
                elif item.get("type") == "image":
                    texts.append(f"[image: {item.get('mimeType', 'unknown')}]")
            elif isinstance(item, str):
                texts.append(item)

        return "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)[:4000]

    @property
    def is_connected(self) -> bool:
        return self._connected and self.process is not None and self.process.poll() is None

    def _send_request(self, method: str, params: dict) -> dict | None:
        """Send a JSON-RPC request and wait for response."""
        with self._lock:
            self._request_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            }
            return self._rpc(request)

    def _send_notification(self, method: str, params: dict):
        """Send a JSON-RPC notification (no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._write(notification)

    def _rpc(self, request: dict) -> dict | None:
        """Execute a JSON-RPC call: write request, read response."""
        if not self.process or not self.process.stdin or not self.process.stdout:
            return None

        self._write(request)

        # Read response (with timeout)
        try:
            line = self.process.stdout.readline()
            if not line:
                return None
            response = json.loads(line.decode("utf-8").strip())
            if "error" in response:
                log.warning(f"MCP [{self.name}] error: {response['error']}")
                return None
            return response.get("result", {})
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            log.error(f"MCP [{self.name}] read error: {e}")
            return None

    def _write(self, data: dict):
        """Write a JSON-RPC message to the process stdin."""
        if not self.process or not self.process.stdin:
            return
        try:
            msg = json.dumps(data, ensure_ascii=False) + "\n"
            self.process.stdin.write(msg.encode("utf-8"))
            self.process.stdin.flush()
        except (OSError, BrokenPipeError) as e:
            log.error(f"MCP [{self.name}] write error: {e}")
            self._connected = False


class PFMCPManager:
    """Manages multiple MCP server connections and registers their tools."""

    def __init__(self, config: dict = None, data_dir: str = None):
        self.config = config or {}
        self.data_dir = Path(data_dir) if data_dir else Path.home() / ".permafrost"
        self.connections: dict[str, MCPConnection] = {}

    def start_all(self):
        """Connect to all configured MCP servers."""
        mcp_config = self.config.get("mcp_servers", {})
        if not mcp_config:
            log.debug("No MCP servers configured")
            return

        for name, server_config in mcp_config.items():
            if not server_config.get("enabled", True):
                continue
            command = server_config.get("command", "")
            if not command:
                continue

            conn = MCPConnection(
                name=name,
                command=command,
                args=server_config.get("args", []),
                env=server_config.get("env", {}),
                cwd=server_config.get("cwd"),
            )

            if conn.connect():
                self.connections[name] = conn
                log.info(f"MCP [{name}] ready: {len(conn.tools)} tools")
            else:
                log.warning(f"MCP [{name}] failed to connect")

    def stop_all(self):
        """Disconnect all MCP servers."""
        for name, conn in self.connections.items():
            conn.disconnect()
        self.connections.clear()

    def register_tools(self):
        """Register all MCP tools as PF tools so the brain can use them."""
        from core.tools import TOOLS

        count = 0
        for server_name, conn in self.connections.items():
            for tool in conn.tools:
                tool_name = f"mcp_{server_name}_{tool['name']}"
                description = tool.get("description", f"MCP tool from {server_name}")

                # Convert MCP input schema to PF parameters
                pf_params = {}
                input_schema = tool.get("inputSchema", {})
                for prop_name, prop_info in input_schema.get("properties", {}).items():
                    pf_params[prop_name] = {
                        "type": prop_info.get("type", "string"),
                        "description": prop_info.get("description", ""),
                    }

                # Create a closure for this specific tool
                def make_handler(srv_name, tl_name):
                    def handler(**kwargs):
                        c = self.connections.get(srv_name)
                        if not c or not c.is_connected:
                            return f"[error] MCP server '{srv_name}' not connected"
                        return c.call_tool(tl_name, kwargs)
                    return handler

                TOOLS[tool_name] = {
                    "function": make_handler(server_name, tool["name"]),
                    "description": f"[MCP:{server_name}] {description}",
                    "parameters": pf_params,
                }
                count += 1

        if count:
            log.info(f"Registered {count} MCP tools as PF tools")
        return count

    def get_status(self) -> list[dict]:
        """Get status of all MCP connections."""
        result = []
        for name, conn in self.connections.items():
            result.append({
                "name": name,
                "connected": conn.is_connected,
                "tools": len(conn.tools),
                "tool_names": [t["name"] for t in conn.tools],
            })
        return result

    def call_tool(self, server_name: str, tool_name: str, arguments: dict = None) -> str:
        """Call a tool on a specific MCP server."""
        conn = self.connections.get(server_name)
        if not conn:
            return f"[error] MCP server '{server_name}' not found"
        return conn.call_tool(tool_name, arguments)
