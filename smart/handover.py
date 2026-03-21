"""
Permafrost Handover — Self-continuity across restarts and context resets.

The AI writes a handover file before each session ends,
so the next session knows exactly where to pick up.

This solves the "cold start amnesia" problem — every restart
feels like a continuation, not a fresh start.
"""

import json
import os
from datetime import datetime
from pathlib import Path


class PFHandover:
    """Self-continuity system via handover files."""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or os.path.expanduser("~/.permafrost"))
        self.handover_file = self.data_dir / "handover.json"

    def read(self) -> dict:
        """Read current handover. Called on every wake-up."""
        if not self.handover_file.exists():
            return {"active_tasks": [], "standing_rules": []}
        try:
            return json.loads(self.handover_file.read_text(encoding="utf-8"))
        except Exception:
            return {"active_tasks": [], "standing_rules": []}

    def write(self, handover: dict):
        """Write updated handover. Called after completing work."""
        handover["_updated"] = datetime.now().isoformat()
        self.handover_file.write_text(
            json.dumps(handover, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add_task(self, task_id: str, title: str, priority: int,
                 what_to_do: str, progress: str, next_checkpoint: str):
        """Add or update a task in the handover."""
        handover = self.read()
        tasks = handover.get("active_tasks", [])

        # Update existing or add new
        found = False
        for t in tasks:
            if t["id"] == task_id:
                t.update({
                    "title": title, "priority": priority,
                    "what_to_do": what_to_do,
                    "last_progress": progress,
                    "next_checkpoint": next_checkpoint,
                })
                found = True
                break

        if not found:
            tasks.append({
                "id": task_id, "title": title, "priority": priority,
                "what_to_do": what_to_do,
                "last_progress": progress,
                "next_checkpoint": next_checkpoint,
            })

        # Sort by priority
        tasks.sort(key=lambda t: t.get("priority", 99))
        handover["active_tasks"] = tasks
        self.write(handover)

    def complete_task(self, task_id: str):
        """Remove a completed task from handover."""
        handover = self.read()
        handover["active_tasks"] = [
            t for t in handover.get("active_tasks", [])
            if t["id"] != task_id
        ]
        self.write(handover)

    def update_progress(self, task_id: str, progress: str, next_checkpoint: str):
        """Update a task's progress and next checkpoint."""
        handover = self.read()
        for t in handover.get("active_tasks", []):
            if t["id"] == task_id:
                t["last_progress"] = progress
                t["next_checkpoint"] = next_checkpoint
                break
        self.write(handover)

    def get_next_action(self) -> dict:
        """Get the highest priority task's next checkpoint."""
        handover = self.read()
        tasks = handover.get("active_tasks", [])
        if tasks:
            return tasks[0]  # already sorted by priority
        return {}
