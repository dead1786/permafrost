"""
Permafrost Background Agents — Sub-sessions for autonomous maintenance tasks.

Spawns lightweight AI sessions in background threads to handle:
  - Memory maintenance (GC, promotion, index rebuild)
  - Context analysis (extract important info before compaction)
  - Rules validation (check L1 consistency)
  - Custom user-defined background tasks

Each agent:
  1. Gets its own AI provider call (same credentials as main brain)
  2. Runs in a daemon thread (won't block main brain loop)
  3. Writes results to data_dir for brain to pick up
  4. Auto-terminates after task completion
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("permafrost.agents")


class AgentResult:
    """Result from a background agent task."""

    def __init__(self, agent_name: str, task: str):
        self.agent_name = agent_name
        self.task = task
        self.success = False
        self.output = ""
        self.error = ""
        self.started_at = datetime.now().isoformat()
        self.completed_at = ""
        self.changes: list[str] = []

    def to_dict(self) -> dict:
        return {
            "agent": self.agent_name,
            "task": self.task,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "changes": self.changes,
        }


class PFAgentManager:
    """Manages background agent threads."""

    def __init__(self, data_dir: str, config: dict = None):
        self.data_dir = Path(data_dir)
        self.config = config or {}
        self.results_file = self.data_dir / "agent-results.json"
        self.active_agents: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def is_running(self, agent_name: str) -> bool:
        """Check if an agent is currently running."""
        with self._lock:
            thread = self.active_agents.get(agent_name)
            return thread is not None and thread.is_alive()

    def run_agent(self, agent_name: str, task_func, provider=None, **kwargs):
        """Spawn a background agent thread.

        Args:
            agent_name: Unique name for this agent instance
            task_func: Callable(data_dir, provider, **kwargs) -> AgentResult
            provider: AI provider instance (optional, some tasks don't need AI)
        """
        if self.is_running(agent_name):
            log.info(f"Agent '{agent_name}' already running, skipping")
            return

        def _wrapper():
            result = AgentResult(agent_name, task_func.__name__)
            try:
                log.info(f"Agent '{agent_name}' started: {task_func.__name__}")
                result = task_func(str(self.data_dir), provider, **kwargs)
                result.completed_at = datetime.now().isoformat()
                result.success = True
                log.info(f"Agent '{agent_name}' completed: {len(result.changes)} changes")
            except Exception as e:
                result.error = str(e)
                result.completed_at = datetime.now().isoformat()
                log.error(f"Agent '{agent_name}' failed: {e}")
            finally:
                self._save_result(result)
                with self._lock:
                    self.active_agents.pop(agent_name, None)

        thread = threading.Thread(target=_wrapper, name=f"pf-agent-{agent_name}", daemon=True)
        with self._lock:
            self.active_agents[agent_name] = thread
        thread.start()

    def _save_result(self, result: AgentResult):
        """Save agent result to results file."""
        try:
            results = []
            if self.results_file.exists():
                results = json.loads(self.results_file.read_text(encoding="utf-8"))
            results.append(result.to_dict())
            # Keep last 100 results
            results = results[-100:]
            self.results_file.write_text(
                json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            log.error(f"agent result save failed: {e}")

    def get_recent_results(self, limit: int = 10) -> list[dict]:
        """Get recent agent results."""
        if not self.results_file.exists():
            return []
        try:
            results = json.loads(self.results_file.read_text(encoding="utf-8"))
            return results[-limit:]
        except (json.JSONDecodeError, OSError):
            return []

    def get_active(self) -> list[str]:
        """Get list of currently active agent names."""
        with self._lock:
            return [name for name, t in self.active_agents.items() if t.is_alive()]


# ── Built-in Agent Tasks ──────────────────────────────────────────


def agent_memory_maintenance(data_dir: str, provider=None, **kwargs) -> AgentResult:
    """Background agent: Memory GC + promotion + index rebuild.

    Runs without AI — pure data maintenance.
    """
    result = AgentResult("memory-maintenance", "agent_memory_maintenance")
    result.changes = []

    try:
        from smart.memory import PFMemory
        mem = PFMemory(data_dir)

        # Run GC
        gc_result = mem.gc()
        if gc_result["promoted"] > 0:
            result.changes.append(f"Promoted {gc_result['promoted']} L3 entries to L2")
        if gc_result["archived"] > 0:
            result.changes.append(f"Archived {gc_result['archived']} expired L3 entries to L4")
        result.changes.append(f"L3: {gc_result['kept']} entries kept")

        # Rebuild markdown index
        mem._rebuild_index()
        result.changes.append("Rebuilt memory INDEX.md")

        # Rebuild vector index (if embedding available)
        try:
            mem.index_all_memories()
            vs_count = mem.get_stats().get("vectors", 0)
            result.changes.append(f"Vector index rebuilt: {vs_count} entries")
        except Exception as ve:
            result.changes.append(f"Vector index skipped: {ve}")

        # Get stats
        stats = mem.get_stats()
        result.output = json.dumps(stats, ensure_ascii=False)
        result.success = True

    except Exception as e:
        result.error = str(e)
        result.success = False

    result.completed_at = datetime.now().isoformat()
    return result


def agent_context_extractor(data_dir: str, provider=None, **kwargs) -> AgentResult:
    """Background agent: Extract important info from conversation before compaction.

    Uses AI to identify key decisions, preferences, and state changes
    that should be saved to L2/L3 before context is compressed.
    """
    result = AgentResult("context-extractor", "agent_context_extractor")
    result.changes = []

    if not provider:
        result.error = "No AI provider available"
        result.completed_at = datetime.now().isoformat()
        return result

    try:
        from smart.memory import PFMemory
        mem = PFMemory(data_dir)

        # Read recent conversation
        conv_file = Path(data_dir) / "brain-conversation.json"
        if not conv_file.exists():
            result.output = "No conversation to analyze"
            result.success = True
            result.completed_at = datetime.now().isoformat()
            return result

        conversation = json.loads(conv_file.read_text(encoding="utf-8"))
        if len(conversation) < 4:
            result.output = "Conversation too short to extract from"
            result.success = True
            result.completed_at = datetime.now().isoformat()
            return result

        # Take last 20 messages for extraction
        recent = conversation[-20:]
        conv_text = "\n".join(
            f"[{m.get('role', '?')}]: {m.get('content', '')[:500]}"
            for m in recent
        )

        extract_prompt = (
            "Analyze this conversation and extract important items to remember. "
            "For each item, output a JSON array with objects containing:\n"
            '- "key": short identifier\n'
            '- "value": what to remember\n'
            '- "type": one of "context", "preference", "progress", "insight"\n'
            '- "importance": 1-5 (5=critical)\n\n'
            "Only extract genuinely important items (preferences, decisions, errors, tasks). "
            "Skip casual conversation. Output ONLY the JSON array, nothing else.\n\n"
            f"Conversation:\n{conv_text}"
        )

        response = provider.chat([
            {"role": "user", "content": extract_prompt}
        ])

        # Parse AI response
        try:
            # Try to find JSON array in response
            start = response.find("[")
            end = response.rfind("]") + 1
            if start >= 0 and end > start:
                items = json.loads(response[start:end])
                for item in items:
                    if isinstance(item, dict) and "key" in item and "value" in item:
                        mem.add_l3(
                            key=item["key"],
                            value=item["value"],
                            mem_type=item.get("type", "context"),
                            importance=item.get("importance", 3),
                        )
                        result.changes.append(f"Saved to L3: {item['key']}")
        except (json.JSONDecodeError, ValueError) as e:
            result.changes.append(f"Parse warning: {e}")

        result.output = f"Extracted {len(result.changes)} items from conversation"
        result.success = True

    except Exception as e:
        result.error = str(e)
        result.success = False

    result.completed_at = datetime.now().isoformat()
    return result


def agent_health_check(data_dir: str, provider=None, **kwargs) -> AgentResult:
    """Background agent: System health check.

    Checks memory integrity, conversation file health, and disk usage.
    """
    result = AgentResult("health-check", "agent_health_check")
    result.changes = []

    try:
        data_path = Path(data_dir)
        from smart.memory import PFMemory
        mem = PFMemory(data_dir)

        # Check memory stats
        stats = mem.get_stats()
        result.changes.append(f"Memory stats: {json.dumps(stats)}")

        # Check conversation file size
        conv_file = data_path / "brain-conversation.json"
        if conv_file.exists():
            size_kb = conv_file.stat().st_size / 1024
            conv = json.loads(conv_file.read_text(encoding="utf-8"))
            result.changes.append(f"Conversation: {len(conv)} messages, {size_kb:.1f} KB")
            if size_kb > 500:
                result.changes.append("WARNING: Conversation file > 500 KB, compaction recommended")

        # Check L3 for expired entries
        l3_entries = mem._load_l3()
        expired = 0
        from datetime import timedelta
        now = datetime.now()
        for e in l3_entries:
            created = datetime.fromisoformat(e["created"])
            ttl = e.get("ttl_days", 14)
            if (now - created).days > ttl:
                expired += 1
        if expired > 0:
            result.changes.append(f"L3: {expired} expired entries need GC")

        # Check heartbeat freshness
        hb_file = data_path / "brain-heartbeat.json"
        if hb_file.exists():
            hb = json.loads(hb_file.read_text(encoding="utf-8"))
            hb_time = datetime.fromisoformat(hb["timestamp"])
            age = (now - hb_time).total_seconds()
            if age > 120:
                result.changes.append(f"WARNING: Brain heartbeat stale ({age:.0f}s old)")
            else:
                result.changes.append(f"Brain heartbeat OK ({age:.0f}s ago)")

        result.output = "\n".join(result.changes)
        result.success = True

    except Exception as e:
        result.error = str(e)
        result.success = False

    result.completed_at = datetime.now().isoformat()
    return result
