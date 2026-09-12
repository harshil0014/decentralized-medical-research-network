#!/usr/bin/env bash
set -euo pipefail

REQUEST_ID="${1:-}"

if [ -z "$REQUEST_ID" ]; then
    echo "Usage: ./controlled-fetch.sh <request-id>"
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../../test-network"

source "$SCRIPT_DIR/env-org2.sh" >/dev/null
source "$SCRIPT_DIR/peer-targets.sh"

ALLOWED=$(peer chaincode query \
    -C mychannel \
    -n medicalregistry \
    -c "{\"function\":\"CanAccess\",\"Args\":[\"$REQUEST_ID\"]}" 2>/dev/null)

if [ "$ALLOWED" != "true" ]; then
    echo "ACCESS DENIED by Fabric"
    exit 1
fi

REQUEST_JSON=$(peer chaincode query \
    -C mychannel \
    -n medicalregistry \
    -c "{\"function\":\"ReadAccessRequest\",\"Args\":[\"$REQUEST_ID\"]}" 2>/dev/null)

DATASET_ID=$(echo "$REQUEST_JSON" | jq -r '.datasetId')

DATASET_JSON=$(peer chaincode query \
    -C mychannel \
    -n medicalregistry \
    -c "{\"function\":\"ReadDataset\",\"Args\":[\"$DATASET_ID\"]}" 2>/dev/null)

CID=$(echo "$DATASET_JSON" | jq -r '.cid')

echo "ACCESS GRANTED by Fabric"
echo "Dataset: $DATASET_ID"
echo "CID: $CID"
echo "----- FILE -----"

docker exec medical-ipfs ipfs cat "$CID"
