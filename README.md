![Z.ai Coding Plans](https://img.shields.io/badge/Z.ai-Coding_Plans-7C3AED?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNTAgMjUwIj48cGF0aCBmaWxsPSIjN0MzQUVEIiBkPSJNMTI1IDBDMTU1LjkgMCAxODAgMjQuMSAxODAgNTRWMjI2QzE4MCAyNDYuNiAxNTUuOSAyNzAgMTI1IDI3MEM5NC4xIDI3MCA3MCAyNDYuNiA3MCAyMjZWNTRDNyAyNC4xIDk0LjEgMCAxMjUgMEwwIDEyNUMwIDU1LjkgMjQuMSAzMCA1NS45IDMwSDk0LjFDMTE5LjcgMzAgMTQwIDUwLjMgMTQwIDc1VjE3NUMxNDAgMTk5LjYgMTE5LjcgMjIwIDk0LjIgMjIwSDU1LjlDMzAgMjIwIDEwIDE5OS42IDEwIDE3NVY3NUMxMCA1MC4zIDMwLjMgMzAgNTUuOSAzMEgxNjkuMUMxOTQuNyAzMCAyMTUgNTAuMyAyMTUgNzVWMTc1QzIxNSAxOTkuNiAxOTQuNyAyMjAgMTY5LjIgMjIwSDE1MC44QzEyNSAyMjAgMTAwIDE5OS42IDEwMCAxNzVWNzVDMTAwIDUwLjMgNzkuMyAzMCA1NC45IDMwSDEyNVoiLz48L3N2Zz4=)

## 🚀 Get 10% OFF Z.ai Coding Plans

__Access the latest GLM models for coding with an exclusive discount__

👉 Claim Your 10% OFF →

_Power your development with state-of-the-art AI models — from code generation to full-stack apps_

---

# 🔮 zClaude

**Universal AI Proxy & Launcher** — Multi-provider bridge for Claude, Gemini, GPT, Kiro, CodeBuff & more. Cross-platform: **Linux · macOS · Windows · Android/Termux**

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Linux-passed-brightgreen?logo=linux&logoColor=black" alt="Linux">
  <img src="https://img.shields.io/badge/macOS-passed-brightgreen?logo=apple&logoColor=white" alt="macOS">
  <img src="https://img.shields.io/badge/Windows-passed-brightgreen?logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/Termux-passed-brightgreen?&logo=android&logoColor=white" alt="Termux">
  <img src="https://img.shields.io/badge/License-MIT-green?logo=opensourceinitiative&logoColor=white" alt="License">
</p>

---

## ✨ What Is zClaude?

zClaude is a **universal translation proxy** that lets any AI coding tool (Codex CLI, Claude Code, OpenCode, Kiro, etc.) talk to **any AI provider**. One prompt → any backend. No vendor lock-in.

```
Your favorite AI coding tool
        │
        ▼
   ┌─────────┐
   │  zClaude │  ← Universal translation layer
   │  Proxy   │     Auto-detects protocol, converts formats
   └────┬─────┘
        │
   ┌────┼────┬──────────┬────────────┐
   ▼    ▼    ▼          ▼            ▼
 Claude GPT  Gemini    DeepSeek    Kiro ... (any provider)
```

### 🧠 What It Does

| Feature | Description |
|---------|-------------|
| **Multi-Provider** | 9+ backend types: OpenAI-compatible, Anthropic, Google Gemini/Antigravity, AWS Kiro, CodeBuff, Command Code |
| **Auto-Sense** | Probes unknown endpoints, detects API style, adapts automatically |
| **Protocol Translation** | Converts between Responses API, Chat Completions, Messages API, gRPC, SSE streaming |
| **Smart Compaction** | Adaptive context trimming when you hit token limits — auto-retry with compacted history |
| **Anti-Loop Defense** | Detects and breaks infinite tool-call loops, budget caps, NULL-tool cycles |
| **Auto-Continue** | Truncated responses are automatically continued until complete |
| **Vision Fallback** | Automatically describes images via vision-capable models when target doesn't support images |
| **Circuit Breaker** | Prevents cascading failures with automatic recovery |
| **Multi-Account Rotation** | API key pools + OAuth account rotation for rate limit resilience |
| **BGP Routing** | Backend Gateway Protocol — route requests across multiple providers with smart scoring |
| **Reasoning Support** | Full thinking/reasoning mode support across all providers |
| **Session Manager** | Unified scanner/parser for Codex, Claude Code, and Gemini CLI sessions |
| **Provider Manager** 🆕 | Interactive TUI to add/edit/remove providers, manage models, test connectivity |

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.8+** (no pip packages needed — pure stdlib)
- For GUI: `tkinter` (usually included with Python)

### ⚡ Install (3 options)

#### Option A: Clone & Run (Any Platform)

```bash
git clone https://github.com/roman-ryzenadvanced/zClaude.git
cd zClaude
python3 provider_manager.py wizard    # ← Guided setup (recommended first run)
python3 translate-proxy.py            # ← Start the proxy server
```

#### Option B: Linux / macOS Installer

```bash
# Download & run installer
curl -fsSL https://raw.githubusercontent.com/roman-ryzenadvanced/zClaude/main/install.sh | bash

# Or manually:
git clone https://github.com/roman-ryzenadvanced/zClaude.git
cd zClaude
bash install.sh
```

#### Option C: Windows Installer

```powershell
# PowerShell
irm https://raw.githubusercontent.com/roman-ryzenadvanced/zClaude/main/install.ps1 | iex

# Or manually:
git clone https://github.com/roman-ryzenadvanced/zClaude.git
cd zClaude
powershell -ExecutionPolicy Bypass -File install.ps1
```

#### Option D: Android / Termux

```bash
pkg update && pkg install python bash curl jq
git clone https://github.com/roman-ryzenadvanced/zClaude.git
cd zClaude
bash install-termux.sh
```

---

## 📖 Usage

### Provider Management (NEW 🎉)

The built-in **Provider Manager** gives you full control over your AI backends:

```bash
# Interactive TUI (full menu)
python3 provider_manager.py

# Quick commands
python3 provider_manager.py list                  # List all configured providers
python3 provider_manager.py show my-provider      # Show details
python3 provider_manager.py add                   # Add new provider (wizard)
python3 provider_manager.py edit my-provider      # Edit existing provider
python3 provider_manager.py remove my-provider    # Remove a provider
python3 provider_manager.py models my-provider    # Manage model list
python3 provider_manager.py set-default           # Set default provider
python3 provider_manager.py test my-provider      # Test connectivity
python3 provider_manager.py export                # Export config as JSON
python3 provider_manager.py import backup.json    # Import from file
python3 provider_manager.py wizard                # Full guided setup
```

### Start the Proxy Server

```bash
# Basic start (reads from ~/.codex/proxy-config.json)
python3 translate-proxy.py

# Custom port
python3 translate-proxy.py --port 9090

# Environment variables
PROXY_PORT=8080 PROXY_BACKEND=openai-compat PROXY_TARGET_URL=http://localhost:11434/v1 PROXY_API_KEY=your-key python3 translate-proxy.py
```

### Launch the GUI

```bash
# Classic GTK/Tkinter GUI
python3 codex-launcher-gui.py

# Modern X Edition (dark theme)
python3 codex-launcher-gui-x.py
```

### Session Management

```bash
# Scan sessions from all providers
python3 session_manager.py

# Or programmatically:
from session_manager import scan_all, load_messages
sessions = scan_all()          # Scan Codex + Claude + Gemini
messages = load_messages(sessions[0])  # Load conversation
```

### Test Suite

```bash
# End-to-end Antigravity/Gemini test suite
bash test-antigravity.sh              # Quick tests
bash test-antigravity.sh --task       # + real CLI task
bash test-antigravity.sh --verbose    # Show full logs
```

---

## 🏗️ Architecture

```
zclaude/
├── translate-proxy.py          # Main proxy server entry point
├── provider_manager.py         # 🆕 Provider CRUD & settings manager
├── session_manager.py          # Multi-provider session scanner
├── universal_runtime.py        # Cross-platform runtime detection
├── config_schema.py            # JSON schema validation
├── codex_launcher_lib.py       # Shared library (re-exports)
├── codex-launcher-gui.py       # Classic GUI entry point
├── codex-launcher-gui-x.py     # Modern X Edition GUI
│
├── proxy/                      # Core proxy engine
│   ├── server.py               # HTTP handler, request routing
│   ├── config.py               # Configuration, circuit breakers
│   ├── auth_pools.py           # OAuth + API key rotation
│   ├── compaction.py           # Adaptive context compaction
│   ├── tool_validation.py      # Tool call repair/synthesis
│   ├── logging_utils.py        # Secret redaction, rate limiting
│   ├── cc_parser.py            # Command Code protocol parser
│   └── adapters/               # Provider-specific translators
│       ├── openai.py           # OpenAI-compatible (Chat Completions)
│       ├── anthropic.py        # Anthropic (Messages API)
│       ├── gemini.py           # Google Gemini OAuth + Antigravity
│       ├── gemini_helpers.py   # Gemini sig store, version check
│       ├── kiro.py             # AWS Kiro / CodeWhisperer
│       ├── command_code.py     # Google Command Code
│       ├── codebuff.py         # CodeBuff free tier
│       └── auto_sense.py       # Auto-detect unknown endpoints
│
├── lib/                        # Shared utilities
│   ├── bootstrap.py            # Runtime bootstrap
│   ├── config_manager.py       # TOML/JSON config management
│   ├── constants.py            # Global constants
│   ├── endpoints.py            # Endpoint resolution
│   ├── doctor.py               # Health checks
│   ├── monitoring.py           # Usage stats, log analysis
│   ├── platform_utils.py       # OS detection helpers
│   ├── presets.py              # Provider preset configs
│   ├── process.py              # Process lifecycle (cross-platform)
│   ├── profiles.py             # Runtime profiles per OS
│   ├── proxy_lifecycle.py      # Start/stop/restart proxy
│   ├── cleanup.py              # Log rotation, temp file cleanup
│   ├── changelog.py            # Version tracking
│   ├── model_fetcher.py        # Dynamic model catalog fetch
│   ├── utils.py                # General utilities
│   └── oauth_secrets.py        # Secure credential storage
│
├── gui/                        # Classic GUI (tkinter)
│   ├── launcher.py             # Main window
│   ├── endpoint_dialogs.py     # Endpoint configuration dialogs
│   ├── helpers.py              # GUI utilities
│   ├── bgp_dialogs.py          # BGP route management
│   └── oauth_flows.py          # OAuth login flows
│
├── gui_x/                      # Modern X Edition GUI
│   ├── launcher.py             # Main launcher window
│   ├── sidebar.py              # Navigation sidebar
│   ├── cards.py                # Provider cards
│   ├── theme.py                # Dark theme definitions
│   ├── fonts.py                # Font loading
│   ├── titlebar.py             # Custom title bar
│   ├── anims.py                # Animations
│   ├── log_console.py          # Live log viewer
│   └── windows.py              # Sub-windows (monitoring, etc.)
│
├── gui/windows/                # GUI sub-windows
│   ├── monitoring.py           # Real-time monitoring dashboard
│   ├── sessions.py             # Session browser
│   ├── usage.py                # Usage statistics
│   ├── benchmark.py            # Latency benchmark tool
│   ├── history.py              # Request history
│   └── updater.py              # Auto-update checker
│
├── antigravity_grpc/           # gRPC fallback for Antigravity
│   ├── client.py               # gRPC client wrapper
│   └── proto/                  # Protocol buffer definitions
│
├── plugins/                    # Plugin system
│   └── base.py                 # Plugin base class
│
├── locales/                    # i18n translations
│   ├── en.json                 # English
│   ├── es.json                 # Spanish
│   └── zh.json                 # Chinese
│
├── install.sh                  # Linux/macOS installer
├── install.ps1                 # Windows PowerShell installer
├── install-termux.sh           # Termux/Android installer
├── termux-daemon.sh            # Termux daemon manager
├── verify-universal.sh         # Cross-platform test runner
├── test-antigravity.sh         # E2E Antigravity test suite
├── codex-launcher.desktop.template  # .desktop file template
└── README.md                   # This file
```

---

## 🔌 Supported Providers

| Provider | Backend Type | Auth | Tools | Streaming | Vision |
|----------|-------------|------|-------|-----------|---------|
| **OpenAI / GPT** | `openai-compat` | API Key | ✅ | ✅ | ✅ |
| **Anthropic / Claude** | `anthropic` | API Key | ✅ | ✅ | ✅ |
| **Google Gemini** | `gemini-oauth-antigravity` | OAuth | ✅ | ✅ | ✅ |
| **Google Cloud Code** | `command-code` | OAuth | ✅ | ✅ | ❌ |
| **AWS Kiro** | `kiro-oauth` | SSO/OAuth | ✅ | ✅ | ❌ |
| **CodeBuff (Free)** | `codebuff` | Cookie | ✅ | ✅ | ❌ |
| **Ollama (Local)** | `openai-compat` | None | ✅ | ✅ | ❌ |
| **vLLM / LMStudio** | `openai-compat` | None | ✅ | ✅ | ❌ |
| **OpenRouter** | `openai-compat` | API Key | ✅ | ✅ | ✅ |
| **Groq** | `openai-compat` | API Key | ✅ | ✅ | ❌ |
| **DeepSeek** | `openai-compat` | API Key | ✅ | ✅ | ❌ |
| **Auto-Detect** | `auto` | Auto | ✅ | ✅ | ✅ |

---

## 🛡️ Safety & Guardrails

- **Secret scanning** before every operation (API keys, tokens, passwords auto-redacted in logs)
- **Never force-pushes** to protected branches
- **Circuit breaker** prevents cascading upstream failures
- **Request cancellation** support with graceful drain
- **PID-based anti-stall** — kills orphaned proxy processes on startup
- **Hot-reload** API keys without restart (`POST /admin/reload`)
- **Rate limiting** with Token Bucket algorithm per-route
- **Config validation** with JSON schema enforcement

---

## 🌍 Cross-Platform Details

### Linux (Desktop & Server)
- Dependencies: `python3`, `bash`, `curl`
- UI Mode: GTK (via tkinter) or CLI
- Daemon: systemd user service supported
- Default proxy port: `8080`
- Config dir: `~/.codex/`

### macOS
- Same as Linux — fully compatible via Python 3 from Homebrew / python.org
- UI Mode: Tkinter (included with python.org installer)
- Launch at login: `launchd` plist generation available

### Windows
- Dependencies: Python 3.8+ (from python.org or Microsoft Store)
- UI Mode: Tkinter (included)
- Daemon: Background process with taskbar notification
- Config dir: `%APPDATA%/codex-proxy/` or `~/.codex/`
- Process group management for clean shutdown
- Signal handling: SIGBREAK + atexit handlers

### Android / Termux
- Dependencies: `python`, `bash`, `curl`, `jq` (via `pkg install`)
- UI Mode: CLI-only (or web-lite dashboard)
- Battery-aware: skips heavy ops when battery < 15%
- Wake lock support via `termux-wake-lock`
- Boot hooks: auto-start on device boot
- Widget shortcuts: Status, Start, Stop, TUI launcher
- Notifications: `termux-notification` integration
- Daemon: managed by `termux-daemon.sh {start|stop|restart|status|logs}`

---

## 🔄 Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PROXY_PORT` | Proxy listen port | `8080` |
| `PROXY_BACKEND` | Backend type override | _(from config)_ |
| `PROXY_TARGET_URL` | Upstream API URL | _(from config)_ |
| `PROXY_API_KEY` | API key for upstream | _(from config)_ |
| `CODEX_HOST` | Bind address | `127.0.0.1` |
| `CODEX_PORT` | Alias for PROXY_PORT | `8080` |
| `CAVEMAN_MODE` | Concise output mode | `0` |
| `RTK_COMPRESSION` | Enable RTK compression | `0` |
| `AUTO_COMPACT` | Auto-compact long contexts | `0` |
| `ADAPTIVE_COMPACT` | Smart adaptive compaction | `0` |

---

## 📊 API Endpoints

Once running, the proxy exposes these endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/models` | List available models |
| GET | `/v1/accounts` | Account/pool status |
| GET | `/health` | Health check (uptime, memory, requests) |
| POST | `/v1/responses` | Main Responses API endpoint |
| POST | `/admin/reload` | Hot-reload API key |
| GET | `/admin/verify-key` | Validate current API key |
| DELETE | `/admin/cancel/:id` | Cancel a running request |

---

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit changes: `git commit -m 'Add amazing feature'`
4. Push: `git push origin feature/amazing-feature`
5. Open a Pull Request

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- Built on the shoulders of giants — [OpenAI](https://openai.com), [Anthropic](https://anthropic.com), [Google DeepMind](https://deepmind.google), [AWS](https://aws.amazon.com/codewhisperer/)
- Powered by [Z.ai](https://z.ai) GLM models
- Inspired by the open-source AI community

---

<p align="center">
  <b>Built with ❤️ by <a href="https://github.com/roman-ryzenadvanced">roman-ryzenadvanced</a></b>
  <br><br>
  <i>⭐ If this helped you, give it a star! ⭐</i>
</p>
