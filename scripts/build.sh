#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_DIR="$ROOT_DIR/.runtime/env"
APP_BUNDLE="$ROOT_DIR/dist/Kimi Paper.app"
MODE="${1:---build-only}"
if [[ "$MODE" != '--build-only' && "$MODE" != '--run' ]]; then
  echo 'usage: scripts/build.sh [--build-only|--run]'; exit 1
fi
if [[ -f "$APP_BUNDLE/Contents/MacOS/KimiPaper" ]] && /usr/sbin/lsof -t "$APP_BUNDLE/Contents/MacOS/KimiPaper" >/dev/null 2>&1; then
  echo '请先退出此构建目录中的 Kimi Paper。'; exit 1
fi
"$ROOT_DIR/scripts/prepare-runtime.sh"
swift build --package-path "$ROOT_DIR" -c release
BINARY_DIR="$(swift build --package-path "$ROOT_DIR" -c release --show-bin-path)"
PYTHON_BASE="$("$ENV_DIR/bin/python" -c 'import sys; print(sys.base_prefix)')"
mkdir -p "$ROOT_DIR/dist"
STAGE_DIR="$(mktemp -d "$ROOT_DIR/dist/.package-XXXXXX")"
STAGED_APP="$STAGE_DIR/Kimi Paper.app"
RESOURCES="$STAGED_APP/Contents/Resources"
mkdir -p "$STAGED_APP/Contents/MacOS" "$RESOURCES"
cp "$BINARY_DIR/KimiPaper" "$STAGED_APP/Contents/MacOS/KimiPaper"
ditto "$PYTHON_BASE" "$RESOURCES/python"
ditto "$ENV_DIR/lib/python3.12/site-packages" "$RESOURCES/python/lib/python3.12/site-packages"
cp "$ROOT_DIR/Resources/prepare_project.py" "$ROOT_DIR/Resources/prepare_workspace.py" "$ROOT_DIR/Resources/supervise.py" "$ROOT_DIR/Resources/example.tex" "$ROOT_DIR/Resources/AppIcon.icns" "$RESOURCES/"
cp "$ROOT_DIR/Resources/"paper_*.py "$ROOT_DIR/Resources/"paper_*.js "$ROOT_DIR/Resources/paper_studio_panel.html" "$ROOT_DIR/Resources/workbench.html" "$RESOURCES/"
cp "$ROOT_DIR/licenses/tex-mcp-web-MIT.txt" "$RESOURCES/tex-mcp-web-LICENSE"
cp "$ROOT_DIR/Resources/Info.plist" "$STAGED_APP/Contents/Info.plist"
codesign --force --sign - "$STAGED_APP" >/dev/null
codesign --verify --deep --strict "$STAGED_APP"
if [[ -e "$APP_BUNDLE" ]]; then
  BACKUP_DIR="$(mktemp -d "$ROOT_DIR/dist/previous-XXXXXX")"
  mv "$APP_BUNDLE" "$BACKUP_DIR/"
fi
mv "$STAGED_APP" "$APP_BUNDLE"
rmdir "$STAGE_DIR"
echo "Built $APP_BUNDLE"
if [[ "$MODE" == '--run' ]]; then /usr/bin/open "$APP_BUNDLE"; fi
