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

    @staticmethod
    def create_agent_workspace(agent_name: str, base_dir: str, claude_md_content: str = ""):
        """Create a new agent's workspace with required files."""
        workspace = Path(base_dir) / agent_name
        workspace.mkdir(parents=True, exist_ok=True)

        # CLAUDE.md
        claude_md = workspace / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text(
                claude_md_content or f"# {agent_name} Agent\n\nRole and instructions here.\n",
                encoding="utf-8"
            )

        # Memory directory
        (workspace / "memory").mkdir(exist_ok=True)

        # Comms directory
        (workspace / "comms").mkdir(exist_ok=True)

        return str(workspace)
