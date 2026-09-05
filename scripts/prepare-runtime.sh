#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_DIR="$ROOT_DIR/.runtime/env"
command -v uv >/dev/null || { echo '请先安装 uv。'; exit 1; }
mkdir -p "$ROOT_DIR/.runtime"
if [[ ! -x "$ENV_DIR/bin/python" ]]; then
  uv venv --managed-python --python 3.12.13 "$ENV_DIR"
fi
uv pip sync --python "$ENV_DIR/bin/python" "$ROOT_DIR/requirements.lock"
"$ENV_DIR/bin/python" -c 'from mcp.server.fastmcp import FastMCP; import tex_mcp_web; print("Paper runtime ready")'
