"""
Permafrost Tool System — Let AI execute tools to interact with the system.

Built-in tools:
  - bash: Execute shell commands
  - read_file: Read file contents
  - write_file: Write/create files
  - list_files: List directory contents
  - web_search: Search the web (placeholder)

Tools are defined as functions with schema (for AI function calling).
Security layer (security.py) controls which tools are allowed.
"""

import json
import logging
import os
import re
import subprocess

log = logging.getLogger("permafrost.tools")

# ── Tool Registry ─────────────────────────────────────────────

TOOLS: dict[str, dict] = {}


def register_tool(name: str, description: str, parameters: dict):
    """Decorator to register a tool with its schema."""
    def decorator(func):
        TOOLS[name] = {
            "function": func,
            "description": description,
            "parameters": parameters,
        }
        return func
    return decorator


# ── Built-in Tools ────────────────────────────────────────────

@register_tool(
    "bash",
    "Execute a shell command",
    {"command": {"type": "string", "description": "Shell command to run"}},
)
def tool_bash(command: str, **kwargs) -> str:
    """Execute a shell command and return stdout + stderr."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=30, encoding="utf-8", errors="replace",
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr] {result.stderr}"
        return output[:4000]  # cap output
    except subprocess.TimeoutExpired:
        return "[error] Command timed out (30s)"
    except Exception as e:
        return f"[error] {e}"


@register_tool(
    "read_file",
    "Read a file's contents",
    {"path": {"type": "string", "description": "File path to read"}},
)
def tool_read_file(path: str, **kwargs) -> str:
    """Read file contents (capped at 10K chars)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(10000)
        return content
    except Exception as e:
        return f"[error] {e}"


@register_tool(
    "write_file",
    "Write content to a file (creates parent directories if needed)",
    {
        "path": {"type": "string", "description": "File path to write"},
        "content": {"type": "string", "description": "Content to write"},
    },
)
def tool_write_file(path: str, content: str, **kwargs) -> str:
    """Write content to a file, creating directories as needed."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"[error] {e}"


@register_tool(
    "list_files",
    "List files in a directory",
    {"path": {"type": "string", "description": "Directory path (default: current dir)"}},
)
def tool_list_files(path: str = ".", **kwargs) -> str:
    """List directory entries (capped at 100)."""
    try:
        entries = os.listdir(path)
        return "\n".join(entries[:100])
    except Exception as e:
        return f"[error] {e}"


@register_tool(
    "web_search",
    "Search the web (placeholder — not yet implemented)",
    {"query": {"type": "string", "description": "Search query"}},
)
def tool_web_search(query: str, **kwargs) -> str:
    """Placeholder for web search integration."""
    return f"[not implemented] web_search for: {query}"


# ── Tool Executor ─────────────────────────────────────────────

def execute_tool(name: str, args: dict, security=None) -> str:
    """Execute a registered tool by name.

    Args:
        name: Tool name (must be in TOOLS registry).
        args: Keyword arguments to pass to the tool function.
        security: Optional PFSecurity instance for authorization.

    Returns:
        Tool output string (always returns a string, never raises).
    """
    if name not in TOOLS:
        return f"[error] Unknown tool: {name}"

    # Security check via PFSecurity.authorize_tool()
    if security:
        allowed, reason = security.authorize_tool(name, args)
        if not allowed:
            log.warning(f"Tool '{name}' blocked: {reason}")
            return f"[blocked] Tool '{name}' not allowed: {reason}"

    try:
        return TOOLS[name]["function"](**args)
    except Exception as e:
        log.error(f"Tool '{name}' execution failed: {e}")
        return f"[error] Tool execution failed: {e}"


# ── Schema Export ─────────────────────────────────────────────

def get_tool_schemas() -> list[dict]:
    """Return tool schemas in OpenAI function-calling format.

    Useful for providers that support native function calling.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": info["description"],
                "parameters": {
                    "type": "object",
                    "properties": info["parameters"],
                },
            },
        }
        for name, info in TOOLS.items()
    ]


def get_tool_prompt() -> str:
    """Generate a text prompt describing available tools.

    Used for prompt-injection-based tool use (works with any LLM).
    """
    lines = [
        "You have access to these tools:",
        "",
    ]
    for name, info in TOOLS.items():
        param_parts = []
        for pname, pinfo in info["parameters"].items():
            desc = pinfo.get("description", pname)
            param_parts.append(f"{pname}")
        params_str = ", ".join(param_parts)
        lines.append(f"- {name}({params_str}): {info['description']}")

    lines.extend([
        "",
        "To use a tool, include in your response:",
        '[TOOL_CALL]{"name": "tool_name", "args": {"key": "value"}}[/TOOL_CALL]',
        "",
        "You can call multiple tools — use one [TOOL_CALL]...[/TOOL_CALL] block per tool.",
        "After each tool call, you will receive the result and can continue your response.",
        "Only use tools when needed to answer the question.",
    ])
    return "\n".join(lines)


# ── Tool Call Parser ──────────────────────────────────────────

_TOOL_CALL_PATTERN = re.compile(
    r"\[TOOL_CALL\]\s*(\{.*?\})\s*\[/TOOL_CALL\]",
    re.DOTALL,
)


def parse_tool_calls(text: str) -> list[dict]:
    """Parse [TOOL_CALL]...[/TOOL_CALL] blocks from AI response.

    Returns list of {"name": str, "args": dict} dicts.
    Invalid JSON blocks are skipped with a warning.
    """
    calls = []
    for match in _TOOL_CALL_PATTERN.finditer(text):
        raw = match.group(1)
        try:
            data = json.loads(raw)
            name = data.get("name", "")
            args = data.get("args", {})
            if name:
                calls.append({"name": name, "args": args})
            else:
                log.warning(f"Tool call missing 'name': {raw[:100]}")
        except json.JSONDecodeError as e:
            log.warning(f"Invalid tool call JSON: {e} — {raw[:100]}")
    return calls


def strip_tool_calls(text: str) -> str:
    """Remove [TOOL_CALL]...[/TOOL_CALL] blocks from text.

    Returns the text with tool call blocks removed,
    so the final response to the user is clean.
    """
    return _TOOL_CALL_PATTERN.sub("", text).strip()
