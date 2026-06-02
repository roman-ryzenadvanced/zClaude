# Changelog

All notable changes to zClaude will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-06-02

### 🎉 Initial Release — Universal AI Proxy & Launcher

#### Added
- **Universal Translation Proxy** (`translate-proxy.py`)
  - Multi-provider support: OpenAI-compatible, Anthropic, Google Gemini/Antigravity,
    AWS Kiro, CodeBuff, Command Code, auto-detect
  - Protocol translation between Responses API, Chat Completions, Messages API, gRPC, SSE
  - Full streaming support with buffered event delivery
  - Adaptive context compaction with CROF (Context-Remaining-Optimized-Fallback)
  - Anti-loop defense system (NULL-tool detection, budget caps, read-loop detection)
  - Auto-continue for truncated responses
  - Vision fallback — describes images via vision-capable models when target doesn't support them
  - Circuit breaker pattern with automatic recovery (CLOSED → OPEN → HALF_OPEN)
  - Multi-account rotation (API key pools + OAuth account pools)
  - BGP (Backend Gateway Protocol) routing across multiple providers
  - Smart tool-call repair and synthesis
  - Prompt enhancer (offline/online modes)
  - Caveman mode for concise output
  - RTK compression for token optimization
  - Hot-reload API keys without restart
  - Request cancellation with graceful drain
  - PID-based anti-stall cleanup on startup
  - Rate limiting with Token Bucket algorithm per route
  - Secret redaction in all logs

- **Provider Manager** (`provider_manager.py`) 🆕
  - Interactive TUI for full provider lifecycle management
  - Add / edit / remove providers with guided wizards
  - Model catalog management per backend type
  - Set default provider
  - Test connectivity to any provider endpoint
  - Export/import configuration as JSON
  - Full setup wizard for new users
  - Cross-platform input handling (Linux, macOS, Windows, Termux)
  - Colorized output with graceful fallback on non-TTY

- **Session Manager** (`session_manager.py`)
  - Unified scanner for Codex CLI, Claude Code, and Gemini CLI sessions
  - Parse session metadata (model, project, timestamps, titles)
  - Load full conversation messages from any provider
  - Session deletion with path validation (security)
  - XML tag cleaning for title extraction

- **GUI Applications**
  - Classic GUI (`codex-launcher-gui.py`) — tkinter-based launcher
  - Modern X Edition (`codex-launcher-gui-x.py`) — dark theme, Warp-inspired layout
  - Endpoint configuration dialogs
  - OAuth login flows (Google Antigravity, Kiro SSO)
  - BGP route management UI
  - Real-time monitoring dashboard
  - Session browser with message viewer
  - Usage statistics and request history
  - Latency benchmarking tool
  - Auto-update checker
  - Live log console viewer

- **Cross-Platform Runtime** (`universal_runtime.py`)
  - Environment detection: Linux, macOS, Windows, WSL, Termux, Android
  - Per-platform runtime profiles with safe defaults
  - Dependency checking and missing-dependency reporting
  - Offline queue for disconnected operation
  - Doctor++ health check system
  - Session pack export/import (ZIP bundles)
  - Incident classification with playbook routing
  - Policy-based provider selection (latency/cost/reliability)

- **Configuration System**
  - JSON schema validation (`config_schema.py`)
  - TOML config generation for Codex CLI integration
  - Endpoints management with multi-default detection
  - Proxy config with hot-reload support
  - Dynamic model catalog fetching (Kiro ListAvailableModels, Gemini OAuth discovery)

- **Installers**
  - `install.sh` — Linux & macOS installer
  - `install.ps1` — Windows PowerShell installer
  - `install-termux.sh` — Android/Termux installer with battery awareness
  - `termux-daemon.sh` — Background daemon manager with notifications
  - `verify-universal.sh` — Cross-platform test runner

- **Test Suite**
  - `test-antigravity.sh` — Comprehensive E2E test suite
  - Token validity verification
  - Direct REST endpoint probing (prod + sandbox)
  - Proxy adapter end-to-end testing
  - Real Codex CLI task execution
  - Anomaly monitoring (stalls, loops, errors, compaction)
  - Anti-loop defense verification

- **Internationalization**
  - English, Spanish, Chinese locale files
  - Extensible locale system

- **Plugin System**
  - Base plugin class for extensibility

- **Documentation**
  - Comprehensive README with quick start guides
  - Architecture overview
  - API endpoint reference
  - Cross-platform details for all supported OSes
  - Environment variable reference
  - Provider compatibility matrix

### Security
- All secrets redacted from logs by default
- Path validation on session file operations
- No hardcoded credentials in source code
- Credentials stored in OS-appropriate secure locations
- OAuth token refresh with automatic expiry handling
- `.gitignore` excludes all credential/token files

---

## [Unreleased]

### Planned
- Docker container image
- systemd / launchd service files
- Web dashboard (browser-based UI)
- MCP server mode
- Plugin marketplace
- CI/CD pipeline with GitHub Actions
- Performance benchmarks and comparison charts
