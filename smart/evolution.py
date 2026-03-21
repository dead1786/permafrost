"""
Permafrost Evolution Engine — Self-improvement closed loop.

Cycle: error/feedback → pitfall log → evolution plan → execute → improve

This module manages:
- Evolution queue (queued/in_progress/done items)
- Pitfall-to-evolution bridge (auto-convert pitfalls to plans)
- Evolution execution (pick highest priority, execute, verify)
"""
import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("permafrost.evolution")

class EvolutionEngine:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.queue_file = self.data_dir / "evolution-queue.json"
        self.pitfalls_file = self.data_dir / "pitfalls.json"

    def load_queue(self):
        if self.queue_file.exists():
            return json.loads(self.queue_file.read_text(encoding="utf-8"))
        return {"items": [], "completed_count": 0}

    def save_queue(self, data):
        data["last_updated"] = datetime.now().isoformat()
        self.queue_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def add_item(self, title, why, expected, priority=1):
        q = self.load_queue()
        max_id = max([int(i["id"].split("-")[1]) for i in q["items"] if i["id"].startswith("evo-")], default=0)
        new_id = f"evo-{max_id+1:03d}"
        q["items"].append({
            "id": new_id, "title": title, "why": why,
            "expected": expected, "status": "queued",
            "priority": priority, "created": datetime.now().strftime("%Y-%m-%d"),
        })
        self.save_queue(q)
        return new_id

    def get_next(self):
        q = self.load_queue()
        in_progress = [i for i in q["items"] if i["status"] == "in_progress"]
        if in_progress:
            return in_progress[0]
        queued = sorted([i for i in q["items"] if i["status"] == "queued"], key=lambda x: x.get("priority", 5))
        return queued[0] if queued else None

    def mark_done(self, item_id, result=""):
        q = self.load_queue()
        for i in q["items"]:
            if i["id"] == item_id:
                i["status"] = "done"
                i["completed"] = datetime.now().strftime("%Y-%m-%d")
                i["result"] = result
        q["completed_count"] = len([i for i in q["items"] if i["status"] == "done"])
        self.save_queue(q)

    def get_stats(self):
        q = self.load_queue()
        items = q.get("items", [])
        return {
            "queued": len([i for i in items if i["status"] == "queued"]),
            "in_progress": len([i for i in items if i["status"] == "in_progress"]),
            "done": len([i for i in items if i["status"] == "done"]),
            "total": len(items),
        }

    def log_pitfall(self, description, category="general"):
        pitfalls = []
        if self.pitfalls_file.exists():
            try:
                pitfalls = json.loads(self.pitfalls_file.read_text(encoding="utf-8"))
            except:
                pitfalls = []
        pitfalls.append({
            "description": description,
            "category": category,
            "timestamp": datetime.now().isoformat(),
            "converted_to_plan": False,
        })
        self.pitfalls_file.write_text(json.dumps(pitfalls, indent=2, ensure_ascii=False), encoding="utf-8")

    def convert_pitfalls_to_plans(self):
        if not self.pitfalls_file.exists():
            return 0
        pitfalls = json.loads(self.pitfalls_file.read_text(encoding="utf-8"))
        converted = 0
        for p in pitfalls:
            if not p.get("converted_to_plan"):
                self.add_item(
                    title=f"Fix: {p['description'][:50]}",
                    why=p["description"],
                    expected="Prevent this issue from recurring",
                    priority=1,
                )
                p["converted_to_plan"] = True
                converted += 1
        self.pitfalls_file.write_text(json.dumps(pitfalls, indent=2, ensure_ascii=False), encoding="utf-8")
        return converted
