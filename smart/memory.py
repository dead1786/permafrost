"""
Permafrost Memory — L1-L6 layered memory system.

L1: Core rules (permanent, auto-loaded)
L2: Verified knowledge (long-term, topic-indexed)
L3: Dynamic memory (short-term, TTL + auto-GC)
L4: Monthly archive (compressed L3)
L5: Quarterly summary
L6: Annual summary
"""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("permafrost.memory")

L3_TTL = {
    "context": 14,
    "preference": 30,
    "progress": 7,
    "insight": 21,
}

L2_TYPES = ["user", "feedback", "project", "reference"]


class PFMemory:
    """Layered memory system with L1-L6 hierarchy, auto-GC, and promotion."""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or os.path.expanduser("~/.permafrost"))
        self.memory_dir = self.data_dir / "memory"
        for layer in ["L1", "L2", "L3", "L4", "L5", "L6"]:
            (self.memory_dir / layer).mkdir(parents=True, exist_ok=True)
        self.l3_file = self.memory_dir / "L3" / "dynamic.json"
        self.index_file = self.memory_dir / "INDEX.md"

    # ── L1: Core Rules ──────────────────────────────────────────

    def ensure_l1_defaults(self):
        """Always overwrite L1 defaults with latest templates.

        L1 rules are framework-managed (not user-editable).
        This ensures AI always gets the latest tool list and behavior rules.
        """
        from smart.rules_template import RULES_TEMPLATE, TOOLS_TEMPLATE

        l1 = self.memory_dir / "L1"

        rules_file = l1 / "rules.md"
        rules_file.write_text(RULES_TEMPLATE, encoding="utf-8")

        tools_file = l1 / "tools.md"
        tools_file.write_text(TOOLS_TEMPLATE, encoding="utf-8")

    def load_l1(self) -> str:
        """Load all L1 rules as a single text block."""
        l1 = self.memory_dir / "L1"
        blocks = []
        for f in sorted(l1.glob("*.md")):
            blocks.append(f.read_text(encoding="utf-8"))
        return "\n\n".join(blocks)

    # ── L2: Verified Knowledge ──────────────────────────────────

    def save_l2(self, name: str, description: str, mem_type: str, content: str, filename: str = None) -> str:
        """Save a verified knowledge memory to L2."""
        if mem_type not in L2_TYPES:
            raise ValueError(f"type must be one of {L2_TYPES}")
        if not filename:
            safe_name = name.lower().replace(" ", "_").replace("/", "_")[:40]
            filename = f"{mem_type}_{safe_name}.md"
        filepath = self.memory_dir / "L2" / filename
        frontmatter = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"type: {mem_type}\n"
            f"updated: {datetime.now().isoformat()[:19]}\n"
            f"---\n\n"
            f"{content}\n"
        )
        filepath.write_text(frontmatter, encoding="utf-8")
        self._rebuild_index()
        return str(filepath)

    def load_l2(self, filename: str) -> dict:
        """Load an L2 memory, parsing frontmatter."""
        filepath = self.memory_dir / "L2" / filename
        if not filepath.exists():
            return {}
        text = filepath.read_text(encoding="utf-8")
        meta, body = self._parse_frontmatter(text)
        meta["body"] = body
        meta["filename"] = filename
        return meta

    def search_l2(self, query: str) -> list:
        """Search L2 memories by keyword."""
        results = []
        q = query.lower()
        for f in (self.memory_dir / "L2").glob("*.md"):
            meta = self.load_l2(f.name)
            text = f"{meta.get('name', '')} {meta.get('description', '')} {meta.get('body', '')}".lower()
            if q in text:
                results.append(meta)
        return results

    def list_l2(self, mem_type: str = None) -> list:
        """List L2 memories, optionally filtered by type."""
        results = []
        for f in (self.memory_dir / "L2").glob("*.md"):
            meta = self.load_l2(f.name)
            if mem_type is None or meta.get("type") == mem_type:
                results.append(meta)
        return results

    def delete_l2(self, filename: str) -> bool:
        """Delete an L2 memory file."""
        filepath = self.memory_dir / "L2" / filename
        if filepath.exists():
            filepath.unlink()
            self._rebuild_index()
            return True
        return False

    # ── L3: Dynamic Memory ──────────────────────────────────────

    def _load_l3(self) -> list:
        """Load L3 dynamic entries from JSON."""
        if not self.l3_file.exists():
            return []
        try:
            return json.loads(self.l3_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _save_l3(self, entries: list):
        """Save L3 dynamic entries to JSON."""
        self.l3_file.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def add_l3(self, key: str, value: str, mem_type: str = "context", importance: int = 3):
        """Add or update a dynamic memory entry in L3."""
        if mem_type not in L3_TTL:
            mem_type = "context"
        entries = self._load_l3()
        # Update existing entry if key matches
        for e in entries:
            if e.get("key") == key:
                e["value"] = value
                e["updated"] = datetime.now().isoformat()
                e["access_count"] = e.get("access_count", 0) + 1
                e["importance"] = importance
                self._save_l3(entries)
                return
        # Add new entry
        entries.append({
            "key": key,
            "value": value,
            "type": mem_type,
            "importance": importance,
            "created": datetime.now().isoformat(),
            "updated": datetime.now().isoformat(),
            "ttl_days": L3_TTL[mem_type],
            "access_count": 0,
        })
        self._save_l3(entries)

    def search_l3(self, query: str) -> list:
        """Search L3 dynamic memories by keyword. Increments access_count on match."""
        q = query.lower()
        results = []
        entries = self._load_l3()
        for e in entries:
            if q in e.get("key", "").lower() or q in e.get("value", "").lower():
                e["access_count"] = e.get("access_count", 0) + 1
                results.append(e)
        if results:
            self._save_l3(entries)
        return results

    def list_l3(self, mem_type: str = None) -> list:
        """List L3 entries, optionally filtered by type."""
        entries = self._load_l3()
        if mem_type:
            return [e for e in entries if e.get("type") == mem_type]
        return entries

    # ── GC + Promotion + Archive ────────────────────────────────

    def gc(self) -> dict:
        """Garbage collect expired L3 entries. Archive to L4. Promote popular ones to L2.

        Returns dict with counts: kept, promoted, archived.
        """
        entries = self._load_l3()
        now = datetime.now()
        keep, archive, promote = [], [], []

        for e in entries:
            created = datetime.fromisoformat(e["created"])
            age_days = (now - created).days
            ttl = e.get("ttl_days", 14)

            if e.get("access_count", 0) >= 3:
                promote.append(e)
            elif age_days > ttl:
                archive.append(e)
            else:
                keep.append(e)

        # Promote to L2
        for e in promote:
            self.save_l2(
                name=e["key"],
                description=f"Promoted from L3 ({e['type']})",
                mem_type="reference",
                content=e["value"],
            )
            log.info(f"L3->L2 promoted: {e['key']}")

        # Archive to L4
        if archive:
            month = now.strftime("%Y-%m")
            l4_file = self.memory_dir / "L4" / f"{month}.json"
            existing = []
            if l4_file.exists():
                try:
                    existing = json.loads(l4_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    existing = []
            existing.extend(archive)
            l4_file.write_text(
                json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            log.info(f"L3->L4 archived: {len(archive)} entries to {month}")

        self._save_l3(keep)
        return {"kept": len(keep), "promoted": len(promote), "archived": len(archive)}

    # ── L4: Monthly Archive ─────────────────────────────────────

    def list_l4(self) -> list:
        """List all L4 monthly archive files."""
        results = []
        for f in sorted((self.memory_dir / "L4").glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append({"month": f.stem, "count": len(data), "filename": f.name})
            except (json.JSONDecodeError, OSError):
                results.append({"month": f.stem, "count": 0, "filename": f.name})
        return results

    def load_l4(self, filename: str) -> list:
        """Load entries from an L4 monthly archive."""
        filepath = self.memory_dir / "L4" / filename
        if not filepath.exists():
            return []
        try:
            return json.loads(filepath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    # ── L5: Quarterly Summary ───────────────────────────────────

    def compress_to_l5(self, quarter: str = None) -> str:
        """Compress L4 monthly archives into an L5 quarterly summary.

        Args:
            quarter: e.g. "2026-Q1". If None, auto-detect from current date.

        Returns path to created L5 file.
        """
        now = datetime.now()
        if not quarter:
            q = (now.month - 1) // 3 + 1
            quarter = f"{now.year}-Q{q}"

        # Parse quarter to find matching months
        parts = quarter.split("-Q")
        year = int(parts[0])
        q_num = int(parts[1])
        months = [f"{year}-{m:02d}" for m in range((q_num - 1) * 3 + 1, q_num * 3 + 1)]

        all_entries = []
        for month in months:
            l4_file = self.memory_dir / "L4" / f"{month}.json"
            if l4_file.exists():
                try:
                    entries = json.loads(l4_file.read_text(encoding="utf-8"))
                    all_entries.extend(entries)
                except (json.JSONDecodeError, OSError):
                    pass

        if not all_entries:
            return ""

        # Build summary
        summary = {
            "quarter": quarter,
            "months": months,
            "entry_count": len(all_entries),
            "created": now.isoformat(),
            "topics": {},
        }
        for e in all_entries:
            t = e.get("type", "unknown")
            if t not in summary["topics"]:
                summary["topics"][t] = []
            summary["topics"][t].append({
                "key": e.get("key", ""),
                "value": e.get("value", "")[:200],
            })

        l5_file = self.memory_dir / "L5" / f"{quarter}.json"
        l5_file.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info(f"L4->L5 compressed: {len(all_entries)} entries into {quarter}")
        return str(l5_file)

    def list_l5(self) -> list:
        """List all L5 quarterly summary files."""
        results = []
        for f in sorted((self.memory_dir / "L5").glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append({
                    "quarter": data.get("quarter", f.stem),
                    "entry_count": data.get("entry_count", 0),
                    "filename": f.name,
                })
            except (json.JSONDecodeError, OSError):
                results.append({"quarter": f.stem, "entry_count": 0, "filename": f.name})
        return results

    # ── L6: Annual Summary ──────────────────────────────────────

    def compress_to_l6(self, year: int = None) -> str:
        """Compress L5 quarterly summaries into an L6 annual summary.

        Args:
            year: e.g. 2026. If None, use current year.

        Returns path to created L6 file.
        """
        if not year:
            year = datetime.now().year

        quarters = [f"{year}-Q{q}" for q in range(1, 5)]
        all_topics: dict[str, list] = {}
        total_entries = 0

        for quarter in quarters:
            l5_file = self.memory_dir / "L5" / f"{quarter}.json"
            if l5_file.exists():
                try:
                    data = json.loads(l5_file.read_text(encoding="utf-8"))
                    total_entries += data.get("entry_count", 0)
                    for topic_type, items in data.get("topics", {}).items():
                        if topic_type not in all_topics:
                            all_topics[topic_type] = []
                        all_topics[topic_type].extend(items)
                except (json.JSONDecodeError, OSError):
                    pass

        if total_entries == 0:
            return ""

        summary = {
            "year": year,
            "quarters": quarters,
            "total_entries": total_entries,
            "created": datetime.now().isoformat(),
            "topics": all_topics,
        }

        l6_file = self.memory_dir / "L6" / f"{year}.json"
        l6_file.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info(f"L5->L6 compressed: {total_entries} entries into {year}")
        return str(l6_file)

    def list_l6(self) -> list:
        """List all L6 annual summary files."""
        results = []
        for f in sorted((self.memory_dir / "L6").glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append({
                    "year": data.get("year", f.stem),
                    "total_entries": data.get("total_entries", 0),
                    "filename": f.name,
                })
            except (json.JSONDecodeError, OSError):
                results.append({"year": f.stem, "total_entries": 0, "filename": f.name})
        return results

    # ── Vector Search Integration ────────────────────────────────

    def _get_vector_search(self, config: dict = None):
        """Lazy-init vector search engine."""
        if not hasattr(self, "_vector_search"):
            try:
                from smart.vector import PFVectorSearch
                self._vector_search = PFVectorSearch(str(self.data_dir), config or {})
            except Exception as e:
                log.warning(f"Vector search init failed: {e}")
                self._vector_search = None
        return self._vector_search

    def search_semantic(self, query: str, top_k: int = 5, config: dict = None) -> list:
        """Semantic search across all indexed memories using vector similarity.

        Falls back to keyword search if vector search is unavailable.
        """
        vs = self._get_vector_search(config)
        if vs is None:
            return self.search_all(query)
        try:
            results = vs.search(query, top_k=top_k)
            return results
        except Exception as e:
            log.warning(f"Vector search failed, falling back to keyword: {e}")
            return self.search_all(query)

    def index_all_memories(self, config: dict = None):
        """Build/rebuild the vector index from all L2 + L3 memories."""
        vs = self._get_vector_search(config)
        if vs is None:
            log.warning("Vector search not available, skipping index build")
            return

        entries = []

        # Index L2 memories
        for f in (self.memory_dir / "L2").glob("*.md"):
            meta = self.load_l2(f.name)
            text = f"{meta.get('name', '')} {meta.get('description', '')} {meta.get('body', '')}"
            entries.append({
                "id": f"L2:{f.name}",
                "text": text,
                "metadata": {"layer": "L2", "type": meta.get("type", ""), "filename": f.name},
                "created_at": meta.get("updated", datetime.now().isoformat()),
            })

        # Index L3 memories
        for e in self._load_l3():
            entries.append({
                "id": f"L3:{e.get('key', '')}",
                "text": f"{e.get('key', '')} {e.get('value', '')}",
                "metadata": {"layer": "L3", "type": e.get("type", ""), "importance": e.get("importance", 3)},
                "created_at": e.get("created", datetime.now().isoformat()),
            })

        if entries:
            vs.rebuild_index(entries)
            log.info(f"Vector index built: {len(entries)} entries (L2+L3)")

    # ── Context for AI ──────────────────────────────────────────

    def get_context_block(self, query: str = "", config: dict = None) -> str:
        """Get compact memory context for AI prompt injection.

        If query is provided and vector search is available, includes
        semantically relevant memories in addition to core memories.

        Args:
            query: Current user message for semantic search (optional)
            config: Vector search config (optional)
        """
        blocks = []

        # L2: feedback and user memories (always include — these are core)
        for mem_type in ["feedback", "user"]:
            items = self.list_l2(mem_type)
            for i in items:
                blocks.append(f"[{mem_type}] {i.get('name', '')}: {i.get('body', '')[:200]}")

        # L3: recent dynamic memories (top 10 by importance)
        l3 = sorted(self._load_l3(), key=lambda x: x.get("importance", 0), reverse=True)[:10]
        for e in l3:
            blocks.append(f"[L3:{e.get('type', '')}] {e['key']}: {e['value'][:150]}")

        # Vector search: inject semantically relevant memories for current query
        if query:
            vs = self._get_vector_search(config)
            if vs and vs.store.count() > 0:
                try:
                    relevant = vs.search(query, top_k=3, use_mmr=True)
                    seen_ids = set()
                    for r in relevant:
                        rid = r.get("id", "")
                        if rid not in seen_ids and r.get("score", 0) > 0.3:
                            seen_ids.add(rid)
                            layer = r.get("metadata", {}).get("layer", "?")
                            blocks.append(f"[relevant:{layer}] {r['text'][:200]}")
                except Exception as e:
                    log.debug(f"Context vector search skipped: {e}")

        return "\n".join(blocks)

    # ── Search All Layers ───────────────────────────────────────

    def search_all(self, query: str) -> list:
        """Search across L2 and L3 (keyword-based)."""
        results = []
        for r in self.search_l2(query):
            r["layer"] = "L2"
            results.append(r)
        for r in self.search_l3(query):
            r["layer"] = "L3"
            results.append(r)
        return results

    # ── Statistics ──────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get entry counts for all memory layers."""
        stats = {
            "L1": len(list((self.memory_dir / "L1").glob("*.md"))),
            "L2": len(list((self.memory_dir / "L2").glob("*.md"))),
            "L3": len(self._load_l3()),
            "L4": len(list((self.memory_dir / "L4").glob("*.json"))),
            "L5": len(list((self.memory_dir / "L5").glob("*.json"))),
            "L6": len(list((self.memory_dir / "L6").glob("*.json"))),
        }
        # Add vector stats if available
        vs = self._get_vector_search() if hasattr(self, "_vector_search") else None
        if vs:
            stats["vectors"] = vs.store.count()
        return stats

    # ── Backward Compatibility ──────────────────────────────────

    def ensure_defaults(self):
        """Backward-compatible alias for ensure_l1_defaults.

        Also migrates any old flat memory files into L2.
        """
        self.ensure_l1_defaults()
        self._migrate_flat_to_l2()

    def save(self, name: str, description: str, mem_type: str, content: str, filename: str = None) -> str:
        """Backward-compatible save — routes to save_l2."""
        return self.save_l2(name, description, mem_type, content, filename)

    def load(self, filename: str) -> dict:
        """Backward-compatible load — tries L2 first."""
        return self.load_l2(filename)

    def search(self, query: str) -> list:
        """Backward-compatible search — searches all layers."""
        return self.search_all(query)

    def list_by_type(self, mem_type: str) -> list:
        """Backward-compatible list_by_type — routes to list_l2."""
        return self.list_l2(mem_type)

    def delete(self, filename: str) -> bool:
        """Backward-compatible delete — routes to delete_l2."""
        return self.delete_l2(filename)

    # ── Helpers ─────────────────────────────────────────────────

    def _parse_frontmatter(self, text: str) -> tuple:
        """Parse YAML frontmatter from a markdown file.

        Returns (meta_dict, body_text).
        """
        meta, body = {}, text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
                body = parts[2].strip()
        return meta, body

    def _rebuild_index(self):
        """Rebuild INDEX.md from all memory layers."""
        lines = ["# Permafrost Memory Index\n"]

        lines.append("## L1: Core Rules")
        for f in sorted((self.memory_dir / "L1").glob("*.md")):
            lines.append(f"- {f.stem}")

        lines.append("\n## L2: Verified Knowledge")
        for f in sorted((self.memory_dir / "L2").glob("*.md")):
            meta = self.load_l2(f.name)
            lines.append(f"- [{meta.get('type', '?')}] {meta.get('name', f.stem)}: {meta.get('description', '')}")

        l3 = self._load_l3()
        lines.append(f"\n## L3: Dynamic ({len(l3)} entries)")

        for layer in ["L4", "L5", "L6"]:
            files = list((self.memory_dir / layer).glob("*.json"))
            lines.append(f"\n## {layer}: {len(files)} files")

        self.index_file.write_text("\n".join(lines), encoding="utf-8")

    def _migrate_flat_to_l2(self):
        """Migrate old flat memory files (in memory_dir root) into L2 subdirectory."""
        for f in self.memory_dir.glob("*.md"):
            if f.name == "INDEX.md":
                continue
            dest = self.memory_dir / "L2" / f.name
            if not dest.exists():
                try:
                    dest.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
                    f.unlink()
                    log.info(f"Migrated flat memory to L2: {f.name}")
                except OSError as e:
                    log.warning(f"Migration failed for {f.name}: {e}")
