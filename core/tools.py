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
    "python_exec",
    "Execute Python code",
    {"code": {"type": "string", "description": "Python code to execute"}},
)
def tool_python_exec(code: str, **kwargs) -> str:
    """Execute a Python code snippet in a subprocess."""
    try:
        result = subprocess.run(
            ["python", "-c", code], capture_output=True, text=True,
            timeout=30, encoding="utf-8", errors="replace",
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr] {result.stderr}"
        return output[:4000]
    except subprocess.TimeoutExpired:
        return "[error] Timeout (30s)"
    except Exception as e:
        return f"[error] {e}"


@register_tool(
    "web_fetch",
    "Fetch content from a URL",
    {"url": {"type": "string", "description": "URL to fetch"}},
)
def tool_web_fetch(url: str, **kwargs) -> str:
    """Fetch webpage content, stripping HTML tags for readability."""
    import requests
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Permafrost/1.0"})
        text = re.sub(r'<[^>]+>', '', r.text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:4000]
    except Exception as e:
        return f"[error] {e}"


@register_tool(
    "search_web",
    "Search the web using DuckDuckGo",
    {"query": {"type": "string", "description": "Search query"}},
)
def tool_search_web(query: str, **kwargs) -> str:
    """Search the web via DuckDuckGo Instant Answer API."""
    import requests
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json"},
            timeout=10,
        )
        data = r.json()
        results = []
        if data.get("Abstract"):
            results.append(f"Summary: {data['Abstract']}")
        for topic in data.get("RelatedTopics", [])[:5]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(f"- {topic['Text']}")
        return "\n".join(results) if results else "No results found."
    except Exception as e:
        return f"[error] {e}"


@register_tool(
    "edit_file",
    "Find and replace text in a file",
    {
        "path": {"type": "string", "description": "File path"},
        "old_text": {"type": "string", "description": "Text to find"},
        "new_text": {"type": "string", "description": "Replacement text"},
    },
)
def tool_edit_file(path: str, old_text: str, new_text: str, **kwargs) -> str:
    """Find and replace first occurrence of old_text with new_text in a file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if old_text not in content:
            return f"[error] old_text not found in {path}"
        content = content.replace(old_text, new_text, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Replaced in {path}"
    except Exception as e:
        return f"[error] {e}"


# ── Memory Tools (L1-L6 layered system) ──────────────────────

@register_tool("memory_save", "Save information to L2 verified knowledge (long-term)", {
    "name": {"type": "string", "description": "Memory name/title"},
    "content": {"type": "string", "description": "Content to remember"},
    "type": {"type": "string", "description": "Type: user/feedback/project/reference"},
})
def tool_memory_save(name, content, type="reference", **kwargs):
    from smart.memory import PFMemory
    try:
        mem = PFMemory()
        mem.save_l2(name, name, type, content)
        # Auto-index to vector store for semantic search
        try:
            from smart.vector import PFVectorSearch
            vs = PFVectorSearch(str(mem.data_dir))
            vs.index_memory(f"L2:{name}", f"{name} {content}", {"layer": "L2", "type": type})
        except Exception:
            pass  # Vector indexing is optional
        return f"[L2] Saved: {name} ({type})"
    except Exception as e:
        return f"[error] {e}"


@register_tool("memory_note", "Add a short-term dynamic note to L3 (auto-expires)", {
    "key": {"type": "string", "description": "Note key/title"},
    "value": {"type": "string", "description": "Note content"},
    "type": {"type": "string", "description": "Type: context(14d)/preference(30d)/progress(7d)/insight(21d)"},
})
def tool_memory_note(key, value, type="context", **kwargs):
    from smart.memory import PFMemory
    try:
        mem = PFMemory()
        importance = int(kwargs.get("importance", 3))
        mem.add_l3(key, value, type, importance)
        # Auto-index to vector store
        try:
            from smart.vector import PFVectorSearch
            vs = PFVectorSearch(str(mem.data_dir))
            vs.index_memory(f"L3:{key}", f"{key} {value}", {"layer": "L3", "type": type})
        except Exception:
            pass
        return f"[L3] Noted: {key} ({type})"
    except Exception as e:
        return f"[error] {e}"


@register_tool("memory_search", "Search across all memory layers (L2 + L3) with semantic vector search", {
    "query": {"type": "string", "description": "Search query (supports natural language semantic search)"},
})
def tool_memory_search(query, **kwargs):
    from smart.memory import PFMemory
    mem = PFMemory()

    # Try semantic search first, fallback to keyword
    results = mem.search_semantic(query, top_k=10)
    if not results:
        results = mem.search_all(query)

    if not results:
        return "No memories found."

    lines = []
    for r in results[:10]:
        # Handle both semantic and keyword result formats
        layer = r.get("layer", r.get("metadata", {}).get("layer", "?"))
        score = r.get("score", "")
        score_str = f" (score:{score:.2f})" if isinstance(score, float) else ""

        if layer == "L2":
            name = r.get("name", r.get("text", "")[:50])
            body = r.get("body", r.get("text", ""))[:200]
            lines.append(f"[L2:{r.get('type', r.get('metadata', {}).get('type', ''))}]{score_str} {name}: {body}")
        elif layer == "L3":
            key = r.get("key", "")
            value = r.get("value", r.get("text", ""))[:200]
            lines.append(f"[L3:{r.get('type', r.get('metadata', {}).get('type', ''))}]{score_str} {key}: {value}")
        else:
            lines.append(f"[{layer}]{score_str} {r.get('text', '')[:200]}")
    return "\n".join(lines)


@register_tool("memory_list", "List all saved memories across layers", {})
def tool_memory_list(**kwargs):
    from smart.memory import PFMemory, L2_TYPES
    mem = PFMemory()
    lines = []

    # L2: Verified Knowledge
    for mtype in L2_TYPES:
        items = mem.list_l2(mtype)
        if items:
            lines.append(f"\n[L2:{mtype}]")
            for i in items:
                lines.append(f"  - {i.get('name','')}: {i.get('description','')}")

    # L3: Dynamic
    l3 = mem.list_l3()
    if l3:
        lines.append(f"\n[L3] ({len(l3)} entries)")
        for e in l3[:15]:
            lines.append(f"  - [{e.get('type','')}] {e.get('key','')}: {e.get('value','')[:80]}")

    return "\n".join(lines) if lines else "No memories saved yet."


@register_tool("memory_gc", "Run garbage collection on L3 dynamic memories (expire/promote/archive)", {})
def tool_memory_gc(**kwargs):
    from smart.memory import PFMemory
    try:
        mem = PFMemory()
        result = mem.gc()
        return f"GC complete: kept={result['kept']}, promoted={result['promoted']}, archived={result['archived']}"
    except Exception as e:
        return f"[error] {e}"


@register_tool("memory_reindex", "Rebuild vector search index from all L2+L3 memories", {})
def tool_memory_reindex(**kwargs):
    from smart.memory import PFMemory
    try:
        mem = PFMemory()
        mem.index_all_memories()
        stats = mem.get_stats()
        vectors = stats.get("vectors", 0)
        return f"Vector index rebuilt: {vectors} entries indexed (L2+L3)"
    except Exception as e:
        return f"[error] {e}"


@register_tool("memory_stats", "Show memory layer statistics (L1-L6 + vector index)", {})
def tool_memory_stats(**kwargs):
    from smart.memory import PFMemory
    mem = PFMemory()
    stats = mem.get_stats()
    lines = [f"  {layer}: {count}" for layer, count in stats.items()]
    return "Memory Stats:\n" + "\n".join(lines)


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
        "You have access to these tools to interact with the computer:",
        "",
    ]
    for idx, (name, info) in enumerate(TOOLS.items(), 1):
        param_parts = []
        for pname in info["parameters"]:
            param_parts.append(pname)
        params_str = ", ".join(param_parts)
        lines.append(f"{idx}. {name}({params_str}) — {info['description']}")

    lines.extend([
        "",
        "To use a tool, write:",
        '[TOOL_CALL]{"name": "tool_name", "args": {"key": "value"}}[/TOOL_CALL]',
        "",
        "You can use multiple tools in sequence. After each tool call, you'll receive the result and can continue.",
        "Always use tools when asked to interact with files, run commands, or search for information.",
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
