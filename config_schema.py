#!/usr/bin/env python3
"""
Codex Launcher Config Schema - Validation for configuration files.
Zero pip dependencies. Python 3.8+.
"""

import json
import re
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".codex"

ENDPOINTS_SCHEMA = {
    "type": "object",
    "description": "AI provider endpoints configuration",
    "pattern_properties": {
        ".*": {
            "type": "object",
            "required": ["backend_type", "base_url"],
            "properties": {
                "backend_type": {
                    "type": "string",
                    "enum": ["openai-compat", "anthropic", "command-code",
                             "gemini-oauth-codeassist", "gemini-oauth-antigravity",
                             "codebuff", "freebuff"],
                },
                "base_url": {
                    "type": "string",
                    "pattern": r"^https?://",
                },
                "api_key": {"type": "string"},
                "model": {"type": "string"},
                "is_default": {"type": "boolean"},
                "reasoning_effort": {"type": "string"},
                "context_size": {"type": "integer", "minimum": 1024},
                "stream_idle_timeout": {"type": "integer", "minimum": 10},
                "caveman_mode": {"type": "boolean"},
                "rtk_compression": {"type": "boolean"},
            },
        },
    },
}

PROXY_CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        "host": {"type": "string"},
        "backend": {"type": "string"},
        "target_url": {"type": "string"},
        "api_key": {"type": "string"},
        "model": {"type": "string"},
        "log_level": {"type": "string", "enum": ["DEBUG", "INFO", "WARNING", "ERROR"]},
        "stream_idle_timeout": {"type": "integer", "minimum": 10},
        "max_connections": {"type": "integer", "minimum": 1},
        "request_timeout": {"type": "integer", "minimum": 5},
        "caveman_mode": {"type": "boolean"},
        "rtk_compression": {"type": "boolean"},
    },
}


class ValidationError:
    def __init__(self, path, message, severity="error"):
        self.path = path
        self.message = message
        self.severity = severity

    def __str__(self):
        icon = "E" if self.severity == "error" else "W"
        return f"  [{icon}] {self.path}: {self.message}"


def validate_json_structure(data, schema, path=""):
    """Validate JSON data against a simplified schema."""
    errors = []
    stype = schema.get("type", "any")

    if stype == "object":
        if not isinstance(data, dict):
            errors.append(ValidationError(path, f"Expected object, got {type(data).__name__}"))
            return errors

        if "required" in schema:
            for field in schema["required"]:
                if field not in data:
                    errors.append(ValidationError(
                        f"{path}.{field}" if path else field,
                        f"Required field missing"
                    ))

        if "properties" in schema:
            for key, prop_schema in schema["properties"].items():
                if key in data:
                    sub_path = f"{path}.{key}" if path else key
                    errors.extend(validate_json_structure(data[key], prop_schema, sub_path))

        if "pattern_properties" in schema:
            for key, value in data.items():
                for pattern, prop_schema in schema["pattern_properties"].items():
                    if re.match(pattern, key):
                        sub_path = f"{path}.{key}" if path else key
                        if isinstance(value, dict):
                            errors.extend(validate_json_structure(value, prop_schema, sub_path))
                        break

    elif stype == "string":
        if not isinstance(data, str):
            errors.append(ValidationError(path, f"Expected string, got {type(data).__name__}"))
        elif "pattern" in schema and not re.search(schema["pattern"], data):
            errors.append(ValidationError(path, f"Does not match pattern: {schema['pattern']}"))
        elif "enum" in schema and data not in schema["enum"]:
            errors.append(ValidationError(
                path,
                f"Invalid value '{data}'. Must be one of: {', '.join(schema['enum'])}"
            ))

    elif stype == "integer":
        if not isinstance(data, int):
            errors.append(ValidationError(path, f"Expected integer, got {type(data).__name__}"))
        else:
            if "minimum" in schema and data < schema["minimum"]:
                errors.append(ValidationError(path, f"Value {data} below minimum {schema['minimum']}"))
            if "maximum" in schema and data > schema["maximum"]:
                errors.append(ValidationError(path, f"Value {data} above maximum {schema['maximum']}"))

    elif stype == "boolean":
        if not isinstance(data, bool):
            errors.append(ValidationError(path, f"Expected boolean, got {type(data).__name__}"))

    return errors


def validate_endpoints(filepath=None):
    """Validate endpoints.json file."""
    filepath = Path(filepath) if filepath else CONFIG_DIR / "endpoints.json"
    if not filepath.exists():
        return [ValidationError(str(filepath), "File does not exist")]

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [ValidationError(str(filepath), f"Invalid JSON: {e}")]

    errors = validate_json_structure(data, ENDPOINTS_SCHEMA)

    # Additional semantic checks
    if isinstance(data, dict):
        defaults = [k for k, v in data.items() if v.get("is_default")]
        if len(defaults) > 1:
            errors.append(ValidationError(
                "endpoints",
                f"Multiple default endpoints: {', '.join(defaults)}",
                severity="warning"
            ))
        if len(defaults) == 0 and len(data) > 0:
            errors.append(ValidationError(
                "endpoints",
                "No default endpoint set",
                severity="warning"
            ))

    return errors


def validate_proxy_config(filepath=None):
    """Validate proxy-config.json file."""
    filepath = Path(filepath) if filepath else CONFIG_DIR / "proxy-config.json"
    if not filepath.exists():
        return [ValidationError(str(filepath), "File does not exist", severity="warning")]

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [ValidationError(str(filepath), f"Invalid JSON: {e}")]

    return validate_json_structure(data, PROXY_CONFIG_SCHEMA)


def validate_all():
    """Validate all configuration files."""
    all_errors = []

    print("Validating Codex Launcher configuration...\n")

    print("Checking endpoints.json...")
    errors = validate_endpoints()
    all_errors.extend(errors)
    if not errors:
        print("  OK")
    for e in errors:
        print(e)

    print("\nChecking proxy-config.json...")
    errors = validate_proxy_config()
    all_errors.extend(errors)
    if not errors:
        print("  OK")
    for e in errors:
        print(e)

    error_count = sum(1 for e in all_errors if e.severity == "error")
    warn_count = sum(1 for e in all_errors if e.severity == "warning")

    print(f"\n{'='*40}")
    print(f"Validation complete: {error_count} errors, {warn_count} warnings")

    return error_count == 0


if __name__ == "__main__":
    ok = validate_all()
    sys.exit(0 if ok else 1)
