"""
Permafrost Memory — Structured memory with layers and auto-GC.

Layer 1: Core rules (CLAUDE.md equivalent, always loaded)
Layer 2: Topic memories (indexed, loaded on demand)
Layer 3: Dynamic index (auto-GC, vector search ready)

Key feature: Memory files have frontmatter (name, description, type)
for smart retrieval without reading full content.
"""

import json
import os
from datetime import datetime
from pathlib import Path


class PFMemory:
    """Structured memory system with layers and garbage collection."""

    TYPES = ["user", "feedback", "project", "reference"]

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or os.path.expanduser("~/.permafrost"))
        self.memory_dir = self.data_dir / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.memory_dir / "INDEX.md"

    def save(self, name: str, description: str, mem_type: str, content: str, filename: str = None):
        """Save a memory file with frontmatter."""
        if mem_type not in self.TYPES:
            raise ValueError(f"type must be one of {self.TYPES}")

        if not filename:
            filename = name.lower().replace(" ", "_").replace("/", "_")[:50] + ".md"

        filepath = self.memory_dir / filename

        frontmatter = f"""---
name: {name}
description: {description}
type: {mem_type}
updated: {datetime.now().isoformat()[:19]}
---

{content}
"""
        filepath.write_text(frontmatter, encoding="utf-8")
        self._update_index()
        return str(filepath)

    def load(self, filename: str) -> dict:
        """Load a memory file, parsing frontmatter."""
        filepath = self.memory_dir / filename
        if not filepath.exists():
            return {}

        text = filepath.read_text(encoding="utf-8")
        meta = {}
        body = text

        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().split("\n"):
                    if ":" in line:
                        key, val = line.split(":", 1)
                        meta[key.strip()] = val.strip()
                body = parts[2].strip()

        meta["body"] = body
        meta["filename"] = filename
        return meta

    def search(self, query: str) -> list:
        """Search memories by keyword in name/description."""
        results = []
        query_lower = query.lower()
        for f in self.memory_dir.glob("*.md"):
            if f.name == "INDEX.md":
                continue
            meta = self.load(f.name)
            searchable = f"{meta.get('name', '')} {meta.get('description', '')} {meta.get('body', '')}".lower()
            if query_lower in searchable:
                results.append(meta)
        return results

    def list_by_type(self, mem_type: str) -> list:
        """List all memories of a given type."""
        results = []
        for f in self.memory_dir.glob("*.md"):
            if f.name == "INDEX.md":
                continue
            meta = self.load(f.name)
            if meta.get("type") == mem_type:
                results.append(meta)
        return results

    def delete(self, filename: str) -> bool:
        """Delete a memory file."""
        filepath = self.memory_dir / filename
        if filepath.exists():
            filepath.unlink()
            self._update_index()
            return True
        return False

    def gc(self, max_age_days: int = 30):
        """Garbage collect old, low-priority memories."""
        now = datetime.now()
        removed = []
        for f in self.memory_dir.glob("*.md"):
            if f.name == "INDEX.md":
                continue
            meta = self.load(f.name)
            updated = meta.get("updated", "")
            if not updated:
                continue
            try:
                age = (now - datetime.fromisoformat(updated)).days
                if age > max_age_days and meta.get("type") not in ["feedback", "user"]:
                    f.unlink()
                    removed.append(f.name)
            except Exception:
                continue
        if removed:
            self._update_index()
        return removed

    def _update_index(self):
        """Rebuild INDEX.md from all memory files."""
        lines = ["# Permafrost Memory Index\n"]
        for f in sorted(self.memory_dir.glob("*.md")):
            if f.name == "INDEX.md":
                continue
            meta = self.load(f.name)
            name = meta.get("name", f.stem)
            desc = meta.get("description", "")
            mtype = meta.get("type", "?")
            lines.append(f"- **{name}** ({mtype}) — {desc}")

        self.index_file.write_text("\n".join(lines), encoding="utf-8")

    def get_context_block(self) -> str:
        """Get a compact context block of all memories for AI prompt injection."""
        block = []
        for f in sorted(self.memory_dir.glob("*.md")):
            if f.name == "INDEX.md":
                continue
            meta = self.load(f.name)
            if meta.get("type") in ["feedback", "user"]:
                # Always include feedback and user memories
                block.append(f"[{meta.get('type')}] {meta.get('name', '')}: {meta.get('body', '')[:200]}")
        return "\n".join(block)
