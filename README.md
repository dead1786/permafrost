<p align="center">
  <img src="docs/banner.png" alt="Permafrost" width="400">
</p>

<h1 align="center">Permafrost (PF) — 雪花</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.10%2B-green.svg" alt="Python"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/Tests-159%20passed-brightgreen.svg" alt="Tests"></a>
</p>

> Turn any AI agent from a one-time tool into a 24/7 autonomous brain.

## What is Permafrost?

Permafrost is an open-source framework that gives AI agents **persistence, scheduling, self-healing, and multi-channel communication**. Like permafrost in nature — always there, never melting.

## Quick Start

```bash
# Windows
start.bat

# Linux/Mac
./start.sh
```

That's it. A browser opens, you pick your AI model, connect your channels, and your brain goes online.

## Features

| Feature | Description |
|---------|-------------|
| **Persistent Brain** | Stream-JSON sessions that survive restarts. Zero cold-start. |
| **Multi-Model Support** | Claude, GPT-4o, Gemini, Ollama, OpenRouter — pluggable provider layer. |
| **Smart Scheduler** | Cron + one-shot + interval tasks with ack-based completion tracking. |
| **Self-Healing Watchdog** | Auto-detects and fixes daemon failures with restart limits. |
| **Context Guard** | Auto-backup and compaction when context window fills up. |
| **Channel Plugins** | Telegram, Discord, LINE, Web Chat — one brain, many interfaces. |
| **Web Console** | Setup wizard, chat, schedule tasks, monitor status — all from browser. |
| **Memory Layers** | Structured memory with auto-GC, pitfall tracking, and self-reflection. |
| **Night Silence** | Queue non-urgent notifications during sleep hours. |
| **Multi-Agent** | Cross-machine AI coordination via inbox/outbox messaging. |

## Architecture

```
permafrost/
├── core/
│   ├── brain.py          # Persistent AI session engine
│   ├── providers.py      # Multi-model abstraction (Claude/GPT/Gemini/Ollama/OpenRouter)
│   ├── scheduler.py      # Cron-like task engine with ack tracking
│   ├── watchdog.py       # Self-healing daemon monitor
│   ├── guard.py          # Context overflow protection
│   ├── notifier.py       # Unified notification routing
│   └── multi_agent.py    # Cross-machine AI coordination
├── channels/
│   ├── base.py           # Channel plugin base class + registry
│   ├── telegram.py       # Telegram Bot API (polling)
│   ├── discord.py        # Discord Bot (REST API)
│   ├── line.py           # LINE Messaging API (webhook)
│   └── web.py            # Built-in web chat (file-based)
├── console/
│   └── app.py            # Web UI (Streamlit) — setup wizard + chat + status
├── smart/
│   ├── pitfalls.py       # Auto error learning
│   ├── memory.py         # Layered memory system with GC
│   ├── reflection.py     # Nightly self-review
│   ├── night_silence.py  # Night notification queue
│   └── handover.py       # Cross-restart task continuity
├── launcher.py           # Unified daemon launcher (brain + scheduler + watchdog)
├── start.bat             # Windows launcher
└── start.sh              # Linux/Mac launcher
```

## Supported AI Models

| Provider | Models | API Key |
|----------|--------|---------|
| **Claude** (Anthropic) | claude-sonnet-4, claude-opus-4 | Required |
| **GPT** (OpenAI) | gpt-4o, gpt-4o-mini, o3-mini | Required |
| **Gemini** (Google) | gemini-2.0-flash, gemini-2.5-pro | Required |
| **Ollama** (Local) | llama3, mistral, codestral | Not needed |
| **OpenRouter** | Any model via aggregator | Required |

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

## Roadmap

### Phase 1: Core Engine (Done)
- Persistent brain session engine
- Multi-model provider abstraction (Claude/GPT/Gemini/Ollama/OpenRouter)
- Smart scheduler with ack tracking
- Self-healing watchdog
- Context guard (auto-backup + compaction)
- Multi-channel plugins (Telegram/Discord/LINE/Web)

### Phase 2: Web Console (Done)
- Setup wizard (provider + channel config)
- Chat interface with inbox/outbox polling
- Schedule management UI
- Status dashboard with heartbeat monitoring

### Phase 3: Intelligence Layer (Next)
**3.1 Vector Hybrid Memory Retrieval**
- Add embedding-based vector search to memory layer
- BM25 keyword search + vector similarity fusion
- MMR (Maximal Marginal Relevance) reranking for diversity
- Temporal decay: recent memories score higher
- Provider-agnostic: support OpenAI, Gemini, Voyage, Ollama embeddings
- Target: 3x better memory recall accuracy vs keyword-only

**3.2 Feedback Loop (Self-Evolution)**
- Post-execution quality evaluation (auto-score each task result)
- Failure pattern detection: repeated errors -> auto-adjust parameters
- Memory retrieval feedback: track hit/miss -> optimize index weights
- Skill effectiveness tracking: which skills solve which problems
- Nightly reflection with concrete action items (not just notes)
- Evolution log: what changed, why, measured impact
- Goal: AI improves measurably over time without human intervention

**3.3 Provider-Agnostic Context Management (Future)**
- Abstract compaction away from CLI-specific tools
- LLM-based summarization with quality guard (retry if summary is poor)
- Adaptive chunk ratio based on context usage
- Support any LLM API for summarization, not tied to Claude Code /compact

### Phase 4: Multi-Agent Orchestration (Planned)
- Cross-device AI team coordination
- Persistent personality per agent
- DC-style communication bus (inbox/outbox/office channels)
- Scoring + raid system for team accountability
- Task dependency tracking

### Competitive Positioning

| | Permafrost | OpenClaw |
|---|-----------|----------|
| Focus | Multi-device AI team | Single-machine personal assistant |
| Agents | Persistent personality, team roles | Generic task runner |
| Memory | L1-L6 layered, never-delete sedimentation | Vector store + temporal decay |
| Security | 6-layer hooks + integrity checking | Sandbox only |
| Communication | DC channels + cross-machine IPC | Multi-channel messaging |
| Self-evolution | Feedback loop (Phase 3.2) | None (framework only) |
| Team accountability | Scoring + raid + audit | None |

## Security

Permafrost includes a 4-layer security system. See [SECURITY.md](SECURITY.md) for details.

## Contributing

Contributions welcome! Please:
1. Fork the repo
2. Create a feature branch
3. Add tests for new functionality
4. Submit a PR

## License

MIT — see [LICENSE](LICENSE)
