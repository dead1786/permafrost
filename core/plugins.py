"""
Permafrost Plugin System — Auto-discover and load extension plugins.

Plugins are Python packages in the `plugins/` directory. Each plugin can:
  - Register new tools (via @register_tool)
  - Register new channels (via @register_channel)
  - Add hooks (on_start, on_message_in, etc.)
  - Add scheduled tasks

Plugin structure:
  plugins/
    my_plugin/
      plugin.json     # Manifest (name, version, description, entry)
      __init__.py     # Auto-imported, registers tools/channels/hooks
      ...

Manifest (plugin.json):
  {
    "name": "my_plugin",
    "version": "1.0.0",
    "description": "What this plugin does",
    "author": "Author Name",
    "entry": "__init__",          // Python module to import (default: __init__)
    "requires": ["requests"],     // pip dependencies (informational)
    "config_fields": [            // Plugin-specific config (shown in UI)
      {"name": "api_key", "label": "API Key", "type": "password", "required": true}
    ]
  }

Usage:
  manager = PFPluginManager(plugins_dir="plugins/", config=config)
  manager.discover()    # Find all plugins
  manager.load_all()    # Import and activate enabled plugins
"""

import importlib
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger("permafrost.plugins")


class PluginInfo:
    """Metadata for a discovered plugin."""

    def __init__(self, path: Path, manifest: dict):
        self.path = path
        self.name = manifest.get("name", path.name)
        self.version = manifest.get("version", "0.0.0")
        self.description = manifest.get("description", "")
        self.author = manifest.get("author", "")
        self.entry = manifest.get("entry", "__init__")
        self.requires = manifest.get("requires", [])
        self.config_fields = manifest.get("config_fields", [])
        self.enabled = True
        self.loaded = False
        self.error = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "path": str(self.path),
            "enabled": self.enabled,
            "loaded": self.loaded,
            "error": self.error,
            "config_fields": self.config_fields,
        }


class PFPluginManager:
    """Discovers, loads, and manages Permafrost plugins."""

    def __init__(self, plugins_dir: str = None, data_dir: str = None, config: dict = None):
        if plugins_dir:
            self.plugins_dir = Path(plugins_dir)
        elif data_dir:
            self.plugins_dir = Path(data_dir) / "plugins"
        else:
            self.plugins_dir = Path("plugins")
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

        self.config = config or {}
        self.plugins: dict[str, PluginInfo] = {}
        self._state_file = self.plugins_dir / "plugin-state.json"

    def discover(self) -> list[PluginInfo]:
        """Scan plugins directory for valid plugins. Returns list of found plugins."""
        self.plugins.clear()

        for item in self.plugins_dir.iterdir():
            if not item.is_dir():
                continue
            if item.name.startswith("_") or item.name.startswith("."):
                continue

            manifest_file = item / "plugin.json"
            if not manifest_file.exists():
                # Check for __init__.py as minimal plugin
                init_file = item / "__init__.py"
                if not init_file.exists():
                    continue
                manifest = {"name": item.name}
            else:
                try:
                    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as e:
                    log.warning(f"Plugin '{item.name}': bad manifest: {e}")
                    continue

            plugin = PluginInfo(item, manifest)

            # Check enabled state from saved state
            state = self._load_state()
            if plugin.name in state:
                plugin.enabled = state[plugin.name].get("enabled", True)

            # Check enabled from config
            config_key = f"plugin_{plugin.name}_enabled"
            if config_key in self.config:
                plugin.enabled = bool(self.config[config_key])

            self.plugins[plugin.name] = plugin
            log.debug(f"Discovered plugin: {plugin.name} v{plugin.version}")

        log.info(f"Discovered {len(self.plugins)} plugin(s)")
        return list(self.plugins.values())

    def load_all(self):
        """Load all enabled plugins."""
        if not self.plugins:
            self.discover()

        loaded = 0
        for name, plugin in self.plugins.items():
            if not plugin.enabled:
                log.debug(f"Plugin '{name}' disabled, skipping")
                continue
            if self._load_plugin(plugin):
                loaded += 1

        log.info(f"Loaded {loaded}/{len(self.plugins)} plugin(s)")

    def _load_plugin(self, plugin: PluginInfo) -> bool:
        """Import and activate a single plugin."""
        try:
            # Add plugin directory to sys.path if not already
            plugin_parent = str(plugin.path.parent)
            if plugin_parent not in sys.path:
                sys.path.insert(0, plugin_parent)

            # Import the entry module
            module_name = f"{plugin.path.name}.{plugin.entry}" if plugin.entry != "__init__" else plugin.path.name
            if module_name in sys.modules:
                # Reload if already imported
                importlib.reload(sys.modules[module_name])
            else:
                importlib.import_module(module_name)

            plugin.loaded = True
            plugin.error = ""
            log.info(f"Loaded plugin: {plugin.name} v{plugin.version}")
            return True

        except Exception as e:
            plugin.loaded = False
            plugin.error = str(e)
            log.error(f"Plugin '{plugin.name}' load failed: {e}")
            return False

    def enable(self, name: str) -> bool:
        """Enable a plugin."""
        if name in self.plugins:
            self.plugins[name].enabled = True
            self._save_state()
            if not self.plugins[name].loaded:
                return self._load_plugin(self.plugins[name])
            return True
        return False

    def disable(self, name: str) -> bool:
        """Disable a plugin (will not load on next start)."""
        if name in self.plugins:
            self.plugins[name].enabled = False
            self._save_state()
            return True
        return False

    def list_plugins(self) -> list[dict]:
        """List all discovered plugins with their status."""
        return [p.to_dict() for p in self.plugins.values()]

    def get_plugin(self, name: str) -> PluginInfo | None:
        return self.plugins.get(name)

    def _load_state(self) -> dict:
        if not self._state_file.exists():
            return {}
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_state(self):
        state = {}
        for name, plugin in self.plugins.items():
            state[name] = {"enabled": plugin.enabled}
        try:
            self._state_file.write_text(
                json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:
            log.error(f"plugin state save failed: {e}")
