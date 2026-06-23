"""
Tests for core/plugins.py — PFPluginManager plugin lifecycle system.
"""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plugins import PFPluginManager, PluginInfo


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_plugin(plugins_dir, name, manifest=None, has_init=True):
    """Create a minimal plugin directory with manifest and __init__.py."""
    plugin_path = os.path.join(plugins_dir, name)
    os.makedirs(plugin_path, exist_ok=True)
    if manifest is not None:
        with open(os.path.join(plugin_path, "plugin.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)
    if has_init:
        with open(os.path.join(plugin_path, "__init__.py"), "w", encoding="utf-8") as f:
            f.write("# test plugin\n")
    return plugin_path


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def plugins_dir(tmp_path):
    d = tmp_path / "plugins"
    d.mkdir()
    return str(d)


@pytest.fixture
def manager(plugins_dir):
    return PFPluginManager(plugins_dir=plugins_dir)


# ── PluginInfo ────────────────────────────────────────────────────────────────


class TestPluginInfo:
    def test_defaults_from_minimal_manifest(self, tmp_path):
        p = PluginInfo(tmp_path, {"name": "myplugin"})
        assert p.name == "myplugin"
        assert p.version == "0.0.0"
        assert p.description == ""
        assert p.author == ""
        assert p.entry == "__init__"
        assert p.requires == []
        assert p.config_fields == []
        assert p.enabled is True
        assert p.loaded is False
        assert p.error == ""

    def test_full_manifest_parsed(self, tmp_path):
        manifest = {
            "name": "full",
            "version": "2.1.0",
            "description": "Full plugin",
            "author": "Test Author",
            "entry": "main",
            "requires": ["requests", "aiohttp"],
            "config_fields": [{"name": "api_key", "type": "password"}],
        }
        p = PluginInfo(tmp_path, manifest)
        assert p.version == "2.1.0"
        assert p.description == "Full plugin"
        assert p.author == "Test Author"
        assert p.entry == "main"
        assert p.requires == ["requests", "aiohttp"]
        assert len(p.config_fields) == 1

    def test_to_dict_contains_expected_keys(self, tmp_path):
        p = PluginInfo(tmp_path, {"name": "x"})
        d = p.to_dict()
        for key in ("name", "version", "description", "author", "path",
                    "enabled", "loaded", "error", "config_fields"):
            assert key in d

    def test_name_falls_back_to_dir_name(self, tmp_path):
        p = PluginInfo(tmp_path, {})
        assert p.name == tmp_path.name


# ── PFPluginManager init ──────────────────────────────────────────────────────


class TestPFPluginManagerInit:
    def test_default_plugins_dir_created(self, tmp_path):
        # data_dir path
        m = PFPluginManager(data_dir=str(tmp_path))
        assert (tmp_path / "plugins").is_dir()

    def test_explicit_plugins_dir(self, plugins_dir):
        m = PFPluginManager(plugins_dir=plugins_dir)
        assert m.plugins_dir.exists()

    def test_fallback_plugins_dir(self):
        # Neither plugins_dir nor data_dir provided — defaults to "plugins"
        m = PFPluginManager()
        assert m.plugins_dir.name == "plugins"


# ── discover ──────────────────────────────────────────────────────────────────


class TestDiscover:
    def test_empty_dir_returns_empty(self, manager):
        found = manager.discover()
        assert found == []

    def test_finds_plugin_with_manifest(self, manager, plugins_dir):
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin", "version": "1.0.0"})
        found = manager.discover()
        assert len(found) == 1
        assert found[0].name == "myplugin"

    def test_finds_plugin_with_only_init(self, manager, plugins_dir):
        make_plugin(plugins_dir, "nomanifest", manifest=None, has_init=True)
        found = manager.discover()
        assert len(found) == 1

    def test_skips_files_not_dirs(self, manager, plugins_dir):
        # Create a file (not dir) in plugins_dir
        open(os.path.join(plugins_dir, "file.txt"), "w").close()
        found = manager.discover()
        assert len(found) == 0

    def test_skips_underscore_dirs(self, manager, plugins_dir):
        make_plugin(plugins_dir, "_private", manifest={"name": "_private"})
        found = manager.discover()
        assert len(found) == 0

    def test_skips_dot_dirs(self, manager, plugins_dir):
        dotdir = os.path.join(plugins_dir, ".hidden")
        os.makedirs(dotdir, exist_ok=True)
        found = manager.discover()
        assert len(found) == 0

    def test_skips_dir_without_init_or_manifest(self, manager, plugins_dir):
        os.makedirs(os.path.join(plugins_dir, "empty_plugin"), exist_ok=True)
        found = manager.discover()
        assert len(found) == 0

    def test_skips_bad_manifest_json(self, manager, plugins_dir, caplog):
        import logging
        plugin_path = os.path.join(plugins_dir, "badplugin")
        os.makedirs(plugin_path, exist_ok=True)
        with open(os.path.join(plugin_path, "plugin.json"), "w") as f:
            f.write("NOT VALID JSON{{{")
        with caplog.at_level(logging.WARNING, logger="permafrost.plugins"):
            found = manager.discover()
        assert len(found) == 0
        assert "bad manifest" in caplog.text

    def test_multiple_plugins_discovered(self, manager, plugins_dir):
        make_plugin(plugins_dir, "plugin_a", manifest={"name": "plugin_a"})
        make_plugin(plugins_dir, "plugin_b", manifest={"name": "plugin_b"})
        found = manager.discover()
        assert len(found) == 2

    def test_plugin_enabled_by_default(self, manager, plugins_dir):
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin"})
        found = manager.discover()
        assert found[0].enabled is True

    def test_config_overrides_enabled(self, plugins_dir):
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin"})
        m = PFPluginManager(plugins_dir=plugins_dir, config={"plugin_myplugin_enabled": False})
        found = m.discover()
        assert found[0].enabled is False

    def test_discover_clears_previous_state(self, manager, plugins_dir):
        make_plugin(plugins_dir, "plugin_a", manifest={"name": "plugin_a"})
        manager.discover()
        assert len(manager.plugins) == 1
        # Add another and re-discover
        make_plugin(plugins_dir, "plugin_b", manifest={"name": "plugin_b"})
        manager.discover()
        assert len(manager.plugins) == 2


# ── load_all ──────────────────────────────────────────────────────────────────


class TestLoadAll:
    def test_load_all_imports_enabled_plugin(self, manager, plugins_dir):
        make_plugin(plugins_dir, "validplugin", manifest={"name": "validplugin"})
        manager.discover()
        manager.load_all()
        assert manager.plugins["validplugin"].loaded is True

    def test_load_all_skips_disabled_plugin(self, manager, plugins_dir):
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin"})
        manager.discover()
        manager.plugins["myplugin"].enabled = False
        manager.load_all()
        assert manager.plugins["myplugin"].loaded is False

    def test_load_all_calls_discover_if_empty(self, manager, plugins_dir):
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin"})
        # Don't call discover manually
        manager.load_all()
        assert "myplugin" in manager.plugins

    def test_load_bad_plugin_records_error(self, manager, plugins_dir):
        # Plugin with bad __init__.py that raises on import
        plugin_path = os.path.join(plugins_dir, "badplugin")
        os.makedirs(plugin_path, exist_ok=True)
        with open(os.path.join(plugin_path, "__init__.py"), "w") as f:
            f.write("raise RuntimeError('intentional error')\n")
        manager.discover()
        manager.load_all()
        assert manager.plugins["badplugin"].loaded is False
        assert manager.plugins["badplugin"].error != ""


# ── enable / disable ──────────────────────────────────────────────────────────


class TestEnableDisable:
    def test_disable_existing_plugin(self, manager, plugins_dir):
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin"})
        manager.discover()
        result = manager.disable("myplugin")
        assert result is True
        assert manager.plugins["myplugin"].enabled is False

    def test_disable_nonexistent_plugin(self, manager):
        result = manager.disable("doesnotexist")
        assert result is False

    def test_enable_existing_plugin(self, manager, plugins_dir):
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin"})
        manager.discover()
        manager.disable("myplugin")
        result = manager.enable("myplugin")
        assert result is True
        assert manager.plugins["myplugin"].enabled is True

    def test_enable_nonexistent_plugin(self, manager):
        result = manager.enable("doesnotexist")
        assert result is False

    def test_enable_saves_state(self, manager, plugins_dir):
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin"})
        manager.discover()
        manager.disable("myplugin")
        manager.enable("myplugin")
        state = manager._load_state()
        assert state["myplugin"]["enabled"] is True

    def test_disable_saves_state(self, manager, plugins_dir):
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin"})
        manager.discover()
        manager.disable("myplugin")
        state = manager._load_state()
        assert state["myplugin"]["enabled"] is False


# ── list_plugins / get_plugin ─────────────────────────────────────────────────


class TestListAndGet:
    def test_list_plugins_returns_dicts(self, manager, plugins_dir):
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin"})
        manager.discover()
        lst = manager.list_plugins()
        assert len(lst) == 1
        assert isinstance(lst[0], dict)
        assert lst[0]["name"] == "myplugin"

    def test_list_plugins_empty(self, manager):
        manager.discover()
        assert manager.list_plugins() == []

    def test_get_plugin_returns_info(self, manager, plugins_dir):
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin"})
        manager.discover()
        info = manager.get_plugin("myplugin")
        assert isinstance(info, PluginInfo)
        assert info.name == "myplugin"

    def test_get_plugin_returns_none_for_unknown(self, manager):
        assert manager.get_plugin("unknown") is None


# ── _load_state / _save_state ─────────────────────────────────────────────────


class TestStateFile:
    def test_load_state_returns_empty_when_no_file(self, manager):
        state = manager._load_state()
        assert state == {}

    def test_save_then_load_state(self, manager, plugins_dir):
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin"})
        manager.discover()
        manager.disable("myplugin")
        # Force reload to ensure state persisted
        manager2 = PFPluginManager(plugins_dir=plugins_dir)
        make_plugin(plugins_dir, "myplugin", manifest={"name": "myplugin"})
        found = manager2.discover()
        assert found[0].enabled is False

    def test_load_state_handles_corrupt_file(self, manager, plugins_dir):
        state_file = os.path.join(plugins_dir, "plugin-state.json")
        with open(state_file, "w") as f:
            f.write("CORRUPT{{")
        state = manager._load_state()
        assert state == {}
