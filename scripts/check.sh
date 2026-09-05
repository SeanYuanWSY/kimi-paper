#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT_DIR/.build/checks"
xcrun swiftc "$ROOT_DIR/Sources/KimiPaper/Support/AccessEndpoint.swift" "$ROOT_DIR/Tests/SecurityChecks.swift" -o "$ROOT_DIR/.build/checks/security-checks"
"$ROOT_DIR/.build/checks/security-checks"
"$ROOT_DIR/.runtime/env/bin/python" -m py_compile "$ROOT_DIR/Resources/prepare_project.py" "$ROOT_DIR/Resources/supervise.py"
