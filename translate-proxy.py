#!/usr/bin/env python3
"""Backward-compatible shim for translate-proxy.py.
All functionality has been moved to the src/proxy/ package.
"""
import sys
import os
import types
import gc

# Ensure src/ is in sys.path
_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from proxy import main

class _ProxyModuleShim(types.ModuleType):
    def _init_modules(self):
        proxy_mods = self.__dict__.get("_proxy_modules")
        if proxy_mods is None:
            import proxy.config
            import proxy.shared_utils
            import proxy.compaction
            import proxy.tool_validation
            import proxy.logging_utils
            import proxy.auth_pools
            import proxy.cc_parser
            import proxy.server
            import proxy.adapters.openai
            import proxy.adapters.kiro
            import proxy.adapters.command_code
            import proxy.adapters.anthropic
            import proxy.adapters.auto_sense
            import proxy.adapters.gemini_helpers
            import proxy.adapters.gemini

            proxy_mods = [
                proxy.config,
                proxy.shared_utils,
                proxy.compaction,
                proxy.tool_validation,
                proxy.logging_utils,
                proxy.auth_pools,
                proxy.cc_parser,
                proxy.server,
                proxy.adapters.openai,
                proxy.adapters.kiro,
                proxy.adapters.command_code,
                proxy.adapters.anthropic,
                proxy.adapters.auto_sense,
                proxy.adapters.gemini_helpers,
                proxy.adapters.gemini,
            ]
            self.__dict__["_proxy_modules"] = proxy_mods

    def __getattr__(self, name):
        if name == "main":
            return main
        self._init_modules()
        proxy_mods = self.__dict__.get("_proxy_modules")
        for mod in proxy_mods:
            if hasattr(mod, name):
                return getattr(mod, name)
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

    def __setattr__(self, name, value):
        if name in ("_proxy_modules", "__class__", "__spec__", "__file__", "__name__", "__package__", "__loader__", "__path__", "__cached__"):
            self.__dict__[name] = value
            return
        self._init_modules()
        proxy_mods = self.__dict__.get("_proxy_modules")
        found = False
        for mod in proxy_mods:
            if hasattr(mod, name):
                setattr(mod, name, value)
                found = True
        if not found:
            import proxy.config
            setattr(proxy.config, name, value)

    def __dir__(self):
        self._init_modules()
        proxy_mods = self.__dict__.get("_proxy_modules")
        keys = {"main"}
        for mod in proxy_mods:
            keys.update(dir(mod))
        return sorted(list(keys))

# Find the module object in sys.modules, or dynamically via gc if executed directly/manually
self_module = sys.modules.get(__name__)
if self_module is None:
    for obj in gc.get_referrers(globals()):
        if isinstance(obj, types.ModuleType) and getattr(obj, "__name__", None) == __name__:
            self_module = obj
            break

if self_module is not None:
    self_module.__class__ = _ProxyModuleShim

if __name__ == "__main__":
    main()
