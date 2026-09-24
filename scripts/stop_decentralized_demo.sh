#!/usr/bin/env bash
set -euo pipefail
docker stop medical-besu-1 medical-besu-2 medical-besu-3 medical-besu-4 \
  medical-ipfs medical-ipfs-2 medical-ipfs-3
echo "Decentralized demo stopped; node data retained."
