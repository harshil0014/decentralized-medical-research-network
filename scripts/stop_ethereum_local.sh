#!/usr/bin/env bash
set -euo pipefail
pkill -f "node.*ganache" 2>/dev/null || true
pkill -f "ganache.*8545" 2>/dev/null || true
echo "GANACHE STOP: PASS"
