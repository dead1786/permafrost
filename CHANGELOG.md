# Changelog

## v0.9.0 (2026-03-31)

### Added
- **Provider Fallback Chain** (`core/provider_fallback.py`): Automatic failover between AI providers when primary fails. Uses 9-type error classification (rate limit, auth, billing, overloaded, etc.) to make intelligent failover decisions. Features:
  - Ordered chain of providers tried in sequence
  - Exponential cooldown with automatic recovery probing
  - Permanent disable for revoked keys / missing models
  - Context overflow errors raised immediately (no pointless failover)
  - Status reporting and manual reset
  - Integrated into Brain via `_chat()` / `_chat_with_tools()` helper methods
- **37 new tests** for provider fallback chain (error classification, failover, cooldown recovery, tools, status, reset, edge cases)

### Changed
- Brain now routes all AI calls through fallback chain when `fallback_chain` is configured in config.json
- `core/__init__.py` now exports both `get_tool_schemas` (OpenAI format) and `get_tools_schema` (multi-provider format)
- Token tracker pricing updated for GPT-4.1 family, Claude Haiku 4.5, and OpenRouter variants
- Python version requirement corrected to `>=3.10` in pyproject.toml (was `>=3.11`, code uses 3.10 union syntax)
- Config example updated with `fallback_chain` and `fallback_cooldown` fields
- Test badge updated: 76 -> 113 tests

### Fixed
- `get_tools_schema()` (multi-provider version) was not exported from `core/__init__.py`
- Token tracker missing pricing for `claude-haiku-4-5-20251001`, `gpt-4.1`, `gpt-4.1-mini`, `gpt-4.1-nano`

## v0.8.0 (2026-03-21)

### Added
- **13 AI Providers**: Claude, GPT, Gemini, Ollama, OpenRouter + 8 free OAuth (Qwen, Copilot, Codex, MiniMax, Chutes, Claude CLI, LM Studio, Echo)
- **64 Built-in Tools** across 11 categories: System, Web, Memory, Documents (PDF/DOCX/Excel), Utility, Reminder, Network, Git, Text/Data, Multi-Agent, Meta
- **Native Function Calling**: Claude/GPT/Gemini use API-level tool schemas; fallback text injection for others
- **Tool Call Normalization**: `normalize_tool_calls()` converts any AI output format to standard `[TOOL_CALL]`
- **AI Context Compactor** (`core/compactor.py`): AI-powered conversation compression with configurable thresholds
- **Background Agents** (`core/agents.py`): Daemon thread agents for memory-maintenance, context-extractor, health-check with stall detection (60s warning, 6h hard limit)
- **Hybrid Vector Search** (`smart/vector.py`): Cosine + BM25 + temporal decay + MMR reranking
- **Plugin System** (`core/plugins.py`): Auto-discover plugins from `plugins/` directory with manifest
- **MCP Client** (`core/mcp_client.py`): Connect to external MCP servers via stdio JSON-RPC
- **Multi-Agent Spawning** (`core/multi_agent.py`): Independent sub-agents with isolated memory
- **AI Self-Evolution**: `update_rules` tool lets AI modify its own L1 rules; `create_tool` lets AI write new tools at runtime
- **Workspace Boundary Enforcement**: Path-policy sandbox escape prevention (symlink-aware, Windows-compatible)
- **Error Classification**: 9-type FailoverReason for intelligent provider fallback decisions
- **Payload Redaction**: Deep-traverse image data redaction for diagnostic logging
- **OpenClaw-Inspired Prompt**: Mandatory pre-reply checks, wrong/right examples, NEVER/ALWAYS rules
- **76 Automated Tests**: tools (25), vector (19), memory (14), providers (11), security (23+)

### Changed
- Security upgraded from 4-layer to 6-layer (added workspace boundary + audit log improvements)
- L1 rules now always overwrite framework rules on startup (AI custom rules preserved)
- Source tag moved from user message to system message (prevents AI echoing metadata)
- Brain maintenance loop: every 10 min auto-checks context level, compaction, GC, health
- Web Console: provider labels with [FREE]/[API KEY] tags, model dropdown, context status panel

### Fixed
- `_TOOL_CALL_VARIANTS` NameError (renamed to `_TOOL_CALL_ANY` but references not updated)
- `os` not imported in providers.py (OAuth providers used `os.path.expanduser`)
- requirements.txt em dash causing cp950 UnicodeDecodeError on Windows
- Signal handler crash when brain runs in non-main thread
- Claude CLI `--output-format=stream-json` requires `--verbose` with `--print`
- Gemini tool schema: all functions must be in ONE object with `function_declarations`
- Gemini `number` type rejected (changed to `string`)
- Echo provider matching "now" from source tag substring
- Web Console `is_configured()` blocking no-key providers
- Chat page flickering from status page auto-refresh

## v0.2.1 (2026-03-21)

### Fixed
- Brain writes heartbeat immediately on start (prevents watchdog false restart)
- PID lock prevents duplicate brain processes
- All provider SDKs included in requirements.txt (users no longer hit missing module errors)

## v0.2.0 (2026-03-21)

### Added
- 4-layer security system: tool whitelist, file ACL, prompt injection detection (20+ patterns), rate limiter
- AI-guided persona wizard: 8-question interactive setup, bilingual (EN/ZH)
- 5-step setup wizard: model -> channels -> persona -> security -> launch
- Docker deployment: multi-stage build, non-root, healthcheck, secrets management
- 159 unit + E2E tests across 6 test files
- Security audit logging

### Changed
- Restructured Brain core for multi-provider support (Anthropic, OpenAI, Google, OpenRouter)
- Improved channel abstraction for Telegram, Discord, and Web

## v0.1.0 (2026-03-15)

### Added
- Initial release
- Brain core: message processing + provider routing
- Channel system: Telegram, Discord, Web support
- Provider abstraction: Anthropic Claude, OpenAI GPT, Google Gemini, OpenRouter
- Configuration via JSON
- Basic persona system
