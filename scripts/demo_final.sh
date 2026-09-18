#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Decentralized Medical Research Network"
echo "Secure API: https://localhost:8443"
echo "Hospital -> AES-256-GCM -> IPFS -> local private locator commitment -> Ethereum"
echo "Researcher -> Solidity access request -> approved encrypted computation"
echo "Ethereum/Solidity + Ganache: local governance with test ETH\nMicrosoft SEAL CKKS: researcher computes without secret key"
echo "See docs/FINAL_SYSTEM.md"
