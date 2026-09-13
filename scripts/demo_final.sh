#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Decentralized Medical Research Network"
echo "Secure API: https://localhost:8443"
echo "Hospital -> AES-256-GCM -> IPFS -> Fabric private locator"
echo "Researcher -> discovery -> access request -> encrypted computation"
echo "Microsoft SEAL CKKS: researcher computes without secret key"
echo "See docs/FINAL_SYSTEM.md"
