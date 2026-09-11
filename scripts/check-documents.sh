#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
mkdir -p .build/checks
xcrun swiftc -swift-version 5 Sources/KimiPaper/Support/*.swift Sources/KimiPaper/Services/*.swift Tests/DocumentChecks.swift -o .build/checks/document-checks
.build/checks/document-checks
xcrun swiftc -swift-version 5 Sources/KimiPaper/Support/*.swift Sources/KimiPaper/Services/*.swift Sources/KimiPaper/Views/DocumentReader.swift Tests/ReaderInteractionChecks.swift -o .build/checks/reader-interaction-checks
.build/checks/reader-interaction-checks
.runtime/env/bin/python -m unittest discover -s Tests/Python -p 'test_document_service.py' -v
