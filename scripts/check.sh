#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT_DIR/.build/checks"
xcrun swiftc "$ROOT_DIR/Sources/KimiPaper/Support/AccessEndpoint.swift" "$ROOT_DIR/Tests/SecurityChecks.swift" -o "$ROOT_DIR/.build/checks/security-checks"
"$ROOT_DIR/.build/checks/security-checks"
xcrun swiftc -swift-version 5 "$ROOT_DIR/Sources/KimiPaper/Support/RuntimePaths.swift" "$ROOT_DIR/Sources/KimiPaper/Services/TranslationService.swift" "$ROOT_DIR/Tests/TranslationChecks.swift" -o "$ROOT_DIR/.build/checks/translation-checks"
"$ROOT_DIR/.build/checks/translation-checks"
"$ROOT_DIR/.runtime/env/bin/python" -m compileall -q "$ROOT_DIR/Resources"
"$ROOT_DIR/.runtime/env/bin/python" -m unittest discover -s "$ROOT_DIR/Tests/Python" -v
if command -v node >/dev/null 2>&1; then
  node --check "$ROOT_DIR/Resources/paper_viewer.js"
  node -e 'const fs=require("fs"),vm=require("vm");new vm.Script(fs.readFileSync(process.argv[1],"utf8").match(/<script>([\s\S]*)<\/script>/)[1]);' "$ROOT_DIR/Resources/workbench.html"
  node "$ROOT_DIR/Tests/JavaScript/reading-translation.cjs"
fi
