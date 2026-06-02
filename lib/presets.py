"""Provider presets — backend definitions for supported AI providers."""
from lib.utils import normalize_base_url

PROVIDER_PRESETS = {
    "Custom": {
        "backend_type": "openai-compat",
        "base_url": "",
        "models": [],
    },
    "OpenAI": {
        "backend_type": "native",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini"],
    },
    "Anthropic": {
        "backend_type": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-sonnet-4-5", "claude-3-5-haiku-latest"],
    },
    "OpenCode Zen (OpenAI-compatible)": {
        "backend_type": "openai-compat",
        "base_url": "https://opencode.ai/zen/v1",
        "models": [
            "glm-5.1", "glm-5", "kimi-k2.5", "kimi-k2.6",
            "minimax-m2.7", "minimax-m2.5", "minimax-m2.5-free",
            "deepseek-v4-flash-free", "nemotron-3-super-free",
            "qwen3.6-plus", "qwen3.5-plus", "qwen3.6-plus-free",
            "gemini-3-flash", "gemini-3.1-pro", "gemini-3.5-flash",
            "big-pickle", "grok-build-0.1",
        ],
    },
    "OpenCode Zen (Anthropic)": {
        "backend_type": "anthropic",
        "base_url": "https://opencode.ai/zen/v1",
        "models": [
            "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5",
            "claude-opus-4-1", "claude-sonnet-4-6", "claude-sonnet-4-5",
            "claude-sonnet-4", "claude-haiku-4-5",
        ],
    },
    "OpenCode Go (OpenAI-compatible)": {
        "backend_type": "openai-compat",
        "base_url": "https://opencode.ai/zen/go/v1",
        "models": [
            "glm-5.1", "glm-5", "kimi-k2.5", "kimi-k2.6",
            "mimo-v2-omni", "mimo-v2-pro", "mimo-v2.5", "mimo-v2.5-pro",
            "minimax-m2.7", "minimax-m2.5",
            "qwen3.7-max", "qwen3.6-plus", "qwen3.5-plus",
            "deepseek-v4-pro", "deepseek-v4-flash", "hy3-preview",
        ],
    },
    "OpenCode Go (Anthropic)": {
        "backend_type": "anthropic",
        "base_url": "https://opencode.ai/zen/go/v1",
        "models": ["minimax-m2.7", "minimax-m2.5"],
    },
    "Crof.ai": {
        "backend_type": "openai-compat",
        "base_url": "https://crof.ai/v1",
        "models": [],
    },
    "Ocenza": {
        "backend_type": "openai-compat",
        "base_url": "https://global.ocenza.com/v1",
        "models": [
            "gpt-oss-120b", "mimo-v2-pro", "mimo-v2.5", "mimo-v2.5-pro",
        ],
    },
    "MiMo (Xiaomi)": {
        "backend_type": "openai-compat",
        "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
        "models": [
            "mimo-v2-omni", "mimo-v2-pro", "mimo-v2.5", "mimo-v2.5-pro",
        ],
    },
    "NVIDIA NIM": {
        "backend_type": "openai-compat",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "models": [],
    },
    "Kilo.ai Gateway": {
        "backend_type": "openai-compat",
        "base_url": "https://api.kilo.ai/api/gateway",
        "models": [],
    },
    "Command Code": {
        "backend_type": "command-code",
        "base_url": "https://api.commandcode.ai",
        "cc_version": "0.26.8",
        "models": [
            "deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro",
            "anthropic:claude-sonnet-4-6", "anthropic:claude-haiku-4-5-20251001",
            "anthropic:claude-opus-4-7", "anthropic:claude-opus-4-6",
            "openai:gpt-5.5", "openai:gpt-5.4", "openai:gpt-5.4-mini", "openai:gpt-5.3-codex",
            "moonshotai/Kimi-K2.6", "moonshotai/Kimi-K2.5",
            "zai-org/GLM-5.1", "zai-org/GLM-5",
            "MiniMaxAI/MiniMax-M2.7", "MiniMaxAI/MiniMax-M2.5",
            "Qwen/Qwen3.6-Max-Preview", "Qwen/Qwen3.6-Plus",
            "stepfun/Step-3.5-Flash", "google/gemini-3.1-flash-lite",
        ],
    },
    "OpenRouter": {
        "backend_type": "openai-compat",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [],
    },
    "Nous Research": {
        "backend_type": "openai-compat",
        "base_url": "https://inference-api.nousresearch.com/v1",
        "models": [
            "stepfun/step-3.7-flash:free",
        ],
    },
    "Perplexity": {
        "backend_type": "openai-compat",
        "base_url": "https://api.perplexity.ai",
        "models": [
            "sonar",
            "sonar-pro",
            "sonar-reasoning-pro",
            "sonar-deep-research",
        ],
    },
    "Cohere": {
        "backend_type": "openai-compat",
        "base_url": "https://api.cohere.ai/compatibility/v1",
        "models": [],
    },
    "Hugging Face": {
        "backend_type": "openai-compat",
        "base_url": "https://router.huggingface.co/v1",
        "models": [],
    },
    "Together AI": {
        "backend_type": "openai-compat",
        "base_url": "https://api.together.xyz/v1",
        "models": [],
    },
    "Groq": {
        "backend_type": "openai-compat",
        "base_url": "https://api.groq.com/openai/v1",
        "models": [],
    },
    "Fireworks AI": {
        "backend_type": "openai-compat",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "models": [],
    },
    "Google Gemini (API Key)": {
        "backend_type": "openai-compat",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": [
            "gemini-2.5-flash", "gemini-2.5-pro",
            "gemini-2.0-flash", "gemini-2.0-flash-lite",
            "gemini-2.5-flash-preview-native-audio-dialog",
        ],
    },
    "Google Gemini (OAuth)": {
        "backend_type": "gemini-oauth-cli",
        "base_url": "https://cloudcode-pa.googleapis.com",
        "oauth_provider": "google-cli",
        "models": [
            "gemini-2.5-flash", "gemini-2.5-pro",
        ],
    },
    "Google Antigravity (OAuth)": {
        "backend_type": "gemini-oauth-antigravity",
        "base_url": "https://cloudcode-pa.googleapis.com",
        "oauth_provider": "google-antigravity",
        "models": [
            "antigravity-gemini-3-flash",
            "antigravity-gemini-3-pro",
            "antigravity-gemini-3.1-pro",
            "antigravity-claude-sonnet-4-6",
            "antigravity-claude-opus-4-6-thinking",
            "gemini-2.5-flash", "gemini-2.5-pro",
            "gemini-3-flash-preview", "gemini-3-pro-preview", "gemini-3.1-pro-preview",
        ],
    },
    "OpenAdapter": {
        "backend_type": "openai-compat",
        "base_url": "https://api.openadapter.in/v1",
        "models": [
            "0G-DeepSeek-V3",
            "0G-DeepSeek-v4-Pro",
            "0G-GLM-5",
            "0G-GLM-5.1",
            "0G-Qwen3.6",
            "0G-Qwen-VL",
        ],
    },
    "Z.ai Coding": {
        "backend_type": "openai-compat",
        "base_url": "https://api.z.ai/api/coding/paas/v4",
        "models": [
            "glm-5.1", "glm-5", "glm-5v-turbo", "glm-4.7", "glm-4.7-flash", "GLM-4-Plus", "GLM-4-Long",
            "GLM-4-Flash", "GLM-4-FlashX", "GLM-Z1-Flash",
        ],
    },
    "Freebuff (Free DeepSeek/Kimi)": {
        "backend_type": "freebuff",
        "base_url": "https://freebuff.com",
        "models": [
            "deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash",
            "moonshotai/kimi-k2.6", "minimax/minimax-m2.7",
        ],
    },
    "Ollama (local)": {
        "backend_type": "openai-compat",
        "base_url": "http://localhost:11434/v1",
        "models": [],
    },
    "LM Studio (local)": {
        "backend_type": "openai-compat",
        "base_url": "http://127.0.0.1:1234/v1",
        "models": [],
    },
    "vLLM / OpenAI-Compatible (self-hosted)": {
        "backend_type": "openai-compat",
        "base_url": "http://localhost:8000/v1",
        "models": [],
    },
    "Kiro (AWS CodeWhisperer)": {
        "backend_type": "kiro-oauth",
        "base_url": "https://codewhisperer.us-east-1.amazonaws.com",
        "oauth_provider": "kiro",
        "models": [],  # fetched dynamically via ListAvailableModels
    },
}
# ═══════════════════════════════════════════════════════════════════════
# Provider preset helpers
# ═══════════════════════════════════════════════════════════════════════

def apply_provider_preset(endpoint, preset_name):
    preset = PROVIDER_PRESETS.get(preset_name)
    if not preset:
        return endpoint
    updated = dict(endpoint)
    updated["provider_preset"] = preset_name
    updated["backend_type"] = preset["backend_type"]
    updated["base_url"] = normalize_base_url(preset["base_url"])
    if preset.get("cc_version") and not updated.get("cc_version"):
        updated["cc_version"] = preset["cc_version"]
    if not updated.get("models") or (preset.get("backend_type") or "").startswith("gemini-oauth"):
        updated["models"] = list(preset.get("models", []))
    if preset.get("oauth_provider"):
        updated["oauth_provider"] = preset["oauth_provider"]
    if not updated.get("default_model") and updated.get("models"):
        updated["default_model"] = updated["models"][0]
    return updated
