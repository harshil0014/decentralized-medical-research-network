#!/usr/bin/env bash
set -euo pipefail

echo "== local source checks =="
go test ./...
go vet ./...
echo "PASS: Go chaincode compiles/tests locally"
