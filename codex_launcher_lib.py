#!/usr/bin/env python3
"""Codex Launcher shared library — backward-compatible shim.

All functionality has been moved to the src/lib/ package.
This file re-exports everything for backward compatibility.
"""

# Ensure src/ is on the path so `from lib.xxx` works
import os
import sys
from pathlib import Path

_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

# Re-export the entire public API from the lib package
# Note: `import *` does NOT export names starting with `_`,
# so private symbols used by consumers are explicitly imported.

from lib.constants import *                     # noqa: F401,F403
from lib.changelog import CHANGELOG            # noqa: F401
from lib.presets import PROVIDER_PRESETS, apply_provider_preset  # noqa: F401
from lib.utils import *                        # noqa: F401,F403
from lib.utils import _profile_slug, _fmt_tok, _fmt_dur, _status_pill, _usage_theme  # noqa: F401
from lib.process import *                      # noqa: F401,F403
from lib.process import _load_pid_registry, _save_pid_registry, _subprocess_new_group_flag, _subprocess_preexec_fn, _kill_process_group, _kill_process_group_soft, _register_pgid_entry  # noqa: F401
from lib.platform_utils import *               # noqa: F401,F403
from lib.oauth_secrets import *                # noqa: F401,F403
from lib.bootstrap import *                    # noqa: F401,F403
from lib.endpoints import *                    # noqa: F401,F403
from lib.profiles import *                     # noqa: F401,F403
from lib.config_manager import *               # noqa: F401,F403
from lib.config_manager import _rotate_backups, _toml_safe, _resolve_secret, _merge_toml, _gen_model_catalog  # noqa: F401
from lib.model_fetcher import *                # noqa: F401,F403
from lib.model_fetcher import _fetch_kiro_models  # noqa: F401
from lib.doctor import *                       # noqa: F401,F403
from lib.doctor import _doctor_check_streaming, _doctor_check_toolcall  # noqa: F401
from lib.proxy_lifecycle import *              # noqa: F401,F403
from lib.proxy_lifecycle import PROXY_PORT, _get_proxy_port, _start_proxy_with_config  # noqa: F401
from lib.codex_detect import *                 # noqa: F401,F403
from lib.monitoring import *                   # noqa: F401,F403
from lib.monitoring import _LogAnalyzerThread  # noqa: F401
