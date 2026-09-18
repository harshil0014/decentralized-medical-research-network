#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

command -v node >/dev/null
command -v npm >/dev/null
command -v curl >/dev/null

npm install --prefix ethereum

pkill -f "node.*ganache" 2>/dev/null || true
pkill -f "ganache.*8545" 2>/dev/null || true
sleep 1

nohup npm --prefix ethereum run ganache >/tmp/medical-ganache.log 2>&1 &

for i in $(seq 1 30); do
  if curl -fsS -X POST     -H 'Content-Type: application/json'     --data '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}'     http://127.0.0.1:8545 >/tmp/medical-chain.json
  then
    break
  fi
  sleep 1
done

grep -q '"result"' /tmp/medical-chain.json
npm --prefix ethereum run compile
npm --prefix ethereum run deploy
npm --prefix ethereum run test:contract

echo "GANACHE + SOLIDITY SETUP: PASS"
echo "10 local accounts are configured with 100 test ETH each."
