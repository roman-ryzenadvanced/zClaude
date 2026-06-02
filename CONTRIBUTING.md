# Contributing to zClaude

Thank you for considering contributing to zClaude! 🎉

## Development Setup

```bash
# Clone the repo
git clone https://github.com/roman-ryzenadvanced/zClaude.git
cd zClaude

# Ensure Python 3.8+ is available
python3 --version

# Run the provider manager to set up your config
python3 provider_manager.py wizard

# Start the proxy for testing
python3 translate-proxy.py --port 8080
```

## Code Style

- **Pure Python stdlib** — No external pip dependencies in core modules
- **Cross-platform** — Must work on Linux, macOS, Windows, and Termux
- **Type hints** — Use `from __future__ import annotations` for forward refs
- **Docstrings** — Google-style or NumPy-style docstrings on all public functions
- **Error handling** — Graceful degradation, never crash on user input

## Project Structure Rules

| Directory | Rule |
|-----------|------|
| `proxy/` | Core engine — no GUI code |
| `lib/` | Pure utilities — no network calls at import time |
| `gui/` | Classic tkinter GUI |
| `gui_x/` | Modern X Edition |
| `adapters/` | One file per provider, must be self-contained |

## Adding a New Provider Adapter

1. Create `proxy/adapters/my_provider.py`
2. Implement required functions:
   - `my_input_to_messages(input_data, instructions) -> list`
   - `my_convert_tools(tools) -> list`
   - `my_resp_to_responses(raw_resp, model) -> dict`
   - `my_stream_to_sse(upstream, request_id) -> generator`
3. Register in `proxy/server.py` imports
4. Add backend type to `provider_manager.py` BACKEND_TYPES dict
5. Add models to `provider_manager.py` MODEL_CATALOGS
6. Test with `python3 provider_manager.py test <name>`

## Testing

```bash
# Run verification suite
bash verify-universal.sh

# Run Antigravity E2E tests (requires OAuth setup)
bash test-antigravity.sh

# Validate config schema
python3 config_schema.py

# Test provider connectivity
python3 provider_manager.py test <provider-name>
```

## Submitting Changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes (follow code style above)
4. Test thoroughly on your platform(s)
5. Commit with a clear message: `git commit -m "Add: my feature"`
6. Push to your fork: `git push origin feature/my-feature`
7. Open a Pull Request with a description of what changed

## Security

- **Never commit secrets/tokens** — The `.gitignore` blocks these, but double-check
- **Redact sensitive data in logs** — Use `_redact()` from `proxy/logging_utils.py`
- **Validate paths** — Always resolve and validate file paths before operations
- **No eval/exec on user input** — Parse data safely with `json.loads()` only

## Questions?

Open an issue or start a discussion on GitHub!
