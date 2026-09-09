#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ICON_STAGE="$(mktemp -d "${TMPDIR:-/tmp}/kimi-paper-icon.XXXXXX")"
mkdir "$ICON_STAGE/AppIcon.iconset"
swift "$ROOT_DIR/Resources/DrawIcon.swift" "$ROOT_DIR/Resources/Brand/B2-source.png" "$ICON_STAGE/master.png"
for points in 16 32 128 256 512; do
  sips -z "$points" "$points" "$ICON_STAGE/master.png" --out "$ICON_STAGE/AppIcon.iconset/icon_${points}x${points}.png" >/dev/null
  pixels=$((points * 2))
  sips -z "$pixels" "$pixels" "$ICON_STAGE/master.png" --out "$ICON_STAGE/AppIcon.iconset/icon_${points}x${points}@2x.png" >/dev/null
done
iconutil -c icns "$ICON_STAGE/AppIcon.iconset" -o "$ROOT_DIR/Resources/AppIcon.icns"
cp "$ICON_STAGE/master.png" "$ROOT_DIR/Resources/Brand/AppIcon.png"
printf 'Icon encoded: %s\n' "$ROOT_DIR/Resources/AppIcon.icns"
