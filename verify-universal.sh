#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "== Universal verification: unit tests =="
python3 -m unittest -v tests.test_universal_runtime

echo "== Universal verification: presets sync =="
python3 -m unittest -v tests.test_provider_presets

echo "== Universal verification: proxy unit tests =="
python3 -m unittest -v tests.test_translate_proxy

echo "Verification complete."
