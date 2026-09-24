#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

BESU_IMAGE="${MEDICAL_BESU_IMAGE:-hyperledger/besu:26.8.1}"
IPFS_IMAGE="${MEDICAL_IPFS_IMAGE:-ipfs/kubo:v0.43.1}"
RUNTIME="${MEDICAL_DECENTRALIZED_RUNTIME:-$PWD/.runtime/decentralized}"
QBFT="$RUNTIME/qbft"
AUTH="${MEDICAL_REGISTRY_AUTH_DIR:-$RUNTIME/authority}"
NETWORK="medical-decentralized"

command -v docker >/dev/null
command -v node >/dev/null
command -v npm >/dev/null
command -v curl >/dev/null
command -v python3 >/dev/null

rm -rf "$QBFT"
mkdir -p "$QBFT" "$AUTH"
chmod 700 "$AUTH"

npm ci --prefix ethereum
npm --prefix ethereum run compile

MEDICAL_REGISTRY_AUTH_DIR="$AUTH"   node ethereum/scripts/generate-qbft-config.mjs "$QBFT"

docker rm -f medical-besu-1 medical-besu-2 medical-besu-3 medical-besu-4   medical-ipfs medical-ipfs-2 medical-ipfs-3 >/dev/null 2>&1 || true
docker network rm "$NETWORK" >/dev/null 2>&1 || true
docker network create --subnet 172.29.0.0/24 "$NETWORK" >/dev/null

docker run --rm --user root   -v "$QBFT:/network"   "$BESU_IMAGE"   operator generate-blockchain-config   --config-file=/network/qbftConfigFile.json   --to=/network/networkFiles   --private-key-file-name=key

cp "$QBFT/networkFiles/genesis.json" "$QBFT/genesis.json"
mapfile -t KEY_DIRS < <(find "$QBFT/networkFiles/keys" -mindepth 1 -maxdepth 1 -type d | sort)
test "${#KEY_DIRS[@]}" -eq 4

for i in 1 2 3 4; do
  mkdir -p "$QBFT/node$i/data"
  cp "${KEY_DIRS[$((i-1))]}/key" "$QBFT/node$i/data/key"
  cp "${KEY_DIRS[$((i-1))]}/key.pub" "$QBFT/node$i/data/key.pub"
  chmod 600 "$QBFT/node$i/data/key"
done

docker run -d --name medical-besu-1   --network "$NETWORK" --ip 172.29.0.11   -p 127.0.0.1:8545:8545   -v "$QBFT:/network"   "$BESU_IMAGE"   --data-path=/network/node1/data   --genesis-file=/network/genesis.json   --p2p-host=172.29.0.11 --p2p-port=30303   --rpc-http-enabled --rpc-http-host=0.0.0.0 --rpc-http-port=8545   --rpc-http-api=ETH,NET,QBFT,WEB3,ADMIN   --host-allowlist="*" --rpc-http-cors-origins="all"   --profile=ENTERPRISE >/dev/null

for i in $(seq 1 90); do
  if curl -fsS -X POST -H 'Content-Type: application/json'     --data '{"jsonrpc":"2.0","method":"admin_nodeInfo","params":[],"id":1}'     http://127.0.0.1:8545 >"$QBFT/node1.json"; then
    break
  fi
  sleep 1
done

BOOTNODE="$(python3 - "$QBFT/node1.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))["result"]["enode"])
PY
)"
test -n "$BOOTNODE"

for i in 2 3 4; do
  ip=$((10+i))
  docker run -d --name "medical-besu-$i"     --network "$NETWORK" --ip "172.29.0.$ip"     -v "$QBFT:/network"     "$BESU_IMAGE"     --data-path="/network/node$i/data"     --genesis-file=/network/genesis.json     --bootnodes="$BOOTNODE"     --p2p-host="172.29.0.$ip" --p2p-port=30303     --rpc-http-enabled --rpc-http-host=0.0.0.0 --rpc-http-port=8545     --rpc-http-api=ETH,NET,QBFT,WEB3,ADMIN     --host-allowlist="*" --rpc-http-cors-origins="all"     --profile=ENTERPRISE >/dev/null
done

for i in $(seq 1 90); do
  VALIDATORS="$(curl -fsS -X POST -H 'Content-Type: application/json'     --data '{"jsonrpc":"2.0","method":"qbft_getValidatorsByBlockNumber","params":["latest"],"id":1}'     http://127.0.0.1:8545 || true)"
  PEERS="$(curl -fsS -X POST -H 'Content-Type: application/json'     --data '{"jsonrpc":"2.0","method":"net_peerCount","params":[],"id":1}'     http://127.0.0.1:8545 || true)"
  if python3 - "$VALIDATORS" "$PEERS" <<'PY'
import json,sys
try:
    validators=json.loads(sys.argv[1]).get("result") or []
    peers=int(json.loads(sys.argv[2]).get("result","0x0"),16)
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if len(validators)==4 and peers>=3 else 1)
PY
  then
    break
  fi
  sleep 1
done

python3 - "$VALIDATORS" "$PEERS" <<'PY'
import json,sys
validators=json.loads(sys.argv[1]).get("result") or []
peers=int(json.loads(sys.argv[2]).get("result","0x0"),16)
assert len(validators)==4, validators
assert peers>=3, peers
print(f"QBFT: {len(validators)} validators, {peers} peers")
PY

# Three online Kubo peers. Only node 1 exposes its RPC API to the host.
for spec in "medical-ipfs:21:5001" "medical-ipfs-2:22:5002" "medical-ipfs-3:23:5003"; do
  IFS=: read -r name last hostport <<<"$spec"
  docker run -d --name "$name"     --network "$NETWORK" --ip "172.29.0.$last"     -p "127.0.0.1:$hostport:5001"     --entrypoint sh     "$IPFS_IMAGE"     -c 'export IPFS_PATH=/data/ipfs; ipfs init; ipfs bootstrap rm all; ipfs config Addresses.API /ip4/0.0.0.0/tcp/5001; exec ipfs daemon' >/dev/null
done

for name in medical-ipfs medical-ipfs-2 medical-ipfs-3; do
  for i in $(seq 1 90); do
    if docker logs "$name" 2>&1 | grep -q "Daemon is ready"; then break; fi
    sleep 1
  done
done

PEER1="$(docker exec medical-ipfs ipfs id -f='<id>')"
PEER2="$(docker exec medical-ipfs-2 ipfs id -f='<id>')"
PEER3="$(docker exec medical-ipfs-3 ipfs id -f='<id>')"
docker exec medical-ipfs-2 ipfs swarm connect "/ip4/172.29.0.21/tcp/4001/p2p/$PEER1" >/dev/null
docker exec medical-ipfs-3 ipfs swarm connect "/ip4/172.29.0.21/tcp/4001/p2p/$PEER1" >/dev/null
docker exec medical-ipfs ipfs swarm connect "/ip4/172.29.0.22/tcp/4001/p2p/$PEER2" >/dev/null
docker exec medical-ipfs ipfs swarm connect "/ip4/172.29.0.23/tcp/4001/p2p/$PEER3" >/dev/null

for name in medical-ipfs medical-ipfs-2 medical-ipfs-3; do
  count="$(docker exec "$name" ipfs swarm peers | wc -l)"
  test "$count" -ge 2
done

HOSPITAL_KEY_FILE="$AUTH/hospital_eth.key"
ETH_RPC_URL=http://127.0.0.1:8545 MEDICAL_ETHEREUM_NETWORK=Besu-QBFT-4 MEDICAL_HOSPITAL_PRIVATE_KEY_FILE="$HOSPITAL_KEY_FILE"   npm --prefix ethereum run deploy

ETH_RPC_URL=http://127.0.0.1:8545 MEDICAL_HOSPITAL_PRIVATE_KEY_FILE="$HOSPITAL_KEY_FILE"   npm --prefix ethereum run test:contract

cat > "$RUNTIME/demo.env" <<EOF
export MEDICAL_HOSPITAL_PRIVATE_KEY_FILE="$HOSPITAL_KEY_FILE"
export MEDICAL_IPFS_CONTAINERS="medical-ipfs,medical-ipfs-2,medical-ipfs-3"
export MEDICAL_ETHEREUM_NETWORK="Besu-QBFT-4"
EOF
chmod 600 "$RUNTIME/demo.env"

echo "DECENTRALIZED DEMO NETWORK: PASS"
echo "Ethereum: 4-validator Besu QBFT"
echo "IPFS: 3 connected Kubo peers, replication factor 3"
echo "Before starting the API: source '$RUNTIME/demo.env'"
