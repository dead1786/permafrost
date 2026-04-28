<p align="center">
  <img src="docs/banner.png" alt="Permafrost" width="400">
</p>

<h1 align="center">Permafrost (PF) — 雪花</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.10%2B-green.svg" alt="Python"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/Tests-317%20passed-brightgreen.svg" alt="Tests"></a>
</p>

> Turn any AI into a persistent, self-improving companion that remembers everything, uses tools, and never sleeps.

## What is Permafrost?

Permafrost is an open-source framework that gives AI agents **persistence, memory, tools, scheduling, and multi-channel communication**. Connect any LLM (Claude, GPT, Gemini, Ollama, or 8 free providers), and it becomes a 24/7 autonomous brain with 64 built-in tools.

## Quick Start

```bash
pip install -r requirements.txt

# Windows
start.bat

# Linux/Mac
./start.sh
```

A browser opens. Pick your AI model (8 providers work without API keys), connect channels, and your brain goes online.

## Features

| Feature | Description |
|---------|-------------|
| **13 AI Providers** | Claude, GPT, Gemini, Ollama, OpenRouter + 8 free OAuth providers (no API key needed) |
| **64 Built-in Tools** | File ops, web, memory, PDF/DOCX/Excel, git, network, QR codes, multi-agent, and more |
| **Native Function Calling** | Claude/GPT/Gemini use API-level tool schemas; others get intelligent fallback |
| **L1-L6 Memory System** | Rules / Knowledge / Dynamic / Monthly Archive / Quarterly / Yearly + auto-GC + promotion |
| **Hybrid Vector Search** | Cosine + BM25 + temporal decay + MMR reranking for intelligent memory retrieval |
| **AI Context Compactor** | AI-powered conversation compression (not blind truncation) with auto context-level tracking |
| **Background Agents** | Daemon threads for memory maintenance, context extraction, health checks |
| **Plugin System** | Auto-discover plugins from directory with manifest-based loading |
| **MCP Client** | Connect to external MCP servers via stdio JSON-RPC |
| **AI Self-Evolution** | AI can modify its own rules and create new tools at runtime |
| **6-Layer Security** | Tool whitelist, file ACL, injection detection, rate limiting, approval system, audit log |
| **Multi-Channel** | Telegram, Discord, LINE, Web Chat — one brain, many interfaces |
| **Smart Scheduler** | Cron + one-shot + interval tasks with ack-based completion tracking |
| **Provider Fallback Chain** | Automatic failover between AI providers with error classification and cooldown probing |
| **Multi-Agent** | Spawn independent sub-agents with isolated memory + stall detection |
| **Night Silence** | Queue non-urgent notifications during sleep hours |

## Architecture

```
permafrost/
├── core/
│   ├── brain.py          # Persistent AI session engine with maintenance loop
│   ├── providers.py      # 13 AI providers (Claude/GPT/Gemini/Ollama/8 free OAuth)
│   ├── tools.py          # 64 tools across 11 categories + tool call normalization
│   ├── compactor.py      # AI-powered context compression
│   ├── agents.py         # Background agent framework with stall detection
│   ├── plugins.py        # Plugin auto-discovery from plugins/ directory
│   ├── mcp_client.py     # MCP server connection via stdio JSON-RPC
│   ├── multi_agent.py    # Independent sub-agent spawning
│   ├── security.py       # 6-layer security (whitelist/ACL/injection/rate/approval/audit)
│   ├── scheduler.py      # Cron-like task engine with ack tracking
│   ├── watchdog.py       # Self-healing daemon monitor
│   ├── provider_fallback.py # Provider fallback chain with error classification
│   ├── guard.py          # Context overflow protection
│   └── notifier.py       # Unified notification routing
├── channels/
│   ├── telegram.py       # Telegram Bot API (polling)
│   ├── discord.py        # Discord Bot (REST API)
│   ├── line.py           # LINE Messaging API (webhook via Cloudflare tunnel)
│   └── web.py            # Built-in web chat
├── console/
│   └── app.py            # Web UI (Streamlit) — setup, chat, status, agents panel
├── smart/
│   ├── memory.py         # L1-L6 layered memory with semantic search
│   ├── vector.py         # Hybrid vector search (cosine+BM25+MMR+decay)
│   ├── default_prompt.py # AI behavior prompt with mandatory pre-reply checks
│   ├── rules_template.py # L1 rules with tool reference (58 tools documented)
│   └── evolution.py      # Self-improvement engine
├── plugins/              # Auto-discovered plugin directory
├── tests/                # 317 automated tests (tools/vector/memory/providers/security/fallback/guard/token_tracker)
├── launcher.py           # Unified daemon launcher
└── start.bat / start.sh  # One-click launchers
```

## Supported AI Providers

### API Key Required
| Provider | Models | Notes |
|----------|--------|-------|
| **Claude** (Anthropic) | claude-sonnet-4, claude-opus-4, claude-haiku-4.5 | Native function calling |
| **GPT** (OpenAI) | gpt-4o, gpt-4o-mini, o3-mini, gpt-4.1 | Native function calling |
| **Gemini** (Google) | gemini-2.0-flash, gemini-2.5-pro | Native function calling |
| **OpenRouter** | Any model via aggregator | Unified access to 100+ models |

### Free (No API Key)
| Provider | How It Works | Models |
|----------|-------------|--------|
| **Ollama** | Local LLM server | llama3, mistral, codestral, qwen2, gemma2 |
| **LM Studio** | Local OpenAI-compatible | Any GGUF model |
| **Claude CLI** | Your Claude subscription | claude-sonnet-4, claude-opus-4 |
| **Qwen** | OAuth device code flow | qwen-coder-plus, qwen-turbo, qwen-max |
| **GitHub Copilot** | OAuth device code flow | copilot-chat |
| **OpenAI Codex** | Browser OAuth flow | gpt-4o via ChatGPT subscription |
| **MiniMax** | OAuth flow | minimax-01 |
| **Chutes** | PKCE OAuth flow | Various open-source models |
| **Echo** | Built-in test mode | No AI needed, great for testing tools |
| **Custom** | Any OpenAI-compatible endpoint | Point to any local proxy |

## Adding a Custom Provider

```python
from core.providers import BaseProvider, register_provider

@register_provider("my_provider")
class MyProvider(BaseProvider):
    LABEL = "My Custom LLM"
    NEEDS_API_KEY = True
    DEFAULT_MODEL = "my-model-v1"

    def chat(self, messages: list[dict], **kwargs) -> str:
        # Your API call here
        return "response"
```

## Adding a Custom Channel

```python
from channels.base import BaseChannel, register_channel

@register_channel("slack")
class PFSlack(BaseChannel):
    LABEL = "Slack"
    CONFIG_FIELDS = [
        {"name": "slack_token", "label": "Bot Token", "type": "password", "required": True},
    ]

    @property
    def name(self) -> str:
        return "slack"

    def send_message(self, text: str, **kwargs) -> bool:
        # Your send logic here
        return True

    def run(self):
        # Your receive loop here
        pass
```

## Built-in Tools (64)

| Category | Tools | Count |
|----------|-------|-------|
| **System** | bash, read/write/edit/append/list files, grep | 8 |
| **Web** | search, fetch, HTTP request | 3 |
| **Memory** | save, note, search, list, GC, reindex, stats | 7 |
| **Documents** | create/read PDF, create/read DOCX, create/read spreadsheet, read image | 7 |
| **Utility** | datetime, calculate, clipboard, screenshot, system info | 5 |
| **Reminder** | set/list/delete reminders | 3 |
| **Network** | ping, port check, DNS lookup | 3 |
| **Git** | status, log, diff | 3 |
| **Text/Data** | encode/decode, regex, text stats, password gen, UUID gen | 5 |
| **Multi-Agent** | spawn, send, list, read outbox | 4 |
| **Meta** | update rules (AI self-modify), create tool (AI self-extend) | 2 |
| **Other** | QR code, schedule add/list, compress, extract, diff, hash, download | 7+ |

## Roadmap

### Done
- 13 AI providers (8 free, no API key needed)
- 64 built-in tools with native function calling
- L1-L6 memory system with hybrid vector search
- AI context compaction (not blind truncation)
- Provider fallback chain with error classification and cooldown probing
- Background agents with stall detection
- Plugin system + MCP client
- AI self-evolution (modify rules, create tools)
- 6-layer security system
- Multi-channel (Telegram/Discord/LINE/Web)
- Web console with setup wizard + chat + status

### Next
- React/Next.js frontend (replace Streamlit)
- Voice input/output
- Plugin marketplace
- Multi-stage compaction pipeline
- Mobile app

## Security

Permafrost includes a 6-layer security system:

1. **Tool Whitelist** — only approved tools can be called
2. **File ACL** — restrict which paths AI can read/write
3. **Workspace Boundary** — prevent sandbox escape (symlink-aware, Windows-compatible)
4. **Prompt Injection Detection** — block override/hijack/exfiltration attempts
5. **Rate Limiter** — prevent runaway loops and token abuse
6. **Audit Log** — every action recorded for review

Plus: payload redaction for logging, error classification for intelligent fallback.

> **Warning**: Do NOT run Permafrost with administrator/root privileges. The AI has tool access (bash, file operations) — running as admin gives it full system control. Always use a normal user account.

## Contributing

Contributions welcome! Please:
1. Fork the repo
2. Create a feature branch
3. Add tests for new functionality
4. Submit a PR

## License

MIT — see [LICENSE](LICENSE)
