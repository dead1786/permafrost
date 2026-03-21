"""
Permafrost Reflection — Automated self-improvement through nightly review.

Every night, the AI reviews its day:
  - What went well?
  - What went wrong?
  - What patterns keep recurring?
  - What actions to take?

Reflections are stored as JSON for trend analysis across days.
"""

import json
import os
from datetime import datetime, date
from pathlib import Path


class PFReflection:
    """Nightly self-reflection and continuous improvement."""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or os.path.expanduser("~/.permafrost"))
        self.reflections_dir = self.data_dir / "reflections"
        self.reflections_dir.mkdir(parents=True, exist_ok=True)

    def create(self, went_well: list, went_wrong: list, patterns: list,
               actions: list, score: dict = None) -> str:
        """Create today's reflection."""
        today = date.today().isoformat()
        filepath = self.reflections_dir / f"{today}.json"

        reflection = {
            "date": today,
            "timestamp": datetime.now().isoformat(),
            "went_well": went_well,
            "went_wrong": went_wrong,
            "patterns_detected": patterns,
            "action_items": actions,
            "score": score or {},
        }

        filepath.write_text(
            json.dumps(reflection, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return str(filepath)

    def get_recent(self, days: int = 7) -> list:
        """Get recent reflections for trend analysis."""
        files = sorted(self.reflections_dir.glob("*.json"), reverse=True)
        results = []
        for f in files[:days]:
            try:
                results.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return results

    def analyze_trends(self, days: int = 7) -> dict:
        """Analyze patterns across recent reflections."""
        recent = self.get_recent(days)
        if not recent:
            return {"message": "No reflections yet"}

        all_patterns = []
        all_wrongs = []
        scores = []

        for r in recent:
            all_patterns.extend(r.get("patterns_detected", []))
            all_wrongs.extend(r.get("went_wrong", []))
            if r.get("score", {}).get("overall"):
                try:
                    scores.append(float(str(r["score"]["overall"]).split("/")[0]))
                except (ValueError, IndexError):
                    pass

        # Find recurring patterns
        pattern_counts = {}
        for p in all_patterns:
            p_lower = p.lower()[:50]
            pattern_counts[p_lower] = pattern_counts.get(p_lower, 0) + 1

        recurring = {k: v for k, v in pattern_counts.items() if v >= 2}

        return {
            "days_analyzed": len(recent),
            "recurring_patterns": recurring,
            "total_issues": len(all_wrongs),
            "avg_score": sum(scores) / len(scores) if scores else None,
            "score_trend": "improving" if len(scores) >= 2 and scores[0] > scores[-1]
                          else "declining" if len(scores) >= 2 and scores[0] < scores[-1]
                          else "stable",
        }

    def get_follow_up_items(self) -> list:
        """Get action items from recent reflections that haven't been addressed."""
        recent = self.get_recent(3)
        items = []
        for r in recent:
            for action in r.get("action_items", []):
                items.append({
                    "date": r["date"],
                    "action": action,
                })
        return items
