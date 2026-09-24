#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

AUTH=/root/.medical-registry
TLS="$AUTH/tls"

test -s "$AUTH/hospital_api.token"
test -s "$AUTH/researcher_api.token"
test -s "$TLS/server.key"
test -s "$TLS/server.crt"

: "${MEDICAL_MASTER_KEY_HEX:?MEDICAL_MASTER_KEY_HEX must be supplied by an external secret source}"
backend/.venv/bin/python - <<'PY'
import os
value = os.environ.get("MEDICAL_MASTER_KEY_HEX", "")
if len(value) != 64:
    raise SystemExit("MEDICAL_MASTER_KEY_HEX must be exactly 64 hexadecimal characters")
try:
    bytes.fromhex(value)
except ValueError as exc:
    raise SystemExit("MEDICAL_MASTER_KEY_HEX must be hexadecimal") from exc
PY

test -s "ethereum/deployment.json"
backend/.venv/bin/python - <<'PY'
from backend.ethereum_ledger import health
state = health()
assert state["connected"] is True
print("Ethereum:", state["network"], "chain", state["chainId"], state["contractAddress"])
PY

pkill -f "uvicorn backend.app:app" 2>/dev/null || true
sleep 2

nohup backend/.venv/bin/uvicorn \
  backend.app:app \
  --host 127.0.0.1 \
  --port 8443 \
  --ssl-keyfile "$TLS/server.key" \
  --ssl-certfile "$TLS/server.crt" \
  --no-server-header \
  >/tmp/medical-api-secure.log 2>&1 &

for i in $(seq 1 15); do
  if curl -fsS \
    --cacert "$TLS/ca.crt" \
    https://localhost:8443/health \
    >/tmp/secure-health.json
  then
    cat /tmp/secure-health.json
    echo
    echo "HTTPS API START: PASS"
    exit 0
  fi
  sleep 1
done

cat /tmp/medical-api-secure.log
exit 1
