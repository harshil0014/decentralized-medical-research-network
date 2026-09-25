#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHONPATH=. python3 scripts/test_decentralized_topology.py --status-only
