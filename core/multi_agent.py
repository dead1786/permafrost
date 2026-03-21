"""
Permafrost Multi-Agent — Cross-machine AI coordination via inbox/outbox.

Enables multiple AI agents to communicate through JSON file queues,
synchronized via Syncthing or shared filesystem.

Features:
  - Inbox/outbox per agent
  - Message routing with read tracking
  - Broadcast to all agents
  - Command chain (leader → subordinates)
"""

import json
import os
from datetime import datetime
from pathlib import Path


class PFMultiAgent:
    """Multi-agent coordination hub."""

    def __init__(self, agent_name: str, data_dir: str = None):
        self.agent_name = agent_name
        self.data_dir = Path(data_dir or os.path.expanduser("~/.permafrost"))
        self.comms_dir = self.data_dir / "comms"
        self.comms_dir.mkdir(parents=True, exist_ok=True)

        self.inbox_file = self.comms_dir / f"{agent_name}-inbox.json"
        self.outbox_file = self.comms_dir / f"{agent_name}-outbox.json"

    def send(self, to: str, message: str):
        """Send a message to another agent's inbox."""
        target_inbox = self.comms_dir / f"{to}-inbox.json"

        inbox = []
        if target_inbox.exists():
            try:
                inbox = json.loads(target_inbox.read_text(encoding="utf-8"))
            except Exception:
                inbox = []

        inbox.append({
            "from": self.agent_name,
            "to": to,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "read": False,
        })

        target_inbox.write_text(
            json.dumps(inbox, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Also log to own outbox
        outbox = []
        if self.outbox_file.exists():
            try:
                outbox = json.loads(self.outbox_file.read_text(encoding="utf-8"))
            except Exception:
                outbox = []

        outbox.append({
            "from": self.agent_name,
            "to": to,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep last 200
        outbox = outbox[-200:]
        self.outbox_file.write_text(
            json.dumps(outbox, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def broadcast(self, agents: list, message: str):
        """Send same message to multiple agents."""
        for agent in agents:
            if agent != self.agent_name:
                self.send(agent, message)

    def check_inbox(self) -> list:
        """Check for unread messages. Returns list of unread messages."""
        if not self.inbox_file.exists():
            return []
        try:
            inbox = json.loads(self.inbox_file.read_text(encoding="utf-8"))
            return [m for m in inbox if not m.get("read", False)]
        except Exception:
            return []

    def mark_read(self, mark_all: bool = True):
        """Mark inbox messages as read."""
        if not self.inbox_file.exists():
            return
        try:
            inbox = json.loads(self.inbox_file.read_text(encoding="utf-8"))
            for m in inbox:
                if not m.get("read", False):
                    m["read"] = True
            self.inbox_file.write_text(
                json.dumps(inbox, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def get_conversation(self, with_agent: str, limit: int = 20) -> list:
        """Get recent conversation with another agent."""
        messages = []

        # From outbox (sent)
        if self.outbox_file.exists():
            try:
                outbox = json.loads(self.outbox_file.read_text(encoding="utf-8"))
                for m in outbox:
                    if m.get("to") == with_agent:
                        m["direction"] = "sent"
                        messages.append(m)
            except Exception:
                pass

        # From inbox (received)
        if self.inbox_file.exists():
            try:
                inbox = json.loads(self.inbox_file.read_text(encoding="utf-8"))
                for m in inbox:
                    if m.get("from") == with_agent:
                        m["direction"] = "received"
                        messages.append(m)
            except Exception:
                pass

        # Sort by timestamp
        messages.sort(key=lambda m: m.get("timestamp", ""))
        return messages[-limit:]

    def spawn_agent(self, name: str, persona: str = "", task: str = "") -> str:
        """Spawn an independent agent with its own brain, memory, and data directory.

        The sub-agent runs as a separate PF brain process with isolated memory.
        Communication happens through inbox/outbox files.

        Args:
            name: Agent name (lowercase, no spaces)
            persona: System prompt / personality for the agent
            task: Initial task to send to the agent's inbox
        """
        import subprocess as sp
        import sys

        safe_name = name.lower().replace(" ", "_")
        agent_data = self.data_dir / "agents" / safe_name
        agent_data.mkdir(parents=True, exist_ok=True)

        # Create isolated memory structure
        for sub in ["memory/L1", "memory/L2", "memory/L3", "memory/L4", "memory/L5", "memory/L6"]:
            (agent_data / sub).mkdir(parents=True, exist_ok=True)

        # Write agent config (inherit main config, override data_dir and prompt)
        agent_config = {}
        main_config = self.data_dir / "config.json"
        if main_config.exists():
            try:
                agent_config = json.loads(main_config.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        agent_config["data_dir"] = str(agent_data)
        if persona:
            agent_config["system_prompt"] = persona

        config_file = agent_data / "config.json"
        config_file.write_text(json.dumps(agent_config, indent=2, ensure_ascii=False), encoding="utf-8")

        # Write agent status
        (agent_data / "agent-status.json").write_text(json.dumps({
            "name": safe_name,
            "persona": persona[:200],
            "created_at": datetime.now().isoformat(),
            "created_by": self.agent_name,
        }, indent=2, ensure_ascii=False), encoding="utf-8")

        # Initialize comms
        comms = agent_data / "comms"
        comms.mkdir(exist_ok=True)
        for f in [f"{safe_name}-inbox.json", f"{safe_name}-outbox.json"]:
            p = comms / f
            if not p.exists():
                p.write_text("[]", encoding="utf-8")

        # Send initial task
        if task:
            target_inbox = comms / f"{safe_name}-inbox.json"
            target_inbox.write_text(json.dumps([{
                "from": self.agent_name,
                "to": safe_name,
                "message": task,
                "timestamp": datetime.now().isoformat(),
                "read": False,
            }], indent=2, ensure_ascii=False), encoding="utf-8")

        # Start sub-agent brain
        launcher = Path(__file__).resolve().parent.parent / "launcher.py"
        try:
            env = os.environ.copy()
            env["PF_DATA_DIR"] = str(agent_data)
            process = sp.Popen(
                [sys.executable, str(launcher)],
                env=env,
                cwd=str(launcher.parent),
                creationflags=sp.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return f"Agent '{safe_name}' spawned (PID {process.pid}). Data: {agent_data}"
        except Exception as e:
            return f"Agent workspace created at {agent_data}, but process start failed: {e}"

    def list_agents(self) -> list[dict]:
        """List all known sub-agents."""
        agents_dir = self.data_dir / "agents"
        if not agents_dir.exists():
            return []
        result = []
        for d in agents_dir.iterdir():
            if d.is_dir():
                status_file = d / "agent-status.json"
                if status_file.exists():
                    try:
                        status = json.loads(status_file.read_text(encoding="utf-8"))
                        status["data_dir"] = str(d)
                        result.append(status)
                    except (json.JSONDecodeError, OSError):
                        result.append({"name": d.name, "data_dir": str(d)})
        return result

    @staticmethod
    def create_agent_workspace(agent_name: str, base_dir: str, claude_md_content: str = ""):
        """Create a new agent's workspace with required files."""
        workspace = Path(base_dir) / agent_name
        workspace.mkdir(parents=True, exist_ok=True)
        claude_md = workspace / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text(
                claude_md_content or f"# {agent_name} Agent\n\nRole and instructions here.\n",
                encoding="utf-8"
            )
        (workspace / "memory").mkdir(exist_ok=True)
        (workspace / "comms").mkdir(exist_ok=True)
        return str(workspace)
