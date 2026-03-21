"""
Permafrost Brain — Persistent AI session engine.

The brain maintains a long-running AI session via stream-JSON protocol,
routing messages from multiple channels through a single persistent context.

Architecture:
  Channels (TG/Web/etc) -> inbox queues (JSON files) -> Brain picks up ->
  AI processes -> Brain routes reply back to source channel

Key features:
  - Zero cold-start: session persists across messages
  - Multi-channel routing: one brain handles all channels
  - Wake signal: channels write trigger file to wake brain from idle
  - Heartbeat: periodic health check file for watchdog monitoring
  - Multi-model: pluggable AI providers via core.providers
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from .hooks import HookManager
from .providers import create_provider, BaseProvider
from .tools import execute_tool, get_tool_prompt, get_tools_schema, parse_tool_calls, strip_tool_calls, has_tool_calls, normalize_tool_calls
from .compactor import PFCompactor
from .agents import PFAgentManager, agent_memory_maintenance, agent_context_extractor, agent_health_check
from .plugins import PFPluginManager
from .mcp_client import PFMCPManager
from smart.default_prompt import build_default_prompt

log = logging.getLogger("permafrost.brain")

# ── Configuration ──────────────────────────────────────────────

DEFAULT_CONFIG = {
    "ai_provider": "claude",        # claude | openai | gemini | ollama | openrouter
    "ai_model": "",                 # model ID (provider-specific)
    "api_key": "",                  # API key or endpoint
    "ai_timeout": 120,              # seconds per AI call
    "ai_max_retries": 2,            # retry count on failure
    "poll_interval": 1.0,           # seconds between inbox checks
    "idle_interval": 5.0,           # seconds when no activity
    "heartbeat_interval": 60,       # seconds between heartbeat writes
    "max_context_pct": 70,          # trigger compaction at this %
    "data_dir": "",                 # base directory for all data files
    "system_prompt": "",            # optional system prompt for AI
    "enable_tools": True,           # enable tool use (AI can call tools)
    "max_tool_rounds": 5,           # max consecutive tool-use rounds per message
    "allowed_user_ids": "",         # comma-separated user IDs (empty = allow all)
    # Context compaction settings
    "compact_message_threshold": 30,    # trigger compaction after this many messages
    "compact_keep_recent": 10,          # keep this many recent messages during compaction
    "compact_cooldown": 300,            # seconds between compactions
    # Self-maintenance settings
    "maintenance_interval": 600,        # seconds between maintenance cycles (10 min)
}


class PFBrain:
    """Persistent AI brain with multi-channel message routing."""

    def __init__(self, config_path: str = None):
        self.config = self._load_config(config_path)
        self.data_dir = Path(self.config["data_dir"] or os.path.expanduser("~/.permafrost"))
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # File paths
        self.pid_file = self.data_dir / "brain.pid"
        self.heartbeat_file = self.data_dir / "brain-heartbeat.json"
        self.state_file = self.data_dir / "brain-state.json"
        self.wake_trigger = self.data_dir / "brain-wake.trigger"
        self.reload_trigger = self.data_dir / "brain-reload.trigger"
        self.message_log = self.data_dir / "message-log.json"

        # Channel inboxes (registered dynamically)
        self.channel_inboxes = {}   # name -> Path
        self.channel_handlers = {}  # name -> callback(reply, original_msg)

        # AI provider (lazy init)
        self._provider: BaseProvider | None = None

        # Conversation history for context
        self._conversation: list[dict] = []
        self._max_history = 50  # messages to keep in memory
        self._conversation_file = self.data_dir / "brain-conversation.json"

        # Hook system
        self.hooks = HookManager(self.config)

        # Security (for tool authorization)
        self._security = None
        if self.config.get("enable_tools"):
            try:
                from .security import PFSecurity
                self._security = PFSecurity(
                    config=self.config.get("security", {}),
                    data_dir=str(self.data_dir),
                )
            except Exception as e:
                log.warning(f"Security init failed (tools run without auth): {e}")

        # Context compactor (AI-powered conversation compression)
        self.compactor = PFCompactor(
            data_dir=str(self.data_dir),
            config=self.config,
        )

        # Background agent manager
        self.agent_manager = PFAgentManager(
            data_dir=str(self.data_dir),
            config=self.config,
        )

        # Context level tracking
        self.context_level_file = self.data_dir / "context-level.json"

        # State
        self.running = False
        self.loop_count = 0
        self.last_heartbeat = 0
        self._last_maintenance = 0  # timestamp of last maintenance cycle
        self._maintenance_interval = self.config.get("maintenance_interval", 600)  # 10 min

        # Restore conversation from disk
        self._load_conversation()

    def _load_conversation(self):
        """Restore conversation history from disk."""
        if self._conversation_file.exists():
            try:
                with open(self._conversation_file, "r", encoding="utf-8") as f:
                    self._conversation = json.load(f)
                log.info(f"restored {len(self._conversation)} conversation entries")
            except (json.JSONDecodeError, OSError):
                self._conversation = []

    def _save_conversation(self):
        """Persist conversation history to disk."""
        try:
            with open(self._conversation_file, "w", encoding="utf-8") as f:
                json.dump(self._conversation, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log.error(f"conversation save failed: {e}")

    def _update_context_level(self):
        """Write current context usage to context-level.json for guard/UI."""
        level = self.compactor.get_context_level(
            self._conversation, self._max_history
        )
        try:
            with open(self.context_level_file, "w", encoding="utf-8") as f:
                json.dump({
                    "percent": round(level, 1),
                    "messages": len(self._conversation),
                    "max_messages": self._max_history * 2,
                    "timestamp": time.time(),
                }, f, indent=2)
        except OSError as e:
            log.debug(f"context level write failed: {e}")

    def _auto_compact(self):
        """Check and trigger context compaction if needed."""
        if not self.compactor.should_compact(self._conversation):
            return

        log.info(f"Auto-compaction triggered ({len(self._conversation)} messages)")

        # Run context extraction agent first (save important info before compacting)
        if not self.agent_manager.is_running("context-extractor"):
            self.agent_manager.run_agent(
                "context-extractor",
                agent_context_extractor,
                provider=self.provider,
            )
            # Give it a moment to start
            time.sleep(1)

        # Compact the conversation
        compacted = self.compactor.compact(self._conversation, self.provider)
        old_len = len(self._conversation)
        self._conversation = compacted
        self._save_conversation()
        self._update_context_level()

        log.info(f"Compaction done: {old_len} -> {len(self._conversation)} messages")

        # Hook: on_compact
        self.hooks.emit("on_compact", {
            "before": old_len,
            "after": len(self._conversation),
        })

    def _run_maintenance(self):
        """Periodic self-maintenance cycle.

        Runs every maintenance_interval seconds:
          1. Update context level
          2. Check if compaction needed
          3. Run memory GC agent (if not already running)
          4. Run health check agent periodically
        """
        now = time.time()
        if now - self._last_maintenance < self._maintenance_interval:
            return
        self._last_maintenance = now

        log.debug("Running maintenance cycle")

        # Update context tracking
        self._update_context_level()

        # Check compaction
        self._auto_compact()

        # Memory maintenance (GC, promotion, index)
        if not self.agent_manager.is_running("memory-maintenance"):
            self.agent_manager.run_agent(
                "memory-maintenance",
                agent_memory_maintenance,
            )

        # Health check (less frequent — every 5 maintenance cycles)
        if self.loop_count % 5 == 0 and not self.agent_manager.is_running("health-check"):
            self.agent_manager.run_agent(
                "health-check",
                agent_health_check,
            )

    def _setup_signal_handlers(self):
        """Register graceful shutdown handlers (main thread only)."""
        import threading
        if threading.current_thread() is not threading.main_thread():
            log.debug("Not main thread, skipping signal handlers")
            return

        def handler(signum, frame):
            log.info(f"received signal {signum}, shutting down...")
            self.running = False

        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, handler)
            signal.signal(signal.SIGHUP, handler)
        signal.signal(signal.SIGINT, handler)

    def _load_config(self, path: str = None) -> dict:
        """Load config from file, falling back to defaults."""
        config = DEFAULT_CONFIG.copy()
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            config.update(user_config)
        return config

    @property
    def provider(self) -> BaseProvider:
        """Lazy-init AI provider from config."""
        if self._provider is None:
            self._provider = create_provider(
                self.config["ai_provider"],
                api_key=self.config["api_key"],
                model=self.config.get("ai_model", ""),
                timeout=self.config.get("ai_timeout", 120),
                max_retries=self.config.get("ai_max_retries", 2),
            )
            ok, err = self._provider.validate()
            if not ok:
                log.warning(f"provider validation: {err}")
        return self._provider

    def register_channel(self, name: str, inbox_path: str, handler=None):
        """Register a channel's inbox file and optional reply handler."""
        self.channel_inboxes[name] = Path(inbox_path)
        if handler:
            self.channel_handlers[name] = handler
        log.info(f"registered channel: {name} -> {inbox_path}")

    def _write_heartbeat(self):
        """Write heartbeat file for watchdog monitoring."""
        now = time.time()
        if now - self.last_heartbeat < self.config["heartbeat_interval"]:
            return
        self.last_heartbeat = now
        hb = {
            "pid": os.getpid(),
            "timestamp": datetime.now().isoformat(),
            "loop_count": self.loop_count,
            "channels": list(self.channel_inboxes.keys()),
            "provider": self.config["ai_provider"],
        }
        try:
            with open(self.heartbeat_file, "w", encoding="utf-8") as f:
                json.dump(hb, f, indent=2)
        except OSError as e:
            log.error(f"heartbeat write failed: {e}")

    def _write_pid(self):
        """Write PID file."""
        with open(self.pid_file, "w") as f:
            f.write(str(os.getpid()))

    def _process_pending_tasks(self):
        """Process scheduled tasks from pending.json.

        Scheduler writes tasks here; brain reads them, feeds to AI as
        system-initiated messages, and clears the queue.
        """
        pending_file = self.data_dir / "pending.json"
        if not pending_file.exists():
            return

        try:
            with open(pending_file, "r", encoding="utf-8") as f:
                tasks = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        if not tasks:
            return

        log.info(f"Processing {len(tasks)} scheduled task(s)")

        for task in tasks:
            task_id = task.get("task_id", "unknown")
            command = task.get("command", task.get("description", ""))
            if not command:
                continue

            log.info(f"[scheduler] executing: {task_id}")

            # Process as internal message (not from any channel)
            msg = {
                "text": command,
                "source": "scheduler",
                "user_id": "system",
                "username": "scheduler",
            }
            response = self._process_message("scheduler", msg)

            if response:
                log.info(f"[scheduler] {task_id} done: {response[:80]}")
                # Write ack
                ack_dir = self.data_dir / "acks"
                ack_dir.mkdir(exist_ok=True)
                ack_file = ack_dir / f"{task_id}.ack"
                ack_file.write_text(datetime.now().isoformat())

        # Clear the queue
        try:
            with open(pending_file, "w", encoding="utf-8") as f:
                json.dump([], f)
        except OSError:
            pass

    def _check_inboxes(self) -> list:
        """Check all channel inboxes for new messages. Returns list of (channel, unread, all_msgs)."""
        results = []
        for name, inbox_path in self.channel_inboxes.items():
            if not inbox_path.exists():
                continue
            try:
                raw = inbox_path.read_bytes()
                # Handle BOM and encoding issues gracefully
                for enc in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
                    try:
                        text = raw.decode(enc)
                        break
                    except (UnicodeDecodeError, ValueError):
                        continue
                else:
                    text = "[]"
                messages = json.loads(text)
                if not isinstance(messages, list) or not messages:
                    continue
                unread = [m for m in messages if not m.get("read", False)]
                if unread:
                    results.append((name, unread, messages))
            except (json.JSONDecodeError, OSError):
                # Corrupted inbox — reset it
                try:
                    inbox_path.write_text("[]", encoding="utf-8")
                except OSError:
                    pass
        return results

    def _mark_read(self, inbox_path: Path, messages: list):
        """Clear inbox after processing to prevent re-reading stale messages."""
        try:
            with open(inbox_path, "w", encoding="utf-8") as f:
                json.dump([], f)
        except OSError as e:
            log.error(f"inbox clear failed: {e}")

    def _build_messages(self, channel: str, text: str, metadata: dict = None) -> list[dict]:
        """Build message list with system prompt, tool instructions, and conversation history."""
        msgs = []

        # System prompt (with tool instructions appended if enabled)
        sys_prompt = self.config.get("system_prompt", "")
        if not sys_prompt:
            sys_prompt = build_default_prompt(self.config)

        # L1: Always-loaded core rules
        from smart.memory import PFMemory
        mem = PFMemory(str(self.data_dir))
        mem.ensure_defaults()

        l1_rules = mem.load_l1()
        if l1_rules:
            sys_prompt = (sys_prompt or "") + f"\n\n## Core Rules (L1)\n{l1_rules}"

        # Tool instructions: only inject text-based tool prompt for non-native providers
        # Native providers (Claude/GPT/Gemini) get tool schemas via API, no text needed
        use_native = (self.config.get("enable_tools") and
                     hasattr(self.provider, 'SUPPORTS_TOOLS') and
                     self.provider.SUPPORTS_TOOLS)
        if self.config.get("enable_tools") and not use_native:
            # Fallback mode: inject text-based tool instructions
            tool_prompt = get_tool_prompt()
            if sys_prompt:
                sys_prompt = f"{sys_prompt}\n\n{tool_prompt}"
            else:
                sys_prompt = tool_prompt

        # L2 + L3 + Vector: Memory context injection (with semantic search if available)
        memory_context = mem.get_context_block(query=text, config=self.config)
        if memory_context:
            sys_prompt = (sys_prompt or "") + f"\n\n## Your Memories (L2+L3)\n{memory_context}"

        if sys_prompt:
            msgs.append({"role": "system", "content": sys_prompt})

        # Conversation history
        msgs.extend(self._conversation)

        # Channel/user context as system note (NOT in user message — prevents leaking)
        user_id = (metadata or {}).get("user_id", "")
        username = (metadata or {}).get("username", "")
        user_info = username or user_id or "unknown"
        msgs.append({"role": "system", "content": f"[Current message from {channel} channel, user: {user_info}. Do NOT mention this metadata in your reply.]"})

        # User message (clean, no tags)
        msgs.append({"role": "user", "content": text})

        return msgs

    def _check_whitelist(self, message: dict) -> bool:
        """Check if message sender is in global whitelist. Empty whitelist = allow all."""
        whitelist_raw = self.config.get("allowed_user_ids", "")
        if not whitelist_raw or not whitelist_raw.strip():
            return True  # No whitelist configured — allow everyone
        allowed = [uid.strip() for uid in whitelist_raw.split(",") if uid.strip()]
        if not allowed:
            return True
        user_id = str(message.get("user_id", ""))
        if user_id and user_id in allowed:
            return True
        log.warning(f"blocked message from user_id={user_id} (not in allowed_user_ids)")
        return False

    def _process_message(self, channel: str, message: dict) -> str | None:
        """Send message to AI provider, handle tool calls, and get final response.

        Tool use loop (when enable_tools is True):
          1. AI responds with optional [TOOL_CALL]...[/TOOL_CALL] blocks
          2. Brain parses tool calls, executes them, appends results
          3. AI gets another turn to process results (up to max_tool_rounds)
          4. Final response is the text with tool call blocks stripped

        Returns None if a hook blocked the message or user is not whitelisted.
        """
        # Global whitelist check
        if not self._check_whitelist(message):
            return None

        text = message.get("text", message.get("message", ""))
        user = message.get("user", message.get("from", ""))
        tools_enabled = self.config.get("enable_tools", False)
        max_rounds = self.config.get("max_tool_rounds", 5)

        # Hook: on_message_in (can block processing)
        hook_result = self.hooks.emit("on_message_in", {
            "channel": channel,
            "text": text,
            "user": user,
        })
        if hook_result.block:
            log.info(f"[{channel}] message blocked by hook")
            return None

        msgs = self._build_messages(channel, text, metadata=message)

        # Inject hook system message if provided
        if hook_result.system_message:
            msgs.insert(-1, {"role": "system", "content": hook_result.system_message})

        # ── Determine tool calling mode ─────────────────────────────
        use_native_tools = (tools_enabled and
                           hasattr(self.provider, 'SUPPORTS_TOOLS') and
                           self.provider.SUPPORTS_TOOLS)

        if use_native_tools:
            # ── Native function calling (Claude/GPT/Gemini) ──────
            provider_type = self.config.get("ai_provider", "openai")
            tool_schemas = get_tools_schema(provider_type)
            response = ""
            round_count = 0

            try:
                result = self.provider.chat_with_tools(msgs, tools=tool_schemas)
            except Exception as e:
                log.error(f"AI provider error: {e}")
                self.hooks.emit("on_error", {"error": str(e), "channel": channel})
                result = {"text": f"[error] AI provider failed: {e}", "tool_calls": []}

            response = result.get("text", "")
            pending_calls = result.get("tool_calls", [])

            while pending_calls and round_count < max_rounds:
                round_count += 1
                log.info(f"Native tool round {round_count}/{max_rounds}: "
                         f"{len(pending_calls)} call(s)")

                for tc in pending_calls:
                    tool_name = tc["name"]
                    tool_args = tc.get("args", {})
                    log.info(f"  -> {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:100]})")
                    tool_result = execute_tool(tool_name, tool_args, security=self._security)
                    log.info(f"  <- {tool_result[:100]}")

                    # Append tool call + result to conversation for next round
                    msgs.append({"role": "assistant", "content": response or f"Calling {tool_name}..."})
                    msgs.append({"role": "user", "content": f"[Tool result from {tool_name}]: {tool_result}"})

                # Call AI again with results
                try:
                    result = self.provider.chat_with_tools(msgs, tools=tool_schemas)
                except Exception as e:
                    log.error(f"AI provider error (tool round {round_count}): {e}")
                    response = f"[error] AI failed during tool use: {e}"
                    break

                response = result.get("text", "")
                pending_calls = result.get("tool_calls", [])

            if round_count >= max_rounds and pending_calls:
                log.warning(f"Native tool use hit max rounds ({max_rounds})")

            final_response = response

        else:
            # ── Fallback: prompt injection (Ollama/Echo/etc.) ────
            try:
                response = self.provider.chat(msgs)
            except Exception as e:
                log.error(f"AI provider error: {e}")
                self.hooks.emit("on_error", {"error": str(e), "channel": channel})
                response = f"[error] AI provider failed: {e}"

            if tools_enabled:
                response = normalize_tool_calls(response)

            if tools_enabled and has_tool_calls(response):
                round_count = 0
                while round_count < max_rounds:
                    tool_calls_parsed = parse_tool_calls(response)
                    if not tool_calls_parsed:
                        break

                    round_count += 1
                    log.info(f"Fallback tool round {round_count}/{max_rounds}: "
                             f"{len(tool_calls_parsed)} call(s)")

                    tool_results = []
                    for tc in tool_calls_parsed:
                        tool_name = tc["name"]
                        tool_args = tc["args"]
                        log.info(f"  -> {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:100]})")
                        result = execute_tool(tool_name, tool_args, security=self._security)
                        tool_results.append({"tool": tool_name, "args": tool_args, "result": result})
                        log.info(f"  <- {result[:100]}")

                    results_text = "\n".join(
                        f"[TOOL_RESULT tool={tr['tool']}]\n{tr['result']}\n[/TOOL_RESULT]"
                        for tr in tool_results
                    )
                    msgs.append({"role": "assistant", "content": response})
                    msgs.append({"role": "user", "content": results_text})

                    try:
                        response = self.provider.chat(msgs)
                        response = normalize_tool_calls(response)
                    except Exception as e:
                        log.error(f"AI provider error (fallback round {round_count}): {e}")
                        response = f"[error] AI failed during tool use: {e}"
                        break

                    if not has_tool_calls(response):
                        break

            final_response = strip_tool_calls(response) if tools_enabled else response

        # Update conversation history (store clean response, not raw tool calls)
        self._conversation.append({"role": "user", "content": text})
        self._conversation.append({"role": "assistant", "content": final_response})

        # Smart compaction instead of blind truncation
        if self.compactor.should_compact(self._conversation):
            log.info("Conversation threshold reached, triggering auto-compaction")
            self._auto_compact()
        elif len(self._conversation) > self._max_history * 2:
            # Safety fallback: hard trim if compaction somehow doesn't run
            self._conversation = self._conversation[-(self._max_history * 2):]

        # Persist to disk and update context tracking
        self._save_conversation()
        self._update_context_level()

        # Hook: on_message_out
        self.hooks.emit("on_message_out", {
            "channel": channel,
            "reply": final_response,
        })

        return final_response

    def _log_message(self, channel: str, direction: str, text: str):
        """Log message to unified message log."""
        try:
            log_data = []
            if self.message_log.exists():
                with open(self.message_log, "r", encoding="utf-8") as f:
                    log_data = json.load(f)
            log_data.append({
                "channel": channel,
                "direction": direction,
                "text": text[:500],
                "timestamp": datetime.now().isoformat(),
            })
            log_data = log_data[-500:]
            with open(self.message_log, "w", encoding="utf-8") as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _check_wake(self) -> bool:
        """Check if wake trigger file exists (written by channels)."""
        if self.wake_trigger.exists():
            try:
                self.wake_trigger.unlink()
            except Exception:
                pass
            return True
        return False

    def _check_duplicate(self):
        """Prevent duplicate brain processes."""
        if self.pid_file.exists():
            try:
                old_pid = json.loads(self.pid_file.read_text(encoding="utf-8")).get("pid", 0)
                if old_pid and old_pid != os.getpid():
                    import psutil
                    try:
                        p = psutil.Process(old_pid)
                        if p.is_running() and "main.py" in " ".join(p.cmdline()):
                            log.warning(f"Brain already running (PID {old_pid}), aborting duplicate")
                            return False
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception:
                pass
        return True

    def run(self):
        """Main brain loop."""
        if not self._check_duplicate():
            return

        self.running = True
        self._write_pid()
        self._setup_signal_handlers()

        # Write heartbeat immediately so watchdog doesn't restart us
        self.last_heartbeat = 0
        self._write_heartbeat()

        log.info(f"Permafrost Brain started (PID {os.getpid()})")
        log.info(f"Provider: {self.config['ai_provider']} / Model: {self.provider.model}")
        log.info(f"Data dir: {self.data_dir}")
        log.info(f"Channels: {list(self.channel_inboxes.keys())}")

        self.hooks.emit("on_start", {
            "pid": os.getpid(),
            "provider": self.config["ai_provider"],
            "channels": list(self.channel_inboxes.keys()),
        })

        # Load plugins
        try:
            plugins = PFPluginManager(
                data_dir=str(self.data_dir),
                config=self.config,
            )
            plugins.load_all()
            loaded = [p["name"] for p in plugins.list_plugins() if p["loaded"]]
            if loaded:
                log.info(f"Plugins loaded: {loaded}")
        except Exception as e:
            log.warning(f"Plugin loading failed: {e}")

        # Connect MCP servers and register their tools
        try:
            self._mcp = PFMCPManager(config=self.config, data_dir=str(self.data_dir))
            self._mcp.start_all()
            mcp_tool_count = self._mcp.register_tools()
            if mcp_tool_count:
                log.info(f"MCP tools registered: {mcp_tool_count}")
        except Exception as e:
            log.warning(f"MCP init failed: {e}")
            self._mcp = None

        try:
            while self.running:
                self._write_heartbeat()
                self.loop_count += 1

                # Self-maintenance cycle (compaction, memory GC, health check)
                self._run_maintenance()

                # Check for config reload trigger
                if self.reload_trigger.exists():
                    try:
                        self.reload_trigger.unlink()
                        log.info("Reload trigger detected — reloading config...")
                        new_config = {}
                        config_file = self.data_dir / "config.json"
                        if config_file.exists():
                            with open(config_file, "r", encoding="utf-8") as f:
                                new_config = json.load(f)
                        self.config.update(new_config)
                        self._provider = None  # Force re-init provider
                        # Re-register and start channels (new + existing with updated config)
                        import threading
                        from channels.base import _CHANNELS
                        for ch_name, ch_cls in _CHANNELS.items():
                            key = f"{ch_name}_enabled"
                            if not new_config.get(key):
                                continue
                            # Rebuild channel with fresh config (fixes stale settings)
                            ch_instance = ch_cls(new_config, str(self.data_dir))
                            ok, err = ch_instance.validate()
                            if not ok:
                                log.warning(f"{ch_name} skipped: {err}")
                                continue
                            inbox_path = str(self.data_dir / f"{ch_name}-inbox.json")
                            is_new = ch_name not in self.channel_inboxes
                            self.register_channel(ch_name, inbox_path, ch_instance.reply_handler)
                            # Start polling thread for new non-web channels
                            if is_new and ch_name != "web":
                                def _run_ch(ch=ch_instance):
                                    try:
                                        ch.run()
                                    except Exception as e:
                                        log.error(f"channel {ch.name} crashed: {e}")
                                t = threading.Thread(target=_run_ch, name=f"channel-{ch_name}", daemon=True)
                                t.start()
                                log.info(f"Channel started: {ch_name} (polling thread)")
                            elif is_new:
                                log.info(f"Channel added: {ch_name}")
                            else:
                                log.info(f"Channel updated: {ch_name} (config refreshed)")
                        self.hooks.reload(self.config)
                        self.hooks.emit("on_reload", {
                            "channels": list(self.channel_inboxes.keys()),
                        })
                        log.info(f"Config reloaded. Channels: {list(self.channel_inboxes.keys())}")
                    except Exception as e:
                        log.error(f"Config reload failed: {e}")

                # Check scheduled task queue (pending.json)
                self._process_pending_tasks()

                inbox_results = self._check_inboxes()

                if inbox_results:
                    for channel, unread, all_msgs in inbox_results:
                        for msg in unread:
                            text = msg.get("text", msg.get("message", ""))
                            log.info(f"[{channel}] received: {text[:80]}")
                            self._log_message(channel, "in", text)

                            response = self._process_message(channel, msg)

                            if response is None:
                                # Blocked by hook — skip this message
                                continue

                            log.info(f"[{channel}] reply: {response[:80]}")
                            self._log_message(channel, "out", response)

                            # Route reply to source channel
                            if channel in self.channel_handlers:
                                try:
                                    self.channel_handlers[channel](response, msg)
                                except Exception as e:
                                    log.error(f"handler error [{channel}]: {e}")

                        self._mark_read(self.channel_inboxes[channel], all_msgs)

                    time.sleep(self.config["poll_interval"])
                else:
                    if self._check_wake():
                        time.sleep(self.config["poll_interval"])
                    else:
                        time.sleep(self.config["idle_interval"])

        except KeyboardInterrupt:
            log.info("shutting down...")
        finally:
            self.hooks.emit("on_stop", {"loop_count": self.loop_count})
            self.running = False
            self._save_conversation()
            if self.pid_file.exists():
                self.pid_file.unlink()
            log.info("stopped.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    brain = PFBrain()
    brain.run()
