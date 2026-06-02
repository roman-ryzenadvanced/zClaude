"""
Codex Launcher Plugin Base - Template for custom provider plugins.

To create a plugin:
1. Copy this file to ~/.codex/plugins/my_provider.py
2. Edit the register() function with your provider details
3. The provider will appear in the launcher automatically

Plugin files must define a register() function that returns a dict with:
  - name (str): Display name for the provider
  - backend_type (str): One of: openai-compat, anthropic, command-code
  - base_url (str): API base URL
  - model (str, optional): Default model name
  - api_key (str, optional): API key (if static)
  - auth_type (str, optional): Authentication type (bearer, x-api-key, none)
  - headers (dict, optional): Extra HTTP headers
  - models_url (str, optional): URL to fetch available models
"""


def register():
    """Register this plugin with Codex Launcher.

    Returns:
        dict: Provider configuration with at minimum:
            - name: Display name
            - backend_type: Protocol type
            - base_url: API endpoint URL
    """
    return {
        # Required fields
        "name": "My Custom Provider",
        "backend_type": "openai-compat",
        "base_url": "https://api.example.com/v1",

        # Optional fields
        "model": "my-model-name",
        "auth_type": "bearer",
        "models_url": "https://api.example.com/v1/models",

        # Custom headers to send with every request
        "headers": {},

        # Provider capabilities
        "supports_streaming": True,
        "supports_tools": True,
        "supports_vision": False,

        # Model list (optional, overrides models_url)
        "models": [
            {"id": "my-model-name", "context_length": 128000},
        ],
    }
