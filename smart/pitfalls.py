"""
Permafrost Pitfalls Tracker — AI learns from its mistakes.

Every time the AI makes an error or gets corrected by the user,
the pitfall is recorded. Before taking similar actions in the future,
the AI checks pitfalls to avoid repeating mistakes.

This is one of the key differentiators vs OpenClaw — built-in self-improvement.
"""

import json
import os
from datetime import datetime
from pathlib import Path


class PFPitfalls:
    """Track and learn from mistakes."""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or os.path.expanduser("~/.permafrost"))
        self.pitfalls_file = self.data_dir / "pitfalls.json"
        self.checklist_file = self.data_dir / "pitfalls-checklist.json"

    def _load(self) -> list:
        if self.pitfalls_file.exists():
            return json.loads(self.pitfalls_file.read_text(encoding="utf-8"))
        return []

    def _save(self, pitfalls: list):
        self.pitfalls_file.write_text(
            json.dumps(pitfalls, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def record(self, category: str, description: str, lesson: str, severity: str = "medium"):
        """Record a new pitfall.

        Args:
            category: e.g., "scheduling", "decision", "verification"
            description: What happened
            lesson: What to do differently next time
            severity: "low", "medium", "high", "critical"
        """
        pitfalls = self._load()
        pitfalls.append({
            "id": len(pitfalls) + 1,
            "category": category,
            "description": description,
            "lesson": lesson,
            "severity": severity,
            "timestamp": datetime.now().isoformat(),
            "recurrence_count": 0,
        })
        self._save(pitfalls)

    def check_before_action(self, action_category: str) -> list:
        """Check for relevant pitfalls before taking an action.

        Returns list of relevant lessons to consider.
        """
        pitfalls = self._load()
        relevant = [p for p in pitfalls if p["category"] == action_category]
        # Sort by severity and recurrence
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        relevant.sort(key=lambda p: (severity_order.get(p["severity"], 2), -p.get("recurrence_count", 0)))
        return [p["lesson"] for p in relevant]

    def mark_recurrence(self, pitfall_id: int):
        """Mark that a pitfall has recurred (same mistake made again)."""
        pitfalls = self._load()
        for p in pitfalls:
            if p["id"] == pitfall_id:
                p["recurrence_count"] = p.get("recurrence_count", 0) + 1
                p["last_recurrence"] = datetime.now().isoformat()
                break
        self._save(pitfalls)

    def get_checklist(self) -> list:
        """Get pre-action checklist derived from pitfalls."""
        pitfalls = self._load()
        # Group by category, extract top lessons
        categories = {}
        for p in pitfalls:
            cat = p["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(p["lesson"])

        checklist = []
        for cat, lessons in categories.items():
            checklist.append({
                "category": cat,
                "checks": list(set(lessons))[:5],  # top 5 unique lessons per category
            })
        return checklist

    def get_summary(self) -> dict:
        """Get pitfalls summary for reflection."""
        pitfalls = self._load()
        return {
            "total": len(pitfalls),
            "by_severity": {
                s: len([p for p in pitfalls if p["severity"] == s])
                for s in ["critical", "high", "medium", "low"]
            },
            "recurring": [p for p in pitfalls if p.get("recurrence_count", 0) > 0],
            "recent": pitfalls[-5:] if pitfalls else [],
        }
