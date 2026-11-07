#!/usr/bin/env bash
# Установка официального perchai-cli в .cli/ (node >= 22).
# Запуск: bash tools/install-cli.sh [версия]
set -euo pipefail
cd "$(dirname "$0")/.."
VER="${1:-2.4.125}"
mkdir -p .cli
export -n PYTHONHOME PYTHONPATH 2>/dev/null || true
npm install --prefix .cli --no-audit --no-fund "perchai-cli@${VER}"
CLI="$PWD/.cli/node_modules/.bin/perch"
echo "== perch cli: $CLI"
HOME="$PWD/data/cli-home" PERCH_CLI_AUTH_DIR="$PWD/data/cli-auth" \
  PERCH_NO_UPDATE_NOTIFIER=1 node "$CLI" status 2>&1 | head -3 || true
