"""
Codex Launcher Plugin System - Custom provider plugins.

Plugins are discovered from ~/.codex/plugins/ and loaded dynamically.
Each plugin must define a register() function that returns provider metadata.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

PLUGIN_DIR = Path.home() / ".codex" / "plugins"
BUILTIN_PROVIDERS_FILE = Path(__file__).parent.parent / "codex_launcher_lib.py"


class PluginError(Exception):
    """Plugin loading or execution error."""
    pass


def discover_plugins():
    """Find all .py plugin files in the plugin directory."""
    plugins = []
    if not PLUGIN_DIR.exists():
        return plugins
    for f in sorted(PLUGIN_DIR.glob("*.py")):
        if f.name.startswith("_"):
            continue
        plugins.append(f)
    return plugins


def load_plugin(path):
    """Load a single plugin file and call its register() function."""
    module_name = f"codex_plugin_{path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "register"):
            raise PluginError(f"Plugin {path.name} has no register() function")

        info = module.register()
        required_fields = {"name", "backend_type", "base_url"}
        if not required_fields.issubset(info.keys()):
            missing = required_fields - set(info.keys())
            raise PluginError(f"Plugin {path.name} missing fields: {missing}")

        info["_plugin_file"] = str(path)
        info["_plugin_name"] = path.stem
        return info

    except PluginError:
        raise
    except Exception as e:
        raise PluginError(f"Failed to load plugin {path.name}: {e}")


def load_all_plugins():
    """Load all discovered plugins. Returns list of provider info dicts."""
    providers = []
    errors = []
    for plugin_path in discover_plugins():
        try:
            info = load_plugin(plugin_path)
            providers.append(info)
        except PluginError as e:
            errors.append(str(e))

    if errors:
        import warnings
        for err in errors:
            warnings.warn(f"Plugin error: {err}")

    return providers


def get_plugin_provider_presets():
    """Get provider presets from all loaded plugins."""
    presets = {}
    for info in load_all_plugins():
        name = info["name"]
        presets[name] = {
            "backend_type": info["backend_type"],
            "base_url": info["base_url"],
            "model": info.get("model", ""),
            "api_key": info.get("api_key", ""),
            "is_default": False,
            "_plugin": True,
        }
    return presets
